# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\tests\test_merge.py
"""合并与分层测试：全部用合成数据，不触碰真实词表。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lexicon import paths as PP
from lexicon.merge import (
    MergeError,
    XDF_LAYER,
    build_layers,
    export_master_json,
    merge_master,
    tier_of,
)
from lexicon.paths import DataPaths
from lexicon.qa import verify
from lexicon.sources import read_csv, write_csv

MASTER_FIELDS = [
    "word", "wd_id", "url", "kind", "tier", "ielts_books", "listen_books",
    "reading_books", "writing_books", "speaking_books", "skills", "in_base",
    "base_level", "base_books", "base_source",
]

MASTER_ROWS = [
    # word, wd_id, kind, ielts_books, listen, reading, writing, speaking, in_base, base_level
    ("alpha", "1", "word", 2, 4, 0, 0, 0, 1, 1),
    ("beta", "2", "word", 9, 9, 0, 0, 0, 0, 0),
    ("gamma", "3", "word", 1, 0, 0, 0, 5, 0, 0),
    ("delta", "4", "word", 1, 0, 0, 0, 0, 0, 0),
    ("spot-on", "5", "word", 3, 3, 0, 0, 0, 0, 0),
]

#: (lemma_key, display, kind, listening)
XDF_WORDS = [
    ("alpha", "alpha", "word", True),
    ("beta", "beta", "word", True),
    ("gamma", "gamma", "word", False),
    ("spot on", "spot on", "phrase", True),
    ("epsilon", "epsilon", "word", True),
    ("zeta", "zeta", "word", False),
]


def make_wordbook(path: Path, words: list[tuple[str, str, str, bool]]) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE word (id INTEGER PRIMARY KEY, lemma_key TEXT, display TEXT, kind TEXT);"
        "CREATE TABLE tag (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE word_tag (word_id INTEGER, tag_id INTEGER);"
        "INSERT INTO tag(id, name) VALUES (1, 'listening');"
    )
    for index, (key, display, kind, listening) in enumerate(words, start=1):
        conn.execute(
            "INSERT INTO word(id, lemma_key, display, kind) VALUES (?,?,?,?)",
            (index, key, display, kind),
        )
        if listening:
            conn.execute("INSERT INTO word_tag(word_id, tag_id) VALUES (?,1)", (index,))
    conn.commit()
    conn.close()


def master_row(word, wd_id, kind, books, listen, reading, writing, speaking, in_base, base_level):
    row = {
        "word": word,
        "wd_id": wd_id,
        "url": f"https://www.koolearn.com/dict/wd_{wd_id}.html",
        "kind": kind,
        "tier": tier_of(books),
        "ielts_books": str(books),
        "listen_books": str(listen),
        "reading_books": str(reading),
        "writing_books": str(writing),
        "speaking_books": str(speaking),
        "skills": "",
        "in_base": str(in_base),
        "base_level": str(base_level),
        "base_books": "0",
        "base_source": "",
    }
    # 合成数据要和真实总表一样自洽，否则「未触及行不漂移」这条检查会被假阳性污染
    row["skills"] = ";".join(
        name
        for name, column in (("听力", "listen_books"), ("口语", "speaking_books"),
                             ("阅读", "reading_books"), ("写作", "writing_books"))
        if int(row[column]) > 0
    )
    return row


class MergeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.koolearn = self.root / "koolearn"
        self.koolearn.mkdir()
        self.wordbook = self.root / "wordbook.db"
        make_wordbook(self.wordbook, XDF_WORDS)
        self.paths = DataPaths(root=self.root, koolearn=self.koolearn)

        rows = [master_row(*spec) for spec in MASTER_ROWS]
        write_csv(self.paths.master, MASTER_FIELDS, rows)
        build_layers(self.paths)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def master_by(self) -> dict[str, dict]:
        return {r["word"]: r for r in read_csv(self.paths.master)[1]}


class MergeMasterTest(MergeTestCase):
    def test_counts(self) -> None:
        report = merge_master(self.paths, backup=False)
        self.assertEqual(report.master_before, 5)
        self.assertEqual(report.master_after, 7)
        self.assertEqual(report.matched_existing, 4)
        self.assertEqual(report.added, ["epsilon", "zeta"])
        self.assertEqual(report.matched_normalized, [["spot on", "spot-on"]])
        # alpha / beta / spot-on 命中；epsilon 是新增的听力词，不计入 bumped
        self.assertEqual(report.listen_bumped, 3)
        self.assertEqual(report.xdf_listening_total, 4)

    def test_books_incremented(self) -> None:
        merge_master(self.paths, backup=False)
        by = self.master_by()
        self.assertEqual(by["alpha"]["ielts_books"], "3")
        self.assertEqual(by["beta"]["ielts_books"], "10")
        self.assertEqual(by["gamma"]["ielts_books"], "2")
        self.assertEqual(by["spot-on"]["ielts_books"], "4")
        self.assertEqual(by["delta"]["ielts_books"], "1")  # 不在我们词书里，不动

    def test_listening_bucket_incremented_only_for_marked_words(self) -> None:
        merge_master(self.paths, backup=False)
        by = self.master_by()
        self.assertEqual(by["alpha"]["listen_books"], "5")
        self.assertEqual(by["beta"]["listen_books"], "10")
        self.assertEqual(by["spot-on"]["listen_books"], "4")
        self.assertEqual(by["gamma"]["listen_books"], "0")  # 无 * 标记
        self.assertEqual(by["gamma"]["xdf_listening"], "0")

    def test_new_rows_have_empty_koolearn_provenance(self) -> None:
        merge_master(self.paths, backup=False)
        by = self.master_by()
        for word in ("epsilon", "zeta"):
            self.assertEqual(by[word]["wd_id"], "")
            self.assertEqual(by[word]["url"], "")
            self.assertEqual(by[word]["sources"], "xdf")
            self.assertEqual(by[word]["xdf"], "1")
            self.assertEqual(by[word]["ielts_books"], "1")
        self.assertEqual(by["epsilon"]["listen_books"], "1")  # * 标记且策略开启
        self.assertEqual(by["zeta"]["listen_books"], "0")

    def test_existing_rows_get_sources_marker(self) -> None:
        merge_master(self.paths, backup=False)
        by = self.master_by()
        self.assertEqual(by["alpha"]["sources"], "koolearn;xdf")
        self.assertEqual(by["delta"]["sources"], "koolearn")
        self.assertEqual(by["delta"]["xdf"], "0")

    def test_tier_and_skills_recomputed(self) -> None:
        report = merge_master(self.paths, backup=False)
        by = self.master_by()
        self.assertEqual(by["alpha"]["tier"], "B")       # 2 -> 3
        self.assertEqual(by["beta"]["tier"], "S")        # 9 -> 10
        self.assertEqual(by["gamma"]["tier"], "C")       # 1 -> 2
        self.assertEqual(by["spot-on"]["tier"], "B")     # 3 -> 4，仍是 B
        self.assertEqual(report.tier_changed, 3)         # alpha / beta / gamma
        self.assertEqual(by["alpha"]["skills"], "听力")
        self.assertEqual(by["gamma"]["skills"], "口语")   # skills 含口语
        self.assertEqual(by["delta"]["skills"], "")
        self.assertEqual(report.untouched_skills_drift, 0)

    def test_no_drift_on_rows_we_did_not_touch(self) -> None:
        """未进入我们词书的行，skills 必须一字不动。"""
        before = {r["word"]: r["skills"] for r in read_csv(self.paths.master)[1]}
        merge_master(self.paths, backup=False)
        _, rows = read_csv(self.paths.master)
        ours = {r["word"] for r in rows if r.get("xdf") == "1"}
        for row in rows:
            if row["word"] in ours:
                continue
            self.assertEqual(before[row["word"]], row["skills"], row["word"])
        self.assertNotIn("delta", ours)

    def test_second_run_is_refused(self) -> None:
        merge_master(self.paths, backup=False)
        with self.assertRaises(MergeError) as ctx:
            merge_master(self.paths, backup=False)
        self.assertIn("合并执行过了", str(ctx.exception))

    def test_rerun_is_idempotent(self) -> None:
        """已合并的行不会重复计数（force 只是跳过安全拦截）。"""
        merge_master(self.paths, backup=False)
        merge_master(self.paths, backup=False, force=True)
        by = self.master_by()
        self.assertEqual(by["alpha"]["ielts_books"], "3")
        self.assertEqual(by["alpha"]["listen_books"], "5")
        self.assertEqual(by["alpha"]["sources"], "koolearn;xdf")
        self.assertEqual(len(self.master_by()), 7)

    def test_rerun_backfills_listening_policy(self) -> None:
        """首次关掉听力口径，之后重跑应补做听力桶，而不重复计 ielts_books。"""
        merge_master(self.paths, backup=False, xdf_listening=False)
        self.assertEqual(self.master_by()["alpha"]["listen_books"], "4")
        report = merge_master(self.paths, backup=False, force=True, xdf_listening=True)
        by = self.master_by()
        self.assertEqual(by["alpha"]["listen_books"], "5")
        self.assertEqual(by["alpha"]["ielts_books"], "3")
        # alpha / beta / spot-on 三个已有行 + epsilon 这个上一轮新增的听力词
        self.assertEqual(report.listen_backfilled, 4)
        self.assertEqual(by["epsilon"]["listen_books"], "1")
        self.assertEqual(by["epsilon"]["ielts_books"], "1")  # 新增行不重复计入

    def test_migration_infers_counted_flag_without_double_counting(self) -> None:
        """旧数据没有 xdf_listen_counted 列时，重跑不能把 listen_books 再加一遍。"""
        merge_master(self.paths, backup=False)
        # 模拟旧版产物：删掉计数标记列，只留 xdf / xdf_listening
        fields, rows = read_csv(self.paths.master)
        fields = [f for f in fields if f != "xdf_listen_counted"]
        write_csv(self.paths.master, fields, rows)

        report = merge_master(self.paths, backup=False, force=True)
        by = self.master_by()
        self.assertEqual(by["alpha"]["listen_books"], "5")   # 没有被再加一次
        self.assertEqual(by["alpha"]["ielts_books"], "3")
        self.assertEqual(report.migrated_listen_counted, 4)  # alpha/beta/spot-on/epsilon
        self.assertEqual(report.listen_backfilled, 0)

    def test_listening_policy_can_be_disabled(self) -> None:
        report = merge_master(self.paths, backup=False, xdf_listening=False)
        by = self.master_by()
        self.assertEqual(report.listen_bumped, 0)
        self.assertEqual(by["alpha"]["listen_books"], "4")   # 不变
        self.assertEqual(by["alpha"]["xdf_listening"], "1")  # 事实仍记录
        self.assertEqual(by["alpha"]["skills"], "听力")       # 原有归属不变


class BuildLayersTest(MergeTestCase):
    def test_listening_core_grows_by_threshold_crossing(self) -> None:
        before = len(read_csv(self.paths.file(PP.LISTENING))[1])
        merge_master(self.paths, backup=False)
        report = build_layers(self.paths)
        self.assertEqual(before, 1)               # 仅 beta
        self.assertEqual(report.listening, 2)     # alpha 4->5 跨过阈值
        self.assertEqual(report.listening_before, 1)

    def test_base_is_in_base_rows(self) -> None:
        merge_master(self.paths, backup=False)
        report = build_layers(self.paths)
        self.assertEqual(report.base, 1)
        self.assertEqual([r["word"] for r in read_csv(self.paths.file(PP.BASE))[1]], ["alpha"])

    def test_speaking_bucket_does_not_produce_a_layer(self) -> None:
        """口语不进 layers（无口语分层文件），但仍在 master 的 skills 里。"""
        merge_master(self.paths, backup=False)
        build_layers(self.paths)
        pack = {r["word"]: r for r in read_csv(self.paths.pack)[1]}
        self.assertIn("gamma", pack)
        self.assertEqual(pack["gamma"]["layers"], XDF_LAYER)
        self.assertNotIn("L2-speaking", ";".join(r["layers"] for r in pack.values()))

    def test_pack_composition(self) -> None:
        merge_master(self.paths, backup=False)
        report = build_layers(self.paths)
        pack = {r["word"]: r for r in read_csv(self.paths.pack)[1]}
        # alpha: 底座 + 听力核心
        self.assertEqual(pack["alpha"]["layers"], "L1-base-L1;L2-listening")
        self.assertEqual(pack["alpha"]["layer_count"], "2")
        self.assertEqual(pack["beta"]["layers"], "L2-listening")
        for word in ("gamma", "spot-on", "epsilon", "zeta"):
            self.assertEqual(pack[word]["layers"], XDF_LAYER, word)
            self.assertEqual(pack[word]["layer_count"], "1", word)
        # delta 既不在任何层、也不在我们的词书里 → 不进推荐包
        self.assertNotIn("delta", pack)
        self.assertEqual(report.pack, 6)
        self.assertEqual(report.pack_xdf_layer, 4)

    def test_pack_sorted_by_layer_count(self) -> None:
        merge_master(self.paths, backup=False)
        build_layers(self.paths)
        counts = [int(r["layer_count"]) for r in read_csv(self.paths.pack)[1]]
        self.assertEqual(counts, sorted(counts, reverse=True))


class ExportJsonTest(MergeTestCase):
    """JSON 是 0 号的派生视图，必须与 CSV 一致。"""

    def test_export_matches_master(self) -> None:
        merge_master(self.paths, backup=False)
        build_layers(self.paths)

        payload = json.loads(
            (self.koolearn / "ielts_layered.json").read_text(encoding="utf-8")
        )
        _, rows = read_csv(self.paths.master)
        self.assertEqual(len(payload["words"]), len(rows))
        self.assertEqual(
            [w["word"] for w in payload["words"]], [r["word"] for r in rows]
        )

    def test_numeric_fields_are_typed(self) -> None:
        merge_master(self.paths, backup=False)
        build_layers(self.paths)
        payload = json.loads(
            (self.koolearn / "ielts_layered.json").read_text(encoding="utf-8")
        )
        alpha = next(w for w in payload["words"] if w["word"] == "alpha")
        self.assertIsInstance(alpha["ielts_books"], int)
        self.assertEqual(alpha["ielts_books"], 3)
        self.assertEqual(alpha["xdf"], 1)

    def test_meta_keeps_provenance_and_updates_counts(self) -> None:
        # 先放一份带 meta 的旧 JSON，模拟抓取侧产物
        original = {
            "meta": {
                "source": "https://www.koolearn.com/dict/",
                "ielts_book_count": 119,
                "counts": {"ielts_words": 26265, "union_words": 5},
            },
            "words": [],
        }
        (self.koolearn / "ielts_layered.json").write_text(
            json.dumps(original, ensure_ascii=False), encoding="utf-8"
        )

        merge_master(self.paths, backup=False)
        build_layers(self.paths)

        payload = json.loads(
            (self.koolearn / "ielts_layered.json").read_text(encoding="utf-8")
        )
        meta = payload["meta"]
        self.assertEqual(meta["source"], "https://www.koolearn.com/dict/")
        self.assertEqual(meta["ielts_book_count"], 119)          # 抓取侧信息保留
        self.assertEqual(meta["counts"]["union_words"], len(payload["words"]))
        self.assertEqual(meta["counts"]["ielts_words"], 26265)   # 不覆盖抓取口径
        self.assertEqual(meta["counts"]["xdf_words"], 6)

    def test_layers_report_mentions_export(self) -> None:
        merge_master(self.paths, backup=False)
        report = build_layers(self.paths)
        master_rows = len(read_csv(self.paths.master)[1])
        self.assertTrue(report.json_path.endswith("ielts_layered.json"))
        self.assertEqual(report.json_words, master_rows)
        self.assertTrue(any("JSON 导出" in line for line in report.summary_lines()))

    def test_standalone_export(self) -> None:
        merge_master(self.paths, backup=False)
        out = self.koolearn / "custom.json"
        info = export_master_json(self.paths, output=out)
        self.assertEqual(info["words"], 7)
        self.assertTrue(out.is_file())

    def test_export_survives_broken_existing_json(self) -> None:
        (self.koolearn / "ielts_layered.json").write_text("{ 坏文件", encoding="utf-8")
        merge_master(self.paths, backup=False)
        report = build_layers(self.paths)
        payload = json.loads(
            (self.koolearn / "ielts_layered.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(payload["words"]), report.json_words)
        self.assertIn("generated_at", payload["meta"])

    def test_export_is_stable_when_words_unchanged(self) -> None:
        """词表没变时不得重写文件——否则每次 layers/all 都产生无意义 diff。"""
        merge_master(self.paths, backup=False)
        build_layers(self.paths)
        target = self.koolearn / "ielts_layered.json"
        first = target.read_text(encoding="utf-8")

        second = export_master_json(self.paths)
        self.assertFalse(second["changed"])
        self.assertEqual(target.read_text(encoding="utf-8"), first)

        # 词表变了就必须重写，并刷新时间戳
        _, rows = read_csv(self.paths.master)
        write_csv(
            self.paths.master,
            list(rows[0].keys()),
            rows + [{**rows[0], "word": "zzz-brand-new"}],
        )
        third = export_master_json(self.paths)
        self.assertTrue(third["changed"])
        self.assertNotEqual(target.read_text(encoding="utf-8"), first)


class VerifyTest(MergeTestCase):
    def test_verify_passes_after_merge_and_layers(self) -> None:
        merge_master(self.paths, backup=False)
        build_layers(self.paths)
        self.assertEqual(verify(self.paths), [])

    def test_verify_catches_broken_pack(self) -> None:
        merge_master(self.paths, backup=False)
        build_layers(self.paths)
        fields, rows = read_csv(self.paths.pack)
        rows = rows[:-1]  # 抽掉一行
        write_csv(self.paths.pack, fields, rows)
        failures = verify(self.paths)
        self.assertTrue(any("2 号" in item for item in failures), failures)

    def test_verify_catches_tier_mismatch(self) -> None:
        fields, rows = read_csv(self.paths.master)
        rows[0]["tier"] = "S"
        write_csv(self.paths.master, fields, rows)
        failures = verify(self.paths)
        self.assertTrue(any("tier" in item for item in failures), failures)


if __name__ == "__main__":
    unittest.main()
