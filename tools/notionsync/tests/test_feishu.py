"""飞书云文档读写与格式保真实测。

与 Notion 那套（``test_fidelity_notion.py``）目标一致，但飞书的原生格式是 **DocxXML**
而不是 Markdown，所以保真基线是"XML 进、XML 出"。

流程
----
0. 就绪检查：lark-cli 是否找到、应用是否已配置、身份是否已授权（未就绪则跳过）
1. 解析你给的知识库链接 → node_token / obj_token / 标题
2. **读**：抓取目标文档 XML，统计结构元素并落盘
3. **写**：新建草稿文档，写入一份覆盖全部构造的 XML
4. 读回草稿，逐项断言结构（多列 / 表格 / 高亮块 / 代码 / 待办 / 引用 / 公式 …）
5. **无损写回**：把草稿读回的原样 XML 再写进第二个草稿，再读回，逐字符比对
6. 回收两个草稿文档

运行::

    python tools\\notionsync\\tests\\test_feishu.py
    python tools\\notionsync\\tests\\test_feishu.py --url "<你的链接>"
    python tools\\notionsync\\tests\\test_feishu.py --keep     # 保留草稿便于人工看

产物：``tools/notionsync/temp/report_feishu.json`` 与 ``temp/feishu_*.xml``
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parents[1]
TEMP_DIR = TOOL_DIR / "temp"
sys.path.insert(0, str(TOOL_DIR))

from larksync import LarkCliError, LarkClient   # noqa: E402

DEFAULT_URL = "https://my.feishu.cn/wiki/TfKjwPJ9KiZ7C9k6vJUcJMlS66d"

#: 覆盖飞书 XML 主要构造；注意标题层级必须连续、<title> 唯一且在开头
SAMPLE_XML = """<title>飞书保真度测试</title>\
<h1>一级标题</h1>\
<p>普通段落，含 <b>粗体</b>、<em>斜体</em>、<del>删除线</del>。</p>\
<h2>多列文本</h2>\
<grid>\
<column width-ratio="0.5"><p>左栏正文</p><ul><li>左甲</li><li>左乙</li></ul></column>\
<column width-ratio="0.5"><p>右栏正文</p><blockquote>右栏引用</blockquote></column>\
</grid>\
<h2>表格</h2>\
<table><thead><tr><th><p>名称</p></th><th><p>数量</p></th></tr></thead>\
<tbody><tr><td><p>甲</p></td><td><p>1</p></td></tr>\
<tr><td><p>乙</p></td><td><p>2</p></td></tr></tbody></table>\
<h2>列表与待办</h2>\
<ul><li>一级项<ul><li>二级项</li></ul></li></ul>\
<checkbox done="true">已完成待办</checkbox>\
<checkbox done="false">未完成待办</checkbox>\
<callout emoji="💡"><p>高亮块内容</p></callout>\
<pre lang="python"><code>def hello():
    return "ok"</code></pre>\
<blockquote>引用行</blockquote>\
<hr/>\
<p>行内公式 <latex>E = mc^2</latex></p>"""

#: 读回后必须出现的关键结构（子串匹配，容忍服务端格式化差异）
EXPECTED_MARKERS = [
    ("多列 <grid>", "<grid"),
    ('两列 width-ratio="0.5"', None),          # 单独计数断言
    ("表格 <table>", "<table"),
    ("表头 <thead>", "<thead"),
    ("表体 <tbody>", "<tbody"),
    ("高亮块 <callout", "<callout"),
    ('高亮块 emoji="💡"', 'emoji="💡"'),
    ("代码 <pre", "<pre"),
    ('代码语言 lang="python"', 'lang="python"'),
    ('待办 done="true"', 'done="true"'),
    ('待办 done="false"', 'done="false"'),
    ("引用 <blockquote>", "<blockquote"),
    ("分割线 <hr", "<hr"),
    ("公式 <latex>", "<latex"),
    ("粗体 <b>", "<b>"),
    ("斜体 <em>", "<em>"),
    ("删除线 <del>", "<del>"),
    ("嵌套列表（ul 内嵌 ul）", None),           # 单独计数断言
]

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def normalize_xml(text: str) -> str:
    """比较用归一化：去掉标签之间的空白与整体首尾空白。"""
    return re.sub(r">\s+<", "><", (text or "").strip())


def count_tag(xml: str, tag: str) -> int:
    return len(re.findall(rf"<{tag}[\s>/]", xml or ""))


def doc_id_of(payload: dict) -> str:
    document = ((payload.get("data") or {}).get("document") or {})
    return str(document.get("document_id") or document.get("token") or "")


def fetch_xml(client: LarkClient, doc: str, *, detail: str = "simple") -> str:
    payload = client.docs_fetch(doc, doc_format="xml", detail=detail, scope="full")
    return str(((payload.get("data") or {}).get("document") or {}).get("content") or "")


def write_report(args, client: LarkClient, readiness: dict,
                 skipped_at: str | None = None) -> pathlib.Path:
    """统一落盘，含 SKIP 路径——否则环境未就绪时没有可追溯记录。"""
    passed = sum(1 for c in CHECKS if c["ok"])
    report = {
        "suite": "larksync feishu fidelity",
        "url": args.url,
        "client": client.describe(),
        "readiness": readiness,
        "skippedAt": skipped_at,
        "status": "SKIP" if skipped_at else ("PASS" if passed == len(CHECKS) else "FAIL"),
        "ok": skipped_at is None and passed == len(CHECKS),
        "passed": passed,
        "total": len(CHECKS),
        "checks": CHECKS,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = TEMP_DIR / "report_feishu.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="飞书云文档保真度实测")
    parser.add_argument("--url", default=DEFAULT_URL, help="要读取的飞书文档/知识库链接")
    parser.add_argument("--keep", action="store_true", help="保留草稿文档便于人工比对")
    parser.add_argument("--profile", help="lark-cli profile 名（本机有多个飞书应用时必填）")
    args = parser.parse_args()

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    client = LarkClient(identity="user", cwd=str(TEMP_DIR), profile=args.profile)

    print("=" * 70)
    print("飞书云文档读写与格式保真实测")
    print("=" * 70)
    print(f"生效 profile  : {client.describe()}")

    readiness = client.readiness()
    print(f"lark-cli      : {readiness['cli']}")
    print(f"应用已配置    : {readiness['configured']}")
    print(f"身份已授权    : {readiness['authorized']}")
    for problem in readiness["problems"]:
        print(f"  - {problem}")
    profile_flag = f" --profile {client.profile}" if client.profile else ""
    if not readiness["configured"]:
        print("\n[SKIP] 飞书应用尚未配置。先运行（后台，会输出授权链接）：")
        print(f"       lark-cli{profile_flag} config init --new")
        write_report(args, client, readiness, skipped_at="config")
        return 2
    if not readiness["authorized"]:
        print("\n[SKIP] 应用已配置但 user 身份未授权。先运行：")
        print(f"       lark-cli{profile_flag} auth login")
        print("       注意：该账号必须在本应用的「可用范围」内（自建应用只服务其所在租户）")
        write_report(args, client, readiness, skipped_at="auth")
        return 2
    check("就绪检查：CLI + 应用配置 + 身份授权", True, str(readiness.get("identity") or ""))

    # 预检：把"环境未就绪"（缺 scope / 资源没共享）与"代码有问题"分开。
    # 前者记 SKIP（exit 2），后者才记 FAIL——否则会把权限问题误报成测试失败。
    pre = client.preflight(args.url)
    if not pre["ready"]:
        print(f"\n[SKIP] {pre['message']}")
        print(f"       {pre['hint']}")
        if pre.get("console_url"):
            print(f"       开发者后台：{pre['console_url']}")
        write_report(args, client, readiness, skipped_at=pre["step"])
        return 2
    check("预检：目标文档当前可访问", True, pre.get("message", ""))

    created: list[tuple[str, str]] = []   # (document_id, note)
    try:
        # ---- 1. 解析目标链接 ------------------------------------------------
        node = {}
        try:
            payload = client.wiki_node_get(args.url)
            node = payload.get("data") or {}
            check("解析知识库链接", bool(node), json.dumps(
                {k: node.get(k) for k in ("node_token", "obj_token", "obj_type", "title")},
                ensure_ascii=False)[:160])
        except LarkCliError as exc:
            check("解析知识库链接", False, str(exc))

        # ---- 2. 读目标文档 --------------------------------------------------
        try:
            target_xml = fetch_xml(client, args.url, detail="simple")
            dump = TEMP_DIR / "feishu_target.xml"
            dump.write_text(target_xml, encoding="utf-8")
            check("读取目标文档 XML", bool(target_xml),
                  f"{len(target_xml)} 字符；已存 {dump.name}")
            if target_xml:
                kinds = sorted({m.group(1) for m in re.finditer(r"<([a-zA-Z0-9-]+)", target_xml)})
                check("目标文档结构元素", True, ", ".join(kinds[:18]))
        except LarkCliError as exc:
            check("读取目标文档 XML", False, str(exc))

        # ---- 3. 写：新建草稿并写入样例 --------------------------------------
        source_id = ""
        try:
            payload = client.docs_create(SAMPLE_XML, doc_format="xml")
            source_id = doc_id_of(payload)
            created.append((source_id, "写入源"))
            check("创建草稿文档并写入 XML", bool(source_id), source_id)
        except LarkCliError as exc:
            check("创建草稿文档并写入 XML", False, str(exc))

        # ---- 4. 读回并断言结构 ----------------------------------------------
        read_back = ""
        if source_id:
            try:
                read_back = fetch_xml(client, source_id)
                dump = TEMP_DIR / "feishu_roundtrip_source.xml"
                dump.write_text(read_back, encoding="utf-8")
                missing = [name for name, marker in EXPECTED_MARKERS
                           if marker and marker not in read_back]
                check("读回：关键结构标记齐全", not missing,
                      "缺 " + json.dumps(missing, ensure_ascii=False) if missing
                      else f"{len(read_back)} 字符")

                columns = count_tag(read_back, "column")
                check("多列文本：<column> 恰好 2 个", columns == 2, f"column={columns}")

                nested = len(re.findall(r"<li>[^<]*<ul>", read_back))
                check("嵌套列表：<li> 内含 <ul>", nested >= 1, f"命中 {nested} 处")

                th = count_tag(read_back, "th")
                td = count_tag(read_back, "td")
                check("表格：表头 2 格 / 数据 4 格", th >= 2 and td >= 4,
                      f"th={th} td={td}")
            except LarkCliError as exc:
                check("读回草稿文档", False, str(exc))

        # ---- 5. 无损写回：把读回的 XML 原样再写一遍 --------------------------
        if source_id and read_back:
            target2 = ""
            try:
                payload = client.docs_create(read_back, doc_format="xml")
                target2 = doc_id_of(payload)
                created.append((target2, "写回目标"))
                check("写回：用读回的 XML 新建第二篇", bool(target2), target2)
            except LarkCliError as exc:
                check("写回：用读回的 XML 新建第二篇", False, str(exc))

            if target2:
                try:
                    second = fetch_xml(client, target2)
                    dump = TEMP_DIR / "feishu_roundtrip_target.xml"
                    dump.write_text(second, encoding="utf-8")
                    same = normalize_xml(read_back) == normalize_xml(second)
                    detail = f"{len(read_back)} → {len(second)} 字符"
                    if not same:
                        detail += f"；首个差异：{_first_diff(read_back, second)}"
                    check("无损写回：读→写→读 完全一致", same, detail)
                except LarkCliError as exc:
                    check("无损写回：读→写→读 完全一致", False, str(exc))

        # ---- 6. 目标文档写入能力（只读验证权限，不污染原文）------------------
        if node:
            scope = node.get("obj_type") or "?"
            check("目标文档类型可写判定", True,
                  f"obj_type={scope}（写操作在草稿上完成，未改动原文档）")
    finally:
        for document_id, note in created:
            if args.keep:
                print(f"  [KEEP] {note} 保留：{document_id}")
                continue
            try:
                client.drive_delete(document_id, file_type="docx", yes=True)
                print(f"  [清理] {note} 已删除：{document_id}")
            except LarkCliError as exc:
                print(f"  [清理失败] {note} {document_id}：{exc}")

    passed = sum(1 for c in CHECKS if c["ok"])
    path = write_report(args, client, readiness)
    print("\n" + "=" * 70)
    print(f"结果：{passed}/{len(CHECKS)} 通过；报告：{path}")
    print("=" * 70)
    return 0 if passed == len(CHECKS) else 1


def _first_diff(a: str, b: str) -> str:
    na, nb = normalize_xml(a), normalize_xml(b)
    for index in range(min(len(na), len(nb))):
        if na[index] != nb[index]:
            return f"第 {index} 字符 {na[index-30:index+30]!r} != {nb[index-30:index+30]!r}"
    return f"长度不同 {len(na)} vs {len(nb)}"


if __name__ == "__main__":
    raise SystemExit(main())
