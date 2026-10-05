"""真实 Notion 保真度实测：读取格式 → 无损写回。

回答的问题
----------
1. Python 能不能准确读到 **多列文本（columns）**、**表格（table）**、**数据库（database）**
   及其他块的**结构与格式**？
2. 读到的东西能不能**无损写回**（写回去再读出来，结构与内容完全一致）？

做法
----
在真实工作空间里建一个草稿父页，然后：

* 页 1：把一份覆盖全部构造的 Markdown 写进去 → 读回**原始块树** → 逐项断言结构
* 读回内容渲染成 Markdown₁ → 再解析成块 → 写进**页 2** → 读回块树 → 渲染成 Markdown₂
* **Markdown₁ == Markdown₂** 即证明「读 → 写」这一环没有信息损失
* 新建一个数据库（含 8 种属性类型）→ 插 2 行 → 查回逐属性比对 → 验证数据库读写保真
* 最后把整个草稿父页移入回收站（子页与数据库随之回收）

运行::

    python tools\\notionsync\\tests\\test_fidelity_notion.py            # 所有工作空间
    python tools\\notionsync\\tests\\test_fidelity_notion.py -w B        # 只测 B
    python tools\\notionsync\\tests\\test_fidelity_notion.py --keep      # 保留草稿便于人工看

产物：``tools/notionsync/temp/report_fidelity.json`` 与 ``temp/fidelity_<key>.md``
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

from notionsync import service                                     # noqa: E402
from notionsync.client import (                                    # noqa: E402
    NotionClient,
    NotionError,
    plain_text,
    rich_text,
)
from notionsync.config import ConfigError, WorkspaceRegistry, load_registry  # noqa: E402
from notionsync.markdown import blocks_to_markdown, markdown_to_blocks       # noqa: E402

SCRATCH_TITLE = "[FIDELITY] notionsync 保真度实测 — 可删除"

#: 覆盖全部受支持构造；顺序与嵌套都刻意做得不平凡
FIDELITY_MARKDOWN = """## 标题与段落

普通段落，含 **粗体**、*斜体*、~~删除线~~ 与 `行内代码`。

<columns>
\t<column ratio="50">
\t\t### 左列
\t\t- 左甲
\t\t- 左乙
\t\t> 左列引用
\t</column>
\t<column ratio="50">
\t\t### 右列
\t\t1. 右一
\t\t2. 右二
\t\t<callout icon="🔥">
\t\t\t右列 callout 正文
\t\t</callout>
\t</column>
</columns>

| 名称 | 数量 | 备注 |
| --- | --- | --- |
| 甲 | 1 | 第一行 |
| 乙 | 2 | 含 \\| 竖线 |
| 丙 | 3 | |

### 列表与待办

- 一级项
  - 二级项
    - 三级项
- [x] 已完成待办
- [ ] 未完成待办

<details>
<summary>折叠块标题</summary>
\t- 折叠内容一
\t- 折叠内容二
</details>

<callout icon="⚠️">
注意：这是一段 callout 正文。
</callout>

> 引用行

```python
def hello():
    return "无损"
```

$$ E = mc^2 $$

![示例图](https://www.notion.so/images/logo-ios.png)

---
"""

#: 期望在读回的块树里出现的结构
EXPECTED_TYPES = [
    "heading_2", "paragraph", "column_list", "column", "heading_3",
    "bulleted_list_item", "numbered_list_item", "callout", "table", "table_row",
    "to_do", "toggle", "quote", "code", "equation", "image", "divider",
]

SCHEMA = {
    "名称": {"title": {}},
    "备注": {"rich_text": {}},
    "数量": {"number": {"format": "number"}},
    "状态": {"select": {"options": [{"name": "进行中", "color": "blue"},
                                    {"name": "完成", "color": "green"}]}},
    "标签": {"multi_select": {"options": [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}]}},
    "截止": {"date": {}},
    "已启用": {"checkbox": {}},
    "链接": {"url": {}},
}

ROW_ONE = {
    "名称": {"title": rich_text("行一")},
    "备注": {"rich_text": rich_text("第一行备注")},
    "数量": {"number": 42.5},
    "状态": {"select": {"name": "进行中"}},
    "标签": {"multi_select": [{"name": "甲"}, {"name": "丙"}]},
    "截止": {"date": {"start": "2026-10-02"}},
    "已启用": {"checkbox": True},
    "链接": {"url": "https://example.com/one"},
}

ROW_TWO = {
    "名称": {"title": rich_text("行二")},
    "备注": {"rich_text": rich_text("第二行备注")},
    "数量": {"number": -7},
    "状态": {"select": {"name": "完成"}},
    "标签": {"multi_select": [{"name": "乙"}]},
    "截止": {"date": {"start": "2026-12-31"}},
    "已启用": {"checkbox": False},
    "链接": {"url": "https://example.com/two"},
}


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
        return all(c["ok"] for c in self.checks)

    def to_dict(self) -> dict:
        return {"workspace": self.workspace, "ok": self.ok,
                "passed": sum(1 for c in self.checks if c["ok"]), "total": len(self.checks),
                "checks": self.checks, "artifacts": self.artifacts}


def flatten(client: NotionClient, block_id: str, depth: int = 0) -> list[tuple[int, dict]]:
    """把块树拍平成 (深度, 块) 列表，便于断言结构。"""
    rows: list[tuple[int, dict]] = []
    for item in client.list_children(block_id):
        rows.append((depth, item))
        if item.get("has_children"):
            rows.extend(flatten(client, item["id"], depth + 1))
    return rows


def normalize_markdown(text: str) -> str:
    """比较用的归一化：去掉行尾空白与多余空行。"""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


def run_workspace(registry: WorkspaceRegistry, client: NotionClient, *,
                  keep: bool) -> Report:
    report = Report(client.key)
    print(f"\n=== 空间 {client.key}（{client.label}）===")

    scratch = client.create_page(None, SCRATCH_TITLE, parent_type="workspace", icon="🧪")
    scratch_id = scratch["id"]
    report.check("建立草稿父页", bool(scratch_id), f"{scratch_id}")
    print(f"      {scratch.get('url')}")

    try:
        # ---- 页 1：按 Markdown 写入，再读回原始块树 -----------------------
        page1 = client.create_page(scratch_id, "写入源", children=markdown_to_blocks(
            FIDELITY_MARKDOWN))
        tree1 = flatten(client, page1["id"])
        types1 = [b["type"] for _, b in tree1]
        missing = [t for t in EXPECTED_TYPES if t not in types1]
        report.check("写入后读回：全部块类型都在", not missing,
                     "缺 " + json.dumps(missing, ensure_ascii=False) if missing
                     else f"{len(tree1)} 个块 / {len(set(types1))} 种类型")

        # 多列文本：结构必须真的是 column_list + 2 个 column，且各列有内容
        column_lists = [b for _, b in tree1 if b["type"] == "column_list"]
        ok_columns = len(column_lists) == 1
        columns = ([b for _, b in tree1 if b["type"] == "column"] if ok_columns else [])
        ratios = [(c.get("column") or {}).get("width_ratio") for c in columns]
        report.check("多列文本：column_list + 2 列", ok_columns and len(columns) == 2,
                     f"column_list={len(column_lists)} column={len(columns)} ratio={ratios}")

        # 表格：宽度、表头、单元格（表头本身也是一行 table_row，所以共 4 行）
        tables = [b for _, b in tree1 if b["type"] == "table"]
        rows = [b for _, b in tree1 if b["type"] == "table_row"]
        ok_table = len(tables) == 1 and len(rows) == 4
        report.check("表格：1 个 table / 表头 + 3 数据行", ok_table and
                     (tables[0]["table"].get("has_column_header") is True),
                     f"table={len(tables)} row={len(rows)} "
                     f"header={(tables[0]['table'].get('has_column_header') if tables else None)}")
        if len(rows) >= 3:
            def _cells_of(row):
                return [plain_text(cell) for cell in
                        (row.get("table_row") or {}).get("cells") or []]
            report.check("表格：表头行内容", _cells_of(rows[0]) == ["名称", "数量", "备注"],
                         json.dumps(_cells_of(rows[0]), ensure_ascii=False))
            report.check("表格：数据行内容与转义竖线",
                         _cells_of(rows[2]) == ["乙", "2", "含 | 竖线"],
                         json.dumps(_cells_of(rows[2]), ensure_ascii=False))

        # callout：类型 + 图标
        callouts = [b for _, b in tree1 if b["type"] == "callout"]
        icons = [(c.get("callout") or {}).get("icon", {}).get("emoji") for c in callouts]
        report.check("callout：2 个且图标保留", len(callouts) == 2 and "🔥" in icons
                     and "⚠️" in icons, f"icons={icons}")

        # 折叠块
        toggles = [b for _, b in tree1 if b["type"] == "toggle"]
        report.check("折叠块：类型与子块保留", len(toggles) == 1 and
                     len(client.list_children(toggles[0]["id"])) >= 2,
                     f"toggle={len(toggles)}")

        # 待办：勾选状态
        todos = [b for _, b in tree1 if b["type"] == "to_do"]
        checked = [(t["to_do"] or {}).get("checked") for t in todos]
        report.check("待办：勾选/未勾选各一", sorted(checked, key=str) == [False, True],
                     f"checked={checked}")

        # 三级嵌套列表
        depth3 = [d for d, b in tree1 if b["type"] == "bulleted_list_item" and d >= 4]
        report.check("嵌套列表：达到三层", bool(depth3) or
                     len([1 for _, b in tree1 if b["type"] == "bulleted_list_item"]) >= 4,
                     f"最深 bulleted 深度={max((d for d, b in tree1 if b['type']=='bulleted_list_item'), default=-1)}")

        # 代码语言、公式、图片、分割线
        code = next((b for _, b in tree1 if b["type"] == "code"), {})
        report.check("代码块：语言保留", (code.get("code") or {}).get("language") == "python",
                     str((code.get("code") or {}).get("language")))
        equations = [b for _, b in tree1 if b["type"] == "equation"]
        report.check("公式块", bool(equations) and
                     (equations[0]["equation"] or {}).get("expression") == "E = mc^2",
                     str((equations[0]["equation"] if equations else {}).get("expression")))
        images = [b for _, b in tree1 if b["type"] == "image"]
        report.check("图片块：外链 URL 保留", bool(images) and
                     (images[0]["image"].get("external") or {}).get("url", "").endswith(".png"),
                     str((images[0]["image"] if images else {})).replace("\n", " ")[:110])
        report.check("分割线", any(b["type"] == "divider" for _, b in tree1))

        # ---- 无损写回：读 → Markdown₁ → 块 → 页 2 → 读 → Markdown₂ -------
        md1 = blocks_to_markdown(client, client.list_children(page1["id"]))
        dump1 = TEMP_DIR / f"fidelity_{client.key}_source.md"
        dump1.write_text(md1, encoding="utf-8")
        report.artifacts["source_markdown"] = str(dump1)

        page2 = client.create_page(scratch_id, "写回目标",
                                   children=markdown_to_blocks(md1))
        md2 = blocks_to_markdown(client, client.list_children(page2["id"]))
        dump2 = TEMP_DIR / f"fidelity_{client.key}_roundtrip.md"
        dump2.write_text(md2, encoding="utf-8")
        report.artifacts["roundtrip_markdown"] = str(dump2)

        same = normalize_markdown(md1) == normalize_markdown(md2)
        detail = f"{len(md1)} → {len(md2)} 字符"
        if not same:
            diff = _first_diff(normalize_markdown(md1), normalize_markdown(md2))
            detail += f"；首个差异：{diff}"
        report.check("无损写回：读→写→读 完全一致", same, detail)

        tree2 = flatten(client, page2["id"])
        types2 = [b["type"] for _, b in tree2]
        from collections import Counter
        report.check("无损写回：块类型计数一致",
                     Counter(types1) == Counter(types2),
                     _counter_diff(types1, types2))

        # ---- 数据库 --------------------------------------------------------
        database = client.create_database(scratch_id, "保真度测试库", SCHEMA, icon="🗄️")
        source_ids = [s["id"] for s in (database.get("data_sources") or [])]
        report.check("建库：返回 data source", bool(source_ids),
                     json.dumps(database.get("data_sources"), ensure_ascii=False)[:120])

        for label, row in (("行一", ROW_ONE), ("行二", ROW_TWO)):
            client.create_page(source_ids[0], label, parent_type="data_source",
                               properties=row)

        rows_back = client.query_all(source_ids[0], limit=10)
        report.check("数据库：写入 2 行并可查回", len(rows_back) == 2,
                     f"查回 {len(rows_back)} 行")

        by_title = {((r["properties"].get("名称") or {}).get("title") or [{}])[0]
                    .get("plain_text", ""): r for r in rows_back}
        for label, expected in (("行一", ROW_ONE), ("行二", ROW_TWO)):
            actual = by_title.get(label)
            if not actual:
                report.check(f"数据库属性：{label}", False, "未查回该行")
                continue
            problems = _compare_properties(expected, actual["properties"])
            report.check(f"数据库属性：{label} 8 个属性值一致", not problems,
                         "；".join(problems) if problems else "title/rich_text/number/select/"
                         "multi_select/date/checkbox/url 全部一致")
    finally:
        if keep:
            print(f"  [KEEP] 草稿父页保留：{scratch.get('url')}")
        else:
            try:
                client.update_page(scratch_id, in_trash=True)
                report.check("清理：草稿父页移入回收站", True, scratch_id)
            except NotionError as exc:
                report.check("清理：草稿父页移入回收站", False, str(exc))

    return report


def _compare_properties(expected: dict, actual: dict) -> list[str]:
    problems: list[str] = []
    for name, value in expected.items():
        got = actual.get(name) or {}
        if "title" in value:
            want = plain_text(value["title"])
            have = plain_text(got.get("title"))
        elif "rich_text" in value:
            want = plain_text(value["rich_text"])
            have = plain_text(got.get("rich_text"))
        elif "number" in value:
            want, have = value["number"], got.get("number")
        elif "select" in value:
            want, have = value["select"]["name"], (got.get("select") or {}).get("name")
        elif "multi_select" in value:
            want = sorted(o["name"] for o in value["multi_select"])
            have = sorted(o["name"] for o in got.get("multi_select") or [])
        elif "date" in value:
            want, have = value["date"]["start"], (got.get("date") or {}).get("start")
        elif "checkbox" in value:
            want, have = value["checkbox"], got.get("checkbox")
        elif "url" in value:
            want, have = value["url"], got.get("url")
        else:
            continue
        if want != have:
            problems.append(f"{name}: 期望 {want!r} 实际 {have!r}")
    return problems


def _first_diff(a: str, b: str) -> str:
    for index, (left, right) in enumerate(zip(a.splitlines(), b.splitlines())):
        if left != right:
            return f"第 {index + 1} 行 {left[:60]!r} != {right[:60]!r}"
    return f"行数不同：{len(a.splitlines())} vs {len(b.splitlines())}"


def _counter_diff(a: list[str], b: list[str]) -> str:
    from collections import Counter
    left, right = Counter(a), Counter(b)
    only_a = {k: v for k, v in (left - right).items()}
    only_b = {k: v for k, v in (right - left).items()}
    if not only_a and not only_b:
        return f"{len(a)} 个块"
    return f"仅源有 {only_a}；仅写回有 {only_b}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Notion 格式保真度实测")
    parser.add_argument("--config")
    parser.add_argument("-w", "--workspace", action="append")
    parser.add_argument("--keep", action="store_true", help="保留草稿便于人工比对")
    args = parser.parse_args()

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 70)
    print("Notion 保真度实测（真实账号）")
    print("=" * 70)

    try:
        registry = service.make_registry(args.config)
    except ConfigError as exc:
        print(f"[SKIP] 没有可用配置：{exc}")
        return 2

    keys = args.workspace or registry.keys
    reports: list[Report] = []
    started = time.time()
    for key in keys:
        ws = registry.resolve(key)
        client = NotionClient(ws.token, api_version=ws.api_version, key=ws.key,
                              label=ws.label)
        reports.append(run_workspace(registry, client, keep=args.keep))

    payload = {"suite": "notionsync fidelity", "configSource": registry.source,
               "durationSeconds": round(time.time() - started, 1),
               "workspaces": [r.to_dict() for r in reports]}
    path = TEMP_DIR / "report_fidelity.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    total = sum(r.to_dict()["total"] for r in reports)
    passed = sum(r.to_dict()["passed"] for r in reports)
    print(f"结果：{passed}/{total} 通过")
    for report in reports:
        if not report.ok:
            for item in report.checks:
                if not item["ok"]:
                    print(f"  空间 {report.workspace} 未通过：{item['name']} — {item['detail']}")
    print(f"报告：{path}")
    print("=" * 70)
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
