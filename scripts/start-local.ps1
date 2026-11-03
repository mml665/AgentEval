param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot
try {
    $portProbe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try { $portProbe.Start() } catch { throw "端口 $Port 已被占用。请使用 -Port 指定其他端口。" } finally { $portProbe.Stop() }
    $pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw '请先按照 README 安装 Python 依赖到 .venv。'
    }
    if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
        throw '请先在 frontend 目录运行 npm ci 和 npm run build。'
    }
    if (-not $env:AGENTEVAL_API_TOKEN) {
        $envPath = Join-Path $repoRoot '.env'
        if (Test-Path -LiteralPath $envPath) {
            $tokenLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^AGENTEVAL_API_TOKEN=' } | Select-Object -First 1
            if ($tokenLine) { $env:AGENTEVAL_API_TOKEN = $tokenLine.Substring('AGENTEVAL_API_TOKEN='.Length).Trim() }
        }
        if (-not $env:AGENTEVAL_API_TOKEN -or $env:AGENTEVAL_API_TOKEN.StartsWith('replace-')) {
            $bytes = New-Object byte[] 32
            $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
            $env:AGENTEVAL_API_TOKEN = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
            $existing = if (Test-Path -LiteralPath $envPath) { @(Get-Content -LiteralPath $envPath | Where-Object { $_ -notmatch '^AGENTEVAL_API_TOKEN=' }) } else { @() }
            @($existing; "AGENTEVAL_API_TOKEN=$($env:AGENTEVAL_API_TOKEN)") | Set-Content -LiteralPath $envPath -Encoding utf8
        }
        Write-Host '访问令牌保存在本机 .env 文件中，不会进入 Git。'
    }
    $env:PYTHONPATH = Join-Path $repoRoot 'backend'
    & $pythonPath -m agenteval init-db
    if ($LASTEXITCODE -ne 0) { throw '数据库迁移失败。' }
    Write-Host "打开 http://127.0.0.1:$Port ，使用 AGENTEVAL_API_TOKEN 登录。"
    & $pythonPath -m agenteval serve --port $Port
    if ($LASTEXITCODE -ne 0) { throw '服务启动失败，请检查端口与配置。' }
} finally {
    Pop-Location
}
