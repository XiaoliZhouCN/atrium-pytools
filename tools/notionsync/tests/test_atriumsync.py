"""atriumsync 离线回归测试 —— 不触网、不需要凭据。

覆盖三层里最容易出错、且**已经有真实 bug 教训**的地方：

* 命名规则（``Shirley's Knowledge Repo`` → ``ShirleysKnowledgeRepo``）
* 相对工作根目录的链接（csv 里的 ``[🔗](technology/pages/x.md)``）
* Markdown 行内语法 → run（粗体/斜体/删除线/链接，含嵌套）
* 块 → 飞书 DocxXML（多列、表格、高亮块、代码、待办）
* 飞书正文块 id 提取 —— ``<li>`` 也是块，不能只看顶层
* 幂等命名：同一个 Notion 页面重跑必须复用原文件名
* 从【每日资讯 整理要求】提取三个推送时间点任务

运行::

    python tools\\notionsync\\tests\\test_atriumsync.py
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import time
import unittest
import uuid

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parents[1]
TEMP_DIR = TOOL_DIR / "temp"
sys.path.insert(0, str(TOOL_DIR))

from atriumsync import feishu_xml as fx                       # noqa: E402
from atriumsync import paths                                   # noqa: E402
from atriumsync import pull                                    # noqa: E402
from atriumsync import push                                    # noqa: E402

TEMP_DIR.mkdir(parents=True, exist_ok=True)

CHECKS: list[dict] = []


# ---------------------------------------------------------------------------
# 命名与路径
# ---------------------------------------------------------------------------
class TestNaming(unittest.TestCase):
    def test_user_example(self):
        """用户明确给的例子，必须逐字一致。"""
        self.assertEqual(paths.slug_title("Shirley's Knowledge Repo"),
                         "ShirleysKnowledgeRepo")

    def test_cjk_and_fullwidth_punctuation(self):
        self.assertEqual(paths.slug_title("每日资讯 整理要求"), "每日资讯整理要求")
        self.assertEqual(paths.slug_title("欢迎来到Shirley’s Dashboard！"),
                         "欢迎来到ShirleysDashboard")

    def test_uppercase_tokens_preserved(self):
        self.assertEqual(paths.slug_title("GPU 渲染"), "GPU渲染")
        self.assertEqual(paths.slug_title("PQ"), "PQ")
        self.assertEqual(paths.slug_title("NVIDIA Video Codec SDK"), "NVIDIAVideoCodecSDK")

    def test_dangerous_chars_removed(self):
        for title in ("a/b", "a:b", "a*b", "a?b", 'a"b', "a<b>c", "a|b"):
            name = paths.slug_title(title)
            self.assertFalse(set(name) & set('<>:"/\\|?*'), f"{title} -> {name}")

    def test_empty(self):
        self.assertEqual(paths.slug_title(""), "Untitled")

    def test_markdown_link_relative_to_root(self):
        path = paths.TECHNOLOGY / "pages" / "PQ.md"
        self.assertEqual(paths.markdown_link(path), "[🔗](technology/pages/PQ.md)")


# ---------------------------------------------------------------------------
# 行内 Markdown → runs
# ---------------------------------------------------------------------------
class TestInline(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(fx.parse_inline("普通文本"),
                         [{"text": "普通文本"}])

    def test_annotations(self):
        runs = fx.parse_inline("a **粗** b *斜* c ~~删~~ d")
        by_text = {r["text"]: r for r in runs}
        self.assertTrue(by_text["粗"].get("bold"))
        self.assertTrue(by_text["斜"].get("italic"))
        self.assertTrue(by_text["删"].get("strike"))

    def test_link(self):
        runs = fx.parse_inline("见 [文档](https://example.com/a)")
        link = [r for r in runs if r.get("link")]
        self.assertEqual(len(link), 1)
        self.assertEqual(link[0]["text"], "文档")
        self.assertEqual(link[0]["link"], "https://example.com/a")

    def test_nested_bold_link(self):
        runs = fx.parse_inline("**[文档](https://x.y)**")
        self.assertTrue(all(r.get("bold") for r in runs))
        self.assertEqual(runs[0]["link"], "https://x.y")

    def test_inline_code_flagged(self):
        runs = fx.parse_inline("用 `code` 表示")
        code = [r for r in runs if r.get("code")]
        self.assertEqual(code[0]["text"], "code")

    def test_escape(self):
        self.assertEqual(fx.escape_text('a<b>&c\nd'),
                         "a&lt;b&gt;&amp;c<br/>d")

    def test_runs_to_xml(self):
        xml = fx.runs_to_xml(fx.parse_inline("**粗** 与 [链](https://x.y)"))
        self.assertIn("<b>粗</b>", xml)
        self.assertIn('<a href="https://x.y">链</a>', xml)


# ---------------------------------------------------------------------------
# 块 → 飞书 DocxXML
# ---------------------------------------------------------------------------
RICH_MD = """# 文档标题

## 小节

正文，含 **粗体** 与 [链接](https://e.com)。

<columns>
\t<column ratio="50">
\t\t左栏文字
\t</column>
\t<column ratio="50">
\t\t右栏文字
\t</column>
</columns>

| 名称 | 数量 |
| --- | --- |
| 甲 | 1 |
| 乙 | 2 |

- 项目一
- [x] 已完成

<callout icon="💡">
提示文本
</callout>

```python
x = 1
```

---
"""


class TestFeishuXml(unittest.TestCase):
    def setUp(self):
        self.xml, self.degraded = fx.markdown_to_feishu_xml(RICH_MD)

    def test_title_extracted(self):
        self.assertTrue(self.xml.startswith("<title>文档标题</title>"))

    def test_core_tags(self):
        self.assertIn("<h2>小节</h2>", self.xml)
        self.assertIn("<b>粗体</b>", self.xml)
        self.assertIn('<a href="https://e.com">链接</a>', self.xml)
        self.assertIn("<hr/>", self.xml)
        self.assertIn('<pre lang="python"><code>', self.xml)

    def test_grid_columns(self):
        self.assertIn("<grid>", self.xml)
        self.assertEqual(self.xml.count('<column width-ratio="0.5">'), 2)
        self.assertIn("左栏文字", self.xml)
        self.assertIn("右栏文字", self.xml)

    def test_table_head_and_body(self):
        self.assertIn("<table>", self.xml)
        self.assertIn("<thead><tr><th><p>名称</p></th><th><p>数量</p></th></tr></thead>", self.xml)
        self.assertIn("<tbody>", self.xml)
        self.assertEqual(self.xml.count("<tr>"), 3)   # 表头 1 + 数据 2

    def test_lists_and_checkbox(self):
        self.assertIn("<ul><li>项目一</li>", self.xml)
        self.assertIn('<checkbox done="true">已完成</checkbox>', self.xml)

    def test_callout(self):
        self.assertIn('<callout emoji="💡">', self.xml)
        self.assertIn("</callout>", self.xml)

    def test_no_degradation_for_supported(self):
        self.assertEqual(self.degraded, [], f"不应有降级：{self.degraded}")

    def test_toggle_is_degraded_explicitly(self):
        xml, degraded = fx.markdown_to_feishu_xml("<details>\n<summary>折叠</summary>\n\t内容\n</details>\n")
        self.assertIn("toggle->heading+children", degraded)
        self.assertIn("折叠", xml)

    def test_callout_child_restricted(self):
        """高亮块内不允许表格：必须降级成段落并记录。"""
        xml, degraded = fx.markdown_to_feishu_xml(
            '<callout icon="💡">\n| a | b |\n| --- | --- |\n| 1 | 2 |\n</callout>\n')
        self.assertIn("callout:", " ".join(degraded))


# ---------------------------------------------------------------------------
# 飞书正文块 id 提取（真实 bug 的回归点）
# ---------------------------------------------------------------------------
REAL_XML = (
    '<title id="FI0L">标题</title>'
    '<h1 id="AAA"><b>AI Daily Tasks</b></h1>'
    '<ul><li id="B1">x</li><li id="B2">8:00</li></ul>'
    '<h1 id="CCC">Technology Gallery</h1>'
    '<ul><li id="D1">y</li></ul>'
)


class TestBlockIds(unittest.TestCase):
    def test_lists_items_count_as_blocks(self):
        """<li> 是独立块（<ul> 只是分组、没有 id）——漏掉它们会导致区间替换失败。"""
        ids = push.body_block_ids(REAL_XML)
        self.assertEqual(ids, ["AAA", "B1", "B2", "CCC", "D1"])

    def test_title_excluded(self):
        self.assertNotIn("FI0L", push.body_block_ids(REAL_XML))

    def test_first_last_span_all_blocks(self):
        ids = push.body_block_ids(REAL_XML)
        self.assertEqual((ids[0], ids[-1]), ("AAA", "D1"))

    def test_split_title(self):
        title, body = push.split_title(REAL_XML)
        self.assertEqual(title, '<title id="FI0L">标题</title>')
        self.assertTrue(body.startswith("<h1"))

    def test_empty(self):
        self.assertEqual(push.body_block_ids(""), [])


# ---------------------------------------------------------------------------
# 幂等命名（真实 bug 的回归点）
# ---------------------------------------------------------------------------
class TestIdempotentNaming(unittest.TestCase):
    def setUp(self):
        self.directory = TEMP_DIR / f"_idem_{uuid.uuid4().hex[:8]}"
        (self.directory / paths.FORMATS_DIRNAME).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _write_sidecar(self, stem: str, page_id: str) -> None:
        (self.directory / paths.FORMATS_DIRNAME / f"{stem}.formats.json").write_text(
            json.dumps({"source": {"pageId": page_id}}), encoding="utf-8")

    def test_same_page_reuses_filename(self):
        (self.directory / "PQ.md").write_text("x", encoding="utf-8")
        self._write_sidecar("PQ", "page-1")
        # 同一个 Notion 页面重跑：必须还是 PQ，不能变成 PQ-2
        self.assertEqual(pull._resolve_stem(self.directory, "PQ", "page-1"), "PQ")

    def test_different_page_gets_suffix(self):
        (self.directory / "PQ.md").write_text("x", encoding="utf-8")
        self._write_sidecar("PQ", "page-1")
        # 不同页面撞同名：才追加序号
        self.assertEqual(pull._resolve_stem(self.directory, "PQ", "page-2"), "PQ-2")

    def test_no_existing_file_uses_plain_stem(self):
        self.assertEqual(pull._resolve_stem(self.directory, "New", "page-9"), "New")


# ---------------------------------------------------------------------------
# AI Daily Tasks 提取
# ---------------------------------------------------------------------------
SPEC_MD = """# 每日资讯 整理要求

## 一、推送主题与时间

| 序号 | 推送主题 | 推送时间 | 覆盖范围 |
| --- | --- | --- | --- |
| 1 | 生产效率：AI与工具发展 | 每日 08:00 | AI模型/产品、Agent/平台、工具更新 |
| 2 | 专业领域：色彩管理与媒体编解码 | 每日 12:00 | 色彩管理、图片/视频编解码 |
| 3 | 专业领域：渲染与开发 | 每日 16:00 | 实时渲染、静态渲染、引擎、建模软件 |

## 三、推送内容从何而来

### 3.1 去哪里取

| Topic 取值 | 对应邮件 | 推送时间 |
| --- | --- | --- |
| `AI与工具 08:00` | 生产效率：AI 与工具发展 | 每日 08:00 |
| `色彩与编解码 12:00` | 专业领域：色彩管理与媒体编解码 | 每日 12:00 |
| `渲染与开发 16:00` | 专业领域：渲染与开发 | 每日 16:00 |
"""


class TestDailyTasks(unittest.TestCase):
    def setUp(self):
        self.text = pull.build_daily_tasks(SPEC_MD)

    def test_three_time_points(self):
        for when in ("每日 08:00", "每日 12:00", "每日 16:00"):
            self.assertIn(f"## {when}", self.text)

    def test_topic_mapping_merged(self):
        self.assertIn('Topic = "AI与工具 08:00"', self.text)
        self.assertIn('Topic = "色彩与编解码 12:00"', self.text)
        self.assertIn('Topic = "渲染与开发 16:00"', self.text)

    def test_coverage_included(self):
        self.assertIn("实时渲染、静态渲染、引擎、建模软件", self.text)

    def test_query_uses_gallery_database(self):
        self.assertIn(paths.TECHNOLOGY_DB, self.text)

    def test_graceful_when_tables_missing(self):
        text = pull.build_daily_tasks("没有表格的页面")
        self.assertIn("未能从", text)


def _write_report(result: unittest.TestResult) -> pathlib.Path:
    report = {
        "suite": "atriumsync unit",
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "ok": result.wasSuccessful(),
        "failed_names": [str(n) for n, _ in result.failures + result.errors],
    }
    path = TEMP_DIR / "report_atriumsync.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n报告已写入：{_write_report(result)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
