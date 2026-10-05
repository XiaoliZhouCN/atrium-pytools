# D:\Repositories\Manager\AtriumPyTools\tools\tarotdraw\tests\test_tarotdraw.py
"""tarotdraw 自检：数据完整性、抽牌正确性、渲染与 CLI。

只用标准库 unittest，不引入额外依赖：

    python -m unittest discover -s tests -t . -v
或：
    python tests/test_tarotdraw.py
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import re
import shutil
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 测试产出的落地目录（放在仓库内，避免受限环境对系统临时目录的权限问题）
WORKDIR = Path(__file__).resolve().parent / "_work"


class WorkDirMixin:
    """给测试一个干净的本地工作目录，测试结束自动清理。"""

    def make_workdir(self) -> Path:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        target = WORKDIR / self._testMethodName
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, target, True)
        return target

from tarotdraw import cards, data, decks, engine, spreads  # noqa: E402
from tarotdraw.cli import main  # noqa: E402
from tarotdraw.render_html import HtmlError, image_data_uri, render_html  # noqa: E402
from tarotdraw.render_text import (  # noqa: E402
    SUMMARY_JOINER,
    SUMMARY_SEPARATOR,
    Palette,
    render_reading,
    summary_line,
)

TOTAL = 78


class _TagCollector(HTMLParser):
    """收集标签并检查闭合情况。"""

    VOID = {"meta", "img", "br", "input", "link", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                if self.stack.pop() == tag:
                    break


# --------------------------------------------------------------------------- 数据

#: 上游数据自身的已知缺口（原样拷贝，不做篡改）
KNOWN_EMPTY_MEANINGS = {("universal_waite", "cups02")}


class DataTests(WorkDirMixin, unittest.TestCase):
    def test_data_dir_layout(self):
        paths = data.data_root()
        self.assertTrue((paths.json / "index.json").is_file())
        self.assertTrue(paths.cards.is_dir())
        self.assertEqual(len(list(paths.cards.rglob("*.jpg"))), TOTAL)

    def test_every_index_card_has_image(self):
        for card in cards.all_cards():
            with self.subTest(card=card.id):
                self.assertTrue(card.image_path().is_file())

    def test_index_shape(self):
        all_cards = cards.all_cards()
        self.assertEqual(len(all_cards), TOTAL)
        self.assertEqual(len({c.id for c in all_cards}), TOTAL)
        self.assertEqual(sum(1 for c in all_cards if c.is_major), 22)
        for suit in cards.SUIT_KEYS:
            self.assertEqual(sum(1 for c in all_cards if c.suit == suit), 14)

    def test_decks_cover_all_cards(self):
        self.assertEqual(
            decks.deck_keys(),
            ["general_guide", "ethereal_visions", "universal_waite"],
        )
        ids = set(cards.card_ids())
        gaps = set()
        for key in decks.deck_keys():
            deck = decks.load_deck(key)
            self.assertEqual(set(deck.rows), ids, key)
            for card_id in ids:
                meaning = deck.meaning(card_id)
                if not (meaning["upright"] or meaning["reversed"]):
                    gaps.add((key, card_id))
        # 数据缺口是已知且固定的；新增缺口说明拷贝或解析出了问题
        self.assertEqual(gaps, KNOWN_EMPTY_MEANINGS)

    def test_known_gap_renders_without_crashing(self):
        """cups02 在 universal_waite 中无牌义，其余 deck 仍须正常展示。"""
        reading = engine.draw_cards(TOTAL, seed=3)
        drawn = next(d for d in reading.cards if d.card.id == "cups02")
        self.assertEqual(drawn.meaning("universal_waite")["upright"], "")
        self.assertTrue(drawn.meaning("general_guide")["upright"])
        text = render_reading(reading, color=False, width=84)
        self.assertIn("圣杯二", text)

    def test_card_derived_fields(self):
        fool = cards.get_card("ma01")
        self.assertTrue(fool.is_major)
        self.assertEqual((fool.rank, fool.suit), (0, "major"))
        ace = cards.get_card("cups01")
        self.assertEqual((ace.suit, ace.rank, ace.element), ("cups", 1, "水"))
        king = cards.get_card("pentacles14")
        self.assertEqual((king.rank, king.suit_zh), (14, "星币"))

    def test_bad_data_dir_raises(self):
        with self.assertRaises(data.DataError):
            data.data_root(self.make_workdir() / "missing")

    def test_unknown_card_id_raises(self):
        with self.assertRaises(KeyError):
            cards.get_card("nope99")


# --------------------------------------------------------------------------- 引擎

class EngineTests(unittest.TestCase):
    def test_default_is_one_card(self):
        reading = engine.draw_cards(seed=1)
        self.assertEqual(reading.n, 1)
        self.assertEqual(reading.spread.key, "single")
        self.assertEqual(reading.cards[0].position, "今日指引")

    def test_full_deck_draw_is_unique(self):
        ids = [d.card.id for d in engine.draw_cards(TOTAL, seed=3).cards]
        self.assertEqual(len(ids), TOTAL)
        self.assertEqual(len(set(ids)), TOTAL)

    def test_same_seed_same_result(self):
        self.assertEqual(
            engine.draw_cards(5, seed=99).as_dict(),
            engine.draw_cards(5, seed=99).as_dict(),
        )

    def test_different_seed_differs(self):
        a = [d.card.id for d in engine.draw_cards(5, seed=1).cards]
        b = [d.card.id for d in engine.draw_cards(5, seed=2).cards]
        self.assertNotEqual(a, b)

    def test_reported_seed_reproduces_random_draw(self):
        reading = engine.draw_cards(4, seed=None)
        again = engine.draw_cards(4, seed=reading.seed)
        self.assertEqual(
            [d.card.id for d in again.cards], [d.card.id for d in reading.cards]
        )
        self.assertEqual(
            [d.orientation for d in again.cards],
            [d.orientation for d in reading.cards],
        )

    def test_upright_only(self):
        reading = engine.draw_cards(20, seed=11, allow_reversed=False)
        self.assertTrue(
            all(d.orientation == engine.ORIENTATION_UPRIGHT for d in reading.cards)
        )

    def test_reversed_ratio_extremes(self):
        always = engine.draw_cards(5, seed=4, reversed_ratio=1.0)
        self.assertTrue(all(d.reversed for d in always.cards))
        never = engine.draw_cards(5, seed=4, reversed_ratio=0.0)
        self.assertFalse(any(d.reversed for d in never.cards))

    def test_bad_counts_rejected(self):
        for bad in (0, -1, 79, 1000):
            with self.subTest(n=bad):
                with self.assertRaises(engine.DrawError):
                    engine.draw_cards(bad, seed=1)

    def test_bad_ratio_rejected(self):
        for bad in (-0.1, 1.5):
            with self.subTest(ratio=bad):
                with self.assertRaises(engine.DrawError):
                    engine.draw_cards(1, seed=1, reversed_ratio=bad)

    def test_unknown_deck_and_spread_rejected(self):
        with self.assertRaises(engine.DrawError):
            engine.draw_cards(1, decks=["nope"], seed=1)
        with self.assertRaises(KeyError):
            spreads.resolve_spread("nope")

    def test_spread_mismatch_falls_back(self):
        reading = engine.draw_cards(2, spread="cross", seed=1)
        self.assertEqual(reading.n, 2)
        self.assertEqual(len(reading.spread.positions), 2)

    def test_spread_aliases_and_names(self):
        self.assertEqual(spreads.resolve_spread("3").key, "three")
        self.assertEqual(spreads.resolve_spread("celtic").key, "cross")
        self.assertEqual(spreads.resolve_spread("凯尔特十字").key, "cross")
        self.assertEqual(spreads.get_spread("auto", 3).key, "three")
        self.assertEqual(spreads.get_spread("auto", 2).key, "custom-2")

    def test_meaning_projection_matches_deck(self):
        """``Reading.cards[*].meanings`` 是原始牌义（无 label）；
        ``as_dict()`` 在其上补上 label，供外部程序直接消费。"""
        reading = engine.draw_cards(1, seed=8)
        drawn = reading.cards[0]
        for key, meaning in drawn.meanings.items():
            raw = decks.load_deck(key).meaning(drawn.card.id)
            self.assertEqual(meaning["upright"], raw["upright"])
            self.assertEqual(meaning["reversed"], raw["reversed"])
            self.assertEqual(meaning["name"], raw["name"])
        payload = reading.as_dict()["cards"][0]["meanings"]
        for key in drawn.meanings:
            self.assertEqual(payload[key]["label"], decks.deck_label(key))

    def test_as_dict_json_roundtrip(self):
        payload = engine.draw_cards(3, seed=2).as_dict()
        restored = json.loads(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(restored["n"], 3)
        self.assertTrue(restored["cards"][0]["meanings"]["general_guide"]["label"])

    def test_draw_convenience(self):
        payload = engine.draw(2, seed=6)
        self.assertEqual(payload["tool"], "tarotdraw")
        self.assertEqual(payload["requested_n"], 2)

    def test_deck_filter_reduces_meaning_keys(self):
        drawn = engine.draw_cards(1, seed=8, decks=["universal_waite"]).cards[0]
        self.assertEqual(list(drawn.meanings), ["universal_waite"])


# --------------------------------------------------------------------------- 文本

class TextRenderTests(unittest.TestCase):
    def test_contains_names_positions_and_seed(self):
        reading = engine.draw_cards(3, seed=7)
        text = render_reading(reading, color=False, width=84)
        for drawn in reading.cards:
            self.assertIn(drawn.card.zh, text)
            self.assertIn(drawn.orientation_zh, text)
            self.assertIn(drawn.position, text)
            self.assertIn(drawn.card.id, text)
        self.assertIn(str(reading.seed), text)

    def test_color_off_has_no_ansi(self):
        text = render_reading(engine.draw_cards(1, seed=1), color=False)
        self.assertNotIn("\033[", text)
        self.assertEqual(Palette(False).paint("x", "\033[1m"), "x")
        self.assertIn("\033[", Palette(True).paint("x", "\033[1m"))

    def test_lines_fit_requested_width(self):
        text = render_reading(engine.draw_cards(1, seed=1), color=False, width=80)
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 84)

    def test_lang_variants(self):
        reading = engine.draw_cards(1, seed=1)
        english = render_reading(reading, lang="en", color=False)
        self.assertIn(reading.cards[0].card.en, english)

    def test_lang_both_regression(self):
        """``--lang both`` 曾在 LABELS[lang] 上抛 KeyError，这里锁死不再复现。"""
        reading = engine.draw_cards(2, seed=1)
        for lang in ("zh", "en", "both"):
            with self.subTest(lang=lang):
                text = render_reading(reading, lang=lang, color=False)
                if lang == "zh":
                    self.assertIn(reading.cards[0].card.zh, text)
                else:
                    self.assertIn(reading.cards[0].card.en, text)
                if lang == "both":
                    self.assertIn(reading.cards[0].card.zh, text)
                    self.assertIn(reading.cards[0].card.en, text)


class SummaryLineTests(unittest.TestCase):
    """末尾「牌面·正逆位」一览。"""

    def test_is_the_last_line_and_only_has_cards(self):
        reading = engine.draw_cards(3, seed=7)
        text = render_reading(reading, color=False, width=84)
        last = [line for line in text.splitlines() if line.strip()][-1]
        self.assertTrue(last.startswith("  牌面一览："), last)
        body = last.strip().removeprefix("牌面一览：")
        expected = SUMMARY_SEPARATOR.join(
            f"{d.card.zh}{SUMMARY_JOINER}{d.orientation_zh}" for d in reading.cards
        )
        self.assertEqual(body, expected)
        # 除牌名与位向之外不应夹带其它信息
        for noise in (reading.cards[0].card.id, str(reading.seed), "位置", "正位 ·", "◆"):
            self.assertNotIn(noise, body)

    def test_exact_requested_format(self):
        """用户要求的示例形态：圣杯一·正位，倒吊人·逆位"""
        reading = engine.draw_cards(2, seed=1)
        body = summary_line(reading, with_names=False, palette=Palette(False)).strip()
        self.assertEqual(
            body,
            f"{reading.cards[0].card.zh}{SUMMARY_JOINER}{reading.cards[0].orientation_zh}"
            f"{SUMMARY_SEPARATOR}"
            f"{reading.cards[1].card.zh}{SUMMARY_JOINER}{reading.cards[1].orientation_zh}",
        )
        self.assertIn("·正位", body)
        self.assertEqual(body.count(SUMMARY_SEPARATOR), 1)

    def test_single_card_and_all_reversed(self):
        one = engine.draw_cards(1, seed=42)
        body = summary_line(one, with_names=False, palette=Palette(False)).strip()
        self.assertEqual(body, f"{one.cards[0].card.zh}·{one.cards[0].orientation_zh}")
        rev = engine.draw_cards(4, seed=4, reversed_ratio=1.0)
        body = summary_line(rev, with_names=False, palette=Palette(False))
        self.assertNotIn("正位", body)
        self.assertEqual(body.count("逆位"), 4)

    def test_covers_every_card_in_order(self):
        reading = engine.draw_cards(TOTAL, seed=3)
        body = summary_line(reading, with_names=False, palette=Palette(False))
        # 折行会插换行，还原成一条逻辑串再断言
        flat = body.replace("\n", "").replace("  ", "")
        self.assertEqual(flat.count(SUMMARY_SEPARATOR), TOTAL - 1)
        expected = SUMMARY_SEPARATOR.join(
            f"{d.card.zh}{SUMMARY_JOINER}{d.orientation_zh}" for d in reading.cards
        )
        self.assertEqual(flat, expected)

    def test_can_be_disabled(self):
        reading = engine.draw_cards(2, seed=1)
        text = render_reading(reading, color=False, show_summary=False)
        self.assertNotIn("牌面一览", text)

    def test_respects_lang(self):
        reading = engine.draw_cards(1, seed=1)
        english = summary_line(reading, lang="en", palette=Palette(False))
        self.assertTrue(english.strip().startswith("Cards drawn:"))
        self.assertIn(reading.cards[0].card.en, english)
        both = summary_line(reading, lang="both", palette=Palette(False))
        self.assertIn(reading.cards[0].card.en, both)
        zh = summary_line(reading, lang="zh", palette=Palette(False))
        self.assertNotIn(reading.cards[0].card.en, zh)

    def test_wraps_within_width(self):
        reading = engine.draw_cards(10, seed=1)
        text = summary_line(reading, palette=Palette(False), width=80)
        self.assertGreater(len(text.splitlines()), 1)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 82, line)


# --------------------------------------------------------------------------- HTML

class HtmlRenderTests(unittest.TestCase):
    def test_well_formed_and_self_contained(self):
        reading = engine.draw_cards(3, seed=20260214)
        doc = render_html(reading)
        self.assertTrue(doc.startswith("<!DOCTYPE html>"))
        self.assertTrue(doc.rstrip().endswith("</html>"))
        collector = _TagCollector()
        collector.feed(doc)
        self.assertEqual(collector.stack, [], f"未闭合标签：{collector.stack}")
        self.assertEqual(doc.count("data:image/jpeg;base64,"), 3)
        self.assertIn("<style>", doc)
        self.assertIn("<script>", doc)
        self.assertNotIn("http://", doc)
        self.assertNotIn("https://", doc)
        for drawn in reading.cards:
            self.assertIn(drawn.card.zh, doc)
            self.assertIn(drawn.position, doc)

    def test_embedded_bytes_are_real_jpeg(self):
        doc = render_html(engine.draw_cards(1, seed=5))
        match = re.search(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", doc)
        self.assertIsNotNone(match)
        raw = base64.b64decode(match.group(1))
        self.assertEqual(raw[:2], b"\xff\xd8")
        self.assertEqual(raw[-2:], b"\xff\xd9")

    def test_reversed_card_marked(self):
        reading = engine.draw_cards(1, seed=4, reversed_ratio=1.0)
        self.assertTrue(reading.cards[0].reversed)
        self.assertIn("is-reversed", render_html(reading))

    def test_no_embed_uses_relative_path(self):
        reading = engine.draw_cards(1, seed=5)
        doc = render_html(reading, embed_images=False)
        self.assertNotIn("data:image/jpeg;base64,", doc)
        self.assertIn(f"data/{reading.cards[0].card.image}", doc)

    def test_title_is_escaped(self):
        doc = render_html(engine.draw_cards(1, seed=5), title="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", doc)
        self.assertIn("&lt;script&gt;", doc)

    def test_missing_image_raises(self):
        with self.assertRaises(HtmlError):
            image_data_uri(Path("does-not-exist.jpg"))


# --------------------------------------------------------------------------- CLI

@contextlib.contextmanager
def capture():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def summary_block(text: str, prefix: str = "牌面一览：") -> str:
    """从整段输出里取出末尾牌面一览（去掉折行与缩进）。"""
    return _split_summary(text, prefix)[1]


def _split_summary(text: str, prefix: str = "牌面一览：") -> tuple[list[str], str]:
    """返回 （一览之前的非空行, 一览正文）。"""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if prefix in line)
    body = "".join(line.strip() for line in lines[start:]).split(prefix, 1)[1]
    before = [line for line in lines[:start] if line.strip()]
    return before, body


class CliTests(WorkDirMixin, unittest.TestCase):
    def test_draw_ok(self):
        with capture() as (out, _):
            self.assertEqual(main(["3", "--seed", "7", "--color", "never"]), 0)
        self.assertIn("三张牌", out.getvalue())
        self.assertEqual(out.getvalue().count("位置:"), 3)

    def test_json_output(self):
        with capture() as (out, _):
            self.assertEqual(main(["2", "--seed", "5", "--json"]), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["n"], 2)
        self.assertEqual(len(payload["cards"]), 2)

    def test_list_commands(self):
        with capture() as (out, _):
            self.assertEqual(main(["--list-spreads"]), 0)
        self.assertIn("凯尔特十字", out.getvalue())
        with capture() as (out, _):
            self.assertEqual(main(["--list-decks"]), 0)
        self.assertIn("ethereal_visions", out.getvalue())

    def test_unknown_deck_clean_error(self):
        with capture() as (_, err):
            code = main(["1", "--decks", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("未知 deck", err.getvalue())

    def test_bad_count_exits_2(self):
        with capture():
            with self.assertRaises(SystemExit) as exc:
                main(["999"])
        self.assertEqual(exc.exception.code, 2)

    def test_html_written(self):
        target = self.make_workdir() / "draw.html"
        with capture():
            code = main(["1", "--seed", "5", "--html", str(target), "--color", "never"])
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        self.assertIn("data:image/jpeg;base64,", target.read_text(encoding="utf-8"))

    def test_html_directory_target(self):
        workdir = self.make_workdir()
        with capture():
            code = main(["1", "--seed", "5", "--html", str(workdir)])
        self.assertEqual(code, 0)
        produced = list(workdir.glob("tarotdraw-*.html"))
        self.assertEqual(len(produced), 1)

    def test_no_embed_html(self):
        target = self.make_workdir() / "plain.html"
        with capture():
            main(["1", "--seed", "5", "--html", str(target), "--no-embed"])
        body = target.read_text(encoding="utf-8")
        self.assertNotIn("data:image/jpeg;base64,", body)
        self.assertIn("data/cards/", body)

    def test_spread_only_sets_count(self):
        with capture() as (out, _):
            self.assertEqual(main(["--spread", "cross", "--seed", "1", "--color", "never"]), 0)
        text = out.getvalue()
        self.assertIn("凯尔特十字", text)
        self.assertEqual(text.count("位置:"), 10)
        body = summary_block(text)
        self.assertEqual(body.count(SUMMARY_SEPARATOR), 9)

    def test_summary_is_last_block_after_hint(self):
        with capture() as (out, _):
            main(["3", "--seed", "7", "--color", "never"])
        text = out.getvalue()
        before, body = _split_summary(text)
        self.assertIn("复现同一次抽牌", before[-1])
        self.assertEqual(text.splitlines()[-1].strip(), "")
        for drawn in engine.draw_cards(3, seed=7).cards:
            self.assertIn(drawn.card.zh, body)

    def test_lang_both_no_crash(self):
        with capture() as (out, _):
            code = main(["2", "--seed", "1", "--lang", "both", "--color", "never"])
        self.assertEqual(code, 0)
        self.assertIn("牌面一览", out.getvalue())

    def test_json_mode_has_no_summary(self):
        with capture() as (out, _):
            self.assertEqual(main(["2", "--seed", "5", "--json"]), 0)
        self.assertNotIn("牌面一览", out.getvalue())
        json.loads(out.getvalue())

    def test_upright_only_flag(self):
        with capture() as (out, _):
            self.assertEqual(main(["5", "--seed", "2", "--upright-only", "--color", "never"]), 0)
        body = out.getvalue().split("复现")[0]
        # 逆位牌义文本仍会展示，所以这里断言的是「没有一张牌被判为逆位」
        self.assertNotIn("▼ 逆位", body)
        self.assertEqual(body.count("▲ 正位"), 5)

    def test_data_dir_override(self):
        with capture() as (_, err):
            code = main(["1", "--data-dir", r"D:\definitely\missing\dir"])
        self.assertEqual(code, 3)
        self.assertIn("数据目录不可用", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
