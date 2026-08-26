# =====================================================================
# register_task.ps1 — このPCに「添付文書ウォッチ自動取り込み」タスクを登録する
#
# 使い方: このファイルを右クリック →「PowerShellで実行」(またはPowerShellで1回実行)
# 登録されるタスク名: tenpu-watch-auto
#   ・毎日 06:30 と 17:30 (PCがついていてログオン中のとき)
#   ・PC起動(ログオン)から3分後 ← 定時を逃してもここで追いつく
#   ・多重起動はしない / 電源が無くても(ノートPCバッテリー時も)動く
# 別のPCでも使いたいときは、そのPCでこのスクリプトを1回実行すればOK。
# 解除したいとき: Unregister-ScheduledTask -TaskName "tenpu-watch-auto"
#
# タスクの入口はローカルディスクに生成する「ランチャー」(LOCALAPPDATA の tenpu-watch フォルダの launch.ps1)。
# 本体(local_update.ps1)はGoogleドライブ(G:)上にあり、PC起動直後はマウント前で
# ファイル自体が読めずタスクが即死する(結果 0xFFFD0000・ログも残らない)ため、
# 入口だけC:に置いて「マウントを待ってから本体を呼ぶ」形にしている(2026-08-26変更)。
# =====================================================================
$script = Join-Path $PSScriptRoot "local_update.ps1"
if (-not (Test-Path $script)) { Write-Host "local_update.ps1 が見つかりません: $script"; exit 1 }

# --- ランチャーをローカルディスクに生成 ---
$localDir = Join-Path $env:LOCALAPPDATA "tenpu-watch"
New-Item -ItemType Directory -Force $localDir | Out-Null
$launcher = Join-Path $localDir "launch.ps1"
$logPath = Join-Path $localDir "run.log"
$body = @"
# 自動生成: register_task.ps1 (tenpu-watch)。手で編集しない(再登録すると上書きされる)
# 役割: Googleドライブのマウントを待ってから本体(local_update.ps1)を呼ぶ入口
`$log = "$logPath"
function Say([string]`$m) { Add-Content -Encoding UTF8 `$log ("[{0}] launcher: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), `$m) }
`$main = "$script"
`$deadline = (Get-Date).AddMinutes(15)
while (-not (Test-Path `$main)) {
  if ((Get-Date) -gt `$deadline) { Say "!! ドライブ未マウントのため中止: `$main"; exit 1 }
  Start-Sleep -Seconds 20
}
& `$main
exit `$LASTEXITCODE
"@
Set-Content -Path $launcher -Value $body -Encoding UTF8
Write-Host "ランチャーを生成しました: $launcher"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`"")

$t1 = New-ScheduledTaskTrigger -Daily -At 06:30
$t2 = New-ScheduledTaskTrigger -Daily -At 17:30
$t3 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t3.Delay = "PT3M"   # ログオン3分後(Googleドライブとネットの準備待ち)

$settings = New-ScheduledTaskSettingsSet `
  -MultipleInstances IgnoreNew `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "tenpu-watch-auto" `
  -Action $action -Trigger $t1, $t2, $t3 -Settings $settings -Force | Out-Null

Write-Host "登録しました: tenpu-watch-auto (毎日6:30・17:30・ログオン3分後 → ランチャー経由)"
Write-Host "ログ: $logPath"
