# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\tests\test_build.py
"""构建层测试：import → build → lookup / stats / overrides。

全部在临时目录里跑，不触碰真实词库。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lexicon import (
    BuildError,
    build_wordbook,
    import_source,
    lookup,
    open_wordbook,
    stats,
)

FIXTURE = """雅思词汇词根+联想记忆法（乱序便携版） 单词表

README
由新东方出版。

Word List 01
emperor   /ˈempərə(r)/ n. 皇帝；君主
exact*    /ɪgˈzækt/    a. 精确的；准确的
roll film  胶卷

Word List 02
reject    /rɪˈdʒekt/   vt. 拒绝  /ˈriːdʒekt/ n. 被拒货品，不合格品
contaminate /kənˈtæmɪneɪt/ vt. 污染
"""

DUPLICATE_FIXTURE = """Word List 01
contaminate /kənˈtæmɪneɪt/ n. 致污物，污染物
Word List 01
contaminate /kənˈtæmɪneɪt/ vt. 污染
"""


class BuildTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.source = self.data_dir / "source.txt"
        self.source.write_text(FIXTURE, encoding="utf-8")
        import_source(self.source, data_dir=self.data_dir, name="fixture.txt")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self):
        return build_wordbook(data_dir=self.data_dir)


class BuildBasicsTest(BuildTestBase):
    def test_report_accounting(self) -> None:
        report = self.build()
        self.assertEqual(report.parsed_entries, 5)
        self.assertEqual(report.unparsed_lines, 0)
        self.assertEqual(report.words, 5)
        # 背诵单元：emperor[n.] exact[a.] roll film reject[vt.] reject[n.] contaminate[vt.]
        self.assertEqual(report.units, 6)
        self.assertEqual(report.senses, 8)
        self.assertEqual(report.occurrences, 5)
        self.assertEqual(report.phrases, 1)
        self.assertEqual(report.list_numbers, [1, 2])
        # roll film 是词组，无音标不再算缺陷；reject 有两个音标 → info
        self.assertEqual(report.severity_counts, {"info": 1})
        self.assertEqual(report.issue_counts, {"multi_phonetic": 1})

    def test_artifacts_written(self) -> None:
        report = self.build()
        self.assertTrue(Path(report.db_path).is_file())
        self.assertTrue(Path(report.review_csv).is_file())
        self.assertTrue(Path(report.report_json).is_file())
        payload = json.loads(Path(report.report_json).read_text(encoding="utf-8"))
        self.assertEqual(payload["words"], 5)
        self.assertEqual(payload["source_sha256"], report.source_sha256)

    def test_build_is_idempotent(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first.words, second.words)
        self.assertEqual(first.senses, second.senses)

    def test_builtin_collections(self) -> None:
        report = self.build()
        names = set(report.collections)
        self.assertIn("全库", names)
        self.assertIn("短语", names)
        self.assertIn("List 01", names)
        self.assertIn("List 02", names)
        self.assertEqual(report.collections["List 02"], 2)
        self.assertEqual(report.collections["短语"], 1)

    def test_missing_source_raises(self) -> None:
        (self.data_dir / "raw" / "fixture.txt").unlink()
        with self.assertRaises(BuildError):
            self.build()


class DuplicateMergeTest(unittest.TestCase):
    def test_same_word_twice_merges_but_keeps_both_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            source = data_dir / "source.txt"
            source.write_text(DUPLICATE_FIXTURE, encoding="utf-8")
            import_source(source, data_dir=data_dir, name="fixture.txt")
            report = build_wordbook(data_dir=data_dir)
            self.assertEqual(report.words, 1)
            self.assertEqual(report.occurrences, 2)
            self.assertEqual(report.senses, 2)

            conn = open_wordbook(data_dir)
            try:
                rows = conn.execute("SELECT list_no, seq_no FROM occurrence").fetchall()
                self.assertEqual(len(rows), 2)
                word = conn.execute("SELECT id FROM word WHERE lemma_key='contaminate'").fetchone()
                senses = conn.execute(
                    "SELECT gloss_cn FROM sense WHERE word_id=? ORDER BY sense_no", (word["id"],)
                ).fetchall()
                self.assertEqual([s["gloss_cn"] for s in senses], ["致污物，污染物", "污染"])
            finally:
                conn.close()


class LookupTest(BuildTestBase):
    def test_lookup_word(self) -> None:
        self.build()
        payload = lookup("reject", data_dir=self.data_dir)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["phonetic"], "/rɪˈdʒekt/")
        self.assertEqual(payload["phonetic_alt"], ["/ˈriːdʒekt/"])
        self.assertEqual(
            [(s["pos"], s["gloss_cn"]) for s in payload["senses"]],
            [("vt.", "拒绝"), ("n.", "被拒货品，不合格品")],
        )
        self.assertEqual([o["list_no"] for o in payload["occurrences"]], [2])

    def test_lookup_is_case_insensitive(self) -> None:
        self.build()
        self.assertIsNotNone(lookup("REJECT", data_dir=self.data_dir))

    def test_lookup_tags(self) -> None:
        self.build()
        payload = lookup("exact", data_dir=self.data_dir)
        self.assertEqual(payload["tags"], ["listening"])

    def test_lookup_phrase(self) -> None:
        self.build()
        payload = lookup("roll film", data_dir=self.data_dir)
        self.assertEqual(payload["kind"], "phrase")
        self.assertEqual(payload["senses"][0]["gloss_cn"], "胶卷")

    def test_lookup_missing_returns_none(self) -> None:
        self.build()
        self.assertIsNone(lookup("nonexistentword", data_dir=self.data_dir))

    def test_lookup_survives_fts_syntax_characters(self) -> None:
        """用户输入可能带 FTS5 语法字符，查不到也不能抛异常。"""
        self.build()
        for term in ['"', "*", "a-b", "don't", "NEAR(", "", "   "]:
            lookup(term, data_dir=self.data_dir)


class PosEntryTest(BuildTestBase):
    """背诵单元 = 词头 + 词性，记录格式 `词头[词性] / 音标 / 释义（含词性）`。"""

    def test_units_split_by_pos_and_carry_own_phonetic(self) -> None:
        self.build()
        payload = lookup("reject", data_dir=self.data_dir)
        units = payload["entries"]
        self.assertEqual([u["key"] for u in units], ["reject[vt.]", "reject[n.]"])
        self.assertEqual([u["pos"] for u in units], ["vt.", "n."])
        self.assertEqual([u["phonetic"] for u in units], ["/rɪˈdʒekt/", "/ˈriːdʒekt/"])
        self.assertEqual(units[0]["gloss_cn"], "vt. 拒绝")
        self.assertEqual(units[1]["gloss_cn"], "n. 被拒货品，不合格品")

    def test_unit_gloss_carries_pos_prefix_and_joins_senses(self) -> None:
        # 原文 `emperor /ˈempərə(r)/ n. 皇帝；君主` → 一个单元、两个义项合并
        self.build()
        payload = lookup("emperor", data_dir=self.data_dir)
        self.assertEqual(len(payload["entries"]), 1)
        unit = payload["entries"][0]
        self.assertEqual(unit["key"], "emperor[n.]")
        self.assertEqual(unit["gloss_cn"], "n. 皇帝；君主")
        self.assertEqual(unit["gloss_body"], "皇帝；君主")

    def test_phrase_unit_key_has_no_pos_suffix(self) -> None:
        self.build()
        payload = lookup("roll film", data_dir=self.data_dir)
        self.assertEqual([u["key"] for u in payload["entries"]], ["roll film"])
        self.assertIsNone(payload["entries"][0]["pos"])
        self.assertIsNone(payload["entries"][0]["phonetic"])
        self.assertEqual(payload["entries"][0]["gloss_cn"], "胶卷")

    def test_senses_link_back_to_their_unit(self) -> None:
        self.build()
        conn = open_wordbook(self.data_dir)
        try:
            rows = conn.execute(
                "SELECT s.gloss_cn, pe.entry_key FROM sense s"
                " JOIN pos_entry pe ON pe.id = s.pos_entry_id ORDER BY s.sense_no"
            ).fetchall()
        finally:
            conn.close()
        mapping = {row["gloss_cn"]: row["entry_key"] for row in rows}
        self.assertEqual(mapping["拒绝"], "reject[vt.]")
        self.assertEqual(mapping["被拒货品，不合格品"], "reject[n.]")

    def test_unit_keys_are_unique(self) -> None:
        self.build()
        conn = open_wordbook(self.data_dir)
        try:
            total = conn.execute("SELECT COUNT(*) AS n FROM pos_entry").fetchone()["n"]
            distinct = conn.execute(
                "SELECT COUNT(DISTINCT entry_key) AS n FROM pos_entry"
            ).fetchone()["n"]
            orphan = conn.execute(
                "SELECT COUNT(*) AS n FROM pos_entry pe"
                " LEFT JOIN word w ON w.id = pe.word_id WHERE w.id IS NULL"
            ).fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(total, distinct)
        self.assertEqual(orphan, 0)


INHERITANCE_FIXTURE = """Word List 01
corrupt /kəˈrʌpt/ v. 腐化，腐蚀  a. 堕落的，腐化的
desert* /ˈdezət/ n. 沙漠  a. 沙漠的  /dɪˈzɜːt/ v. 舍弃
reject /rɪˈdʒekt/ vt. 拒绝  /ˈriːdʒekt/ n. 被拒货品
fair* /feə(r)/ a./ad. 公平的/地
"""


class PhoneticGroupInheritanceTest(unittest.TestCase):
    """原书里一个音标标记一个「读音组」起点，其后词性共用，直到下一个音标。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        source = self.data_dir / "source.txt"
        source.write_text(INHERITANCE_FIXTURE, encoding="utf-8")
        import_source(source, data_dir=self.data_dir, name="fixture.txt")
        build_wordbook(data_dir=self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def units(self, term: str) -> list[dict]:
        payload = lookup(term, data_dir=self.data_dir)
        assert payload is not None, term
        return payload["entries"]

    def test_single_phonetic_applies_to_every_pos(self) -> None:
        units = self.units("corrupt")
        self.assertEqual([u["key"] for u in units], ["corrupt[v.]", "corrupt[a.]"])
        self.assertEqual([u["phonetic"] for u in units], ["/kəˈrʌpt/", "/kəˈrʌpt/"])

    def test_pos_without_own_phonetic_inherits_previous_group(self) -> None:
        units = self.units("desert")
        self.assertEqual([u["key"] for u in units], ["desert[n.]", "desert[a.]", "desert[v.]"])
        self.assertEqual(
            [u["phonetic"] for u in units], ["/ˈdezət/", "/ˈdezət/", "/dɪˈzɜːt/"]
        )

    def test_new_phonetic_starts_a_new_group(self) -> None:
        units = self.units("reject")
        self.assertEqual([u["phonetic"] for u in units], ["/rɪˈdʒekt/", "/ˈriːdʒekt/"])

    def test_slash_pos_combo_stays_one_unit(self) -> None:
        units = self.units("fair")
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["key"], "fair[a./ad.]")
        self.assertEqual(units[0]["gloss_cn"], "a./ad. 公平的/地")


class StatsTest(BuildTestBase):
    def test_stats(self) -> None:
        self.build()
        payload = stats(data_dir=self.data_dir)
        self.assertEqual(payload["counts"]["word"], 5)
        self.assertEqual(payload["counts"]["pos_entry"], 6)
        self.assertEqual(payload["counts"]["sense"], 8)
        self.assertEqual(payload["by_kind"], {"word": 4, "phrase": 1})
        self.assertIn("全库", [c["name"] for c in payload["collections"]])
        self.assertEqual(payload["meta"]["parser_version"], "0.1.0")

    def test_open_wordbook_without_build_raises(self) -> None:
        with self.assertRaises(BuildError):
            open_wordbook(self.data_dir)


class OverridesTest(BuildTestBase):
    def _write_overrides(self, payload: dict) -> None:
        path = self.data_dir / "overrides" / "overrides.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_patch_phonetic_and_senses(self) -> None:
        self._write_overrides(
            {
                "patches": {
                    "emperor": {
                        "phonetic": "/ˈempərə/",
                        "phonetic_src": "manual",
                        "senses": [{"pos": "n.", "gloss_cn": "皇帝（修订）"}],
                    }
                }
            }
        )
        report = self.build()
        self.assertEqual(report.overrides["patched"], 1)
        payload = lookup("emperor", data_dir=self.data_dir)
        self.assertEqual(payload["phonetic"], "/ˈempərə/")
        self.assertEqual(payload["phonetic_src"], "manual")
        self.assertEqual([s["gloss_cn"] for s in payload["senses"]], ["皇帝（修订）"])

    def test_drop_removes_word(self) -> None:
        self._write_overrides({"drop": ["roll film"]})
        report = self.build()
        self.assertEqual(report.overrides["dropped"], 1)
        self.assertEqual(report.words, 4)
        self.assertIsNone(lookup("roll film", data_dir=self.data_dir))

    def test_add_user_word_and_collection(self) -> None:
        self._write_overrides(
            {
                "add": [
                    {
                        "lemma": "carbon footprint",
                        "phonetic": "/ˈkɑːbən ˈfʊtprɪnt/",
                        "senses": [{"pos": "n.", "gloss_cn": "碳足迹"}],
                        "tags": ["writing"],
                    }
                ],
                "collections": {"写作 Task2 · 环境类": ["carbon footprint", "contaminate"]},
            }
        )
        report = self.build()
        self.assertEqual(report.overrides["added"], 1)
        self.assertEqual(report.words, 6)
        self.assertEqual(report.collections["写作 Task2 · 环境类"], 2)

        payload = lookup("carbon footprint", data_dir=self.data_dir)
        self.assertEqual(payload["kind"], "phrase")
        self.assertEqual(payload["senses"][0]["gloss_cn"], "碳足迹")
        self.assertEqual(payload["tags"], ["writing"])
        self.assertEqual(payload["occurrences"][0]["list_no"], 0)

    def test_unknown_patch_key_raises(self) -> None:
        self._write_overrides({"patches": {"nosuchword": {"display": "x"}}})
        with self.assertRaises(BuildError):
            self.build()

    def test_collection_referencing_unknown_word_raises(self) -> None:
        self._write_overrides({"collections": {"我的词单": ["nosuchword"]}})
        with self.assertRaises(BuildError):
            self.build()

    def test_add_without_senses_raises(self) -> None:
        self._write_overrides({"add": [{"lemma": "orphan"}]})
        with self.assertRaises(BuildError):
            self.build()


if __name__ == "__main__":
    unittest.main()
