$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$b = Join-Path $root 'backend'
$log = Join-Path $root 'restart.log'
"=== $(Get-Date -Format s) restart ===" | Out-File $log -Encoding utf8
try {
  $conns = Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue
  "listen conns: $($conns | ForEach-Object { $_.OwningProcess })" | Out-File $log -Append -Encoding utf8
  $pids = New-Object 'System.Collections.Generic.List[int]'
  foreach ($c in @($conns)) { if ($c) { $pids.Add([int]$c.OwningProcess) } }
  # run.py 로 실행 중인 python 프로세스도 함께 종료 (리로더 부모 포함)
  $py = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*run.py*' }
  "run.py procs: $($py | ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" })" | Out-File $log -Append -Encoding utf8
  foreach ($q in @($py)) { if ($q) { $pids.Add([int]$q.ProcessId) } }
  # 이 스크립트를 띄운 프로세스(설정 화면의 재시작 버튼)도 run.py 이므로 함께 종료되지만, 스크립트 자신은 분리 실행되어 계속 진행됩니다
  foreach ($p in (@($pids) | Sort-Object -Unique)) {
    try { Stop-Process -Id $p -Force -ErrorAction Stop; "killed $p" | Out-File $log -Append -Encoding utf8 }
    catch {
      "kill fail $p : $_ -> taskkill 재시도" | Out-File $log -Append -Encoding utf8
      $tk = & taskkill.exe /F /PID $p 2>&1
      "taskkill $p : $tk" | Out-File $log -Append -Encoding utf8
    }
  }
  Start-Sleep 2
  $pyexe = Join-Path $b '.venv\Scripts\python.exe'
  $proc = Start-Process -FilePath $pyexe -ArgumentList 'run.py' -WorkingDirectory $b -WindowStyle Hidden -RedirectStandardOutput (Join-Path $b 'server.stdout.log') -RedirectStandardError (Join-Path $b 'server.stderr.log') -PassThru
  "started pid $($proc.Id)" | Out-File $log -Append -Encoding utf8

  # 프론트엔드(Vite, 기본 3300 — vite.config.js)가 꺼져 있으면 함께 기동
  $webPort = 3300
  if ($env:WEAID_WEB_PORT) { $webPort = [int]$env:WEAID_WEB_PORT }
  $fe = Get-NetTCPConnection -LocalPort $webPort -State Listen -ErrorAction SilentlyContinue
  if (-not $fe) {
    $f = Join-Path $root 'frontend'
    $node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
    if ($node) {
      $v = Start-Process -FilePath $node -ArgumentList "node_modules/vite/bin/vite.js --host 127.0.0.1 --port $webPort" -WorkingDirectory $f -WindowStyle Hidden -RedirectStandardOutput (Join-Path $f 'server.stdout.log') -RedirectStandardError (Join-Path $f 'server.stderr.log') -PassThru
      "frontend started pid $($v.Id) → http://localhost:$webPort" | Out-File $log -Append -Encoding utf8
    } else { "node.exe not found" | Out-File $log -Append -Encoding utf8 }
  } else { "frontend already listening on $webPort" | Out-File $log -Append -Encoding utf8 }
} catch { "ERROR: $_" | Out-File $log -Append -Encoding utf8 }
