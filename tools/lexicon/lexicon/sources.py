# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\sources.py
"""读取两层词表数据源，并提供词形归一化。

* koolearn 分层词表（CSV，``utf-8-sig``）
* 我们自己的词库 ``wordbook.db``（lexicon 产出），用于取词头与 listening 标记
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

#: 模糊归一键要抹掉的字符：空格、连字符、下划线、撇号
_STRIP_CHARS = re.compile(r"[\s\-_'’]+")
_WS = re.compile(r"\s+")


def exact_key(word: str) -> str:
    """精确匹配键：小写 + 压缩空白。"""
    return _WS.sub(" ", (word or "").strip().lower())


def norm_key(word: str) -> str:
    """模糊匹配键：在精确键基础上再抹掉空格/连字符/撇号。

    用途：master 里是 ``spot-on``、我们词书里是 ``spot on``，需要认成同一个词。
    只在目标键唯一时才允许靠它匹配，避免误并。
    """
    return _STRIP_CHARS.sub("", exact_key(word))


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """读 CSV，返回 (表头, 行)。未出现的列统一补空串。"""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [{field: (row.get(field) or "") for field in fields} for row in reader]
    return fields, rows


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    """写 CSV（``utf-8-sig``，Excel 双击不乱码）。"""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_xdf_words(db_path: Path) -> list[dict]:
    """从词库取我们的词头。

    返回 ``[{key, display, kind, listening}]``，``listening`` 来自原书 ``*`` 标记
    （用户已确认 ``*`` = 听力词汇）。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT w.id, w.lemma_key, w.display, w.kind,"
            "       CASE WHEN EXISTS ("
            "           SELECT 1 FROM word_tag wt JOIN tag t ON t.id = wt.tag_id"
            "           WHERE wt.word_id = w.id AND t.name = 'listening'"
            "       ) THEN 1 ELSE 0 END AS listening"
            "  FROM word w"
            " ORDER BY w.id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "key": exact_key(row["lemma_key"]),
            "display": row["display"],
            "kind": row["kind"] or ("phrase" if re.search(r"[ /]", row["display"] or "") else "word"),
            "listening": bool(row["listening"]),
        }
        for row in rows
    ]


def load_pool(name: str, paths) -> list[dict[str, str]]:
    """按名称取 drill 词池。

    * ``pack``      2 号文件（推荐背诵包，默认）
    * ``master``    0 号总表
    * ``base``      1 号底座
    * ``listening`` / ``reading`` / ``writing``  3/4/5 号单类别核心
    * ``xdf``       我们词书全部词
    * ``remaining`` 2 号里只属于 ``L3-xdf`` 那层的新增词
    """
    from . import paths as PP

    if name == "remaining":
        _, rows = read_csv(paths.file(PP.PACK))
        selected = [r for r in rows if r.get("layers") == "L3-xdf"]
    elif name == "xdf":
        _, master = read_csv(paths.file(PP.MASTER))
        selected = [r for r in master if r.get("xdf") == "1"]
    else:
        mapping = {
            "pack": PP.PACK,
            "master": PP.MASTER,
            "base": PP.BASE,
            "listening": PP.LISTENING,
            "reading": PP.READING,
            "writing": PP.WRITING,
        }
        if name not in mapping:
            raise ValueError(
                f"未知词池 {name!r}；可选：{', '.join(sorted(mapping))}, remaining, xdf"
            )
        _, selected = read_csv(paths.file(mapping[name]))
    return [r for r in selected if (r.get("word") or "").strip()]
