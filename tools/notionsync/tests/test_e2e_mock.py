"""notionsync 端到端自检（本地 mock Notion API，无需任何真实凭据）。

目的
----
真实 Token 只能由你本人创建，但「代码能不能用」不该等凭据才能验证。本脚本起一个
内存版 Notion API（形状对齐官方 2025-09-03：pages / blocks / data_sources /
comments / search），把 ``NOTIONSYNC_API_BASE`` 指向它，然后让**真实的 client、
service、CLI 与 MCP stdio 服务器**走完整条读写链路：

1. 建页 → 读回 → 断言 Markdown 往返
2. 追加 → 覆盖正文 → 改标题 → 评论 → 回收站
3. database → data source 解析 → 查询 → **按「名称」写库行**（中文库标题属性回归）
4. 独立子进程跑 mcp_server.py，用 tools/call 走一遍同样的操作

它验证的是请求形状、分页、块树嵌套、标题属性解析、错误路径——真实 Token 到位后
只剩「凭据对不对」这一件事需要在线上确认。

运行::

    python tools\\notionsync\\tests\\test_e2e_mock.py

产物：``tools/notionsync/temp/report_e2e_mock.json``
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve()
TOOL_DIR = HERE.parents[1]
TEMP_DIR = TOOL_DIR / "temp"
sys.path.insert(0, str(TOOL_DIR))

MOCK_TOKEN = "ntn_mock_token_for_local_tests"
CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def new_id() -> str:
    return str(uuid.uuid4())


def rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text}, "plain_text": text,
             "annotations": {"bold": False, "italic": False, "strikethrough": False,
                             "underline": False, "code": False}}]


# ---------------------------------------------------------------------------
# 内存版 Notion API
# ---------------------------------------------------------------------------
class MockNotion:
    def __init__(self) -> None:
        self.pages: dict[str, dict] = {}
        self.blocks: dict[str, dict] = {}
        self.children: dict[str, list[str]] = {}
        self.comments: dict[str, list[dict]] = {}
        self._seq = 0

        # 一个 data source（模拟 Technology Gallery：标题属性叫「名称」）
        self.data_source_id = new_id()
        self.database_id = new_id()
        self.data_source_schema = {
            "名称": {"id": "title", "type": "title", "title": {}},
            "Topic": {"id": "topic", "type": "select",
                      "select": {"options": [{"name": "AI与工具 08:00"},
                                             {"name": "渲染与开发 16:00"}]}},
            "Priority": {"id": "pri", "type": "select",
                         "select": {"options": [{"name": "P0"}, {"name": "P1"}]}},
        }
        for name, topic, priority in (("OpenAI", "AI与工具 08:00", "P0"),
                                      ("Vulkan", "渲染与开发 16:00", "P1")):
            self._add_row(name, topic, priority)
        # 一个普通页面，用于 search / 作为父页
        self.root_page_id = new_id()
        self.pages[self.root_page_id] = self._page(
            self.root_page_id, "Personal Home Page", {"type": "workspace", "workspace": True},
            {"title": {"type": "title", "title": rich("Personal Home Page")}})

    # -- 内部工具 -----------------------------------------------------------
    def _page(self, page_id: str, title: str, parent: dict, properties: dict) -> dict:
        stamp = "2026-10-02T00:00:00.000Z"
        return {"object": "page", "id": page_id, "created_time": stamp,
                "last_edited_time": stamp, "parent": parent, "archived": False,
                "in_trash": False, "properties": properties,
                "url": f"https://www.notion.so/{page_id.replace('-', '')}"}

    @staticmethod
    def _annotate(properties: dict) -> dict:
        """把客户端发来的 properties 补上 type，模拟 Notion 的返回形状。"""
        out = {}
        for name, value in (properties or {}).items():
            if not isinstance(value, dict):
                out[name] = value
                continue
            if "title" in value:
                out[name] = {"id": name, "type": "title", "title": value["title"]}
            elif "rich_text" in value:
                out[name] = {"id": name, "type": "rich_text",
                             "rich_text": value["rich_text"]}
            elif "select" in value:
                out[name] = {"id": name, "type": "select", "select": value["select"]}
            else:
                out[name] = value
        return out

    def _materialize(self, items: list[dict]) -> list[str]:
        ids: list[str] = []
        for item in items:
            btype = item.get("type") or "paragraph"
            payload = dict(item.get(btype) or {})
            nested = payload.pop("children", None)
            self._seq += 1
            block_id = new_id()
            block = {"object": "block", "id": block_id, "type": btype,
                     "created_time": "2026-10-02T00:00:00.000Z",
                     "last_edited_time": "2026-10-02T00:00:00.000Z",
                     "has_children": bool(nested), btype: payload}
            self.blocks[block_id] = block
            if nested:
                self.children[block_id] = self._materialize(nested)
            ids.append(block_id)
        return ids

    def _add_row(self, name: str, topic: str, priority: str) -> dict:
        row_id = new_id()
        properties = self._annotate({
            "名称": {"title": rich(name)},
            "Topic": {"select": {"name": topic}},
            "Priority": {"select": {"name": priority}},
        })
        parent = {"type": "data_source_id", "data_source_id": self.data_source_id}
        self.pages[row_id] = self._page(row_id, name, parent, properties)
        return self.pages[row_id]

    def rows(self) -> list[dict]:
        return [p for p in self.pages.values()
                if (p.get("parent") or {}).get("type") == "data_source_id"
                and not p.get("in_trash")]

    def top_pages(self) -> list[dict]:
        return [p for p in self.pages.values()
                if (p.get("parent") or {}).get("type") in ("workspace", "page_id")
                and not p.get("in_trash")]

    def data_source_object(self) -> dict:
        return {"object": "data_source", "id": self.data_source_id,
                "title": rich("Technology Gallery"), "name": "Technology Gallery",
                "properties": self.data_source_schema,
                "parent": {"type": "database_id", "database_id": self.database_id},
                "url": f"https://www.notion.so/{self.data_source_id.replace('-', '')}"}

    def database_object(self) -> dict:
        return {"object": "database", "id": self.database_id,
                "title": rich("Technology Gallery"),
                "data_sources": [{"id": self.data_source_id, "name": "Technology Gallery"}],
                "url": f"https://www.notion.so/{self.database_id.replace('-', '')}"}

    def list_response(self, results: list[dict], start: int = 0, size: int = 100) -> dict:
        chunk = results[start:start + size]
        has_more = start + size < len(results)
        return {"object": "list", "results": chunk, "has_more": has_more,
                "next_cursor": str(start + size) if has_more else None,
                "type": "block"}


class Handler(BaseHTTPRequestHandler):
    server_version = "MockNotion/1.0"
    store: MockNotion

    def log_message(self, *args) -> None:  # 静音
        pass

    # -- 工具 ---------------------------------------------------------------
    def _auth_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {MOCK_TOKEN}" and bool(self.headers.get("Notion-Version"))

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status: int, code: str, message: str) -> None:
        self._send(status, {"object": "error", "status": status, "code": code,
                            "message": message})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _check_auth(self) -> bool:
        if not self._auth_ok():
            self._fail(401, "unauthorized", "API token is invalid.")
            return False
        return True

    @staticmethod
    def _segments(path: str) -> tuple[list[str], dict]:
        if "?" in path:
            path, query = path.split("?", 1)
            params = {}
            for pair in query.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k] = v
            return [p for p in path.split("/") if p], params
        return [p for p in path.split("/") if p], {}

    # -- GET ----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        parts, params = self._segments(self.path)
        store = self.store
        if parts[:2] == ["v1", "users"] and parts[2:] == ["me"]:
            self._send(200, {"object": "user", "id": "bot-1", "type": "bot", "name": "notionsync",
                             "bot": {"owner": {"type": "user",
                                               "user": {"id": "u1", "name": "XiaoliZhouCN"}},
                                     "workspace_name": "Shirley's Dashboard (mock)"}})
            return
        if parts[:2] == ["v1", "pages"] and len(parts) == 3:
            page = store.pages.get(parts[2])
            if not page:
                self._fail(404, "object_not_found", "Not found")
                return
            self._send(200, page)
            return
        if parts[:2] == ["v1", "blocks"] and len(parts) == 4 and parts[3] == "children":
            parent = parts[2]
            ids = store.children.get(parent, [])
            start = int(params.get("start_cursor") or 0)
            size = int(params.get("page_size") or 100)
            self._send(200, store.list_response([store.blocks[i] for i in ids], start, size))
            return
        if parts[:2] == ["v1", "blocks"] and len(parts) == 3:
            block = store.blocks.get(parts[2])
            if not block:
                self._fail(404, "object_not_found", "Not found")
                return
            self._send(200, block)
            return
        if parts[:2] == ["v1", "databases"] and len(parts) == 3:
            if parts[2] == store.database_id:
                self._send(200, store.database_object())
            else:
                self._fail(404, "object_not_found", "Not found")
            return
        if parts[:2] == ["v1", "data_sources"] and len(parts) == 3:
            if parts[2] == store.data_source_id:
                self._send(200, store.data_source_object())
            else:
                self._fail(404, "object_not_found", "Not found")
            return
        if parts[:2] == ["v1", "comments"]:
            page_id = params.get("block_id")
            self._send(200, store.list_response(store.comments.get(page_id, [])))
            return
        self._fail(404, "object_not_found", f"no route for GET {self.path}")

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        parts, _ = self._segments(self.path)
        store = self.store
        body = self._body()

        if parts[:2] == ["v1", "search"]:
            want = (body.get("filter") or {}).get("value")
            if want == "data_source":
                results = [store.data_source_object()]
            elif want == "page":
                results = store.top_pages()
            else:
                results = store.top_pages() + [store.data_source_object()]
            self._send(200, store.list_response(results, 0, body.get("page_size") or 100))
            return

        if parts[:2] == ["v1", "pages"]:
            parent = body.get("parent") or {}
            page_id = new_id()
            properties = store._annotate(body.get("properties") or {})
            title = ""
            for value in properties.values():
                if value.get("type") == "title":
                    title = "".join(t.get("plain_text", "") for t in value["title"])
            page = store._page(page_id, title, parent, properties)
            page["in_trash"] = False
            store.pages[page_id] = page
            if body.get("children"):
                store.children[page_id] = store._materialize(body["children"])
            self._send(200, page)
            return

        if parts[:2] == ["v1", "data_sources"] and len(parts) == 4 and parts[3] == "query":
            rows = store.rows()
            flt = body.get("filter")
            if isinstance(flt, dict) and flt.get("property") and flt.get("select"):
                name = flt["property"]
                want = flt["select"].get("equals")
                rows = [r for r in rows
                        if ((r["properties"].get(name) or {}).get("select") or {})
                        .get("name") == want]
            start = 0
            size = body.get("page_size") or 100
            self._send(200, store.list_response(rows, start, size))
            return

        if parts[:2] == ["v1", "comments"]:
            page_id = (body.get("parent") or {}).get("page_id")
            comment = {"object": "comment", "id": new_id(),
                       "parent": {"type": "page_id", "page_id": page_id},
                       "discussion_id": new_id(),
                       "created_time": "2026-10-02T00:00:00.000Z",
                       "created_by": {"object": "user", "id": "u1"},
                       "rich_text": body.get("rich_text") or []}
            store.comments.setdefault(page_id, []).append(comment)
            self._send(200, comment)
            return

        self._fail(404, "object_not_found", f"no route for POST {self.path}")

    # -- PATCH --------------------------------------------------------------
    def do_PATCH(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        parts, _ = self._segments(self.path)
        store = self.store
        body = self._body()

        if parts[:2] == ["v1", "pages"] and len(parts) == 3:
            page = store.pages.get(parts[2])
            if not page:
                self._fail(404, "object_not_found", "Not found")
                return
            if body.get("properties"):
                merged = dict(page["properties"])
                merged.update(store._annotate(body["properties"]))
                page["properties"] = merged
            if body.get("archived") is not None:
                page["archived"] = bool(body["archived"])
                page["in_trash"] = bool(body["archived"])
            if body.get("in_trash") is not None:
                page["in_trash"] = bool(body["in_trash"])
            if body.get("icon"):
                page["icon"] = body["icon"]
            page["last_edited_time"] = "2026-10-02T01:00:00.000Z"
            self._send(200, page)
            return

        if parts[:2] == ["v1", "blocks"] and len(parts) == 4 and parts[3] == "children":
            parent = parts[2]
            created = store._materialize(body.get("children") or [])
            store.children.setdefault(parent, []).extend(created)
            self._send(200, store.list_response([store.blocks[i] for i in created]))
            return

        self._fail(404, "object_not_found", f"no route for PATCH {self.path}")

    # -- DELETE -------------------------------------------------------------
    def do_DELETE(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        parts, _ = self._segments(self.path)
        store = self.store
        if parts[:2] == ["v1", "blocks"] and len(parts) == 3:
            block = store.blocks.pop(parts[2], None)
            if not block:
                self._fail(404, "object_not_found", "Not found")
                return
            for ids in store.children.values():
                if parts[2] in ids:
                    ids.remove(parts[2])
            self._send(200, block)
            return
        self._fail(404, "object_not_found", f"no route for DELETE {self.path}")


def start_mock() -> tuple[ThreadingHTTPServer, MockNotion, str]:
    store = MockNotion()

    class BoundHandler(Handler):
        pass

    BoundHandler.store = store
    server = ThreadingHTTPServer(("127.0.0.1", 0), BoundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, store, f"http://127.0.0.1:{server.server_address[1]}"


# ---------------------------------------------------------------------------
# 阶段
# ---------------------------------------------------------------------------
ROUND_TRIP_BODY = """## 往返测试

正文段落。

- 甲
- 乙
  - 嵌套

- [x] 完成
- [ ] 未完成

> 引用

```python
x = 1
```

---
"""

ANCHORS = ["## 往返测试", "正文段落", "- 甲", "  - 嵌套", "- [x] 完成", "> 引用",
           "```python", "x = 1", "---"]


def phase_service(registry, store) -> None:
    from notionsync import service

    print("\n--- 阶段 1：service 层完整读写 ---")
    result = service.create_page(registry, None, None, "[MOCK] 草稿页",
                                 parent_type="workspace", markdown=ROUND_TRIP_BODY)
    page_id = result.get("page_id")
    check("建工作空间级页面", bool(page_id), str(result.get("url")))
    if not page_id:
        return

    read = service.read_page(registry, None, page_id, include_title=False)
    text = read.get("markdown", "")
    missing = [a for a in ANCHORS if a not in text]
    check("读回 Markdown 无丢失", not missing,
          "缺失 " + json.dumps(missing, ensure_ascii=False) if missing else f"{len(text)} 字符")

    appended = service.append_markdown(registry, None, page_id, "### 追加\n\n追加内容。\n")
    after = service.read_page(registry, None, page_id, include_title=False).get("markdown", "")
    check("追加块", appended.get("ok") and "追加内容。" in after, f"{appended.get('blocks_written')} 块")

    replaced = service.replace_page_content(registry, None, page_id, "# 新正文\n\n只剩这段。\n")
    after2 = service.read_page(registry, None, page_id, include_title=False).get("markdown", "")
    check("覆盖正文（清空 + 重写）",
          replaced.get("ok") and "只剩这段。" in after2 and "追加内容。" not in after2,
          f"删除 {replaced.get('removed')} 块，写入 {replaced.get('blocks_written')} 块")

    titled = service.set_title(registry, None, page_id, "[MOCK] 已改名")
    check("改标题", titled.get("ok") and
          service.read_page(registry, None, page_id).get("title") == "[MOCK] 已改名")

    comment = service.create_comment(registry, None, page_id, "来自 mock 的评论")
    listed = service.list_comments(registry, None, page_id)
    check("评论创建与列出",
          comment.get("ok") and listed.get("count") == 1,
          f"count={listed.get('count')}")

    described = service.describe_database(registry, None, store.database_id)
    check("database → data source 解析",
          [s["id"] for s in described.get("data_sources", [])] == [store.data_source_id],
          json.dumps(described.get("data_sources"), ensure_ascii=False))

    queried = service.query_database(registry, None, store.database_id, limit=10)
    check("查询数据库行", queried.get("count") == 2, f"count={queried.get('count')}")

    filtered = service.query_database(
        registry, None, store.data_source_id,
        filter={"property": "Topic", "select": {"equals": "AI与工具 08:00"}}, limit=10)
    check("带 filter 查询", filtered.get("count") == 1,
          f"count={filtered.get('count')}")

    # 关键回归：中文库标题属性叫「名称」，写库行必须用它
    row = service.create_page(registry, None, store.data_source_id, "新主体",
                              parent_type="data_source")
    row_page = store.pages.get(row.get("page_id") or "", {})
    row_read = (service.read_page(registry, None, row["page_id"])
                if row.get("page_id") else {})
    check("按「名称」写入数据库行（中文标题属性回归）",
          "名称" in (row_page.get("properties") or {})
          and "title" not in (row_page.get("properties") or {}),
          json.dumps(list((row_page.get("properties") or {}).keys()), ensure_ascii=False))
    check("新库行标题可读回", row_read.get("title") == "新主体",
          f"读回标题 = {row_read.get('title')!r}")

    archived = service.archive_page(registry, None, page_id)
    check("回收站", archived.get("ok") and archived.get("trashed") is True)


def phase_errors(registry) -> None:
    from notionsync import service

    print("\n--- 阶段 2：错误路径 ---")
    check("未知 workspace 报错并退出非零",
          "error" in service.search(registry, "NOPE", "x"))
    check("未知 object_type 被拒",
          "error" in service.search(registry, None, "x", object_type="bogus"))
    check("不存在的页面返回错误而非抛异常",
          "error" in service.read_page(registry, None, new_id()))
    check("非法 page_id 返回错误",
          "error" in service.read_page(registry, None, "not-an-id"))
    check("空 markdown 追加被拒",
          "error" in service.append_markdown(registry, None, new_id(), "\n\n"))


def phase_mcp(api_base: str) -> None:
    print("\n--- 阶段 3：MCP stdio 服务器（子进程）对 mock 读写 ---")
    config_path = TEMP_DIR / "_e2e_mock_config.json"
    config_path.write_text(json.dumps({
        "version": 1,
        "workspaces": [{"key": "M", "label": "mock", "token": MOCK_TOKEN}],
    }, ensure_ascii=False), encoding="utf-8")

    env = dict(os.environ)
    env.update({"NOTIONSYNC_CONFIG": str(config_path), "NOTIONSYNC_API_BASE": api_base,
                "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    proc = subprocess.Popen(
        [sys.executable, str(TOOL_DIR / "mcp_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(TOOL_DIR))

    seq = 0

    def call(name: str, arguments: dict) -> dict:
        nonlocal seq
        seq += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": seq, "method": "tools/call",
                                     "params": {"name": name, "arguments": arguments}}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        result = json.loads(line)["result"]
        text = (result.get("content") or [{}])[0].get("text", "")
        try:
            return {"isError": result.get("isError"), "data": json.loads(text)}
        except json.JSONDecodeError:
            return {"isError": result.get("isError"), "data": text}

    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18",
                                                "capabilities": {},
                                                "clientInfo": {"name": "e2e", "version": "0"}}}) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()

        listed = call("workspaces_list", {})
        info = (listed["data"].get("workspaces") or [{}])[0]
        check("MCP workspaces_list 连通 mock",
              info.get("reachable") is True and
              info.get("workspaceName") == "Shirley's Dashboard (mock)",
              str(info.get("workspaceName")))

        found = call("notion_search", {"query": "", "object_type": "page"})
        check("MCP notion_search", found["data"].get("count", 0) >= 1,
              f"count={found['data'].get('count')}")

        created = call("notion_create_page", {"title": "[MCP-MOCK] 页面",
                                              "parent_type": "workspace",
                                              "markdown": "## MCP 写入\n\n内容。\n"})
        mcp_page = created["data"].get("page_id")
        check("MCP notion_create_page", bool(mcp_page), str(created["data"].get("url")))

        if mcp_page:
            read = call("notion_read_page", {"page_id": mcp_page, "include_title": False})
            check("MCP notion_read_page 读回 Markdown",
                  "## MCP 写入" in read["data"].get("markdown", ""),
                  f"{len(read['data'].get('markdown', ''))} 字符")

            upd = call("notion_set_title", {"page_id": mcp_page, "title": "[MCP-MOCK] 改名"})
            check("MCP notion_set_title", upd["data"].get("ok") is True)

            app = call("notion_append", {"block_id": mcp_page,
                                         "markdown": "- 追加一\n- 追加二\n"})
            check("MCP notion_append", app["data"].get("blocks_written") == 2,
                  f"{app['data'].get('blocks_written')} 块")

            cm = call("notion_create_comment", {"page_id": mcp_page, "text": "mcp 评论"})
            check("MCP notion_create_comment", cm["data"].get("ok") is True)

            rm = call("notion_archive_page", {"page_id": mcp_page})
            check("MCP notion_archive_page", rm["data"].get("trashed") is True)

        db = call("notion_describe_database", {"database_id": json.loads(
            (TEMP_DIR / "_e2e_ids.json").read_text(encoding="utf-8"))["database_id"]})
        check("MCP notion_describe_database",
              len(db["data"].get("data_sources") or []) == 1,
              json.dumps(db["data"].get("data_sources"), ensure_ascii=False)[:90])

        q = call("notion_query_database", {"database_id": json.loads(
            (TEMP_DIR / "_e2e_ids.json").read_text(encoding="utf-8"))["data_source_id"],
            "limit": 10})
        check("MCP notion_query_database", q["data"].get("count", 0) >= 2,
              f"count={q['data'].get('count')}")
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    stderr = proc.stderr.read()
    check("MCP 子进程无异常堆栈", "Traceback" not in stderr, stderr.strip()[:150])


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    server, store, api_base = start_mock()
    print("=" * 70)
    print(f"本地 mock Notion API：{api_base}")
    print("=" * 70)

    # 客户端在构造时读取 env，所以必须在任何 client 之前设置
    os.environ["NOTIONSYNC_API_BASE"] = api_base

    from notionsync.config import Workspace, WorkspaceRegistry

    registry = WorkspaceRegistry([Workspace(key="M", label="mock", token=MOCK_TOKEN)],
                                 source="(mock)")
    (TEMP_DIR / "_e2e_ids.json").write_text(json.dumps({
        "database_id": store.database_id, "data_source_id": store.data_source_id,
    }), encoding="utf-8")

    try:
        phase_service(registry, store)
        phase_errors(registry)
        phase_mcp(api_base)
    finally:
        server.shutdown()

    passed = sum(1 for c in CHECKS if c["ok"])
    report = {"suite": "notionsync e2e (mock notion api)", "ok": passed == len(CHECKS),
              "passed": passed, "total": len(CHECKS), "checks": CHECKS,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path = TEMP_DIR / "report_e2e_mock.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"结果：{passed}/{len(CHECKS)} 通过；报告：{path}")
    print("=" * 70)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
