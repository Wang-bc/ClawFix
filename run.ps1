$python = Get-Command python -ErrorAction SilentlyContinue
if ($python -and $python.Source -notmatch "WindowsApps") {
  & python -m app.main
  exit $LASTEXITCODE
}

$python3 = Get-Command python3 -ErrorAction SilentlyContinue
if ($python3 -and $python3.Source -notmatch "WindowsApps") {
  & python3 -m app.main
  exit $LASTEXITCODE
}

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wsl) {
  $winPath = (Get-Location).Path
  $drive = $winPath.Substring(0, 1).ToLower()
  $rest = $winPath.Substring(2).Replace("\", "/")
  $wslPath = "/mnt/$drive$rest"
  & wsl.exe bash -lc "cd '$wslPath' && python3 -m app.main"
  exit $LASTEXITCODE
}

Write-Error "未找到可用的 Python 解释器。"
exit 1

