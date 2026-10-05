# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\tests\test_drill.py
"""认词判定测试：三态判定、状态持久化、一轮逻辑、合并清单、HTTP 接口。"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from lexicon.drill import (
    STATUS_KNOWN,
    STATUS_UNKNOWN,
    STATUS_UNSURE,
    STATUS_UNTESTED,
    DrillServer,
    DrillSession,
    DrillState,
)
from lexicon.paths import DataPaths
from lexicon.sources import read_csv, write_csv

PACK_FIELDS = [
    "word", "wd_id", "url", "layers", "layer_count", "tier", "ielts_books",
    "listen_books", "reading_books", "writing_books", "speaking_books",
    "in_base", "base_level", "sources", "xdf",
]

WORDS = ["alpha", "beta", "gamma", "delta", "epsilon"]


def make_pack(path: Path, words: list[str]) -> None:
    rows = [
        {
            "word": word,
            "wd_id": str(index),
            "url": "",
            "layers": "L2-listening",
            "layer_count": "1",
            "tier": "A",
            "ielts_books": "6",
            "listen_books": "6",
            "reading_books": "0",
            "writing_books": "0",
            "speaking_books": "0",
            "in_base": "0",
            "base_level": "0",
            "sources": "koolearn",
            "xdf": "0",
        }
        for index, word in enumerate(words, start=1)
    ]
    write_csv(path, PACK_FIELDS, rows)


def make_wordbook(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE word (id INTEGER PRIMARY KEY, lemma_key TEXT, display TEXT, kind TEXT);"
        "CREATE TABLE tag (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE word_tag (word_id INTEGER, tag_id INTEGER);"
    )
    conn.commit()
    conn.close()


class DrillTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.koolearn = self.root / "koolearn"
        self.koolearn.mkdir()
        self.wordbook = self.root / "wordbook.db"
        make_wordbook(self.wordbook)
        make_pack(self.koolearn / "2_study_pack_recommended.csv", WORDS)
        self.paths = DataPaths(root=self.root, koolearn=self.koolearn)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class DrillStateTest(DrillTestCase):
    def test_untested_by_default(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        self.assertEqual(state.status("alpha"), STATUS_UNTESTED)

    def test_mark_three_states_and_reload(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)
        state.mark("beta", STATUS_UNSURE, 1)
        state.mark("gamma", STATUS_KNOWN, 1)
        state.save()

        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.status("alpha"), STATUS_UNKNOWN)
        self.assertEqual(reloaded.status("beta"), STATUS_UNSURE)
        self.assertEqual(reloaded.status("gamma"), STATUS_KNOWN)
        self.assertEqual(reloaded.status("delta"), STATUS_UNTESTED)

    def test_counts_cover_three_states(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)
        state.mark("beta", STATUS_UNSURE, 1)
        state.mark("gamma", STATUS_KNOWN, 1)
        self.assertEqual(
            state.counts(WORDS),
            {STATUS_UNTESTED: 2, STATUS_UNKNOWN: 1, STATUS_UNSURE: 1, STATUS_KNOWN: 1},
        )

    def test_corrupt_file_falls_back_to_empty(self) -> None:
        self.paths.drill_state.write_text("{ not json", encoding="utf-8")
        state = DrillState.load(self.paths.drill_state)
        self.assertEqual(state.records, {})

    def test_save_leaves_no_temp_file(self) -> None:
        """落盘用「唯一临时名 + os.replace」，不该留下任何临时文件。

        回归：原来固定写 ``drill_state.tmp``，且 replace 不重试 —— Windows 上
        杀软/索引器短暂持有句柄时抛 PermissionError，用户答到一半就崩。
        """
        state = DrillState.load(self.paths.drill_state)
        for i in range(5):
            state.mark(f"word{i}", STATUS_UNKNOWN, 1)
            state.save()
        leftovers = [p.name for p in self.koolearn.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertTrue(self.paths.drill_state.is_file())

    def test_save_survives_a_transient_permission_error(self) -> None:
        """模拟一次瞬时的 WinError 5：应当重试成功，而不是崩掉整轮。"""
        from unittest import mock

        from lexicon import drill as D

        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)

        real_replace = D.os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(5, "拒绝访问。")
            return real_replace(src, dst)

        with mock.patch.object(D.os, "replace", side_effect=flaky_replace):
            state.save()

        self.assertGreaterEqual(calls["n"], 2)          # 确实重试过
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.status("alpha"), STATUS_UNKNOWN)

    def test_save_gives_up_and_cleans_temp_after_persistent_failure(self) -> None:
        from unittest import mock

        from lexicon import drill as D

        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)

        with mock.patch.object(
            D.os, "replace", side_effect=PermissionError(5, "拒绝访问。")
        ):
            with self.assertRaises(PermissionError):
                state.save()

        leftovers = [p.name for p in self.koolearn.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], "放弃时也要清掉临时文件")


class DrillSessionTest(DrillTestCase):
    def _session(self, limit: int = 100, retest: str = "none", count_unsure: bool = False):
        state = DrillState.load(self.paths.drill_state)
        return DrillSession(
            WORDS, state, limit=limit, retest=retest, count_unsure=count_unsure
        )

    def test_queue_is_untested_only(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_KNOWN, 1)
        state.mark("beta", STATUS_UNSURE, 1)
        session = DrillSession(WORDS, state)
        self.assertEqual(session.queue, ["gamma", "delta", "epsilon"])

    def test_each_status_persists_immediately(self) -> None:
        session = self._session()
        session.answer("alpha", STATUS_UNKNOWN)
        session.answer("beta", STATUS_UNSURE)
        session.answer("gamma", STATUS_KNOWN)
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.status("alpha"), STATUS_UNKNOWN)
        self.assertEqual(reloaded.status("beta"), STATUS_UNSURE)
        self.assertEqual(reloaded.status("gamma"), STATUS_KNOWN)
        self.assertEqual(reloaded.status("delta"), STATUS_UNTESTED)

    def test_three_buckets_are_separated(self) -> None:
        session = self._session()
        session.answer("alpha", STATUS_UNKNOWN)
        session.answer("beta", STATUS_UNSURE)
        session.answer("gamma", STATUS_KNOWN)
        self.assertEqual(session.unknown, ["alpha"])
        self.assertEqual(session.unsure, ["beta"])
        self.assertEqual(session.known, ["gamma"])
        self.assertEqual(session.tested, 3)

    def test_rejects_unknown_status_value(self) -> None:
        session = self._session()
        outcome = session.answer("alpha", "maybe")
        self.assertFalse(outcome["ok"])
        self.assertEqual(session.tested, 0)

    def test_limit_counts_only_unknown_by_default(self) -> None:
        session = self._session(limit=2)
        session.answer("alpha", STATUS_UNSURE)
        session.answer("beta", STATUS_UNSURE)
        session.answer("gamma", STATUS_UNSURE)
        self.assertFalse(session.finished)
        self.assertEqual(session.limit_cursor, 0)

    def test_limit_can_count_unsure(self) -> None:
        session = self._session(limit=2, count_unsure=True)
        session.answer("alpha", STATUS_UNSURE)
        self.assertFalse(session.finished)
        session.answer("beta", STATUS_UNSURE)
        self.assertTrue(session.finished)
        self.assertEqual(session.reason, "limit")
        self.assertIn("不熟 + 不认识", session.result()["reason_label"])

    def test_auto_finish_at_limit(self) -> None:
        session = self._session(limit=2)
        session.answer("alpha", STATUS_UNKNOWN)
        self.assertFalse(session.finished)
        session.answer("beta", STATUS_UNKNOWN)
        self.assertTrue(session.finished)
        self.assertEqual(session.reason, "limit")
        self.assertEqual(session.result()["comma"], "alpha,beta")

    def test_known_answers_do_not_trigger_the_limit(self) -> None:
        session = self._session(limit=2)
        for word in WORDS[:-1]:
            session.answer(word, STATUS_KNOWN)
        self.assertFalse(session.finished)

    def test_exhausted_when_queue_runs_out(self) -> None:
        session = self._session(limit=99)
        for word in WORDS:
            session.answer(word, STATUS_KNOWN)
        self.assertTrue(session.finished)
        self.assertEqual(session.reason, "exhausted")

    def test_stop_keeps_only_tested_words(self) -> None:
        session = self._session(limit=99)
        session.answer("alpha", STATUS_UNKNOWN)
        session.answer("beta", STATUS_KNOWN)
        result = session.stop()
        self.assertEqual(result["reason"], "stopped")
        self.assertEqual(result["comma"], "alpha")

        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.status("alpha"), STATUS_UNKNOWN)
        self.assertEqual(reloaded.status("gamma"), STATUS_UNTESTED)

    def test_out_of_sync_answer_is_rejected(self) -> None:
        session = self._session()
        outcome = session.answer("gamma", STATUS_KNOWN)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["expected"], "alpha")
        self.assertEqual(session.tested, 0)

    def test_next_round_picks_up_remaining_untested(self) -> None:
        session = self._session(limit=1)
        session.answer("alpha", STATUS_UNKNOWN)
        self.assertTrue(session.finished)

        state = DrillState.load(self.paths.drill_state)
        follow_up = DrillSession(WORDS, state, limit=100)
        self.assertEqual(follow_up.queue, ["beta", "gamma", "delta", "epsilon"])

    def test_retest_unlearned_requeues_unknown_and_unsure(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)
        state.mark("beta", STATUS_UNSURE, 1)
        state.mark("gamma", STATUS_KNOWN, 1)
        session = DrillSession(WORDS, state, retest="unlearned")
        # 队列按词池顺序：alpha(不认识) beta(不熟) delta epsilon
        self.assertEqual(session.queue, ["alpha", "beta", "delta", "epsilon"])

    def test_retest_all_requeues_everything(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)
        state.mark("beta", STATUS_KNOWN, 1)
        session = DrillSession(WORDS, state, retest="all")
        self.assertEqual(session.queue, WORDS)

    def test_round_history_recorded_with_breakdown(self) -> None:
        session = self._session(limit=1)
        session.answer("alpha", STATUS_UNKNOWN)
        rounds = json.loads(self.paths.drill_state.read_text(encoding="utf-8"))["rounds"]
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["unknown_words"], ["alpha"])
        self.assertEqual(rounds[0]["unsure"], 0)
        self.assertEqual(rounds[0]["known"], 0)


class MergedOutputTest(DrillTestCase):
    """结束本轮时，「不认识」与「见过但不熟」合并成一个清单输出。"""

    def test_merged_list_puts_unknown_before_unsure(self) -> None:
        state = DrillState.load(self.paths.drill_state)
        session = DrillSession(WORDS, state, limit=99)
        session.answer("alpha", STATUS_UNSURE)
        session.answer("beta", STATUS_UNKNOWN)
        session.answer("gamma", STATUS_KNOWN)
        session.answer("delta", STATUS_UNSURE)
        session.answer("epsilon", STATUS_UNKNOWN)
        result = session.stop()

        self.assertEqual(result["unknown_count"], 2)
        self.assertEqual(result["unsure_count"], 2)
        self.assertEqual(result["known_count"], 1)
        # 不认识在前，不熟在后；认识的不进清单
        self.assertEqual(result["merged_words"], ["beta", "epsilon", "alpha", "delta"])
        self.assertEqual(result["merged_comma"], "beta,epsilon,alpha,delta")

    def test_merged_output_is_a_single_file(self) -> None:
        server = DrillServer(self.paths, pool="pack", limit=99, port=0, quiet=True)
        server.answer("alpha", STATUS_UNSURE)
        server.answer("beta", STATUS_UNKNOWN)
        server.answer("gamma", STATUS_KNOWN)
        server.stop()

        files = sorted(p.name for p in self.koolearn.glob("drill_*.txt"))
        self.assertEqual(len(files), 1, files)
        self.assertTrue(files[0].startswith("drill_unlearned_r"), files[0])
        content = (self.koolearn / files[0]).read_text(encoding="utf-8").strip()
        self.assertEqual(content, "beta,alpha")

    def test_no_file_when_everything_is_known(self) -> None:
        server = DrillServer(self.paths, pool="pack", limit=99, port=0, quiet=True)
        for word in WORDS:
            server.answer(word, STATUS_KNOWN)
        self.assertEqual(list(self.koolearn.glob("drill_*.txt")), [])

    def test_result_payload_keeps_per_status_lists(self) -> None:
        """合并输出之外，分档明细仍要保留，便于程序消费。"""
        state = DrillState.load(self.paths.drill_state)
        session = DrillSession(WORDS, state, limit=99)
        session.answer("alpha", STATUS_UNKNOWN)
        session.answer("beta", STATUS_UNSURE)
        result = session.stop()
        self.assertEqual(result["unknown_words"], ["alpha"])
        self.assertEqual(result["unsure_words"], ["beta"])
        self.assertEqual(result["comma"], "alpha")
        self.assertEqual(result["unsure_comma"], "beta")


class UndoTest(DrillTestCase):
    """回溯：退回上一个词并允许改判。"""

    def _session(self, limit: int = 100, retest: str = "none"):
        state = DrillState.load(self.paths.drill_state)
        return DrillSession(WORDS, state, limit=limit, retest=retest), state

    def test_undo_returns_word_to_untested_when_never_judged(self) -> None:
        session, _ = self._session()
        session.answer("alpha", STATUS_UNKNOWN)
        outcome = session.undo()
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["word"], "alpha")
        self.assertEqual(outcome["undone"][0]["undone_status"], STATUS_UNKNOWN)
        self.assertEqual(outcome["restored_status"], STATUS_UNTESTED)
        # 从未判定过的词，回溯后记录应被删除而不是留个 untested
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertNotIn("alpha", reloaded.records)
        self.assertEqual(reloaded.status("alpha"), STATUS_UNTESTED)

    def test_undo_makes_the_word_current_again(self) -> None:
        session, _ = self._session()
        session.answer("alpha", STATUS_UNKNOWN)
        self.assertEqual(session.current(), "beta")
        session.undo()
        self.assertEqual(session.current(), "alpha")
        self.assertEqual(session.tested, 0)

    def test_can_redo_with_a_different_status(self) -> None:
        session, _ = self._session()
        session.answer("alpha", STATUS_UNKNOWN)
        session.undo()
        session.answer("alpha", STATUS_KNOWN)
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.status("alpha"), STATUS_KNOWN)
        self.assertEqual(session.unknown, [])
        self.assertEqual(session.known, ["alpha"])

    def test_undo_recomputes_buckets_and_limit_cursor(self) -> None:
        session, _ = self._session(limit=2)
        session.answer("alpha", STATUS_UNKNOWN)
        session.answer("beta", STATUS_UNKNOWN)
        self.assertTrue(session.finished)
        self.assertEqual(session.limit_cursor, 2)

        session.undo()
        self.assertFalse(session.finished)
        self.assertEqual(session.limit_cursor, 1)
        self.assertEqual(session.unknown, ["alpha"])
        self.assertEqual(session.unsure, [])

    def test_undo_reopens_finished_round_and_drops_its_record(self) -> None:
        session, state = self._session(limit=1)
        session.answer("alpha", STATUS_UNKNOWN)
        self.assertTrue(session.finished)
        self.assertEqual(len(state.rounds), 1)

        outcome = session.undo()
        self.assertFalse(session.finished)
        self.assertEqual(session.reason, "")
        self.assertTrue(outcome["reopened"])
        self.assertEqual(len(state.rounds), 0)
        self.assertEqual(session.round_no, 1)

    def test_can_walk_back_through_the_whole_round(self) -> None:
        session, _ = self._session(limit=99)
        for word, status in zip(
            WORDS,
            [STATUS_UNKNOWN, STATUS_UNSURE, STATUS_KNOWN, STATUS_UNSURE, STATUS_UNKNOWN],
        ):
            session.answer(word, status)
        self.assertEqual(session.tested, 5)

        while session.can_undo:      # 一直退到本轮起点
            session.undo()
        self.assertEqual(session.tested, 0)
        self.assertEqual(session.current(), "alpha")
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertTrue(all(reloaded.status(w) == STATUS_UNTESTED for w in WORDS))
        self.assertEqual(reloaded.records, {})

    def test_undo_at_round_start_is_rejected(self) -> None:
        session, _ = self._session()
        outcome = session.undo()
        self.assertFalse(outcome["ok"])
        self.assertIn("起点", outcome["error"])

    def test_undo_multiple_steps_returns_the_oldest_as_current(self) -> None:
        session, _ = self._session()
        for word in WORDS[:3]:
            session.answer(word, STATUS_UNKNOWN)
        outcome = session.undo(steps=3)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["steps"], 3)
        self.assertEqual(outcome["word"], "alpha")   # 退回最靠前的那个
        self.assertEqual(session.current(), "alpha")
        self.assertEqual(session.tested, 0)

    def test_undo_steps_larger_than_stack_is_clamped(self) -> None:
        session, _ = self._session()
        session.answer("alpha", STATUS_UNKNOWN)
        outcome = session.undo(steps=99)
        self.assertEqual(outcome["steps"], 1)
        self.assertEqual(session.tested, 0)

    def test_undo_restores_prior_status_in_retest_mode(self) -> None:
        """重测模式下回溯，应还原到本轮之前的状态，而不是抹成「待检测」。"""
        state = DrillState.load(self.paths.drill_state)
        state.mark("alpha", STATUS_UNKNOWN, 1)
        state.save()

        session = DrillSession(WORDS, state, limit=99, retest="unlearned")
        self.assertIn("alpha", session.queue)
        session.answer("alpha", STATUS_KNOWN)
        self.assertEqual(
            DrillState.load(self.paths.drill_state).status("alpha"), STATUS_KNOWN
        )

        session.undo()
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.status("alpha"), STATUS_UNKNOWN)
        self.assertEqual(reloaded.records["alpha"]["round"], 1)

    def test_undo_all_the_way_leaves_a_clean_state_file(self) -> None:
        session, _ = self._session()
        session.answer("alpha", STATUS_UNSURE)
        session.answer("beta", STATUS_KNOWN)
        session.undo()
        session.undo()
        reloaded = DrillState.load(self.paths.drill_state)
        self.assertEqual(reloaded.records, {})
        self.assertEqual(reloaded.rounds, [])


class DrillHttpTest(DrillTestCase):
    """真起一个 HTTP 服务，走完整判定流程。"""

    def setUp(self) -> None:
        super().setUp()
        self.server = DrillServer(self.paths, pool="pack", limit=2, port=0, quiet=True)
        self.httpd = self.server.make_httpd()
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_page_has_three_option_keys(self) -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("雅思认词判定", html)
        self.assertIn("不认识（左）", html)
        self.assertIn("见过但不熟（下）", html)
        self.assertIn("认识（右）", html)
        self.assertIn('const LIMIT = 2;', html)
        self.assertIn("const COUNT_UNSURE = false;", html)
        self.assertNotIn("__LIMIT__", html)
        self.assertNotIn("__COUNT_UNSURE__", html)

    def test_next_returns_first_word(self) -> None:
        payload = self._get("/api/next")
        self.assertFalse(payload["finished"])
        self.assertEqual(payload["word"], "alpha")
        self.assertEqual(payload["session"]["round_size"], 5)
        self.assertEqual(payload["session"]["unsure_count"], 0)

    def test_full_round_auto_finishes_and_writes_merged_file(self) -> None:
        """limit=2 只统计「不认识」，所以要两个 unknown 才收工。"""
        self.assertEqual(self._get("/api/next")["word"], "alpha")
        first = self._post("/api/answer", {"word": "alpha", "status": "unsure"})
        self.assertFalse(first["session"]["finished"])
        self.assertEqual(first["session"]["unsure_count"], 1)
        self.assertEqual(first["session"]["limit_cursor"], 0)

        second = self._post("/api/answer", {"word": "beta", "status": "unknown"})
        self.assertFalse(second["session"]["finished"])
        self.assertEqual(second["session"]["limit_cursor"], 1)

        third = self._post("/api/answer", {"word": "gamma", "status": "unknown"})
        self.assertTrue(third["session"]["finished"])
        # 不认识在前，不熟在后
        self.assertEqual(third["result"]["merged_comma"], "beta,gamma,alpha")

        files = list(self.koolearn.glob("drill_unlearned_r*.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8").strip(), "beta,gamma,alpha")
        self.assertTrue(self._get("/api/next")["finished"])

    def test_count_unsure_flag_changes_stop_condition(self) -> None:
        server = DrillServer(
            self.paths, pool="pack", limit=2, port=0, quiet=True, count_unsure=True
        )
        httpd = server.make_httpd()
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            def post(payload):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/answer",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            post({"word": "alpha", "status": "unsure"})
            payload = post({"word": "beta", "status": "unsure"})
            self.assertTrue(payload["session"]["finished"])
            self.assertEqual(payload["result"]["merged_comma"], "alpha,beta")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_stop_endpoint_returns_merged_comma_list(self) -> None:
        self._post("/api/answer", {"word": "alpha", "status": "unknown"})
        self._post("/api/answer", {"word": "beta", "status": "unsure"})
        result = self._post("/api/stop", {})
        self.assertEqual(result["reason"], "stopped")
        self.assertEqual(result["merged_comma"], "alpha,beta")
        self.assertEqual(result["unknown_count"], 1)
        self.assertEqual(result["unsure_count"], 1)

    def test_invalid_status_is_rejected(self) -> None:
        outcome = self._post("/api/answer", {"word": "alpha", "status": "nope"})
        self.assertFalse(outcome["ok"])
        self.assertEqual(self._get("/api/session")["tested"], 0)

    def test_restart_resumes_with_untested_words(self) -> None:
        self._post("/api/answer", {"word": "alpha", "status": "unknown"})
        self._post("/api/stop", {})
        payload = self._post("/api/restart", {})
        self.assertEqual(payload["session"]["round_size"], 4)
        self.assertEqual(self._get("/api/next")["word"], "beta")

    def test_page_has_undo_affordances(self) -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn('id="btn-undo"', html)
        self.assertIn("Backspace", html)
        self.assertIn("ArrowLeft", html)
        self.assertIn('id="hint"', html)

    def test_undo_endpoint_rewinds_and_allows_rejudging(self) -> None:
        self._post("/api/answer", {"word": "alpha", "status": "unknown"})
        self.assertEqual(self._get("/api/next")["word"], "beta")

        outcome = self._post("/api/undo", {})
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["word"], "alpha")
        self.assertEqual(outcome["restored_status"], "untested")
        self.assertEqual(outcome["session"]["tested"], 0)
        self.assertEqual(outcome["session"]["can_undo"], False)
        self.assertEqual(self._get("/api/next")["word"], "alpha")

        # 改判成「认识」
        self._post("/api/answer", {"word": "alpha", "status": "known"})
        session = self._get("/api/session")
        self.assertEqual(session["known_count"], 1)
        self.assertEqual(session["unknown_count"], 0)

    def test_undo_after_stop_takes_back_the_round(self) -> None:
        self._post("/api/answer", {"word": "alpha", "status": "unknown"})
        self._post("/api/answer", {"word": "beta", "status": "unsure"})
        self._post("/api/stop", {})
        self.assertTrue(self._get("/api/next")["finished"])

        outcome = self._post("/api/undo", {})
        self.assertTrue(outcome["ok"])
        self.assertFalse(self._get("/api/next")["finished"])
        session = self._get("/api/session")
        self.assertEqual(session["unsure_count"], 0)
        self.assertEqual(session["unknown_count"], 1)

    def test_undo_at_start_returns_error_but_keeps_game_running(self) -> None:
        outcome = self._post("/api/undo", {})
        self.assertFalse(outcome["ok"])
        self.assertEqual(self._get("/api/next")["word"], "alpha")

    def test_undo_multiple_steps(self) -> None:
        for word, status in (("alpha", "unknown"), ("beta", "unsure"), ("gamma", "known")):
            self._post("/api/answer", {"word": word, "status": status})
        outcome = self._post("/api/undo", {"steps": 2})
        self.assertEqual(outcome["steps"], 2)
        self.assertEqual(outcome["word"], "beta")
        self.assertEqual(self._get("/api/next")["word"], "beta")
        session = self._get("/api/session")
        self.assertEqual(session["tested"], 1)
        self.assertEqual(session["known_count"], 0)

    def test_restart_round_still_writes_a_result_file(self) -> None:
        """回归：restart 后新一轮收工时必须照常输出（曾被 _emitted_round 吞掉）。"""
        self._post("/api/answer", {"word": "alpha", "status": "unknown"})
        self._post("/api/stop", {})
        self.assertEqual(len(list(self.koolearn.glob("drill_unlearned_r*.txt"))), 1)

        self._post("/api/restart", {})
        self._post("/api/answer", {"word": "beta", "status": "unknown"})
        self._post("/api/answer", {"word": "gamma", "status": "unknown"})
        files = sorted(p.name for p in self.koolearn.glob("drill_unlearned_r*.txt"))
        self.assertEqual(len(files), 2, files)
        self.assertTrue(any(name.startswith("drill_unlearned_r2_") for name in files), files)

    def test_unknown_path_returns_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/nope")
        code = ctx.exception.code
        ctx.exception.close()
        self.assertEqual(code, 404)

    def test_state_survives_server_restart(self) -> None:
        self._post("/api/answer", {"word": "alpha", "status": "unsure"})
        self._post("/api/stop", {})

        fresh = DrillServer(self.paths, pool="pack", limit=2, port=0, quiet=True)
        self.assertEqual(fresh.session.queue, ["beta", "gamma", "delta", "epsilon"])
        overall = fresh.session.payload()["overall"]
        self.assertEqual(overall[STATUS_UNSURE], 1)
        self.assertEqual(overall[STATUS_UNTESTED], 4)


class PoolLoadingTest(DrillTestCase):
    def test_pack_pool_order_preserved(self) -> None:
        server = DrillServer(self.paths, pool="pack", limit=100, port=0, quiet=True)
        self.assertEqual(server.words, WORDS)

    def test_duplicate_words_are_collapsed(self) -> None:
        make_pack(self.koolearn / "2_study_pack_recommended.csv", ["alpha", "alpha", "Beta", "beta"])
        server = DrillServer(self.paths, pool="pack", limit=100, port=0, quiet=True)
        self.assertEqual(server.words, ["alpha", "Beta"])

    def test_unknown_pool_raises(self) -> None:
        with self.assertRaises(ValueError):
            DrillServer(self.paths, pool="nosuchpool", limit=100, port=0, quiet=True)


if __name__ == "__main__":
    unittest.main()
