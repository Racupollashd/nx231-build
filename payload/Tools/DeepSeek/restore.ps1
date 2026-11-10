# DeepSeek Kit restore: put back a version saved before an update.
#   restore.ps1            restore the most recent backup (= the version before the last update)
#   restore.ps1 -List      show all backups, newest first
#   restore.ps1 -Pick 3    restore backup number 3 from the list
# The current version is backed up first, so a restore can itself be undone.
# Keeps: the API key, memories, CLAUDE.md, ~/.claude/settings.json, spend ledger and logs.
param([switch]$List, [int]$Pick = 1, [string]$TargetHome = $env:USERPROFILE)
$ErrorActionPreference = 'Stop'
$H = $TargetHome
$DS = "$H\Tools\DeepSeek"
function Say($m, $c = 'Cyan') { Write-Host $m -ForegroundColor $c }
# same as in update.ps1 (kept in sync; update.ps1 must stay self-contained for `irm | iex`)
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

$all = @(Get-ChildItem "$DS\backups" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending |
  ForEach-Object { $i = @{}; Get-Content "$($_.FullName)\INFO.txt" -ErrorAction SilentlyContinue | ForEach-Object { $k, $v = $_ -split '=', 2; $i[$k] = $v }
                   [pscustomobject]@{ Dir = $_.FullName; Version = $i.version; Saved = $i.saved; Why = $i.why } } |
  Sort-Object Saved -Descending)
if (-not $all) { Say "No backups yet. One is made automatically before every update." Yellow; return }
$cur = if (Test-Path "$DS\VERSION") { (Get-Content "$DS\VERSION" -Raw).Trim() } else { 'unknown' }

if ($List) {
  Say "Installed now: $cur`n"
  $n = 0; foreach ($b in $all) { $n++; Say ("  {0}. version {1}   saved {2}   ({3})" -f $n, $b.Version, $b.Saved, $b.Why) White }
  Say "`nRestore one with: /backup <number>   (or: deepseek-restore -Pick <number>)" Gray
  return
}
if ($Pick -lt 1 -or $Pick -gt $all.Count) { Say "There is no backup number $Pick (there are $($all.Count))." Red; return }
$b = $all[$Pick - 1]
Say "Restoring version $($b.Version) (saved $($b.Saved)) over $cur ..."
$safety = Save-KitBackup $H $cur 'before restore'
Copy-Item "$($b.Dir)\ds\*" $DS -Force
Copy-Item "$($b.Dir)\bin\*" "$H\.local\bin" -Force -ErrorAction SilentlyContinue
if (Test-Path "$($b.Dir)\claude") { Copy-Item "$($b.Dir)\claude\*" "$H\.claude" -Recurse -Force }
$ErrorActionPreference = 'Continue'
$r = cmd /c "cd /d `"$DS`" && python dsk_selftest.py 2>&1" | Select-Object -Last 1
Say "  self-test: $r" Gray
Say "Restored version $($b.Version). The version you just left ($cur) was saved too, so this can be undone." Green
Say "Close deepseek (/exit) and start it again to use the restored version." White
