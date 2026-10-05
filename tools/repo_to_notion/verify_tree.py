#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_tree.py — 校验「可折叠 toggle 树」版式是否与清单一致。

校验项
------
1. 页面结构：恰好 1 个子页面（容器页）、2 段说明 + 1 条分隔线、顶层 toggle 数量与清单一致。
2. **整棵树逐层核对**：每个 toggle 的子块必须与清单里该目录的子目录 / 文件一一对应
   （子目录 → 同名 toggle；文件 → 指向该文件页的 📄 列表项），数量、顺序、链接目标全对。
3. 计数：toggle 总数 = 目录数，📄 列表项总数 = 文件页数。
4. 抽样：容器页下每个文件页标题一致，正文块 ≥ 2。

用法::

    python verify_tree.py --parent <页面 ID>
    python verify_tree.py --parent <页面 ID> --max-parents 60   # 只抽查前 60 个 toggle
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from push_to_notion import DEFAULT_WORKSPACE, Pacer, iter_dirs, iter_files, load_notionsync  # noqa: E402

DEFAULT_OUTLINE = HERE / "out" / "outline.json"
DEFAULT_STATE = HERE / "out" / "tree_state.json"


def toggle_text(block: dict) -> str:
    payload = block.get("toggle") or {}
    return "".join((i.get("text") or {}).get("content", "")
                   for i in payload.get("rich_text") or [])


def bullet_mention(block: dict) -> str | None:
    for item in (block.get("bulleted_list_item") or {}).get("rich_text") or []:
        if item.get("type") == "mention":
            return ((item.get("mention") or {}).get("page") or {}).get("id")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验折叠 toggle 树")
    parser.add_argument("--outline", default=str(DEFAULT_OUTLINE))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--parent", required=True)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--config", default=None)
    parser.add_argument("--throttle", type=float, default=0.34)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-parents", type=int, default=0, help=">0 时只校验前 N 个父块")
    parser.add_argument("--sample", type=int, default=12, help="正文抽样文件页数")
    args = parser.parse_args(argv)

    outline = json.loads(Path(args.outline).read_text(encoding="utf-8"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    tree = outline["tree"]
    dirs = list(iter_dirs(tree))
    files = list(iter_files(tree))
    pages = state.get("pages", {})
    problems: list[str] = []

    print(f"清单：目录 {len(dirs)}，文件页 {len(files)}；state 记录 {len(pages)} 页")
    if len(pages) != len(files):
        problems.append(f"state 页面数 {len(pages)} != 文件页数 {len(files)}")

    NotionClient, NotionError, load_registry, extract_id, _ = load_notionsync()
    registry = load_registry(Path(args.config) if args.config else None)
    workspace = registry.resolve(args.workspace)
    client = NotionClient(workspace.token, api_version=workspace.api_version,
                          key=workspace.key, label=workspace.label, max_retries=5)
    pacer = Pacer(args.throttle)
    original = client._request

    def paced(method, path, **kwargs):
        pacer.wait()
        return original(method, path, **kwargs)

    client._request = paced  # type: ignore[method-assign]

    page_id = extract_id(args.parent)

    # -- 1. 页面头部 ---------------------------------------------------------
    top = client.list_children(page_id)
    kinds: dict[str, int] = {}
    for block in top:
        kinds[block["type"]] = kinds.get(block["type"], 0) + 1
    print(f"页面顶层块：{kinds}（共 {len(top)}）")
    child_pages = [b for b in top if b.get("type") == "child_page"]
    if len(child_pages) != 1:
        problems.append(f"顶层子页面应为 1 个（容器页），实际 {len(child_pages)}")
    if kinds.get("toggle", 0) != len(tree):
        problems.append(f"顶层 toggle {kinds.get('toggle', 0)} != 顶层目录 {len(tree)}")

    # -- 2. 逐层核对整棵树 ---------------------------------------------------
    stats = {"toggles": 0, "bullets": 0, "parents": 0, "missing_toggle": 0,
             "missing_file": 0, "bad_link": 0}
    if args.max_parents:
        queue = [(b["id"], node) for b, node in zip([b for b in top if b["type"] == "toggle"], tree)]
        queue = queue[:args.max_parents]
    else:
        queue = [(b["id"], node) for b, node in zip([b for b in top if b["type"] == "toggle"], tree)]

    lock_problems: list[str] = []

    def check(item):
        block_id, node = item
        expected_dirs = [c for c in node["children"] if c["kind"] == "dir"]
        expected_files = [c for c in node["children"] if c["kind"] == "file"]
        children = client.list_children(block_id)
        toggles = [b for b in children if b["type"] == "toggle"]
        bullets = [b for b in children if b["type"] == "bulleted_list_item"]
        local: list[str] = []
        local_stats = {"toggles": len(toggles), "bullets": len(bullets)}
        if len(toggles) != len(expected_dirs):
            local.append(f"{node['rel']}：子 toggle {len(toggles)} != {len(expected_dirs)}")
        for block, sub in zip(toggles, expected_dirs):
            want = sub["name"] + (" ⋯" if sub.get("truncated") else "")
            if toggle_text(block) != want:
                local.append(f"{node['rel']}：toggle 名 {toggle_text(block)!r} != {want!r}")
        if len(bullets) != len(expected_files):
            local.append(f"{node['rel']}：文件项 {len(bullets)} != {len(expected_files)}")
        for block, sub in zip(bullets, expected_files):
            record = pages.get(sub["rel"])
            got = bullet_mention(block)
            if record is None:
                local.append(f"{node['rel']}：{sub['name']} 无页面记录")
            elif got != record["id"]:
                local.append(f"{node['rel']}：{sub['name']} 链接目标不对")
        return node, toggles, expected_dirs, local, local_stats

    frontier = list(queue)
    level = 0
    while frontier:
        level += 1
        print(f"  第 {level} 层：{len(frontier)} 个 toggle", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(check, frontier))
        next_frontier = []
        for node, toggles, expected_dirs, local, local_stats in results:
            stats["parents"] += 1
            stats["toggles"] += local_stats["toggles"]
            stats["bullets"] += local_stats["bullets"]
            lock_problems.extend(local)
            for block, sub in zip(toggles, expected_dirs):
                if sub.get("children"):
                    next_frontier.append((block["id"], sub))
        frontier = next_frontier

    print(f"遍历：父块 {stats['parents']}，子 toggle {stats['toggles']}"
          f"（+ 顶层 {len(tree)}），文件项 {stats['bullets']}")
    if not args.max_parents:
        total_toggles = stats["toggles"] + len(tree)
        if total_toggles != len(dirs):
            problems.append(f"toggle 总数 {total_toggles} != 目录数 {len(dirs)}")
        if stats["bullets"] != len(files):
            problems.append(f"文件项总数 {stats['bullets']} != 文件页数 {len(files)}")
    problems.extend(lock_problems[:50])

    # -- 3. 容器页与正文抽样 -------------------------------------------------
    container = state.get("container") or {}
    if container.get("id"):
        kids = client.list_children(container["id"])
        found = {b["id"]: (b.get("child_page") or {}).get("title", "")
                 for b in kids if b.get("type") == "child_page"}
        print(f"容器页子页面：{len(found)}（期望 {len(files)}）")
        for node in files:
            record = pages.get(node["rel"])
            if record and found.get(record["id"]) != node["name"]:
                problems.append(f"容器页标题不一致：{node['rel']}")
    rng = random.Random(20261005)
    for node in rng.sample(files, min(args.sample, len(files))):
        record = pages.get(node["rel"])
        if not record:
            continue
        if len(client.list_children(record["id"])) < 2:
            problems.append(f"正文块不足：{node['rel']}")

    print("=" * 70)
    if problems:
        print(f"发现问题 {len(problems)} 项：")
        for item in problems[:25]:
            print("  -", item)
    else:
        print("全部通过 ✅")
    report = HERE / "out" / "verify_tree_report.json"
    report.write_text(json.dumps({
        "dirs": len(dirs), "files": len(files), "top_blocks": kinds,
        "walked": stats, "problems": problems,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
