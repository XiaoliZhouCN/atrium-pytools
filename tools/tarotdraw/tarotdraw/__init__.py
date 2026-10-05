# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\__init__.py
"""tarotdraw — 塔罗抽牌小工具（纯数据 + 纯文本/HTML 渲染，不引入 UI 框架）。

对外 API：

    from tarotdraw import draw, draw_cards, load_deck, spread_list, spread_keys

    draw(n=1, spread=None, decks=None, seed=None, allow_reversed=True) -> dict
    draw_cards(...) -> Reading          # 结构化结果
    render_reading(reading) -> str      # 终端文本
    render_html(reading) -> str         # 单文件 HTML

CLI：

    python -m tarotdraw                 # 单张牌
    python -m tarotdraw 3               # 三张牌（过去 / 现在 / 未来）
    python -m tarotdraw --spread cross  # 凯尔特十字
    python -m tarotdraw 3 --html draw.html
"""

from .cards import (
    TOTAL_CARDS,
    Card,
    all_cards,
    card_ids,
    cards_by_id,
    get_card,
)
from .data import DataError, DataPaths, data_root, resolve_data_root
from .decks import (
    DECK_FILES,
    DECK_KEYS,
    DECK_LABELS,
    DEFAULT_DECKS,
    Deck,
    deck_keys,
    deck_label,
    load_deck,
    load_decks,
)
from .engine import (
    DrawError,
    DrawnCard,
    Reading,
    draw,
    draw_cards,
    readings_for,
)
from .spreads import (
    SPREADS,
    Spread,
    describe_spreads,
    get_spread,
    resolve_spread,
    spread_keys,
    spread_list,
)

__version__ = "0.1.0"

__all__ = [
    # 抽牌
    "draw",
    "draw_cards",
    "readings_for",
    "Reading",
    "DrawnCard",
    "DrawError",
    # 数据
    "DataError",
    "DataPaths",
    "data_root",
    "resolve_data_root",
    "Card",
    "TOTAL_CARDS",
    "get_card",
    "all_cards",
    "card_ids",
    "cards_by_id",
    "Deck",
    "load_deck",
    "load_decks",
    "deck_keys",
    "deck_label",
    "DECK_KEYS",
    "DECK_LABELS",
    "DECK_FILES",
    "DEFAULT_DECKS",
    # 牌阵
    "SPREADS",
    "Spread",
    "spread_list",
    "spread_keys",
    "get_spread",
    "resolve_spread",
    "describe_spreads",
    # 渲染（懒加载，见 __getattr__）
    "render_reading",
    "render_spreads",
    "render_decks",
    "render_html",
    "write_html",
]


def __getattr__(name: str):
    """渲染模块按需导入，避免纯数据调用方被无谓依赖拖慢。"""
    if name in ("render_reading", "render_spreads", "render_decks", "Palette"):
        from . import render_text

        return getattr(render_text, name)
    if name in ("render_html", "write_html", "image_data_uri"):
        from . import render_html

        return getattr(render_html, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
