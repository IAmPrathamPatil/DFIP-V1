param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start-check", "stop")]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot
)

$ErrorActionPreference = "Continue"

function Get-ListenPids {
    param([int]$Port)
    $ids = New-Object System.Collections.Generic.List[int]
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess -and -not $ids.Contains([int]$c.OwningProcess)) {
                $ids.Add([int]$c.OwningProcess)
            }
        }
    }
    catch {
        # Fallback when Get-NetTCPConnection is unavailable.
        $lines = netstat -ano | Select-String -Pattern ":$Port\s"
        foreach ($line in $lines) {
            $text = [string]$line
            if ($text -notmatch "LISTENING") {
                continue
            }
            $parts = $text.Trim() -split "\s+"
            $procId = 0
            if ([int]::TryParse($parts[-1], [ref]$procId) -and $procId -gt 0 -and -not $ids.Contains($procId)) {
                $ids.Add($procId)
            }
        }
    }
    return $ids
}

function Get-CommandLine {
    param([int]$ProcessId)
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
        if ($null -eq $p) {
            return ""
        }
        return [string]$p.CommandLine
    }
    catch {
        return ""
    }
}

function Test-DfipCommand {
    param([string]$CommandLine, [string]$Marker)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    return ($CommandLine -match [regex]::Escape($Marker))
}

function Test-HttpOk {
    param([string]$Uri)
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    }
    catch {
        return $false
    }
}

if ($Action -eq "stop") {
    $stopped = 0
    $skipped = 0
    foreach ($spec in @(
            @{ Port = 8000; Marker = "dfip_api" },
            @{ Port = 3000; Marker = "dfip_web" }
        )) {
        $pids = Get-ListenPids -Port $spec.Port
        if ($pids.Count -eq 0) {
            Write-Host "Port $($spec.Port): nothing listening."
            continue
        }
        foreach ($procId in $pids) {
            $cmd = Get-CommandLine -ProcessId $procId
            if (Test-DfipCommand -CommandLine $cmd -Marker $spec.Marker) {
                Write-Host "Stopping DFIP process on port $($spec.Port) (PID $procId)."
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $stopped += 1
            }
            else {
                Write-Host "Port $($spec.Port) PID $procId is not a DFIP $($spec.Marker) process; not killed."
                $skipped += 1
            }
        }
    }
    Write-Host "Stopped: $stopped  Left untouched: $skipped"
    exit 0
}

# start-check: conflict if a port is in use by a non-DFIP process, or DFIP is up but unhealthy.
$apiOk = Test-HttpOk -Uri "http://127.0.0.1:8000/health"
$webOk = Test-HttpOk -Uri "http://127.0.0.1:3000/"

$apiPids = Get-ListenPids -Port 8000
if ($apiPids.Count -gt 0 -and -not $apiOk) {
    $anyDfip = $false
    foreach ($procId in $apiPids) {
        if (Test-DfipCommand -CommandLine (Get-CommandLine -ProcessId $procId) -Marker "dfip_api") {
            $anyDfip = $true
        }
    }
    Write-Host "PORT CONFLICT: 8000 is already in use."
    Write-Host "http://127.0.0.1:8000/health is not succeeding."
    if ($anyDfip) {
        Write-Host "A process matching dfip_api is listening but health failed."
        Write-Host "Use STOP_DFIP_DEMO.bat, then START_DFIP_DEMO.bat."
    }
    else {
        Write-Host "The listener is not python -m dfip_api. Stop that application or use another host/port in .env."
    }
    exit 1
}

$webPids = Get-ListenPids -Port 3000
if ($webPids.Count -gt 0 -and -not $webOk) {
    Write-Host "PORT CONFLICT: 3000 is already in use."
    Write-Host "http://127.0.0.1:3000 is not succeeding."
    Write-Host "Stop the other listener, or use STOP_DFIP_DEMO.bat if it is DFIP-WEB."
    exit 1
}

if ($apiOk) {
    Write-Host "Port 8000: existing DFIP API health OK (will not start a second API)."
}
else {
    Write-Host "Port 8000: available for python -m dfip_api"
}
if ($webOk) {
    Write-Host "Port 3000: existing DFIP website OK (will not start a second website)."
}
else {
    Write-Host "Port 3000: available for python -m dfip_web"
}
exit 0
