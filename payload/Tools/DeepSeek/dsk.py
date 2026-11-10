"""dsk - robust DeepSeek worker CLI for Claude Code (stdlib only).

Commands
  dsk ask      --prompt TEXT | --prompt-file F | stdin   [--file F ...] [--effort none|low|high|max] [--pro]
               [--json] [--system TEXT] [--max-tokens N] [--out F] [--meta F] [--job-cap USD] [--allow-private]
  dsk research QUESTION | --prompt-file F   [--turns N] [--job-cap USD] [--effort E] [--out F] [--meta F]
  dsk map      --prompt TEXT --file BIG [--file ...]   chunked map-reduce for long inputs [--workers 3]
  dsk status   month spend vs cap, live balance, current price window
  dsk selftest offline fault-injection tests (no API calls, no money)

Exit codes: 0 ok | 1 API failure after retries | 2 bad usage/input | 3 budget cap | 4 privacy block | 5 answer incomplete
Answer -> stdout (or --out file, UTF-8). Stats -> stderr. --meta writes JSON (model, effort, tokens, cost, truncated, exit).
Env: DEEPSEEK_API_KEY (user env), DEEPSEEK_MONTHLY_CAP (default 5), DSK_HOME (state dir, default this folder),
     DSK_MOCK (JSON file of canned responses, for tests).
"""
import sys
for _s in (sys.stdout, sys.stderr, sys.stdin):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

import argparse, json, os, re, time, uuid, random, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.deepseek.com"

# ---------- prices (USD per 1M tokens, PEAK; off-peak = half). Check docs often: they change ~every 11 days.
PRICES = {
    "deepseek-flash":  {"miss": 0.30, "hit": 0.006, "out": 1.20},
    "deepseek-v4-pro": {"miss": 1.32, "hit": 0.044, "out": 3.96},
}
TOOL_CALL_USD = 0.00075           # per successful search or fetch
PRICES_CHECKED = "2026-11-10"

def home(): return os.environ.get("DSK_HOME") or HERE
def monthly_cap(): return float(os.environ.get("DEEPSEEK_MONTHLY_CAP", "5.00"))

def account_spend():
    """This PC's DeepSeek spend this month (dsk + deepseek mode), from ds_budget.py. Friends on the key don't count."""
    try:
        if home() != HERE: return 0.0          # selftests use a temp home
        import ds_budget; return ds_budget.my_spend()
    except Exception:
        return 0.0

def is_peak(t=None):
    t = t or datetime.now(timezone.utc)
    try:
        import ds_peak; return ds_peak.is_peak(t)      # official rule incl. Chinese holidays
    except Exception:
        return t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)

def price_mult(): return 1.0 if is_peak() else 0.5

def cost_of(model, usage):
    p = PRICES[model]; m = price_mult()
    hit = usage.get("prompt_cache_hit_tokens", 0) or 0
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None: miss = max((usage.get("prompt_tokens", 0) or 0) - hit, 0)
    out = usage.get("completion_tokens", 0) or 0
    return (miss * p["miss"] + hit * p["hit"] + out * p["out"]) / 1e6 * m

def est_tokens(text): return int(len(text) / 3.2) + 1

# ---------- errors
class DskError(Exception):
    code = 1
class UsageErr(DskError): code = 2
class BudgetErr(DskError): code = 3
class PrivacyErr(DskError): code = 4
class ApiErr(DskError): code = 1

def log(msg): print(f"[dsk] {msg}", file=sys.stderr, flush=True)

# ---------- ledger: append-only JSONL + file lock; reserve before a call, settle after (parallel-safe)
class Ledger:
    STALE = 2 * 3600
    def __init__(self):
        self.path = os.path.join(home(), "ledger.jsonl")
        self.lockpath = self.path + ".lock"
        self._tl = threading.Lock()

    def _locked(self, fn):
        import msvcrt
        with self._tl:
            fd = os.open(self.lockpath, os.O_RDWR | os.O_CREAT)
            try:
                while True:
                    try: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1); break
                    except OSError: time.sleep(0.05)
                try: return fn()
                finally:
                    os.lseek(fd, 0, 0); msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally: os.close(fd)

    def _entries(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError: return []

    def _append(self, e):
        with open(self.path, "a", encoding="utf-8") as f: f.write(json.dumps(e) + "\n")

    def _month_total(self, month=None):
        month = month or time.strftime("%Y-%m"); now = time.time()
        res, settled = {}, {}
        for e in self._entries():
            if e.get("month") != month: continue
            if e["kind"] == "settle": settled[e["id"]] = e["usd"]
            elif e["kind"] == "reserve":
                if now - e["ts"] < self.STALE: res[e["id"]] = e["usd"]
            elif e["kind"] in ("carry", "manual"): settled[e["id"]] = e["usd"]
        total = sum(settled.values()) + sum(v for k, v in res.items() if k not in settled)
        return total

    def month_total(self): return self._locked(self._month_total)

    def reserve(self, usd, cap=None):
        cap = monthly_cap() if cap is None else cap
        rid = uuid.uuid4().hex[:12]
        def f():
            tot = max(self._month_total(), account_spend())  # dsk + deepseek mode on this PC
            if tot + usd > cap:
                raise BudgetErr(f"monthly cap: ${tot:.3f} spent + ${usd:.3f} needed > ${cap:.2f} cap ({time.strftime('%Y-%m')}). Do this with Claude instead, or raise DEEPSEEK_MONTHLY_CAP.")
            self._append({"id": rid, "kind": "reserve", "usd": round(usd, 6), "ts": time.time(), "month": time.strftime("%Y-%m")})
            return rid
        return self._locked(f)

    def settle(self, rid, usd):
        self._locked(lambda: self._append({"id": rid, "kind": "settle", "usd": round(usd, 6), "ts": time.time(), "month": time.strftime("%Y-%m")}))

    def add(self, usd, kind="manual", note=""):
        self._locked(lambda: self._append({"id": uuid.uuid4().hex[:12], "kind": kind, "usd": round(usd, 6), "ts": time.time(), "month": time.strftime("%Y-%m"), "note": note}))

# ---------- transport (real or mock)
def api_key():
    k = os.environ.get("DEEPSEEK_API_KEY")
    if not k and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                k = winreg.QueryValueEx(h, "DEEPSEEK_API_KEY")[0]
        except OSError: pass
    if not k: raise UsageErr("DEEPSEEK_API_KEY is not set")
    return k

_mock_lock = threading.Lock()
_mock_state = None
def _mock(path_key):
    global _mock_state
    with _mock_lock:
        if _mock_state is None:
            with open(os.environ["DSK_MOCK"], encoding="utf-8") as f: _mock_state = json.load(f)
        q = _mock_state.get(path_key)
        if isinstance(q, list):
            if not q: raise ApiErr(f"mock queue '{path_key}' empty")
            item = q.pop(0)
        else: item = q
    if item is None: raise ApiErr(f"no mock for {path_key}")
    return item.get("status", 200), item.get("body", {})

def http(method, path, body=None, timeout=600, retries=5):
    """Returns (status, json). Retries 429/5xx/network with backoff; raises ApiErr on hard errors."""
    key_name = {"/chat/completions": "chat", "/v1/tools/search": "search", "/v1/tools/fetch": "fetch", "/user/balance": "balance"}.get(path, path)
    delays = [2, 4, 8, 16, 30][:retries]
    for attempt in range(len(delays) + 1):
        try:
            if os.environ.get("DSK_MOCK"):
                status, data = _mock(key_name)
            else:
                req = urllib.request.Request(API + path, method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        raw = r.read().decode("utf-8", "replace").strip()
                        status, data = r.status, (json.loads(raw) if raw else {})
                except urllib.error.HTTPError as e:
                    txt = e.read().decode("utf-8", "replace")[:400]
                    status, data = e.code, {"error": txt}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, json.JSONDecodeError) as e:
            status, data = 599, {"error": f"network: {e}"}
        if status == 200: return status, data
        if status == 402: raise ApiErr("402 insufficient DeepSeek balance - top up at platform.deepseek.com")
        if status == 401: raise ApiErr("401 invalid API key")
        if status in (400, 403, 404, 413, 422):
            return status, data                     # caller decides (e.g. fetch 404 is normal)
        if attempt < len(delays):                   # 429, 5xx, network
            d = delays[attempt] + random.random()
            log(f"HTTP {status} on {path}, retry in {d:.0f}s")
            if not os.environ.get("DSK_MOCK"): time.sleep(d)
            continue
        raise ApiErr(f"HTTP {status} on {path} after retries: {str(data)[:200]}")

# ---------- privacy guard (paths only; regex PII scanning would give false confidence)
# Only RAW private sources are blocked (chats, memory, credentials). Anonymous facts in a prompt are fine (user, 2026-11-06).
# User is fine sending his own data (2026-11-06). Only blocked: secrets (keys/passwords) and other people's raw chats.
PRIVATE_PATTERNS = [r"\\\.ssh\\", r"\.env$", r"password", r"credential", r"secret", r"\.pem$", r"\.key$",
                    r"private_chats", r"whatsapp", r"telegram"]
def privacy_check(paths, allow):
    if allow: return
    for p in paths:
        ap = os.path.abspath(p).lower()
        for pat in PRIVATE_PATTERNS:
            if re.search(pat, ap):
                raise PrivacyErr(f"refusing to send '{p}' to DeepSeek (matches private pattern '{pat}'; data is stored in China). "
                                 f"Use Claude/agy for personal data, or pass --allow-private if it is NOT personal.")

# ---------- core completion with all the safety nets
DSML_RE = re.compile(r"<\s*[|｜]{1,2}\s*DSML", re.I)
def has_dsml(t): return bool(t and DSML_RE.search(t))
def strip_dsml(t):
    i = DSML_RE.search(t or "")
    return t[:i.start()].rstrip() if i else t

class Job:
    def __init__(self, model, effort, cap, ledger):
        self.model, self.effort, self.ledger = model, effort, ledger
        remaining = monthly_cap() - ledger.month_total()
        if remaining < 0.01: raise BudgetErr(f"monthly cap reached (${monthly_cap():.2f}). Do this with Claude instead.")
        self.cap = min(cap, remaining)
        self.spent = 0.0; self.calls = 0; self.tin = 0; self.tout = 0; self.thit = 0
        self.tools = 0; self.last_prompt = 0; self.truncated = False; self.notes = []
        self._lk = threading.Lock()

    def estimate(self, messages, max_out):
        p = PRICES[self.model]; m = price_mult()
        tot = est_tokens(json.dumps(messages, ensure_ascii=False))
        new = max(tot - self.last_prompt, 0); old = tot - new
        return (new * p["miss"] + old * p["hit"] + min(max_out, 6000) * p["out"]) / 1e6 * m

    def chat(self, messages, max_tokens=32000, tools=None, json_mode=False, effort=None):
        effort = effort or self.effort
        est = self.estimate(messages, max_tokens)
        with self._lk:
            if self.spent + est > self.cap:
                raise BudgetErr(f"job cap ${self.cap:.2f} reached (spent ${self.spent:.3f})")
        rid = self.ledger.reserve(est)
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if effort == "none": body["thinking"] = {"type": "disabled"}
        else: body["thinking"] = {"type": "enabled"}; body["reasoning_effort"] = effort
        if tools: body["tools"] = tools
        if json_mode: body["response_format"] = {"type": "json_object"}
        t0 = time.time()
        try:
            status, data = http("POST", "/chat/completions", body)
        except Exception:
            self.ledger.settle(rid, 0.0); raise
        if status != 200:
            self.ledger.settle(rid, 0.0)
            raise ApiErr(f"HTTP {status}: {str(data.get('error', data))[:300]}")
        u = data.get("usage", {}) or {}
        c = cost_of(self.model, u)
        self.ledger.settle(rid, c)
        with self._lk:
            self.spent += c; self.calls += 1
            self.tin += u.get("prompt_tokens", 0) or 0; self.tout += u.get("completion_tokens", 0) or 0
            self.thit += u.get("prompt_cache_hit_tokens", 0) or 0
            self.last_prompt = u.get("prompt_tokens", 0) or self.last_prompt
        ch = data["choices"][0]
        _calllog({"model": self.model, "effort": effort, "usage": u, "usd": c, "finish": ch.get("finish_reason"), "sec": round(time.time() - t0, 1)})
        return ch["message"], ch.get("finish_reason")

    def add_tool_cost(self, n=1):
        c = n * TOOL_CALL_USD
        self.ledger.add(c, kind="manual", note="search/fetch")
        with self._lk: self.spent += c; self.tools += n

    def complete(self, messages, max_tokens=32000, json_mode=False):
        """Plain completion (no tools) that never returns silently broken output."""
        msg, fin = self.chat(messages, max_tokens, json_mode=json_mode)
        text = msg.get("content") or ""
        # 1) empty answer (thinking ate the budget, or just empty) -> retry bigger, then lower effort
        if not text.strip():
            self.notes.append(f"empty ({fin}): retry with 2x max_tokens")
            msg, fin = self.chat(messages, min(max_tokens * 2, 384000), json_mode=json_mode)
            text = msg.get("content") or ""
            if not text.strip() and self.effort not in ("none", "low"):
                self.notes.append("still empty: retry at effort low")
                msg, fin = self.chat(messages, min(max_tokens * 2, 384000), json_mode=json_mode, effort="low")
                text = msg.get("content") or ""
        # 2) leaked tool markup -> retry once, then strip
        if has_dsml(text):
            self.notes.append("DSML leak: retry")
            msg, fin = self.chat(messages, max_tokens, json_mode=json_mode)
            text = msg.get("content") or ""
            if has_dsml(text): self.notes.append("DSML leak again: stripped"); text = strip_dsml(text)
        # 3) JSON mode returning whitespace / invalid JSON -> retry once
        if json_mode:
            try: json.loads(text)
            except Exception:
                self.notes.append("invalid JSON: retry")
                msg, fin = self.chat(messages, max_tokens, json_mode=True)
                text = msg.get("content") or ""
                try: json.loads(text)
                except Exception: self.truncated = True; self.notes.append("JSON still invalid")
        # 4) truncated non-empty answer -> ask to continue (up to 2x)
        conts = 0
        while fin == "length" and text.strip() and not json_mode and conts < 2:
            conts += 1; self.notes.append(f"truncated: continuation {conts}")
            more_msgs = messages + [{"role": "assistant", "content": text},
                                    {"role": "user", "content": "Your answer was cut off. Continue EXACTLY where it stopped. Do not repeat anything."}]
            msg, fin = self.chat(more_msgs, max_tokens)
            text += (msg.get("content") or "")
        if fin == "length" or not text.strip(): self.truncated = True
        return strip_dsml(text)

    def stats(self):
        return {"model": self.model, "effort": self.effort, "calls": self.calls, "tool_calls": self.tools,
                "tokens_in": self.tin, "tokens_cache_hit": self.thit, "tokens_out": self.tout,
                "usd": round(self.spent, 5), "peak": is_peak(), "truncated": self.truncated, "notes": self.notes}

def _calllog(e):
    try:
        d = os.path.join(home(), "logs"); os.makedirs(d, exist_ok=True)
        e["ts"] = datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(d, "calls.jsonl"), "a", encoding="utf-8") as f: f.write(json.dumps(e) + "\n")
    except Exception: pass

# ---------- research
TOOLS = [
    {"type": "function", "function": {"name": "web_search",
        "description": "Search the live web. Operators: site:, -site:, \"exact\", lang:, after:YYYY-MM-DD, before:. Returns title, url, date, summary, authority (0-4) and rerank_score (0-3, >=1.5 good).",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "count": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "web_fetch",
        "description": "Fetch one web page as Markdown (first ~12000 chars). Use for primary sources.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]
RESEARCH_SYS = ("You are a meticulous web researcher. Today is {today}. Use web_search and web_fetch to find CURRENT facts; never answer from memory. "
                "Prefer official/primary sources and open them with web_fetch. Cross-check key facts. Stop searching as soon as you can answer well. "
                "When done, answer: direct answer first, key details, then 'Sources:' with the URLs you used. Flag anything uncertain.")
SYNTH_SYS = ("You write the final answer to a research question using ONLY the evidence provided (search results and fetched pages). "
             "Direct answer first, then key details, then 'Sources:' listing URLs from the evidence that support it. "
             "If the evidence does not settle something, say so explicitly. Never invent URLs. Plain text/Markdown only, no tool calls. "
             "Answer in the language of the question.")

def run_tool(job, name, args):
    """Never raises for a bad page/search (only for 401/402): the model just sees a failure note."""
    try: return _run_tool(job, name, args)
    except ApiErr as e:
        if any(c in str(e) for c in ("401", "402")): raise
        return f"{name} failed ({str(e)[:120]}) - try another source"

def _run_tool(job, name, args):
    if name == "web_search":
        q = str(args.get("query", ""))[:128]
        if not q.strip(): return "error: empty query"
        try: cnt = max(1, min(int(args.get("count", 8) or 8), 12))
        except (TypeError, ValueError): cnt = 8
        st, d = http("POST", "/v1/tools/search", {"query": q, "count": cnt}, timeout=60, retries=1)
        if st != 200: return f"search error HTTP {st}"
        res = d.get("results", [])
        if not res: return "search: no results - rephrase the query"
        job.add_tool_cost()
        return json.dumps([{k: r.get(k) for k in ("title", "url", "date", "authority", "rerank_score", "summary")} for r in res], ensure_ascii=False)[:9000]
    if name == "web_fetch":
        url = str(args.get("url", ""))
        if not url.startswith(("http://", "https://")): return "error: bad url"
        st, d = http("POST", "/v1/tools/fetch", {"url": url}, timeout=60, retries=1)
        if st != 200: return f"fetch failed HTTP {st} (page unavailable)"
        if not (d.get("content") or "").strip(): return "fetch: page had no extractable content - try another source"  # unbilled by DeepSeek
        job.add_tool_cost()
        return (f"URL: {url}\nTITLE: {d.get('title','')}\n" + (d.get("content") or ""))[:12000]
    return "error: unknown tool"

def research(job, question, turns=10, evidence_cap=220_000):
    msgs = [{"role": "system", "content": RESEARCH_SYS.format(today=time.strftime("%Y-%m-%d"))},
            {"role": "user", "content": question}]
    evidence, ev_chars, stop = [], 0, None
    for turn in range(turns):
        if job.spent > 0.8 * job.cap: stop = "job budget 80% used"; break
        if ev_chars > evidence_cap: stop = "evidence cap reached"; break
        try:
            msg, fin = job.chat(msgs, 32000, tools=TOOLS)
        except BudgetErr as e: stop = str(e); break
        except ApiErr as e:
            if any(c in str(e) for c in ("401", "402")): raise
            stop = f"API error during research ({str(e)[:100]})"; break
        content, tcs = msg.get("content") or "", msg.get("tool_calls") or []
        if not tcs:
            if content.strip() and not has_dsml(content) and fin != "length":
                return content, {"stopped": "model answered", "turns": turn + 1}
            stop = "model output unusable (DSML/empty/truncated)"; break
        a = {"role": "assistant", "content": content, "tool_calls": tcs}
        if msg.get("reasoning_content"): a["reasoning_content"] = msg["reasoning_content"]
        msgs.append(a)
        for tc in tcs:
            try: args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception: args = {}
            out = run_tool(job, tc["function"]["name"], args)
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
            evidence.append(f"### {tc['function']['name']} {json.dumps(args, ensure_ascii=False)}\n{out}")
            ev_chars += len(out)
    else:
        stop = "turn limit"
    # separate synthesis call: no tools in request or history -> no DSML leak path
    log(f"research: synthesizing ({stop})")
    ev = "\n\n".join(evidence)[-evidence_cap:] or "(no evidence gathered)"
    synth = [{"role": "system", "content": SYNTH_SYS},
             {"role": "user", "content": f"QUESTION:\n{question}\n\nEVIDENCE:\n{ev}\n\nWrite the final answer now."}]
    job.cap = max(job.cap, job.spent + 0.05)      # always allow the final write-up (still bounded by monthly ledger)
    try:
        return job.complete(synth, 48000), {"stopped": stop, "turns": turns}
    except DskError:
        p = os.path.join(home(), "logs", "last_evidence.md"); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f: f.write(f"QUESTION: {question}\n\n{ev}")
        log(f"synthesis failed; evidence saved to {p}")
        raise

# ---------- map-reduce for long inputs
CHUNK_CHARS = 330_000     # ~100K tokens: long-context recall drops beyond this
def chunks_of(text, size=CHUNK_CHARS):
    out, i = [], 0
    while i < len(text):
        j = min(i + size, len(text))
        if j < len(text):
            k = text.rfind("\n", i + size // 2, j)
            if k > i: j = k + 1
        out.append(text[i:j]); i = j
    return out

def do_map(job, prompt, text, workers=3):
    parts = chunks_of(text)
    log(f"map: {len(parts)} chunks")
    def one(ix):
        m = [{"role": "system", "content": "You process one chunk of a larger input. Extract everything relevant to the task; be complete and precise; quote exact numbers/lines."},
             {"role": "user", "content": f"TASK: {prompt}\n\nCHUNK {ix+1}/{len(parts)}:\n{parts[ix]}"}]
        try: return job.complete(m, 32000)
        except DskError as e:
            if isinstance(e, ApiErr) and any(c in str(e) for c in ("401", "402")): raise
            job.truncated = True; job.notes.append(f"chunk {ix+1} failed: {str(e)[:80]}")
            return f"(chunk {ix+1} could not be processed: {str(e)[:80]})"
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        outs = list(ex.map(one, range(len(parts))))
    if len(outs) == 1: return outs[0]
    merged = "\n\n".join(f"=== CHUNK {i+1} RESULT ===\n{o}" for i, o in enumerate(outs))
    if len(merged) > CHUNK_CHARS: return do_map(job, "Merge these partial results for the task: " + prompt, merged, workers)
    m = [{"role": "system", "content": "Merge partial results from chunks into one final answer. Remove duplicates, keep every distinct fact, keep exact numbers."},
         {"role": "user", "content": f"TASK: {prompt}\n\n{merged}"}]
    return job.complete(m, 48000)

# ---------- CLI
def read_prompt(a):
    if getattr(a, "prompt", None): return a.prompt
    if getattr(a, "prompt_file", None):
        with open(a.prompt_file, encoding="utf-8-sig") as f: return f.read()
    if not sys.stdin.isatty():
        t = sys.stdin.read()
        if t.strip(): return t
    raise UsageErr("no prompt: use --prompt, --prompt-file or stdin")

def read_files(files):
    out = ""
    for p in files or []:
        if not os.path.isfile(p): raise UsageErr(f"missing file: {p}")
        with open(p, encoding="utf-8-sig", errors="replace") as f:
            out += f"\n\n===== FILE: {os.path.basename(p)} =====\n" + f.read()
    return out

def emit(a, text, meta):
    if getattr(a, "out", None):
        with open(a.out, "w", encoding="utf-8") as f: f.write(text)
    else:
        print(text)
    if getattr(a, "meta", None):
        with open(a.meta, "w", encoding="utf-8") as f: json.dump(meta, f, indent=1, ensure_ascii=False)
    log(f"{meta.get('model')}/{meta.get('effort')} calls={meta.get('calls')} tools={meta.get('tool_calls')} "
        f"in={meta.get('tokens_in')} (cached {meta.get('tokens_cache_hit')}) out={meta.get('tokens_out')} "
        f"${meta.get('usd'):.4f} {'PEAK' if meta.get('peak') else 'off-peak'}"
        + (" TRUNCATED" if meta.get("truncated") else "") + (f" notes={meta['notes']}" if meta.get("notes") else ""))

def cmd_ask(a):
    prompt = read_prompt(a)
    privacy_check(a.file or [], a.allow_private)
    if getattr(a, "prompt_file", None): privacy_check([a.prompt_file], a.allow_private)
    # Files FIRST, question last: DeepSeek's prefix cache then reuses the document across different questions (~50x cheaper input).
    files = read_files(a.file)
    content = (files.strip() + "\n\n" + prompt) if files.strip() else prompt
    if a.json:  # DeepSeek's json_object mode returns whitespace unless the prompt itself asks for JSON
        content += "\n\nReply with a single JSON object and nothing else."
    if est_tokens(content) > 120_000:
        raise UsageErr(f"input ~{est_tokens(content)} tokens is too long for reliable recall; use 'dsk map' (chunked) instead")
    job = Job("deepseek-v4-pro" if a.pro else "deepseek-flash", a.effort, a.job_cap, Ledger())
    msgs = [{"role": "system", "content": a.system}, {"role": "user", "content": content}]
    text = job.complete(msgs, a.max_tokens, json_mode=a.json)
    meta = job.stats(); emit(a, text, meta)
    return 5 if meta["truncated"] else 0

def cmd_research(a):
    q = a.question or read_prompt(a)
    if getattr(a, "prompt_file", None): privacy_check([a.prompt_file], a.allow_private)
    job = Job("deepseek-flash", a.effort, a.job_cap, Ledger())
    text, info = research(job, q, a.turns)
    meta = job.stats(); meta.update(info); emit(a, text, meta)
    return 5 if meta["truncated"] else 0

def cmd_map(a):
    prompt = read_prompt(a)
    if not a.file: raise UsageErr("map needs --file")
    privacy_check(a.file, a.allow_private)
    job = Job("deepseek-v4-pro" if a.pro else "deepseek-flash", a.effort, a.job_cap, Ledger())
    text = do_map(job, prompt, read_files(a.file), a.workers)
    meta = job.stats(); emit(a, text, meta)
    return 5 if meta["truncated"] else 0

def cmd_status(a):
    L = Ledger(); spent = L.month_total()
    try:
        st, d = http("GET", "/user/balance", timeout=30)
        bal = d.get("balance_infos", [{}])[0].get("total_balance", "?")
    except Exception as e: bal = f"unavailable ({e})"
    print(f"month {time.strftime('%Y-%m')}: ${spent:.4f} of ${monthly_cap():.2f} cap  (remaining ${max(monthly_cap()-spent,0):.4f})")
    print(f"live DeepSeek balance: ${bal}")
    try:
        import ds_budget
        if home() == HERE: ds_budget.update(float(bal))
        print(ds_budget.verdict(once_a_day=False)[1])
    except Exception: pass
    try:
        import ds_peak; print("now: " + ds_peak.short() + f"  | prices last checked {PRICES_CHECKED}")
    except Exception:
        print(f"now: {'PEAK (full price)' if is_peak() else 'OFF-PEAK (half price)'}  | prices last checked {PRICES_CHECKED}")
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser(prog="dsk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    def common(p, effort="high", cap=0.25):
        p.add_argument("--prompt"); p.add_argument("--prompt-file")
        p.add_argument("--effort", default=effort, choices=["none", "low", "high", "max"])
        p.add_argument("--job-cap", type=float, default=cap, help="max USD for this job")
        p.add_argument("--out"); p.add_argument("--meta"); p.add_argument("--allow-private", action="store_true")
    p = sub.add_parser("ask"); common(p, "high", 0.25)
    p.add_argument("--file", action="append"); p.add_argument("--pro", action="store_true")
    p.add_argument("--json", action="store_true"); p.add_argument("--max-tokens", type=int, default=32000)
    p.add_argument("--system", default="You are a precise worker model. Do exactly the task. Be concise and factual; quote exact numbers and lines when relevant. Answer in the language of the task.")
    p = sub.add_parser("research"); common(p, "max", 0.25)
    p.add_argument("question", nargs="?"); p.add_argument("--turns", type=int, default=10)
    p = sub.add_parser("map"); common(p, "high", 1.00)
    p.add_argument("--file", action="append"); p.add_argument("--pro", action="store_true"); p.add_argument("--workers", type=int, default=3)
    sub.add_parser("status"); sub.add_parser("selftest")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "selftest":
            import dsk_selftest; return dsk_selftest.run()
        return {"ask": cmd_ask, "research": cmd_research, "map": cmd_map, "status": cmd_status}[a.cmd](a)
    except DskError as e:
        log(f"ERROR: {e}"); return e.code
    except KeyboardInterrupt:
        log("interrupted"); return 1

if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())
