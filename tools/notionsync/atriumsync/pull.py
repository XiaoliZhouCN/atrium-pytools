"""atriumsync.pull — Notion → 本地（md / csv + 格式 sidecar）。

映射规则（用户指定）
--------------------
``notion_home_pages/``  B 空间的 **1 级页面** → 一个页面一个 md，文件名按 ``slug_title``
``technology/``         Technology Gallery → ``technology_gallery.csv``，
                        每行页面的正文放 ``technology/pages/<名称>.md``，
                        csv 里用**相对工作根目录**的链接指向它，链接文本 ``🔗``
``ai_work_space/``      【每日资讯 整理要求.md】、``daily_news.csv``、``daily_news/``，
                        以及从整理要求提取的 ``ai_daily_tasks.md``（三个推送时间点任务）

Notion 是唯一真源：本模块只做「读 Notion → 写本地」，不会反向覆盖 Notion。
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
from typing import Any

from notionsync import service
from notionsync.client import NotionClient, plain_text
from notionsync.markdown import blocks_to_markdown

from . import formats as fmt
from .paths import (AI_WORK_SPACE, DAILY_COLUMNS, DAILY_NEWS_DB, DAILY_SPEC_PAGE,
                    FORMATS_DIRNAME, HOME_PAGES, HOME_PAGE_IDS, LINK_TEXT,
                    TECHNOLOGY, TECHNOLOGY_DB, TECH_COLUMNS, WORKSPACE,
                    markdown_link, rel_to_root, slug_title, unique_filename)


def _client(registry) -> NotionClient:
    return service.client_for(registry, WORKSPACE)


def _write_text(path: pathlib.Path, text: str) -> pathlib.Path:
    """UTF-8 **无 BOM**（AtriumNote 的既有约定），结尾补一个换行。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _formats_path(directory: pathlib.Path, stem: str) -> pathlib.Path:
    return directory / FORMATS_DIRNAME / f"{stem}.formats.json"


def _resolve_stem(directory: pathlib.Path, stem: str, page_id: str) -> str:
    """决定本次导出用哪个文件名主干——**保证重跑幂等**。

    先看这个 Notion 页面是不是已经导出过（sidecar 里记着 ``source.pageId``），
    是就复用原文件名；只有「不同页面撞同名」时才追加 ``-2``、``-3``。
    否则每次同步都会多出一份 ``xxx-2.md``。
    """
    sidecar_dir = directory / FORMATS_DIRNAME
    if sidecar_dir.is_dir():
        for candidate in sorted(sidecar_dir.glob("*.formats.json")):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (payload.get("source") or {}).get("pageId") == page_id:
                return candidate.name[: -len(".formats.json")]
    if not (directory / f"{stem}.md").exists():
        return stem
    return unique_filename(directory, stem).removesuffix(".md")


def export_page(client: NotionClient, page_id: str, directory: pathlib.Path, *,
                name: str | None = None, include_title: bool = True) -> dict:
    """导出一个页面：正文 md + 格式 sidecar。返回摘要。"""
    page = client.get_page(page_id)
    title = name or ""
    if not title:
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                title = plain_text(prop.get("title"))
                break
    title = title or page_id
    stem = _resolve_stem(directory, slug_title(title), page_id)
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / f"{stem}.md"

    body = blocks_to_markdown(client, client.list_children(page_id))
    text = f"# {title}\n\n{body}" if include_title else body
    _write_text(md_path, text)

    payload = {
        "source": {"system": "notion", "workspace": WORKSPACE, "kind": "page",
                   "pageId": page_id, "url": page.get("url"),
                   "lastEditedTime": page.get("last_edited_time"),
                   "parent": page.get("parent")},
        "localPath": rel_to_root(md_path),
        "title": title,
        "page": fmt.page_extras(page),
        "blocks": fmt.collect_block_formats(client, page_id),
    }
    fmt.write_formats(_formats_path(directory, md_path.stem), payload)
    return {"title": title, "md": md_path, "blocks": len(payload["blocks"])}


def pull_home_pages(registry) -> list[dict]:
    """B 空间的 1 级页面 → ``notion_home_pages/``。"""
    client = _client(registry)
    results: list[dict] = []
    for title, page_id in HOME_PAGE_IDS.items():
        results.append(export_page(client, page_id, HOME_PAGES, name=title))
    return results


def _row_cells(client: NotionClient, row: dict, columns: list[str]) -> dict[str, str]:
    """把一行 Notion 属性的可读文本取出来，供 csv 使用。"""
    properties = row.get("properties") or {}
    cells: dict[str, str] = {}
    for column in columns:
        prop = properties.get(column)
        if not prop:
            cells[column] = ""
            continue
        ptype = prop.get("type")
        if ptype in ("title", "rich_text"):
            cells[column] = plain_text(prop.get(ptype))
        elif ptype in ("select", "status"):
            cells[column] = ((prop.get(ptype) or {}).get("name") or "")
        elif ptype == "multi_select":
            cells[column] = ", ".join(o.get("name", "") for o in prop[ptype])
        elif ptype == "number":
            value = prop.get("number")
            cells[column] = "" if value is None else str(value)
        elif ptype == "checkbox":
            cells[column] = "是" if prop.get("checkbox") else "否"
        elif ptype == "date":
            cells[column] = ((prop.get("date") or {}).get("start") or "")
        elif ptype == "url":
            cells[column] = prop.get("url") or ""
        else:
            cells[column] = plain_text(prop.get(ptype) if isinstance(prop.get(ptype), list)
                                       else [])
    return cells


def export_database(registry, database_id: str, directory: pathlib.Path, *,
                    csv_name: str, columns: list[str],
                    pages_subdir: str = "pages") -> dict:
    """导出一个数据库：csv（含本地相对路径链接）+ 每行页面的 md + 格式 sidecar。"""
    client = _client(registry)
    directory.mkdir(parents=True, exist_ok=True)
    pages_dir = directory / pages_subdir
    rows = service.query_database(registry, WORKSPACE, database_id, limit=1000)
    if "error" in rows:
        raise RuntimeError(f"查询数据库失败：{rows['error']}")

    records = rows.get("results") or []
    csv_rows: list[dict[str, str]] = []
    exported: list[dict] = []

    for row in records:
        title = row.get("title") or row.get("id")
        cells = _row_cells(client, row, columns)
        pages_dir.mkdir(parents=True, exist_ok=True)
        stem = _resolve_stem(pages_dir, slug_title(title), row["id"])
        md_path = pages_dir / f"{stem}.md"

        body = blocks_to_markdown(client, client.list_children(row["id"]))
        _write_text(md_path, f"# {title}\n\n{body}")

        payload = {
            "source": {"system": "notion", "workspace": WORKSPACE, "kind": "database_row",
                       "databaseId": database_id, "pageId": row.get("id"),
                       "url": row.get("url"),
                       "lastEditedTime": row.get("last_edited_time")},
            "localPath": rel_to_root(md_path),
            "title": title,
            "properties": fmt.property_formats(row.get("properties")),
            "blocks": fmt.collect_block_formats(client, row["id"]),
        }
        fmt.write_formats(_formats_path(pages_dir, md_path.stem), payload)

        cells["本地路径"] = markdown_link(md_path, LINK_TEXT)
        csv_rows.append(cells)
        exported.append({"title": title, "md": md_path})

    csv_path = directory / csv_name
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for cells in csv_rows:
            writer.writerow({c: cells.get(c, "") for c in columns})

    return {"csv": csv_path, "rows": len(csv_rows), "pages": exported}


# ---------------------------------------------------------------------------
# ai_work_space
# ---------------------------------------------------------------------------
_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")


def _parse_tables(markdown: str) -> list[list[list[str]]]:
    """把 markdown 里的管道表解析成 ``[[[cell,…], …], …]``。"""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        match = _TABLE_ROW.match(line.strip())
        if match:
            cells = [c.strip() for c in match.group(1).split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
                continue
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def build_daily_tasks(spec_markdown: str) -> str:
    """从【每日资讯 整理要求】提取「三个时间点对应的任务」。

    取两张表：``一、推送主题与时间``（时间/主题/覆盖范围）与
    ``3.1 去哪里取`` 的 Topic 映射（Topic 取值 → 邮件 → 时间），按时间合并。
    """
    tables = _parse_tables(spec_markdown)

    schedule: list[list[str]] = []
    topic_map: list[list[str]] = []
    for table in tables:
        header = " ".join(table[0]) if table else ""
        if "推送主题" in header and "推送时间" in header:
            schedule = table[1:]
        elif "Topic" in header and "对应邮件" in header:
            topic_map = table[1:]

    by_time: dict[str, str] = {}
    for cells in topic_map:
        if len(cells) >= 3:
            by_time[cells[2]] = cells[0].strip("`")

    lines = [
        "# AI Daily Tasks",
        "",
        "> 来源：Notion 空间 B 的《Shirley‘s Knowledge Repo》→ AI Daily Tasks 段，",
        "> 任务定义取自《每日资讯 整理要求》第一章与 3.1 节。",
        "> 时区统一 Asia/Shanghai；搜索窗口为「昨天该时间点 → 今天该时间点」。",
        "",
    ]
    emitted = 0
    for cells in schedule:
        if len(cells) < 4:
            continue
        index, theme, when, coverage = (c.strip() for c in cells[:4])
        topic = by_time.get(when, "")
        emitted += 1
        lines += [
            f"## {when} — {theme}",
            "",
            f"- **序号**：{index}",
            f"- **覆盖范围**：{coverage}",
            f"- **数据源**：Technology Gallery 数据库，`Topic = \"{topic}\"`",
            "- **参考查询**：",
            "",
            "```sql",
            'SELECT "名称", "Category", "Kind", "Priority", "Sources", "Query Keys"',
            f'FROM "collection://{TECHNOLOGY_DB}"',
            f"WHERE \"Topic\" = '{topic}'",
            'ORDER BY "Priority";',
            "```",
            "",
        ]
    if not emitted:
        # 解析不出排期时必须显式报警：静默输出空任务列表等于让人以为"今天没有任务"
        lines.append("（未能从《每日资讯 整理要求》解析出推送排期，请检查该页是否被改写。）")
    return "\n".join(lines).rstrip() + "\n"


def pull_ai_work_space(registry) -> dict:
    """【每日资讯 整理要求】+ 每日资讯数据库 + AI Daily Tasks。"""
    client = _client(registry)
    out: dict[str, Any] = {}

    # 1) 整理要求（正文 + 格式 sidecar）
    spec = export_page(client, DAILY_SPEC_PAGE, AI_WORK_SPACE, name="每日资讯 整理要求")
    out["spec"] = spec

    # 2) 三个时间点任务（从整理要求提取）
    spec_md = spec["md"].read_text(encoding="utf-8")
    tasks_path = _write_text(AI_WORK_SPACE / "ai_daily_tasks.md", build_daily_tasks(spec_md))
    out["tasks"] = tasks_path

    # 3) daily_news.csv + 子页面目录
    out["daily"] = export_database(registry, DAILY_NEWS_DB, AI_WORK_SPACE,
                                   csv_name="daily_news.csv", columns=DAILY_COLUMNS,
                                   pages_subdir="daily_news")
    return out


def pull_all(registry, *, verbose: bool = True) -> dict:
    """执行全部导出。返回摘要字典。"""
    report: dict[str, Any] = {}

    report["home_pages"] = pull_home_pages(registry)
    if verbose:
        for item in report["home_pages"]:
            print(f"  [home]    {item['md'].name}  (格式项 {item['blocks']})")

    report["technology"] = export_database(
        registry, TECHNOLOGY_DB, TECHNOLOGY, csv_name="technology_gallery.csv",
        columns=TECH_COLUMNS, pages_subdir="pages")
    if verbose:
        print(f"  [tech]    {report['technology']['csv'].name}"
              f"  行数={report['technology']['rows']}")

    report["ai_work_space"] = pull_ai_work_space(registry)
    if verbose:
        daily = report["ai_work_space"]["daily"]
        print(f"  [ai]      {daily['csv'].name}  行数={daily['rows']}")
        print(f"  [ai]      {report['ai_work_space']['tasks'].name}")
    return report
