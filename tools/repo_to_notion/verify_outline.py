#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_outline.py — 校验新版「目录大纲 + 文件页」是否与清单一致。

校验项
------
1. **根页面结构**：只剩 1 个子页面（容器页）；旧版编号树的子页面已全部归档；
   大纲块数量 ≥ 目录数 + 文件页数。
2. **文件页**：清单里每个文件都能在容器页下找到同名子页面，且正文块 ≥ 2。
3. **旧树清理**：抽查旧 state 里的页面，确认已进回收站。
4. 打印大纲前若干块，肉眼确认层级/缩进/链接。

用法::

    python verify_outline.py
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from push_to_notion import (  # noqa: E402
    DEFAULT_PARENT, DEFAULT_WORKSPACE, Pacer, iter_dirs, iter_files, load_notionsync,
)

DEFAULT_OUTLINE = HERE / "out" / "outline.json"
DEFAULT_STATE = HERE / "out" / "outline_state.json"
OLD_STATE = HERE / "out" / "sync_state.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验目录大纲与文件页")
    parser.add_argument("--outline", default=str(DEFAULT_OUTLINE))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--old-state", default=str(OLD_STATE))
    parser.add_argument("--parent", default=DEFAULT_PARENT)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--config", default=None)
    parser.add_argument("--sample", type=int, default=15, help="正文抽样数量")
    parser.add_argument("--preview", type=int, default=24, help="打印前 N 个大纲块")
    parser.add_argument("--throttle", type=float, default=0.34)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    outline = json.loads(Path(args.outline).read_text(encoding="utf-8"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    tree = outline["tree"]
    dirs = list(iter_dirs(tree))
    files = list(iter_files(tree))
    pages = state.get("pages", {})
    problems: list[str] = []

    print(f"清单：目录 {len(dirs)}，文件页 {len(files)}；state 记录 {len(pages)} 页")
    missing_state = [n["rel"] for n in files if n["rel"] not in pages]
    if missing_state:
        problems.append(f"state 缺 {len(missing_state)} 个文件页记录")
    missing_content = [n["rel"] for n in files
                       if not state.get("content", {}).get(n["rel"])]
    if missing_content:
        problems.append(f"{len(missing_content)} 个文件页没写正文")

    NotionClient, NotionError, load_registry, extract_id, _ = load_notionsync()
    registry = load_registry(Path(args.config) if args.config else None)
    workspace = registry.resolve(args.workspace)
    client = NotionClient(workspace.token, api_version=workspace.api_version,
                          key=workspace.key, label=workspace.label, max_retries=4)
    pacer = Pacer(args.throttle)
    original = client._request

    def paced(method, path, **kwargs):
        pacer.wait()
        return original(method, path, **kwargs)

    client._request = paced  # type: ignore[method-assign]

    # -- 1. 根页面 -----------------------------------------------------------
    root_id = extract_id(args.parent)
    root_blocks = client.list_children(root_id)
    child_pages = [b for b in root_blocks if b.get("type") == "child_page"]
    container = state.get("container") or {}
    print(f"根页面块：{len(root_blocks)}（其中子页面 {len(child_pages)}）")
    if len(child_pages) != 1 or child_pages[0]["id"] != container.get("id"):
        problems.append(f"根页面子页面异常：{[b['id'] for b in child_pages]}")
    expected_min = len(dirs) + len(files)
    outline_blocks = [b for b in root_blocks if b.get("type") != "child_page"]
    if len(outline_blocks) < expected_min:
        problems.append(f"大纲块偏少：{len(outline_blocks)} < {expected_min}")

    kinds: dict[str, int] = {}
    for block in outline_blocks:
        kinds[block["type"]] = kinds.get(block["type"], 0) + 1
    print(f"大纲块类型：{dict(sorted(kinds.items(), key=lambda kv: -kv[1]))}")

    def plain(block: dict) -> str:
        payload = block.get(block["type"]) or {}
        parts = []
        for item in payload.get("rich_text") or []:
            if item.get("type") == "mention":
                parts.append("[[page:" + str((item.get("mention") or {}).get("page", {}).get("id"))[:8] + "]]")
            else:
                parts.append((item.get("text") or {}).get("content", ""))
        return "".join(parts)

    print(f"--- 大纲前 {args.preview} 块 ---")
    for block in outline_blocks[:args.preview]:
        depth = ""
        if block["type"].startswith("heading_"):
            depth = "#" * int(block["type"][-1]) + " "
        print(f"  {depth}{plain(block)}")

    # -- 2. 容器页下的文件页 --------------------------------------------------
    if container.get("id"):
        container_blocks = client.list_children(container["id"])
        found = {b["id"]: (b.get("child_page") or {}).get("title", "")
                 for b in container_blocks if b.get("type") == "child_page"}
        print(f"容器页子页面：{len(found)}（期望 {len(files)}）")
        for node in files:
            record = pages.get(node["rel"])
            if not record:
                continue
            title = found.get(record["id"])
            if title is None:
                problems.append(f"容器页下缺页面：{node['name']} ({node['rel']})")
            elif title != node["name"]:
                problems.append(f"标题不一致：{title!r} != {node['name']!r}")
        extra = set(found) - {pages[n["rel"]]["id"] for n in files if n["rel"] in pages}
        if extra:
            problems.append(f"容器页下多出 {len(extra)} 个页面")

    # -- 3. 正文抽样 + 旧树清理 ----------------------------------------------
    sample = files[:args.sample]
    for node in sample:
        record = pages.get(node["rel"])
        if not record:
            continue
        blocks = client.list_children(record["id"])
        if len(blocks) < 2:
            problems.append(f"正文块不足：{node['name']}（{len(blocks)}）")

    old_path = Path(args.old_state)
    if old_path.is_file():
        old = json.loads(old_path.read_text(encoding="utf-8")).get("pages", {})
        sample_ids = [old[k]["id"] for k in list(old)[:5]]
        trashed = 0
        for page_id in sample_ids:
            page = client.get_page(page_id)
            if page.get("in_trash") or page.get("archived"):
                trashed += 1
        print(f"旧树抽查：{trashed}/{len(sample_ids)} 已进回收站")
        if trashed != len(sample_ids):
            problems.append(f"旧树未清理干净（{trashed}/{len(sample_ids)}）")

    print("=" * 70)
    if problems:
        print(f"发现问题 {len(problems)} 项：")
        for item in problems[:20]:
            print("  -", item)
    else:
        print("全部通过 ✅")
    report = HERE / "out" / "verify_outline_report.json"
    report.write_text(json.dumps({
        "dirs": len(dirs), "files": len(files), "root_blocks": len(root_blocks),
        "child_pages": len(child_pages), "outline_block_kinds": kinds,
        "problems": problems,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
