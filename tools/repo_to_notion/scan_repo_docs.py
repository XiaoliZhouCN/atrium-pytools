#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_repo_docs.py — 扫描仓库里「给人 / 给 AI 看」的文档文件，产出带分级序号的层级清单。

设计要点
--------
* **只收文档，不收工程构件**：``package.json`` / ``CMakeLists.txt`` / ``pyproject.toml``
  / ``*.csproj`` / ``Dockerfile`` 这类构建与配置件一律排除；README、AGENTS、CLAUDE、
  docs/ 目录下的正文才是目标。
* **跳过生成物与第三方**：``node_modules``、构建输出、Unity 的 ``Library``/``Temp``、
  第三方目录、i18n 镜像、测试快照、缓存目录等都不进清单。
* **分级序号**：目录与文件统一编号（``1`` / ``1.1`` / ``1.1.2`` / ``1.1.2.3``），
  序号即 Notion 里的页面标题前缀，天然表达层级。
* **纯标准库**：只用 ``os`` / ``json`` / ``re``，任何 Python 3.9+ 都能跑。

用法::

    python scan_repo_docs.py                          # 默认扫 D:\\Repositories
    python scan_repo_docs.py --root D:\\Repositories --mode docs
    python scan_repo_docs.py --mode md                # 放宽到所有 .md
    python scan_repo_docs.py --print-tree

输出（默认写到脚本同级的 ``out/``）：``manifest.json`` / ``manifest.txt`` / ``manifest.csv``。
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = r"D:\Repositories"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "out"

# ---------------------------------------------------------------------------
# 排除规则
# ---------------------------------------------------------------------------
#: 目录名（小写精确匹配）——依赖、构建产物、缓存、第三方、生成数据
EXCLUDE_DIR_NAMES = {
    # 版本控制 / IDE
    ".git", ".svn", ".hg", ".idea", ".vs", ".vscode", ".fleet",
    # 依赖与包管理
    "node_modules", "bower_components", "site-packages", "dist-packages",
    # 构建产物
    "dist", "build", "out", "output", "outputs", "target", "release", "debug",
    "obj", "bin", "intermediate", "binaries", "cmake-build-debug", "cmake-build-release",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".dsh-build",
    # Unity / 引擎
    "library", "temp", "logs", "usersettings", "memorycaptures", "recordings",
    # Python
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv",
    "env", ".tox", ".eggs",
    # 第三方 / 生成
    "vendor", "vendors", "thirdparty", "third_party", "external", "externals",
    "submodules", "snapshots", "generated", "gen", "autogen",
    # 本仓库特有的缓存 / 临时区
    "cache", "caches", ".cache", "temp", "tmp", ".tmp", "probe-home", ".run",
    "course_files", "course_files_extracted", "course_notes_work",
    # 代理/镜像类
    ".agents", ".trae",
}
#: 目录名通配符（小写）
EXCLUDE_DIR_GLOBS = (".probe*", "*.egg-info", "*.dist-info", "cmake-build-*")

#: 文件级排除（小写精确名 / 正则）
EXCLUDE_FILE_RE = re.compile(
    r"(\.i18n\.yaml$|\.meta$|\.asset$|\.lock$|\.min\.(js|css)$|^package-lock\.json$)",
    re.I,
)

#: 视为「文档扩展名」
MARKDOWN_EXTS = {".md", ".markdown", ".mdx"}
OTHER_DOC_EXTS = {".rst", ".adoc", ".org"}
TEXT_EXTS = {".txt"}

#: 文件名命中即视为文档（不分目录、不分扩展名）
DOC_NAME_RE = re.compile(
    r"^(readme|agents|claude|contributing|changelog|changes|history|architecture|"
    r"design|guide|guides|docs?|documentation|notes|note|todo|roadmap|plan|"
    r"security|safety|brand|benchmark|benchmarks|third_party|license|notice|"
    r"code_of_conduct|faq|manual|spec|specification|overview|summary|index|"
    r"migration|deploy|deployment|setup|install|usage|tutorial|about|authors|"
    r"glossary|principles|conventions|onboarding)",
    re.I,
)

#: 目录名命中即视为「文档目录」，其下所有 markdown 都收
DOC_DIR_NAMES = {
    "docs", "doc", "documentation", "manual", "manuals", "handbook",
    "guides", "guide", "wiki", "spec", "specs", "specification", "specifications",
    "reference", "references",
}

#: DOC_NAME_RE 命中时仍允许的扩展名（避免把 README.png 之类算成文档）
DOC_NAME_EXTS = MARKDOWN_EXTS | OTHER_DOC_EXTS | TEXT_EXTS


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def iso_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def dir_excluded(name: str) -> bool:
    low = name.lower()
    if low in EXCLUDE_DIR_NAMES:
        return True
    return any(fnmatch.fnmatch(low, pattern) for pattern in EXCLUDE_DIR_GLOBS)


def file_included(rel_parts: tuple[str, ...], name: str, mode: str) -> bool:
    """判断一个文件是否属于「给人 / 给 AI 看的文档」。"""
    if EXCLUDE_FILE_RE.search(name):
        return False

    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    in_doc_dir = any(part.lower() in DOC_DIR_NAMES for part in rel_parts)

    # 1) 文档型文件名（README / AGENTS / CLAUDE / docs / notes ...）
    if DOC_NAME_RE.match(stem) and ext in DOC_NAME_EXTS:
        if mode in ("docs", "md", "all"):
            return True

    # 2) markdown 正文
    if ext in MARKDOWN_EXTS:
        if mode in ("md", "all"):
            return True
        return in_doc_dir  # docs 模式：只在 docs/ 之类目录里收散装 md

    # 3) 其它文档格式
    if ext in OTHER_DOC_EXTS:
        if mode == "all":
            return True
        return in_doc_dir

    # 4) 纯文本：只在明确是文档时收（requirements.txt / CMakeLists.txt 不在 DOC_NAME_RE 里）
    if ext in TEXT_EXTS and mode in ("docs", "all"):
        return DOC_NAME_RE.match(stem) is not None

    return False


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def scan_tree(root: Path, mode: str, *, max_depth: int = 24) -> tuple[list[dict], dict]:
    stats = {"dirs_visited": 0, "dirs_skipped": 0, "files_seen": 0, "files_kept": 0,
             "skipped_dirs": []}

    def walk(path: Path, rel_parts: tuple[str, ...], depth: int) -> list[dict]:
        if depth > max_depth:
            return []
        try:
            entries = list(os.scandir(path))
        except (PermissionError, OSError) as exc:  # 权限不足的目录直接跳过
            stats["skipped_dirs"].append({"path": str(path), "reason": str(exc)})
            return []

        dirs: list[dict] = []
        files: list[dict] = []
        for entry in sorted(entries, key=lambda e: e.name.lower()):
            # 符号链接不跟随，避免死循环
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stats["dirs_visited"] += 1
                    if dir_excluded(entry.name):
                        stats["dirs_skipped"] += 1
                        continue
                    children = walk(Path(entry.path), rel_parts + (entry.name,), depth + 1)
                    if children:
                        dirs.append({
                            "kind": "dir",
                            "name": entry.name,
                            "rel": "/".join(rel_parts + (entry.name,)),
                            "children": children,
                        })
                elif entry.is_file(follow_symlinks=False):
                    stats["files_seen"] += 1
                    if not file_included(rel_parts, entry.name, mode):
                        continue
                    stats["files_kept"] += 1
                    stat = entry.stat()
                    files.append({
                        "kind": "file",
                        "name": entry.name,
                        "rel": "/".join(rel_parts + (entry.name,)),
                        "abs": str(Path(entry.path)),
                        "ext": os.path.splitext(entry.name)[1].lower(),
                        "size": stat.st_size,
                        "size_human": human_size(stat.st_size),
                        "mtime": iso_mtime(stat.st_mtime),
                    })
            except OSError:
                continue
        return dirs + files  # 目录在前、文件在后，序号即阅读顺序

    top = walk(root, (), 0)
    return top, stats


def number_tree(nodes: list[dict], prefix: str = "") -> None:
    """就地写入 ``number`` / ``title`` 字段（分级序号）。"""
    for index, node in enumerate(nodes, start=1):
        number = f"{prefix}{index}" if not prefix else f"{prefix}.{index}"
        node["number"] = number
        node["title"] = f"{number} {node['name']}"
        if node["kind"] == "dir":
            number_tree(node["children"], number)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def count_nodes(nodes: list[dict]) -> tuple[int, int]:
    files = dirs = 0
    for node in nodes:
        if node["kind"] == "dir":
            dirs += 1
            sub_files, sub_dirs = count_nodes(node["children"])
            files += sub_files
            dirs += sub_dirs
        else:
            files += 1
    return files, dirs


def render_tree(nodes: list[dict], indent: int = 0) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        pad = "  " * indent
        if node["kind"] == "dir":
            lines.append(f"{pad}{node['title']}/")
            lines.extend(render_tree(node["children"], indent + 1))
        else:
            lines.append(f"{pad}{node['title']}  ({node['size_human']})")
    return lines


def flatten(nodes: list[dict], out: list[dict] | None = None) -> list[dict]:
    out = [] if out is None else out
    for node in nodes:
        if node["kind"] == "dir":
            flatten(node["children"], out)
        else:
            out.append(node)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描仓库里的文档文件并生成分级清单")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="扫描根目录")
    parser.add_argument("--mode", default="docs", choices=("docs", "md", "all"),
                        help="docs=文档名/文档目录；md=所有 markdown；all=所有文档格式")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录")
    parser.add_argument("--print-tree", action="store_true", help="把树打印到标准输出")
    parser.add_argument("--max-print", type=int, default=400, help="打印树的最大行数")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"扫描根目录不存在：{root}", file=sys.stderr)
        return 2

    started = time.time()
    nodes, stats = scan_tree(root, args.mode)
    number_tree(nodes)
    files, dirs = count_nodes(nodes)
    elapsed = time.time() - started

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "mode": args.mode,
        "totals": {"files": files, "dirs": dirs, "bytes": sum(n["size"] for n in flatten(nodes))},
        "scan_stats": {k: v for k, v in stats.items() if k != "skipped_dirs"},
        "skipped_dirs": stats["skipped_dirs"][:50],
        "tree": nodes,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    tree_lines = render_tree(nodes)
    (out_dir / "manifest.txt").write_text("\n".join(tree_lines) + "\n", encoding="utf-8")

    with (out_dir / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["number", "title", "kind", "rel_path", "abs_path", "ext",
                         "size_bytes", "size_human", "mtime"])
        for node in flatten(nodes):
            writer.writerow([node["number"], node["title"], node["kind"], node["rel"],
                             node.get("abs", ""), node["ext"], node["size"],
                             node["size_human"], node["mtime"]])
        # 目录也写一行，方便按目录统计
        def write_dirs(items: list[dict]) -> None:
            for item in items:
                if item["kind"] == "dir":
                    writer.writerow([item["number"], item["title"], "dir", item["rel"],
                                     "", "", "", "", ""])
                    write_dirs(item["children"])
        write_dirs(nodes)

    print(f"扫描完成：{root}")
    print(f"  模式       : {args.mode}")
    print(f"  文档文件   : {files}")
    print(f"  目录       : {dirs}")
    print(f"  遍历文件数 : {stats['files_seen']}（命中 {stats['files_kept']}）")
    print(f"  跳过目录   : {stats['dirs_skipped']}")
    print(f"  耗时       : {elapsed:.2f}s")
    print(f"  清单       : {manifest_path}")
    print(f"  树文本     : {out_dir / 'manifest.txt'}")
    print(f"  CSV        : {out_dir / 'manifest.csv'}")

    print("\n--- 顶层分布 ---")
    for node in nodes:
        sub_files, sub_dirs = count_nodes([node])
        print(f"  {node['title']:<34} 文件 {sub_files:>4}  目录 {sub_dirs:>4}")

    if args.print_tree:
        print("\n--- 层级树 ---")
        for line in tree_lines[:args.max_print]:
            print(line)
        if len(tree_lines) > args.max_print:
            print(f"  ...（共 {len(tree_lines)} 行，已截断）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
