---
description: Update the DeepSeek Kit to the newest version on GitHub (backs up the current version first)
allowed-tools: PowerShell, Bash
---
Run this exact command with the PowerShell tool (one call, timeout 300000 ms), then stop:

powershell -NoProfile -ExecutionPolicy Bypass -File "__HOMEBS__\Tools\DeepSeek\update.ps1"

Then tell the user in 2-4 short lines, in their language:
1. The result: "Updated from X to Y", or "Already up to date (X)", or the error text.
2. If it updated: the "What's new" lines, in plain words.
3. If it updated: "Type /exit and start deepseek again to use the new version. If anything breaks, type /backup to go back."
Do not run anything else and do not try to fix errors yourself; show the error.
