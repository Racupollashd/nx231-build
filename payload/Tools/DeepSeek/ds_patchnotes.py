"""Patch notes after an update (Rainbow-Six-style, short and simple).

update.ps1 writes dsmode/patchnotes_pending.txt (notes for every version between the old and new one).
  python ds_patchnotes.py --show     launcher: print the notes in color before DeepSeek opens (Enter to continue)
  python ds_patchnotes.py --hook     SessionStart hook: show the same notes inside DeepSeek, once
  python ds_patchnotes.py --extract OLD NEW PATCHNOTES.md   (used by update.ps1) -> writes the pending file
Never crashes.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, "dsmode", "patchnotes_pending.txt")
SEEN = os.path.join(HERE, "dsmode", "patchnotes_seen.txt")


def vkey(v):
    try:
        return [int(x) for x in v.split(".")]
    except ValueError:
        return [0]


def extract(old, new, src):
    """Keep only the PATCH blocks with old < version <= new."""
    text = open(src, encoding="utf-8").read()
    blocks = re.split(r"(?m)^(?=PATCH )", text)
    keep = []
    for b in blocks:
        m = re.match(r"PATCH\s+(\S+)", b)
        if m and vkey(old) < vkey(m.group(1)) <= vkey(new):
            keep.append(b.strip())
    if keep:
        os.makedirs(os.path.dirname(PENDING), exist_ok=True)
        open(PENDING, "w", encoding="utf-8").write("\n\n".join(keep) + "\n")


def read_pending():
    try:
        return open(PENDING, encoding="utf-8").read().strip()
    except OSError:
        return ""


def colorize(t):
    out = []
    for line in t.splitlines():
        s = line.strip()
        if s.startswith("PATCH"):
            out.append(f"\033[1;97;44m  {s}  \033[0m")
        elif s in ("NEW", "FIXES", "BALANCING", "REMOVED"):
            c = {"NEW": "92", "FIXES": "93", "BALANCING": "96", "REMOVED": "91"}[s]
            out.append(f"\033[1;{c}m{s}\033[0m")
        elif s.startswith("+"):
            out.append(f"  \033[92m+\033[0m{s[1:]}")
        elif s.startswith("*"):
            out.append(f"  \033[93m*\033[0m{s[1:]}")
        elif s.startswith("~"):
            out.append(f"  \033[96m~\033[0m{s[1:]}")
        elif s.startswith("-"):
            out.append(f"  \033[91m-\033[0m{s[1:]}")
        else:
            out.append(line)
    return "\n".join(out)


def main():
    a = sys.argv[1:]
    if not a:
        return
    if a[0] == "--extract" and len(a) == 4:
        extract(a[1], a[2], a[3])
    elif a[0] == "--show":
        t = read_pending()
        if t:
            sys.stdout.reconfigure(encoding="utf-8")
            print("\n" + "=" * 60 + "\n  DEEPSEEK WAS UPDATED - PATCH NOTES\n" + "=" * 60)
            print(colorize(t))
            print("=" * 60)
            try:
                input("  Press Enter to start DeepSeek ")
            except EOFError:
                pass
    elif a[0] == "--hook":
        try:
            sys.stdin.read()
        except Exception:
            pass
        t = read_pending()
        if t:
            print(json.dumps({"systemMessage": "DEEPSEEK WAS UPDATED - PATCH NOTES\n\n" + t + "\n\n(/backup goes back to the old version if anything feels off)"}))
            try:
                os.replace(PENDING, SEEN)                # show once
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
