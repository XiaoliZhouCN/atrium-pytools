#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_sync.py — 独立校验清单、state 与 Notion 实际页面是否一致。

校验三层
--------
1. **清单 ↔ state**（离线）：manifest 里每个目录/文件都有页面记录，文件都写了正文。
2. **层级结构**（走 API，但很省）：Notion 里子页面会在父页面里生成一个 ``child_page`` 块，
   而该块的 id 就是子页面 id。于是只要逐个目录页拉一次 children，就能核对
   「每个页面都挂在正确的父页面下、标题与清单一致、且没有多余页面」——
   覆盖全部 1731 个页面只需要 ~509 次请求。
3. **正文抽样**（走 API）：随机抽 N 个文件页，确认正文块真的写进去了。

用法::

    python verify_sync.py                 # 结构全量 + 40 个正文抽样
    python verify_sync.py --sample 0      # 不做正文抽样
    python verify_sync.py --summary-only  # 只做第 1 层（不联网）
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from push_to_notion import (  # noqa: E402
    DEFAULT_MANIFEST, DEFAULT_PARENT, DEFAULT_STATE, DEFAULT_WORKSPACE,
    Pacer, iter_dirs, iter_files, load_notionsync,
)


def blocks_by_parent(client, page_id: str) -> list[dict]:
    return client.list_children(page_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验清单与 Notion 页面的一致性")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--parent", default=DEFAULT_PARENT)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--config", default=None)
    parser.add_argument("--sample", type=int, default=40, help="正文抽样数量，0=不抽样")
    parser.add_argument("--seed", type=int, default=20261004)
    parser.add_argument("--throttle", type=float, default=0.34)
    parser.add_argument("--summary-only", action="store_true", help="只比对清单与 state")
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    pages = state.get("pages", {})
    tree = manifest["tree"]

    dirs = list(iter_dirs(tree))
    files = list(iter_files(tree))
    print(f"清单：{len(files)} 个文件页 / {len(dirs)} 个目录页；state 记录 {len(pages)} 页")

    missing = [n["rel"] for n in [*dirs, *files] if n["rel"] not in pages]
    no_content = [n["rel"] for n in files
                  if n["rel"] in pages and not state.get("content", {}).get(n["rel"])]
    no_index = [n["rel"] for n in dirs
                if n["rel"] in pages and not state.get("indexed", {}).get(n["rel"])]
    print(f"state 缺页面：{len(missing)}；文件缺正文：{len(no_content)}；目录缺索引：{len(no_index)}")
    for rel in (missing + no_content + no_index)[:20]:
        print(f"  - {rel}")

    if args.summary_only:
        return 1 if (missing or no_content) else 0

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

    # -- 第 2 层：父页面里的 child_page 块 vs 清单期望 ----------------------
    parent_id = extract_id(args.parent)
    checks = [(".", parent_id, tree)]
    checks += [(node["rel"], pages[node["rel"]]["id"], node["children"])
               for node in dirs if node["rel"] in pages]

    print(f"开始结构校验：{len(checks)} 个父页面，期望子页面共 {len(files) + len(dirs)} 个 …",
          flush=True)
    problems: list[dict] = []
    for index, (rel, page_id, children) in enumerate(checks, start=1):
        try:
            blocks = blocks_by_parent(client, page_id)
        except Exception as exc:  # noqa: BLE001
            problems.append({"parent": rel, "kind": "fetch_error", "detail": str(exc)})
            continue
        found = {block["id"]: (block.get("child_page") or {}).get("title", "")
                 for block in blocks if block.get("type") == "child_page"}
        expected = {}
        for child in children:
            record = pages.get(child["rel"])
            if record:
                expected[record["id"]] = child["title"]
        for child_id, title in expected.items():
            if child_id not in found:
                problems.append({"parent": rel, "kind": "missing_child",
                                 "detail": f"{title} ({child_id}) 不在父页面里"})
            elif found[child_id] != title:
                problems.append({"parent": rel, "kind": "title_mismatch",
                                 "detail": f"{found[child_id]!r} != {title!r}"})
        for child_id, title in found.items():
            if child_id not in expected:
                problems.append({"parent": rel, "kind": "unexpected_child",
                                 "detail": f"{title} ({child_id})"})
        if index % 100 == 0:
            print(f"  … 已校验 {index}/{len(checks)} 个父页面", flush=True)

    # -- 第 3 层：正文抽样 --------------------------------------------------
    sample = []
    if args.sample:
        rng = random.Random(args.seed)
        sample = rng.sample(files, min(args.sample, len(files)))
    empty_body: list[dict] = []
    for node in sample:
        record = pages.get(node["rel"])
        if not record:
            continue
        try:
            blocks = client.list_children(record["id"])
        except Exception as exc:  # noqa: BLE001
            problems.append({"parent": node["rel"], "kind": "fetch_error", "detail": str(exc)})
            continue
        if len(blocks) < 2:
            empty_body.append({"rel": node["rel"], "blocks": len(blocks)})

    print("=" * 70)
    print(f"结构校验：{len(checks)} 个父页面，问题 {len(problems)}")
    for kind in ("missing_child", "title_mismatch", "unexpected_child", "fetch_error"):
        count = sum(1 for item in problems if item["kind"] == kind)
        if count:
            print(f"  {kind}: {count}")
    for item in problems[:20]:
        print(f"  - [{item['kind']}] {item['parent']} :: {item['detail']}")
    if args.sample:
        print(f"正文抽样：{len(sample)} 个文件页，块数异常 {len(empty_body)}")
        for item in empty_body[:10]:
            print(f"  - {item['rel']} :: {item['blocks']} 块")

    report = HERE / "out" / "verify_report.json"
    report.write_text(json.dumps({
        "manifest": args.manifest, "parents_checked": len(checks),
        "files": len(files), "dirs": len(dirs), "state_pages": len(pages),
        "state_missing": missing, "content_missing": no_content, "index_missing": no_index,
        "structure_problems": problems, "content_sample": len(sample),
        "content_sample_empty": empty_body,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report}")

    failed = bool(missing or no_content or problems or empty_body)
    print("结论：" + ("存在问题，见上" if failed else "全部通过 ✅"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
