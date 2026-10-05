#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_to_notion.py — 把 ``scan_repo_docs.py`` 的清单按层级写进 Notion。

层级表现
--------
* 目录  → 一个子页面，标题是「分级序号 + 目录名」，例如 ``1.2 deepseek-harness``；
  正文里再放一段「目录」索引，逐条链接到它的子页面。
* 文件  → 一个子页面，标题是「分级序号 + 文件名」，例如 ``1.2.3 README.md``；
  正文是「元信息 callout + 分隔线 + 文件完整正文」。

执行模型（三阶段）
------------------
1. **建页**：按层级逐层并发创建页面（父页 id 依赖上一层结果，天然分轮次）。
2. **正文**：并发把每个文件的内容写成 Notion 原生块。
3. **索引**：并发给每个目录页补一段带链接的子页面清单。

三个阶段都只是「HTTP 请求 + 幂等写」，因此可以安全并发；真正限制吞吐的是 Notion
的 3 req/s，用一个**全局令牌桶（Pacer）**统一限速，靠并发把「网络往返等待」重叠掉。
实测顺序写法约 0.8 req/s，本写法可稳定跑到 ~2.9 req/s。

工程特性
--------
* **断点续传**：每建一页就记进 state，中断后重跑只补缺失部分。
* **正文兜底**：rich_text 单段 2000 字符、单次 100 块、代码语言枚举，都在写之前清洗。
* **复用 notionsync**：直接 import 仓库里现成的 ``notionsync`` 包（纯标准库客户端 +
  Markdown→Blocks 转换器），不重复造轮子。

用法::

    python push_to_notion.py --dry-run                 # 只看计划，不写 Notion
    python push_to_notion.py --only "Projects/ChestTarot"
    python push_to_notion.py                           # 全量（可反复重跑）
    python push_to_notion.py --workers 4 --throttle 0.34
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "out" / "manifest.json"
DEFAULT_STATE = HERE / "out" / "sync_state.json"

#: 目标页：https://app.notion.com/p/Project-Manager-298e47bbdac58043a709d980a380a27f
DEFAULT_PARENT = "298e47bbdac58043a709d980a380a27f"
DEFAULT_WORKSPACE = "A"

# ---------------------------------------------------------------------------
# 找 notionsync（同级目录里的兄弟包，或环境变量 / 已安装包）
# ---------------------------------------------------------------------------
NOTIONSYNC_CANDIDATES = [
    os.environ.get("NOTIONSYNC_HOME", ""),
    str(HERE.parent / "notionsync"),                     # tools/notionsync
    r"D:\Repositories\Manager\AtriumPyTools\tools\notionsync",
    str(Path.home() / "AtriumPyTools" / "tools" / "notionsync"),
]


def load_notionsync():
    for candidate in NOTIONSYNC_CANDIDATES:
        if candidate and (Path(candidate) / "notionsync" / "__init__.py").is_file():
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            break
    try:
        import notionsync  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - 环境问题
        raise SystemExit(
            "找不到 notionsync 包。请设置环境变量 NOTIONSYNC_HOME 指向 "
            "包含 notionsync/__init__.py 的目录（例如 …\\AtriumPyTools\\tools\\notionsync）。"
        ) from exc
    from notionsync import NotionClient, NotionError, load_registry
    from notionsync.client import extract_id
    from notionsync.markdown import markdown_to_blocks
    return NotionClient, NotionError, load_registry, extract_id, markdown_to_blocks


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
RT_LIMIT = 1990            # Notion rich_text 单段上限 2000（UTF-16 码元），留 10 余量
RT_MAX_ITEMS = 100         # rich_text 数组的保守上限
MAX_BLOCKS_DEFAULT = 900   # 单页最多写入的内容块（其余截断并注明）
MARKDOWN_EXTS = (".md", ".markdown", ".mdx", ".rst", ".adoc", ".org")

#: Notion 支持的代码语言（小写）。不在表内的一律降级为 plain text，避免 400。
NOTION_LANGUAGES = {
    "abap", "abc", "agda", "arduino", "ascii art", "assembly", "bash", "basic",
    "bnf", "c", "c#", "c++", "clojure", "coffeescript", "coq", "css", "dart",
    "dhall", "diff", "docker", "ebnf", "elixir", "elm", "erlang", "f#", "flow",
    "fortran", "gherkin", "glsl", "go", "graphql", "groovy", "haskell", "html",
    "idris", "java", "javascript", "json", "julia", "kotlin", "latex", "less",
    "lisp", "livescript", "llvm ir", "makefile", "markdown", "markup", "matlab",
    "mermaid", "nix", "objective-c", "ocaml", "pascal", "perl", "php",
    "plain text", "powershell", "prolog", "protobuf", "purescript", "python", "r",
    "racket", "reason", "ruby", "rust", "sass", "scala", "scheme", "scss",
    "shell", "smalltalk", "solidity", "sql", "swift", "toml", "typescript",
    "vb.net", "verilog", "vhdl", "visual basic", "webassembly", "xml", "yaml",
}
LANGUAGE_ALIASES = {
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript", "sh": "shell", "zsh": "shell",
    "console": "shell", "py": "python", "py3": "python", "yml": "yaml",
    "rb": "ruby", "cs": "c#", "csharp": "c#", "cpp": "c++", "cxx": "c++",
    "hpp": "c++", "hxx": "c++", "h": "c", "objc": "objective-c",
    "ps1": "powershell", "ps": "powershell", "md": "markdown", "text": "plain text",
    "txt": "plain text", "plaintext": "plain text", "none": "plain text",
    "rs": "rust", "kt": "kotlin", "hs": "haskell", "pl": "perl", "vb": "visual basic",
    "dockerfile": "docker", "make": "makefile", "makefile": "makefile",
    "tex": "latex", "htm": "html", "jsonc": "json", "json5": "json",
    "golang": "go", "node": "javascript", "ini": "plain text", "conf": "plain text",
    "csv": "plain text", "log": "plain text", "bat": "plain text", "cmd": "plain text",
    "hlsl": "glsl", "wgsl": "glsl", "vert": "glsl", "frag": "glsl",
    "cmake": "plain text", "gradle": "groovy",
}

ICON_BY_NAME = {
    "readme": "📘", "readme.zh": "📗", "agents": "🤖", "claude": "🤖",
    "contributing": "🤝", "changelog": "🗒️", "license": "⚖️",
}
ICON_DIR = "📁"
ICON_FILE = "📄"

TEXT_BLOCK_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item",
    "numbered_list_item", "to_do", "quote", "callout", "code", "toggle",
}


# ---------------------------------------------------------------------------
# 限速 + blocks 清洗
# ---------------------------------------------------------------------------
class Pacer:
    """全局令牌桶：保证任意线程发出的请求平均间隔 ≥ interval 秒。"""

    def __init__(self, interval: float) -> None:
        self.interval = max(interval, 0.0)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if delay:
            time.sleep(delay)


def _u16len(text: str) -> int:
    """Notion 按 UTF-16 码元计数，emoji 等增补平面字符占 2 个。"""
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def _split_text(content: str, limit: int) -> list[str]:
    """按 UTF-16 长度把长文本切成 ≤ limit 的片段。"""
    if _u16len(content) <= limit:
        return [content]
    parts: list[str] = []
    buffer: list[str] = []
    size = 0
    for char in content:
        width = 2 if ord(char) > 0xFFFF else 1
        if size + width > limit:
            parts.append("".join(buffer))
            buffer, size = [], 0
        buffer.append(char)
        size += width
    if buffer:
        parts.append("".join(buffer))
    return parts


def _split_rich_text(items: list | None, limit: int = RT_LIMIT) -> list:
    """把超过 2000 字符（UTF-16）的单段 rich_text 拆成多段（Notion 硬上限）。"""
    out: list = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            out.append(item)
            continue
        content = (item.get("text") or {}).get("content", "")
        link = (item.get("text") or {}).get("link")
        if _u16len(content) <= limit:
            out.append(item)
            continue
        for piece_text in _split_text(content, limit):
            piece: dict = {"type": "text", "text": {"content": piece_text}}
            if link:
                piece["text"]["link"] = link
            out.append(piece)
    if len(out) >= RT_MAX_ITEMS:
        out = out[:RT_MAX_ITEMS - 1]
        out.append({"type": "text", "text": {"content": "…（内容过长，已截断）"}})
    return out


def _valid_image_url(url: str) -> bool:
    low = (url or "").strip().lower()
    return low.startswith("http://") or low.startswith("https://")


def _image_fallback(block: dict) -> dict:
    """非法图片 URL（相对路径等）会让整页 400，降级成普通段落保留信息。"""
    payload = block.get("image") or {}
    url = ((payload.get("external") or {}).get("url")
           or (payload.get("file") or {}).get("url") or "")
    caption = "".join((item.get("text") or {}).get("content", "")
                      for item in (payload.get("caption") or []))
    text = " ".join(part for part in ("[图片]", caption, url) if part)
    return rich_text_block("paragraph", text)


def _split_large_table(block: dict, limit: int = 100) -> list[dict]:
    """Notion 单个 table 最多 100 行；超长表格拆成多张，后续分片重复表头。"""
    payload = block.get("table") or {}
    rows = payload.get("children") or []
    if len(rows) <= limit:
        return [block]
    has_header = bool(payload.get("has_column_header"))
    header = rows[0] if (has_header and rows) else None
    body = rows[1:] if header else rows
    base = {key: value for key, value in payload.items() if key != "children"}

    tables: list[dict] = []
    capacity = limit - (1 if header else 0)
    for start in range(0, len(body), capacity):
        children = ([header] if header else []) + body[start:start + capacity]
        tables.append({"object": "block", "type": "table",
                       "table": {**base, "children": children}})
    return tables


def _fix_language(block: dict) -> None:
    if block.get("type") != "code":
        return
    payload = block.setdefault("code", {})
    raw = str(payload.get("language") or "").strip().lower()
    language = LANGUAGE_ALIASES.get(raw, raw)
    payload["language"] = language if language in NOTION_LANGUAGES else "plain text"


def sanitize_blocks(blocks: list[dict]) -> list[dict]:
    """递归修正 rich_text 长度、代码语言、表格行数、图片 URL。"""
    cleaned: list[dict] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        payload = block.get(btype)
        if isinstance(payload, dict):
            if isinstance(payload.get("rich_text"), list):
                payload["rich_text"] = _split_rich_text(payload["rich_text"])
            if btype == "table_row" and isinstance(payload.get("cells"), list):
                payload["cells"] = [_split_rich_text(cell) for cell in payload["cells"]]
            if isinstance(payload.get("children"), list):
                payload["children"] = sanitize_blocks(payload["children"])
        _fix_language(block)

        if btype == "image":
            url = ((payload or {}).get("external") or {}).get("url") or ""
            if not _valid_image_url(url):
                cleaned.append(_image_fallback(block))
                continue
        if btype == "table":
            cleaned.extend(_split_large_table(block))
            continue
        cleaned.append(block)
    return cleaned


def rich_text_block(btype: str, text: str, **extra) -> dict:
    payload: dict = {}
    if btype in TEXT_BLOCK_TYPES:
        payload["rich_text"] = [{"type": "text", "text": {"content": text}}] if text else []
    payload.update(extra)
    return {"object": "block", "type": btype, btype: payload}


def meta_callout(lines: list[tuple[str, str]], icon: str = "📄") -> dict:
    rich: list[dict] = []
    for index, (label, value) in enumerate(lines):
        if index:
            rich.append({"type": "text", "text": {"content": "\n"}})
        rich.append({"type": "text", "text": {"content": label}, "annotations": {"bold": True}})
        rich.append({"type": "text", "text": {"content": f" {value}"}})
    return {
        "object": "block",
        "type": "callout",
        "callout": {"rich_text": _split_rich_text(rich),
                    "icon": {"type": "emoji", "emoji": icon}},
    }


def link_bullet(title: str, url: str, suffix: str = "") -> dict:
    rich: list[dict] = [{"type": "text", "text": {"content": title, "link": {"url": url}}}]
    if suffix:
        rich.append({"type": "text", "text": {"content": f"  {suffix}"}})
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _split_rich_text(rich)}}


def pick_icon(name: str, kind: str) -> str:
    if kind == "dir":
        return ICON_DIR
    stem = Path(name).stem.lower()
    if stem in ICON_BY_NAME:
        return ICON_BY_NAME[stem]
    for key, emoji in ICON_BY_NAME.items():
        if stem.startswith(key):
            return emoji
    return ICON_FILE


def make_converter(markdown_to_blocks):
    """返回 ``convert(text, ext) -> blocks``：markdown 走转换器，其余按代码块保留。"""

    def convert(text: str, ext: str) -> list[dict]:
        if ext in MARKDOWN_EXTS:
            return sanitize_blocks(markdown_to_blocks(text))
        language = LANGUAGE_ALIASES.get(ext.lstrip(".").lower(), "plain text")
        if language not in NOTION_LANGUAGES:
            language = "plain text"
        return [{"object": "block", "type": "code",
                 "code": {"rich_text": _split_rich_text(
                     [{"type": "text", "text": {"content": text}}]), "language": language}}]

    return convert


# ---------------------------------------------------------------------------
# 树工具
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


def count_pages(nodes: list[dict]) -> int:
    return sum(1 + (count_pages(n["children"]) if n["kind"] == "dir" else 0) for n in nodes)


def iter_dirs(nodes: list[dict]):
    for node in nodes:
        if node["kind"] == "dir":
            yield node
            yield from iter_dirs(node["children"])


def iter_files(nodes: list[dict]):
    for node in nodes:
        if node["kind"] == "dir":
            yield from iter_files(node["children"])
        else:
            yield node


def filter_tree(nodes: list[dict], prefix: str) -> list[dict]:
    """按 rel 前缀裁剪树：保留祖先链与目标子树，丢掉无关兄弟。"""
    kept: list[dict] = []
    for node in nodes:
        rel = node["rel"]
        if rel == prefix or rel.startswith(prefix + "/"):
            kept.append(node)
        elif prefix.startswith(rel + "/") and node["kind"] == "dir":
            clone = dict(node)
            clone["children"] = filter_tree(node["children"], prefix)
            if clone["children"]:
                kept.append(clone)
    return kept


def truncate_tree(nodes: list[dict], budget: int) -> list[dict]:
    """按 DFS 顺序保留前 ``budget`` 个页面（用于 --limit 试跑）。"""
    kept: list[dict] = []
    for node in nodes:
        if budget <= 0:
            break
        budget -= 1
        if node["kind"] == "dir":
            clone = dict(node)
            clone["children"] = truncate_tree(node["children"], budget)
            budget -= count_pages(clone["children"])
            kept.append(clone)
        else:
            kept.append(node)
    return kept


# ---------------------------------------------------------------------------
# 同步器
# ---------------------------------------------------------------------------
class Syncer:
    def __init__(self, client, state: dict, args, convert, pacer: Pacer) -> None:
        self.client = client
        self.state = state
        self.args = args
        self.convert = convert
        self.pacer = pacer
        self.state_path = Path(args.state)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._last_save = 0.0
        self.stats = {"created": 0, "reused": 0, "content": 0, "index": 0,
                      "skipped_content": 0, "failed": 0, "blocks": 0}
        self.failures: list[dict] = []
        self.done = 0
        self.planned = 0

    # -- 基础设施 -----------------------------------------------------------
    def install_pacer(self) -> None:
        original = self.client._request

        def paced(method, path, **kwargs):
            self.pacer.wait()
            return original(method, path, **kwargs)

        self.client._request = paced  # type: ignore[method-assign]

    def save_state(self, *, force: bool = False) -> None:
        if self.args.dry_run:
            return
        with self._save_lock:
            now = time.monotonic()
            if not force and now - self._last_save < 2.0:
                return
            self._last_save = now
            with self._lock:
                payload = json.dumps(self.state, ensure_ascii=False, indent=2)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.state_path)

    def log(self, message: str) -> None:
        with self._lock:
            self.done += 1
            done = self.done
        print(f"[{done}/{self.planned}] {message}", flush=True)

    def record_failure(self, rel: str, stage: str, error: str) -> None:
        with self._lock:
            self.stats["failed"] += 1
            self.failures.append({"rel": rel, "stage": stage, "error": error})
        self.log(f"失败 {stage} {rel} :: {error}")

    # -- 阶段 1：建页 -------------------------------------------------------
    def ensure_page(self, node: dict, parent_id: str) -> dict | None:
        rel = node["rel"]
        with self._lock:
            record = self.state.get("pages", {}).get(rel)
        if record and record.get("id"):
            with self._lock:
                self.stats["reused"] += 1
            self.log(f"复用 {node['title']}")
            return record
        if self.args.dry_run:
            with self._lock:
                self.stats["created"] += 1
            self.log(f"[dry-run] 建页 {node['title']}  parent={parent_id}")
            return {"id": f"dry-{rel}", "url": "", "title": node["title"], "kind": node["kind"]}
        try:
            page = self.client.create_page(
                parent_id, node["title"], parent_type="page",
                icon=pick_icon(node["name"], node["kind"]),
            )
        except Exception as exc:  # noqa: BLE001
            self.record_failure(rel, "建页", str(exc))
            return None
        record = {"id": page["id"], "url": page.get("url", ""), "title": node["title"],
                  "kind": node["kind"]}
        with self._lock:
            self.state.setdefault("pages", {})[rel] = record
            self.stats["created"] += 1
        self.save_state()
        self.log(f"建页 {node['title']}  -> {record['url']}")
        return record

    def create_all(self, tree: list[dict], parent_id: str) -> None:
        level: list[tuple[dict, str]] = [(node, parent_id) for node in tree]
        with ThreadPoolExecutor(max_workers=self.args.workers) as pool:
            while level:
                pairs = list(pool.map(lambda item: (item[0], self.ensure_page(item[0], item[1])),
                                      level))
                level = [(child, record["id"])
                         for node, record in pairs if record
                         for child in (node["children"] if node["kind"] == "dir" else [])]

    # -- 阶段 2：文件正文 ---------------------------------------------------
    def write_content(self, node: dict) -> None:
        rel = node["rel"]
        with self._lock:
            record = self.state.get("pages", {}).get(rel)
            written = self.state.get("content", {}).get(rel)
        if not record:
            return
        if written:
            with self._lock:
                self.stats["skipped_content"] += 1
            self.log(f"跳过正文 {node['title']}（已写过）")
            return
        blocks = self.file_blocks(node)
        if self.args.dry_run:
            with self._lock:
                self.stats["blocks"] += len(blocks)
                self.stats["content"] += 1
            self.log(f"[dry-run] 追加 {len(blocks)} 块 正文 {rel}")
            return
        try:
            created = self.client.append_children(record["id"], sanitize_blocks(blocks))
        except Exception as exc:  # noqa: BLE001
            self.record_failure(rel, "正文", str(exc))
            return
        with self._lock:
            self.state.setdefault("content", {})[rel] = True
            self.stats["blocks"] += len(created)
            self.stats["content"] += 1
        self.save_state()
        self.log(f"正文 {node['title']}  ({len(created)} 块)")

    def file_blocks(self, node: dict) -> list[dict]:
        path = Path(node["abs"])
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return [meta_callout([("读取失败", str(exc))], icon="⚠️")]
        if b"\x00" in raw[:4096]:
            return [meta_callout([("提示", "二进制文件，未写入正文")], icon="⚠️")]

        text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
        header = meta_callout([
            ("路径", node["rel"]),
            ("大小", f"{node['size_human']}（{node['size']} 字节）"),
            ("修改时间", node["mtime"]),
            ("行数", str(text.count("\n") + 1)),
        ])
        blocks: list[dict] = [header, rich_text_block("divider", "")]

        content = self.convert(text, node["ext"])
        max_blocks = 10 ** 9 if self.args.full_content else self.args.max_blocks
        if len(content) > max_blocks:
            content = content[:max_blocks]
            blocks.append(meta_callout(
                [("提示", f"文档过长，本页只写入前 {max_blocks} 个内容块；"
                          f"完整文件见 {node['abs']}")], icon="✂️"))
        blocks.extend(content)
        return blocks

    # -- 阶段 3：目录索引 ---------------------------------------------------
    def write_index(self, node: dict) -> None:
        rel = node["rel"]
        with self._lock:
            record = self.state.get("pages", {}).get(rel)
            done = self.state.get("indexed", {}).get(rel)
        if not record or done:
            return
        children: list[dict] = []
        for child in node["children"]:
            with self._lock:
                child_record = self.state.get("pages", {}).get(child["rel"])
            if child_record:
                children.append({"kind": child["kind"], "title": child["title"],
                                 "url": child_record.get("url", ""), "node": child})
        blocks = self.dir_blocks(node, children)
        if self.args.dry_run:
            with self._lock:
                self.stats["blocks"] += len(blocks)
                self.stats["index"] += 1
            self.log(f"[dry-run] 追加 {len(blocks)} 块 目录索引 {rel}")
            return
        try:
            created = self.client.append_children(record["id"], sanitize_blocks(blocks))
        except Exception as exc:  # noqa: BLE001
            self.record_failure(rel, "目录索引", str(exc))
            return
        with self._lock:
            self.state.setdefault("indexed", {})[rel] = True
            self.stats["blocks"] += len(created)
            self.stats["index"] += 1
        self.save_state()
        self.log(f"目录索引 {node['title']}  ({len(created)} 块)")

    def dir_blocks(self, node: dict, children: list[dict]) -> list[dict]:
        files, dirs = count_nodes(node["children"])
        blocks: list[dict] = [
            meta_callout([("层级", node["number"]),
                          ("相对路径", node["rel"]),
                          ("子目录", str(dirs)),
                          ("文档文件", str(files))], icon="📁"),
        ]
        subdirs = [c for c in children if c["kind"] == "dir"]
        docs = [c for c in children if c["kind"] == "file"]
        if subdirs:
            blocks.append(rich_text_block("heading_2", "子目录"))
            for child in subdirs:
                blocks.append(link_bullet(child["title"], child["url"],
                                          f"{count_nodes(child['node']['children'])[0]} 个文档"))
        if docs:
            blocks.append(rich_text_block("heading_2", "文档"))
            for child in docs:
                blocks.append(link_bullet(child["title"], child["url"],
                                          child["node"].get("size_human", "")))
        return blocks

    # -- 并行执行器 ---------------------------------------------------------
    def run_parallel(self, fn, nodes: list[dict], label: str) -> None:
        if not nodes:
            return
        with ThreadPoolExecutor(max_workers=self.args.workers) as pool:
            list(pool.map(fn, nodes))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把文档清单按层级写进 Notion")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--parent", default=DEFAULT_PARENT, help="父页面 ID 或 URL")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE, help="工作空间 key（A/B）")
    parser.add_argument("--config", default=None, help="notionsync 配置文件路径")
    parser.add_argument("--throttle", type=float, default=0.34,
                        help="全局请求间隔下限（秒），Notion 平均上限 3 req/s")
    parser.add_argument("--workers", type=int, default=4, help="并发请求线程数")
    parser.add_argument("--max-blocks", type=int, default=MAX_BLOCKS_DEFAULT,
                        help="单页正文最多写入的块数")
    parser.add_argument("--full-content", action="store_true", help="不截断长文档")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个页面（试跑）")
    parser.add_argument("--only", default="", help="只同步 rel 前缀匹配的子树")
    parser.add_argument("--root-index", action=argparse.BooleanOptionalAction, default=True,
                        help="是否在父页面写入顶层索引")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写 Notion")
    parser.add_argument("--restart", action="store_true", help="忽略 state，重新建页")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"清单不存在：{manifest_path}，请先运行 scan_repo_docs.py", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tree: list[dict] = manifest["tree"]

    if args.only:
        prefix = args.only.strip("/").replace("\\", "/")
        tree = filter_tree(tree, prefix)
        if not tree:
            print(f"--only {prefix} 没有匹配到任何节点", file=sys.stderr)
            return 2
    if args.limit:
        tree = truncate_tree(tree, args.limit)

    NotionClient, NotionError, load_registry, extract_id, markdown_to_blocks = load_notionsync()

    parent_id = extract_id(args.parent)
    registry = load_registry(Path(args.config) if args.config else None)
    workspace = registry.resolve(args.workspace)
    client = NotionClient(workspace.token, api_version=workspace.api_version,
                          key=workspace.key, label=workspace.label, max_retries=6)

    state_path = Path(args.state)
    state: dict = {"pages": {}, "content": {}, "indexed": {}, "meta": {}}
    if state_path.is_file() and not args.restart:
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            for key in ("pages", "content", "indexed"):
                state[key] = loaded.get(key) or {}
        except json.JSONDecodeError:
            print("state 文件损坏，已忽略（建议加 --restart 重跑）", file=sys.stderr)

    syncer = Syncer(client, state, args, convert=make_converter(markdown_to_blocks),
                    pacer=Pacer(args.throttle))
    syncer.install_pacer()
    syncer.planned = count_pages(tree)

    print("=" * 78)
    print(f"目标页        : {parent_id}")
    print(f"工作空间      : {workspace.key} / {workspace.label}（{registry.source}）")
    print(f"清单          : {manifest_path}（{manifest.get('mode')} 模式，"
          f"{manifest['totals']['files']} 文件）")
    print(f"待处理页面    : {syncer.planned}（目录 {len(list(iter_dirs(tree)))} + "
          f"文件 {len(list(iter_files(tree)))}）")
    print(f"并发/限速     : {args.workers} 线程，{args.throttle}s/请求下限，"
          f"单页 {args.max_blocks} 块"
          f"{'（full-content 关闭截断）' if args.full_content else ''}")
    print(f"续传 state    : {state_path}（已记录 {len(state['pages'])} 页）")
    print("=" * 78, flush=True)

    started = time.time()
    try:
        # 阶段 1：按层并发建页
        print("--- 阶段 1/3：创建页面 ---", flush=True)
        syncer.create_all(tree, parent_id)
        # 阶段 2：并发写文件正文
        print("--- 阶段 2/3：写入文件正文 ---", flush=True)
        syncer.run_parallel(syncer.write_content, list(iter_files(tree)), "正文")
        # 阶段 3：并发写目录索引
        print("--- 阶段 3/3：写入目录索引 ---", flush=True)
        syncer.run_parallel(syncer.write_index, list(iter_dirs(tree)), "索引")

        if args.root_index and not args.only:
            marker = f"root_index::{parent_id}"
            if not state["indexed"].get(marker):
                total_files, total_dirs = count_nodes(tree)
                blocks = [
                    rich_text_block("heading_1", "仓库文档索引"),
                    rich_text_block(
                        "paragraph",
                        f"扫描根目录：{manifest['root']}　模式：{manifest['mode']}　"
                        f"文档文件：{total_files}　目录：{total_dirs}　"
                        f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}"),
                ]
                for node in tree:
                    with syncer._lock:  # noqa: SLF001
                        record = state["pages"].get(node["rel"])
                    if not record:
                        continue
                    if node["kind"] == "dir":
                        sub_files, sub_dirs = count_nodes(node["children"])
                        suffix = f"{sub_files} 个文档 / {sub_dirs} 个子目录"
                    else:
                        suffix = node.get("size_human", "")
                    blocks.append(link_bullet(node["title"], record.get("url", ""), suffix))
                if not args.dry_run:
                    syncer.client.append_children(parent_id, sanitize_blocks(blocks))
                    with syncer._lock:
                        state["indexed"][marker] = True
                syncer.log("顶层索引")
    except KeyboardInterrupt:
        print("\n收到中断，已保存 state，可重跑续传", file=sys.stderr)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        syncer.stats["failed"] += 1

    syncer.save_state(force=True)
    elapsed = time.time() - started
    print("=" * 78)
    print(f"完成：新建 {syncer.stats['created']} 页 / 复用 {syncer.stats['reused']} 页 / "
          f"写正文 {syncer.stats['content']} 篇 / 目录索引 {syncer.stats['index']} 个")
    print(f"     写入块数 {syncer.stats['blocks']}，失败 {syncer.stats['failed']}，"
          f"耗时 {elapsed / 60:.1f} 分钟")
    if syncer.failures:
        report = HERE / "out" / "sync_failures.json"
        report.write_text(json.dumps(syncer.failures, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"     失败清单：{report}（重跑本脚本即会重试这些页面）")
    print(f"     目标页：https://app.notion.com/p/{parent_id.replace('-', '')}")
    print("=" * 78)
    return 0 if syncer.stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
