"""notionsync.client — Notion REST API 客户端（纯标准库）。

只依赖 ``urllib``，因此本机 Python、DSH 内置 MCP、Trae 都能直接复用同一份实现。

目标 API 版本为 ``2025-09-03``：该版本起 *database* 只是容器，真正的数据在
*data source* 上，查询必须走 ``/v1/data_sources/{id}/query``。旧版 ``2022-06-28``
在「一个 database 挂多个 data source」时会直接报错，故不采用。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

#: 默认 API 基址。可用 ``NOTIONSYNC_API_BASE`` 覆盖——测试时指向本地 mock 服务器，
#: 就能在不碰真实 Notion 的前提下把整条读写链路跑通。
DEFAULT_API_BASE = "https://api.notion.com"
ENV_API_BASE = "NOTIONSYNC_API_BASE"
USER_AGENT = "notionsync/0.1.0 (+https://www.notion.so)"


def resolve_api_base(override: str | None = None) -> str:
    return (override or os.environ.get(ENV_API_BASE) or DEFAULT_API_BASE).rstrip("/")

#: 需要自动重试的 HTTP 状态码
RETRY_STATUS = {429, 500, 502, 503, 504}

_ID_RE = re.compile(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


class NotionError(RuntimeError):
    """Notion API 返回的错误，或在重试耗尽后仍然失败。"""

    def __init__(self, message: str, *, status: int | None = None,
                 code: str | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body

    @property
    def unauthorized(self) -> bool:
        return self.status == 401 or self.code == "unauthorized"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def extract_id(value: str) -> str:
    """从 Notion URL 或裸 ID 中取出 32 位十六进制 ID（带连字符）。"""
    text = (value or "").strip()
    # Notion 链接可能是 slug-<32hex>?pvs=... 或 .../<32hex>
    match = _ID_RE.search(text.replace("_", "-"))
    if not match:
        raise ValueError(f"无法从 {value!r} 解析出 Notion ID")
    raw = match.group(1).replace("-", "").lower()
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def plain_text(rich: list[dict] | None) -> str:
    """把 rich_text / title 数组拼成纯文本。"""
    if not rich:
        return ""
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in rich)


def rich_text(content: str, *, link: str | None = None) -> list[dict]:
    """构造 rich_text 数组；内容为空时返回空数组（Notion 不接受空 text）。"""
    if not content:
        return []
    item: dict[str, Any] = {"type": "text", "text": {"content": content}}
    if link:
        item["text"]["link"] = {"url": link}
    return [item]


def title_property(name: str, content: str) -> dict:
    return {name: {"title": rich_text(content)}}


def find_title_property(properties: dict | None) -> str | None:
    """在 properties 字典里找到 title 型属性的名字。

    Notion 数据库的标题属性**未必叫 ``title``**——中文库里常见的是「名称」「Name」
    「标题」。写数据库行时必须用真实属性名，否则会直接被 API 拒掉。
    """
    for name, prop in (properties or {}).items():
        if isinstance(prop, dict) and prop.get("type") == "title":
            return name
    return None


def page_title(page: dict) -> str:
    """从 page 对象里取出标题文本。"""
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain_text(prop.get("title"))
    # data source 行、或 search 结果的简化形态
    for key in ("title", "name"):
        if isinstance(page.get(key), str):
            return page[key]
    return ""


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class NotionClient:
    """单个 Notion 工作空间的 REST 客户端。

    参数
    ----
    token:
        PAT 或内部连接 Token。两者都通过 ``Authorization: Bearer`` 认证。
    api_version:
        ``Notion-Version`` 头，默认 ``2025-09-03``。
    """

    def __init__(self, token: str, *, api_version: str = "2025-09-03",
                 timeout: float = 60.0, max_retries: int = 4,
                 key: str = "", label: str = "", api_base: str | None = None) -> None:
        if not token or not token.strip():
            raise ValueError("token 为空")
        self.token = token.strip()
        self.api_version = api_version
        self.timeout = timeout
        self.max_retries = max_retries
        self.key = key
        self.label = label
        self.api_base = resolve_api_base(api_base)
        self._self_info: dict | None = None

    # -- 底层请求 -----------------------------------------------------------
    def _request(self, method: str, path: str, *, body: dict | None = None,
                 params: dict | None = None) -> Any:
        url = self.api_base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.api_version,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", "replace")
                return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", "replace")
                detail: Any = None
                try:
                    detail = json.loads(raw)
                except json.JSONDecodeError:
                    detail = raw[:500]
                message = ""
                code = None
                if isinstance(detail, dict):
                    message = str(detail.get("message") or "")
                    code = detail.get("code")
                if exc.code in RETRY_STATUS and attempt < self.max_retries:
                    delay = self._retry_delay(exc, attempt)
                    time.sleep(delay)
                    last_error = NotionError(
                        f"HTTP {exc.code} {message}", status=exc.code, code=code, body=detail
                    )
                    continue
                raise NotionError(
                    f"HTTP {exc.code} {message or raw[:200]}",
                    status=exc.code, code=code, body=detail,
                ) from exc
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise NotionError(f"网络错误：{exc.reason}") from exc
        raise NotionError(f"重试耗尽：{last_error}")

    @staticmethod
    def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                return min(float(header), 30.0)
            except ValueError:
                pass
        return min(2 ** attempt, 8)

    # -- 身份 / 工作空间 ----------------------------------------------------
    def self_info(self, *, refresh: bool = False) -> dict:
        if self._self_info is None or refresh:
            self._self_info = self._request("GET", "/v1/users/me")
        return self._self_info

    @property
    def workspace_name(self) -> str:
        """Token 所属工作空间的名称（PAT 返回用户名，内部连接返回 bot 名称）。"""
        info = self.self_info()
        bot = info.get("bot") or {}
        return str(bot.get("workspace_name") or info.get("name") or "")

    @property
    def workspace_owner(self) -> str:
        info = self.self_info()
        owner = (info.get("bot") or {}).get("owner") or {}
        if owner.get("type") == "user":
            return str(owner.get("user", {}).get("name") or "")
        return str((info.get("bot") or {}).get("owner", {}).get("type") or "")

    # -- 搜索 ---------------------------------------------------------------
    def search(self, query: str = "", *, object_type: str | None = None,
               page_size: int = 100, start_cursor: str | None = None,
               sort_last_edited: bool = False) -> dict:
        """``object_type`` 取 ``page`` / ``data_source``；留空表示都搜。"""
        body: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
        if query:
            body["query"] = query
        if object_type:
            body["filter"] = {"value": object_type, "property": "object"}
        if start_cursor:
            body["start_cursor"] = start_cursor
        if sort_last_edited:
            body["sort"] = {"direction": "descending", "timestamp": "last_edited_time"}
        return self._request("POST", "/v1/search", body=body)

    def search_all(self, query: str = "", *, object_type: str | None = None,
                   limit: int = 200) -> list[dict]:
        results: list[dict] = []
        cursor: str | None = None
        while len(results) < limit:
            page = self.search(query, object_type=object_type, page_size=100,
                               start_cursor=cursor)
            results.extend(page.get("results") or [])
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return results[:limit]

    # -- 页面 ---------------------------------------------------------------
    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/v1/pages/{extract_id(page_id)}")

    def create_page(self, parent_id: str | None, title: str, *, parent_type: str = "page",
                    properties: dict | None = None, children: list[dict] | None = None,
                    icon: str | None = None,
                    title_property_name: str | None = None) -> dict:
        """新建页面。

        ``parent_type``：
          * ``page``        —— 在某个页面下新建子页，标题写入 ``title`` 属性
          * ``data_source`` —— 在数据库（data source）里新建一行
          * ``workspace``   —— 建一个工作空间级页面（PAT 会落在「私人」区），无需 parent_id

        写数据库行时标题属性名不一定是 ``title``（中文库常叫「名称」）。除非显式传入
        ``title_property_name``，否则会读取该 data source 的 schema 自动取真实属性名。
        """
        resolved: str | None = None
        if parent_type == "workspace":
            parent: dict[str, Any] = {"type": "workspace", "workspace": True}
        else:
            if not parent_id:
                raise ValueError(f"parent_type={parent_type} 时必须提供 parent_id")
            resolved = extract_id(parent_id)
            parent = (
                {"type": "page_id", "page_id": resolved}
                if parent_type == "page"
                else {"type": "data_source_id", "data_source_id": resolved}
            )

        if properties is None:
            property_name = title_property_name
            if property_name is None and parent_type == "data_source" and resolved:
                source = self.get_data_source(resolved)
                property_name = find_title_property(source.get("properties"))
            properties = title_property(property_name or "title", title)
        payload: dict[str, Any] = {"parent": parent, "properties": properties}
        if children:
            payload["children"] = children
        if icon:
            payload["icon"] = {"type": "emoji", "emoji": icon}
        return self._request("POST", "/v1/pages", body=payload)

    @property
    def _trash_field(self) -> str:
        """回收站字段名随 API 版本变化：2026-03-11 起 ``archived`` → ``in_trash``。"""
        return "in_trash" if self.api_version >= "2026-03-11" else "archived"

    def update_page(self, page_id: str, *, properties: dict | None = None,
                    icon: str | None = None, in_trash: bool | None = None) -> dict:
        body: dict[str, Any] = {}
        if properties:
            body["properties"] = properties
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        if in_trash is not None:
            body[self._trash_field] = in_trash
        if not body:
            raise ValueError("update_page 没有任何要修改的字段")
        return self._request("PATCH", f"/v1/pages/{extract_id(page_id)}", body=body)

    def set_page_title(self, page_id: str, title: str) -> dict:
        page = self.get_page(page_id)
        prop_name = None
        for name, prop in (page.get("properties") or {}).items():
            if prop.get("type") == "title":
                prop_name = name
                break
        if prop_name is None:
            raise NotionError("该页面没有可写的 title 属性（可能是数据库行，请用 properties 直接更新）")
        return self.update_page(page_id, properties=title_property(prop_name, title))

    # -- 块（页面正文） -----------------------------------------------------
    def get_block(self, block_id: str) -> dict:
        return self._request("GET", f"/v1/blocks/{extract_id(block_id)}")

    def iter_children(self, block_id: str, *, page_size: int = 100) -> Iterator[dict]:
        """迭代某个块（通常是页面）的直接子块。"""
        cursor: str | None = None
        while True:
            page = self._request(
                "GET",
                f"/v1/blocks/{extract_id(block_id)}/children",
                params={"page_size": min(max(page_size, 1), 100), "start_cursor": cursor},
            )
            for block in page.get("results") or []:
                yield block
            if not page.get("has_more"):
                return
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def list_children(self, block_id: str, *, page_size: int = 100) -> list[dict]:
        return list(self.iter_children(block_id, page_size=page_size))

    def append_children(self, block_id: str, children: list[dict]) -> list[dict]:
        """向某块追加子块。Notion 单次上限 100 个，超出自动分批。"""
        created: list[dict] = []
        for start in range(0, len(children), 100):
            chunk = children[start:start + 100]
            result = self._request(
                "PATCH",
                f"/v1/blocks/{extract_id(block_id)}/children",
                body={"children": chunk},
            )
            created.extend(result.get("results") or [])
        return created

    def delete_block(self, block_id: str) -> dict:
        return self._request("DELETE", f"/v1/blocks/{extract_id(block_id)}")

    def clear_page_content(self, page_id: str) -> int:
        """删除页面的全部直接子块，返回删除数量。"""
        removed = 0
        for block in self.list_children(page_id):
            self.delete_block(block["id"])
            removed += 1
            time.sleep(0.05)
        return removed

    # -- 数据库 / data source ----------------------------------------------
    def get_database(self, database_id: str) -> dict:
        """取 database 容器对象，含 ``data_sources`` 数组。"""
        return self._request("GET", f"/v1/databases/{extract_id(database_id)}")

    def create_database(self, parent_page_id: str, title: str, properties: dict,
                        *, icon: str | None = None) -> dict:
        """在某个页面下新建数据库。

        ``2025-09-03`` 起 database 只是容器，属性 schema 必须挂在
        ``initial_data_source`` 下，否则会被 API 拒绝。
        """
        body: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": extract_id(parent_page_id)},
            "title": rich_text(title),
            "initial_data_source": {"properties": properties},
        }
        if icon:
            body["icon"] = {"type": "emoji", "emoji": icon}
        return self._request("POST", "/v1/databases", body=body)

    def resolve_data_source_ids(self, database_or_source_id: str) -> list[str]:
        """把 database 容器 ID 解析成一个或多个 data source ID。

        如果传入的本身就是 data source ID，会原样返回。
        """
        identifier = extract_id(database_or_source_id)
        try:
            database = self.get_database(identifier)
        except NotionError as exc:
            if exc.status == 404:
                return [identifier]
            raise
        sources = [item.get("id") for item in (database.get("data_sources") or [])]
        return [s for s in sources if s] or [identifier]

    def get_data_source(self, data_source_id: str) -> dict:
        return self._request("GET", f"/v1/data_sources/{extract_id(data_source_id)}")

    def query_data_source(self, data_source_id: str, *, filter: dict | None = None,
                          sorts: list[dict] | None = None, page_size: int = 100,
                          start_cursor: str | None = None) -> dict:
        body: dict[str, Any] = {"page_size": min(max(page_size, 1), 100)}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        if start_cursor:
            body["start_cursor"] = start_cursor
        return self._request(
            "POST", f"/v1/data_sources/{extract_id(data_source_id)}/query", body=body
        )

    def query_all(self, data_source_id: str, *, filter: dict | None = None,
                  sorts: list[dict] | None = None, limit: int = 500) -> list[dict]:
        rows: list[dict] = []
        cursor: str | None = None
        while len(rows) < limit:
            page = self.query_data_source(data_source_id, filter=filter, sorts=sorts,
                                          page_size=100, start_cursor=cursor)
            rows.extend(page.get("results") or [])
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor:
                break
        return rows[:limit]

    # -- 评论 ---------------------------------------------------------------
    def list_comments(self, block_id: str, *, page_size: int = 100) -> list[dict]:
        result = self._request(
            "GET", "/v1/comments",
            params={"block_id": extract_id(block_id), "page_size": page_size},
        )
        return result.get("results") or []

    def create_comment(self, page_id: str, content: str) -> dict:
        return self._request("POST", "/v1/comments", body={
            "parent": {"page_id": extract_id(page_id)},
            "rich_text": rich_text(content),
        })
