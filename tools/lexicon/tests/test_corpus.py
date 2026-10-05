# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\tests\test_corpus.py
"""开放语料测试：例句与介词搭配统计。全部用合成语料，不联网。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lexicon import build_wordbook, import_source, lookup
from lexicon.corpus import (
    CorpusError,
    _pick_examples,
    build_corpus,
    fetch_tatoeba,
    iter_sentences,
    scan_corpus,
)

SOURCE_TEXT = """雅思词表

Word List 01
depend /dɪˈpend/ vi. 依靠
rely /rɪˈlaɪ/ vi. 信赖
corrupt /kəˈrʌpt/ v. 腐化
"""

#: (id, text) —— 长度都落在 20..160 之间，除特意标注的两句
SENTENCES = [
    (1, "Many people depend on their parents for money."),   # depend + on
    (2, "We depend on each other."),                         # depend + on
    (3, "You should not depend too much."),                  # depend，后不接介词
    (4, "I rely on you completely."),                        # rely + on
    (5, "The whole system was corrupt."),                    # corrupt
    (6, "depend depend"),                                    # 太短 → 长度过滤
    (7, "Depend on it."),                                    # 太短 → 长度过滤
    (8, "They depend on depend on nothing at all really."),  # depend 出现两次 → 不收例句
]


class PickExamplesTest(unittest.TestCase):
    def test_prefers_length_near_target(self) -> None:
        candidates = [(1, "x" * 20), (2, "y" * 60), (3, "z" * 100)]
        picked = _pick_examples(candidates, 2)
        self.assertEqual([sid for sid, _ in picked], [2, 1])

    def test_limit_respected(self) -> None:
        candidates = [(i, "a" * (30 + i)) for i in range(10)]
        self.assertEqual(len(_pick_examples(candidates, 3)), 3)


class CorpusTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "wordbook"
        source = self.root / "source.txt"
        source.write_text(SOURCE_TEXT, encoding="utf-8")
        import_source(source, data_dir=self.data_dir, name="fixture.txt")
        build_wordbook(data_dir=self.data_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def scan(self, sentences=None, max_examples: int = 3):
        return scan_corpus(
            self.data_dir, iter(sentences or SENTENCES), max_examples=max_examples
        )


class ScanCorpusTest(CorpusTestBase):
    def test_length_filter(self) -> None:
        _rows, _colls, report = self.scan()
        # 第 6、7 句太短被丢弃，其余 6 句进入统计
        self.assertEqual(report.sentences, len(SENTENCES))

    def test_collocation_counts(self) -> None:
        _rows, colls, _report = self.scan()
        by_pattern = {pattern: (hits, word_hits, total) for _wid, pattern, hits, word_hits, total in colls}
        # 句 1、2 各一次，句 8 两次 → depend on 共 4
        self.assertEqual(by_pattern["depend on"][0], 4)
        self.assertEqual(by_pattern["depend on"][2], 4)
        # rely on：1 次，rely 后接介词 1 次
        self.assertEqual(by_pattern["rely on"][0], 1)
        self.assertEqual(by_pattern["rely on"][2], 1)
        # corrupt 后面从没接过介词 → 不产生搭配行
        self.assertNotIn("corrupt", " ".join(by_pattern))

    def test_word_hits_counts_every_occurrence(self) -> None:
        _rows, colls, _report = self.scan()
        depend = next(c for c in colls if c[1] == "depend on")
        # 长度过滤在分词之前：句 6、7 太短被丢弃。
        # 因此 depend 出现在句 1、2、3 各一次 + 句 8 两次 = 5 次
        self.assertEqual(depend[3], 5)

    def test_examples_exclude_sentences_with_repeats(self) -> None:
        rows, _colls, report = self.scan()
        texts = {text for _wid, _sid, text, _seq in rows}
        self.assertIn("Many people depend on their parents for money.", texts)
        self.assertNotIn("They depend on depend on nothing at all really.", texts)
        self.assertNotIn("depend depend", texts)
        self.assertEqual(report.examples, len(rows))

    def test_examples_limited_per_word(self) -> None:
        rows, _colls, _report = self.scan(max_examples=1)
        counts: dict[int, int] = {}
        for wid, _sid, _text, _seq in rows:
            counts[wid] = counts.get(wid, 0) + 1
        self.assertTrue(all(n == 1 for n in counts.values()), counts)

    def test_top_preposition_summary(self) -> None:
        _rows, _colls, report = self.scan()
        top = dict(report.top_preposition_share)
        # depend 与 rely 都是 on 占第一
        self.assertEqual(top.get("on"), 2)


class BuildCorpusTest(CorpusTestBase):
    def test_rows_land_in_db(self) -> None:
        report = build_corpus(data_dir=self.data_dir, corpus=self._write_bz2())
        self.assertGreater(report.examples, 0)
        self.assertGreater(report.collocations, 0)

        payload = lookup("depend", data_dir=self.data_dir)
        patterns = {c["pattern"] for c in payload["collocations"]}
        self.assertIn("depend on", patterns)
        self.assertTrue(payload["examples"])
        for example in payload["examples"]:
            self.assertIn("depend", example["text_en"].lower())

    def test_share_of_preps(self) -> None:
        build_corpus(data_dir=self.data_dir, corpus=self._write_bz2())
        payload = lookup("depend", data_dir=self.data_dir)
        top = payload["collocations"][0]
        self.assertEqual(top["pattern"], "depend on")
        self.assertEqual(top["share_of_preps"], 1.0)   # depend 只接 on

    def test_idempotent(self) -> None:
        path = self._write_bz2()
        first = build_corpus(data_dir=self.data_dir, corpus=path)
        second = build_corpus(data_dir=self.data_dir, corpus=path)
        self.assertEqual(first.examples, second.examples)
        self.assertEqual(first.collocations, second.collocations)
        from lexicon import open_wordbook

        handle = open_wordbook(self.data_dir)
        try:
            examples = handle.execute("SELECT COUNT(*) AS n FROM example").fetchone()["n"]
            colls = handle.execute("SELECT COUNT(*) AS n FROM collocation").fetchone()["n"]
        finally:
            handle.close()
        self.assertEqual(examples, first.examples)
        self.assertEqual(colls, first.collocations)

    def test_missing_corpus_raises(self) -> None:
        with self.assertRaises(CorpusError):
            build_corpus(data_dir=self.data_dir, corpus=self.root / "nope.bz2")

    def test_rebuild_drops_corpus_data(self) -> None:
        build_corpus(data_dir=self.data_dir, corpus=self._write_bz2())
        self.assertTrue(lookup("depend", data_dir=self.data_dir)["examples"])
        build_wordbook(data_dir=self.data_dir)
        payload = lookup("depend", data_dir=self.data_dir)
        self.assertEqual(payload["examples"], [])
        self.assertEqual(payload["collocations"], [])

    def _write_bz2(self) -> Path:
        import bz2

        path = self.root / "corpus.tsv.bz2"
        with bz2.open(path, "wt", encoding="utf-8") as handle:
            for sid, text in SENTENCES:
                handle.write(f"{sid}\teng\t{text}\n")
        return path


class FetchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin = self.root / "origin.tsv.bz2"
        import bz2

        with bz2.open(self.origin, "wt", encoding="utf-8") as handle:
            handle.write("1\teng\tHello world.\n")
        self.dest = self.root / "cache" / "eng_sentences.tsv.bz2"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_download_then_cache_hit(self) -> None:
        first = fetch_tatoeba(url=self.origin.as_uri(), dest=self.dest)
        self.assertTrue(first["downloaded"])
        second = fetch_tatoeba(url=self.origin.as_uri(), dest=self.dest)
        self.assertFalse(second["downloaded"])
        self.assertEqual(second["sha256"], first["sha256"])

    def test_iter_sentences_parses_tsv(self) -> None:
        self.assertEqual(list(iter_sentences(self.origin)), [("1", "Hello world.")])

    def test_iter_sentences_missing_file(self) -> None:
        with self.assertRaises(CorpusError):
            list(iter_sentences(self.root / "nope.bz2"))


if __name__ == "__main__":
    unittest.main()
