"""Offline fault-injection tests for dsk (mock transport, temp ledger; spends nothing)."""
import json, os, subprocess, sys, tempfile, threading
import dsk

HERE = os.path.dirname(os.path.abspath(__file__))
U = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1000, "completion_tokens": 200}

def chat(content="", fin="stop", tool_calls=None, reasoning=None, usage=U):
    m = {"role": "assistant", "content": content}
    if tool_calls: m["tool_calls"] = tool_calls
    if reasoning: m["reasoning_content"] = reasoning
    return {"body": {"choices": [{"message": m, "finish_reason": fin}], "usage": usage}}

def tc(name, args, i="c1"):
    return {"id": i, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

DSML = '<\uff5c\uff5cDSML\uff5c\uff5c calls>\n<\uff5c\uff5cDSML\uff5c\uff5c invoke name="web_fetch">x</\uff5c\uff5cDSML\uff5c\uff5c invoke>'

class Env:
    def __init__(self, mock, cap="5"):
        self.d = tempfile.mkdtemp(prefix="dsk_t_"); self.mock = mock; self.cap = cap
    def __enter__(self):
        p = os.path.join(self.d, "mock.json")
        with open(p, "w", encoding="utf-8") as f: json.dump(self.mock, f)
        self.old = {k: os.environ.get(k) for k in ("DSK_MOCK", "DSK_HOME", "DEEPSEEK_MONTHLY_CAP", "DEEPSEEK_API_KEY")}
        os.environ.update({"DSK_MOCK": p, "DSK_HOME": self.d, "DEEPSEEK_MONTHLY_CAP": self.cap, "DEEPSEEK_API_KEY": "test"})
        dsk._mock_state = None
        return self
    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        dsk._mock_state = None

def run_cli(args, mock, cap="5", stdin=None):
    with Env(mock, cap) as e:
        env = dict(os.environ); env.pop("PYTHONIOENCODING", None)
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "dsk.py")] + args, input=stdin,
                           capture_output=True, env=env, cwd=e.d)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), e.d

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))

def run():
    # 1 empty answer because thinking ate max_tokens -> retried, real answer returned
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [chat("", "length"), chat("real answer")]})
    check("empty+length is retried", rc == 0 and out.strip() == "real answer", f"rc={rc} out={out!r} err={err[-300:]}")
    # 2 DSML leak in plain answer -> retried, clean
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [chat(DSML), chat("clean")]})
    check("DSML leak in ask is retried", rc == 0 and out.strip() == "clean" and "DSML" not in out, f"rc={rc} out={out!r}")
    # 3 429 then success
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [{"status": 429, "body": {}}, {"status": 503, "body": {}}, chat("ok after retry")]})
    check("429/503 retried with backoff", rc == 0 and "ok after retry" in out, f"rc={rc} err={err[-300:]}")
    # 4 402 -> clear error, exit 1, no retry loop
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [{"status": 402, "body": {}}]})
    check("402 gives clear top-up error", rc == 1 and "insufficient" in err, f"rc={rc} err={err[-300:]}")
    # 5 truncated non-empty -> continuation appended
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [chat("part one ", "length"), chat("part two")]})
    check("truncated answer is continued", rc == 0 and out.strip() == "part one part two", f"rc={rc} out={out!r}")
    # 6 truncated forever -> exit 5 and flagged, partial text kept
    rc, out, err, d = run_cli(["ask", "--prompt", "q", "--meta", "m.json"], {"chat": [chat("a", "length"), chat("b", "length"), chat("c", "length")]})
    meta = json.load(open(os.path.join(d, "m.json"), encoding="utf-8")) if os.path.exists(os.path.join(d, "m.json")) else {}
    check("unfixable truncation -> exit 5 + meta.truncated", rc == 5 and meta.get("truncated") is True and out.strip() == "abc", f"rc={rc} out={out!r} meta={meta}")
    # 7 JSON mode whitespace -> retried
    rc, out, err, _ = run_cli(["ask", "--prompt", "q", "--json"], {"chat": [chat("   "), chat('{"a": 1}')]})
    check("JSON whitespace answer is retried", rc == 0 and json.loads(out) == {"a": 1}, f"rc={rc} out={out!r}")
    # 8 unicode output (accents, arrows, CJK) through -I without PYTHONIOENCODING
    txt = "Árbol → camión ñ 【DeepSeek】 €2.00 ✓"
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [chat(txt)]})
    check("unicode output does not crash", rc == 0 and txt in out, f"rc={rc} err={err[-300:]}")
    # 9 prompt file > 32K chars (Windows argv limit) + stdin prompt
    with Env({"chat": [chat("big ok")]}) as e:
        pf = os.path.join(e.d, "big.txt"); open(pf, "w", encoding="utf-8").write("x" * 60000)
    rc, out, err, _ = run_cli(["ask", "--prompt-file", pf], {"chat": [chat("big ok")]})
    check("prompt file > 32K chars works", rc == 0 and "big ok" in out, f"rc={rc} err={err[-300:]}")
    rc, out, err, _ = run_cli(["ask"], {"chat": [chat("stdin ok")]}, stdin="prompt via stdin ñ".encode("utf-8"))
    check("prompt via stdin works", rc == 0 and "stdin ok" in out, f"rc={rc} err={err[-300:]}")
    # 10 privacy block
    rc, out, err, _ = run_cli(["ask", "--prompt", "q", "--file", r"__HOMEBS__\Documents\private_chats_analisis\x.txt"], {"chat": []})
    check("private path is blocked (exit 4)", rc == 4 and "refusing" in err, f"rc={rc} err={err[-200:]}")
    # 11 monthly cap already reached -> exit 3 before any call
    with Env({"chat": [chat("should not be called")]}, cap="0.50") as e:
        dsk.Ledger().add(0.50, kind="carry")
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "dsk.py"), "ask", "--prompt", "q"], capture_output=True, env=dict(os.environ))
        check("monthly cap blocks (exit 3)", r.returncode == 3 and "cap" in r.stderr.decode("utf-8", "replace"), f"rc={r.returncode}")
    # 12 research: DSML leaked on a turn -> separate synthesis call, clean answer with source
    mock = {"chat": [chat("", "tool_calls", [tc("web_search", {"query": "city bus fare"})], reasoning="think"),
                     chat(DSML),                                     # leaked markup instead of answer
                     chat("Fare is EUR 2.00.\nSources:\n- https://example.mt/fares")],
            "search": [{"body": {"results": [{"title": "Fares", "url": "https://example.mt/fares", "summary": "Winter fare EUR 2.00"}]}}]}
    rc, out, err, _ = run_cli(["research", "city fare?"], mock)
    check("research DSML leak -> synthesis answer", rc == 0 and "2.00" in out and "DSML" not in out and "synthesizing" in err, f"rc={rc} out={out!r} err={err[-300:]}")
    # 13 research: tool turns never end -> turn limit -> synthesis
    loop = [chat("", "tool_calls", [tc("web_fetch", {"url": "https://a.example"}, f"c{i}")], reasoning="r") for i in range(3)]
    mock = {"chat": loop + [chat("final from evidence")], "fetch": [{"body": {"title": "A", "content": "page"}}] * 3}
    rc, out, err, _ = run_cli(["research", "q", "--turns", "3"], mock)
    check("research turn limit -> synthesis", rc == 0 and "final from evidence" in out, f"rc={rc} out={out!r} err={err[-300:]}")
    # 14 research: job budget stops gathering early, still answers
    big = {"prompt_tokens": 700000, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 700000, "completion_tokens": 2000}
    # one gather call costs >= $0.105 (off-peak) -> over 80% of a $0.10 cap -> must stop and synthesize
    mock = {"chat": [chat("", "tool_calls", [tc("web_search", {"query": "q"})], reasoning="r", usage=big), chat("answer under budget")],
            "search": [{"body": {"results": [{"url": "https://x"}]}}]}
    rc, out, err, _ = run_cli(["research", "q", "--job-cap", "0.10"], mock)
    check("research job cap -> stops and answers", rc == 0 and "answer under budget" in out and "budget" in err, f"rc={rc} err={err[-300:]}")
    # 15 reasoning_content: passed back when present, never sent empty
    with Env({"chat": [chat("", "tool_calls", [tc("web_search", {"query": "a"})], reasoning=""), chat("done")],
              "search": [{"body": {"results": []}}]}) as e:
        sent = []
        orig = dsk.http
        def spy(method, path, body=None, timeout=600, retries=5):
            if path == "/chat/completions": sent.append(json.loads(json.dumps(body)))
            return orig(method, path, body, timeout, retries)
        dsk.http = spy
        try:
            job = dsk.Job("deepseek-flash", "high", 1.0, dsk.Ledger()); dsk.research(job, "q", 3)
        finally: dsk.http = orig
        asst = [m for m in sent[-1]["messages"] if m["role"] == "assistant"]
        check("empty reasoning_content is not sent", asst and "reasoning_content" not in asst[0], str(asst)[:200])
    # 16 ledger: 8 threads x 25 reserve/settle -> exact total, no lost updates
    with Env({}) as e:
        L = dsk.Ledger()
        def worker():
            for _ in range(25):
                rid = L.reserve(0.01); L.settle(rid, 0.001)
        ts = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        tot = L.month_total()
        check("ledger parallel-safe (200 writes)", abs(tot - 0.2) < 1e-6, f"total={tot}")
    # 17 ledger: parallel reservations cannot overshoot the cap
    with Env({}, cap="0.10") as e:
        L = dsk.Ledger(); ok = []; lk = threading.Lock()
        def grab():
            try:
                L.reserve(0.03)
                with lk: ok.append(1)
            except dsk.BudgetErr: pass
        ts = [threading.Thread(target=grab) for _ in range(10)]
        [t.start() for t in ts]; [t.join() for t in ts]
        check("parallel reservations respect cap", len(ok) == 3, f"granted={len(ok)}")
    # 18 map: long input is chunked and merged
    with Env({"chat": [chat("r1"), chat("r2"), chat("merged")]}) as e:
        job = dsk.Job("deepseek-flash", "low", 1.0, dsk.Ledger())
        text = ("line of text\n" * 40000)            # ~520K chars -> 2 chunks
        out = dsk.do_map(job, "summarize", text, workers=2)
        check("map chunks long input and merges", out == "merged" and job.calls == 3, f"out={out!r} calls={job.calls}")
    # 20 fetch keeps failing (504) -> research still answers
    mock = {"chat": [chat("", "tool_calls", [tc("web_fetch", {"url": "https://down.example"})], reasoning="r"),
                     chat("answer despite dead page")],
            "fetch": [{"status": 504, "body": {}}] * 4}
    rc, out, err, _ = run_cli(["research", "q"], mock)
    check("fetch 504 does not kill research", rc == 0 and "despite dead page" in out, f"rc={rc} out={out!r} err={err[-300:]}")
    # 21 chat 400 on gather turn 2 -> answers from turn-1 evidence
    mock = {"chat": [chat("", "tool_calls", [tc("web_search", {"query": "a"})], reasoning="r"),
                     {"status": 400, "body": {"error": "bad request"}},
                     chat("answer from turn 1 evidence")],
            "search": [{"body": {"results": [{"url": "https://x", "summary": "fact"}]}}]}
    rc, out, err, _ = run_cli(["research", "q"], mock)
    check("chat 400 mid-research -> synthesis", rc == 0 and "turn 1 evidence" in out, f"rc={rc} out={out!r} err={err[-300:]}")
    # 22 empty answer with finish=stop is retried too
    rc, out, err, _ = run_cli(["ask", "--prompt", "q"], {"chat": [chat("", "stop"), chat("second try")]})
    check("empty answer (stop) is retried", rc == 0 and out.strip() == "second try", f"rc={rc} out={out!r}")
    # 23 ledger across 6 separate processes (real parallel use)
    with Env({}) as e:
        code = ("import sys; sys.path.insert(0, r'%s'); import dsk\n"
                "L = dsk.Ledger()\nfor _ in range(20):\n    r = L.reserve(0.01); L.settle(r, 0.002)\n") % HERE
        ps = [subprocess.Popen([sys.executable, "-I", "-c", code], env=dict(os.environ)) for _ in range(6)]
        [p.wait() for p in ps]
        tot = dsk.Ledger().month_total()
        check("ledger safe across 6 processes", abs(tot - 0.24) < 1e-6, f"total={tot}")
    # 24 exit code 3 survives the dsk.cmd wrapper when called from PowerShell
    with Env({"chat": [chat("x")]}, cap="0.01") as e:
        dsk.Ledger().add(0.02, kind="carry")
        cmd = os.path.join(HERE, "dsk.cmd")
        r = subprocess.run(["pwsh", "-NoProfile", "-Command", f"& '{cmd}' ask --prompt q *> $null; exit $LASTEXITCODE"],
                           capture_output=True, env=dict(os.environ))
        check("exit code 3 through dsk.cmd + PowerShell", r.returncode == 3, f"rc={r.returncode} {r.stderr[-200:]}")
    # 19 peak pricing
    from datetime import datetime, timezone
    check("peak window detection", dsk.is_peak(datetime(2026, 11, 5, 7, 0, tzinfo=timezone.utc)) and
          not dsk.is_peak(datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)) and
          not dsk.is_peak(datetime(2026, 11, 7, 7, 0, tzinfo=timezone.utc)), "")
    n_ok = sum(1 for r in results if r[1])
    print(f"\n{n_ok}/{len(results)} passed")
    return 0 if n_ok == len(results) else 1

if __name__ == "__main__":
    sys.exit(run())
