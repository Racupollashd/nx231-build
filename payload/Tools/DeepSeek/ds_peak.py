"""DeepSeek peak / off-peak clock (single source of truth for dsk, ds_budget and the status line).

Official rule (api-docs.deepseek.com/quick_start/pricing, checked 2026-11-10):
  PEAK = 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday, excluding Chinese public holidays.
  Everything else is OFF-PEAK = half price, including weekends and Chinese public holidays in full.
Holidays are Beijing-calendar dates (UTC+8), listed in ds_holidays.json (State Council notice each November).
Weekend make-up workdays are treated as off-peak (the docs say weekends are off-peak in full).

  python ds_peak.py           one-shot summary in local time
  python ds_peak.py --watch   live one-line clock (Ctrl+C to stop)
  python ds_peak.py --short   the compact text the status line uses
  python ds_peak.py --world   peak hours in major cities
  --tz Asia/Tokyo (or env DS_TZ) forces a zone. Otherwise the zone comes from where the PC is
  (public-IP lookup, re-checked every 20 min), falling back to the Windows clock when offline.
  A VPN or Tailscale exit node makes the IP lookup report the VPN's country.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
HOLIDAYS_FILE = os.path.join(HERE, "ds_holidays.json")
WINDOWS = ((1, 4), (6, 10))           # UTC hours [start, end)
BEIJING = timezone(timedelta(hours=8))
_hol = None


def holidays():
    global _hol
    if _hol is None:
        try:
            data = json.load(open(HOLIDAYS_FILE, encoding="utf-8"))
            _hol = {d for k, v in data.items() if k[:1].isdigit() for d in v}
            _hol_years.update(k for k in data if k[:1].isdigit())
        except Exception:
            _hol = set()
    return _hol


_hol_years = set()


def is_holiday(t):
    return t.astimezone(BEIJING).strftime("%Y-%m-%d") in holidays()


def is_peak(t=None):
    t = (t or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if t.weekday() >= 5:
        return False
    if not any(a <= t.hour < b for a, b in WINDOWS):
        return False
    return not is_holiday(t)


def next_change(t=None):
    """UTC datetime of the next peak<->off-peak switch (state can only change on whole hours)."""
    t = (t or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now = is_peak(t)
    h = t.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 21):
        h += timedelta(hours=1)
        if is_peak(h) != now:
            return h
    return None


def holidays_missing(t=None):
    """True from 15 Dec if next year's Chinese holidays aren't in ds_holidays.json yet."""
    t = t or datetime.now(timezone.utc)
    holidays()
    if str(t.year) not in _hol_years:
        return True
    return t.month == 12 and t.day >= 15 and str(t.year + 1) not in _hol_years


def fmt_delta(td):
    m = max(0, int(td.total_seconds() // 60))
    d, m = divmod(m, 1440)
    h, m = divmod(m, 60)
    return (f"{d}d " if d else "") + (f"{h}h{m:02d}m" if h or d else f"{m}m")


TZ_CACHE = os.path.join(os.environ.get("TEMP", HERE), "ds_peak_tz.json")
TZ_TTL = 20 * 60          # re-check location every 20 min, so a flight or a new country is picked up


def _ip_timezone():
    """IANA zone of where this PC actually is, from its public IP (cached). None if offline."""
    try:
        c = json.load(open(TZ_CACHE, encoding="utf-8"))
        if time.time() - c["t"] < TZ_TTL:
            return c.get("tz"), c.get("place")
    except Exception:
        c = None
    import urllib.request
    for url, parse in (("https://ipapi.co/json/", lambda d: (d.get("timezone"), ", ".join(x for x in (d.get("city"), d.get("country_name")) if x))),
                       ("http://ip-api.com/json/?fields=status,timezone,city,country", lambda d: (d.get("timezone"), ", ".join(x for x in (d.get("city"), d.get("country")) if x)))):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ds-peak/1.0"})
            with urllib.request.urlopen(req, timeout=2) as r:
                tz, place = parse(json.load(r))
            if tz:
                json.dump({"t": time.time(), "tz": tz, "place": place}, open(TZ_CACHE, "w", encoding="utf-8"))
                return tz, place
        except Exception:
            continue
    # offline: remember the failure for 5 min so the status bar doesn't wait 4 s on every refresh
    tz, place = (c.get("tz"), c.get("place")) if c else (None, None)
    try:
        json.dump({"t": time.time() - TZ_TTL + 300, "tz": tz, "place": place}, open(TZ_CACHE, "w", encoding="utf-8"))
    except Exception:
        pass
    return tz, place


def zone():
    """(tzinfo, label). Order: DS_TZ env / --tz override > location from IP > Windows clock."""
    from zoneinfo import ZoneInfo
    want = os.environ.get("DS_TZ")
    if "--tz" in sys.argv:
        i = sys.argv.index("--tz")
        want = sys.argv[i + 1] if i + 1 < len(sys.argv) else want
    if want:
        try:
            return ZoneInfo(want), want
        except Exception:
            pass
    tz, place = _ip_timezone()
    if tz:
        try:
            return ZoneInfo(tz), place or tz
        except Exception:
            pass
    return None, "PC clock"


_zone, _zone_t = None, 0.0


def local(t):
    global _zone, _zone_t
    if _zone is None or time.time() - _zone_t > 300:     # long-running --watch picks up a new country
        _zone, _zone_t = zone(), time.time()
    return t.astimezone(_zone[0]) if _zone[0] else t.astimezone()


WORLD = [("Madrid/Paris/Rome", "Europe/Madrid"), ("London", "Europe/London"), ("New York", "America/New_York"),
         ("Los Angeles", "America/Los_Angeles"), ("Mexico City", "America/Mexico_City"), ("Sao Paulo", "America/Sao_Paulo"),
         ("Dubai", "Asia/Dubai"), ("India", "Asia/Kolkata"), ("Beijing", "Asia/Shanghai"), ("Tokyo", "Asia/Tokyo"),
         ("Sydney", "Australia/Sydney")]


def world():
    from zoneinfo import ZoneInfo
    t = datetime.now(timezone.utc)
    state = "PEAK (full price)" if is_peak(t) else "OFF-PEAK (half price)"
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    wins = [(day.replace(hour=a), day.replace(hour=b)) for a, b in WINDOWS]
    lines = [f"Right now everywhere: {state}  ({t:%a %H:%M} UTC)", "", f"{'City':20} {'local now':12} peak hours today (local)"]
    for name, z in WORLD:
        zi = ZoneInfo(z)
        lines.append(f"{name:20} {t.astimezone(zi):%a %H:%M}    " +
                     ", ".join(f"{a.astimezone(zi):%H:%M}-{b.astimezone(zi):%H:%M}" for a, b in wins))
    lines.append("")
    lines.append("Peak is the same moment worldwide; only the clock time differs. Weekends (UTC) and Chinese holidays are off-peak.")
    return "\n".join(lines)


SOON = timedelta(minutes=15)
G, R, Y, B, X = "[32m", "[31m", "[33m", "[1m", "[0m"


def short(t=None, color=False):
    """Status-line text, e.g. 'OFF-PEAK 1/2 price, peak 07:00 (in 2h13m)'. color=True adds ANSI colors."""
    t = t or datetime.now(timezone.utc)
    nc = next_change(t)
    c = (lambda code, txt: f"{code}{txt}{X}") if color else (lambda code, txt: txt)
    if is_peak(t):
        s = c(R + B, "PEAK full price")
        if nc:
            s += f", off-peak {local(nc):%H:%M} (in {fmt_delta(nc - t)})"
    else:
        s = c(G + B, "OFF-PEAK 1/2 price")
        if nc:
            when = f"{local(nc):%a %H:%M}" if (nc - t) > timedelta(hours=20) else f"{local(nc):%H:%M}"
            soon = (nc - t) <= SOON
            s += ", " + (c(Y + B, f"PEAK SOON {when} (in {fmt_delta(nc - t)})") if soon else f"peak {when} (in {fmt_delta(nc - t)})")
    if is_holiday(t):
        s += " [China holiday]"
    if holidays_missing(t):
        s += " [update ds_holidays.json]"
    return s


ALERT_STATE = os.path.join(os.environ.get("TEMP", HERE), "ds_peak_alert.json")


def alert(t=None):
    """One-time warning text when peak/off-peak switched since the last check, or peak starts within 15 min.
    Returns None when there is nothing new to say."""
    t = t or datetime.now(timezone.utc)
    now, nc = is_peak(t), next_change(t)
    try:
        st = json.load(open(ALERT_STATE, encoding="utf-8"))
    except Exception:
        st = {}
    msg = None
    if "peak" in st and st["peak"] != now:
        msg = ("PEAK started: DeepSeek is now FULL price" + (f" until {local(nc):%H:%M}" if nc else "")) if now else               ("OFF-PEAK started: DeepSeek is now HALF price" + (f" until {local(nc):%a %H:%M}" if nc else ""))
    elif not now and nc and (nc - t) <= SOON and st.get("soon") != nc.isoformat():
        msg = f"Heads up: PEAK (full price) starts at {local(nc):%H:%M}, in {fmt_delta(nc - t)}"
        st["soon"] = nc.isoformat()
    st["peak"] = now
    try:
        json.dump(st, open(ALERT_STATE, "w", encoding="utf-8"))
    except Exception:
        pass
    return msg


def today_windows(t=None):
    """Peak spans that fall inside YOUR local calendar day (00:00-24:00 where you are)."""
    t = t or datetime.now(timezone.utc)
    lt = local(t)
    start = lt.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    out, run_s = [], None
    h = start.replace(minute=0)                      # walk whole UTC hours across the local day
    while h < end + timedelta(hours=1):
        p = start <= h < end and is_peak(h)
        if p and run_s is None:
            run_s = max(h, start)
        if not p and run_s is not None:
            out.append(f"{local(run_s):%H:%M}-{local(min(h, end)):%H:%M}".replace("-00:00", "-24:00"))
            run_s = None
        h += timedelta(hours=1)
    return out


def summary():
    t = datetime.now(timezone.utc)
    lt = local(t)
    lines = [f"Now {lt:%a %d %b %H:%M} {lt.strftime('%Z')} ({_zone[1]}) = {t:%H:%M} UTC", short(t)]
    w = today_windows(t)
    lines.append("Peak today (your time): " + (", ".join(w) if w else "none, all day off-peak"))
    lines.append("Rule: peak 01-04 + 06-10 UTC Mon-Fri; weekends and Chinese holidays off-peak.")
    return "\n".join(lines)


def main():
    if "--short" in sys.argv:
        print(short())
    elif "--alert" in sys.argv:          # hook mode: prints a systemMessage JSON only when there is news
        try:
            sys.stdin.read()
        except Exception:
            pass
        m = alert()
        if m:
            print(json.dumps({"systemMessage": m}))
    elif "--world" in sys.argv:
        print(world())
    elif "--watch" in sys.argv:
        try:
            while True:
                t = datetime.now(timezone.utc)
                sys.stdout.write(f"\r{local(t):%H:%M:%S}  {short(t)}" + " " * 10)
                sys.stdout.flush()
                time.sleep(1)
        except KeyboardInterrupt:
            print()
    else:
        print(summary())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
