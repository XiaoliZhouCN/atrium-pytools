# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\spreads.py
"""牌阵定义。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spread:
    """一个牌阵：名称、张数、每张的位置名。"""

    key: str
    name: str
    en: str
    positions: tuple[str, ...]
    description: str = ""

    @property
    def size(self) -> int:
        return len(self.positions)


SPREADS: dict[str, Spread] = {
    "single": Spread(
        key="single",
        name="单张牌",
        en="Single Card",
        positions=("今日指引",),
        description="一张牌回答一个明确的问题。",
    ),
    "three": Spread(
        key="three",
        name="三张牌",
        en="Three Card",
        positions=("过去", "现在", "未来"),
        description="最通用的时间之流牌阵。",
    ),
    "situation": Spread(
        key="situation",
        name="情境牌阵",
        en="Situation / Action / Outcome",
        positions=("现状", "建议", "结果"),
        description="用于决策：看清现状、该怎么做、会走向哪里。",
    ),
    "cross": Spread(
        key="cross",
        name="凯尔特十字",
        en="Celtic Cross",
        positions=(
            "现状", "阻碍", "目标", "根基", "过去",
            "未来", "自我", "环境", "希望与恐惧", "结果",
        ),
        description="十张牌的传统大牌阵。",
    ),
}

#: 别名 -> 规范 key
ALIASES: dict[str, str] = {
    "1": "single", "one": "single", "card": "single",
    "3": "three", "past-present-future": "three", "ppf": "three",
    "action": "situation", "sao": "situation",
    "10": "cross", "celtic": "cross", "celtic-cross": "cross",
}

#: 未指定牌阵时，按张数自动匹配的默认牌阵
DEFAULT_BY_SIZE: dict[int, str] = {1: "single", 3: "three", 10: "cross"}


def spread_keys() -> list[str]:
    """全部规范牌阵 key。"""
    return list(SPREADS)


def resolve_spread(name: str) -> Spread:
    """把用户输入（key / 别名 / 中文名 / 大小写变体）解析为牌阵。"""
    raw = str(name).strip()
    if not raw:
        raise KeyError("牌阵名不能为空")
    lowered = raw.lower()
    if lowered in SPREADS:
        return SPREADS[lowered]
    if lowered in ALIASES:
        return SPREADS[ALIASES[lowered]]
    for spread in SPREADS.values():
        if raw == spread.name or lowered == spread.en.lower():
            return spread
    raise KeyError(
        f"未知牌阵：{raw}（可选：{', '.join(SPREADS)}；也可用别名 {', '.join(ALIASES)}）"
    )


def default_spread_for(size: int) -> Spread:
    """未指定牌阵时按张数给出：1/3/10 用经典牌阵，其余即时生成。"""
    key = DEFAULT_BY_SIZE.get(int(size))
    if key is not None:
        return SPREADS[key]
    return _custom(int(size))


def get_spread(name: str, size: int | None = None) -> Spread:
    """取牌阵。

    - ``size`` 为 ``None``：按名字解析。
    - ``name`` 表示「按张数自动」（``auto``/``count`` 等）：返回 :func:`default_spread_for`。
    - ``size`` 与牌阵张数不符：降级为即时生成的等张数牌阵。
    """
    if size is None:
        return resolve_spread(name)
    if not 1 <= int(size) <= 78:
        raise ValueError(f"张数应在 1..78 之间，实际 {size}")
    count = int(size)
    if str(name).strip().lower() in {"auto", "count", "custom", "n", ""}:
        return default_spread_for(count)
    spread = resolve_spread(name)
    if spread.size != count:
        return _custom(count)
    return spread


def _custom(size: int) -> Spread:
    return Spread(
        key=f"custom-{size}",
        name=f"{size} 张牌",
        en=f"Custom {size}",
        positions=tuple(f"第 {i + 1} 张" for i in range(size)),
        description="按张数即时生成的牌阵。",
    )


def spread_list() -> list[Spread]:
    """全部牌阵定义。

    注意：不要命名为 ``spreads()``——本模块本身就叫 ``spreads``，
    同名函数会把子模块遮蔽掉。
    """
    return list(SPREADS.values())


def describe_spreads() -> str:
    """供 CLI ``--list-spreads`` 使用的多行文本。"""
    width = max(len(s.key) for s in SPREADS.values())
    lines = []
    for spread in SPREADS.values():
        lines.append(
            f"  {spread.key.ljust(width)}  {spread.size:>2} 张  {spread.name}"
            f"（{spread.en}）— {spread.description}"
        )
    return "\n".join(lines)
