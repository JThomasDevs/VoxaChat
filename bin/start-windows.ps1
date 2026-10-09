$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:VOXA_WINDOWS = "1"
python bin/server.py --port 8766 --windows @args
