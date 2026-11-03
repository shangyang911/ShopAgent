# Start the API and the local AMap proxy together. Reuse an existing proxy.
param([int]$Port = 8001)

$ErrorActionPreference = 'Stop'
$backendPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $backendPython)) {
    throw 'Create backend/.venv and install requirements.txt first.'
}

$ownedProxy = $null
try {
    $proxyListener = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    if (-not $proxyListener) {
        $ownedProxy = Start-Process -FilePath $backendPython -ArgumentList @('dev_amap_proxy.py') `
            -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $PSScriptRoot '.venv\amap-proxy.stdout.log') `
            -RedirectStandardError (Join-Path $PSScriptRoot '.venv\amap-proxy.stderr.log')
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if ($ownedProxy.HasExited) {
                throw 'AMap proxy failed to start. See backend/.venv/amap-proxy.stderr.log.'
            }
            if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { break }
            Start-Sleep -Milliseconds 250
        }
        if (-not (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)) {
            throw 'AMap proxy did not become ready on port 8080.'
        }
    }
    & $backendPython -m uvicorn app.main:app --app-dir $PSScriptRoot --reload --reload-dir $PSScriptRoot --port $Port
}
finally {
    if ($ownedProxy -and -not $ownedProxy.HasExited) {
        Stop-Process -Id $ownedProxy.Id -ErrorAction SilentlyContinue
    }
}
