"""DeepSeek monthly budget for THIS PC (dsk + deepseek mode), plus an account-wide view.

my spend      = dsk ledger this month + every deepseek-mode message this month (from the transcripts,
                at DeepSeek prices, same math as ds_statusline.py). Others sharing the key don't count.
account spend = balance at month start + top-ups - current balance (includes friends on the key).

The cap (DEEPSEEK_MONTHLY_CAP, default 5) applies to MY spend: warn at 80%, block prompts at 100%.
Account spend only produces an info warning (once a day). Balance is fetched at most every 2 min.
Override for the rest of a month: create dsmode/over_cap_ok_<YYYY-MM>.

  python ds_budget.py status | session-start | prompt
Never raises.
"""
import glob, json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "dsmode", "budget_state.json")
TXCACHE = os.path.join(HERE, "dsmode", "budget_tx_cache.json")
LEDGER = os.path.join(HERE, "ledger.jsonl")
PROJECTS = os.path.expanduser("~/.claude/projects")
TTL, WARN_FRAC, LOW_BALANCE = 120, 0.8, 1.0
PRICES = {"flash": (0.30, 0.006, 1.20), "pro": (1.32, 0.044, 3.96)}   # peak $/M: miss, hit, out


def cap():
    try:
        return float(os.environ.get("DEEPSEEK_MONTHLY_CAP", "5.00"))
    except ValueError:
        return 5.0


def month():
    return time.strftime("%Y-%m")


def jload(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def jsave(p, obj):
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, p)
    except OSError:
        pass


# ---------- account balance ----------
def fetch_balance():
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = winreg.QueryValueEx(k, "DEEPSEEK_API_KEY")[0]
        except OSError:
            pass
    req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=3) as r:
        return float(json.load(r)["balance_infos"][0]["total_balance"])


def update(balance, path=STATE):
    st = jload(path, None)
    if not st or st.get("month") != month():
        # new month (or no state): start counting from now; spend in a gap between months is not guessed
        st = {"month": month(), "start": balance, "last": balance, "warned": (st or {}).get("warned")}
    if balance > st["last"] + 0.0005:          # top-up
        st["start"] += balance - st["last"]
    st["last"], st["t"] = balance, time.time()
    jsave(path, st)
    return st


def cached_spend(path=STATE):
    st = jload(path, None)
    if not st or st.get("month") != month():
        return 0.0
    return max(0.0, st["start"] - st["last"])


def account():
    """(spend, balance) using the cached reading if it is < TTL old."""
    st = jload(STATE, None)
    fresh = st and st.get("month") == month() and time.time() - st.get("t", 0) < TTL
    failed_recently = st and time.time() - st.get("fail_t", 0) < 60      # offline: don't retry every prompt
    if not fresh and not failed_recently:
        try:
            st = update(fetch_balance())
        except Exception:
            if st:
                st["fail_t"] = time.time()
                jsave(STATE, st)
            else:
                jsave(STATE, {"fail_t": time.time()})
    if not st or st.get("month") != month() or "last" not in st:
        return None, None
    return max(0.0, st["start"] - st["last"]), st["last"]


# ---------- my own spend ----------
def is_peak(ts):
    from datetime import datetime, timezone
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return True
    try:
        sys.path.insert(0, HERE); import ds_peak; return ds_peak.is_peak(t)
    except Exception:
        return t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)


def msg_cost(model, u, ts):
    p = PRICES["pro" if "pro" in model else "flash"]
    miss = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
    usd = (miss * p[0] + (u.get("cache_read_input_tokens") or 0) * p[1] + (u.get("output_tokens") or 0) * p[2]) / 1e6
    return usd if is_peak(ts) else usd / 2


def deepseek_mode_spend():
    """Incremental scan of transcripts touched this month; only deepseek-model messages of this month."""
    m = month()
    cache = jload(TXCACHE, {})
    if cache.get("month") != m:
        cache = {"month": m, "files": {}}
    start = time.mktime(time.strptime(m + "-01", "%Y-%m-%d"))
    files = glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")) + glob.glob(os.path.join(PROJECTS, "*", "*", "subagents", "*.jsonl"))
    total = 0.0
    for f in files:
        try:
            if os.path.getmtime(f) < start:
                continue
            size = os.path.getsize(f)
        except OSError:
            continue
        c = cache["files"].get(f, {"off": 0, "msgs": {}})
        if size < c["off"]:
            c = {"off": 0, "msgs": {}}
        if size > c["off"]:
            with open(f, "rb") as fh:
                fh.seek(c["off"])
                data = fh.read()
            end = data.rfind(b"\n") + 1
            for line in data[:end].splitlines():
                if b'"deepseek' not in line or b'"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = d.get("message") or {}
                model, u, ts = msg.get("model") or "", msg.get("usage"), d.get("timestamp", "")
                if d.get("type") != "assistant" or not u or "deepseek" not in model or not ts.startswith(m):
                    continue
                c["msgs"][msg.get("id") or d.get("uuid")] = msg_cost(model, u, ts)
            c["off"] += end
            cache["files"][f] = c
        total += sum(c["msgs"].values())
    jsave(TXCACHE, cache)
    return total


def dsk_spend():
    m, settled, res = month(), {}, {}
    try:
        lines = open(LEDGER, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:                                   # one broken line (crash mid-write) must not hide the rest
            e = json.loads(line)
            if e.get("month") != m:
                continue
            if e["kind"] in ("settle", "carry", "manual"):
                settled[e["id"]] = e["usd"]
            elif e["kind"] == "reserve" and time.time() - e["ts"] < 900:
                res[e["id"]] = e["usd"]
        except Exception:
            continue
    return sum(settled.values()) + sum(v for k, v in res.items() if k not in settled)


def my_spend():
    return dsk_spend() + deepseek_mode_spend()


# ---------- verdict ----------
def override_on():
    return os.path.exists(os.path.join(HERE, "dsmode", "over_cap_ok_" + month()))


def verdict(once_a_day=True):
    c, mine = cap(), my_spend()
    acct, bal = account()
    line = f"DeepSeek this month: you ${mine:.2f} of ${c:.2f} cap"
    if acct is not None:
        line += f" | whole key ${acct:.2f} (others ~${max(0.0, acct - mine):.2f}) | balance ${bal:.2f}"
    if mine >= c and not override_on():
        return "block", (line + ". YOUR CAP IS REACHED. To keep going this month, create the empty file "
                         f"__HOMEFS__/Tools/DeepSeek/dsmode/over_cap_ok_{month()} or raise DEEPSEEK_MONTHLY_CAP.")
    msgs = []
    if mine >= c * WARN_FRAC:
        msgs.append(f"you've used {mine / c:.0%} of your cap")
    if bal is not None and bal < LOW_BALANCE:
        msgs.append("balance under $1, top up soon")
    if not msgs:
        return "ok", line
    st = jload(STATE, {})
    today = time.strftime("%Y-%m-%d")
    if once_a_day and st.get("warned") == today:
        return "ok", line
    st["warned"] = today                       # saved even when there is no state yet (offline first run)
    jsave(STATE, st)
    return "warn", line + ": " + "; ".join(msgs)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if mode in ("prompt", "session-start"):
            try:
                sys.stdin.read()
            except Exception:
                pass
        kind, msg = verdict(once_a_day=(mode != "status"))
        if mode == "status":
            print(msg)
        elif kind == "block" and mode == "prompt":
            print(json.dumps({"decision": "block", "reason": msg}))
        elif kind in ("block", "warn"):
            print(json.dumps({"systemMessage": msg}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
