---
description: Restore the DeepSeek Kit to the version before the last update (or pick one with /backup list)
argument-hint: "[list | number]"
allowed-tools: PowerShell, Bash
---
The user typed: /backup $ARGUMENTS

- If the argument is empty: run with the PowerShell tool
  powershell -NoProfile -ExecutionPolicy Bypass -File "__HOMEBS__\Tools\DeepSeek\restore.ps1"
  (this restores the most recent backup = the version before the last update)
- If the argument is "list": run
  powershell -NoProfile -ExecutionPolicy Bypass -File "__HOMEBS__\Tools\DeepSeek\restore.ps1" -List
  and show the list exactly, then tell the user: "Type /backup <number> to restore one."
- If the argument is a number N: run
  powershell -NoProfile -ExecutionPolicy Bypass -File "__HOMEBS__\Tools\DeepSeek\restore.ps1" -Pick N

After a restore, tell the user in 2-3 short lines, in their language: which version is back, that the version they left was saved too (so /backup list can undo it), and "Type /exit and start deepseek again."
Run only that one command. Do not try to fix errors yourself; show the error.
