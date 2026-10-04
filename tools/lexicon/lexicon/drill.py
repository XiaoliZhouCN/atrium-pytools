# D:\Repositories\Manager\AtriumPyTools\tools\lexicon\lexicon\drill.py
"""A/D 认词判定网页工具。

* 只显示单词本身，不带释义（用户要求）
* 键盘 A = 不认识（左） / D = 认识（右），Esc = 停止本轮
* 累计「不认识」达到 ``--limit``（默认 100）自动结束一轮
* 判定结果**逐词立即落盘**：中途停止只更新已判定的词，未判定的保持「待检测」
* 一轮结束时输出不认识单词（逗号分隔），同时写文件并在控制台打印

实现只用标准库：``http.server`` + 单文件 HTML，无第三方依赖、无 UI 框架。
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import paths as PP
from .sources import exact_key, load_pool

STATUS_UNTESTED = "untested"
STATUS_UNKNOWN = "unknown"
STATUS_UNSURE = "unsure"
STATUS_KNOWN = "known"

#: 一轮内可以作出的判定：A = 不认识 / S = 见过但不熟 / D = 认识
ANSWERABLE = (STATUS_UNKNOWN, STATUS_UNSURE, STATUS_KNOWN)

STATUS_LABEL = {
    STATUS_UNTESTED: "待检测",
    STATUS_UNKNOWN: "不认识",
    STATUS_UNSURE: "见过但不熟",
    STATUS_KNOWN: "认识",
}

REASON_LABELS = {
    "limit": "达到停止上限",
    "exhausted": "待检测词已全部判完",
    "stopped": "手动停止",
}

DEFAULT_LIMIT = 100


def reason_label(
    reason: str,
    unknown_count: int = 0,
    unsure_count: int = 0,
    count_unsure: bool = False,
) -> str:
    """一轮结束原因的可读描述。"""
    if reason == "limit":
        if count_unsure:
            return (
                f"不熟 + 不认识累计达到 {unknown_count + unsure_count} 个"
                f"（不认识 {unknown_count} ｜ 见过但不熟 {unsure_count}）"
            )
        return f"不认识累计达到 {unknown_count} 个"
    return REASON_LABELS.get(reason, reason)


def _now() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# 判定状态（持久化）
# --------------------------------------------------------------------------- #


@dataclass
class DrillState:
    path: Path
    pool: str = ""
    records: dict[str, dict] = field(default_factory=dict)
    rounds: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "DrillState":
        if not path.is_file():
            return cls(path=path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path=path)
        return cls(
            path=path,
            pool=str(payload.get("pool", "")),
            records=dict(payload.get("records") or {}),
            rounds=list(payload.get("rounds") or []),
        )

    def save(self) -> None:
        payload = {
            "version": 1,
            "pool": self.pool,
            "updated_at": _now(),
            "records": self.records,
            "rounds": self.rounds,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.path)

    def status(self, word: str) -> str:
        record = self.records.get(word)
        return record["status"] if record else STATUS_UNTESTED

    def mark(self, word: str, status: str, round_no: int) -> None:
        self.records[word] = {"status": status, "round": round_no, "at": _now()}

    def snapshot(self, word: str) -> dict | None:
        """取某词判定前的记录（供回溯还原）；从未判定过则返回 None。"""
        record = self.records.get(word)
        return dict(record) if record else None

    def restore(self, word: str, snapshot: dict | None) -> None:
        """把某词还原到快照状态；快照为 None 表示它当时还没被判定过。"""
        if snapshot is None:
            self.records.pop(word, None)
        else:
            self.records[word] = dict(snapshot)

    def counts(self, words: list[str]) -> dict[str, int]:
        tally = {
            STATUS_UNTESTED: 0,
            STATUS_UNKNOWN: 0,
            STATUS_UNSURE: 0,
            STATUS_KNOWN: 0,
        }
        for word in words:
            tally[self.status(word)] += 1
        return tally


# --------------------------------------------------------------------------- #
# 一轮会话
# --------------------------------------------------------------------------- #


@dataclass
class RoundResult:
    round_no: int
    reason: str
    unknown: list[str]
    unsure: list[str]
    known: list[str]
    count_unsure: bool = False
    finished_at: str = field(default_factory=_now)

    @property
    def comma(self) -> str:
        """只看「不认识」的逗号分隔串（分档保留，供程序消费）。"""
        return ",".join(self.unknown)

    @property
    def unsure_comma(self) -> str:
        return ",".join(self.unsure)

    @property
    def merged(self) -> list[str]:
        """本轮要复习的词：「不认识」在前，「见过但不熟」在后。"""
        return list(self.unknown) + list(self.unsure)

    @property
    def merged_comma(self) -> str:
        """结束本轮时输出的合并清单（逗号分隔）。"""
        return ",".join(self.merged)

    @property
    def reason_label(self) -> str:
        return reason_label(
            self.reason, len(self.unknown), len(self.unsure), self.count_unsure
        )


class DrillSession:
    """一轮判定。状态由外部持锁保护。

    本轮的判定结果存成**栈**（``answers``），因此可以逐词回溯：
    出栈 + 把该词还原到判定前的状态。回溯深度不限，一直到本轮第一个词为止；
    若本轮已经结束（达到上限或手动停止），回溯会把它重新打开。
    """

    def __init__(
        self,
        words: list[str],
        state: DrillState,
        limit: int = DEFAULT_LIMIT,
        retest: str = "none",
        count_unsure: bool = False,
    ) -> None:
        self.state = state
        self.limit = limit
        self.all_words = words
        #: 「见过但不熟」是否也计入停止上限。默认只统计「不认识」。
        self.count_unsure = count_unsure

        wanted = {STATUS_UNTESTED}
        if retest == "unlearned":
            wanted.update({STATUS_UNKNOWN, STATUS_UNSURE})
        elif retest == "unknown":
            wanted.add(STATUS_UNKNOWN)
        elif retest == "all":
            wanted.update({STATUS_KNOWN, STATUS_UNKNOWN, STATUS_UNSURE})
        self.queue = [w for w in words if state.status(w) in wanted]

        #: 本轮的判定栈：[{"word","status","prev"}]，``prev`` 是判定前的记录快照
        self.answers: list[dict] = []
        self.finished = False
        self.reason = ""
        self.round_no = len(state.rounds) + 1
        self.started_at = _now()

    # -- 查询 ------------------------------------------------------------- #

    @property
    def cursor(self) -> int:
        """本轮已判定的词数，也就是队列位置。"""
        return len(self.answers)

    def _words_with(self, status: str) -> list[str]:
        return [a["word"] for a in self.answers if a["status"] == status]

    @property
    def unknown(self) -> list[str]:
        return self._words_with(STATUS_UNKNOWN)

    @property
    def unsure(self) -> list[str]:
        return self._words_with(STATUS_UNSURE)

    @property
    def known(self) -> list[str]:
        return self._words_with(STATUS_KNOWN)

    @property
    def tested(self) -> int:
        return len(self.answers)

    @property
    def limit_cursor(self) -> int:
        """计入停止上限的累计数。"""
        return len(self.unknown) + (len(self.unsure) if self.count_unsure else 0)

    @property
    def merged(self) -> list[str]:
        """本轮要复习的词：「不认识」在前，「见过但不熟」在后。"""
        return self.unknown + self.unsure

    @property
    def merged_comma(self) -> str:
        return ",".join(self.merged)

    @property
    def can_undo(self) -> bool:
        return bool(self.answers)

    def current(self) -> str | None:
        if self.finished or self.cursor >= len(self.queue):
            return None
        return self.queue[self.cursor]

    def payload(self) -> dict:
        return {
            "pool": self.state.pool,
            "limit": self.limit,
            "count_unsure": self.count_unsure,
            "limit_cursor": self.limit_cursor,
            "round_no": self.round_no,
            "round_size": len(self.queue),
            "tested": self.tested,
            "unknown_count": len(self.unknown),
            "unsure_count": len(self.unsure),
            "known_count": len(self.known),
            "finished": self.finished,
            "reason": self.reason,
            "remaining": max(0, len(self.queue) - self.cursor),
            "can_undo": self.can_undo,
            "undo_depth": self.cursor,
            "overall": self.state.counts(self.all_words),
            "total": len(self.all_words),
        }

    def result(self) -> dict:
        return {
            "round_no": self.round_no,
            "reason": self.reason,
            "reason_label": reason_label(
                self.reason, len(self.unknown), len(self.unsure), self.count_unsure
            )
            if self.finished
            else "",
            "tested": self.tested,
            "unknown_count": len(self.unknown),
            "unsure_count": len(self.unsure),
            "known_count": len(self.known),
            "unknown_words": self.unknown,
            "unsure_words": self.unsure,
            "merged_words": self.merged,
            "comma": ",".join(self.unknown),
            "unsure_comma": ",".join(self.unsure),
            #: 收工时输出的清单：不认识 + 见过但不熟，合并成一个
            "merged_comma": self.merged_comma,
        }

    # -- 推进与回溯 ------------------------------------------------------- #

    def answer(self, word: str, status: str) -> dict:
        """对当前词作出判定：``unknown`` / ``unsure`` / ``known``。"""
        if self.finished:
            return {"ok": False, "error": "本轮已结束"}
        if status not in ANSWERABLE:
            return {"ok": False, "error": f"未知判定 {status!r}"}
        current = self.current()
        if current is None:
            self._finish("exhausted")
            return {"ok": False, "error": "没有待判定的词"}
        if exact_key(word) != exact_key(current):
            # 客户端与服务端不同步：不消耗这一词，让前端重新拉取
            return {"ok": False, "error": "不同步", "expected": current}

        # 逐词立即落盘：中途停止只影响已判定的词
        self.answers.append(
            {"word": current, "status": status, "prev": self.state.snapshot(current)}
        )
        self.state.mark(current, status, self.round_no)
        self.state.save()

        if self.limit_cursor >= self.limit:
            self._finish("limit")
        elif self.cursor >= len(self.queue):
            self._finish("exhausted")
        return {"ok": True}

    def undo(self, steps: int = 1) -> dict:
        """退回上 ``steps`` 个词，把它们还原到判定前的状态。

        被退回的词重新成为当前词，可以改判。已结束的本轮会被重新打开。
        """
        if not self.answers:
            return {"ok": False, "error": "已经回到本轮起点，没有更早的词了"}

        steps = max(1, min(int(steps), len(self.answers)))
        undone: list[dict] = []
        for _ in range(steps):
            entry = self.answers.pop()
            self.state.restore(entry["word"], entry["prev"])
            undone.append(
                {
                    "word": entry["word"],
                    "undone_status": entry["status"],
                    "restored_status": (entry["prev"] or {}).get(
                        "status", STATUS_UNTESTED
                    ),
                }
            )

        if self.finished:
            # 本轮是被「上限」或「手动停止」关掉的，回溯即重新打开它
            self.finished = False
            self.reason = ""
            if self.state.rounds and self.state.rounds[-1].get("round") == self.round_no:
                self.state.rounds.pop()

        self.state.save()
        target = undone[-1]
        return {
            "ok": True,
            "steps": len(undone),
            "word": target["word"],
            "undone": list(reversed(undone)),
            "restored_status": target["restored_status"],
            "undo_depth": self.cursor,
            "reopened": not self.finished,
        }

    def stop(self) -> dict:
        if not self.finished:
            self._finish("stopped")
        return self.result()

    def _finish(self, reason: str) -> None:
        self.finished = True
        self.reason = reason
        self.state.rounds.append(
            {
                "round": self.round_no,
                "started_at": self.started_at,
                "finished_at": _now(),
                "reason": reason,
                "count_unsure": self.count_unsure,
                "tested": self.tested,
                "known": len(self.known),
                "unsure": len(self.unsure),
                "unknown": len(self.unknown),
                "unknown_words": self.unknown,
                "unsure_words": self.unsure,
            }
        )
        self.state.save()


def write_result_file(state: DrillState, result: dict) -> Path | None:
    """把本轮要复习的词写到数据目录。

    「不认识」与「见过但不熟」**合并成一个清单**（不认识在前），逗号分隔一行，
    方便直接拿去当背诵目标。分档计数保留在 ``drill_state.json`` 的轮次历史里。
    """
    if not result["merged_words"]:
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = state.path.parent / f"drill_unlearned_r{result['round_no']}_{stamp}.txt"
    path.write_text(result["merged_comma"] + "\n", encoding="utf-8")
    return path


def print_round_result(result: dict, path: Path | None = None) -> None:
    bar = "=" * 68
    print(f"\n{bar}")
    print(f"第 {result['round_no']} 轮结束 —— {result['reason_label']}")
    print(
        f"已判定 {result['tested']} ｜ 认识 {result['known_count']}"
        f" ｜ 见过但不熟 {result['unsure_count']} ｜ 不认识 {result['unknown_count']}"
    )
    print(bar)
    if result["merged_words"]:
        print(
            f"本轮要复习的词（不认识 {result['unknown_count']} + 不熟 "
            f"{result['unsure_count']} = {len(result['merged_words'])}，逗号分隔）："
        )
        print(result["merged_comma"])
    else:
        print("本轮没有需要复习的词。")
    if path is not None:
        print(f"\n已写入：{path}")
    print(bar + "\n")


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    server_version = "lexicon-drill"

    def log_message(self, *args) -> None:  # noqa: D102 - 静音默认请求日志
        return

    # -- 工具 ------------------------------------------------------------- #

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    @property
    def app(self) -> "DrillServer":
        return self.server.drill  # type: ignore[attr-defined]

    # -- 路由 ------------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(
                200,
                self.app.page().encode("utf-8"),
                "text/html; charset=utf-8",
            )
        elif path == "/api/session":
            self._json(self.app.session_payload())
        elif path == "/api/next":
            self._json(self.app.next_payload())
        elif path == "/api/result":
            self._json(self.app.result_payload())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        data = self._read_json()
        if path == "/api/answer":
            self._json(self.app.answer(data.get("word", ""), data.get("status", "")))
        elif path == "/api/undo":
            self._json(self.app.undo(int(data.get("steps") or 1)))
        elif path == "/api/stop":
            self._json(self.app.stop())
        elif path == "/api/restart":
            self._json(self.app.restart())
        else:
            self._json({"error": "not found"}, 404)


class DrillServer:
    """把词池、状态、HTTP 服务绑在一起。"""

    def __init__(
        self,
        paths: PP.DataPaths,
        pool: str = "pack",
        limit: int = DEFAULT_LIMIT,
        retest: str = "none",
        host: str = "127.0.0.1",
        port: int = 8765,
        quiet: bool = False,
        count_unsure: bool = False,
    ) -> None:
        self.paths = paths
        self.pool_name = pool
        self.limit = limit
        self.retest = retest
        self.host = host
        self.port = port
        self.quiet = quiet
        self.count_unsure = count_unsure

        rows = load_pool(pool, paths)
        # 去重但保序（词池里同一词头可能因大小写/空白差异重复）
        seen: set[str] = set()
        words: list[str] = []
        for row in rows:
            word = (row.get("word") or "").strip()
            if not word:
                continue
            key = exact_key(word)
            if key in seen:
                continue
            seen.add(key)
            words.append(word)
        self.words = words

        self.lock = threading.Lock()
        self._emitted_round = 0
        self.state = DrillState.load(paths.drill_state)
        self.state.pool = pool
        self.session = DrillSession(
            self.words,
            self.state,
            limit=limit,
            retest=retest,
            count_unsure=count_unsure,
        )

    # -- 状态 ------------------------------------------------------------- #

    def session_payload(self) -> dict:
        with self.lock:
            return self.session.payload()

    def next_payload(self) -> dict:
        with self.lock:
            if self.session.finished:
                return {
                    "finished": True,
                    "word": None,
                    "result": self.session.result(),
                    "session": self.session.payload(),
                }
            return {
                "finished": False,
                "word": self.session.current(),
                "result": None,
                "session": self.session.payload(),
            }

    def result_payload(self) -> dict:
        with self.lock:
            return self.session.result()

    def answer(self, word: str, status: str) -> dict:
        emit: dict | None = None
        with self.lock:
            outcome = self.session.answer(word, status)
            finished = self.session.finished
            payload = self.session.payload()
            result = self.session.result() if finished else None
            if outcome.get("ok") and finished and self._emitted_round != self.session.round_no:
                self._emitted_round = self.session.round_no
                emit = result
        if emit is not None:
            self._emit_result(emit)
        return {**outcome, "session": payload, "result": result}

    def stop(self) -> dict:
        emit: dict | None = None
        with self.lock:
            result = self.session.stop()
            if self._emitted_round != self.session.round_no:
                self._emitted_round = self.session.round_no
                emit = result
        if emit is not None:
            self._emit_result(emit)
        return result

    def undo(self, steps: int = 1) -> dict:
        """退回上一个（或上 N 个）已判定的词，允许改判。"""
        with self.lock:
            outcome = self.session.undo(steps)
            if outcome.get("ok"):
                # 本轮被重新打开：撤销「已结束」的标记，让再次收工时能重新输出
                self._emitted_round = 0
            payload = self.session.payload()
        return {**outcome, "session": payload}

    def restart(self) -> dict:
        with self.lock:
            self.session = DrillSession(
                self.words,
                self.state,
                limit=self.limit,
                retest=self.retest,
                count_unsure=self.count_unsure,
            )
            # 归零而不是设成新轮号：否则新一轮收工时会被认为「已输出过」而不再输出
            self._emitted_round = 0
            payload = self.session.payload()
        return {"restarted": True, "session": payload}

    def _emit_result(self, result: dict | None) -> None:
        if not result:
            return
        path = write_result_file(self.state, result)
        if not self.quiet:
            print_round_result(result, path)

    # -- 服务 ------------------------------------------------------------- #

    def make_httpd(self) -> ThreadingHTTPServer:
        """构造 HTTP 服务（端口传 0 可拿随机空闲端口，便于测试）。"""
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.drill = self  # type: ignore[attr-defined]
        return httpd

    def serve(self) -> None:
        httpd = self.make_httpd()
        self.port = httpd.server_address[1]
        url = f"http://{self.host}:{self.port}/"
        pending = len(self.session.queue)
        overall = self.session.payload()["overall"]
        # flush：输出被重定向时 stdout 是块缓冲的，否则横幅要等退出才出现
        print("=" * 68, flush=True)
        print("雅思认词判定（A = 不认识 / S = 见过但不熟 / D = 认识 / Esc = 停止）", flush=True)
        print("=" * 68, flush=True)
        print(f"词池        {self.pool_name}（{len(self.words)} 词）", flush=True)
        print(f"本轮待检测  {pending} 词", flush=True)
        limit_note = "不熟 + 不认识" if self.count_unsure else "不认识"
        print(f"停止条件    {limit_note}累计达到 {self.limit} 个", flush=True)
        print(
            f"历史进度    待检测 {overall[STATUS_UNTESTED]}"
            f" ｜ 认识 {overall[STATUS_KNOWN]}"
            f" ｜ 见过但不熟 {overall[STATUS_UNSURE]}"
            f" ｜ 不认识 {overall[STATUS_UNKNOWN]}",
            flush=True,
        )
        print(f"状态文件    {self.paths.drill_state}", flush=True)
        print(f"\n请在浏览器打开： {url}", flush=True)
        print("（按 Ctrl+C 结束服务）\n", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止。")
            emit: dict | None = None
            with self.lock:
                if self.session.tested and not self.session.finished:
                    self.session.stop()
                result = self.session.result()
                # 已经自动结束并输出过的那一轮不再重复输出
                if result["merged_words"] and self._emitted_round != self.session.round_no:
                    self._emitted_round = self.session.round_no
                    emit = result
            if emit is not None:
                self._emit_result(emit)
        finally:
            httpd.server_close()

    # -- 页面 ------------------------------------------------------------- #

    def page(self) -> str:
        return (
            PAGE.replace("__LIMIT__", str(self.limit))
            .replace("__COUNT_UNSURE__", "true" if self.count_unsure else "false")
            .replace("__POOL__", self.pool_name)
            .replace("__TOTAL__", str(len(self.words)))
        )


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>雅思认词判定</title>
<style>
  :root { --bg:#12151c; --fg:#e8ecf3; --dim:#7d8798; --no:#e0555f; --mid:#e0a33f; --yes:#3fb27f; --line:#252b37; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; }
  body { background:var(--bg); color:var(--fg); font-family:"Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif; overflow:hidden; }
  .wrap { display:flex; flex-direction:column; height:100vh; }
  header { padding:12px 20px; border-bottom:1px solid var(--line); display:flex; gap:24px; flex-wrap:wrap; font-size:13px; color:var(--dim); }
  header b { color:var(--fg); font-weight:600; }
  .stage { flex:1; display:flex; position:relative; }
  .side { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; padding-bottom:104px; transition:background .12s; cursor:pointer; user-select:none; }
  .side.left:hover { background:rgba(224,85,95,.10); }
  .side.right:hover { background:rgba(63,178,127,.10); }
  .side .key { font-size:44px; font-weight:700; line-height:1; }
  .side .cap { font-size:15px; color:var(--dim); margin-top:8px; }
  .left .key { color:var(--no); }
  .right .key { color:var(--yes); }
  .center { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; pointer-events:none; padding:0 12vw; }
  .stack { display:flex; flex-direction:column; align-items:center; gap:16px; }
  #word { font-size:clamp(38px,7vw,92px); font-weight:700; letter-spacing:.01em; text-align:center; word-break:break-word; }
  #hint { font-size:15px; color:var(--mid); min-height:22px; text-align:center; max-width:60vw; }
  .split { position:absolute; left:50%; top:8%; bottom:8%; width:1px; background:var(--line); }
  .band { position:absolute; left:50%; transform:translateX(-50%); bottom:26px; width:min(460px,52vw); display:flex; flex-direction:column; align-items:center; padding:10px 18px; border:1px solid var(--line); border-radius:10px; background:#171c26; transition:background .12s, border-color .12s; cursor:pointer; user-select:none; z-index:2; }
  .band:hover { background:rgba(224,163,63,.12); border-color:var(--mid); }
  .band .key { font-size:26px; font-weight:700; line-height:1; color:var(--mid); }
  .band .cap { font-size:14px; color:var(--dim); margin-top:4px; }
  footer { padding:10px 20px; border-top:1px solid var(--line); font-size:13px; color:var(--dim); display:flex; gap:20px; align-items:center; flex-wrap:wrap; }
  .bar { flex:1; min-width:140px; height:6px; background:#1d2330; border-radius:3px; overflow:hidden; }
  .bar > i { display:block; height:100%; width:0; background:var(--no); transition:width .15s; }
  button { background:#1d2330; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer; }
  button:hover { border-color:#3a4a63; }
  .overlay { position:absolute; inset:0; background:rgba(10,12,17,.97); display:none; flex-direction:column; padding:28px 32px; gap:12px; z-index:5; }
  .overlay.show { display:flex; }
  .overlay h2 { margin:0; font-size:20px; }
  .overlay .sub { color:var(--dim); font-size:14px; }
  .overlay label { font-size:13px; color:var(--dim); }
  textarea { flex:1; min-height:70px; background:#0d1017; color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:12px; font-family:ui-monospace,Consolas,monospace; font-size:14px; line-height:1.6; resize:none; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .empty { color:var(--dim); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>词池 <b>__POOL__</b></div>
    <div>共 <b>__TOTAL__</b> 词</div>
    <div>待检测 <b id="s-untested">-</b></div>
    <div>认识 <b id="s-known">-</b></div>
    <div>见过但不熟 <b id="s-unsure">-</b></div>
    <div>不认识 <b id="s-unknown">-</b></div>
  </header>

  <div class="stage">
    <div class="side left"  id="btn-no"><div class="key">A</div><div class="cap">不认识（左）</div></div>
    <div class="side right" id="btn-yes"><div class="key">D</div><div class="cap">认识（右）</div></div>
    <div class="split"></div>
    <div class="center"><div class="stack"><div id="word">加载中…</div><div id="hint"></div></div></div>
    <div class="band" id="btn-mid"><div class="key">S</div><div class="cap">见过但不熟（下）</div></div>
    <div class="overlay" id="overlay">
      <h2 id="ov-title">本轮结束</h2>
      <div class="sub" id="ov-sub"></div>
      <label id="ov-list-label">本轮要复习的词（逗号分隔）</label>
      <textarea id="ov-list" readonly></textarea>
      <div class="row">
        <button id="btn-copy">复制清单</button>
        <button id="btn-new">开始新一轮</button>
        <span class="empty" id="ov-hint">未判定的词保持「待检测」，下次继续</span>
      </div>
    </div>
  </div>

  <footer>
    <div>本轮 <b id="r-tested">0</b> / <b id="r-size">0</b></div>
    <div>本轮不认识 <b id="r-unknown">0</b> ｜ 不熟 <b id="r-unsure">0</b> ｜ 上限 <b id="r-limit">0</b></div>
    <div class="bar"><i id="r-bar"></i></div>
    <button id="btn-undo">← 上一个 (Backspace)</button>
    <button id="btn-stop">停止本轮 (Esc)</button>
  </footer>
</div>

<script>
(function () {
  const LIMIT = __LIMIT__;
  const COUNT_UNSURE = __COUNT_UNSURE__;
  let current = null;
  let busy = false;

  const $ = (id) => document.getElementById(id);

  const STATUS_CN = { untested:"待检测", unknown:"不认识", unsure:"见过但不熟", known:"认识" };

  function setHint(text) { $("hint").textContent = text || ""; }

  function renderSession(s) {
    $("s-untested").textContent = s.overall.untested;
    $("s-known").textContent = s.overall.known;
    $("s-unsure").textContent = s.overall.unsure;
    $("s-unknown").textContent = s.overall.unknown;
    $("r-tested").textContent = s.tested;
    $("r-size").textContent = s.round_size;
    $("r-unknown").textContent = s.unknown_count;
    $("r-unsure").textContent = s.unsure_count;
    $("r-limit").textContent = LIMIT;
    $("r-bar").style.width = Math.min(100, (s.limit_cursor / LIMIT) * 100) + "%";
    $("btn-undo").disabled = !s.can_undo;
    $("btn-undo").textContent = s.can_undo
      ? "← 上一个 (Backspace)  可回溯 " + s.undo_depth
      : "← 上一个 (Backspace)";
  }

  function showResult(res) {
    $("ov-title").textContent = "第 " + res.round_no + " 轮结束 —— " + res.reason_label;
    $("ov-sub").textContent =
      "已判定 " + res.tested + " 词 ｜ 认识 " + res.known_count +
      " ｜ 见过但不熟 " + res.unsure_count + " ｜ 不认识 " + res.unknown_count;
    $("ov-list-label").textContent =
      "本轮要复习的词（不认识 " + res.unknown_count + " + 不熟 " +
      res.unsure_count + " = " + (res.unknown_count + res.unsure_count) + "，逗号分隔）";
    $("ov-list").value = res.merged_comma || "（本轮没有需要复习的词）";
    $("overlay").classList.add("show");
  }

  async function fetchNext() {
    const r = await fetch("/api/next");
    const d = await r.json();
    renderSession(d.session);
    if (d.finished) { current = null; showResult(d.result); return; }
    current = d.word;
    $("word").textContent = current || "（词池已空）";
    setHint("");
  }

  async function undo() {
    if (busy) return;
    busy = true;
    try {
      const r = await fetch("/api/undo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ steps: 1 })
      });
      const d = await r.json();
      renderSession(d.session);
      if (!d.ok) { setHint(d.error || "无法回溯"); return; }
      $("overlay").classList.remove("show");
      current = d.word;
      $("word").textContent = current;
      const last = d.undone[d.undone.length - 1];
      setHint(
        "已退回 " + d.word + "：原判定「" + STATUS_CN[last.undone_status] +
        "」→ 现为「" + STATUS_CN[d.restored_status] + "」，请重新判定" +
        (d.reopened ? "（本轮已重新打开）" : "")
      );
    } finally { busy = false; }
  }

  async function answer(status) {
    if (!current || busy) return;
    busy = true;
    try {
      const r = await fetch("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ word: current, status: status })
      });
      const d = await r.json();
      if (d.session) renderSession(d.session);
      if (d.result) { current = null; showResult(d.result); return; }
      if (!d.ok) { await fetchNext(); return; }
      await fetchNext();
    } finally { busy = false; }
  }

  async function stop() {
    const r = await fetch("/api/stop", { method: "POST" });
    const d = await r.json();
    current = null;
    const s = await (await fetch("/api/session")).json();
    renderSession(s);
    showResult(d);
  }

  async function restart() {
    $("overlay").classList.remove("show");
    const r = await fetch("/api/restart", { method: "POST" });
    const d = await r.json();
    renderSession(d.session);
    await fetchNext();
  }

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) return;
    const k = e.key.toLowerCase();
    if (k === "a") { e.preventDefault(); answer("unknown"); }
    else if (k === "s") { e.preventDefault(); answer("unsure"); }
    else if (k === "d") { e.preventDefault(); answer("known"); }
    else if (e.key === "Backspace" || e.key === "ArrowLeft") { e.preventDefault(); undo(); }
    else if (e.key === "Escape") { e.preventDefault(); stop(); }
  });

  $("btn-no").addEventListener("click", () => answer("unknown"));
  $("btn-mid").addEventListener("click", () => answer("unsure"));
  $("btn-yes").addEventListener("click", () => answer("known"));
  $("btn-undo").addEventListener("click", undo);
  $("btn-stop").addEventListener("click", stop);
  $("btn-new").addEventListener("click", restart);

  async function copyFrom(id) {
    const text = $(id).value;
    try {
      await navigator.clipboard.writeText(text);
      $("ov-hint").textContent = "已复制到剪贴板";
    } catch (err) {
      $(id).select();
      document.execCommand("copy");
      $("ov-hint").textContent = "已复制（兼容模式）";
    }
  }

  $("btn-copy").addEventListener("click", () => copyFrom("ov-list"));

  fetchNext();
})();
</script>
</body>
</html>
"""
