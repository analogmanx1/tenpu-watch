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

# --- マージ衝突の自動復旧 ---
# pullが衝突すると未解消のまま残り、以後の実行が全部失敗し続ける(2026-08-26の障害)。
# 生成物(docs)と他系統の担当ファイル(if_index.json・yakka_index.json)は向こう側を採用、
# このPCが取り込む日付データ(data/days)とarchive・PC側だけが書くデータ(識別コード・注意チェック・規格の対応表・副作用・薬理作用)はこちら側を維持して、自動で解消する。
# ※日付データは向こう側(Actions)がもうコミットしない設計なので、ours採用で取りこぼしは出ない
function Resolve-StuckMerge {
  git rev-parse -q --verify MERGE_HEAD *> $null
  if ($LASTEXITCODE -ne 0) { return }
  Say "!! マージ衝突を検出 → 自動復旧します(docs/if_index=向こう側、data/days・archive=こちら側)"
  foreach ($p in @("docs", "data/if_index.json", "data/yakka_index.json")) { git checkout --theirs -- $p 2>&1 | Out-Null }
  foreach ($p in @("data/days", "archive", "data/shikibetsu_index.json", "data/chuui_index.json", "data/yj_index.json", "data/fukusayo_index.json", "data/yakuri_index.json")) { git checkout --ours -- $p 2>&1 | Out-Null }
  git add -A data archive docs 2>&1 | Out-Null
  [void](Step "python src/run.py --build-only (衝突復旧のため再生成)" { python src/run.py --build-only })
  git add -A docs 2>&1 | Out-Null
  [void](Step "git commit (衝突の自動解消)" { git commit -m "auto: マージ衝突の自動解消(PC: $env:COMPUTERNAME)" })
  git rev-parse -q --verify MERGE_HEAD *> $null
  if ($LASTEXITCODE -eq 0) { Say "!! 自動解消しきれない衝突が残っています(data/docs以外?)。手で解消してください" }
}

# --- 古い空のロックファイルの片づけ(2026-11-25 対策。つまずき帳#13) ---
# 同期フォルダの上では .git\index.lock などが消えずに残ることがあり、残ると以後のgit操作が全部失敗する
# (2026-10-28〜11-25の約1か月、これで公開が止まっていた)。
# 「0バイト」かつ「$StaleLockMinutes 分以上前」のロックだけを、ゴミ箱へ移す(元に戻せる形)。
# 本当に動いているgitのロックを消さないよう、作られたばかりのロックには触らない
$StaleLockMinutes = 60
function Clear-StaleLock {
  $locks = @(Get-ChildItem -Force -File -Path (Join-Path $repo ".git") -Filter "*.lock" -ErrorAction SilentlyContinue)
  $locks += @(Get-ChildItem -Force -File -Recurse -Path (Join-Path $repo ".git\refs") -Filter "*.lock" -ErrorAction SilentlyContinue)
  foreach ($l in $locks) {
    if ($l.Length -eq 0 -and $l.LastWriteTime -lt (Get-Date).AddMinutes(-$StaleLockMinutes)) {
      try {
        Add-Type -AssemblyName Microsoft.VisualBasic
        [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($l.FullName, 'OnlyErrorDialogs', 'SendToRecycleBin')
        Say ("!! 古い空のロックをゴミ箱へ移しました: {0}(作成 {1:yyyy-MM-dd HH:mm})" -f $l.Name, $l.LastWriteTime)
      } catch {
        Say ("!! 古いロックを片づけられませんでした: {0} ({1})" -f $l.FullName, $_.Exception.Message)
      }
    }
  }
}

# --- 取り込み本体 ---
Clear-StaleLock      # 古い空のロックが残っていたら、まず片づける
Resolve-StuckMerge   # 前回の実行が衝突で止まっていたら、まず片付ける
$pullRc = Step "git pull" { git pull --no-rebase origin main }
Resolve-StuckMerge   # いまのpullが衝突したら、その場で片付ける
if ($pullRc -ne 0) { Say "!! git pull に失敗(exit $pullRc)。このあと取得は続け、push のときにもう一度取り込みを試します" }
$rc = Step "python src/run.py (取得+識別コード差分+サイト生成)" { python src/run.py --shikibetsu }
if ($rc -ne 0) { Say "!! run.py が異常終了 (exit $rc)。今回はコミットせず終了(次回やり直し)"; exit $rc }

# --- 変更があればコミットしてpush(pushが先を越されたらpullして1回だけやり直し) ---
# git add の失敗(ロックなど)を握りつぶすと「差分なし=新しい更新なし」に見えてしまうので、結果を見る(つまずき帳#13)
$addOut = git add -A data archive docs 2>&1 | Out-String
$addRc = $LASTEXITCODE
if ($addRc -ne 0) { Say "!! git add に失敗(exit $addRc)"; if ($addOut.Trim()) { Add-Content -Encoding UTF8 $log $addOut.TrimEnd() } }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
  [void](Step "git commit" { git commit -m "auto: $stamp JST 更新取り込み(PC: $env:COMPUTERNAME)" })
  $rc = Step "git push" { git push origin main }
  if ($rc -ne 0) {
    [void](Step "git pull (push失敗のためやり直し)" { git pull --no-rebase origin main })
    Resolve-StuckMerge   # やり直しのpullが衝突したら、その場で片付けてからpush
    $rc = Step "git push (2回目)" { git push origin main }
    if ($rc -ne 0) { Say "!! push に失敗。次回の実行で回収されます"; exit 1 }
  }
  Say "---- done: 更新をpushしました ----"
} elseif ($pullRc -ne 0 -or $addRc -ne 0) {
  # pull / add に失敗したまま「新しい更新なし」で終わると、止まっていても気づけない(つまずき帳#13)。
  # 失敗だと分かる言葉で終わらせ、タスクスケジューラの「前回の結果」も 0 以外にする
  Say "---- !! 失敗で終了: git の取り込み・登録ができませんでした(上のログを確認。ロック・衝突・ネットワーク) ----"
  exit 2
} else {
  Say "---- done: 新しい更新なし ----"
}
exit 0
