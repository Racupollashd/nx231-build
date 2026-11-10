"""Status line for DeepSeek mode (claude-ds / deepseek). Never raises; always prints one line.

session $ = THIS session's cost computed from its own transcript (+ its subagents) at DeepSeek prices
            (cache hit / miss / output, Flash vs Pro, peak vs off-peak by each message's UTC timestamp).
            Independent of other windows, dsk jobs and top-ups.
balance $ = account balance from the API (shared by everything), cached 20 s; '?' marks a stale value.
"""
import glob, json, os, sys, time, urllib.request
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
CACHE = os.path.join(os.environ.get("TEMP", "."), "ds_statusline")
os.makedirs(CACHE, exist_ok=True)

# USD per 1M tokens at PEAK: (cache-miss input, cache-hit input, output). Off-peak = half.
PRICES = {"flash": (0.30, 0.006, 1.20), "pro": (1.32, 0.044, 3.96)}


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ds_peak
except Exception:
    ds_peak = None


def is_peak(ts):
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        t = datetime.now(timezone.utc)
    if ds_peak:
        return ds_peak.is_peak(t)
    return t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)


def msg_cost(model, usage, ts):
    p = PRICES["pro" if "pro" in (model or "") else "flash"]
    miss = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    hit = usage.get("cache_read_input_tokens") or 0
    out = usage.get("output_tokens") or 0
    usd = (miss * p[0] + hit * p[1] + out * p[2]) / 1e6
    return usd if is_peak(ts) else usd / 2


def file_cost(path):
    """Incremental: remembers byte offset + per-message cost, so huge transcripts stay fast."""
    key = os.path.join(CACHE, "tx_" + str(abs(hash(path))) + ".json")
    st = {"off": 0, "msgs": {}}
    try:
        with open(key) as fh:
            st = json.load(fh)
    except Exception:
        pass
    try:
        size = os.path.getsize(path)
    except OSError:
        return sum(st["msgs"].values())
    if size < st["off"]:          # file rewritten: start over
        st = {"off": 0, "msgs": {}}
    if size > st["off"]:
        with open(path, "rb") as fh:
            fh.seek(st["off"])
            data = fh.read()
        end = data.rfind(b"\n") + 1   # only complete lines
        for line in data[:end].splitlines():
            if b'"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("message") or {}
            u = m.get("usage")
            if d.get("type") != "assistant" or not u:
                continue
            mid = m.get("id") or d.get("uuid")
            st["msgs"][mid] = msg_cost(m.get("model"), u, d.get("timestamp", ""))  # last write per id wins
        st["off"] += end
        try:
            with open(key, "w") as fh:
                json.dump(st, fh)
        except Exception:
            pass
    return sum(st["msgs"].values())


def session_cost(transcript):
    if not transcript or not os.path.exists(transcript):
        return None
    total = file_cost(transcript)
    sub_dir = os.path.splitext(transcript)[0]
    for f in glob.glob(os.path.join(sub_dir, "subagents", "*.jsonl")):
        total += file_cost(f)
    return total


def balance():
    f = os.path.join(CACHE, "balance.json")
    c = None
    try:
        with open(f) as fh:
            c = json.load(fh)
        if time.time() - c["t"] < 20:
            return c["b"], False
    except Exception:
        pass
    key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("DEEPSEEK_API_KEY") or ""
    try:
        req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            b = float(json.load(r)["balance_infos"][0]["total_balance"])
        with open(f, "w") as fh:
            json.dump({"t": time.time(), "b": b}, fh)
        try:  # keep the account-wide monthly budget fresh (ds_budget.py)
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import ds_budget; ds_budget.update(b)
        except Exception:
            pass
        return b, False
    except Exception:
        return (c["b"], True) if c else (None, True)


def main():
    try:
        d = json.loads(sys.stdin.read() or "{}")
    except Exception:
        d = {}
    model = (d.get("model") or {}).get("display_name") or "DeepSeek"
    if "deepseek" not in model.lower():
        model = "DeepSeek " + model
    eff = (d.get("effort") or {}).get("level")
    parts = [f"{model} ({eff})" if eff else model]
    pct = (d.get("context_window") or {}).get("used_percentage")
    if pct is not None:
        parts.append(f"ctx {pct:.0f}%")
    try:
        sc = session_cost(d.get("transcript_path"))
    except Exception:
        sc = None
    if sc is not None:
        parts.append(f"session ${sc:.3f}")
    if ds_peak:
        try:
            parts.append(ds_peak.short(color=True))
        except Exception:
            pass
    b, stale = balance()
    if b is not None:
        parts.append(f"balance ${b:.2f}" + ("?" if stale else ""))
    cwd = (d.get("workspace") or {}).get("current_dir") or d.get("cwd")
    if cwd:
        parts.append(os.path.basename(cwd.rstrip("\\/")) or cwd)
    print(" | ".join(parts))


try:
    main()
except Exception:
    print("DeepSeek")
