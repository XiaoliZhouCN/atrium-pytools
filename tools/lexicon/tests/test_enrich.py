# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\tests\test_enrich.py
"""ECDICT 补全测试：全部用合成数据，不联网、不碰真实词库。"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from lexicon import build_wordbook, import_source, lookup
from lexicon.enrich import (
    EnrichError,
    _one_line,
    build_enrichment,
    decode_exchange,
    fetch_ecdict,
    stripword,
)
from lexicon.paths import DataPaths, data_paths

ECDICT_FIELDS = [
    "word", "phonetic", "definition", "translation", "pos", "collins",
    "oxford", "tag", "bnc", "frq", "exchange", "detail", "audio",
]

#: 西里尔 ә（U+04D9）：ECDICT 音标里混入的形近字母
CYRILLIC_SCHWA = "\u04d9"

#: 注意 "\\n" 是**字面量**反斜杠+n，与 ECDICT 实际数据一致
ECDICT_ROWS = [
    {
        "word": "corrupt",
        "phonetic": f"k{CYRILLIC_SCHWA}'r\u028cpt",
        "definition": "v. def one\\na. def two",
        "translation": "a. 腐败的\\nvt. 使腐烂",
        "pos": "",
        "collins": "2",
        "oxford": "",
        "tag": "gk cet6 ielts gre",
        "bnc": "7571",
        "frq": "5850",
        "exchange": "d:corrupted/i:corrupting/p:corrupted",
        "detail": "",
        "audio": "",
    },
    {
        "word": "spot-on",
        "phonetic": "sp\u0252t \u0252n",
        "definition": "adj. exactly right",
        "translation": "完全正确的",
        "pos": "",
        "collins": "3",
        "oxford": "1",
        "tag": "cet4 ielts",
        "bnc": "1000",
        "frq": "900",
        "exchange": "",
        "detail": "",
        "audio": "",
    },
    {
        "word": "accommodation",
        "phonetic": f"{CYRILLIC_SCHWA}.k\u0252m{CYRILLIC_SCHWA}'dei\u0283{CYRILLIC_SCHWA}n",
        "definition": "n. a settlement of differences",
        "translation": "n. 膳宿",
        "pos": "",
        "collins": "2",
        "oxford": "1",
        "tag": "gk cet4 ielts",
        "bnc": "2048",
        "frq": "5369",
        "exchange": "s:accommodations",
        "detail": "",
        "audio": "",
    },
    {
        # 我们词库里没有这个词，不该被匹配进来
        "word": "irrelevant",
        "phonetic": "",
        "definition": "",
        "translation": "",
        "pos": "",
        "collins": "",
        "oxford": "",
        "tag": "",
        "bnc": "",
        "frq": "",
        "exchange": "",
        "detail": "",
        "audio": "",
    },
]

SOURCE_TEXT = """雅思词表

Word List 01
corrupt /kəˈrʌpt/ v. 腐化  a. 堕落的
spot on 恰好的
accommodation /əˌkɒməˈdeɪʃn/ n. 膳宿
zzznowhere /z/ n. 不存在的词
"""


def write_ecdict(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ECDICT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class HelperTest(unittest.TestCase):
    def test_stripword_removes_separators(self) -> None:
        self.assertEqual(stripword("spot on"), "spoton")
        self.assertEqual(stripword("spot-on"), "spoton")
        self.assertEqual(stripword("long-time"), "longtime")
        self.assertEqual(stripword("Long Time!"), "longtime")

    def test_decode_exchange(self) -> None:
        self.assertEqual(
            decode_exchange("d:corrupted/i:corrupting/p:corrupted"),
            "过去分词 corrupted；现在分词 corrupting；过去式 corrupted",
        )
        self.assertEqual(decode_exchange(""), "")
        self.assertEqual(decode_exchange("garbage"), "")

    def test_one_line_handles_literal_backslash_n(self) -> None:
        """回归：ECDICT 用字面量 \\n 分隔释义，不是真换行。"""
        raw = "a. 腐败的\\nvt. 使腐烂"          # 反斜杠 + n
        self.assertNotIn("\n", raw)
        self.assertEqual(_one_line(raw), "a. 腐败的 / vt. 使腐烂")

    def test_one_line_handles_real_newline_too(self) -> None:
        self.assertEqual(_one_line("甲\n乙"), "甲 / 乙")

    def test_one_line_empty(self) -> None:
        self.assertEqual(_one_line(""), "")
        self.assertEqual(_one_line(None), "")


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin = self.root / "origin.csv"
        write_ecdict(self.origin, ECDICT_ROWS)
        self.dest = self.root / "cache" / "ecdict.csv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_download_then_cache_hit(self) -> None:
        first = fetch_ecdict(url=self.origin.as_uri(), dest=self.dest)
        self.assertTrue(first["downloaded"])
        self.assertTrue(self.dest.is_file())
        self.assertEqual(first["bytes"], self.origin.stat().st_size)

        second = fetch_ecdict(url=self.origin.as_uri(), dest=self.dest)
        self.assertFalse(second["downloaded"])
        self.assertEqual(second["sha256"], first["sha256"])

    def test_force_redownloads(self) -> None:
        fetch_ecdict(url=self.origin.as_uri(), dest=self.dest)
        again = fetch_ecdict(url=self.origin.as_uri(), dest=self.dest, force=True)
        self.assertTrue(again["downloaded"])


class EnrichmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "wordbook"
        self.ecdict = self.root / "ecdict.csv"
        write_ecdict(self.ecdict, ECDICT_ROWS)

        source = self.root / "source.txt"
        source.write_text(SOURCE_TEXT, encoding="utf-8")
        import_source(source, data_dir=self.data_dir, name="fixture.txt")
        build_wordbook(data_dir=self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def enrich(self):
        return build_enrichment(data_dir=self.data_dir, ecdict=self.ecdict)

    def test_match_report(self) -> None:
        report = self.enrich()
        self.assertEqual(report.scanned, len(ECDICT_ROWS))
        self.assertEqual(report.targets, 4)
        self.assertEqual(report.matched, 3)
        self.assertEqual(report.by_exact, 2)      # corrupt, accommodation
        self.assertEqual(report.by_strip, 1)      # spot on -> spot-on
        self.assertEqual(report.unmatched, ["zzznowhere"])
        self.assertAlmostEqual(report.coverage, 75.0, places=2)

    def test_phonetic_shape_letters_normalized(self) -> None:
        report = self.enrich()
        self.assertEqual(report.phonetic_fixed, 2)   # corrupt + accommodation
        payload = lookup("corrupt", data_dir=self.data_dir)
        self.assertEqual(payload["enrichment"]["phonetic"], "kə'r\u028cpt")
        self.assertNotIn(CYRILLIC_SCHWA, payload["enrichment"]["phonetic"])

    def test_fields_landed_correctly(self) -> None:
        self.enrich()
        payload = lookup("corrupt", data_dir=self.data_dir)
        enrich = payload["enrichment"]
        self.assertEqual(enrich["source"], "ecdict")
        self.assertEqual(enrich["matched_by"], "exact")
        self.assertEqual(enrich["translation"], "a. 腐败的 / vt. 使腐烂")
        self.assertEqual(enrich["definition"], "v. def one / a. def two")
        self.assertEqual(enrich["collins"], 2)
        self.assertFalse(enrich["oxford"])
        self.assertEqual(enrich["tags"], ["gk", "cet6", "ielts", "gre"])
        self.assertEqual(enrich["bnc"], 7571)
        self.assertEqual(enrich["frq"], 5850)
        self.assertEqual(
            enrich["exchange_cn"],
            "过去分词 corrupted；现在分词 corrupting；过去式 corrupted",
        )

    def test_oxford_flag(self) -> None:
        self.enrich()
        self.assertTrue(lookup("accommodation", data_dir=self.data_dir)["enrichment"]["oxford"])
        self.assertTrue(lookup("spot on", data_dir=self.data_dir)["enrichment"]["oxford"])

    def test_strip_match_records_source_word(self) -> None:
        self.enrich()
        enrich = lookup("spot on", data_dir=self.data_dir)["enrichment"]
        self.assertEqual(enrich["matched_by"], "strip")
        self.assertEqual(enrich["source_word"], "spot-on")

    def test_unmatched_word_has_no_enrichment(self) -> None:
        self.enrich()
        self.assertIsNone(lookup("zzznowhere", data_dir=self.data_dir)["enrichment"])

    def test_idempotent(self) -> None:
        first = self.enrich()
        second = self.enrich()
        self.assertEqual(first.matched, second.matched)
        conn = lookup("corrupt", data_dir=self.data_dir)
        self.assertIsNotNone(conn["enrichment"])
        from lexicon import open_wordbook

        handle = open_wordbook(self.data_dir)
        try:
            total = handle.execute("SELECT COUNT(*) AS n FROM enrichment").fetchone()["n"]
        finally:
            handle.close()
        self.assertEqual(total, 3)     # 不因重跑而翻倍

    def test_missing_ecdict_file_raises(self) -> None:
        with self.assertRaises(EnrichError):
            build_enrichment(data_dir=self.data_dir, ecdict=self.root / "nope.csv")

    def test_missing_wordbook_raises(self) -> None:
        from lexicon.build import BuildError

        with self.assertRaises(BuildError):
            build_enrichment(data_dir=self.root / "empty", ecdict=self.ecdict)

    def test_rebuild_drops_enrichment(self) -> None:
        """build 会重建整库，补全必须重跑——这条行为要显式固定下来。"""
        self.enrich()
        self.assertIsNotNone(lookup("corrupt", data_dir=self.data_dir)["enrichment"])
        build_wordbook(data_dir=self.data_dir)
        self.assertIsNone(lookup("corrupt", data_dir=self.data_dir)["enrichment"])

    def test_lookup_payload_has_new_sections(self) -> None:
        payload = lookup("corrupt", data_dir=self.data_dir)
        for key in ("enrichment", "examples", "collocations"):
            self.assertIn(key, payload)
        self.assertEqual(payload["examples"], [])
        self.assertEqual(payload["collocations"], [])

    def test_meta_records_source_digest(self) -> None:
        report = self.enrich()
        from lexicon import open_wordbook

        handle = open_wordbook(self.data_dir)
        try:
            meta = {
                row["key"]: row["value"]
                for row in handle.execute("SELECT key, value FROM meta")
            }
        finally:
            handle.close()
        self.assertEqual(meta["enrichment_source"], "ecdict")
        self.assertEqual(meta["enrichment_sha256"], report.source_sha256)
        self.assertEqual(meta["enrichment_matched"], "3")


if __name__ == "__main__":
    unittest.main()
