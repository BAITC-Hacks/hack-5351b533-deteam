$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $project '.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Run scripts/setup.ps1 first.' }
$line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^AMI_SECRET=' } | Select-Object -First 1
if (-not $line) { throw 'AMI_SECRET missing from .env.' }
$secret = $line.Substring('AMI_SECRET='.Length)

$client = [System.Net.Sockets.TcpClient]::new()
try {
    $client.Connect('127.0.0.1', 5038)
    $stream = $client.GetStream()
    $stream.ReadTimeout = 3000
    $reader = [System.IO.StreamReader]::new($stream)
    $writer = [System.IO.StreamWriter]::new($stream, [System.Text.Encoding]::ASCII)
    $writer.NewLine = "`r`n"
    $writer.AutoFlush = $true
    $null = $reader.ReadLine() # AMI banner
    $writer.Write("Action: Login`r`nUsername: voice-ai`r`nSecret: $secret`r`nEvents: off`r`n`r`n")
    $response = @()
    while ($true) {
        $next = $reader.ReadLine()
        if ($null -eq $next -or $next -eq '') { break }
        $response += $next
    }
    if ($response -notcontains 'Response: Success') {
        throw ('AMI login failed: ' + ($response -join '; '))
    }
    $writer.Write("Action: Ping`r`n`r`n")
    $ping = @()
    while ($true) {
        $next = $reader.ReadLine()
        if ($null -eq $next -or $next -eq '') { break }
        $ping += $next
    }
    if ($ping -notcontains 'Response: Success') {
        throw ('AMI ping failed: ' + ($ping -join '; '))
    }
    Write-Host 'AMI login and Ping: OK'
    $writer.Write("Action: Logoff`r`n`r`n")
} finally {
    $client.Dispose()
}
