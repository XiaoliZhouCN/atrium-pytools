# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\tests\test_parse.py
"""解析层测试。

用例全部取自原文真实行（含两处原文笔误），作为回归基线。
"""

from __future__ import annotations

import unittest

from lexicon import parse as P
from lexicon.parse import _IPA_CHARS

FIXTURE = """雅思词汇词根+联想记忆法（乱序便携版） 单词表

README
《雅思词汇词根+联想记忆法（乱序便携版）》  由新东方出版。

Word List 01
emperor   /ˈempərə(r)/ n. 皇帝；君主
exact*    /ɪgˈzækt/    a. 精确的；准确的
easy-going* /ˈiːziˏgəuɪŋ/ a. 脾气随和的，心平气和的；随便的

Word List 02
reject    /rɪˈdʒekt/   vt. 拒绝  /ˈriːdʒekt/ n. 被拒货品，不合格品
desert*   /ˈdezət/     n. 沙漠；荒地  a. 沙漠的；荒凉的  /dɪˈzɜːt/ v. 舍弃
supervision {ˌsu:pə'vɪʒn}; [ˌsju:pə'vɪʒn] n. 监督，管理；指导
commonwealth /ˈkɔmənwelθ/ n. [the C-] 英联邦；联合体
wage      /weɪdʒ/      n. 工资；[常 pl.] 报酬
fair*     /feə(r)/     a./ad. 公平的/地
roll film  胶卷
inductive reasoning [inˈdʌktiv ˈri:zənɪŋ] 归纳；推理
landfill* ['lændfɪl    n. 垃圾堆；垃圾填筑地，废渣埋填地
campfire  [ˈkæmpfaɪə(r)]] n. 营火
inland    {ˈɪnlænd}    a. 内陆的  /ˏɪnˈlænd/ ad. 向内地（或内陆）
contaminate /kənˈtæmɪneɪt/ vt. 污染
"""


def by_key(result: P.ParseResult, key: str) -> P.Entry:
    for entry in result.entries:
        if entry.lemma_key == key:
            return entry
    raise AssertionError(f"未解析出 {key}")


def issues_of(entry: P.Entry, code: str) -> list[P.Issue]:
    return [issue for issue in entry.issues if issue.code == code]


class FixtureParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = P.parse_text(FIXTURE)

    def test_line_accounting(self) -> None:
        """每一条词条都要被解析出来，且不产生未识别行。"""
        self.assertEqual(len(self.result.entries), 15)
        self.assertEqual(self.result.unparsed, [])
        self.assertEqual(self.result.list_numbers, [1, 2])

    def test_readme_block_is_not_treated_as_entries(self) -> None:
        self.assertNotIn("readme", {e.lemma_key for e in self.result.entries})

    def test_star_becomes_listening_tag_and_is_stripped(self) -> None:
        exact = by_key(self.result, "exact")
        self.assertEqual(exact.display, "exact")
        self.assertEqual(exact.tags, ["listening"])
        no_star = by_key(self.result, "emperor")
        self.assertEqual(no_star.tags, [])

    def test_hyphen_compound_is_a_word_not_a_phrase(self) -> None:
        easy = by_key(self.result, "easy-going")
        self.assertEqual(easy.kind, "word")
        self.assertEqual([s.gloss_cn for s in easy.senses], ["脾气随和的，心平气和的", "随便的"])

    def test_heteronym_phonetics_split_by_pos(self) -> None:
        reject = by_key(self.result, "reject")
        self.assertEqual(len(reject.phonetics), 2)
        self.assertEqual(reject.phonetic, "/rɪˈdʒekt/")
        self.assertEqual(reject.phonetic_alt, ["/ˈriːdʒekt/"])
        self.assertEqual(
            [(s.pos, s.gloss_cn) for s in reject.senses],
            [("vt.", "拒绝"), ("n.", "被拒货品，不合格品")],
        )
        # 回归：第二个音标曾因偏移量错位泄漏进第一个义项
        self.assertEqual(reject.senses[0].gloss_cn, "拒绝")
        self.assertEqual(reject.senses[0].phonetic, "/rɪˈdʒekt/")
        self.assertEqual(reject.senses[1].phonetic, "/ˈriːdʒekt/")

    def test_parse_keeps_only_the_phonetics_written_before_each_pos(self) -> None:
        """解析层保持原文保真：只记「写在某词性前面」的音标。

        原文 `desert* /ˈdezət/ n. 沙漠；荒地  a. 沙漠的；荒凉的  /dɪˈzɜːt/ v. 舍弃`
        里 a. 前面没有音标，解析层就是 None；「读音组继承」由构建层负责
        （见 tests/test_build.py 的 PosEntryTest）。
        """
        desert = by_key(self.result, "desert")
        self.assertEqual(
            [(s.pos, s.gloss_cn) for s in desert.senses],
            [("n.", "沙漠"), ("n.", "荒地"), ("a.", "沙漠的"), ("a.", "荒凉的"), ("v.", "舍弃")],
        )
        self.assertEqual(
            [s.phonetic for s in desert.senses],
            ["/ˈdezət/", "/ˈdezət/", None, None, "/dɪˈzɜːt/"],
        )
        for sense in desert.senses:
            self.assertNotIn("/", sense.gloss_cn)

    def test_two_phonetics_on_one_pos_become_primary_and_alt(self) -> None:
        supervision = by_key(self.result, "supervision")
        self.assertEqual(supervision.phonetic, "{ˌsu:pə'vɪʒn}")
        self.assertEqual(supervision.phonetic_alt, ["[ˌsju:pə'vɪʒn]"])
        self.assertEqual(
            [(s.pos, s.gloss_cn) for s in supervision.senses],
            [("n.", "监督，管理"), ("n.", "指导")],
        )

    def test_usage_bracket_is_not_mistaken_for_phonetic(self) -> None:
        commonwealth = by_key(self.result, "commonwealth")
        self.assertEqual(commonwealth.phonetics, [P.Phonetic("ˈkɔmənwelθ", "lingoes")])
        self.assertEqual(commonwealth.senses[0].gloss_cn, "[the C-] 英联邦")
        wage = by_key(self.result, "wage")
        self.assertEqual(wage.senses[1].gloss_cn, "[常 pl.] 报酬")
        self.assertEqual(len(wage.phonetics), 1)

    def test_pos_slash_combo_kept_verbatim(self) -> None:
        fair = by_key(self.result, "fair")
        self.assertEqual(fair.senses[0].pos, "a./ad.")
        self.assertEqual(fair.senses[0].gloss_cn, "公平的/地")

    def test_phrase_without_phonetic_or_pos(self) -> None:
        """词组本来就没有音标，按词组记即可，不应报 no_phonetic。"""
        phrase = by_key(self.result, "roll film")
        self.assertEqual(phrase.kind, "phrase")
        self.assertEqual(phrase.phonetics, [])
        self.assertEqual([(s.pos, s.gloss_cn) for s in phrase.senses], [(None, "胶卷")])
        self.assertEqual(issues_of(phrase, "no_phonetic"), [])

    def test_word_without_phonetic_still_warns(self) -> None:
        result = P.parse_text("Word List 01\nquirk    n. 怪癖\n")
        entry = result.entries[0]
        self.assertEqual(entry.kind, "word")
        self.assertTrue(issues_of(entry, "no_phonetic"))

    def test_phrase_with_phonetic_but_no_pos(self) -> None:
        phrase = by_key(self.result, "inductive reasoning")
        self.assertEqual(phrase.kind, "phrase")
        self.assertEqual(phrase.phonetic, "[inˈdʌktiv ˈri:zənɪŋ]")
        self.assertEqual(phrase.senses[0].pos, None)
        self.assertEqual(phrase.senses[0].gloss_cn, "归纳；推理")

    def test_all_ascii_bracket_phonetic_detected_via_following_pos(self) -> None:
        """`[fju:mz]` 不含音标专用字符，靠「后面跟词性」兜住。"""
        result = P.parse_text("Word List 01\nfumes*    [fju:mz]    n. 烟，气，汽\n")
        entry = result.entries[0]
        self.assertEqual(entry.display, "fumes")
        self.assertEqual(entry.phonetic, "[fju:mz]")
        self.assertEqual(entry.senses[0].gloss_cn, "烟，气，汽")
        self.assertEqual(entry.issues, [])

    def test_unclosed_phonetic_is_recovered_and_reported(self) -> None:
        landfill = by_key(self.result, "landfill")
        self.assertEqual(landfill.display, "landfill")
        self.assertEqual(landfill.phonetic, "['lændfɪl]")
        self.assertTrue(issues_of(landfill, "unclosed_phonetic"))
        self.assertEqual(
            [s.gloss_cn for s in landfill.senses], ["垃圾堆", "垃圾填筑地，废渣埋填地"]
        )

    def test_extra_delimiter_is_absorbed_and_reported(self) -> None:
        campfire = by_key(self.result, "campfire")
        self.assertEqual(campfire.display, "campfire")
        self.assertEqual(campfire.phonetic, "[ˈkæmpfaɪə(r)]")
        self.assertTrue(issues_of(campfire, "stray_phonetic_delimiter"))
        self.assertEqual([s.gloss_cn for s in campfire.senses], ["营火"])

    def test_phonetic_belongs_to_the_pos_that_follows_it(self) -> None:
        inland = by_key(self.result, "inland")
        self.assertEqual(inland.phonetic, "{ˈɪnlænd}")
        self.assertEqual(inland.phonetic_alt, ["/ˏɪnˈlænd/"])
        self.assertEqual(
            [(s.pos, s.gloss_cn, s.phonetic) for s in inland.senses],
            [("a.", "内陆的", "{ˈɪnlænd}"), ("ad.", "向内地（或内陆）", "/ˏɪnˈlænd/")],
        )

    def test_duplicate_detection(self) -> None:
        text = "Word List 01\ncontaminate /kənˈtæmɪneɪt/ vt. 污染\n" * 1
        text += "Word List 01\ncontaminate /kənˈtæmɪneɪt/ n. 致污物\n"
        result = P.parse_text(text)
        P.annotate_duplicates(result)
        self.assertEqual(len(P.duplicate_lemmas(result.entries)), 1)
        self.assertTrue(all(issues_of(e, "duplicate_lemma") for e in result.entries))

    def test_no_gloss_leaks_phonetic_characters(self) -> None:
        """核心不变式：释义里不得残留任何音标字符或花括号。"""
        for entry in self.result.entries:
            for sense in entry.senses:
                self.assertFalse(
                    _IPA_CHARS & set(sense.gloss_cn),
                    f"{entry.display} 的释义泄漏了音标：{sense.gloss_cn!r}",
                )
                self.assertNotIn("{", sense.gloss_cn)
                self.assertNotIn("}", sense.gloss_cn)


class RealSourceInvariantTest(unittest.TestCase):
    """对真实原文跑同一套不变式；原文不在时跳过。"""

    @classmethod
    def setUpClass(cls) -> None:
        from lexicon.paths import DataError, data_paths

        try:
            paths = data_paths()
        except DataError:
            raise unittest.SkipTest("词库数据目录不可用")
        source = paths.source_file("ielts_xdf_2015.txt")
        if not source.is_file():
            raise unittest.SkipTest(f"原文未归档：{source}")
        cls.result = P.parse_text(source.read_text(encoding="utf-8-sig"))

    def test_every_line_parsed(self) -> None:
        self.assertEqual(self.result.unparsed, [])
        self.assertEqual(len(self.result.entries), 3611)

    def test_every_entry_has_at_least_one_sense(self) -> None:
        for entry in self.result.entries:
            self.assertTrue(entry.senses, f"{entry.display} 没有义项")

    def test_no_gloss_leaks_phonetic_characters(self) -> None:
        offenders = [
            (entry.display, sense.gloss_cn)
            for entry in self.result.entries
            for sense in entry.senses
            if (_IPA_CHARS & set(sense.gloss_cn)) or "{" in sense.gloss_cn or "}" in sense.gloss_cn
        ]
        self.assertEqual(offenders, [])

    def test_no_headword_keeps_markup(self) -> None:
        """词头不得残留 * 或音标定界符（除 legit 的短语斜杠，如 bring around/round）。"""
        bad = [
            entry.display
            for entry in self.result.entries
            if any(ch in entry.display for ch in "*[{]}")
        ]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
