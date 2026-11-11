"""DeepSeek Kit auto-updater check (runs on kit installs; does nothing on the source PC).

  python ds_update_check.py --auto   (launcher) if a newer kit version exists, show what changed and install it (DS_UPDATE_ASK=1 to ask first)
  python ds_update_check.py --hook   (SessionStart / prompt hook) tells the user an update is waiting
  python ds_update_check.py --now    (deepseek-update) install right away, no question
Checks at most every 10 minutes, 3-second timeout, never blocks the launch on errors.
"""
import json, os, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "Racupollashd/nx231-build"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/"
STATE = os.path.join(HERE, "dsmode", "update_state.json")
EVERY = 10 * 60


def local_version():
    try:
        return open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return "0"


def remote_version():
    """GitHub API first (fresh within ~1 min); raw file as fallback (can be ~5 min stale)."""
    try:
        import base64
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/contents/VERSION?ref=main",
                                     headers={"User-Agent": "deepseek-kit", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return base64.b64decode(json.load(r)["content"]).decode().strip()
    except Exception:
        req = urllib.request.Request(RAW + "VERSION", headers={"User-Agent": "deepseek-kit"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.read().decode().strip()


def newer(a, b):
    try:
        return [int(x) for x in a.split(".")] > [int(x) for x in b.split(".")]
    except ValueError:
        return a != b


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--hook"
    if mode == "--hook":
        try:
            sys.stdin.read()
        except Exception:
            pass
    if os.path.isdir(os.path.join(HERE, "kits", "kitsrc")):
        return                                    # source PC: updates are published from here
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    if time.time() - st.get("t", 0) < EVERY and mode != "--now":
        rv = st.get("remote")
    else:
        try:
            rv = remote_version()
        except Exception:                          # offline: don't retry until the next 10-min window
            st["t"] = time.time()
            try:
                os.makedirs(os.path.dirname(STATE), exist_ok=True)
                json.dump(st, open(STATE, "w", encoding="utf-8"))
            except Exception:
                pass
            return
        st.update({"t": time.time(), "remote": rv})
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(st, open(STATE, "w", encoding="utf-8"))
    lv = local_version()
    if not rv or not newer(rv, lv):
        return
    if mode == "--auto":
        try:
            req = urllib.request.Request(RAW + "CHANGELOG.md?t=" + str(int(time.time())), headers={"User-Agent": "deepseek-kit"})
            with urllib.request.urlopen(req, timeout=3) as r:
                news = [l for l in r.read().decode("utf-8", "replace").splitlines() if l.strip()]
            news = [l for l in news if not newer(lv, l.split(":")[0].strip()) and l.split(":")[0].strip() != lv][:6]
        except Exception:
            news = []
        print(f"\n  Updating DeepSeek Kit {lv} -> {rv}. What's new:")
        for l in news:
            print("   - " + l)
        if os.environ.get("DS_UPDATE_ASK") == "1":   # optional: set this env var to be asked first
            try:
                ans = input("  Install it now? [Y/n] ").strip().lower()
            except EOFError:
                ans = "n"
            if ans not in ("", "y", "yes", "s", "si"):
                print("  Skipped. It will ask again next time (or run: deepseek-update).\n")
                return
    if mode in ("--auto", "--now"):
        print(f"Installing DeepSeek Kit {rv} (about 20 s)...", flush=True)
        ps = os.path.join(HERE, "update.ps1")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps], timeout=300)
        st["t"] = 0
        json.dump(st, open(STATE, "w", encoding="utf-8"))
    else:
        if st.get("told") == rv:
            return                                # say it once per version
        st["told"] = rv
        json.dump(st, open(STATE, "w", encoding="utf-8"))
        print(json.dumps({"systemMessage": f"DeepSeek Kit update {rv} is ready (you have {lv}). It installs by itself the next time you start deepseek (or type /updatedeepseek now)"}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
