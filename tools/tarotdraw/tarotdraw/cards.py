# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\cards.py
"""牌面索引：78 张牌的 id / 英文名 / 中文名 / 牌面图。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from .data import DataPaths, load_json, data_root

#: 大阿卡纳张数（ma01..ma22）
MAJOR_COUNT = 22
#: 每个花色张数（01..14，其中 11-14 为宫廷牌）
SUIT_COUNT = 14
TOTAL_CARDS = MAJOR_COUNT + 4 * SUIT_COUNT  # 78

SUIT_KEYS = ("wands", "cups", "swords", "pentacles")
SUIT_ZH = {"wands": "权杖", "cups": "圣杯", "swords": "宝剑", "pentacles": "星币"}
SUIT_EN = {"wands": "Wands", "cups": "Cups", "swords": "Swords", "pentacles": "Pentacles"}
SUIT_ELEMENT = {"wands": "火", "cups": "水", "swords": "风", "pentacles": "土"}

RANK_ZH = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七",
    8: "八", 9: "九", 10: "十", 11: "侍从", 12: "骑士", 13: "王后", 14: "国王",
}
RANK_EN = {
    1: "Ace", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
    8: "Eight", 9: "Nine", 10: "Ten", 11: "Page", 12: "Knight", 13: "Queen", 14: "King",
}


@dataclass(frozen=True)
class Card:
    """一张牌的静态信息（不含正逆位）。"""

    id: str
    en: str
    zh: str
    image: str

    @property
    def suit(self) -> str:
        """``major`` 或四个花色之一。"""
        for key in SUIT_KEYS:
            if self.id.startswith(key):
                return key
        return "major"

    @property
    def rank(self) -> int:
        """大阿卡纳为 0；小阿卡纳为 1..14。"""
        if self.suit == "major":
            return 0
        return int(self.id[len(self.suit):])

    @property
    def is_major(self) -> bool:
        return self.suit == "major"

    @property
    def suit_zh(self) -> str:
        return "大阿卡纳" if self.is_major else SUIT_ZH[self.suit]

    @property
    def element(self) -> str:
        return "" if self.is_major else SUIT_ELEMENT[self.suit]

    def image_path(self, paths: DataPaths | None = None) -> Path:
        """牌面 jpg 的绝对路径。"""
        resolved = paths if paths is not None else data_root()
        return resolved.card_image(self.image)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "en": self.en,
            "zh": self.zh,
            "image": self.image,
            "suit": self.suit,
            "rank": self.rank,
            "is_major": self.is_major,
        }


@lru_cache(maxsize=16)
def _index(root: str) -> tuple[Card, ...]:
    paths = data_root(root)
    rows = load_json(paths.index_file)
    cards = tuple(
        Card(
            id=str(row["id"]),
            en=str(row.get("en", "")),
            zh=str(row.get("zh", "")),
            image=str(row.get("image", "")),
        )
        for row in rows
    )
    if len(cards) != TOTAL_CARDS:
        raise ValueError(f"index.json 应有 {TOTAL_CARDS} 张牌，实际 {len(cards)}")
    return cards


def _cached_index(paths: DataPaths | None = None) -> tuple[Card, ...]:
    return _index(str((paths if paths is not None else data_root()).root))


def all_cards(paths: DataPaths | None = None) -> tuple[Card, ...]:
    """按 index.json 顺序返回 78 张牌。"""
    return _cached_index(paths)


def get_card(card_id: str, paths: DataPaths | None = None) -> Card:
    """按 id 取牌，找不到抛 :class:`KeyError`。"""
    for card in _cached_index(paths):
        if card.id == card_id:
            return card
    raise KeyError(f"未知牌面 id：{card_id}")


def card_ids(paths: DataPaths | None = None) -> list[str]:
    """78 个 id，顺序与 index.json 一致。"""
    return [card.id for card in _cached_index(paths)]


def cards_by_id(paths: DataPaths | None = None) -> dict[str, Card]:
    """``{id: Card}`` 映射。"""
    return {card.id: card for card in _cached_index(paths)}


def iter_cards(paths: DataPaths | None = None) -> Iterator[Card]:
    return iter(_cached_index(paths))


def clear_cache() -> None:
    """清空索引缓存（测试或切换数据目录后使用）。"""
    _index.cache_clear()


# 兼容旧式常量访问：cards.ALL_CARDS / cards.CARDS_BY_ID（懒加载）
def __getattr__(name: str):
    if name == "ALL_CARDS":
        return _cached_index()
    if name == "CARDS_BY_ID":
        return cards_by_id()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
