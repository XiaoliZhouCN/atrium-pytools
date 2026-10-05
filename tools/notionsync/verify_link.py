"""notionsync / verify_link.py — 验证 Python ⇄ Notion 链路（MCP over HTTP）。

用途（一次性验证，非同步器本体）：
  1. 凭据与握手：能否用现有 dsh 凭据建立 Notion MCP 会话
  2. 读：关键词搜索、页面读取
  3. 数据库：取 schema（data source）+ 查询行
  4. 格式保真：写入含多列 / 表格 / 嵌套列表的页面，读回后逐项比对结构
  5. 写：对已存在页面做定点更新并复核

凭据来源（优先顺序）：
  NOTION_MCP_TOKEN 环境变量  →  ~/.dsh/.credentials.yaml 的 NOTION_OAUTH.accessToken
脚本不会打印任何凭据。

运行：
  & "D:\\Repositories\\Manager\\.venv\\Scripts\\python.exe" verify_link.py
  & "...python.exe" verify_link.py --read-only          # 跳过所有写入
  & "...python.exe" verify_link.py --page <URL|ID>      # 复用已有测试页，不新建

注意：MCP 未暴露删页工具。用 --page 复用同一页可避免堆垃圾；最终需手动删除。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

MCP_URL = "https://mcp.notion.com/mcp"
PROTOCOL = "2025-06-18"
CRED_PATH = pathlib.Path(os.path.expanduser("~/.dsh/.credentials.yaml"))

PAGE_HOME = "https://app.notion.com/p/39fe47bbdac5802c909bfddc000ca070"  # Personal Home Page
DB_SCREEN = "https://app.notion.com/p/3e2e47bbdac580e9b065ce3bffd1f777"  # Screen 数据库
SEARCH_TERM = "OpenGL"

SCRATCH_TITLE = "[SCRATCH] notionsync verify — 可删除"

# 多列 + 表格 + 嵌套列表 + 引用 + 分隔线：最容易在往返中丢失的排版
SAMPLE_CONTENT = "\n".join([
    "Round-trip format test.",
    "",
    "## Section heading",
    "",
    "<columns>",
    '\t<column ratio="50">',
    "\t\t### Left column",
    "\t\t- item one",
    "\t\t- item two",
    '\t</column>',
    '\t<column ratio="50">',
    "\t\t### Right column",
    "\t\t> quoted text",
    "\t\t---",
    '\t</column>',
    "</columns>",
    "",
    '<table fit-page-width="true" header-row="true">',
    "\t<tr>",
    "\t\t<td>Header A</td>",
    "\t\t<td>Header B</td>",
    "\t</tr>",
    "\t<tr>",
    "\t\t<td>cell one</td>",
    "\t\t<td>cell two</td>",
    "\t</tr>",
    "</table>",
])

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def load_token() -> str:
    env = os.environ.get("NOTION_MCP_TOKEN")
    if env:
        return env.strip()
    if not CRED_PATH.exists():
        raise SystemExit(f"凭据文件不存在：{CRED_PATH}")
    m = re.search(r"NOTION_OAUTH:\s*'(\{.*?\})'", CRED_PATH.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit("凭据文件中没有 NOTION_OAUTH 记录")
    return json.loads(m.group(1))["accessToken"]


def unwrap(text: str) -> str:
    """部分工具（如 notion-fetch）把结果再包一层 JSON：{metadata,title,text,...}。"""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        if isinstance(obj, dict) and isinstance(obj.get("text"), str):
            return obj["text"]
    return text


def page_id_of(value: str) -> str | None:
    hexes = re.findall(r"[0-9a-fA-F]{32}", value.replace("-", ""))
    return hexes[0] if hexes else None


class NotionMCP:
    """极简 MCP streamable-HTTP 客户端（仅标准库）。"""

    def __init__(self, token: str) -> None:
        self.token = token
        self.session_id: str | None = None
        self._seq = 0
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "atrium-notionsync-verify", "version": "0.1.0"},
        })
        self._rpc("notifications/initialized", None, notify=True)

    def _rpc(self, method: str, params, notify: bool = False, timeout: int = 180):
        body: dict = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._seq += 1
            body["id"] = self._seq
        if params is not None:
            body["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer " + self.token,
            "MCP-Protocol-Version": PROTOCOL,
            # 缺 User-Agent 会被 Cloudflare 以 error 1010 拦掉
            "User-Agent": "atrium-notionsync-verify/0.1.0",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(MCP_URL, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if not self.session_id:
                    self.session_id = (resp.headers.get("Mcp-Session-Id")
                                       or resp.headers.get("mcp-session-id"))
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return {"error": f"HTTP {exc.code}", "body": exc.read().decode("utf-8", "replace")[:300]}
        if notify:
            return None
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> dict:
        found = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if msg.get("id") is not None:
                found = msg
        if found is None:
            try:
                found = json.loads(raw)
            except json.JSONDecodeError:
                found = {"unparsed": raw[:300]}
        return found

    def tools(self) -> list[str]:
        resp = self._rpc("tools/list", {})
        return sorted(t["name"] for t in (resp.get("result") or {}).get("tools") or [])

    def call(self, name: str, args: dict) -> tuple[str, dict]:
        """返回 (已解包的可读文本, 原始 result)。"""
        resp = self._rpc("tools/call", {"name": name, "arguments": args})
        result = resp.get("result") or {}
        text = "\n".join(
            c.get("text", "") for c in result.get("content") or []
            if isinstance(c, dict) and c.get("type") == "text"
        )
        if not text and resp:
            text = json.dumps(resp)[:400]
        return unwrap(text), result


def first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(0) if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Python ⇄ Notion 链路")
    parser.add_argument("--read-only", action="store_true", help="跳过写入与往返测试")
    parser.add_argument("--page", help="复用已有测试页（URL 或 ID），不新建")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 68)
    print("Notion 链路验证 — MCP over HTTP（仅标准库）")
    print("=" * 68)

    # ---- 1. 凭据与握手 ----------------------------------------------------
    try:
        mcp = NotionMCP(load_token())
    except SystemExit as exc:
        print(f"[FAIL] 凭据加载 — {exc}")
        return 2
    record("凭据加载 + MCP 握手", bool(mcp.session_id), f"session={bool(mcp.session_id)}")

    tools = mcp.tools()
    record("tools/list", len(tools) > 0, f"{len(tools)} 个工具")
    for need in ("notion-search", "notion-fetch", "notion-create-pages",
                 "notion-update-page", "notion-query-data-sources"):
        record(f"  工具存在: {need}", need in tools)

    # ---- 2. 读：搜索 ------------------------------------------------------
    text, _ = mcp.call("notion-search", {"query": SEARCH_TERM, "page_size": 5})
    record(f"搜索 '{SEARCH_TERM}'", len(text) > 50, f"返回 {len(text)} 字符")

    # ---- 3. 读：页面 ------------------------------------------------------
    home, _ = mcp.call("notion-fetch", {"id": PAGE_HOME})
    record("读取 Personal Home Page", len(home) > 200, f"{len(home)} 字符")
    print("     该页格式特征:", {
        "columns": "<columns>" in home, "table": "<table" in home,
        "callout": "<callout" in home, "mention": "<mention-" in home,
        "database": "<database" in home or "<mention-database" in home,
    })

    # ---- 4. 数据库：schema + 查询 ----------------------------------------
    dbtxt, _ = mcp.call("notion-fetch", {"id": DB_SCREEN})
    ds = first(r'collection://[0-9a-fA-F-]{36}', dbtxt)
    record("获取数据库 schema", bool(ds), f"data source: {ds or '未找到'}")
    record("  数据库含 <data-source> 标记", "<data-source" in dbtxt)
    print("     数据库描述片段:")
    for ln in dbtxt.splitlines()[:14]:
        print("       " + ln[:150])

    if ds:
        rows, _ = mcp.call("notion-query-data-sources",
                           {"data": {"mode": "rows", "data_source_url": ds, "limit": 5}})
        record("查询数据库行 (rows)", len(rows) > 50, f"返回 {len(rows)} 字符")
        print("     首行片段:", rows[:220].replace("\n", " "))

    # ---- 5/6. 写入 + 格式往返 --------------------------------------------
    if args.read_only:
        print("\n[SKIP] --read-only：跳过写入与往返测试")
    else:
        page_id = page_id_of(args.page) if args.page else None
        if page_id:
            created, _ = mcp.call("notion-update-page", {
                "page_id": page_id, "command": "replace_content", "new_str": SAMPLE_CONTENT,
            })
            record("复用已有测试页并重写内容", "error" not in created.lower(), page_id)
        else:
            created, _ = mcp.call("notion-create-pages", {
                "creation_mode": "draft",
                "pages": [{"properties": {"title": SCRATCH_TITLE}, "content": SAMPLE_CONTENT}],
            })
            page_id = page_id_of(created)
            record("创建测试页（私有草稿）", bool(page_id), page_id or created[:120])

        if page_id:
            back, _ = mcp.call("notion-fetch", {"id": page_id})
            checks = {
                "正文段落": "Round-trip format test." in back,
                "二级标题 ##": "## Section heading" in back or "Section heading" in back,
                "多列 <columns>": "<columns>" in back,
                "<column> 数量 = 2": len(re.findall(r"<column[\s>]", back)) == 2,
                "列宽 ratio=\"50\"": back.count('ratio="50"') >= 2,
                "左列内容": "Left column" in back and "item two" in back,
                "右列引用块": "quoted text" in back,
                "右列分隔线": "---" in back,
                "表格 header-row": 'header-row="true"' in back,
                "表格单元格": "Header A" in back and "cell two" in back,
            }
            print("\n  --- 格式往返比对 ---")
            for k, v in checks.items():
                record(f"  {k}", v)
            print("\n  --- 读回的完整内容 ---")
            print("\n".join("      " + ln for ln in back.splitlines()))

            upd, _ = mcp.call("notion-update-page", {
                "page_id": page_id, "command": "update_content",
                "content_updates": [{"old_str": "Round-trip format test.",
                                     "new_str": "Round-trip format test. (updated)"}],
            })
            again, _ = mcp.call("notion-fetch", {"id": page_id})
            record("定点更新生效", "(updated)" in again)
            record("  更新后多列仍在", "<columns>" in again)

        print(f"\n注意：MCP 未暴露删页工具，请手动删除测试页《{SCRATCH_TITLE}》")
        print(f"      页面 ID: {page_id}")

    # ---- 汇总 ------------------------------------------------------------
    print("\n" + "=" * 68)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    if failed:
        print("未通过：")
        for n in failed:
            print("  -", n)
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
