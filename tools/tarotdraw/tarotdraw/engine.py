# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\engine.py
"""抽牌引擎：洗牌、发牌、正逆位、牌义投影。

可复现性是硬要求：``seed`` 为 ``None`` 时使用系统随机源，但结果里总会带
``seed`` 字段，调用方记下它即可完整复现同一次抽牌。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .cards import Card, all_cards, get_card
from .data import DataPaths
from .decks import DECK_LABELS, deck_keys, load_decks
from .spreads import Spread, default_spread_for, get_spread

ORIENTATION_UPRIGHT = "upright"
ORIENTATION_REVERSED = "reversed"

ORIENTATION_ZH = {ORIENTATION_UPRIGHT: "正位", ORIENTATION_REVERSED: "逆位"}


class DrawError(ValueError):
    """非法抽牌参数（张数、牌阵、deck）。"""


@dataclass(frozen=True)
class DrawnCard:
    """一张已抽出的牌：静态牌面 + 位置 + 正逆位 + 各 deck 牌义。"""

    card: Card
    position: str
    orientation: str
    index: int
    meanings: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def reversed(self) -> bool:
        return self.orientation == ORIENTATION_REVERSED

    @property
    def orientation_zh(self) -> str:
        return ORIENTATION_ZH[self.orientation]

    def meaning(self, deck: str) -> dict[str, str]:
        return self.meanings.get(deck, {})

    def as_dict(self) -> dict[str, Any]:
        """JSON 友好的纯数据视图。

        注意：``reversed`` 是布尔，``meanings[deck]["upright"]/["reversed"]``
        才是牌义文本——与 ChestTarot 原后端把布尔命名为 ``upright`` 的用法不同。
        """
        return {
            **self.card.as_dict(),
            "position": self.position,
            "orientation": self.orientation,
            "orientation_zh": self.orientation_zh,
            "reversed": self.reversed,
            "index": self.index,
            "meanings": {
                key: {**values, "label": DECK_LABELS.get(key, key)}
                for key, values in self.meanings.items()
            },
        }


@dataclass(frozen=True)
class Reading:
    """一次完整抽牌。"""

    spread: Spread
    cards: tuple[DrawnCard, ...]
    decks: tuple[str, ...]
    seed: int
    allow_reversed: bool
    requested_n: int
    data_root: str = ""

    @property
    def n(self) -> int:
        return len(self.cards)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": "tarotdraw",
            "spread": {
                "key": self.spread.key,
                "name": self.spread.name,
                "en": self.spread.en,
                "description": self.spread.description,
                "positions": list(self.spread.positions),
            },
            "requested_n": self.requested_n,
            "n": self.n,
            "decks": list(self.decks),
            "seed": self.seed,
            "allow_reversed": self.allow_reversed,
            "data_root": self.data_root,
            "cards": [drawn.as_dict() for drawn in self.cards],
        }


def _normalize_decks(decks: Iterable[str] | None) -> tuple[str, ...]:
    if decks is None:
        return tuple(deck_keys())
    selected: list[str] = []
    for raw in decks:
        for part in str(raw).replace(" ", ",").split(","):
            key = part.strip()
            if key and key not in selected:
                selected.append(key)
    if not selected:
        raise DrawError(f"未选择任何 deck（可选：{', '.join(deck_keys())}）")
    unknown = [key for key in selected if key not in deck_keys()]
    if unknown:
        raise DrawError(f"未知 deck：{', '.join(unknown)}（可选：{', '.join(deck_keys())}）")
    return tuple(selected)


def draw_cards(
    n: int = 1,
    *,
    decks: Iterable[str] | None = None,
    seed: int | None = None,
    allow_reversed: bool = True,
    reversed_ratio: float = 0.5,
    spread: str | Spread | None = None,
    paths: DataPaths | None = None,
) -> Reading:
    """抽 ``n`` 张**不重复**的牌，返回 :class:`Reading`。

    参数
    ----
    n        张数，1..78
    decks    牌义 deck，默认全部三个
    seed     随机种子；``None`` 表示真随机（结果里仍会回报实际种子）
    allow_reversed  是否允许逆位
    reversed_ratio  逆位概率（默认 0.5）
    spread   牌阵 key / 别名 / :class:`Spread`；张数与 ``n`` 不符时自动生成位置名
    """
    if isinstance(n, bool):
        raise DrawError("张数必须是整数")
    try:
        count = int(n)
    except (TypeError, ValueError) as exc:
        raise DrawError(f"张数必须是整数，实际 {n!r}") from exc
    if not 1 <= count <= 78:
        raise DrawError(f"张数应在 1..78 之间，实际 {count}")
    if not 0.0 <= reversed_ratio <= 1.0:
        raise DrawError(f"逆位概率应在 0.0..1.0 之间，实际 {reversed_ratio}")

    deck_keys_selected = _normalize_decks(decks)

    if spread is None:
        spread_obj = default_spread_for(count)
    elif isinstance(spread, Spread):
        spread_obj = spread if spread.size == count else get_spread("auto", count)
    else:
        spread_obj = get_spread(str(spread), count)

    rng = random.Random(seed)
    actual_seed = seed if seed is not None else rng.randrange(0, 2**31 - 1)
    if seed is None:
        # 用新种子重建 RNG，确保回报的 seed 与洗牌结果一一对应
        rng = random.Random(actual_seed)

    ids = [card.id for card in all_cards(paths)]
    sampled = rng.sample(ids, count)

    loaded = load_decks(deck_keys_selected, paths)
    drawn: list[DrawnCard] = []
    for position, card_id in zip(spread_obj.positions, sampled):
        orientation = _roll_orientation(rng, allow_reversed, reversed_ratio)
        card = get_card(card_id, paths)
        meanings = {key: deck.meaning(card_id) for key, deck in loaded.items()}
        drawn.append(
            DrawnCard(
                card=card,
                position=position,
                orientation=orientation,
                index=len(drawn) + 1,
                meanings=meanings,
            )
        )

    return Reading(
        spread=spread_obj,
        cards=tuple(drawn),
        decks=deck_keys_selected,
        seed=actual_seed,
        allow_reversed=allow_reversed,
        requested_n=count,
        data_root=str(paths.root) if paths is not None else "",
    )


def _roll_orientation(rng: random.Random, allow_reversed: bool, ratio: float) -> str:
    if not allow_reversed or ratio <= 0.0:
        return ORIENTATION_UPRIGHT
    if ratio >= 1.0:
        return ORIENTATION_REVERSED
    return ORIENTATION_REVERSED if rng.random() < ratio else ORIENTATION_UPRIGHT


def orientations_for(
    n: int,
    seed: int | None = None,
    *,
    allow_reversed: bool = True,
    reversed_ratio: float = 0.5,
) -> tuple[list[str], int]:
    """只生成正逆位序列（HTML 翻牌校验等复用），返回 ``(orientations, seed)``。"""
    if not 1 <= int(n) <= 78:
        raise DrawError(f"张数应在 1..78 之间，实际 {n}")
    rng = random.Random(seed)
    actual_seed = seed if seed is not None else rng.randrange(0, 2**31 - 1)
    if seed is None:
        rng = random.Random(actual_seed)
    return [_roll_orientation(rng, allow_reversed, reversed_ratio) for _ in range(int(n))], actual_seed


def readings_for(card_ids: Sequence[str], decks: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """对给定 id 列表取各 deck 牌义（不洗牌），返回纯数据列表。"""
    selected = _normalize_decks(decks)
    loaded = load_decks(selected)
    result = []
    for card_id in card_ids:
        card = get_card(card_id)
        result.append(
            {
                **card.as_dict(),
                "meanings": {
                    key: {**deck.meaning(card_id), "label": deck.label}
                    for key, deck in loaded.items()
                },
            }
        )
    return result


def draw(
    n: int = 1,
    spread: str | Spread | None = None,
    decks: Iterable[str] | None = None,
    seed: int | None = None,
    allow_reversed: bool = True,
    paths: DataPaths | None = None,
) -> dict[str, Any]:
    """便利函数：抽牌并直接返回 JSON 友好的 dict。"""
    return draw_cards(
        n, decks=decks, seed=seed, allow_reversed=allow_reversed, spread=spread, paths=paths
    ).as_dict()


def resolve_spread_for(n: int, spread: str | Spread | None = None) -> Spread:
    """按张数解析牌阵（CLI 与渲染共用）。"""
    if spread is None:
        return default_spread_for(n)
    if isinstance(spread, Spread):
        return spread if spread.size == n else get_spread("auto", n)
    return get_spread(str(spread), n)
