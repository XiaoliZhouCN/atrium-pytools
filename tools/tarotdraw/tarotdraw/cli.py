# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\cli.py
"""tarotdraw 命令行入口。

设计原则：CLI 只**打印**结果，不打开浏览器、不调用系统程序（PyTools 红线）。

用法速查：

    tarotdraw                     # 抽 1 张（默认牌阵 single）
    tarotdraw 3                   # 抽 3 张
    tarotdraw --spread cross      # 凯尔特十字（10 张）
    tarotdraw 3 --seed 42         # 可复现
    tarotdraw 1 --upright-only    # 只出正位
    tarotdraw --html draw.html    # 另存单文件 HTML
    tarotdraw --json              # 输出 JSON（供其它程序消费）
    tarotdraw --list-spreads      # 查看牌阵
    tarotdraw --list-decks        # 查看牌义来源
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .cards import TOTAL_CARDS
from .data import DataError, data_root
from .decks import DECK_LABELS, deck_keys
from .engine import DrawError, draw_cards
from .render_text import (
    auto_color,
    render_decks,
    render_reading,
    render_spreads,
    terminal_width,
)
from .spreads import SPREADS, resolve_spread

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_DATA = 3


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"需要整数，得到 {text!r}") from None
    if not 1 <= value <= TOTAL_CARDS:
        raise argparse.ArgumentTypeError(f"张数应在 1..{TOTAL_CARDS} 之间，得到 {value}")
    return value


def _ratio(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"需要 0..1 之间的小数，得到 {text!r}") from None
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"逆位概率应在 0..1 之间，得到 {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tarotdraw",
        description="抽塔罗牌：终端呈现牌面与牌义，可选导出单文件 HTML。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  tarotdraw                      抽 1 张\n"
            "  tarotdraw 3                    抽 3 张（过去/现在/未来）\n"
            "  tarotdraw --spread cross       凯尔特十字\n"
            "  tarotdraw 3 --seed 42          复现同一次抽牌\n"
            "  tarotdraw 1 --upright-only     只出正位\n"
            "  tarotdraw --html out.html      导出单文件 HTML\n"
            "  tarotdraw 3 --json             输出 JSON\n"
        ),
    )
    parser.add_argument(
        "n",
        nargs="?",
        type=_positive_int,
        default=None,
        help=f"抽牌张数 1..{TOTAL_CARDS}（省略时按牌阵张数，默认 1）",
    )
    parser.add_argument(
        "-s", "--spread",
        default=None,
        metavar="NAME",
        help=f"牌阵：{', '.join(SPREADS)}（也支持别名 3 / 10 / celtic）",
    )
    parser.add_argument(
        "-d", "--decks",
        default=None,
        metavar="LIST",
        help=f"牌义来源，逗号分隔，默认全部：{', '.join(deck_keys())}",
    )
    parser.add_argument("--seed", type=int, default=None, help="随机种子（用于复现）")
    parser.add_argument(
        "--upright-only",
        action="store_true",
        help="只出正位（默认正逆随机）",
    )
    parser.add_argument(
        "--reversed-ratio",
        type=_ratio,
        default=0.5,
        metavar="P",
        help="逆位概率 0..1，默认 0.5",
    )
    parser.add_argument("--html", metavar="PATH", help="导出单文件 HTML 到 PATH（可为目录）")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="HTML 不内嵌图片，改用相对路径 data/ 引用",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    parser.add_argument(
        "--lang",
        choices=("zh", "en", "both"),
        default="zh",
        help="文本语言，默认 zh",
    )
    parser.add_argument(
        "--intro",
        action="store_true",
        help="附带牌面简介（intro 字段）",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="文本排版宽度，默认跟随终端",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="颜色输出策略，默认 auto",
    )
    parser.add_argument("--list-spreads", action="store_true", help="列出全部牌阵后退出")
    parser.add_argument("--list-decks", action="store_true", help="列出全部牌义来源后退出")
    parser.add_argument("--data-dir", metavar="DIR", help="覆盖数据目录（默认 tools/tarotdraw/data）")
    parser.add_argument("--version", action="version", version=f"tarotdraw {__version__}")
    return parser


def _resolve_decks(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    keys = [part.strip() for part in raw.replace(" ", ",").split(",") if part.strip()]
    if not keys:
        raise DrawError("--decks 不能为空")
    unknown = [key for key in keys if key not in deck_keys()]
    if unknown:
        raise DrawError(f"未知 deck：{', '.join(unknown)}（可选：{', '.join(deck_keys())}）")
    return keys


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 数据目录覆盖（须在读取任何数据前生效）
    try:
        paths = data_root(args.data_dir) if args.data_dir else data_root()
    except DataError as exc:
        print(f"[tarotdraw] 数据目录不可用：{exc}", file=sys.stderr)
        return EXIT_DATA

    if args.list_decks:
        keys = deck_keys()
        print(render_decks(keys, [DECK_LABELS.get(k, k) for k in keys]))
        return EXIT_OK

    if args.list_spreads:
        print(render_spreads(args.lang))
        return EXIT_OK

    if args.n is None and args.spread is None:
        count = 1
    elif args.n is None:
        try:
            count = resolve_spread(args.spread).size
        except KeyError as exc:
            print(f"[tarotdraw] {exc.args[0]}", file=sys.stderr)
            return EXIT_USAGE
    else:
        count = args.n

    color_enabled = {"always": True, "never": False, "auto": None}[args.color]
    if color_enabled is None:
        color_enabled = auto_color()

    try:
        reading = draw_cards(
            count,
            decks=_resolve_decks(args.decks),
            seed=args.seed,
            allow_reversed=not args.upright_only,
            reversed_ratio=args.reversed_ratio,
            spread=args.spread,
            paths=paths,
        )
    except (DrawError, KeyError, DataError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(f"[tarotdraw] {message}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        print(json.dumps(reading.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            render_reading(
                reading,
                lang=args.lang,
                color=color_enabled,
                width=args.width or terminal_width(),
                show_intro=args.intro,
            )
        )

    if args.html:
        from .render_html import estimate_embedded_bytes, write_html

        embedded = estimate_embedded_bytes(reading)
        target = write_html(
            reading,
            args.html,
            embed_images=not args.no_embed,
        )
        mode = "相对路径引用" if args.no_embed else f"内嵌 base64 约 {embedded / 1024:.0f} KiB"
        print(f"[tarotdraw] HTML 已写入：{target}（{mode}）", file=sys.stderr)

    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
