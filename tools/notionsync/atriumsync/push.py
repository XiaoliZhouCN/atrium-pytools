"""atriumsync.push — 本地 Markdown → 飞书文档（按 Notion 的布局）。

策略：**区间替换，而不是整篇 overwrite**。
飞书 ``docs +update --command overwrite`` 会清空全文并丢掉图片与评论；所以这里先
``docs +fetch --detail with-ids`` 拿到顶层块 id，再用
``block_replace --start-block-id <首个> --end-block-id <末个>`` 只替换正文区间；
文档正文为空时退回 ``append``。

每次推送都会把「实际发出的 DocxXML」与降级说明写进 sidecar，供后续排版优化对照。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import time
from typing import Any

from larksync import LarkCliError, LarkClient

from . import formats as fmt
from .feishu_xml import markdown_to_feishu_xml

_TAG = re.compile(r"<(/?)([a-zA-Z0-9_-]+)([^>]*?)(/?)>")


def body_block_ids(xml: str) -> list[str]:
    """按文档顺序取出**正文里所有块的 id**（``<title>`` 不算）。

    关键点：飞书把连续列表项表达成 ``<ul><li id="…">``——``<ul>`` 只是分组、**没有 id**，
    而每个 ``<li>`` 才是真正的块。所以不能用「只看顶层元素」的写法，否则区间端点会漏掉
    中间那些块，``block_replace`` 会报
    ``Invalid block range: an intermediate sibling has no block ID``。
    """
    ids: list[str] = []
    for match in _TAG.finditer(xml or ""):
        closing, name, attrs = match.group(1), match.group(2), match.group(3)
        if closing or name == "title":
            continue
        found = re.search(r'id="([^"]+)"', attrs)
        if found:
            ids.append(found.group(1))
    return ids


def _title_text(xml: str) -> str:
    match = re.search(r"<title\b[^>]*>(.*?)</title>", xml or "", re.S)
    return match.group(1) if match else ""


def top_level_ids(xml: str) -> list[str]:      # 兼容旧名
    return body_block_ids(xml)


def split_title(xml: str) -> tuple[str, str]:
    """把 ``<title>…</title>`` 与正文拆开。"""
    match = re.match(r"\s*<title\b[^>]*>.*?</title>", xml or "", re.S)
    if not match:
        return "", xml or ""
    return match.group(0), xml[match.end():]


def build_xml(md_path: pathlib.Path, *, title: str | None = None) -> tuple[str, str, list[str]]:
    """读本地 md → (title 片段, 正文 XML, degraded 列表)。"""
    markdown = md_path.read_text(encoding="utf-8")
    xml, degraded = markdown_to_feishu_xml(markdown, title=title)
    title_xml, body = split_title(xml)
    return title_xml, body, degraded


def push_markdown(md_path: pathlib.Path, *, feishu_url: str, client: LarkClient,
                  sidecar: pathlib.Path | None = None,
                  title: str | None = None, dry_run: bool = False) -> dict:
    """把一篇本地 md 同步到飞书文档。返回结果摘要（不改动传回的 sidecar 里的 source 段）。"""
    title_xml, body, degraded = build_xml(md_path, title=title)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    current = client.docs_fetch(feishu_url, doc_format="xml", detail="with-ids",
                                scope="full")
    document = ((current.get("data") or {}).get("document") or {})
    existing = document.get("content") or ""
    revision = document.get("revision_id")
    ids = top_level_ids(existing)
    existing_title, _ = split_title(existing)

    result: dict[str, Any] = {
        "url": feishu_url, "mdPath": str(md_path), "sha256_16": digest,
        "revisionBefore": revision, "existingBlocks": len(ids),
        "degraded": degraded, "dryRun": dry_run,
    }

    if dry_run:
        result["plan"] = ("block_replace 区间" if ids else "append（正文为空）")
        return result

    if not body.strip():
        result["skipped"] = "本地正文为空，未推送（避免清空飞书文档）"
        return result

    # 写之前先把飞书现有 XML 备份下来：block_replace 会替换整个正文区间，
    # 而飞书文档里可能有 Notion 侧并不存在的内容（如指向其他 wiki 的 cite）。
    backup = _backup(sidecar, md_path, existing, revision)
    result["backup"] = str(backup) if backup else None

    try:
        if ids:
            # 不能用一个区间替换整个正文：飞书的 block_replace 要求首尾是**同一父容器下的
            # 直接兄弟块**，而列表项（<li>）是列表容器的子块，与顶层 <h1> 不同级，
            # 会报 "must be sibling blocks under the same parent"。
            # 所以：把第一块整体替换成新正文，再逐个删掉其余旧块（单块删除不受同级限制）。
            client.docs_update(feishu_url, "block_replace", body,
                               block_id=ids[0], doc_format="xml")
            deleted: list[str] = []
            failures: list[str] = []
            for old_id in reversed(ids[1:]):
                try:
                    client.docs_update(feishu_url, "block_delete", None,
                                       block_id=old_id, doc_format="xml")
                    deleted.append(old_id)
                except LarkCliError as exc:
                    failures.append(f"{old_id}: {exc}")
            result["action"] = "block_replace+delete"
            result["replacedBlock"] = ids[0]
            result["deletedBlocks"] = deleted
            if failures:
                result["deleteErrors"] = failures
        else:
            client.docs_update(feishu_url, "append", body, doc_format="xml")
            result["action"] = "append"
    except LarkCliError as exc:
        result["error"] = str(exc)
        result["subtype"] = exc.subtype
        if exc.needs_user_confirmation:
            result["hint"] = "高风险写操作，需要显式 --yes；本模块按需在你的确认下再执行"
        return result

    after = client.docs_fetch(feishu_url, doc_format="xml", detail="with-ids", scope="full")
    document_after = ((after.get("data") or {}).get("document") or {})
    result["revisionAfter"] = document_after.get("revision_id")
    result["blockIdsAfter"] = body_block_ids(document_after.get("content") or "")
    result["titleUnchanged"] = _title_text(existing_title) == _title_text(title_xml)

    if sidecar is not None:
        _record_sidecar(sidecar, result, body, md_path)
    return result


def _backup(sidecar: pathlib.Path | None, md_path: pathlib.Path, xml: str,
            revision: Any) -> pathlib.Path | None:
    """推送前把飞书文档现有 XML 存一份，便于回滚。"""
    if not xml.strip():
        return None
    base = (sidecar.parent if sidecar else md_path.parent / "_formats")
    directory = base / "_backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{md_path.stem}.rev{revision}.{stamp}.xml"
    path.write_text(xml + "\n", encoding="utf-8")
    return path


def _record_sidecar(sidecar: pathlib.Path, result: dict, body_xml: str,
                    md_path: pathlib.Path) -> None:
    """把飞书侧的格式信息写进 sidecar（含实际发出的 XML，供后续排版优化）。"""
    payload = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    emitted_path = sidecar.with_name(sidecar.name.replace(".formats.json", ".feishu.xml"))
    emitted_path.write_text(body_xml + "\n", encoding="utf-8")

    payload["feishu"] = {
        "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": result.get("url"),
        "action": result.get("action"),
        "revisionBefore": result.get("revisionBefore"),
        "revisionAfter": result.get("revisionAfter"),
        "blockIds": result.get("blockIdsAfter"),
        "sha256_16": result.get("sha256_16"),
        "emittedXmlPath": emitted_path.name,
        "degraded": result.get("degraded") or [],
        "note": ("飞书特有的格式（表格表头底色、列宽、高亮块底色等）以 emittedXml "
                 "为准；本轮未设置任何自定义色，保留默认，便于后续按此文件做排版优化。"),
    }
    fmt.write_formats(sidecar, payload)
