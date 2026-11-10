"""PreToolUse hook for DeepSeek mode only (registered in claude-ds.settings.json).

Memory backup: before any Edit/Write to a memory folder (or a shell command naming one),
copies the affected memory file(s) to dsmode/mem-backup/<timestamp>/. Identical copies are
skipped; backups older than 30 days are pruned. Never blocks anything, never crashes.
(The secret-file guard was removed on 2026-11-10 at the user's request: it got in the way of building.)
"""
import glob, hashlib, json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP = os.path.join(HERE, "dsmode", "mem-backup")
MEM_DIR_RE = re.compile(r"((?:~|[A-Za-z]:|/)[^\s'\"]*?[\\/]\.claude[\\/](?:projects[\\/][^\\/\s'\"]+[\\/]memory|deepseek-memory))(?=[\\/\s'\"]|$)", re.I)


def norm(p):
    p = re.sub(r"^/([a-zA-Z])/", lambda m: m.group(1).upper() + ":/", p)   # Git Bash /c/... -> C:/...
    return os.path.expanduser(p)
HASHES = os.path.join(BACKUP, "hashes.json")


def _hash(p):
    with open(p, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def backup(paths):
    try:
        seen = json.load(open(HASHES, encoding="utf-8"))
    except Exception:
        seen = {}
    dest = os.path.join(BACKUP, time.strftime("%Y%m%d-%H%M%S"))
    for p in paths:
        try:
            if not os.path.isfile(p):
                continue
            h = _hash(p)
            if seen.get(p) == h:
                continue                      # same content already backed up
            os.makedirs(dest, exist_ok=True)
            proj = os.path.basename(os.path.dirname(os.path.dirname(p)))
            shutil.copy2(p, os.path.join(dest, proj + "__" + os.path.basename(p)))
            seen[p] = h
        except OSError:
            pass
    try:
        os.makedirs(BACKUP, exist_ok=True)
        json.dump(seen, open(HASHES, "w", encoding="utf-8"))
        cutoff = time.time() - 30 * 86400
        for f in glob.glob(os.path.join(BACKUP, "*", "*")):
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        for d in glob.glob(os.path.join(BACKUP, "*")):
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
    except OSError:
        pass


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    tool, inp = d.get("tool_name", ""), d.get("tool_input") or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        p = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if MEM_DIR_RE.match(p):
            backup([norm(p)])
    elif tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command") or "")
        if "memory" not in cmd.lower():
            return
        dirs = {norm(m.group(1)) for m in MEM_DIR_RE.finditer(cmd)}
        if not dirs:                          # memory mentioned without a full path: main project only
            dm = os.path.expanduser("~/.claude/deepseek-memory")          # kit installs use this folder
            dirs = {dm if os.path.isdir(dm) else os.path.expanduser("~/.claude/projects/C--WINDOWS-system32/memory")}
        files = [f for d in dirs for f in glob.glob(os.path.join(d, "*.md"))]
        backup(files)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
