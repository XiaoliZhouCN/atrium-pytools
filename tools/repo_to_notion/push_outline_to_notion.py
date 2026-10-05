#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_outline_to_notion.py — 把 ``scan_repo_outline.py`` 的目录大纲写进 Project Manager。

版式（与上一版「编号文件夹页」完全不同）
--------------------------------------
* **目录 = 标题**：第一层 ``heading_1``、第二层 ``heading_2``、第三层 ``heading_3``；
  第四层及更深用带全角空格缩进的列表项（Notion 只有三级标题，且 API 单次最多嵌套两层）。
* **文件 = 子页面**：每个文档一个页面，在大纲里以「📄 页面 mention」的形式就地链出，
  点开就是文件全文。所有文件页集中挂在一个容器页下，避免根页面被几百个自动生成的
  子页面链接刷屏。
* **截断提示**：超过 ``--max-dir-depth`` 的目录名后面带 ``⋯``。

流程
----
1. ``--reset``（默认开）：把根页面下旧的子页面整棵归档，并清空旧的正文块。
2. 建容器页 ``📄 文档文件页（自动生成）``。
3. 并发建文件页并写入正文（断点续传，state 记在 ``out/outline_state.json``）。
4. 在根页面写出整份大纲（标题 + 缩进 + 页面链接）。

用法::

    python push_outline_to_notion.py --dry-run
    python push_outline_to_notion.py                 # 归档旧树并写入新格式
    python push_outline_to_notion.py --no-reset      # 不动旧内容，只补写大纲
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from push_to_notion import (  # noqa: E402
    DEFAULT_PARENT, DEFAULT_WORKSPACE, MAX_BLOCKS_DEFAULT, Pacer, iter_files,
    make_converter, meta_callout, pick_icon, rich_text_block, sanitize_blocks,
    load_notionsync,
)

DEFAULT_OUTLINE = HERE / "out" / "outline.json"
DEFAULT_STATE = HERE / "out" / "outline_state.json"
CONTAINER_TITLE = "📄 文档文件页（自动生成）"
INDENT = "\u3000"          # 全角空格，Notion 会原样保留
HEADING_MAX_DEPTH = 3      # Notion 只有 heading_1/2/3


# ---------------------------------------------------------------------------
# 大纲渲染
# ---------------------------------------------------------------------------
def mention_bullet(title: str, page_id: str, prefix: str = "📄 ") -> dict:
    """一个「📄 页面名」列表项：页面 mention，点开直达全文。"""
    rich: list[dict] = []
    if prefix:
        rich.append({"type": "text", "text": {"content": prefix}})
    rich.append({"type": "mention", "mention": {"type": "page", "page": {"id": page_id}}})
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rich}}


def dir_block(node: dict) -> dict:
    depth = node["depth"]
    name = node["name"] + (" ⋯" if node.get("truncated") else "")
    if depth <= HEADING_MAX_DEPTH:
        return rich_text_block(f"heading_{depth}", name)
    return rich_text_block("bulleted_list_item", INDENT * (depth - HEADING_MAX_DEPTH) + name)


def build_outline(tree: list[dict], pages: dict, stats: dict, root_url: str) -> list[dict]:
    blocks: list[dict] = [
        rich_text_block("paragraph",
                        f"本地仓库文档地图　·　扫描根目录 {stats['root']}　·　"
                        f"目录 {stats['dirs']} 个　·　文件页 {stats['files']} 个　·　"
                        f"生成时间 {datetime.now().astimezone().isoformat(timespec='seconds')}"),
        rich_text_block("paragraph",
                        "图例：📄 = 可点开的文件页（点开即全文）；"
                        "⋯ = 该目录还有更深的层级，本次未展开；"
                        "第四层及更深的目录用全角缩进表示层级。"),
        rich_text_block("divider", ""),
    ]

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            if node["kind"] == "dir":
                blocks.append(dir_block(node))
                walk(node["children"])
            else:
                record = pages.get(node["rel"])
                if record:
                    blocks.append(mention_bullet(node["name"], record["id"]))
                else:
                    blocks.append(rich_text_block(
                        "bulleted_list_item", "📄 " + node["name"] + "（未建页）"))

    walk(tree)
    return blocks


# ---------------------------------------------------------------------------
# 写入器
# ---------------------------------------------------------------------------
class OutlineWriter:
    def __init__(self, client, state: dict, args, convert) -> None:
        self.client = client
        self.state = state
        self.args = args
        self.convert = convert
        self.state_path = Path(args.state)
        self.pacer = Pacer(args.throttle)
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._last_save = 0.0
        self.stats = {"created": 0, "reused": 0, "content": 0, "blocks": 0, "failed": 0}
        self.failures: list[dict] = []
        self.done = 0

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
        print(f"[{done}] {message}", flush=True)

    # -- 步骤 1：归档旧树 ---------------------------------------------------
    def reset_root(self, root_id: str) -> None:
        if self.state.get("reset_done"):
            print("旧内容此前已清理，跳过 reset")
            return
        children = self.client.list_children(root_id, page_size=100)
        old_pages = [b for b in children if b.get("type") == "child_page"]
        print(f"归档旧子页面 {len(old_pages)} 个（其余块会被清空）")
        for block in old_pages:
            if self.args.dry_run:
                print(f"  [dry-run] 归档 {block['id']}")
                continue
            try:
                self.client.update_page(block["id"], in_trash=True)
                print(f"  已归档 {block['id']}")
            except Exception as exc:  # noqa: BLE001
                self.failures.append({"rel": block["id"], "stage": "归档", "error": str(exc)})

        leftovers = [b for b in self.client.list_children(root_id, page_size=100)
                     if b.get("type") != "child_page"]
        print(f"清空旧正文块 {len(leftovers)} 个")
        for block in leftovers:
            if self.args.dry_run:
                continue
            try:
                self.client.delete_block(block["id"])
            except Exception as exc:  # noqa: BLE001
                self.failures.append({"rel": block["id"], "stage": "删块", "error": str(exc)})

        if not self.args.dry_run:
            self.state["reset_done"] = True
            self.save_state(force=True)

    # -- 步骤 2：容器页 -----------------------------------------------------
    def ensure_container(self, root_id: str) -> dict:
        record = self.state.get("container")
        if record and record.get("id"):
            self.log(f"复用容器页 {record['url']}")
            return record
        if self.args.dry_run:
            self.log(f"[dry-run] 建容器页 {CONTAINER_TITLE}")
            return {"id": "dry-container", "url": ""}
        page = self.client.create_page(root_id, CONTAINER_TITLE, parent_type="page",
                                       icon="🗂️",
                                       children=[rich_text_block(
                                           "paragraph",
                                           "本页集中存放仓库文档的文件页，供 Project Manager "
                                           "上的大纲逐条链接。请从大纲进入，不要手动调整层级。")])
        record = {"id": page["id"], "url": page.get("url", "")}
        self.state["container"] = record
        self.save_state(force=True)
        self.log(f"建容器页 -> {record['url']}")
        return record

    # -- 步骤 3：文件页 -----------------------------------------------------
    def file_blocks(self, node: dict) -> list[dict]:
        try:
            raw = Path(node["abs"]).read_bytes()
        except OSError as exc:
            return [meta_callout([("读取失败", str(exc))], icon="⚠️")]
        if b"\x00" in raw[:4096]:
            return [meta_callout([("提示", "二进制文件，未写入正文")], icon="⚠️")]
        text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
        blocks = [meta_callout([
            ("路径", node["rel"]),
            ("大小", f"{node['size_human']}（{node['size']} 字节）"),
            ("修改时间", node["mtime"]),
            ("行数", str(text.count("\n") + 1)),
        ]), rich_text_block("divider", "")]
        content = self.convert(text, node["ext"])
        limit = 10 ** 9 if self.args.full_content else self.args.max_blocks
        if len(content) > limit:
            content = content[:limit]
            blocks.append(meta_callout(
                [("提示", f"文档过长，本页只写入前 {limit} 个内容块；完整文件见 {node['abs']}")],
                icon="✂️"))
        return blocks + content

    def ensure_file_page(self, node: dict, container_id: str) -> dict | None:
        rel = node["rel"]
        with self._lock:
            record = self.state.setdefault("pages", {}).get(rel)
        if record and record.get("id"):
            with self._lock:
                self.stats["reused"] += 1
            self.log(f"复用 {node['name']}")
            return record
        if self.args.dry_run:
            self.log(f"[dry-run] 建页 {node['name']}")
            return {"id": f"dry-{rel}", "url": ""}
        try:
            page = self.client.create_page(container_id, node["name"], parent_type="page",
                                           icon=pick_icon(node["name"], "file"))
        except Exception as exc:  # noqa: BLE001
            self.stats["failed"] += 1
            self.failures.append({"rel": rel, "stage": "建页", "error": str(exc)})
            self.log(f"失败 建页 {node['name']} :: {exc}")
            return None
        record = {"id": page["id"], "url": page.get("url", "")}
        with self._lock:
            self.state.setdefault("pages", {})[rel] = record
            self.stats["created"] += 1
        self.save_state()
        self.log(f"建页 {node['name']} -> {record['url']}")
        return record

    def write_content(self, node: dict) -> None:
        rel = node["rel"]
        with self._lock:
            record = self.state.get("pages", {}).get(rel)
            written = self.state.get("content", {}).get(rel)
        if not record or written:
            return
        if self.args.dry_run:
            self.log(f"[dry-run] 写正文 {node['name']}")
            return
        try:
            created = self.client.append_children(record["id"],
                                                   sanitize_blocks(self.file_blocks(node)))
        except Exception as exc:  # noqa: BLE001
            self.stats["failed"] += 1
            self.failures.append({"rel": rel, "stage": "正文", "error": str(exc)})
            self.log(f"失败 正文 {node['name']} :: {exc}")
            return
        with self._lock:
            self.state.setdefault("content", {})[rel] = True
            self.stats["content"] += 1
            self.stats["blocks"] += len(created)
        self.save_state()
        self.log(f"正文 {node['name']}（{len(created)} 块）")

    # -- 步骤 4：大纲 -------------------------------------------------------
    def write_outline(self, root_id: str, tree: list[dict], stats: dict) -> None:
        marker = f"outline::{root_id}"
        if self.state.get("outline_written"):
            print("大纲此前已写入，跳过（如需重写请删掉 state 里的 outline_written）")
            return
        pages = self.state.get("pages", {})
        blocks = build_outline(tree, pages, stats, root_id)
        print(f"大纲共 {len(blocks)} 个块")
        if self.args.dry_run:
            for block in blocks[:25]:
                print("  [dry-run]", block["type"])
            print("  ...")
            return
        created = self.client.append_children(root_id, sanitize_blocks(blocks))
        self.state["outline_written"] = True
        self.state["outline_blocks"] = len(created)
        self.save_state(force=True)
        self.log(f"大纲写入完成（{len(created)} 块）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把目录大纲写入 Notion Project Manager")
    parser.add_argument("--outline", default=str(DEFAULT_OUTLINE))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--parent", default=DEFAULT_PARENT)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--config", default=None)
    parser.add_argument("--throttle", type=float, default=0.34)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-blocks", type=int, default=MAX_BLOCKS_DEFAULT)
    parser.add_argument("--full-content", action="store_true")
    parser.add_argument("--reset", action=argparse.BooleanOptionalAction, default=True,
                        help="是否先归档根页面下旧的子页面并清空旧正文")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    outline_path = Path(args.outline)
    if not outline_path.is_file():
        print(f"大纲不存在：{outline_path}，请先运行 scan_repo_outline.py", file=sys.stderr)
        return 2
    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    tree = outline["tree"]
    stats = {"root": outline["root"], **outline["stats"]}
    files = list(iter_files(tree))

    NotionClient, NotionError, load_registry, extract_id, markdown_to_blocks = load_notionsync()
    root_id = extract_id(args.parent)
    registry = load_registry(Path(args.config) if args.config else None)
    workspace = registry.resolve(args.workspace)
    client = NotionClient(workspace.token, api_version=workspace.api_version,
                          key=workspace.key, label=workspace.label, max_retries=6)

    state_path = Path(args.state)
    state: dict = {"pages": {}, "content": {}, "container": None}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({k: v for k, v in loaded.items() if v is not None})
        except json.JSONDecodeError:
            print("state 损坏，忽略", file=sys.stderr)

    writer = OutlineWriter(client, state, args, convert=make_converter(markdown_to_blocks))
    writer.install_pacer()

    print("=" * 78)
    print(f"根页面        : {root_id}")
    print(f"工作空间      : {workspace.key} / {workspace.label}（{registry.source}）")
    print(f"大纲          : {outline_path}（目录 {stats['dirs']}，文件页 {len(files)}，"
          f"展开到第 {outline.get('max_dir_depth')} 层，截断 {stats.get('clipped_dirs', 0)} 个）")
    print(f"并发/限速     : {args.workers} 线程，{args.throttle}s/请求下限")
    print(f"续传 state    : {state_path}（已记录 {len(state['pages'])} 页）")
    print("=" * 78, flush=True)

    started = time.time()
    try:
        if args.reset:
            print("--- 步骤 1/4：归档旧内容 ---", flush=True)
            writer.reset_root(root_id)
        print("--- 步骤 2/4：容器页 ---", flush=True)
        container = writer.ensure_container(root_id)
        print("--- 步骤 3/4：文件页 ---", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(lambda node: writer.ensure_file_page(node, container["id"]), files))
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(writer.write_content, files))
        print("--- 步骤 4/4：写出大纲 ---", flush=True)
        writer.write_outline(root_id, tree, stats)
    except KeyboardInterrupt:
        print("\n已中断，state 已保存，可重跑续传", file=sys.stderr)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        writer.stats["failed"] += 1

    writer.save_state(force=True)
    print("=" * 78)
    print(f"完成：新建 {writer.stats['created']} 页 / 复用 {writer.stats['reused']} 页 / "
          f"正文 {writer.stats['content']} 篇 / 失败 {writer.stats['failed']} / "
          f"耗时 {(time.time() - started) / 60:.1f} 分钟")
    if writer.failures:
        report = HERE / "out" / "outline_failures.json"
        report.write_text(json.dumps(writer.failures, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"失败清单：{report}")
    print(f"根页面：https://app.notion.com/p/{root_id.replace('-', '')}")
    print("=" * 78)
    return 0 if writer.stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
