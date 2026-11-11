"""PreToolUse hook (DeepSeek / Qwen mode): when a tool call is going to ask for approval anyway,
add a plain-language explanation to the approval prompt, so a non-programmer knows what they're approving.

Never adds prompts: it only answers "ask" for calls that the permission rules would prompt for anyway
(an "ask" rule matches, or the tool is not on an allow list). Free and instant: no AI call.
Never crashes; on any internal error it prints nothing (normal behaviour).
"""
import fnmatch, json, os, re, sys

HOME = os.path.expanduser("~")
SETTINGS = [os.path.join(HOME, ".claude", "settings.json"), os.path.join(HOME, ".claude", "settings.local.json")]
NO_PERMISSION_TOOLS = {"Read", "Glob", "Grep", "TodoWrite", "TodoRead", "ExitPlanMode", "EnterPlanMode", "ToolSearch",
                       "ListMcpResourcesTool", "ReadMcpResourceTool", "TaskOutput", "KillShell", "Task", "Agent", "AskUserQuestion"}


HERE = os.path.dirname(os.path.abspath(__file__))
ASK_FILE = os.path.join(HERE, "ds_ask_rules.json")


def load_rules():
    """allow = global settings + the mode's settings file; ask = ds_ask_rules.json (+ any settings 'ask').
    The approval rules live in ds_ask_rules.json on purpose: when Claude Code's own rules trigger a prompt,
    it shows its technical message instead of ours."""
    extra = os.environ.get("DS_EXPLAIN_SETTINGS") or os.path.join(HERE, "claude-ds.settings.json")
    allow, ask = [], []
    for f in SETTINGS + [extra]:
        try:
            p = json.load(open(f, encoding="utf-8")).get("permissions", {})
            allow += p.get("allow", [])
            ask += p.get("ask", [])
        except Exception:
            pass
    ask += json.load(open(ASK_FILE, encoding="utf-8"))["ask"]   # missing/broken file -> exception -> fail safe
    return allow, ask


def split_rule(r):
    m = re.match(r"^([\w\-\.]+)(?:\((.*)\))?$", r.strip())
    return (m.group(1), m.group(2)) if m else (r, None)


def subcommands(cmd):
    return [c.strip() for c in re.split(r"&&|\|\||;|\||\n", cmd) if c.strip()]


def rule_matches(rule, tool, inp):
    name, pat = split_rule(rule)
    if name.startswith("mcp__") and pat is None:
        return tool == name or tool.startswith(name + "__")
    if name != tool:
        return False
    if pat is None:
        return True
    if tool in ("Bash", "PowerShell"):
        return any(fnmatch.fnmatchcase(c, pat) or fnmatch.fnmatchcase(c.lower(), pat.lower())
                   for c in subcommands(str(inp.get("command", ""))))
    path = str(inp.get("file_path") or inp.get("notebook_path") or "").replace("\\", "/")
    pat = os.path.expanduser(pat).replace("\\", "/")
    return fnmatch.fnmatch(path.lower(), pat.lower())


def will_prompt(tool, inp):
    allow, ask = load_rules()
    if any(rule_matches(r, tool, inp) for r in ask):
        return True
    if tool in NO_PERMISSION_TOOLS:
        return False
    return not any(rule_matches(r, tool, inp) for r in allow)


# ---------- plain-language text ----------
def short(p, n=60):
    p = str(p).replace("\\", "/")
    return p if len(p) <= n else "..." + p[-(n - 3):]


def fname(p):
    p = str(p).replace("\\", "/").rstrip("/")
    return f"'{os.path.basename(p)}' (in {short(os.path.dirname(p), 45)})"


COMMANDS = [  # (regex on one sub-command, risk, explanation)
    (r"^(rm|del|erase|rmdir|rd)\b|Remove-Item|Clear-Content", "RISKY", "DELETE files or folders: {args}. Deleted files may not be recoverable."),
    (r"^format\b|Format-Volume", "VERY RISKY", "ERASE a whole drive. Everything on it would be wiped."),
    (r"^git\s+push", "check", "UPLOAD code changes to the internet (GitHub or similar). Other people may see them."),
    (r"^git\s+reset\s+--hard|^git\s+checkout\s+--|^git\s+clean", "RISKY", "THROW AWAY unsaved code changes in this project. They cannot be brought back."),
    (r"^(taskkill|kill)\b|Stop-Process", "check", "FORCE-CLOSE a running program ({args}). Unsaved work in it would be lost."),
    (r"Stop-Service|Set-Service", "RISKY", "STOP or CHANGE a Windows background service ({args})."),
    (r"^(shutdown)\b|Restart-Computer|Stop-Computer", "RISKY", "SHUT DOWN or RESTART your PC."),
    (r"^reg(\.exe)?\b|Set-ItemProperty|Remove-ItemProperty|New-ItemProperty", "RISKY", "CHANGE a Windows system setting (the registry)."),
    (r"^npm\s+publish", "check", "PUBLISH a package publicly on the internet."),
    (r"winget\s+install|choco\s+install|pip\s+install|npm\s+(i|install)\b|msiexec", "check", "INSTALL software from the internet: {args}."),
    (r"curl|wget|Invoke-WebRequest|\biwr\b|Invoke-RestMethod|\birm\b", "check", "DOWNLOAD something from the internet: {args}."),
]


def plain_args(sc):
    """The things a command acts on, readable: quoted paths kept whole, flags dropped, paths shown as file name
    plus a short folder hint. Example: Remove-Item -LiteralPath "C:\\a\\b\\notes.txt" -> 'notes.txt' (in ...\\b)"""
    toks = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', sc)[1:]
    if toks and toks[0].lower() in ("install", "uninstall", "i", "push", "publish", "reset", "clean", "checkout"):
        toks = toks[1:]                                  # the sub-command verb is already in the sentence
    out = []
    for t in toks:
        t = t.strip("'\"")
        if not t or t.startswith("-") or re.match(r"^\$\w+$", t):
            continue
        if re.search(r"[\\/]", t) and len(t) > 25:
            p = t.replace("\\", "/").rstrip("/")
            parent = os.path.basename(os.path.dirname(p))
            t = f"'{os.path.basename(p)}'" + (f" (in ...\\{parent})" if parent else "")
        out.append(t)
    s = ", ".join(out)
    return s if len(s) <= 140 else s[:137] + "..."


def explain_command(cmd, desc):
    lines = []
    for sc in subcommands(cmd):
        for rx, risk, text in COMMANDS:
            if re.search(rx, sc, re.I):
                args = plain_args(sc) or "(see the details below)"
                lines.append(f"[{risk}] " + text.format(args=args))
                break
    if not lines:
        lines.append("Run a command on your PC.")
    if desc:
        lines.append(f"Its reason: {desc}")
    return lines


def explain(tool, inp):
    t = tool.split("__")[-1] if tool.startswith("mcp__") else tool
    if tool in ("Bash", "PowerShell"):
        return explain_command(str(inp.get("command", "")), inp.get("description"))
    if tool in ("Edit", "MultiEdit"):
        p = inp.get("file_path", "")
        extra = " This is one of your AI's own settings/instruction files." if re.search(r"[\\/]\.claude|claude-ds|Tools[\\/](DeepSeek|Qwen)|\.local[\\/]bin", str(p), re.I) else ""
        return [f"CHANGE the file {fname(p)}.{extra}", "Only the shown part changes; the rest of the file stays."]
    if tool == "Write":
        p = inp.get("file_path", "")
        return [f"CREATE or REPLACE the whole file {fname(p)}. If it already exists, its old content is overwritten."]
    if tool == "NotebookEdit":
        return [f"CHANGE the notebook {fname(inp.get('notebook_path', ''))}."]
    if tool == "WebFetch":
        return [f"OPEN and READ the web page {short(inp.get('url', ''), 80)}"]
    if tool.startswith("mcp__chrome__") or tool.startswith("mcp__playwright__"):
        where = "your real Chrome (your logins)" if tool.startswith("mcp__chrome__") else "a separate, logged-out browser"
        el = inp.get("element") or inp.get("ref") or "an element"
        table = {
            "browser_click": f"CLICK on '{el}' in {where}.",
            "browser_type": f"TYPE \"{str(inp.get('text', ''))[:80]}\" into '{el}' in {where}" + (" and press Enter (this may SEND/SUBMIT it)." if inp.get("submit") else "."),
            "browser_fill_form": f"FILL IN a form in {where} ({len(inp.get('fields') or [])} fields).",
            "browser_press_key": f"PRESS the key '{inp.get('key', '')}' in {where}" + (" (Enter can submit or send things)." if str(inp.get('key', '')).lower() == "enter" else "."),
            "browser_select_option": f"PICK an option in a dropdown '{el}' in {where}.",
            "browser_file_upload": f"UPLOAD files from your PC to the website: {', '.join(short(p, 50) for p in (inp.get('paths') or []))}.",
            "browser_handle_dialog": f"ANSWER a browser pop-up ({'accept' if inp.get('accept') else 'dismiss'}) in {where}.",
            "browser_evaluate": f"RUN a small script inside the open web page in {where}. It can read or change that page.",
            "browser_run_code_unsafe": f"RUN code that controls {where} freely. Only approve if you asked for this.",
            "browser_drag": f"DRAG and drop on the page in {where}.",
            "browser_drop": f"DROP something on the page in {where}.",
            "browser_close": f"CLOSE the browser tab in {where}.",
            "browser_navigate_back": f"GO BACK one page in {where}.",
            "browser_hover": f"MOVE the mouse over '{el}' in {where}.",
            "browser_resize": f"RESIZE the browser window in {where}.",
        }
        return [table.get(t, f"USE the browser tool '{t}' in {where}.")]
    server = tool.split("__")[1] if tool.startswith("mcp__") else ""
    return [f"USE the tool '{t}'" + (f" from '{server}'" if server else "") + "."]


BAR = "=" * 56


def ask(lines):
    text = "\n".join([BAR, ">>>  WHAT IT WANTS TO DO, IN PLAIN WORDS  <<<", BAR] + ["  " + l for l in lines] +
                     [BAR, "1 = Yes, do it      2 = No, don't", BAR])
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                             "permissionDecisionReason": text}}))


RISKY_TOOLS = ("Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit")


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    tool, inp = d.get("tool_name", ""), d.get("tool_input") or {}
    try:
        if not will_prompt(tool, inp):
            return
    except Exception:
        # fail SAFE: if the rules can't be checked, ask anyway (the rules live only in this hook now)
        if tool in RISKY_TOOLS or tool.startswith("mcp__"):
            ask(["(The safety check could not run, so it asks to be sure.)", "Approve only if you understand what it wants to do."])
        return
    try:
        lines = explain(tool, inp)
    except Exception:
        lines = [f"Use the tool '{tool}'. Approve only if you asked for this."]
    ask(lines)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
