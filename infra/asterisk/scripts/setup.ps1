$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $project '.env'
$trunkPath = Join-Path $project 'config/pjsip_trunk.conf'

if (-not (Test-Path -LiteralPath $envPath)) {
    function New-Secret {
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    }
    $content = @"
AMI_SECRET=$(New-Secret)
ARI_SECRET=$(New-Secret)
SIP_TEST_SECRET=$(New-Secret)
ASTERISK_PUBLIC_IP=
ASTERISK_LOCAL_NET=172.16.0.0/12
LOCAL_SIP_PEER_IP=
"@
    [System.IO.File]::WriteAllText($envPath, $content + "`n")
    Write-Host 'Created .env with random passwords.'
} else {
    Write-Host '.env already exists; keeping existing passwords.'
    if (-not (Select-String -LiteralPath $envPath -Pattern '^LOCAL_SIP_PEER_IP=' -Quiet)) {
        Add-Content -LiteralPath $envPath -Value "LOCAL_SIP_PEER_IP="
    }
}

if (-not (Test-Path -LiteralPath $trunkPath)) {
    Copy-Item -LiteralPath (Join-Path $project 'config/pjsip_trunk.conf.example') -Destination $trunkPath
    Write-Host 'Created config/pjsip_trunk.conf. Configure it when provider details are available.'
}
