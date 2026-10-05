"""本地自检：确认 sanitize_blocks 能把会被 Notion 400 的块修好（不联网）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from push_to_notion import RT_LIMIT, _u16len, sanitize_blocks  # noqa: E402

failures = []

# 1) 超长 emoji 段落：按 UTF-16 切分
text = "🎉" * 1500          # 1500 个码点 = 3000 个 UTF-16 码元
blocks = sanitize_blocks([{"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [{"type": "text",
                                                        "text": {"content": text}}]}}])
lens = [_u16len(item["text"]["content"]) for item in blocks[0]["paragraph"]["rich_text"]]
if max(lens) > RT_LIMIT or "".join(i["text"]["content"] for i in blocks[0]["paragraph"]["rich_text"]) != text:
    failures.append(f"rich_text 切分异常: {lens}")

# 2) 292 行的大表格 → 拆成多张，每张 ≤ 100 行
rows = [{"object": "block", "type": "table_row",
         "table_row": {"cells": [[{"type": "text", "text": {"content": f"r{i}c0"}}],
                                 [{"type": "text", "text": {"content": f"r{i}c1"}}]]}}
        for i in range(292)]
table = {"object": "block", "type": "table",
         "table": {"table_width": 2, "has_column_header": True, "has_row_header": False,
                   "children": rows}}
out = sanitize_blocks([table])
if not all(b["type"] == "table" for b in out):
    failures.append("表格拆分后类型不对")
if not all(len(b["table"]["children"]) <= 100 for b in out):
    failures.append(f"表格仍超限: {[len(b['table']['children']) for b in out]}")
total_data = sum(len(b["table"]["children"]) - 1 for b in out)
if total_data != 291:  # 292 行里首行是表头，数据行 291
    failures.append(f"表格行数丢失: {total_data} != 291")
if sum(1 for b in out if b["table"]["children"][0] is rows[0]) != len(out):
    failures.append("后续分片没有重复表头")

# 3) 非法图片 URL → 降级为段落
img = {"object": "block", "type": "image",
       "image": {"type": "external", "external": {"url": "./assets/a.png"},
                 "caption": [{"type": "text", "text": {"content": "架构图"}}]}}
out = sanitize_blocks([img])
if out[0]["type"] != "paragraph" or "./assets/a.png" not in out[0]["paragraph"]["rich_text"][0]["text"]["content"]:
    failures.append("非法图片没有降级为段落")

# 4) 合法 https 图片保留
ok_img = {"object": "block", "type": "image",
          "image": {"type": "external", "external": {"url": "https://x.test/a.png"},
                    "caption": []}}
if sanitize_blocks([ok_img])[0]["type"] != "image":
    failures.append("合法图片被误伤")

# 5) 未知代码语言 → plain text
code = {"object": "block", "type": "code", "code": {"rich_text": [], "language": "brainfuck"}}
if sanitize_blocks([code])[0]["code"]["language"] != "plain text":
    failures.append("未知语言没有降级")

print("FAIL" if failures else "ALL CHECKS PASSED")
for item in failures:
    print(" -", item)
raise SystemExit(1 if failures else 0)
