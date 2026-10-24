$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$backend = Join-Path $PSScriptRoot 'backend'
$frontend = Join-Path $PSScriptRoot 'frontend'
Start-Process -FilePath (Join-Path $backend '.venv\Scripts\python.exe') -ArgumentList 'run.py' -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $backend 'server.stdout.log') -RedirectStandardError (Join-Path $backend 'server.stderr.log')
Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3300' -WorkingDirectory $frontend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $frontend 'server.stdout.log') -RedirectStandardError (Join-Path $frontend 'server.stderr.log')
Write-Output 'WeAidStock: http://localhost:3300/'
