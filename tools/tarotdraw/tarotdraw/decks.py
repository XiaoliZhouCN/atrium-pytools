# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\decks.py
"""牌义 deck 加载：general_guide / ethereal_visions / universal_waite。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable

from .data import DataPaths, DataError, load_json, data_root

#: deck key -> 文件名
DECK_FILES: dict[str, str] = {
    "general_guide": "general_guide.json",
    "ethereal_visions": "ethereal_visions.json",
    "universal_waite": "universal_waite.json",
}

DECK_LABELS: dict[str, str] = {
    "general_guide": "塔罗牌通用指南",
    "ethereal_visions": "Ethereal Visions Illuminated Tarot",
    "universal_waite": "The Universal Waite Tarot",
}

#: 默认抽牌时一并给出的牌义（即 ChestTarot 原后端的行为）
DEFAULT_DECKS: tuple[str, ...] = tuple(DECK_FILES)

DECK_KEYS: tuple[str, ...] = tuple(DECK_FILES)
DECK_FILENAMES: tuple[str, ...] = tuple(DECK_FILES.values())

#: deck 记录中用于展示的字段
MEANING_FIELDS: tuple[str, ...] = (
    "name", "astro", "element", "direction", "intro", "upright", "reversed",
)

#: 未随数据一起拷贝的 deck（personal_notes.json 全为空，已排除）
EXCLUDED_DECKS: dict[str, str] = {
    "personal_notes": "全部牌义为空，未纳入工具数据",
}


@dataclass(frozen=True)
class Deck:
    """一个牌义 deck。"""

    key: str
    label: str
    filename: str
    rows: dict[str, dict[str, Any]]

    def __len__(self) -> int:
        return len(self.rows)

    def meaning(self, card_id: str) -> dict[str, str]:
        row = self.rows.get(card_id, {})
        return {field: str(row.get(field, "") or "") for field in MEANING_FIELDS}

    def non_empty_fields(self) -> tuple[str, ...]:
        """该 deck 实际有内容的字段（用于决定是否展示占星/元素等元信息）。"""
        fields = []
        for field in MEANING_FIELDS:
            if field == "name":
                continue
            if any(str(row.get(field, "") or "").strip() for row in self.rows.values()):
                fields.append(field)
        return tuple(fields)


def deck_keys() -> list[str]:
    """全部可选 deck key。"""
    return list(DECK_FILES)


def deck_label(key: str) -> str:
    return DECK_LABELS.get(key, key)


def _paths(paths: DataPaths | None) -> DataPaths:
    return paths if paths is not None else data_root()


@lru_cache(maxsize=16)
def _load(key: str, root: str) -> Deck:
    paths = data_root(root)
    if key not in DECK_FILES:
        raise KeyError(f"未知 deck：{key}（可选：{', '.join(DECK_FILES)}）")
    filename = DECK_FILES[key]
    rows = load_json(paths.json / filename)
    if not isinstance(rows, list):
        raise DataError(f"{filename} 应为数组，实际 {type(rows).__name__}")
    table: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            raise DataError(f"{filename} 中存在缺少 id 的记录")
        table[str(row["id"])] = row
    return Deck(key=key, label=deck_label(key), filename=filename, rows=table)


def load_deck(key: str, paths: DataPaths | None = None) -> Deck:
    """加载单个 deck（进程内缓存）。"""
    return _load(key, str(_paths(paths).root))


def load_decks(keys: Iterable[str] | None = None, paths: DataPaths | None = None) -> dict[str, Deck]:
    """批量加载 deck，返回 ``{key: Deck}``。"""
    selected = list(keys) if keys is not None else deck_keys()
    unknown = [k for k in selected if k not in DECK_FILES]
    if unknown:
        raise KeyError(f"未知 deck：{', '.join(unknown)}（可选：{', '.join(DECK_FILES)}）")
    return {key: load_deck(key, paths) for key in selected}


def clear_cache() -> None:
    """清空 deck 缓存（测试或切换数据目录后使用）。"""
    _load.cache_clear()
