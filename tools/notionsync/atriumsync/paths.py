"""atriumsync.paths — 目标目录、命名规则与常量。

工作根目录是 ``D:\\Repositories\\Manager\\AtriumNote``。所有相对路径（含 csv 里的链接）
都**相对于这个根**，而不是相对于 csv 自身所在目录。
"""

from __future__ import annotations

import pathlib
import re

#: 工作根目录（csv 中的相对路径以此为基准）
ROOT = pathlib.Path(r"D:\Repositories\Manager\AtriumNote")

#: 三个目标目录（用户指定；均已在 AtriumNote 下创建）
HOME_PAGES = ROOT / "notion_home_pages"
TECHNOLOGY = ROOT / "technology"
AI_WORK_SPACE = ROOT / "ai_work_space"

#: 每个目录下放「markdown 表达不了的格式」的 sidecar
FORMATS_DIRNAME = "_formats"

#: 只处理 Shirley's Dashboard 空间
WORKSPACE = "B"

#: Notion 对象 id
HOME_PAGE_IDS: dict[str, str] = {
    # 标题 -> page_id（B 空间 1 级页面，parent = workspace）
    "Shirley's Knowledge Repo": "3ede36a4-a26d-8050-a28c-f06df02e3945",
    "欢迎来到Shirley’s Dashboard！": "3ede36a4-a26d-80eb-8016-ccdb08c137bf",
}
TECHNOLOGY_DB = "cfee36a4-a26d-83ad-96ea-0183801bcf38"      # Technology Gallery
DAILY_NEWS_DB = "133e36a4-a26d-83e9-9789-01efce464e6b"      # 每日资讯
AI_WORK_SPACE_PAGE = "87ce36a4-a26d-8365-bca0-01ac7cbe2ee9"
DAILY_SPEC_PAGE = "38fe36a4-a26d-82e3-8f07-81577ca69c2e"    # 每日资讯 整理要求

#: Technology Gallery 的 csv 列顺序（名称放第一列，本地链接放最后一列）
TECH_COLUMNS = ["名称", "Field", "Category", "Kind", "Priority", "Topic",
                "Sources", "Query Keys", "本地路径"]
DAILY_COLUMNS = ["名称", "日期", "Topic", "本地路径"]

#: csv 中本地链接的默认文本
LINK_TEXT = "🔗"

#: 飞书写入目标（用户指定：写回那篇 Shirley's Knowledge Repo）
FEISHU_HOME_DOC = "https://my.feishu.cn/wiki/TfKjwPJ9KiZ7C9k6vJUcJMlS66d"
#: 本机用于访问该文档的 lark-cli profile（豆包账号所在租户的应用）
FEISHU_PROFILE = "doubao"

#: 本地文档 → 飞书目标 的默认映射
FEISHU_TARGETS: dict[str, str] = {
    "notion_home_pages/ShirleysKnowledgeRepo.md": FEISHU_HOME_DOC,
}

_ILLEGAL = re.compile(r"[^\w\-]+", re.UNICODE)   # \w 含中日韩字符；其余标点一律成空格
_QUOTES = re.compile(r"['’‘\"“”]")


def slug_title(title: str) -> str:
    """把 Notion 标题变成文件名。

    规则（与用户给的例子一致）：先去引号类字符（``Shirley's`` → ``Shirleys``），
    再把其余标点（含全角 ``！``、``：`` 等）换成空格并切词，纯 ASCII 词首字母大写，
    已全大写的词（GPU / AI / SDK）保持原样，中日韩字符原样保留，最后拼接。

    ``"Shirley's Knowledge Repo"``        → ``ShirleysKnowledgeRepo``
    ``"每日资讯 整理要求"``                 → ``每日资讯整理要求``
    ``"欢迎来到Shirley’s Dashboard！"``    → ``欢迎来到ShirleysDashboard``
    """
    text = _QUOTES.sub("", title or "")
    text = _ILLEGAL.sub(" ", text)
    tokens = [t for t in text.split() if t]
    out: list[str] = []
    for token in tokens:
        if token.isascii() and token.replace("-", "").isalnum() and not token.isupper():
            out.append(token[0].upper() + token[1:])
        else:
            out.append(token)
    name = "".join(out).strip(" .-")
    return name or "Untitled"


def unique_filename(directory: pathlib.Path, name: str, suffix: str = ".md") -> str:
    """重名时追加 ``-2``、``-3``，避免静默覆盖。"""
    candidate = f"{name}{suffix}"
    index = 1
    while (directory / candidate).exists() and index < 1000:
        index += 1
        candidate = f"{name}-{index}{suffix}"
    return candidate


def rel_to_root(path: pathlib.Path) -> str:
    """相对工作根目录的 POSIX 风格路径（用于 csv 链接）。"""
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def markdown_link(path: pathlib.Path, text: str = LINK_TEXT) -> str:
    """按用户要求生成 csv 单元格里的链接：链接文本用 🔗，地址为相对工作根目录的路径。"""
    return f"[{text}]({rel_to_root(path)})"
