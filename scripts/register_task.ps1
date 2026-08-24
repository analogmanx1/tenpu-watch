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
# =====================================================================
$script = Join-Path $PSScriptRoot "local_update.ps1"
if (-not (Test-Path $script)) { Write-Host "local_update.ps1 が見つかりません: $script"; exit 1 }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"")

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

Write-Host "登録しました: tenpu-watch-auto (毎日6:30・17:30・ログオン3分後)"
Write-Host "ログ: $env:LOCALAPPDATA\tenpu-watch\run.log"
