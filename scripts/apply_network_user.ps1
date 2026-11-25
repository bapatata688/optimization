<#
.SYNOPSIS
    Applies non-elevated Milestone M3 user-space network bloat & P2P cleanup (Features F6/F13 user-space).
.DESCRIPTION
    Idempotent non-elevated script for laptop_optimization:
      - Disables 'Free Download Manager' and ensures 'utweb' remains disabled in
        HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run
        using standard 12-byte REG_BINARY (0x03,0x00,0x00,0x00 + UTC ToFileTimeUtc()).
      - Protects FxSound, XMouseButtonControl, SecurityHealth, RtkAudUService, and Zen Browser;
        never deletes registry keys or values.
      - Cleanly stops active P2P / background download hog processes
        (fdm, utweb, qbittorrent, utorrent) via Stop-Process -Force with post-stop verification.
      - Logs read-only advisory status of Wi-Fi DNS and DoSvc without attempting HKLM mutations.
    Supports -DryRun and -WhatIf, and logs every check/change/skip with UTC timestamps
    to reports/apply_network.log (UTF-8 without BOM).
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$DryRun,
    [string]$ReportDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'reports')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$script:isWhatIf = [bool]$WhatIfPreference
$isDryRun = [bool]($DryRun -or $WhatIfPreference)
$ReportDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportDir)
$logPath = Join-Path $ReportDir 'apply_network.log'
if (-not $script:isWhatIf) {
    [void][System.IO.Directory]::CreateDirectory($ReportDir)
    if (-not (Test-Path -LiteralPath $logPath)) {
        [System.IO.File]::WriteAllText($logPath, '', $utf8NoBom)
    }
}

$script:errorCount = 0

function Write-ApplyLog {
    param(
        [Parameter(Mandatory)][ValidateSet('INFO', 'DRY-RUN', 'APPLIED', 'SKIP', 'WARN', 'ERROR')][string]$Level,
        [Parameter(Mandatory)][string]$Message
    )
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $line = "[$ts] [$Level] [user] $Message"
    Write-Host $line
    if (-not $script:isWhatIf) {
        [System.IO.File]::AppendAllText($logPath, $line + "`r`n", $utf8NoBom)
    }
    if ($Level -eq 'ERROR') {
        $script:errorCount++
    }
}

function Convert-HKCUPathToSubKey {
    param([Parameter(Mandatory)][string]$PSPath)
    if ($PSPath -match '^HKCU:\\(.+)$') {
        return $Matches[1]
    }
    throw "Expected HKCU:\ path, got '$PSPath'"
}

function Get-HKCUValueInfo {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name
    )
    $subKeyPath = Convert-HKCUPathToSubKey -PSPath $Path
    $subKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($subKeyPath, $false)
    if ($null -eq $subKey) {
        return [PSCustomObject]@{ KeyExists = $false; ValueExists = $false; Value = $null; Kind = $null }
    }
    try {
        $val = $subKey.GetValue($Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        if ($null -eq $val) {
            return [PSCustomObject]@{ KeyExists = $true; ValueExists = $false; Value = $null; Kind = $null }
        }
        $kind = $subKey.GetValueKind($Name)
        return [PSCustomObject]@{ KeyExists = $true; ValueExists = $true; Value = $val; Kind = $kind }
    }
    finally {
        $subKey.Close()
    }
}

function Disable-StartupApprovedRunValue {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$ProtectedNames
    )

    if ($ProtectedNames -contains $Name) {
        throw "Safety guard violation: Attempted to modify protected startup item '$Name'."
    }

    $saPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
    $info = Get-HKCUValueInfo -Path $saPath -Name $Name

    if ($info.ValueExists -and $info.Kind -eq [Microsoft.Win32.RegistryValueKind]::Binary -and $info.Value -is [byte[]] -and ([byte[]]$info.Value).Length -ge 12) {
        $bytes = [byte[]]$info.Value
        if (($bytes[0] % 2) -eq 1) {
            $hex = ([BitConverter]::ToString($bytes)).Replace('-', '')
            Write-ApplyLog -Level 'SKIP' -Message "StartupApproved\Run\'$Name' already disabled (byte0=0x$($bytes[0].ToString('X2')), len=$($bytes.Length), data=$hex)."
            return
        }
    }

    $currentHex = if ($info.ValueExists -and $info.Value -is [byte[]]) {
        ([BitConverter]::ToString([byte[]]$info.Value)).Replace('-', '')
    }
    elseif ($info.ValueExists) {
        [string]$info.Value
    }
    else {
        '<missing>'
    }

    if ($isDryRun) {
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would write 12-byte disabled REG_BINARY (0x03...) to StartupApproved\Run\'$Name' (current: $currentHex)."
        return
    }

    if ($PSCmdlet.ShouldProcess("$saPath\$Name", 'Disable StartupApproved\Run entry')) {
        $fileTimeBytes = [BitConverter]::GetBytes([DateTime]::UtcNow.ToFileTimeUtc())
        $newBytes = [byte[]](@([byte]0x03, [byte]0x00, [byte]0x00, [byte]0x00) + $fileTimeBytes)
        $subKeyPath = Convert-HKCUPathToSubKey -PSPath $saPath
        $subKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($subKeyPath, $true)
        try {
            $subKey.SetValue($Name, $newBytes, [Microsoft.Win32.RegistryValueKind]::Binary)
        }
        finally {
            $subKey.Close()
        }
        $newHex = ([BitConverter]::ToString($newBytes)).Replace('-', '')
        Write-ApplyLog -Level 'APPLIED' -Message "Disabled StartupApproved\Run\'$Name' (previous: $currentHex, new: $newHex)."
    }
}

Write-ApplyLog -Level 'INFO' -Message "Starting apply_network_user.ps1 (DryRun=$isDryRun, ReportDir='$ReportDir')"

try {
    # =========================================================================
    # 1. HKCU StartupApproved\Run (P2P / Download Bloat)
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F6/F13 User-Space: StartupApproved\Run P2P Cleanup ---'

    $protectedStartupNames = @(
        'FxSound',
        'XMouseButtonControl',
        'SecurityHealth',
        'RtkAudUService',
        'Zen Browser'
    )

    $startupTargets = @(
        'Free Download Manager',
        'utweb'
    )

    foreach ($target in $startupTargets) {
        Disable-StartupApprovedRunValue -Name $target -ProtectedNames $protectedStartupNames
    }
    Write-ApplyLog -Level 'INFO' -Message "Protected startup items untouched: $($protectedStartupNames -join ', '). No registry keys or values deleted."

    # =========================================================================
    # 2. Stop Active P2P / Download Hog Processes
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F13 User-Space: Stop Active P2P / Download Hog Processes ---'

    $protectedProcessPatterns = @(
        'FxSound*',
        'XMouseButtonControl*',
        'SecurityHealth*',
        'RtkAudUService*',
        'zen*'
    )

    $p2pTargets = @('fdm', 'utweb', 'qbittorrent', 'utorrent')

    foreach ($procName in $p2pTargets) {
        foreach ($pat in $protectedProcessPatterns) {
            if ($procName -like $pat) {
                throw "Safety guard violation: Process '$procName' matches protected pattern '$pat'."
            }
        }

        $procs = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
        if ($procs.Count -eq 0) {
            Write-ApplyLog -Level 'SKIP' -Message "Process '$procName' is not running."
            continue
        }

        $pids = ($procs | ForEach-Object { $_.Id }) -join ', '
        if ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would stop P2P/download process '$procName' (count=$($procs.Count), PIDs: $pids) and verify exit."
            continue
        }

        foreach ($p in $procs) {
            if ($PSCmdlet.ShouldProcess("$procName (PID $($p.Id))", 'Stop P2P / download hog process')) {
                try {
                    Stop-Process -Id $p.Id -Force -ErrorAction Stop
                    Start-Sleep -Milliseconds 250
                    $stillAlive = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
                    if ($null -ne $stillAlive) {
                        Write-ApplyLog -Level 'ERROR' -Message "Process '$procName' (PID $($p.Id)) remained running after Stop-Process -Force."
                    }
                    else {
                        Write-ApplyLog -Level 'APPLIED' -Message "Stopped and verified termination of '$procName' (PID $($p.Id))."
                    }
                }
                catch {
                    # Re-check whether the process exited concurrently before failing
                    $afterErr = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
                    if ($null -eq $afterErr) {
                        Write-ApplyLog -Level 'APPLIED' -Message "Process '$procName' (PID $($p.Id)) already exited."
                    }
                    else {
                        Write-ApplyLog -Level 'ERROR' -Message "Could not stop '$procName' (PID $($p.Id)): $($_.Exception.Message)"
                    }
                }
            }
        }
    }

    # =========================================================================
    # 3. Read-Only Advisory Status (Wi-Fi DNS & DoSvc)
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F13 User-Space: Read-Only Advisory Status (Wi-Fi DNS & DoSvc) ---'

    $wifiDns = Get-DnsClientServerAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
    [string[]]$dnsAddresses = @(if ($null -ne $wifiDns -and $null -ne $wifiDns.ServerAddresses) { $wifiDns.ServerAddresses })
    $dnsText = if ($dnsAddresses.Count -gt 0) {
        $dnsAddresses -join ', '
    }
    else {
        '<none/DHCP>'
    }
    Write-ApplyLog -Level 'INFO' -Message "Advisory (read-only): Wi-Fi IPv4 DNS servers = [$dnsText] (preserved; admin script handles optional override & benchmark)."

    $doSvc = Get-Service -Name 'DoSvc' -ErrorAction SilentlyContinue
    $doSvcStatus = if ($null -ne $doSvc) { "$($doSvc.Status)/$($doSvc.StartType)" } else { 'Absent' }
    $doPolicyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization'
    $doModeText = '<policy key absent>'
    if (Test-Path -LiteralPath $doPolicyPath) {
        $doProps = Get-ItemProperty -LiteralPath $doPolicyPath -ErrorAction SilentlyContinue
        if ($null -ne $doProps -and $null -ne $doProps.PSObject.Properties['DODownloadMode']) {
            $doModeText = "DODownloadMode=$($doProps.DODownloadMode)"
        }
        else {
            $doModeText = '<DODownloadMode absent>'
        }
    }
    Write-ApplyLog -Level 'INFO' -Message "Advisory (read-only): DoSvc=$doSvcStatus, Policy=$doModeText (HKLM mutations delegated to apply_network_admin.ps1)."
}
catch {
    Write-ApplyLog -Level 'ERROR' -Message "Unhandled error in apply_network_user.ps1: $($_.Exception.Message)"
    exit 1
}

if ($script:errorCount -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Completed apply_network_user.ps1 with $($script:errorCount) error(s)."
    exit 1
}

Write-ApplyLog -Level 'INFO' -Message "Completed apply_network_user.ps1 cleanly (DryRun=$isDryRun)."
exit 0
