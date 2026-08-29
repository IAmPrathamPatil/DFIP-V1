param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

function Get-DotEnvMap {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $map
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) {
            return
        }
        $i = $line.IndexOf("=")
        if ($i -lt 1) {
            return
        }
        $name = $line.Substring(0, $i).Trim()
        $val = $line.Substring($i + 1).Trim()
        if ($val.Length -ge 2 -and $val.StartsWith('"') -and $val.EndsWith('"')) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        $map[$name] = $val
    }
    return $map
}

function Get-Effective {
    param(
        [hashtable]$FileMap,
        [string]$Name
    )
    $fromEnv = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($fromEnv)) {
        return $fromEnv
    }
    if ($FileMap.ContainsKey($Name)) {
        return [string]$FileMap[$Name]
    }
    return ""
}

$fileMap = Get-DotEnvMap -Path (Join-Path $RepoRoot ".env")
$mode = Get-Effective -FileMap $fileMap -Name "DFIP_AUTH_MODE"
if ([string]::IsNullOrWhiteSpace($mode)) {
    $mode = "dev_token"
}
$mode = $mode.Trim().ToLowerInvariant()

$dfipEnv = Get-Effective -FileMap $fileMap -Name "DFIP_ENV"
if ([string]::IsNullOrWhiteSpace($dfipEnv)) {
    $dfipEnv = "development"
}

Write-Host "Auth mode name: $mode"

if ($dfipEnv.Trim().ToLowerInvariant() -eq "production") {
    Write-Host "This launcher refuses DFIP_ENV=production."
    Write-Host "Missing or unsafe local demo setting: DFIP_ENV"
    exit 1
}

if ($mode -eq "jwt") {
    $secret = Get-Effective -FileMap $fileMap -Name "DFIP_AUTH_SECRET"
    if ([string]::IsNullOrWhiteSpace($secret)) {
        Write-Host "Missing required variable: DFIP_AUTH_SECRET"
        exit 1
    }
}
elseif ($mode -eq "dev_token") {
    $token = Get-Effective -FileMap $fileMap -Name "DFIP_DEV_AUTH_TOKEN"
    if ([string]::IsNullOrWhiteSpace($token)) {
        Write-Host "Missing required variable: DFIP_DEV_AUTH_TOKEN"
        exit 1
    }
    $databaseUrl = Get-Effective -FileMap $fileMap -Name "DATABASE_URL"
    if (-not [string]::IsNullOrWhiteSpace($databaseUrl)) {
        $clientId = Get-Effective -FileMap $fileMap -Name "DFIP_DEV_AUTH_CLIENT_ID"
        if ([string]::IsNullOrWhiteSpace($clientId)) {
            Write-Host "Missing required variable: DFIP_DEV_AUTH_CLIENT_ID"
            exit 1
        }
    }
}
else {
    Write-Host "Unsupported DFIP_AUTH_MODE (allowed: dev_token, jwt)."
    Write-Host "Missing or invalid variable: DFIP_AUTH_MODE"
    exit 1
}

exit 0
