#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_tree_to_notion.py — 把目录大纲写成「可折叠 toggle 树」到 Notion 页面。

版式（v3）
---------
* **每个目录 = 一个 toggle 块（▶ 可折叠）**，子目录是嵌套 toggle，逐层可展开/收起。
* **每个文件 = 一个子页面**，以「📄 页面 mention」列表项挂在所属目录的 toggle 里。
* 页面顶部三行：统计信息、图例、分隔线。

为什么不是标题
--------------
Notion 只有 heading_1/2/3 三级，第 4 层以后没法再用标题；而 toggle 可以无限嵌套，
折叠起来还能把 470 个目录收进一屏。

写入方式
--------
toggle 的 id 只有写进去之后才知道，所以按层 BFS 写入：
第 1 层写到页面下，拿到 id 后并发写第 2 层，以此类推（Notion 单次请求最多嵌套两层，
所以必须逐层来）。总共约「有子目录的目录数」次请求。

用法::

    python push_tree_to_notion.py --parent <页面 ID 或 URL> --dry-run
    python push_tree_to_notion.py --parent <页面 ID 或 URL>
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
    DEFAULT_WORKSPACE, MAX_BLOCKS_DEFAULT, Pacer, iter_dirs, iter_files,
    make_converter, meta_callout, pick_icon, rich_text_block, sanitize_blocks,
    load_notionsync,
)

DEFAULT_OUTLINE = HERE / "out" / "outline.json"
DEFAULT_STATE = HERE / "out" / "tree_state.json"
CONTAINER_TITLE = "📄 文档文件页（自动生成）"


# ---------------------------------------------------------------------------
# 块构造
# ---------------------------------------------------------------------------
def toggle_block(name: str, icon: str = "▶") -> dict:
    return {"object": "block", "type": "toggle",
            "toggle": {"rich_text": [{"type": "text", "text": {"content": name}}]}}


def mention_bullet(name: str, page_id: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [
                {"type": "text", "text": {"content": "📄 "}},
                {"type": "mention", "mention": {"type": "page", "page": {"id": page_id}}},
            ]}}


def header_blocks(stats: dict, files: int) -> list[dict]:
    return [
        rich_text_block("paragraph",
                        f"本地仓库文档地图　·　扫描根目录 {stats['root']}　·　"
                        f"目录 {stats['dirs']} 个（可折叠）　·　文件页 {files} 个　·　"
                        f"生成时间 {datetime.now().astimezone().isoformat(timespec='seconds')}"),
        rich_text_block("paragraph",
                        "图例：▶ 目录可点击展开 / 收起；📄 为该文件的子页面，点开即全文；"
                        "目录名后的 ⋯ 表示该目录还有更深层级、本次未展开。"),
        rich_text_block("divider", ""),
    ]


class TreeWriter:
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
        self.stats = {"created": 0, "reused": 0, "content": 0, "blocks": 0,
                      "failed": 0, "toggles": 0}
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

    def fail(self, rel: str, stage: str, error: str) -> None:
        with self._lock:
            self.stats["failed"] += 1
            self.failures.append({"rel": rel, "stage": stage, "error": error})
        self.log(f"失败 {stage} {rel} :: {error}")

    # -- 页面清理 -----------------------------------------------------------
    def clear_page(self, page_id: str, *, archive_children: bool) -> None:
        blocks = self.client.list_children(page_id, page_size=100)
        keep = (self.state.get("container") or {}).get("id")
        pages = [b for b in blocks if b.get("type") == "child_page" and b["id"] != keep]
        others = [b for b in blocks if b.get("type") != "child_page"]
        print(f"页面现有块 {len(blocks)}：待归档子页面 {len(pages)}，待清正文 {len(others)}"
              f"（容器页 {'保留' if keep else '无'}）")
        if self.args.dry_run:
            return
        if archive_children:
            for block in pages:
                try:
                    self.client.update_page(block["id"], in_trash=True)
                except Exception as exc:  # noqa: BLE001
                    self.fail(block["id"], "归档", str(exc))
        for block in others:
            try:
                self.client.delete_block(block["id"])
            except Exception as exc:  # noqa: BLE001
                self.fail(block["id"], "删块", str(exc))
        if others or (archive_children and pages):
            print(f"已清理：归档 {len(pages) if archive_children else 0} 页，删除 {len(others)} 块")

    # -- 容器页里的孤儿页 ---------------------------------------------------
    def prune_orphans(self, container_id: str, files: list[dict]) -> None:
        """磁盘上已消失的文档，其文件页一并归档，避免容器页里留下死链。"""
        wanted = {self.state.get("pages", {}).get(n["rel"], {}).get("id") for n in files}
        wanted.discard(None)
        try:
            blocks = self.client.list_children(container_id, page_size=100)
        except Exception as exc:  # noqa: BLE001
            self.fail(container_id, "孤儿检查", str(exc))
            return
        orphans = [b for b in blocks
                   if b.get("type") == "child_page" and b["id"] not in wanted]
        if not orphans:
            return
        print(f"归档孤儿文件页 {len(orphans)} 个（源文件已不在磁盘上）")
        gone = {v["id"]: k for k, v in self.state.get("pages", {}).items()}
        for block in orphans:
            if self.args.dry_run:
                continue
            try:
                self.client.update_page(block["id"], in_trash=True)
            except Exception as exc:  # noqa: BLE001
                self.fail(block["id"], "归档孤儿", str(exc))
                continue
            rel = gone.get(block["id"])
            if rel:
                with self._lock:
                    self.state["pages"].pop(rel, None)
                    self.state["content"].pop(rel, None)
        self.save_state(force=True)

    # -- 容器页 -------------------------------------------------------------
    def ensure_container(self, page_id: str) -> dict:
        record = self.state.get("container")
        if record and record.get("id"):
            try:
                page = self.client.get_page(record["id"])
                if not (page.get("in_trash") or page.get("archived")):
                    self.log(f"复用容器页 {record['url']}")
                    return record
            except Exception:  # noqa: BLE001
                pass
            self.log("容器页已失效，重新创建")
        if self.args.dry_run:
            self.log(f"[dry-run] 建容器页 {CONTAINER_TITLE}")
            return {"id": "dry-container", "url": ""}
        page = self.client.create_page(
            page_id, CONTAINER_TITLE, parent_type="page", icon="🗂️",
            children=[rich_text_block(
                "paragraph",
                "本页集中存放仓库文档的文件页，供上级页面的折叠树逐条链接。"
                "请从折叠树进入，不要手动调整这里的层级。")])
        record = {"id": page["id"], "url": page.get("url", "")}
        self.state["container"] = record
        self.save_state(force=True)
        self.log(f"建容器页 -> {record['url']}")
        return record

    # -- 文件页 -------------------------------------------------------------
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

    def page_alive(self, page_id: str) -> bool:
        try:
            page = self.client.get_page(page_id)
        except Exception:  # noqa: BLE001
            return False
        return not (page.get("in_trash") or page.get("archived"))

    def ensure_file_page(self, node: dict, container_id: str) -> dict | None:
        rel = node["rel"]
        with self._lock:
            record = self.state.setdefault("pages", {}).get(rel)
            content_done = self.state.setdefault("content", {}).get(rel)
        if record and record.get("id") and not self.args.dry_run and not self.page_alive(record["id"]):
            self.log(f"页面已失效，重建 {node['name']}")
            with self._lock:
                self.state["pages"].pop(rel, None)
                self.state["content"].pop(rel, None)
            record, content_done = None, None
        if record and record.get("id") and content_done:
            with self._lock:
                self.stats["reused"] += 1
            return record
        if record and record.get("id") and not content_done:
            # 页面在、正文没写完：复用页面
            if not self.args.dry_run:
                try:
                    created = self.client.append_children(record["id"],
                                                           sanitize_blocks(self.file_blocks(node)))
                except Exception as exc:  # noqa: BLE001
                    self.fail(rel, "正文", str(exc))
                    return record
                with self._lock:
                    self.state["content"][rel] = True
                    self.stats["content"] += 1
                    self.stats["blocks"] += len(created)
                self.save_state()
                self.log(f"补正文 {node['name']}（{len(created)} 块）")
            return record
        if self.args.dry_run:
            self.log(f"[dry-run] 建页+正文 {node['name']}")
            return {"id": f"dry-{rel}", "url": ""}
        try:
            page = self.client.create_page(container_id, node["name"], parent_type="page",
                                           icon=pick_icon(node["name"], "file"))
        except Exception as exc:  # noqa: BLE001
            self.fail(rel, "建页", str(exc))
            return None
        record = {"id": page["id"], "url": page.get("url", "")}
        with self._lock:
            self.state.setdefault("pages", {})[rel] = record
            self.stats["created"] += 1
        self.save_state()
        try:
            created = self.client.append_children(record["id"],
                                                   sanitize_blocks(self.file_blocks(node)))
        except Exception as exc:  # noqa: BLE001
            self.fail(rel, "正文", str(exc))
            return record
        with self._lock:
            self.state.setdefault("content", {})[rel] = True
            self.stats["content"] += 1
            self.stats["blocks"] += len(created)
        self.save_state()
        self.log(f"建页 {node['name']} -> {record['url']}（正文 {len(created)} 块）")
        return record

    # -- toggle 树 ----------------------------------------------------------
    def append_nodes(self, parent_id: str, nodes: list[dict]) -> list[tuple[dict, str]]:
        """把 ``nodes`` 写进 ``parent_id``，返回 [(目录节点, 该 toggle 的块 id)]。"""
        blocks: list[dict] = []
        dirs_plan: list[tuple[int, dict]] = []
        for node in nodes:
            if node["kind"] == "dir":
                name = node["name"] + (" ⋯" if node.get("truncated") else "")
                dirs_plan.append((len(blocks), node))
                blocks.append(toggle_block(name))
            else:
                record = self.state.get("pages", {}).get(node["rel"])
                if record:
                    blocks.append(mention_bullet(node["name"], record["id"]))
                else:
                    blocks.append(rich_text_block("bulleted_list_item",
                                                  "📄 " + node["name"] + "（未建页）"))
        try:
            created = self.client.append_children(parent_id, sanitize_blocks(blocks))
        except Exception as exc:  # noqa: BLE001
            self.fail(parent_id, "写子树", str(exc))
            return []
        with self._lock:
            self.stats["blocks"] += len(created)
            self.stats["toggles"] += len(dirs_plan)
        return [(node, created[idx]["id"]) for idx, node in dirs_plan if idx < len(created)]

    def write_tree(self, page_id: str, tree: list[dict]) -> None:
        print("--- 写折叠树（按层 BFS）---", flush=True)
        frontier: list[tuple[str, list[dict]]] = [(page_id, tree)]
        level = 0
        while frontier:
            level += 1
            print(f"  第 {level} 层：{len(frontier)} 个父块", flush=True)
            if self.args.dry_run:
                for parent_id, nodes in frontier[:3]:
                    print(f"    [dry-run] {parent_id} <- {[n['name'] for n in nodes][:4]}")
                break
            with ThreadPoolExecutor(max_workers=self.args.workers) as pool:
                results = list(pool.map(lambda item: self.append_nodes(item[0], item[1]),
                                        frontier))
            frontier = [(block_id, node["children"])
                        for pairs in results for node, block_id in pairs
                        if node.get("children")]
        self.log("折叠树写入完成")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把目录大纲写成可折叠 toggle 树")
    parser.add_argument("--outline", default=str(DEFAULT_OUTLINE))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--parent", required=True, help="目标页面 ID 或 URL")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--config", default=None)
    parser.add_argument("--throttle", type=float, default=0.34)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-blocks", type=int, default=MAX_BLOCKS_DEFAULT)
    parser.add_argument("--full-content", action="store_true")
    parser.add_argument("--reset", action=argparse.BooleanOptionalAction, default=True,
                        help="是否先清空目标页面已有正文/归档已有子页面")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    outline_path = Path(args.outline)
    if not outline_path.is_file():
        print(f"大纲不存在：{outline_path}", file=sys.stderr)
        return 2
    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    tree = outline["tree"]
    stats = {"root": outline["root"], **outline["stats"]}
    files = list(iter_files(tree))
    dirs = list(iter_dirs(tree))

    NotionClient, NotionError, load_registry, extract_id, markdown_to_blocks = load_notionsync()
    page_id = extract_id(args.parent)
    registry = load_registry(Path(args.config) if args.config else None)
    workspace = registry.resolve(args.workspace)
    client = NotionClient(workspace.token, api_version=workspace.api_version,
                          key=workspace.key, label=workspace.label, max_retries=6)

    state_path = Path(args.state)
    state: dict = {"pages": {}, "content": {}, "container": None}
    if state_path.is_file():
        try:
            state.update({k: v for k, v in
                          json.loads(state_path.read_text(encoding="utf-8")).items()
                          if v is not None})
        except json.JSONDecodeError:
            print("state 损坏，忽略", file=sys.stderr)

    writer = TreeWriter(client, state, args, convert=make_converter(markdown_to_blocks))
    writer.install_pacer()

    print("=" * 78)
    print(f"目标页        : {page_id}")
    print(f"工作空间      : {workspace.key} / {workspace.label}（{registry.source}）")
    print(f"大纲          : {outline_path}（目录 {len(dirs)}，文件页 {len(files)}）")
    print(f"并发/限速     : {args.workers} 线程，{args.throttle}s/请求下限")
    print(f"续传 state    : {state_path}（已记录 {len(state['pages'])} 页）")
    print("=" * 78, flush=True)

    started = time.time()
    try:
        if args.reset:
            print("--- 步骤 1/4：清理目标页 ---", flush=True)
            writer.clear_page(page_id, archive_children=True)
        print("--- 步骤 2/4：容器页 ---", flush=True)
        container = writer.ensure_container(page_id)
        print("--- 步骤 3/4：文件页 ---", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(lambda node: writer.ensure_file_page(node, container["id"]), files))
        writer.prune_orphans(container["id"], files)
        print("--- 步骤 4/4：页面头部 + 折叠树 ---", flush=True)
        if not args.dry_run:
            writer.client.append_children(
                page_id, sanitize_blocks(header_blocks(stats, len(files))))
        writer.write_tree(page_id, tree)
    except KeyboardInterrupt:
        print("\n已中断，state 已保存，可重跑续传", file=sys.stderr)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        writer.stats["failed"] += 1

    writer.save_state(force=True)
    print("=" * 78)
    print(f"完成：新建 {writer.stats['created']} 页 / 复用 {writer.stats['reused']} 页 / "
          f"正文 {writer.stats['content']} 篇 / toggle {writer.stats['toggles']} 个 / "
          f"块 {writer.stats['blocks']} / 失败 {writer.stats['failed']} / "
          f"耗时 {(time.time() - started) / 60:.1f} 分钟")
    if writer.failures:
        report = HERE / "out" / "tree_failures.json"
        report.write_text(json.dumps(writer.failures, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"失败清单：{report}")
    print(f"目标页：https://app.notion.com/p/{page_id.replace('-', '')}")
    print("=" * 78)
    return 0 if writer.stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
