# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tarotdraw\render_html.py
"""单文件 HTML 渲染：牌面图默认 base64 内嵌，双击即可离线查看。"""

from __future__ import annotations

import base64
import datetime as _dt
import html
import mimetypes
from pathlib import Path

from .engine import Reading

#: 不做 base64 内嵌时，HTML 里引用图片的路径前缀
RELATIVE_PREFIX = "data/"

DEFAULT_MAX_EMBED_BYTES = 32 * 1024 * 1024


class HtmlError(RuntimeError):
    """HTML 渲染失败（例如牌面图缺失）。"""


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _friendly_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB"):
        if size < 1024 or unit == "MiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} MiB"


def image_data_uri(path: Path) -> str:
    """把图片读成 data URI。"""
    if not path.is_file():
        raise HtmlError(f"牌面图不存在：{path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def collect_image_paths(reading: Reading) -> list[Path]:
    """本次抽牌涉及的所有牌面图。"""
    return [drawn.card.image_path() for drawn in reading.cards]


def estimate_embedded_bytes(reading: Reading) -> int:
    """估算 base64 内嵌后的体积（膨胀 4/3）。"""
    total = 0
    for path in collect_image_paths(reading):
        if path.is_file():
            total += int(path.stat().st_size * 4 / 3)
    return total


def _relative_src(card) -> str:
    """相对路径引用（HTML 放在 tools/tarotdraw/ 下时可用）。"""
    return RELATIVE_PREFIX + card.image.replace("\\", "/")


def _card_html(drawn, *, image_src: str, embed_failed: str = "") -> str:
    card = drawn.card
    orientation = drawn.orientation_zh
    orientation_en = "Reversed" if drawn.reversed else "Upright"
    title_zh = _esc(card.zh)
    title_en = _esc(card.en)
    classes = ["card"]
    if drawn.reversed:
        classes.append("is-reversed")

    meta_bits = [f'<span class="chip">{_esc(card.id)}</span>']
    if card.is_major:
        meta_bits.append('<span class="chip">大阿卡纳</span>')
    else:
        meta_bits.append(f'<span class="chip">{_esc(card.suit_zh)}</span>')
        if card.element:
            meta_bits.append(f'<span class="chip">元素 {_esc(card.element)}</span>')
    image_note = f'<p class="warn">{_esc(embed_failed)}</p>' if embed_failed else ""

    meaning_blocks = []
    for key, meaning in drawn.meanings.items():
        label = meaning.get("label", key)
        extras = []
        for field, tag in (("astro", "占星"), ("element", "元素"), ("direction", "方位")):
            if meaning.get(field):
                extras.append(f'<span class="chip">{tag} {_esc(meaning[field])}</span>')
        intro = f'<p class="intro">{_esc(meaning["intro"])}</p>' if meaning.get("intro") else ""
        rows = []
        if meaning.get("upright"):
            rows.append(
                f'<div class="meaning up"><span class="tag">正位</span>'
                f'<p>{_esc(meaning["upright"])}</p></div>'
            )
        if meaning.get("reversed"):
            rows.append(
                f'<div class="meaning rev"><span class="tag">逆位</span>'
                f'<p>{_esc(meaning["reversed"])}</p></div>'
            )
        meaning_blocks.append(
            f'<section class="deck" data-deck="{_esc(key)}">'
            f"<h4>{_esc(label)}</h4>"
            + (f'<div class="chips">{"".join(extras)}</div>' if extras else "")
            + intro
            + "".join(rows)
            + "</section>"
        )

    ordinal = drawn.index
    return f"""      <article class="{' '.join(classes)}" tabindex="0" role="button"
               aria-label="{title_zh} / {title_en}（{_esc(orientation)}）">
        <div class="flip">
          <div class="face front">
            <img src="{image_src}" alt="{title_zh} {title_en}" loading="lazy" />
            <span class="badge">{_esc(orientation)}</span>
            <span class="ord">{ordinal}</span>
          </div>
          <div class="face back">
            <header>
              <h3>{title_zh}<small>{title_en}</small></h3>
              <p class="pos">{_esc(drawn.position)} · {_esc(orientation)} / {orientation_en}</p>
              <div class="chips">{''.join(meta_bits)}</div>
            </header>
            <div class="meanings">{''.join(meaning_blocks)}</div>
          </div>
        </div>
        <p class="caption">{_esc(drawn.position)} · {title_zh}</p>
        {image_note}
      </article>"""


def _stylesheet() -> str:
    return """
    :root {
      --bg: #0e0b18; --ink: #ece7fb; --dim: #a99fd0;
      --gold: #d9b45b; --rose: #d17fa6; --line: #372f5c; --card: #1b1636;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 32px 20px 64px;
      background: radial-gradient(1200px 600px at 50% -10%, #241a4d 0%, var(--bg) 60%);
      color: var(--ink); line-height: 1.7;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC",
        "Segoe UI", system-ui, sans-serif;
    }
    .wrap { max-width: 1240px; margin: 0 auto; }
    .hero { text-align: center; margin-bottom: 8px; }
    .hero h1 {
      margin: 0; font-size: clamp(22px, 3.4vw, 38px); letter-spacing: .22em;
      color: var(--gold); font-weight: 600;
    }
    .hero p { margin: 10px 0 0; color: var(--dim); font-size: 14px; }
    .bar { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin: 22px 0 30px; }
    button {
      background: #241d47; color: var(--ink); border: 1px solid var(--line);
      border-radius: 999px; padding: 8px 18px; font: inherit; font-size: 14px;
      cursor: pointer; transition: .18s;
    }
    button:hover { border-color: var(--gold); color: var(--gold); }
    .grid {
      display: grid; gap: 26px; justify-content: center;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }
    .card { margin: 0; perspective: 1400px; }
    .flip {
      position: relative; width: 100%; aspect-ratio: 5 / 8.6;
      transform-style: preserve-3d; transition: transform .6s cubic-bezier(.3,.8,.3,1);
    }
    .card.flipped .flip { transform: rotateY(180deg); }
    .face {
      position: absolute; inset: 0; backface-visibility: hidden;
      border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
      background: var(--card); box-shadow: 0 18px 40px rgba(0,0,0,.55);
    }
    .front { display: flex; }
    .front img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .card.is-reversed .front img { transform: rotate(180deg); }
    .badge {
      position: absolute; left: 10px; top: 10px; background: rgba(14,11,24,.82);
      border: 1px solid var(--line); border-radius: 999px; padding: 2px 12px;
      font-size: 12px; color: var(--gold);
    }
    .card.is-reversed .badge { color: var(--rose); }
    .ord {
      position: absolute; right: 10px; top: 10px; width: 26px; height: 26px;
      border-radius: 50%; background: rgba(14,11,24,.82); border: 1px solid var(--line);
      display: grid; place-items: center; font-size: 12px; color: var(--dim);
    }
    .back {
      transform: rotateY(180deg); padding: 14px; overflow-y: auto;
      display: flex; flex-direction: column; gap: 10px;
      background: linear-gradient(160deg, #221b48, #15112c);
    }
    .back h3 { margin: 0; font-size: 17px; color: var(--gold); }
    .back h3 small { display: block; font-size: 11px; color: var(--dim); letter-spacing: .08em; }
    .pos { margin: 4px 0 0; font-size: 12px; color: var(--dim); }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip {
      font-size: 11px; color: var(--dim); border: 1px solid var(--line);
      border-radius: 999px; padding: 1px 9px;
    }
    .deck h4 {
      margin: 0 0 6px; font-size: 12px; color: var(--gold); font-weight: 600;
      border-bottom: 1px dashed var(--line); padding-bottom: 4px;
    }
    .deck .intro { margin: 6px 0; font-size: 12px; color: var(--dim); }
    .meaning { margin-top: 6px; font-size: 12.5px; }
    .meaning .tag {
      display: inline-block; font-size: 11px; margin-right: 6px;
      border-radius: 4px; padding: 0 6px;
    }
    .meaning.up .tag { background: rgba(80,180,200,.18); color: #7fd6e6; }
    .meaning.rev .tag { background: rgba(200,110,160,.18); color: var(--rose); }
    .meaning p { margin: 2px 0 0; }
    .caption { text-align: center; margin: 12px 0 0; font-size: 13px; color: var(--dim); }
    .warn { color: var(--rose); font-size: 11px; text-align: center; }
    footer {
      margin-top: 42px; padding-top: 18px; border-top: 1px solid var(--line);
      color: var(--dim); font-size: 12px; text-align: center;
    }
    footer code { color: var(--gold); }
    @media print {
      body { background: #fff; color: #111; }
      .flip { transform: none !important; }
      .face { position: static; box-shadow: none; }
      .back { transform: none; page-break-inside: avoid; }
      button { display: none; }
    }
    """


def _script() -> str:
    return """
    (function () {
      var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
      function toggle(card) { card.classList.toggle('flipped'); }
      cards.forEach(function (card) {
        card.addEventListener('click', function () { toggle(card); });
        card.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault(); toggle(card);
          }
        });
      });
      var all = document.getElementById('flip-all');
      if (all) {
        all.addEventListener('click', function () {
          var target = !cards.every(function (c) { return c.classList.contains('flipped'); });
          cards.forEach(function (c) { c.classList.toggle('flipped', target); });
          all.textContent = target ? '全部盖回' : '全部翻开';
        });
      }
    })();
    """


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>__STYLE__</style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <h1>__TITLE__</h1>
      <p>__SUBTITLE__</p>
    </header>
    <div class="bar">
      <button id="flip-all" type="button">全部翻开</button>
    </div>
    <main class="grid">
__CARDS__
    </main>
    <footer>
      <p>__FOOTER__</p>
      <p>点击任意牌面即可翻看牌义；本文件为单文件离线 HTML，可直接分享。</p>
    </footer>
  </div>
<script>__SCRIPT__</script>
</body>
</html>
"""


def render_html(
    reading: Reading,
    *,
    title: str = "塔罗抽牌",
    embed_images: bool = True,
    max_embed_bytes: int = DEFAULT_MAX_EMBED_BYTES,
    generated_at: _dt.datetime | None = None,
) -> str:
    """把一次抽牌渲染为完整 HTML 字符串。

    ``embed_images=True`` 时牌面图内嵌 base64（离线单文件）；
    否则引用相对 ``data/cards/...``，需要 HTML 与 ``data/`` 目录相邻。
    """
    generated = generated_at or _dt.datetime.now()
    stamp = generated.strftime("%Y-%m-%d %H:%M")
    positions = " / ".join(reading.spread.positions)

    total_bytes = estimate_embedded_bytes(reading)
    use_embed = bool(embed_images) and total_bytes <= max_embed_bytes

    cards_html = []
    for drawn in reading.cards:
        note = ""
        src = _relative_src(drawn.card)
        if use_embed:
            try:
                src = image_data_uri(drawn.card.image_path())
            except HtmlError as exc:
                note = f"（牌面图缺失：{exc}）"
        cards_html.append(_card_html(drawn, image_src=src, embed_failed=note))

    if use_embed:
        note = f"牌面图已内嵌 base64（{_friendly_size(total_bytes)}），本文件可离线单独分享。"
    elif embed_images:
        note = (
            f"牌面图合计约 {_friendly_size(total_bytes)}，超过内嵌上限 "
            f"{_friendly_size(max_embed_bytes)}，已改用相对路径 data/ 引用。"
        )
    else:
        note = "牌面图以相对路径 data/ 引用（未内嵌），请与 data/ 目录一起移动。"

    subtitle = (
        f"{reading.spread.name}（{reading.spread.en}） · {reading.n} 张 · "
        f"{positions} · {stamp}"
    )
    footer = (
        f"牌阵 <code>{_esc(reading.spread.key)}</code> · "
        f"随机种子 <code>{reading.seed}</code> · "
        f"牌义来源 {', '.join(_esc(d) for d in reading.decks)} · "
        f"复现同一次抽牌：<code>--seed {reading.seed}</code><br />{_esc(note)}"
    )

    return (
        TEMPLATE.replace("__STYLE__", _stylesheet())
        .replace("__SCRIPT__", _script())
        .replace("__TITLE__", _esc(title))
        .replace("__SUBTITLE__", _esc(subtitle))
        .replace("__CARDS__", "\n".join(cards_html))
        .replace("__FOOTER__", footer)
    )


def write_html(reading: Reading, target: str | Path, **kwargs) -> Path:
    """渲染并写入 HTML 文件，返回最终绝对路径。

    ``target`` 是目录时，自动命名为 ``tarotdraw-<时间戳>.html``。
    """
    out = Path(target).expanduser()
    if out.is_dir():
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = out / f"tarotdraw-{stamp}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(reading, **kwargs), encoding="utf-8", newline="\n")
    return out.resolve()
