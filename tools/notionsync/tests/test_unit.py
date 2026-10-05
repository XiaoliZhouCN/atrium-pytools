"""notionsync 单元测试 —— 完全离线，不触网、不需要 Token。

运行::

    python "D:\\Repositories\\Manager\\AtriumPyTools\\tools\\notionsync\\tests\\test_unit.py"

产物写入 ``tools/notionsync/temp/``（已在 .gitignore 忽略）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest
import uuid

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parents[1]              # tools/notionsync
TEMP_DIR = TOOL_DIR / "temp"
sys.path.insert(0, str(TOOL_DIR))

from notionsync import markdown as md                                # noqa: E402
from notionsync.client import (                                      # noqa: E402
    NotionClient,
    extract_id,
    find_title_property,
    page_title,
    plain_text,
)
from notionsync.config import (                                      # noqa: E402
    ConfigError,
    Workspace,
    WorkspaceRegistry,
    load_registry,
)
from notionsync.mcp import TOOLS, handle_message                     # noqa: E402

TEMP_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class FakeClient:
    """只实现 list_children 的假客户端，用于离线验证 Markdown 渲染。"""

    def __init__(self, tree: dict[str, list[dict]]) -> None:
        self.tree = tree
        self.calls: list[str] = []

    def list_children(self, block_id: str) -> list[dict]:
        self.calls.append(block_id)
        return self.tree.get(block_id, [])


def rich(text: str) -> list[dict]:
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def block(block_id: str, btype: str, payload: dict, *, children: bool = False) -> dict:
    return {"id": block_id, "type": btype, btype: payload, "has_children": children}


# ---------------------------------------------------------------------------
# 1. ID 解析
# ---------------------------------------------------------------------------
class TestExtractId(unittest.TestCase):
    def test_plain_uuid(self):
        self.assertEqual(extract_id("3ede47bb-dac5-80ac-997c-c740f9b6e6ec"),
                         "3ede47bb-dac5-80ac-997c-c740f9b6e6ec")

    def test_notion_url(self):
        url = "https://app.notion.com/p/3ede47bbdac580ac997cc740f9b6e6ec?pvs=204"
        self.assertEqual(extract_id(url), "3ede47bb-dac5-80ac-997c-c740f9b6e6ec")

    def test_slugged_url(self):
        url = "https://www.notion.so/My-Page-3ede47bbdac580ac997cc740f9b6e6ec"
        self.assertEqual(extract_id(url), "3ede47bb-dac5-80ac-997c-c740f9b6e6ec")

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            extract_id("not-an-id")


# ---------------------------------------------------------------------------
# 2. 配置解析
# ---------------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def setUp(self):
        # 不用 tempfile.TemporaryDirectory：Windows 上它对临时目录做 chmod/rmtree
        # 容易被 ACL 拦住，直接在工作区内的 temp/ 建文件更稳，也符合产物落 temp 的约定。
        self.path = TEMP_DIR / f"_cfg_{uuid.uuid4().hex}.json"

    def tearDown(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def write(self, payload) -> None:
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_two_workspaces(self):
        self.write({"workspaces": [
            {"key": "A", "label": "个人空间", "token": "ntn_aaa"},
            {"key": "B", "label": "Shirley's Dashboard", "token": "ntn_bbb"},
        ]})
        registry = load_registry(self.path)
        self.assertEqual(registry.keys, ["A", "B"])
        self.assertEqual(registry.resolve("b").label, "Shirley's Dashboard")
        self.assertEqual(registry.resolve("A").api_version, "2025-09-03")

    def test_single_workspace_needs_no_key(self):
        self.write({"workspaces": [{"key": "A", "token": "ntn_aaa"}]})
        self.assertEqual(load_registry(self.path).resolve().key, "A")

    def test_multiple_needs_key(self):
        self.write({"workspaces": [{"key": "A", "token": "x"}, {"key": "B", "token": "y"}]})
        with self.assertRaises(ConfigError):
            load_registry(self.path).resolve()

    def test_unknown_key(self):
        self.write({"workspaces": [{"key": "A", "token": "x"}]})
        with self.assertRaises(ConfigError):
            load_registry(self.path).resolve("Z")

    def test_env_token_override(self):
        self.write({"workspaces": [{"key": "A", "token": "from-file"}]})
        os.environ["NOTIONSYNC_TOKEN_A"] = "from-env"
        try:
            self.assertEqual(load_registry(self.path).resolve("A").token, "from-env")
        finally:
            del os.environ["NOTIONSYNC_TOKEN_A"]

    def test_env_indirection(self):
        self.write({"workspaces": [{"key": "A", "token": "env:MY_NOTION_TOKEN"}]})
        os.environ["MY_NOTION_TOKEN"] = "indirect"
        try:
            self.assertEqual(load_registry(self.path).resolve("A").token, "indirect")
        finally:
            del os.environ["MY_NOTION_TOKEN"]

    def test_missing_key_is_rejected(self):
        self.write({"workspaces": [{"token": "x"}]})
        with self.assertRaises(ConfigError):
            load_registry(self.path)

    def test_describe_never_leaks_token(self):
        ws = Workspace(key="A", label="x", token="ntn_supersecretvalue")
        self.assertNotIn("supersecretvalue", json.dumps(ws.describe(), ensure_ascii=False))


# ---------------------------------------------------------------------------
# 3. Markdown → blocks
# ---------------------------------------------------------------------------
class TestMarkdownToBlocks(unittest.TestCase):
    def types(self, text):
        return [b["type"] for b in md.markdown_to_blocks(text)]

    def test_headings_and_paragraph(self):
        blocks = md.markdown_to_blocks("# 一级\n\n正文\n\n## 二级\n### 三级\n")
        self.assertEqual([b["type"] for b in blocks],
                         ["heading_1", "paragraph", "heading_2", "heading_3"])
        self.assertEqual(blocks[0]["heading_1"]["rich_text"][0]["text"]["content"], "一级")

    def test_list_kinds(self):
        blocks = md.markdown_to_blocks("- 普通\n- [x] 已完成\n- [ ] 未完成\n1. 有序\n")
        self.assertEqual([b["type"] for b in blocks],
                         ["bulleted_list_item", "to_do", "to_do", "numbered_list_item"])
        self.assertTrue(blocks[1]["to_do"]["checked"])
        self.assertFalse(blocks[2]["to_do"]["checked"])

    def test_nested_list(self):
        blocks = md.markdown_to_blocks("- 父项\n  - 子项\n- 同级\n")
        self.assertEqual(len(blocks), 2)
        children = blocks[0]["bulleted_list_item"]["children"]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"], "子项")

    def test_code_fence(self):
        blocks = md.markdown_to_blocks("```python\nprint(1)\nprint(2)\n```\n")
        self.assertEqual(blocks[0]["type"], "code")
        self.assertEqual(blocks[0]["code"]["language"], "python")
        self.assertIn("print(2)", blocks[0]["code"]["rich_text"][0]["text"]["content"])

    def test_quote_and_divider(self):
        self.assertEqual(self.types("> 引用\n\n---\n"), ["quote", "divider"])

    def test_blank_input(self):
        self.assertEqual(md.markdown_to_blocks("\n\n  \n"), [])


# ---------------------------------------------------------------------------
# 4. blocks → Markdown
# ---------------------------------------------------------------------------
class TestBlocksToMarkdown(unittest.TestCase):
    def render(self, tree, root="root"):
        return md.blocks_to_markdown(FakeClient(tree), tree[root])

    def test_basic_types(self):
        tree = {"root": [
            block("h", "heading_2", {"rich_text": rich("标题")}),
            block("p", "paragraph", {"rich_text": rich("段落")}),
            block("d", "divider", {}),
            block("t", "to_do", {"rich_text": rich("待办"), "checked": True}),
            block("q", "quote", {"rich_text": rich("引用")}),
        ]}
        text = self.render(tree)
        self.assertIn("## 标题", text)
        self.assertIn("段落", text)
        self.assertIn("---", text)
        self.assertIn("- [x] 待办", text)
        self.assertIn("> 引用", text)

    def test_code_and_callout(self):
        tree = {"root": [
            block("c", "code", {"rich_text": rich("x = 1"), "language": "python"}),
            block("k", "callout", {"rich_text": rich("注意"), "icon": {"emoji": "⚠️"}}),
        ]}
        text = self.render(tree)
        self.assertIn("```python", text)
        self.assertIn("x = 1", text)
        # callout 必须保留"块类型 + 图标"，否则写回时退化成一个普通引用块
        self.assertIn('<callout icon="⚠️">', text)
        self.assertIn("注意", text)
        self.assertIn("</callout>", text)

    def test_nested_children(self):
        tree = {
            "root": [block("l", "bulleted_list_item", {"rich_text": rich("父")}, children=True)],
            "l": [block("l2", "bulleted_list_item", {"rich_text": rich("子")})],
        }
        text = self.render(tree)
        self.assertIn("- 父", text)
        self.assertIn("  - 子", text)

    def test_numbered_list_renumbers(self):
        tree = {"root": [
            block("n1", "numbered_list_item", {"rich_text": rich("甲")}),
            block("n2", "numbered_list_item", {"rich_text": rich("乙")}),
        ]}
        text = self.render(tree)
        self.assertIn("1. 甲", text)
        self.assertIn("2. 乙", text)

    def test_table(self):
        tree = {
            "root": [block("tb", "table",
                           {"table_width": 2, "has_column_header": True}, children=True)],
            "tb": [
                {"id": "r1", "type": "table_row",
                 "table_row": {"cells": [rich("A"), rich("B")]}},
                {"id": "r2", "type": "table_row",
                 "table_row": {"cells": [rich("1"), rich("2")]}},
            ],
        }
        text = self.render(tree)
        self.assertIn("| A | B |", text)
        self.assertIn("| --- | --- |", text)
        self.assertIn("| 1 | 2 |", text)

    def test_inline_annotations(self):
        tree = {"root": [block("p", "paragraph", {"rich_text": [
            {"type": "text", "plain_text": "粗", "text": {"content": "粗"},
             "annotations": {"bold": True}},
            {"type": "text", "plain_text": "码", "text": {"content": "码"},
             "annotations": {"code": True}},
        ]})]}
        text = self.render(tree)
        self.assertIn("**粗**", text)
        self.assertIn("`码`", text)

    def test_unsupported_type_is_flagged_not_dropped(self):
        tree = {"root": [block("u", "unsupported", {})]}
        self.assertIn("不支持的块类型", self.render(tree))


# ---------------------------------------------------------------------------
# 5. MCP 协议层（离线）
# ---------------------------------------------------------------------------
class TestMcpProtocol(unittest.TestCase):
    def setUp(self):
        self.registry = WorkspaceRegistry(
            [Workspace(key="A", label="a", token="ntn_dummy_a"),
             Workspace(key="B", label="b", token="ntn_dummy_b")],
            source="(test)",
        )

    def test_initialize(self):
        response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18"}},
                                  self.registry)
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["serverInfo"]["name"], "notionsync")

    def test_unknown_protocol_version_falls_back(self):
        response = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "1999-01-01"}},
                                  self.registry)
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_initialized_notification_has_no_response(self):
        self.assertIsNone(handle_message({"jsonrpc": "2.0",
                                          "method": "notifications/initialized"}, self.registry))

    def test_tools_list_shape(self):
        response = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                  self.registry)
        tools = response["result"]["tools"]
        self.assertGreaterEqual(len(tools), 10)
        names = {t["name"] for t in tools}
        for expected in ("workspaces_list", "notion_search", "notion_read_page",
                         "notion_create_page", "notion_append", "notion_replace_content",
                         "notion_query_database", "notion_create_comment"):
            self.assertIn(expected, names)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("description", tool)
        self.assertEqual(names, {t["name"] for t in TOOLS})

    def test_unknown_tool_reports_error(self):
        response = handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": "nope", "arguments": {}}},
                                  self.registry)
        self.assertTrue(response["result"]["isError"])

    def test_missing_required_param(self):
        response = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                   "params": {"name": "notion_read_page", "arguments": {}}},
                                  self.registry)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("page_id", response["result"]["content"][0]["text"])

    def test_unknown_method(self):
        response = handle_message({"jsonrpc": "2.0", "id": 5, "method": "nope/nope"},
                                  self.registry)
        self.assertEqual(response["error"]["code"], -32601)

    def test_workspaces_list_never_leaks_tokens(self):
        """workspaces_list 会真的访问网络，这里只验证凭据脱敏契约本身。"""
        from notionsync import service

        text = service.redact("token=ntn_dummy_a and ntn_dummy_b", self.registry)
        self.assertNotIn("ntn_dummy_a", text)
        self.assertNotIn("ntn_dummy_b", text)
        for ws in self.registry.all():
            self.assertNotIn(ws.token, json.dumps(ws.describe(), ensure_ascii=False))


# ---------------------------------------------------------------------------
# 6. 杂项
# ---------------------------------------------------------------------------
class TestHelpers(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(plain_text([{"plain_text": "a"}, {"plain_text": "b"}]), "ab")

    def test_page_title(self):
        page = {"properties": {"名称": {"type": "title", "title": [{"plain_text": "标题"}]}}}
        self.assertEqual(page_title(page), "标题")

    def test_page_title_missing(self):
        self.assertEqual(page_title({"properties": {}}), "")


# ---------------------------------------------------------------------------
# 7. 数据库行的标题属性名解析（离线，防止「名称」类中文库被写挂）
# ---------------------------------------------------------------------------
class StubClient(NotionClient):
    """拦截 _request，既能离线验证请求体，又不需要网络。"""

    def __init__(self, data_source: dict | None = None) -> None:
        super().__init__("ntn_stub_token")
        self.data_source = data_source or {}
        self.sent: dict = {}

    def get_data_source(self, data_source_id: str) -> dict:  # noqa: D102
        self.data_source_requested = data_source_id
        return self.data_source

    def _request(self, method, path, *, body=None, params=None):  # noqa: D102
        self.sent = {"method": method, "path": path, "body": body, "params": params}
        return {"id": "page-stub", "url": "https://example.invalid/page-stub"}


class TestDatabaseRowTitleProperty(unittest.TestCase):
    def test_find_title_property(self):
        self.assertEqual(
            find_title_property({"名称": {"type": "title"}, "Topic": {"type": "select"}}),
            "名称")
        self.assertEqual(find_title_property({"Name": {"type": "title"}}), "Name")
        self.assertIsNone(find_title_property({"A": {"type": "select"}}))
        self.assertIsNone(find_title_property(None))

    def test_data_source_row_uses_real_title_property(self):
        """中文库标题属性叫「名称」时，必须用「名称」而不是硬编码的 title。"""
        client = StubClient({"properties": {"名称": {"type": "title"},
                                            "Topic": {"type": "select"}}})
        client.create_page("3ede47bb-dac5-8036-a5d7-000ba0673afb", "新行",
                           parent_type="data_source")
        body = client.sent["body"]
        self.assertEqual(client.sent["path"], "/v1/pages")
        self.assertEqual(body["parent"],
                         {"type": "data_source_id",
                          "data_source_id": "3ede47bb-dac5-8036-a5d7-000ba0673afb"})
        self.assertIn("名称", body["properties"])
        self.assertNotIn("title", body["properties"])
        self.assertEqual(
            body["properties"]["名称"]["title"][0]["text"]["content"], "新行")

    def test_explicit_title_property_wins(self):
        client = StubClient({"properties": {"名称": {"type": "title"}}})
        client.create_page("3ede47bb-dac5-8036-a5d7-000ba0673afb", "新行",
                           parent_type="data_source", title_property_name="标题")
        self.assertIn("标题", client.sent["body"]["properties"])

    def test_page_parent_still_uses_title(self):
        client = StubClient()
        client.create_page("39fe47bb-dac5-802c-909b-fddc000ca070", "子页")
        body = client.sent["body"]
        self.assertEqual(body["parent"]["type"], "page_id")
        self.assertIn("title", body["properties"])

    def test_workspace_parent_needs_no_id(self):
        client = StubClient()
        client.create_page(None, "工作空间级页面", parent_type="workspace")
        body = client.sent["body"]
        self.assertEqual(body["parent"], {"type": "workspace", "workspace": True})
        self.assertIn("title", body["properties"])

    def test_missing_parent_id_is_rejected(self):
        client = StubClient()
        with self.assertRaises(ValueError):
            client.create_page(None, "x", parent_type="data_source")


# ---------------------------------------------------------------------------
# 8. 转换器往返一致性：Markdown → blocks → (模拟 Notion) → Markdown
# ---------------------------------------------------------------------------
class MaterializedClient:
    """把 markdown_to_blocks 产出的 block 树「物化」成 Notion 会返回的形状。

    这样就能在完全离线的情况下验证两条路径互相咬合：
    写进去的东西，读回来必须还在。
    """

    def __init__(self, blocks: list[dict]) -> None:
        self.tree: dict[str, list[dict]] = {}
        self._counter = 0

    def _register(self, items: list[dict]) -> list[dict]:
        out: list[dict] = []
        for item in items:
            btype = item["type"]
            payload = dict(item[btype])
            children = payload.pop("children", None)
            self._counter += 1
            block_id = f"blk{self._counter}"
            out.append({"id": block_id, "type": btype, btype: payload,
                        "has_children": bool(children)})
            if children:
                self.tree[block_id] = self._register(children)
        return out

    def materialize(self, blocks: list[dict]) -> list[dict]:
        self.tree["root"] = self._register(blocks)
        return self.tree["root"]

    def list_children(self, block_id: str) -> list[dict]:
        return self.tree.get(block_id, [])


ROUND_TRIP_SOURCE = """# 一级标题

正文段落，含 **粗体** 与 `行内码`。

## 二级标题

- 甲项
- 乙项
  - 嵌套项
- [x] 完成的待办
- [ ] 未完成的待办

1. 有序一
2. 有序二

> 引用行

```python
x = 1
```

---
"""


class TestRoundTrip(unittest.TestCase):
    def render_back(self, source: str) -> str:
        client = MaterializedClient(md.markdown_to_blocks(source))
        return md.blocks_to_markdown(client, client.materialize(md.markdown_to_blocks(source)))

    def test_all_constructs_survive(self):
        out = self.render_back(ROUND_TRIP_SOURCE)
        for anchor in ("# 一级标题", "## 二级标题", "正文段落", "**粗体**", "`行内码`",
                       "- 甲项", "- 乙项", "  - 嵌套项",
                       "- [x] 完成的待办", "- [ ] 未完成的待办",
                       "1. 有序一", "2. 有序二", "> 引用行",
                       "```python", "x = 1", "---"):
            self.assertIn(anchor, out, f"往返后丢失：{anchor!r}\n--- 实际 ---\n{out}")

    def test_nested_list_depth_preserved(self):
        out = self.render_back("- 父\n  - 子\n    - 孙\n")
        self.assertIn("- 父", out)
        self.assertIn("  - 子", out)
        self.assertIn("    - 孙", out)

    def test_no_content_silently_dropped(self):
        """不含 Markdown 语义的普通文字必须原样保留。"""
        out = self.render_back("这是一句普通的中文。\n第二行会并入同一段。\n")
        self.assertIn("这是一句普通的中文。", out)
        self.assertIn("第二行会并入同一段。", out)


# ---------------------------------------------------------------------------
# 5. 结构化块：表格 / 多列 / callout / 折叠块 / 公式 / 图片
# ---------------------------------------------------------------------------
class TestStructuredBlocks(unittest.TestCase):
    def parse(self, text):
        return md.markdown_to_blocks(text)

    # -- 表格 ---------------------------------------------------------------
    def test_pipe_table_with_header(self):
        blocks = self.parse("| 名称 | 数量 |\n| --- | --- |\n| 甲 | 1 |\n| 乙 | 2 |\n")
        self.assertEqual(blocks[0]["type"], "table")
        table = blocks[0]["table"]
        self.assertEqual(table["table_width"], 2)
        self.assertTrue(table["has_column_header"])
        self.assertEqual(len(table["children"]), 3)
        first = table["children"][0]["table_row"]["cells"]
        self.assertEqual(first[0][0]["text"]["content"], "名称")
        self.assertEqual(table["children"][2]["table_row"]["cells"][1][0]["text"]["content"], "2")

    def test_pipe_table_without_header(self):
        blocks = self.parse("| a | b |\n| c | d |\n")
        self.assertFalse(blocks[0]["table"]["has_column_header"])
        self.assertEqual(len(blocks[0]["table"]["children"]), 2)

    def test_table_empty_cell_is_empty_array(self):
        blocks = self.parse("| a | b |\n| --- | --- |\n|  | 2 |\n")
        cells = blocks[0]["table"]["children"][1]["table_row"]["cells"]
        self.assertEqual(cells[0], [])
        self.assertEqual(cells[1][0]["text"]["content"], "2")

    def test_table_escaped_pipe(self):
        blocks = self.parse("| a | b |\n| --- | --- |\n| x \\| y | z |\n")
        cells = blocks[0]["table"]["children"][1]["table_row"]["cells"]
        self.assertEqual(cells[0][0]["text"]["content"], "x | y")
        self.assertEqual(len(cells), 2)

    def test_table_render_then_parse_is_stable(self):
        tree = {
            "root": [block("tb", "table",
                           {"table_width": 2, "has_column_header": True}, children=True)],
            "tb": [
                {"id": "r1", "type": "table_row",
                 "table_row": {"cells": [rich("A"), rich("B")]}},
                {"id": "r2", "type": "table_row",
                 "table_row": {"cells": [rich("1"), rich("2")]}},
            ],
        }
        text = md.blocks_to_markdown(FakeClient(tree), tree["root"])
        again = self.parse(text)
        self.assertEqual(again[0]["type"], "table")
        self.assertEqual(again[0]["table"]["table_width"], 2)
        self.assertTrue(again[0]["table"]["has_column_header"])
        self.assertEqual(len(again[0]["table"]["children"]), 2)

    # -- 多列 ---------------------------------------------------------------
    def test_columns_parse(self):
        source = (
            "<columns>\n"
            '\t<column ratio="50">\n'
            "\t\t### 左列\n"
            "\t\t- 甲\n"
            "\t</column>\n"
            '\t<column ratio="50">\n'
            "\t\t### 右列\n"
            "\t\t> 引用\n"
            "\t</column>\n"
            "</columns>\n"
        )
        blocks = self.parse(source)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "column_list")
        columns = blocks[0]["column_list"]["children"]
        self.assertEqual(len(columns), 2)
        self.assertEqual(columns[0]["column"]["width_ratio"], 0.5)
        self.assertEqual(columns[0]["column"]["children"][0]["type"], "heading_3")
        self.assertEqual(columns[0]["column"]["children"][1]["type"], "bulleted_list_item")
        self.assertEqual(columns[1]["column"]["children"][1]["type"], "quote")

    def test_columns_render_then_parse_is_stable(self):
        tree = {
            "root": [block("cl", "column_list", {}, children=True)],
            "cl": [
                {"id": "c1", "type": "column", "column": {"width_ratio": 0.5},
                 "has_children": True},
                {"id": "c2", "type": "column", "column": {"width_ratio": 0.5},
                 "has_children": True},
            ],
            "c1": [block("p1", "paragraph", {"rich_text": rich("左边")})],
            "c2": [block("p2", "paragraph", {"rich_text": rich("右边")})],
        }
        text = md.blocks_to_markdown(FakeClient(tree), tree["root"])
        self.assertIn("<columns>", text)
        self.assertIn('<column ratio="50">', text)
        again = self.parse(text)
        self.assertEqual(again[0]["type"], "column_list")
        self.assertEqual(len(again[0]["column_list"]["children"]), 2)
        self.assertEqual(again[0]["column_list"]["children"][0]["column"]["width_ratio"], 0.5)

    # -- callout / 折叠块 ---------------------------------------------------
    def test_callout_parse_keeps_icon_and_text(self):
        blocks = self.parse('<callout icon="🔥">\n\t正文\n\t- 子项\n</callout>\n')
        payload = blocks[0]["callout"]
        self.assertEqual(blocks[0]["type"], "callout")
        self.assertEqual(payload["icon"]["emoji"], "🔥")
        self.assertEqual(payload["rich_text"][0]["text"]["content"], "正文")
        self.assertEqual(payload["children"][0]["type"], "bulleted_list_item")

    def test_toggle_parse(self):
        blocks = self.parse("<details>\n<summary>标题</summary>\n\t- 隐藏项\n</details>\n")
        payload = blocks[0]["toggle"]
        self.assertEqual(blocks[0]["type"], "toggle")
        self.assertEqual(payload["rich_text"][0]["text"]["content"], "标题")
        self.assertEqual(payload["children"][0]["type"], "bulleted_list_item")

    def test_toggle_render_then_parse_is_stable(self):
        tree = {
            "root": [block("t", "toggle", {"rich_text": rich("展开我")}, children=True)],
            "t": [block("p", "paragraph", {"rich_text": rich("里面")})],
        }
        text = md.blocks_to_markdown(FakeClient(tree), tree["root"])
        self.assertIn("<summary>展开我</summary>", text)
        again = self.parse(text)
        self.assertEqual(again[0]["type"], "toggle")
        self.assertEqual(again[0]["toggle"]["rich_text"][0]["text"]["content"], "展开我")
        self.assertEqual(again[0]["toggle"]["children"][0]["paragraph"]["rich_text"][0]
                         ["text"]["content"], "里面")

    # -- 公式 / 图片 --------------------------------------------------------
    def test_equation_and_image(self):
        blocks = self.parse("$$ E = mc^2 $$\n\n![图示](https://example.com/a.png)\n")
        self.assertEqual(blocks[0]["type"], "equation")
        self.assertEqual(blocks[0]["equation"]["expression"], "E = mc^2")
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["image"]["external"]["url"], "https://example.com/a.png")
        self.assertEqual(blocks[1]["image"]["caption"][0]["text"]["content"], "图示")

    def test_columns_inside_paragraph_not_swallowed(self):
        blocks = self.parse("前一段\n<columns>\n\t<column>\n\t\t内\n\t</column>\n</columns>\n")
        self.assertEqual([b["type"] for b in blocks], ["paragraph", "column_list"])


def _write_report(result: unittest.TestResult) -> pathlib.Path:
    report = {
        "suite": "notionsync unit",
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "ok": result.wasSuccessful(),
        "failed_names": [str(name) for name, _ in result.failures + result.errors],
    }
    path = TEMP_DIR / "report_unit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n报告已写入：{_write_report(result)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
