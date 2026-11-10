# DeepSeek Kit updater: downloads the latest kit from GitHub and installs it over the current one.
# Keeps: the API key, memories, CLAUDE.md, ~/.claude/settings.json, spend ledger and logs.
# First time (one line in PowerShell):
#   irm https://raw.githubusercontent.com/Racupollashd/nx231-build/main/update.ps1 | iex
param([switch]$Force, [string]$SourceDir, [string]$TargetHome = $env:USERPROFILE)   # SourceDir/TargetHome: for testing
$ErrorActionPreference = 'Stop'
$Repo = 'Racupollashd/nx231-build'
$H = $TargetHome
$DS = "$H\Tools\DeepSeek"
function Say($m, $c = 'Cyan') { Write-Host $m -ForegroundColor $c }

# Kit-owned files = what updates replace and what backups hold. Never memories, CLAUDE.md, settings.json, ledger, logs.
function Save-KitBackup($H, $ver, $why) {
  $DS = "$H\Tools\DeepSeek"
  $bk = "$DS\backups\v$ver-" + (Get-Date -Format yyyyMMdd-HHmmss)
  New-Item -ItemType Directory -Force "$bk\ds", "$bk\bin", "$bk\claude" | Out-Null
  Get-ChildItem "$DS\*" -File -Include *.py, *.json, *.md, *.cmd, *.ps1, VERSION -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch '^ledger' } | Copy-Item -Destination "$bk\ds" -ErrorAction SilentlyContinue
  Get-Item "$H\.local\bin\claude-ds.cmd", "$H\.local\bin\deepseek.cmd", "$H\.local\bin\peak.cmd", "$H\.local\bin\deepseek-update.cmd", "$H\.local\bin\deepseek-restore.cmd" -ErrorAction SilentlyContinue |
    Copy-Item -Destination "$bk\bin" -ErrorAction SilentlyContinue
  foreach ($d in 'skills\video-summarize', 'agents\log-digest.md', 'commands\updatedeepseek.md', 'commands\backup.md') {
    if (Test-Path "$H\.claude\$d") { $t = Split-Path "$bk\claude\$d"; New-Item -ItemType Directory -Force $t | Out-Null; Copy-Item "$H\.claude\$d" $t -Recurse -Force }
  }
  Set-Content "$bk\INFO.txt" "version=$ver`r`nsaved=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`nwhy=$why" -Encoding utf8
  return $bk
}

if (Test-Path "$DS\kits\kitsrc") { Say "This is the source PC; updates are published from here, not installed." Yellow; return }
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
$tmp = Join-Path $env:TEMP ("dskit-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
  if ($SourceDir) { $src = $SourceDir } else {
    Say "Downloading the latest DeepSeek Kit ..."
    # exact newest commit (avoids GitHub's few-minute cache of "main"); falls back to main
    $ref = 'refs/heads/main'
    try { $ref = (Invoke-RestMethod "https://api.github.com/repos/$Repo/commits/main" -Headers @{ 'User-Agent' = 'deepseek-kit' } -TimeoutSec 10).sha } catch {}
    Invoke-WebRequest "https://github.com/$Repo/archive/$ref.zip" -OutFile "$tmp\kit.zip" -UseBasicParsing
    Expand-Archive "$tmp\kit.zip" "$tmp\x" -Force
    $src = (Get-ChildItem "$tmp\x" -Directory | Select-Object -First 1).FullName
  }
  $new = (Get-Content "$src\VERSION" -Raw).Trim()
  $cur = if (Test-Path "$DS\VERSION") { (Get-Content "$DS\VERSION" -Raw).Trim() } else { 'none' }
  if ($new -eq $cur -and -not $Force) { Say "Already up to date ($cur)." Green; return }
  Say "Updating $cur -> $new"

  # full backup of the current kit version (kept forever; restore with /backup or deepseek-restore)
  $bk = Save-KitBackup $H $cur 'before update'

  # install kit-owned files only (never memories, CLAUDE.md, settings.json, ledger, logs, dsmode state)
  $p = "$src\payload"
  New-Item -ItemType Directory -Force $DS, "$H\.local\bin", "$H\.claude" | Out-Null
  Get-ChildItem "$p\Tools\DeepSeek" -File | Copy-Item -Destination $DS -Force
  Copy-Item "$p\bin\*" "$H\.local\bin" -Force
  foreach ($d in 'skills', 'agents', 'commands') { if (Test-Path "$p\claude\$d") { Copy-Item "$p\claude\$d" "$H\.claude" -Recurse -Force } }
  Copy-Item "$src\update.ps1", "$src\VERSION" $DS -Force
  if (Test-Path "$src\restore.ps1") { Copy-Item "$src\restore.ps1" $DS -Force }

  # this PC's home folder into the path placeholders
  $hb = $H.TrimEnd('\'); $hf = $hb -replace '\\', '/'
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  $files = @(Get-ChildItem "$DS\*" -File -Include *.md, *.json, *.py, *.cmd, *.txt) + @(Get-Item "$H\.local\bin\claude-ds.cmd", "$H\.local\bin\deepseek.cmd", "$H\.local\bin\deepseek-update.cmd", "$H\.local\bin\deepseek-restore.cmd", "$H\.local\bin\peak.cmd" -ErrorAction SilentlyContinue) + @(Get-ChildItem "$H\.claude\skills", "$H\.claude\agents", "$H\.claude\commands" -Recurse -File -ErrorAction SilentlyContinue)
  foreach ($f in $files) {
    $t = [IO.File]::ReadAllText($f.FullName)
    if ($t -match '__HOME(BS|FS)__') { [IO.File]::WriteAllText($f.FullName, $t.Replace('__HOMEBS__', $hb).Replace('__HOMEFS__', $hf), $utf8) }
  }
  try { python -m pip install --user --quiet --disable-pip-version-check tzdata 2>&1 | Out-Null } catch {}

  $ErrorActionPreference = 'Continue'
  $r = cmd /c "cd /d `"$DS`" && python dsk_selftest.py 2>&1" | Select-Object -Last 1
  Say "  self-test: $r" Gray
  Say "Updated to $new. Backup of the old files: $bk" Green
  if (Test-Path "$src\CHANGELOG.md") { Say "`nWhat's new:" White; Get-Content "$src\CHANGELOG.md" | Select-Object -First 12 | ForEach-Object { Say "  $_" Gray } }
} catch {
  Say "Update failed: $($_.Exception.Message). Nothing was changed if it failed before 'Updating'." Red
} finally {
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
