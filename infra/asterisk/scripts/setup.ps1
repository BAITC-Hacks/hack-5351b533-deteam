$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $project '.env'
$trunkPath = Join-Path $project 'config/pjsip_trunk.conf'

function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath $envPath)) {
    $content = @"
AMI_SECRET=$(New-Secret)
ARI_SECRET=$(New-Secret)
SIP_TEST_SECRET=$(New-Secret)
SIP_OPERATOR_SECRET=$(New-Secret)
ASTERISK_PUBLIC_IP=
ASTERISK_LOCAL_NET=auto
LOCAL_SIP_PEER_IP=
"@
    [System.IO.File]::WriteAllText($envPath, $content + "`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host 'Created .env with random passwords.'
} else {
    $content = [System.IO.File]::ReadAllText($envPath)
    if ($content -notmatch '(?m)^SIP_OPERATOR_SECRET=') {
        $content = $content.TrimEnd("`r", "`n") + "`nSIP_OPERATOR_SECRET=$(New-Secret)`n"
        Write-Host 'Added SIP_OPERATOR_SECRET to existing .env.'
    }
    if ($content -notmatch '(?m)^LOCAL_SIP_PEER_IP=') {
        $content = $content.TrimEnd("`r", "`n") + "`nLOCAL_SIP_PEER_IP=`n"
    }
    if ($content -match '(?m)^ASTERISK_LOCAL_NET=172\.16\.0\.0/12\r?$') {
        $content = [regex]::Replace($content, '(?m)^ASTERISK_LOCAL_NET=172\.16\.0\.0/12\r?$', 'ASTERISK_LOCAL_NET=auto')
        Write-Host 'Updated old Docker NAT default to ASTERISK_LOCAL_NET=auto.'
    }
    [System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
    Write-Host '.env ready; existing passwords preserved.'
}

if (-not (Test-Path -LiteralPath $trunkPath)) {
    Copy-Item -LiteralPath (Join-Path $project 'config/pjsip_trunk.conf.example') -Destination $trunkPath
    Write-Host 'Created config/pjsip_trunk.conf. Configure it when provider details are available.'
}
