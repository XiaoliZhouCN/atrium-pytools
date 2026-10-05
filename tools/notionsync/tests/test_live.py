"""notionsync 实链路自检 —— 需要真实 Token。

它会真的在 Notion 里建一个草稿页（标题以 ``[SCRATCH]`` 开头），做完整的
写入 / 读回 / 追加 / 评论 / 改标题 / 回收站回收，然后默认把该页面移入回收站。

运行::

    python tools\\notionsync\\tests\\test_live.py                  # 全空间
    python tools\\notionsync\\tests\\test_live.py -w B             # 只测 B
    python tools\\notionsync\\tests\\test_live.py --read-only      # 只读，不建页
    python tools\\notionsync\\tests\\test_live.py --keep           # 保留草稿页

产物：``tools/notionsync/temp/report_live.json`` 与 ``temp/live_<key>.md``。
退出码：0 全部通过 / 1 有失败 / 2 缺配置。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parents[1]
TEMP_DIR = TOOL_DIR / "temp"
sys.path.insert(0, str(TOOL_DIR))

from notionsync import service                                          # noqa: E402
from notionsync.client import NotionClient, NotionError, extract_id     # noqa: E402
from notionsync.config import ConfigError, WorkspaceRegistry, load_registry  # noqa: E402

SCRATCH_TITLE = "[SCRATCH] notionsync 链路自检 — 可删除"

BODY = """## 格式往返测试

正文段落，用来确认段落块可以原样读回。

- 项目一
- 项目二
- [x] 已完成
- [ ] 未完成

1. 有序一
2. 有序二

> 引用行

```python
print("hi")
```

---
"""

APPENDED = """### 追加小节

追加内容，用来确认 append 语义。
"""

# 读回时必须出现的内容（Notion 会做少量归一化，所以只断言稳定的锚点）
EXPECTED = [
    "格式往返测试",
    "正文段落",
    "项目一",
    "[x] 已完成",
    "[ ] 未完成",
    "1. 有序一",
    "引用行",
    "```",
    "print(\"hi\")",
]


class Report:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.checks: list[dict] = []
        self.artifacts: dict[str, str] = {}

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)

    def to_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "ok": self.ok,
            "passed": sum(1 for c in self.checks if c["ok"]),
            "total": len(self.checks),
            "checks": self.checks,
            "artifacts": self.artifacts,
        }


def run_workspace(registry: WorkspaceRegistry, client: NotionClient, *,
                  read_only: bool, keep: bool) -> Report:
    report = Report(client.key)
    print(f"\n=== 空间 {client.key}（{client.label}）===")

    # 1. 凭据与空间身份
    try:
        info = client.self_info()
        name = client.workspace_name
        report.check("凭据有效 / users.me", True,
                     f"workspace={name!r} object={info.get('object')}")
    except NotionError as exc:
        report.check("凭据有效 / users.me", False, str(exc))
        return report

    # 2. 读：搜索页面
    try:
        pages = client.search("", object_type="page", page_size=5).get("results") or []
        report.check("读取：搜索页面", True, f"{len(pages)} 条")
        if pages:
            report.check("读取：首条有标题或 ID", bool(pages[0].get("id")),
                         str(pages[0].get("id")))
    except NotionError as exc:
        report.check("读取：搜索页面", False, str(exc))

    # 3. 读：搜索数据库（data source）
    try:
        sources = client.search("", object_type="data_source", page_size=5).get("results") or []
        report.check("读取：搜索数据库", True, f"{len(sources)} 个 data source")
    except NotionError as exc:
        report.check("读取：搜索数据库", False, str(exc))
        sources = []

    # 4. 读：数据库结构 + 查询
    if sources:
        source_id = sources[0].get("id")
        try:
            described = service.describe_database(
                registry, client.key, source_id)
            report.check("读取：数据库结构", bool(described.get("data_sources")),
                         json.dumps(described.get("data_sources"), ensure_ascii=False)[:120])
            rows = service.query_database(registry, client.key, source_id, limit=3)
            report.check("读取：数据库查询", "error" not in rows,
                         f"{rows.get('count')} 行")

            # 只读地验证「数据库行标题属性名」能解析出来——中文库常叫「名称」，
            # 解析失败会导致 notion_create_page(parent_type="data_source") 写不进去。
            from notionsync.client import find_title_property
            source = client.get_data_source(source_id)
            title_name = find_title_property(source.get("properties"))
            report.check("读取：data source 标题属性可解析", bool(title_name),
                         f"title property = {title_name!r}（写库行时用它）")
        except Exception as exc:  # noqa: BLE001
            report.check("读取：数据库结构/查询", False, f"{type(exc).__name__}: {exc}")

    if read_only:
        print("  [SKIP] --read-only：跳过全部写入测试")
        return report

    # 5. 写：建草稿页
    scratch_id = None
    try:
        page = client.create_page(None, SCRATCH_TITLE, parent_type="workspace",
                                  icon="🧪")
        scratch_id = page.get("id")
        report.check("写入：创建工作空间级草稿页", bool(scratch_id),
                     f"{scratch_id} url={page.get('url')}")
    except NotionError as exc:
        report.check("写入：创建工作空间级草稿页", False, str(exc))
        return report

    try:
        # 6. 写：正文
        from notionsync.markdown import markdown_to_blocks
        created = client.append_children(scratch_id, markdown_to_blocks(BODY))
        report.check("写入：写入正文块", len(created) > 0, f"{len(created)} 块")

        # 7. 读回 + 结构断言
        back = service.read_page(registry, client.key, scratch_id, include_title=False)
        text = back.get("markdown", "")
        dump = TEMP_DIR / f"live_{client.key}.md"
        dump.write_text(text, encoding="utf-8")
        report.artifacts["roundtrip_markdown"] = str(dump)
        missing = [item for item in EXPECTED if item not in text]
        report.check("格式往返：关键内容全部读回", not missing,
                     "缺失：" + json.dumps(missing, ensure_ascii=False) if missing
                     else f"{len(text)} 字符")

        # 8. 写：追加
        appended = client.append_children(scratch_id, markdown_to_blocks(APPENDED))
        after = service.read_page(registry, client.key, scratch_id, include_title=False)
        report.check("写入：追加内容", len(appended) > 0 and "追加内容" in after.get("markdown", ""),
                     f"{len(appended)} 块")

        # 9. 写：改标题
        client.set_page_title(scratch_id, SCRATCH_TITLE + "（已改名）")
        report.check("写入：修改标题", True)

        # 10. 评论
        comment = client.create_comment(scratch_id, "notionsync 自检评论，可删除")
        comments = client.list_comments(scratch_id)
        report.check("写入：评论", len(comments) >= 1,
                     f"comment_id={comment.get('id')} 共 {len(comments)} 条")

        # 11. 局部读块
        children = client.list_children(scratch_id)
        if children:
            one = service.read_block(registry, client.key, children[0]["id"])
            report.check("读取：单块渲染", "markdown" in one, one.get("markdown", "")[:60].replace("\n", " "))
    finally:
        # 12. 回收
        if keep:
            print(f"  [KEEP] 草稿页保留：{scratch_id}")
        else:
            try:
                client.update_page(scratch_id, in_trash=True)
                report.check("清理：草稿页移入回收站", True, str(scratch_id))
            except NotionError as exc:
                report.check("清理：草稿页移入回收站", False, str(exc))

    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="notionsync 实链路自检")
    parser.add_argument("--config")
    parser.add_argument("-w", "--workspace", action="append",
                        help="只测指定空间；可重复")
    parser.add_argument("--read-only", action="store_true", help="不写任何内容")
    parser.add_argument("--keep", action="store_true", help="保留草稿页不回收")
    args = parser.parse_args()

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("notionsync 实链路自检")
    print("=" * 70)

    try:
        registry = service.make_registry(args.config)
    except ConfigError as exc:
        print(f"[SKIP] 没有可用配置：{exc}")
        print("       先创建 ~/.notionsync/workspaces.json（见 README）。")
        return 2

    print(f"配置文件：{registry.source}")
    print(f"工作空间：{', '.join(registry.keys)}")

    keys = args.workspace or registry.keys
    reports: list[Report] = []
    started = time.time()

    for key in keys:
        ws = registry.resolve(key)
        client = NotionClient(ws.token, api_version=ws.api_version, key=ws.key,
                              label=ws.label)
        reports.append(run_workspace(registry, client, read_only=args.read_only,
                                     keep=args.keep))

    payload = {
        "suite": "notionsync live",
        "configSource": registry.source,
        "readOnly": args.read_only,
        "durationSeconds": round(time.time() - started, 1),
        "workspaces": [report.to_dict() for report in reports],
    }
    report_path = TEMP_DIR / "report_live.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print("\n" + "=" * 70)
    total = sum(r.to_dict()["total"] for r in reports)
    passed = sum(r.to_dict()["passed"] for r in reports)
    print(f"结果：{passed}/{total} 通过")
    for report in reports:
        if not report.ok:
            print(f"  空间 {report.workspace} 未通过：")
            for item in report.checks:
                if not item["ok"]:
                    print(f"    - {item['name']}: {item['detail']}")
    print(f"报告：{report_path}")
    print("=" * 70)
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
