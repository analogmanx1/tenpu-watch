# =====================================================================
# local_update.ps1 — 添付文書ウォッチをこのPCから取り込んで公開する
#
# PMDAが添付文書の個別ページを海外・クラウドIPからブロックしているため、
# 取り込みは日本の自宅PCから行う(公開=GitHub Pagesへの反映はpush後にクラウドが自動)。
# タスクスケジューラから毎日 6:30 / 17:30 / PC起動(ログオン)時に呼ばれる。
# 登録は register_task.ps1 を1回実行(PCごとに1回)。
# ログ: %LOCALAPPDATA%\tenpu-watch\run.log
# =====================================================================
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot   # scripts\ の親 = リポジトリ本体

# --- ログ準備(リポジトリ外。Googleドライブ未マウントでも書ける場所) ---
$logDir = Join-Path $env:LOCALAPPDATA "tenpu-watch"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "run.log"
if ((Test-Path $log) -and (Get-Item $log).Length -gt 500KB) {
  Move-Item -Force $log (Join-Path $logDir "run.old.log")   # 簡易ローテーション
}
function Say([string]$msg) {
  Add-Content -Encoding UTF8 $log ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}
function Step([string]$name, [scriptblock]$cmd) {
  Say "== $name =="
  $out = & $cmd 2>&1 | Out-String
  if ($out.Trim()) { Add-Content -Encoding UTF8 $log $out.TrimEnd() }
  return $LASTEXITCODE
}

Say "---- start (PC: $env:COMPUTERNAME) ----"

# --- Googleドライブのマウント待ち(起動直後はまだG:が無いことがある。最大10分) ---
$deadline = (Get-Date).AddMinutes(10)
while (-not (Test-Path (Join-Path $repo ".git"))) {
  if ((Get-Date) -gt $deadline) { Say "!! リポジトリが見つからないため中止: $repo"; exit 1 }
  Start-Sleep -Seconds 20
}
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # pythonのUTF-8出力を正しくログに残す

# --- 取り込み本体 ---
[void](Step "git pull" { git pull --no-rebase origin main })
$rc = Step "python src/run.py (取得+サイト生成)" { python src/run.py }
if ($rc -ne 0) { Say "!! run.py が異常終了 (exit $rc)。今回はコミットせず終了(次回やり直し)"; exit $rc }

# --- 変更があればコミットしてpush(pushが先を越されたらpullして1回だけやり直し) ---
git add -A data archive docs 2>&1 | Out-Null
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
  [void](Step "git commit" { git commit -m "auto: $stamp JST 更新取り込み(PC: $env:COMPUTERNAME)" })
  $rc = Step "git push" { git push origin main }
  if ($rc -ne 0) {
    [void](Step "git pull (push失敗のためやり直し)" { git pull --no-rebase origin main })
    $rc = Step "git push (2回目)" { git push origin main }
    if ($rc -ne 0) { Say "!! push に失敗。次回の実行で回収されます"; exit 1 }
  }
  Say "---- done: 更新をpushしました ----"
} else {
  Say "---- done: 新しい更新なし ----"
}
exit 0
