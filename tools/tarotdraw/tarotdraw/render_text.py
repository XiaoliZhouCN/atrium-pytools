# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\render_text.py
"""终端文本渲染（纯字符串输出，不打印、不打开外部程序）。"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Sequence

from .engine import ORIENTATION_REVERSED, Reading
from .spreads import describe_spreads

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
GOLD = "\033[33m"
ROSE = "\033[35m"
CYAN = "\033[36m"

#: deck key -> 边框字符
DECK_BULLET = {
    "general_guide": "◇",
    "ethereal_visions": "✦",
    "universal_waite": "❖",
}

LABELS = {
    "zh": {
        "title": "塔 罗 抽 牌",
        "position": "位置",
        "orientation": "位向",
        "upright": "正位",
        "reversed": "逆位",
        "deck": "牌义",
        "hint": "提示",
        "seed": "随机种子",
        "spread": "牌阵",
        "decks": "牌义来源",
        "repro": "复现同一次抽牌请在命令后加",
        "intro": "牌面简介",
        "major": "大阿卡纳",
        "element": "元素",
        "astro": "占星",
        "direction": "方位",
        "summary": "牌面一览：",
    },
    "en": {
        "title": "T A R O T   D R A W",
        "position": "Position",
        "orientation": "Orientation",
        "upright": "Upright",
        "reversed": "Reversed",
        "deck": "Meanings",
        "hint": "Hint",
        "seed": "Seed",
        "spread": "Spread",
        "decks": "Decks",
        "repro": "To reproduce this draw, add",
        "intro": "Introduction",
        "major": "Major Arcana",
        "element": "Element",
        "astro": "Astrology",
        "direction": "Direction",
        "summary": "Cards drawn: ",
    },
}

#: 牌面一览的分隔符与「牌名·位向」连接符
SUMMARY_SEPARATOR = "，"
SUMMARY_JOINER = "·"


def _labels(lang: str) -> dict:
    """取语言对应的标签表；``both`` 复用中文标签（牌名本身已中英并列）。"""
    return LABELS["en"] if lang == "en" else LABELS["zh"]


class Palette:
    """颜色开关（默认按 TTY 与 NO_COLOR / FORCE_COLOR 自动判断）。"""

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = auto_color() if enabled is None else bool(enabled)

    def paint(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        return "".join(styles) + text + RESET


def auto_color() -> bool:
    """终端是否支持颜色。"""
    force = os.environ.get("FORCE_COLOR", "").strip()
    if force and force != "0":
        return True
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty())
    except Exception:  # pragma: no cover - 极端环境下 stdout 被替换
        return False


def terminal_width(default: int = 84) -> int:
    """终端宽度（限制在 60..120，便于排版稳定）。"""
    try:
        width = shutil.get_terminal_size((default, 24)).columns
    except Exception:  # pragma: no cover
        width = default
    return max(60, min(120, width))


def _display_width(text: str) -> int:
    """按终端宽度计算字符串显示宽度（CJK 记 2 列）。"""
    import unicodedata

    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def _truncate_display(text: str, limit: int) -> str:
    if limit <= 1:
        return ""
    if _display_width(text) <= limit:
        return text
    out, width = "", 0
    for char in text:
        step = 2 if _display_width(char) == 2 else 1
        if width + step > limit - 1:
            break
        out += char
        width += step
    return out + "…"


def _pad_display(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    """按显示宽度折行（中英混排时也能对齐），每行都带 ``indent``。"""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    current_width = 0
    for char in text:
        if char == "\n":
            lines.append(indent + current)
            current, current_width = "", 0
            continue
        step = 2 if _display_width(char) == 2 else 1
        if current_width and current_width + step > width:
            lines.append(indent + current)
            current, current_width = "", 0
        current += char
        current_width += step
    lines.append(indent + current)
    return lines


def banner(palette: Palette, lang: str = "zh", width: int = 84) -> str:
    """顶部标题框。"""
    label = _labels(lang)["title"]
    inner = width - 4
    top = "╔" + "═" * (width - 2) + "╗"
    mid = "║ " + _pad_display(label, inner) + " ║"
    bottom = "╚" + "═" * (width - 2) + "╝"
    return "\n".join([palette.paint(top, GOLD), palette.paint(mid, GOLD, BOLD), palette.paint(bottom, GOLD)])


def _orientation_display(drawn, lang: str) -> str:
    """箭头 + 位向，例如 ``▼ 逆位`` / ``▲ Upright``。"""
    key = "reversed" if drawn.orientation == ORIENTATION_REVERSED else "upright"
    arrow = "▼" if drawn.reversed else "▲"
    return f"{arrow} {_labels(lang)[key]}"


def _card_names(drawn, lang: str) -> str:
    card = drawn.card
    if lang == "en":
        return card.en or card.id
    if lang == "both":
        return f"{card.zh}  ·  {card.en}"
    return card.zh or card.en


def summary_line(
    reading: Reading,
    *,
    lang: str = "zh",
    palette: Palette | None = None,
    width: int = 84,
    with_names: bool = True,
) -> str:
    """末尾一览：依次给出每张牌的「牌面·正逆位」，不含其它任何信息。

    形如：``牌面一览：圣杯一·正位，倒吊人·逆位``
    """
    palette = palette or Palette(False)
    labels = _labels(lang)
    parts = [
        f"{_card_names(drawn, lang)}{SUMMARY_JOINER}"
        f"{labels['upright' if not drawn.reversed else 'reversed']}"
        for drawn in reading.cards
    ]
    body = SUMMARY_SEPARATOR.join(parts)
    text = f"{labels['summary']}{body}" if with_names else body
    lines = _wrap(text, width, indent="  ")
    return "\n".join(palette.paint(line, GOLD) for line in lines)


def card_block(
    drawn,
    *,
    lang: str = "zh",
    palette: Palette | None = None,
    width: int = 84,
    deck_keys: Sequence[str] | None = None,
    show_intro: bool = False,
    show_meta: bool = True,
) -> str:
    """渲染单张牌。"""
    palette = palette or Palette(False)
    labels = _labels(lang)
    keys: Sequence[str] = deck_keys if deck_keys is not None else tuple(drawn.meanings)
    line_width = width - 8

    head_left = f"{drawn.index}. {_card_names(drawn, lang)}"
    head_right = f"{labels['position']}: {drawn.position}"
    pad = max(1, width - 4 - _display_width(head_left) - _display_width(head_right))
    header = palette.paint(head_left, BOLD) + " " * pad + palette.paint(head_right, DIM)
    lines = ["", header]

    meta_bits = [drawn.card.id]
    if drawn.card.is_major:
        meta_bits.append(labels["major"])
    else:
        meta_bits.append(drawn.card.suit_zh)
        if drawn.card.element:
            meta_bits.append(f"{labels['element']} {drawn.card.element}")
    meta_text = " · ".join(meta_bits)
    orient_text = _orientation_display(drawn, lang)
    lines.append(
        palette.paint(f"   {meta_text}", DIM)
        + palette.paint("   " + orient_text, ROSE if drawn.reversed else CYAN, BOLD)
    )

    for key in keys:
        meaning = drawn.meaning(key)
        if not meaning:
            continue
        label = meaning.get("label", key)
        bullet = DECK_BULLET.get(key, "•")
        lines.append("")
        lines.append(palette.paint(f"   {bullet} {label}", GOLD, BOLD))
        if show_meta:
            extras = []
            if meaning.get("astro"):
                extras.append(f"{labels['astro']}: {meaning['astro']}")
            if meaning.get("element") and drawn.card.is_major:
                extras.append(f"{labels['element']}: {meaning['element']}")
            if meaning.get("direction"):
                extras.append(f"{labels['direction']}: {meaning['direction']}")
            if extras:
                lines.append(palette.paint("     " + "   ".join(extras), DIM))
        if show_intro and meaning.get("intro"):
            lines.append(palette.paint(f"     {labels['intro']}", DIM))
            lines.extend(
                palette.paint(line, DIM)
                for line in _wrap(meaning["intro"], line_width, indent="       ")
            )
        for field in ("upright", "reversed"):
            text = meaning.get(field, "")
            if not text:
                continue
            tag = palette.paint(f"     {labels[field]} · ", CYAN if field == "upright" else ROSE)
            body = _wrap(text, line_width, indent="       ")
            if body:
                lines.append(tag + body[0].lstrip())
                lines.extend(body[1:])
            else:
                lines.append(tag.rstrip())

    lines.append(palette.paint("   " + "·" * (width - 8), DIM))
    return "\n".join(lines)


def render_reading(
    reading: Reading,
    *,
    lang: str = "zh",
    color: bool | None = None,
    width: int | None = None,
    show_intro: bool = False,
    show_meta: bool = True,
    show_hint: bool = True,
    show_summary: bool = True,
    summary_with_names: bool = True,
) -> str:
    """把一次抽牌渲染为完整终端文本。

    末尾一行是牌面一览（``show_summary``）：只给每张牌的「牌面·正逆位」。
    """
    palette = Palette(color)
    width = width or terminal_width()
    labels = _labels(lang)
    parts = [banner(palette, lang, width)]
    parts.append(
        palette.paint(
            f"  {labels['spread']}: {reading.spread.name}（{reading.spread.en}）"
            f"    {reading.n} 张    {labels['seed']}: {reading.seed}",
            DIM,
        )
    )
    for drawn in reading.cards:
        parts.append(
            card_block(
                drawn,
                lang=lang,
                palette=palette,
                width=width,
                show_intro=show_intro,
                show_meta=show_meta,
            )
        )
    if show_hint:
        parts.append(
            palette.paint(
                f"  {labels['repro']}: --seed {reading.seed}", DIM
            )
        )
    if show_summary:
        parts.append(
            summary_line(
                reading,
                lang=lang,
                palette=palette,
                width=width,
                with_names=summary_with_names,
            )
        )
    parts.append("")
    return "\n".join(parts)


def render_spreads(lang: str = "zh") -> str:
    """``--list-spreads`` 输出。"""
    lines = ["可用牌阵：", describe_spreads(), "", "别名：3=past-present-future，10=celtic，sao=情境牌阵"]
    return "\n".join(lines)


def render_decks(keys: Sequence[str], labels: Sequence[str]) -> str:
    """``--list-decks`` 输出。"""
    width = max((len(k) for k in keys), default=4)
    lines = ["可用牌义来源（deck）："]
    lines.extend(f"  {key.ljust(width)}  {label}" for key, label in zip(keys, labels))
    return "\n".join(lines)

__all__ = [
    "banner",
    "card_block",
    "render_reading",
    "render_spreads",
    "render_decks",
    "summary_line",
    "Palette",
    "auto_color",
    "terminal_width",
]
