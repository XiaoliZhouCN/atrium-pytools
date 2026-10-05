#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scan_repo_outline.py — 扫描仓库结构，生成「目录大纲 + 待建文件页」清单。

与 ``scan_repo_docs.py`` 的区别
------------------------------
* 目录**全展开**：每个目录都是一个标题（H1/H2/H3…），不再变成 Notion 文件夹页。
* 文件**按类型精选**：只有 README / 规范 / 设计等文档才建文件页，其余目录只保留层级。
* 支持三种策略：

  ``shallow``  DeepseekHarness / Forks / Storage —— 只列第一层目录，不再向下探索，不建文件页。
  ``limited``  Manager/AtriumNote —— 目录全展开；只给 AGENTS.md、README.md 以及 Docs/ 下的文件建页。
  ``full``     其余（Manager 其它子目录、Projects）—— 目录全展开；文档名或 docs 类目录下的文件建页。

输出：``out/outline.json``（层级树）、``out/outline.txt``（可读大纲）、``out/files.csv``（待建文件页）。

用法::

    python scan_repo_outline.py
    python scan_repo_outline.py --print-outline
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

#: 只列第一层目录的根（不再向下探索、不建文件页）
SHALLOW_ROOTS = {"DeepseekHarness", "Forks", "Storage"}
#: 特殊策略根：目录全展开，但只给 AGENTS/README/docs 目录建页
LIMITED_ROOTS = {"Manager/AtriumNote"}

#: 完全跳过（任何层级都不出现在大纲里）
EXCLUDE_DIR_NAMES = {
    ".git", ".svn", ".hg", ".idea", ".vs", ".vscode", ".fleet", ".trae",
    "node_modules", "bower_components", "site-packages", "dist-packages",
    "dist", "build", "out", "output", "target", "release", "debug",
    "obj", "bin", "intermediate", "binaries", "cmake-build-debug", "cmake-build-release",
    ".next", ".nuxt", ".turbo", ".parcel-cache", ".dsh-build", ".dependencies",
    "library", "temp", "logs", "usersettings", "memorycaptures", "recordings",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "venv",
    "env", ".tox", ".eggs", "vendor", "vendors", "thirdparty", "third_party",
    "external", "externals", "submodules", "snapshots", "generated", "gen", "autogen",
    "cache", "caches", ".cache", "tmp", ".tmp", "probe-home", ".run", ".agents",
    ".probe-merman", "course_files", "course_files_extracted", "course_notes_work",
    # 生成物：CMake / Unity 自动生成
    "cmakefiles", "_autogen", "autogen", "builtin_shaders-2023.2.22f1",
}
EXCLUDE_DIR_GLOBS = (".probe*", "*.egg-info", "*.dist-info", "cmake-build-*",
                     "builtin_shaders-*", "*_autogen", "*.dir")

#: 视为「文档类目录」：其下所有文档文件都建页面
DOC_DIR_NAMES = {
    "docs", "doc", "documentation", "guides", "guide", "manual", "manuals",
    "handbook", "wiki", "specs", "spec", "reference", "references",
    "design", "designs", "architecture",
}

#: 视为「文档」的文件名（不含扩展名）
DOC_NAME_RE = re.compile(
    r"^(readme|agents|claude|contributing|changelog|changes|history|"
    r"code_of_conduct|security|safety|license|notice|"
    r"design|architecture|arch|spec|specification|convention|conventions|"
    r"standard|standards|style|naming|guideline|guidelines|principle|principles|"
    r"structure|roadmap|todo|overview|manual|guide|reference|protocol|schema|"
    r"migration|deploy|deployment|setup|install|usage|faq|glossary|onboarding|"
    r"contributing|release|versioning|brand|benchmark|index|summary|about|"
    r"naming_and_interfaces|build|build_and_test|steward_contract|agents_)",
    re.I,
)

MARKDOWN_EXTS = {".md", ".markdown", ".mdx", ".rst", ".adoc", ".org"}
TEXT_EXTS = {".txt"}

#: 写进 Notion 的文本类扩展名
CONTENT_EXTS = MARKDOWN_EXTS | TEXT_EXTS


def iso_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def dir_excluded(name: str) -> bool:
    low = name.lower()
    if low in EXCLUDE_DIR_NAMES:
        return True
    if name.startswith("."):          # 隐藏目录（.github / .agents 等）不进大纲
        return True
    return any(fnmatch.fnmatch(low, pattern) for pattern in EXCLUDE_DIR_GLOBS)


def in_doc_dir(rel_parts: tuple[str, ...]) -> bool:
    return any(part.lower() in DOC_DIR_NAMES for part in rel_parts)


def file_wanted(rel_parts: tuple[str, ...], name: str, policy: str) -> bool:
    """判断某文件是否要建文件页。``rel_parts`` 不含文件名。"""
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in CONTENT_EXTS:
        return False

    if policy == "shallow":
        return False                                   # 只列目录，不建文件页

    if policy == "limited":                            # AtriumNote
        if stem.lower() in ("agents", "readme"):
            return ext in CONTENT_EXTS
        return in_doc_dir(rel_parts)                   # Docs/ 目录下的所有文件

    # policy == full
    if in_doc_dir(rel_parts):
        return True
    if ext in TEXT_EXTS:                               # 裸 .txt 只认文档目录里的
        return False
    return DOC_NAME_RE.match(stem) is not None


def scan(root: Path, max_dir_depth: int = 8) -> tuple[list[dict], dict]:
    """``max_dir_depth``：目录展开到这一层为止（0 = 不限制）。Unity/生成树可达 15 层。"""
    stats = {"dirs": 0, "files": 0, "files_seen": 0, "max_depth": 0, "depth_hist": {},
             "clipped_dirs": 0}
    cap = max_dir_depth if max_dir_depth and max_dir_depth > 0 else 10 ** 6

    def walk(path: Path, rel_parts: tuple[str, ...], depth: int,
             policy: str) -> tuple[list[dict], bool]:
        if depth > 32:
            return [], False
        try:
            entries = list(os.scandir(path))
        except OSError:
            return [], False

        dirs: list[dict] = []
        files: list[dict] = []
        clipped = False
        for entry in sorted(entries, key=lambda e: e.name.lower()):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if dir_excluded(entry.name):
                        continue
                    child_policy = policy
                    rel_child = rel_parts + (entry.name,)
                    rel_str = "/".join(rel_child)
                    if rel_str in LIMITED_ROOTS:
                        child_policy = "limited"
                    if depth + 1 > cap:                 # 超过展开深度：只计数，不出现在大纲里
                        clipped = True
                        stats["clipped_dirs"] += 1
                        continue
                    if policy == "shallow" and depth >= 1:
                        # 浅根：只保留第一层目录名，不继续下钻
                        dirs.append({"kind": "dir", "name": entry.name, "rel": rel_str,
                                     "depth": depth + 1, "policy": "shallow",
                                     "truncated": False, "children": []})
                        stats["dirs"] += 1
                        stats["max_depth"] = max(stats["max_depth"], depth + 1)
                        stats["depth_hist"][depth + 1] = stats["depth_hist"].get(depth + 1, 0) + 1
                        continue
                    children, child_clipped = walk(Path(entry.path), rel_child,
                                                   depth + 1, child_policy)
                    clipped = clipped or child_clipped
                    dirs.append({"kind": "dir", "name": entry.name, "rel": rel_str,
                                 "depth": depth + 1, "policy": child_policy,
                                 "truncated": child_clipped, "children": children})
                    stats["dirs"] += 1
                    stats["max_depth"] = max(stats["max_depth"], depth + 1)
                    stats["depth_hist"][depth + 1] = stats["depth_hist"].get(depth + 1, 0) + 1
                elif entry.is_file(follow_symlinks=False):
                    stats["files_seen"] += 1
                    if not file_wanted(rel_parts, entry.name, policy):
                        continue
                    stat = entry.stat()
                    files.append({
                        "kind": "file", "name": entry.name,
                        "rel": "/".join(rel_parts + (entry.name,)),
                        "abs": str(Path(entry.path)),
                        "depth": depth + 1,
                        "ext": os.path.splitext(entry.name)[1].lower(),
                        "size": stat.st_size, "size_human": human_size(stat.st_size),
                        "mtime": iso_mtime(stat.st_mtime),
                    })
                    stats["files"] += 1
            except OSError:
                continue
        return dirs + files, clipped

    top: list[dict] = []
    for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
        if not entry.is_dir(follow_symlinks=False) or dir_excluded(entry.name):
            continue
        policy = "shallow" if entry.name in SHALLOW_ROOTS else "full"
        if entry.name in LIMITED_ROOTS:
            policy = "limited"
        children, clipped = walk(Path(entry.path), (entry.name,), 1, policy)
        top.append({"kind": "dir", "name": entry.name, "rel": entry.name, "depth": 1,
                    "policy": policy, "truncated": clipped, "children": children})
        stats["dirs"] += 1
        stats["depth_hist"][1] = stats["depth_hist"].get(1, 0) + 1
    return top, stats


def iter_files(nodes: list[dict]):
    for node in nodes:
        if node["kind"] == "dir":
            yield from iter_files(node["children"])
        else:
            yield node


def iter_dirs(nodes: list[dict]):
    for node in nodes:
        if node["kind"] == "dir":
            yield node
            yield from iter_dirs(node["children"])


def render(nodes: list[dict], indent: int = 0) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        pad = "  " * indent
        if node["kind"] == "dir":
            mark = "  ⋯（更深层未展开）" if node.get("truncated") else ""
            lines.append(f"{pad}[{node['depth']}] {node['name']}/{mark}")
            lines.extend(render(node["children"], indent + 1))
        else:
            lines.append(f"{pad}    📄 {node['name']}  ({node['size_human']})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描仓库结构，生成目录大纲 + 文件页清单")
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--max-dir-depth", type=int, default=8,
                        help="目录展开深度上限（0 = 不限制）")
    parser.add_argument("--print-outline", action="store_true")
    parser.add_argument("--max-print", type=int, default=200)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    started = time.time()
    tree, stats = scan(root, args.max_dir_depth)
    files = list(iter_files(tree))
    dirs = list(iter_dirs(tree))

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "max_dir_depth": args.max_dir_depth,
        "stats": {"dirs": len(dirs), "files": len(files), "files_seen": stats["files_seen"],
                  "max_depth": stats["max_depth"], "depth_hist": stats["depth_hist"],
                  "clipped_dirs": stats["clipped_dirs"]},
        "tree": tree,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "outline.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = render(tree)
    (out_dir / "outline.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (out_dir / "files.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rel_path", "name", "depth", "ext", "size_bytes", "mtime"])
        for node in files:
            writer.writerow([node["rel"], node["name"], node["depth"], node["ext"],
                             node["size"], node["mtime"]])

    print(f"扫描完成：{root}（{time.time() - started:.2f}s）")
    print(f"  目录（大纲标题）: {len(dirs)}")
    print(f"  文件页          : {len(files)}")
    print(f"  遍历文件数      : {stats['files_seen']}")
    print(f"  展开深度上限    : {args.max_dir_depth if args.max_dir_depth else '不限'}"
          f"（实际最深 {stats['max_depth']}，截断目录 {stats['clipped_dirs']} 个）")
    print(f"  深度分布        : {dict(sorted(stats['depth_hist'].items()))}")
    print("  --- 顶层 ---")
    for node in tree:
        sub_dirs = len(list(iter_dirs(node["children"])))
        sub_files = len(list(iter_files(node["children"])))
        print(f"    {node['name']:<18} 策略={node['policy']:<8} 目录 {sub_dirs:>4}  文件页 {sub_files:>4}")
    print(f"  大纲 JSON       : {out_dir / 'outline.json'}")
    print(f"  大纲文本        : {out_dir / 'outline.txt'}")
    print(f"  文件页 CSV      : {out_dir / 'files.csv'}")

    if args.print_outline:
        print("\n--- 大纲 ---")
        for line in lines[:args.max_print]:
            print(line)
        if len(lines) > args.max_print:
            print(f"  ...（共 {len(lines)} 行，已截断）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
