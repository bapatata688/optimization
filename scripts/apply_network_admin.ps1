<#
.SYNOPSIS
    Applies elevated Milestone M3 network & ping optimizations (Features F11, F12, F13, F14).
.DESCRIPTION
    Idempotent elevated optimization script for laptop_optimization:
      - Elevation Gate: Exits cleanly with code 2 when non-elevated unless -DryRun / -WhatIf is passed.
        Never triggers UAC prompts.
      - Pre-Mutation M1 Backup Gate: Runs backup_baseline.ps1 -IncludeElevated (never -Force)
        if backups/power/power_scheme_*.pow files are absent.
      - F11: Preserves 'Gaming Estable (Sin Bateria)' (46476e86-f770-4378-857e-3a25bf0336b4) active
             (never switches to Ultimate Performance, never raises AC PROCTHROTTLEMAX > 99% or PERFBOOSTMODE > 0);
             enforces AC=0 & DC=0 for Wireless Power Saving and PCIe ASPM; verifies Wi-Fi LpsEn='0';
             sets PnPCapabilities=24 and MSPower_DeviceEnable=False (+ Set-NetAdapterPowerManagement Disabled)
             on both Wi-Fi and Ethernet.
      - F12: Sets Wi-Fi RegROAMSensitiveLevel='127' (Deshabilitar), verifies PreferBand='2',
             sets CLARO1_F3E3B9 connectionmode=manual, verifies CLARO2_F3E3B9 stays auto, and verifies
             gaming_wlan_scan_toggle.ps1 steady-state autoconfig remains enabled.
      - F13: Configures DeliveryOptimization policy (DODownloadMode=0, DOPercentageMaxBackgroundBandwidth=25),
             ensures NetworkDaemon stays Disabled/Stopped, preserves Wi-Fi DNS (1.1.1.1 / 1.0.0.1) by default
             unless -DnsServers is explicitly passed, and logs an advisory 1.1.1.1 vs 9.9.9.9 vs 8.8.8.8
             ICMP/DNS benchmark unless -SkipDnsBenchmark.
      - F14: Sets Ethernet standby/interrupt/flow-control properties to '0', preserves checksum offloads ('3'),
             Dhcp/NlaSvc services, TCP global settings (rsc=disabled, rss=enabled, autotuninglevel=normal,
             ecncapability=disabled, timestamps=disabled, nonsackrttresiliency=disabled),
             TcpAckFrequency=1 & TCPNoDelay=1 on both Wi-Fi & Ethernet, MMCSS NetworkThrottlingIndex=0xFFFFFFFF
             & SystemResponsiveness=0, and Fortnite QoS policies.
    Supports -DryRun / -WhatIf, and appends UTC-timestamped logs to reports/apply_network.log.
#>
[CmdletBinding(SupportsShouldProcess = $true, PositionalBinding = $false)]
param(
    [string]$BackupDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups'),
    [string]$ReportDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'reports'),
    [string[]]$DnsServers = @(),
    [switch]$DryRun,
    [switch]$SkipDnsBenchmark
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
    $line = "[$ts] [$Level] [admin] $Message"
    Write-Host $line
    if (-not $script:isWhatIf) {
        [System.IO.File]::AppendAllText($logPath, $line + "`r`n", $utf8NoBom)
    }
    if ($Level -eq 'ERROR') {
        $script:errorCount++
    }
}

function Test-IsAdmin {
    $principal = [Security.Principal.WindowsPrincipal]([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Convert-HKLMPathToSubKey {
    param([Parameter(Mandatory)][string]$PSPath)
    if ($PSPath -match '^HKLM:\\(.+)$') {
        return $Matches[1]
    }
    throw "Expected HKLM:\ path, got '$PSPath'"
}

function Get-HKLMValueInfo {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name
    )
    $subKeyPath = Convert-HKLMPathToSubKey -PSPath $Path
    $subKey = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($subKeyPath, $false)
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

function Convert-ToInt32Unchecked {
    param([Parameter(Mandatory)]$Value)
    if ($Value -is [uint32]) {
        return [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$Value), 0)
    }
    if ($Value -is [int64]) {
        $u32 = [uint32]([int64]$Value -band 0xFFFFFFFF)
        return [BitConverter]::ToInt32([BitConverter]::GetBytes($u32), 0)
    }
    return [int]$Value
}

function Set-HKLMValueIdempotent {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$Value,
        [Parameter(Mandatory)][ValidateSet('DWord', 'String')][string]$Type
    )

    $info = Get-HKLMValueInfo -Path $Path -Name $Name
    $expectedKind = if ($Type -eq 'DWord') { [Microsoft.Win32.RegistryValueKind]::DWord } else { [Microsoft.Win32.RegistryValueKind]::String }

    $alreadyMatch = $false
    if ($info.ValueExists -and $info.Kind -eq $expectedKind) {
        if ($Type -eq 'DWord') {
            $curI32 = Convert-ToInt32Unchecked -Value $info.Value
            $tgtI32 = Convert-ToInt32Unchecked -Value $Value
            $alreadyMatch = ($curI32 -eq $tgtI32)
        }
        else {
            $alreadyMatch = ([string]$info.Value -eq [string]$Value)
        }
    }

    if ($alreadyMatch) {
        Write-ApplyLog -Level 'SKIP' -Message "$Path\$Name already $Value ($Type)."
        return
    }

    $currentDesc = if (-not $info.KeyExists) {
        '<key missing>'
    }
    elseif (-not $info.ValueExists) {
        '<missing>'
    }
    else {
        "$($info.Value) ($($info.Kind))"
    }

    if ($isDryRun) {
        if (-not $info.KeyExists) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would create key '$Path' and set '$Name' = $Value ($Type) (current: $currentDesc)."
        }
        else {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would set $Path\$Name = $Value ($Type) (current: $currentDesc)."
        }
        return
    }

    if ($PSCmdlet.ShouldProcess("$Path\$Name", "Set $Type value to $Value")) {
        $subKeyPath = Convert-HKLMPathToSubKey -PSPath $Path
        $subKey = [Microsoft.Win32.Registry]::LocalMachine.CreateSubKey($subKeyPath, $true)
        try {
            $typedVal = if ($Type -eq 'DWord') { Convert-ToInt32Unchecked -Value $Value } else { [string]$Value }
            $subKey.SetValue($Name, $typedVal, $expectedKind)
        }
        finally {
            $subKey.Close()
        }
        Write-ApplyLog -Level 'APPLIED' -Message "Set $Path\$Name = $Value ($Type) (previous: $currentDesc)."
    }
}

function Get-PowerSettingIndices {
    param(
        [Parameter(Mandatory)][string]$SchemeGuid,
        [Parameter(Mandatory)][string]$SubgroupGuid,
        [Parameter(Mandatory)][string]$SettingGuid
    )
    $raw = (& powercfg /qh $SchemeGuid $SubgroupGuid $SettingGuid 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        return [PSCustomObject]@{ Found = $false; AC = $null; DC = $null; Raw = $raw }
    }
    $acMatch = [regex]::Match($raw, '(?im)(?:AC Power Setting Index|CA actual)[^:]*:\s*(0x[0-9a-fA-F]+)')
    $dcMatch = [regex]::Match($raw, '(?im)(?:DC Power Setting Index|CC actual)[^:]*:\s*(0x[0-9a-fA-F]+)')
    if (-not $acMatch.Success -and -not $dcMatch.Success) {
        $allHex = [regex]::Matches($raw, 'Current[^:]*:\s*(0x[0-9a-fA-F]+)')
        if ($allHex.Count -ge 2) {
            return [PSCustomObject]@{
                Found = $true
                AC    = [Convert]::ToUInt32($allHex[0].Groups[1].Value, 16)
                DC    = [Convert]::ToUInt32($allHex[1].Groups[1].Value, 16)
                Raw   = $raw
            }
        }
        return [PSCustomObject]@{ Found = $false; AC = $null; DC = $null; Raw = $raw }
    }
    $acVal = if ($acMatch.Success) { [Convert]::ToUInt32($acMatch.Groups[1].Value, 16) } else { $null }
    $dcVal = if ($dcMatch.Success) { [Convert]::ToUInt32($dcMatch.Groups[1].Value, 16) } else { $null }
    return [PSCustomObject]@{ Found = $true; AC = $acVal; DC = $dcVal; Raw = $raw }
}

function Resolve-NicClassSubkey {
    param(
        [Parameter(Mandatory)][string]$InterfaceGuid
    )
    $targetGuidB = ([guid]::Parse($InterfaceGuid)).ToString('B')
    $classBase = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
    foreach ($child in @(Get-ChildItem -LiteralPath $classBase -ErrorAction SilentlyContinue)) {
        if ($child.PSChildName -notmatch '^\d{4}$') { continue }
        $psPath = "$classBase\$($child.PSChildName)"
        $idInfo = Get-HKLMValueInfo -Path $psPath -Name 'NetCfgInstanceId'
        if ($idInfo.ValueExists -and ([string]$idInfo.Value).Equals($targetGuidB, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $psPath
        }
    }
    return $null
}

function Get-NicAdvPropValue {
    param(
        [Parameter(Mandatory)][string]$AdapterName,
        [Parameter(Mandatory)][string]$Keyword
    )
    $prop = Get-NetAdapterAdvancedProperty -Name $AdapterName -RegistryKeyword $Keyword -IncludeHidden -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $prop) {
        return [PSCustomObject]@{ Found = $false; Value = $null; DisplayValue = $null }
    }
    $vals = @($prop.RegistryValue | Where-Object { $null -ne $_ })
    $valStr = if ($vals.Count -gt 0) { [string]$vals[0] } else { '' }
    return [PSCustomObject]@{
        Found        = $true
        Value        = $valStr
        DisplayValue = [string]$prop.DisplayValue
    }
}

function Set-NicAdvPropIdempotent {
    param(
        [Parameter(Mandatory)][string]$AdapterName,
        [Parameter(Mandatory)][string]$Keyword,
        [Parameter(Mandatory)][string]$TargetValue,
        [string]$Description = ''
    )
    $cur = Get-NicAdvPropValue -AdapterName $AdapterName -Keyword $Keyword
    if (-not $cur.Found) {
        Write-ApplyLog -Level 'WARN' -Message "NIC '$AdapterName' advanced property '$Keyword' not found."
        return
    }
    if ($cur.Value -eq $TargetValue) {
        Write-ApplyLog -Level 'SKIP' -Message "NIC '$AdapterName' [$Keyword] already '$TargetValue' ($($cur.DisplayValue))."
        return
    }
    $descSuffix = if ($Description) { " ($Description)" } else { '' }
    if ($isDryRun) {
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would set NIC '$AdapterName' [$Keyword] from '$($cur.Value)' ($($cur.DisplayValue)) to '$TargetValue'$descSuffix."
        return
    }
    if ($PSCmdlet.ShouldProcess("$AdapterName [$Keyword]", "Set RegistryValue='$TargetValue'")) {
        Set-NetAdapterAdvancedProperty -Name $AdapterName -RegistryKeyword $Keyword -RegistryValue $TargetValue -NoRestart -ErrorAction Stop
        Write-ApplyLog -Level 'APPLIED' -Message "Set NIC '$AdapterName' [$Keyword] = '$TargetValue'$descSuffix (previous: '$($cur.Value)')."
    }
}

function Set-NicPowerDisableIdempotent {
    param(
        [Parameter(Mandatory)][object]$Adapter,
        [AllowNull()][AllowEmptyString()][string]$ClassPath = ''
    )
    $name = $Adapter.Name

    # 1. PnPCapabilities = 24 (0x18) on driver class subkey
    if ($ClassPath) {
        Set-HKLMValueIdempotent -Path $ClassPath -Name 'PnPCapabilities' -Value 24 -Type 'DWord'
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message "Could not resolve driver class subkey for '$name'; skipping PnPCapabilities."
    }

    # 2. MSPower_DeviceEnable = $false via root\wmi
    $msPower = $null
    if (-not [string]::IsNullOrWhiteSpace([string]$Adapter.PnPDeviceID)) {
        $msPower = Get-CimInstance -Namespace 'root\wmi' -ClassName 'MSPower_DeviceEnable' -ErrorAction SilentlyContinue |
            Where-Object { $_.InstanceName -like "$($Adapter.PnPDeviceID)*" } |
            Select-Object -First 1
    }

    if ($null -eq $msPower) {
        Write-ApplyLog -Level 'WARN' -Message "MSPower_DeviceEnable instance not found for '$name' ($($Adapter.PnPDeviceID))."
    }
    elseif (-not [bool]$msPower.Enable) {
        Write-ApplyLog -Level 'SKIP' -Message "MSPower_DeviceEnable already False for '$name'."
    }
    elseif ($isDryRun) {
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would set MSPower_DeviceEnable.Enable = False for '$name' ($($msPower.InstanceName))."
    }
    elseif ($PSCmdlet.ShouldProcess("$name MSPower_DeviceEnable", 'Set Enable = False')) {
        $msPower.Enable = $false
        Set-CimInstance -InputObject $msPower -ErrorAction Stop
        Write-ApplyLog -Level 'APPLIED' -Message "Set MSPower_DeviceEnable.Enable = False for '$name'."
    }

    # 3. Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice Disabled -NoRestart
    if ($isDryRun) {
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would invoke Set-NetAdapterPowerManagement -Name '$name' -AllowComputerToTurnOffDevice Disabled -NoRestart (if supported by driver)."
    }
    elseif ($PSCmdlet.ShouldProcess($name, 'Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice Disabled -NoRestart')) {
        try {
            $pm = Get-NetAdapterPowerManagement -Name $name -ErrorAction Stop
            if ($pm.AllowComputerToTurnOffDevice -eq 'Disabled') {
                Write-ApplyLog -Level 'SKIP' -Message "NetAdapterPowerManagement AllowComputerToTurnOffDevice already Disabled for '$name'."
            }
            else {
                Set-NetAdapterPowerManagement -Name $name -AllowComputerToTurnOffDevice Disabled -NoRestart -ErrorAction Stop
                Write-ApplyLog -Level 'APPLIED' -Message "Set NetAdapterPowerManagement AllowComputerToTurnOffDevice = Disabled for '$name' (previous: $($pm.AllowComputerToTurnOffDevice))."
            }
        }
        catch {
            Write-ApplyLog -Level 'WARN' -Message "NetAdapterPowerManagement CIM call not supported or failed for '$name' (PnPCapabilities/MSPower_DeviceEnable still applied): $($_.Exception.Message)"
        }
    }
}

$dnsBound = $PSBoundParameters.ContainsKey('DnsServers')
$normalizedDnsList = [System.Collections.Generic.List[string]]::new()
$dnsValidationErrors = [System.Collections.Generic.List[string]]::new()

if ($dnsBound) {
    foreach ($entry in @($DnsServers)) {
        if ($null -eq $entry -or [string]::IsNullOrWhiteSpace($entry)) {
            $dnsValidationErrors.Add("'<empty>'")
            continue
        }
        $commaParts = $entry -split ','
        foreach ($part in @($commaParts)) {
            if ([string]::IsNullOrWhiteSpace($part)) {
                $dnsValidationErrors.Add("'<empty>'")
                continue
            }
            $tokens = @($part.Trim() -split '\s+')
            foreach ($tok in $tokens) {
                $parsedIp = $null
                $isValidIpv4 = ($tok -match '^(?:0|[1-9]\d{0,2})(?:\.(?:0|[1-9]\d{0,2})){3}$') -and
                               [ipaddress]::TryParse($tok, [ref]$parsedIp) -and
                               ($parsedIp.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) -and
                               ($parsedIp.IPAddressToString -ne '0.0.0.0')
                if (-not $isValidIpv4) {
                    $dnsValidationErrors.Add("'$tok'")
                }
                else {
                    $normalizedDnsList.Add($parsedIp.IPAddressToString)
                }
            }
        }
    }
    if ($dnsValidationErrors.Count -eq 0 -and $normalizedDnsList.Count -eq 0) {
        $dnsValidationErrors.Add('no valid IPv4 addresses provided')
    }
}
[string[]]$targetDnsServers = $normalizedDnsList.ToArray()

$isAdmin = Test-IsAdmin
Write-ApplyLog -Level 'INFO' -Message "Starting apply_network_admin.ps1 (IsAdmin=$isAdmin, DryRun=$isDryRun, SkipDnsBenchmark=$SkipDnsBenchmark, DnsServers=[$($targetDnsServers -join ', ')], BackupDir='$BackupDir')"

if (-not $isAdmin -and -not $isDryRun) {
    Write-ApplyLog -Level 'ERROR' -Message 'Administrator privileges required for apply_network_admin.ps1. Re-run in an elevated PowerShell session, or pass -DryRun / -WhatIf for read-only inspection.'
    exit 2
}

if ($dnsBound -and $dnsValidationErrors.Count -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Invalid -DnsServers value(s): $($dnsValidationErrors -join ', '). Provide one or more valid IPv4 addresses (e.g., -DnsServers `"9.9.9.9,8.8.8.8`")."
    exit 1
}

try {
    # =========================================================================
    # Pre-Mutation M1 Backup Gate
    # =========================================================================
    $powerBackupDir = Join-Path $BackupDir 'power'
    $powFiles = @(Get-ChildItem -Path $powerBackupDir -Filter 'power_scheme_*.pow' -ErrorAction SilentlyContinue)
    if ($powFiles.Count -eq 0) {
        $backupScript = Join-Path $PSScriptRoot 'backup_baseline.ps1'
        if ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "No .pow exports found in '$powerBackupDir'; when elevated would run '$backupScript -BackupRoot `"$BackupDir`" -IncludeElevated' (never -Force)."
        }
        else {
            Write-ApplyLog -Level 'INFO' -Message "No .pow exports found in '$powerBackupDir'; invoking backup_baseline.ps1 -IncludeElevated before mutations."
            $global:LASTEXITCODE = 0
            & $backupScript -BackupRoot $BackupDir -IncludeElevated
            $backupExit = $LASTEXITCODE
            $powAfter = @(Get-ChildItem -Path $powerBackupDir -Filter 'power_scheme_*.pow' -ErrorAction SilentlyContinue)
            if ($backupExit -ne 0 -or $powAfter.Count -eq 0) {
                throw "Pre-mutation elevated backup failed (exit=$backupExit, powCount=$($powAfter.Count))."
            }
            Write-ApplyLog -Level 'APPLIED' -Message "Pre-mutation elevated backup complete ($($powAfter.Count) .pow files in '$powerBackupDir')."
        }
    }
    else {
        Write-ApplyLog -Level 'SKIP' -Message "Pre-mutation elevated backup already present ($($powFiles.Count) .pow files in '$powerBackupDir')."
    }

    # =========================================================================
    # Resolve Physical Adapters & Driver Class Subkeys
    # =========================================================================
    $wifiNic = Get-NetAdapter -Name 'Wi-Fi' -ErrorAction SilentlyContinue
    $ethNic = Get-NetAdapter -Name 'Ethernet' -ErrorAction SilentlyContinue

    $wifiGuidB = if ($wifiNic) { ([guid]::Parse([string]$wifiNic.InterfaceGuid)).ToString('B').ToUpper() } else { $null }
    $ethGuidB = if ($ethNic) { ([guid]::Parse([string]$ethNic.InterfaceGuid)).ToString('B').ToUpper() } else { $null }

    $wifiClassPath = if ($wifiNic) { Resolve-NicClassSubkey -InterfaceGuid ([string]$wifiNic.InterfaceGuid) } else { $null }
    $ethClassPath = if ($ethNic) { Resolve-NicClassSubkey -InterfaceGuid ([string]$ethNic.InterfaceGuid) } else { $null }

    Write-ApplyLog -Level 'INFO' -Message "Resolved NICs: Wi-Fi Guid=$wifiGuidB Class='$wifiClassPath'; Ethernet Guid=$ethGuidB Class='$ethClassPath'."

    # =========================================================================
    # F11: Wi-Fi Power-Saving & DC Wireless/ASPM Hardening
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F11: Wi-Fi Power-Saving, DC ASPM/Wireless & NIC Power ---'

    $stableSchemeGuid = '46476e86-f770-4378-857e-3a25bf0336b4'
    $forbiddenSchemes = @('8ac65e32-075f-48d5-828b-02e93ad3c1f8', 'bb7e893a-b185-4dfa-8895-72bd923714aa')

    $activeRaw = (& powercfg /getactivescheme 2>&1) -join ' '
    $activeGuidMatch = [regex]::Match($activeRaw, '([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})')
    $activeGuid = if ($activeGuidMatch.Success) { $activeGuidMatch.Groups[1].Value.ToLower() } else { '' }
    $schemesRaw = (& powercfg /list 2>&1) -join "`n"
    $stableExists = $schemesRaw -match [regex]::Escape($stableSchemeGuid)

    if ($activeGuid -eq $stableSchemeGuid) {
        Write-ApplyLog -Level 'SKIP' -Message "Active power scheme is already 'Gaming Estable (Sin Bateria)' ($stableSchemeGuid)."
    }
    elseif ($stableExists) {
        if ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would activate 'Gaming Estable (Sin Bateria)' ($stableSchemeGuid) instead of '$activeGuid' (never Ultimate Performance)."
        }
        elseif ($PSCmdlet.ShouldProcess($stableSchemeGuid, 'Activate Gaming Estable (Sin Bateria) power scheme')) {
            $out = (& powercfg /setactive $stableSchemeGuid 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to activate power scheme ${stableSchemeGuid}: $out"
            }
            Write-ApplyLog -Level 'APPLIED' -Message "Activated 'Gaming Estable (Sin Bateria)' ($stableSchemeGuid) (previous: '$activeGuid')."
        }
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message "Scheme '$stableSchemeGuid' not found; keeping current scheme '$activeGuid' (never switching to Ultimate Performance)."
    }

    if ($forbiddenSchemes -contains $activeGuid -and -not $stableExists) {
        Write-ApplyLog -Level 'WARN' -Message "Current scheme '$activeGuid' is an Ultimate Performance scheme and '$stableSchemeGuid' is absent."
    }

    $targetScheme = if ($stableExists) { $stableSchemeGuid } else { 'SCHEME_CURRENT' }

    # Read-only guardrail check: never raise AC PROCTHROTTLEMAX > 99% or PERFBOOSTMODE > 0
    $subProc = '54533251-82be-4824-96c1-47b60b740d00'
    $procMax = Get-PowerSettingIndices -SchemeGuid $targetScheme -SubgroupGuid $subProc -SettingGuid 'bc5038f7-23e0-4960-96da-33abaf5935ec'
    $perfBoost = Get-PowerSettingIndices -SchemeGuid $targetScheme -SubgroupGuid $subProc -SettingGuid 'be337238-0d82-4146-a960-4f3749d470c7'
    Write-ApplyLog -Level 'INFO' -Message "Power guardrails preserved ($targetScheme): PROCTHROTTLEMAX AC=$($procMax.AC)% (never >99%), PERFBOOSTMODE AC=$($perfBoost.AC) (never >0)."

    $powerSpecs = @(
        @{ Name = 'Wireless Power Saving'; Sub = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1'; Set = '12bbebe6-58d6-4636-95bb-3217ef867c1a'; Target = 0 },
        @{ Name = 'PCIe ASPM'; Sub = '501a4d13-42af-4429-9fd1-a8218c268e20'; Set = 'ee12f906-d277-404b-b6da-e5fa1a576df5'; Target = 0 }
    )

    $powerSchemeModified = $false
    foreach ($ps in $powerSpecs) {
        $idx = Get-PowerSettingIndices -SchemeGuid $targetScheme -SubgroupGuid $ps.Sub -SettingGuid $ps.Set
        if (-not $idx.Found) {
            Write-ApplyLog -Level 'WARN' -Message "Could not query power setting '$($ps.Name)' ($($ps.Set)) on $targetScheme."
            continue
        }

        if ($null -eq $idx.AC) {
            Write-ApplyLog -Level 'WARN' -Message "Could not parse AC index for power setting '$($ps.Name)' ($($ps.Set)) on $targetScheme."
        }
        elseif ([int]$idx.AC -eq [int]$ps.Target) {
            Write-ApplyLog -Level 'SKIP' -Message "Power setting '$($ps.Name)' AC already $($ps.Target) on $targetScheme."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would set '$($ps.Name)' AC from $($idx.AC) to $($ps.Target) on $targetScheme."
        }
        elseif ($PSCmdlet.ShouldProcess("$targetScheme\$($ps.Name) AC", "Set index to $($ps.Target)")) {
            $out = (& powercfg /setacvalueindex $targetScheme $ps.Sub $ps.Set $ps.Target 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0) {
                throw "powercfg /setacvalueindex failed for $($ps.Name): $out"
            }
            $powerSchemeModified = $true
            Write-ApplyLog -Level 'APPLIED' -Message "Set '$($ps.Name)' AC from $($idx.AC) to $($ps.Target) on $targetScheme."
        }

        if ($null -eq $idx.DC) {
            Write-ApplyLog -Level 'WARN' -Message "Could not parse DC index for power setting '$($ps.Name)' ($($ps.Set)) on $targetScheme."
        }
        elseif ([int]$idx.DC -eq [int]$ps.Target) {
            Write-ApplyLog -Level 'SKIP' -Message "Power setting '$($ps.Name)' DC already $($ps.Target) on $targetScheme."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would set '$($ps.Name)' DC from $($idx.DC) to $($ps.Target) on $targetScheme."
        }
        elseif ($PSCmdlet.ShouldProcess("$targetScheme\$($ps.Name) DC", "Set index to $($ps.Target)")) {
            $out = (& powercfg /setdcvalueindex $targetScheme $ps.Sub $ps.Set $ps.Target 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0) {
                throw "powercfg /setdcvalueindex failed for $($ps.Name): $out"
            }
            $powerSchemeModified = $true
            Write-ApplyLog -Level 'APPLIED' -Message "Set '$($ps.Name)' DC from $($idx.DC) to $($ps.Target) on $targetScheme."
        }
    }

    if ($powerSchemeModified -and -not $isDryRun -and $targetScheme -ne 'SCHEME_CURRENT') {
        $actOut = (& powercfg /setactive $targetScheme 2>&1) -join ' '
        if ($LASTEXITCODE -ne 0) {
            Write-ApplyLog -Level 'WARN' -Message "powercfg /setactive $targetScheme returned exit ${LASTEXITCODE}: $actOut"
        }
        else {
            Write-ApplyLog -Level 'APPLIED' -Message "Re-activated power scheme $targetScheme to commit AC/DC power changes."
        }
    }

    if ($wifiNic) {
        Set-NicAdvPropIdempotent -AdapterName 'Wi-Fi' -Keyword 'LpsEn' -TargetValue '0' -Description 'Low / preserve optimize_network_gaming.ps1'
        Set-NicPowerDisableIdempotent -Adapter $wifiNic -ClassPath $wifiClassPath
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message "Adapter 'Wi-Fi' not found."
    }

    if ($ethNic) {
        Set-NicPowerDisableIdempotent -Adapter $ethNic -ClassPath $ethClassPath
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message "Adapter 'Ethernet' not found."
    }

    # =========================================================================
    # F12: Wi-Fi Roaming Lock & 2.4 GHz Guard
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F12: Wi-Fi Roaming Lock & 2.4 GHz Guard ---'

    if ($wifiNic) {
        Set-NicAdvPropIdempotent -AdapterName 'Wi-Fi' -Keyword 'RegROAMSensitiveLevel' -TargetValue '127' -Description 'Deshabilitar roaming'
        Set-NicAdvPropIdempotent -AdapterName 'Wi-Fi' -Keyword 'PreferBand' -TargetValue '2' -Description '3. 5G first'
    }

    # WLAN profile connectionmode=manual for 2.4 GHz (CLARO1_F3E3B9), auto for 5 GHz (CLARO2_F3E3B9)
    $profileTargets = @(
        @{ Name = 'CLARO1_F3E3B9'; Mode = 'manual'; Label = '2.4 GHz fallback guard' },
        @{ Name = 'CLARO2_F3E3B9'; Mode = 'auto'; Label = '5 GHz primary' }
    )
    foreach ($pt in $profileTargets) {
        $profName = $pt.Name
        $targetMode = $pt.Mode
        $profRaw = (& netsh wlan show profile name="$profName" 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) {
            Write-ApplyLog -Level 'WARN' -Message "WLAN profile '$profName' not found or unreadable; skipping."
            continue
        }
        $isManual = $profRaw -match '(?im)(?:Connection mode|Modo de conexi[oó]n)\s*:\s*(?:Connect manually|Conectar manualmente)'
        $isAuto = $profRaw -match '(?im)(?:Connection mode|Modo de conexi[oó]n)\s*:\s*(?:Connect automatically|Conectar autom[aá]ticamente)'

        if (($targetMode -eq 'manual' -and $isManual) -or ($targetMode -eq 'auto' -and $isAuto)) {
            Write-ApplyLog -Level 'SKIP' -Message "WLAN profile '$profName' ($($pt.Label)) already connectionmode=$targetMode."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would run: netsh wlan set profileparameter name=`"$profName`" connectionmode=$targetMode ($($pt.Label))."
        }
        elseif ($PSCmdlet.ShouldProcess("WLAN profile '$profName'", "Set connectionmode=$targetMode")) {
            $setOut = (& netsh wlan set profileparameter name="$profName" connectionmode=$targetMode 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0) {
                Write-ApplyLog -Level 'ERROR' -Message "netsh wlan set profileparameter name=`"$profName`" connectionmode=$targetMode failed ($LASTEXITCODE): $setOut"
            }
            else {
                Write-ApplyLog -Level 'APPLIED' -Message "Set WLAN profile '$profName' ($($pt.Label)) connectionmode=$targetMode."
            }
        }
    }

    # Verify gaming_wlan_scan_toggle.ps1 & ensure steady-state autoconfig remains enabled
    $toggleScript = Join-Path $PSScriptRoot 'gaming_wlan_scan_toggle.ps1'
    if (Test-Path -LiteralPath $toggleScript) {
        $toggleJsonRaw = (& $toggleScript -Mode status -Json 2>&1) -join "`n"
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($toggleJsonRaw)) {
            $toggleState = $null
            try {
                $toggleState = $toggleJsonRaw | ConvertFrom-Json
            }
            catch {
                $toggleState = $null
            }
            if ($null -ne $toggleState -and $null -ne $toggleState.PSObject.Properties['autoconfig_enabled']) {
                $wlanSvcStat = if ($null -ne $toggleState.PSObject.Properties['wlan_svc_status']) { $toggleState.wlan_svc_status } else { 'unknown' }
                if ($toggleState.autoconfig_enabled -eq $false) {
                    if ($isDryRun) {
                        Write-ApplyLog -Level 'DRY-RUN' -Message 'WLAN autoconfig is currently disabled; when elevated would restore steady-state enabled via gaming_wlan_scan_toggle.ps1 -Mode restore.'
                    }
                    elseif ($PSCmdlet.ShouldProcess('Wi-Fi autoconfig', 'Restore steady-state enabled via gaming_wlan_scan_toggle.ps1')) {
                        & $toggleScript -Mode restore | Out-Null
                        Write-ApplyLog -Level 'APPLIED' -Message 'Restored steady-state WLAN autoconfig=enabled via gaming_wlan_scan_toggle.ps1 -Mode restore.'
                    }
                }
                else {
                    Write-ApplyLog -Level 'SKIP' -Message "Verified gaming_wlan_scan_toggle.ps1 present; steady-state WLAN autoconfig=$($toggleState.autoconfig_enabled), WlanSvc=$wlanSvcStat."
                }
            }
            else {
                Write-ApplyLog -Level 'WARN' -Message "gaming_wlan_scan_toggle.ps1 -Mode status -Json returned unparseable or incomplete JSON: $toggleJsonRaw"
            }
        }
        else {
            Write-ApplyLog -Level 'WARN' -Message "gaming_wlan_scan_toggle.ps1 -Mode status -Json returned exit ${LASTEXITCODE}: $toggleJsonRaw"
        }
        Write-ApplyLog -Level 'INFO' -Message "Per-session WLAN scan toggle: before gaming run 'pwsh -NoProfile -File scripts\gaming_wlan_scan_toggle.ps1 -Mode gaming'; after gaming run '... -Mode restore'."
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message "Missing toggle script at '$toggleScript'."
    }

    # =========================================================================
    # F13: Delivery Optimization Policy, NetworkDaemon & DNS Guardrail
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F13: DoSvc Policy, NetworkDaemon & DNS Preservation ---'

    $doPolicyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization'
    # Verified against C:\Windows\PolicyDefinitions\DeliveryOptimization.admx:
    # DODownloadMode (0 = HTTP only, no peering), DOPercentageMaxBackgroundBandwidth (min=0, max=100, decimal)
    Set-HKLMValueIdempotent -Path $doPolicyPath -Name 'DODownloadMode' -Value 0 -Type 'DWord'
    Set-HKLMValueIdempotent -Path $doPolicyPath -Name 'DOPercentageMaxBackgroundBandwidth' -Value 25 -Type 'DWord'

    # Ensure NetworkDaemon stays Disabled / Stopped if present
    $ndSvc = Get-Service -Name 'NetworkDaemon' -ErrorAction SilentlyContinue
    if ($null -eq $ndSvc) {
        Write-ApplyLog -Level 'SKIP' -Message "Service 'NetworkDaemon' is absent; skipping."
    }
    else {
        $ndStartInfo = Get-HKLMValueInfo -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\NetworkDaemon' -Name 'Start'
        $ndStartInt = if ($ndStartInfo.ValueExists) { [int]$ndStartInfo.Value } else { -1 }
        $ndRunning = ($ndSvc.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Running)
        if (-not $ndRunning -and $ndStartInt -eq 4) {
            Write-ApplyLog -Level 'SKIP' -Message "Service 'NetworkDaemon' already Stopped and Disabled (Start=4)."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would stop and disable service 'NetworkDaemon' (Status=$($ndSvc.Status), Start=$ndStartInt)."
        }
        elseif ($PSCmdlet.ShouldProcess('NetworkDaemon', 'Stop and set Disabled')) {
            if ($ndRunning) {
                Stop-Service -Name 'NetworkDaemon' -Force -ErrorAction Stop
            }
            if ($ndStartInt -ne 4) {
                $scOut = (& sc.exe config NetworkDaemon start= disabled 2>&1) -join ' '
                if ($LASTEXITCODE -ne 0) {
                    throw "sc.exe config NetworkDaemon start= disabled failed ($LASTEXITCODE): $scOut"
                }
            }
            Write-ApplyLog -Level 'APPLIED' -Message 'Ensured NetworkDaemon is Stopped and Disabled (Start=4).'
        }
    }

    # DNS Preservation by default ($targetDnsServers empty) vs explicit override
    $wifiDnsObj = Get-DnsClientServerAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
    [string[]]$currentWifiDns = @(if ($null -ne $wifiDnsObj -and $null -ne $wifiDnsObj.ServerAddresses) { $wifiDnsObj.ServerAddresses })

    if ($targetDnsServers.Count -eq 0) {
        Write-ApplyLog -Level 'SKIP' -Message "Preserving existing Wi-Fi IPv4 DNS [$($currentWifiDns -join ', ')] untouched (-DnsServers not specified; user guardrail active)."
    }
    else {
        $sameDns = ($currentWifiDns.Count -eq $targetDnsServers.Count)
        if ($sameDns) {
            for ($d = 0; $d -lt $targetDnsServers.Count; $d++) {
                if ($currentWifiDns[$d] -ne $targetDnsServers[$d]) { $sameDns = $false; break }
            }
        }

        if ($sameDns) {
            Write-ApplyLog -Level 'SKIP' -Message "Wi-Fi IPv4 DNS already set to [$($targetDnsServers -join ', ')]."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would set Wi-Fi IPv4 DNS from [$($currentWifiDns -join ', ')] to [$($targetDnsServers -join ', ')] and flush DNS cache."
        }
        elseif ($PSCmdlet.ShouldProcess('Wi-Fi DNS', "Set ServerAddresses=$($targetDnsServers -join ', ') and flush cache")) {
            Set-DnsClientServerAddress -InterfaceAlias 'Wi-Fi' -ServerAddresses $targetDnsServers -ErrorAction Stop
            & ipconfig /flushdns 2>&1 | Out-Null
            Clear-DnsClientCache -ErrorAction SilentlyContinue
            Write-ApplyLog -Level 'APPLIED' -Message "Updated Wi-Fi IPv4 DNS -> [$($targetDnsServers -join ', ')] (previous: [$($currentWifiDns -join ', ')]) and flushed DNS cache."
        }
    }

    if (-not $SkipDnsBenchmark) {
        try {
            Import-Module DnsClient -ErrorAction SilentlyContinue
            $pingClient = [System.Net.NetworkInformation.Ping]::new()
            try {
                $null = $pingClient.Send('127.0.0.1', 500)
                $null = Resolve-DnsName -Name 'localhost' -DnsOnly -NoHostsFile -ErrorAction SilentlyContinue

                $benchTargets = @('1.1.1.1', '9.9.9.9', '8.8.8.8')
                $benchDomains = @('google.com', 'epicgames.com', 'cloudflare.com')
                $summaryParts = [System.Collections.Generic.List[string]]::new()
                $sw = [System.Diagnostics.Stopwatch]::new()

                foreach ($srv in $benchTargets) {
                    $icmpRtts = [System.Collections.Generic.List[double]]::new()
                    for ($p = 0; $p -lt 5; $p++) {
                        $sw.Restart()
                        $rep = $null
                        try { $rep = $pingClient.Send($srv, 1500) } catch { $rep = $null }
                        $sw.Stop()
                        if ($null -ne $rep -and $rep.Status -eq [System.Net.NetworkInformation.IPStatus]::Success) {
                            $icmpRtts.Add($sw.Elapsed.TotalMilliseconds)
                        }
                    }
                    $icmpAvg = if ($icmpRtts.Count -gt 0) { [Math]::Round(($icmpRtts | Measure-Object -Average).Average, 2) } else { 'timeout' }

                    $dnsRtts = [System.Collections.Generic.List[double]]::new()
                    foreach ($dom in $benchDomains) {
                        $sw.Restart()
                        $ok = $false
                        try {
                            $null = Resolve-DnsName -Name $dom -Server $srv -Type A -DnsOnly -NoHostsFile -ErrorAction Stop
                            $ok = $true
                        }
                        catch { $ok = $false }
                        $sw.Stop()
                        if ($ok) { $dnsRtts.Add($sw.Elapsed.TotalMilliseconds) }
                    }
                    $dnsAvg = if ($dnsRtts.Count -gt 0) { [Math]::Round(($dnsRtts | Measure-Object -Average).Average, 2) } else { 'fail' }
                    $summaryParts.Add("${srv}: ICMP=${icmpAvg}ms / DNS=${dnsAvg}ms")
                }
            }
            finally {
                $pingClient.Dispose()
            }
            Write-ApplyLog -Level 'INFO' -Message "Advisory DNS/ICMP benchmark (read-only): $($summaryParts -join '; '). To switch DNS explicitly, pass -DnsServers `"9.9.9.9,8.8.8.8`"."
        }
        catch {
            Write-ApplyLog -Level 'WARN' -Message "Advisory DNS benchmark skipped due to error: $($_.Exception.Message)"
        }
    }
    else {
        Write-ApplyLog -Level 'SKIP' -Message 'Advisory DNS benchmark skipped (-SkipDnsBenchmark specified).'
    }

    # =========================================================================
    # F14: Ethernet Standby, Checksum Offloads & TCP/MMCSS/QoS Preservation
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F14: Ethernet Standby, TCP Stack & Offload Preservation ---'

    if ($ethNic) {
        $ethZeroProps = @(
            @{ Keyword = 'EnableGreenEthernet'; Desc = 'Ethernet ecologico' },
            @{ Keyword = 'PowerSavingMode'; Desc = 'Power Saving Mode' },
            @{ Keyword = 'GigaLite'; Desc = 'Gigabit Lite' },
            @{ Keyword = '*InterruptModeration'; Desc = 'Interrupt Moderation' },
            @{ Keyword = '*FlowControl'; Desc = 'Flow Control' }
        )
        foreach ($ep in $ethZeroProps) {
            Set-NicAdvPropIdempotent -AdapterName 'Ethernet' -Keyword $ep.Keyword -TargetValue '0' -Description $ep.Desc
        }

        # Verify checksum offloads remain '3' (Rx y Tx activado) - NEVER disable
        $checksumKeywords = @(
            '*IPChecksumOffloadIPv4',
            '*TCPChecksumOffloadIPv4',
            '*TCPChecksumOffloadIPv6',
            '*UDPChecksumOffloadIPv4',
            '*UDPChecksumOffloadIPv6'
        )
        $csSummary = [System.Collections.Generic.List[string]]::new()
        foreach ($ck in $checksumKeywords) {
            $cVal = Get-NicAdvPropValue -AdapterName 'Ethernet' -Keyword $ck
            if ($cVal.Found -and $cVal.Value -ne '3') {
                Set-NicAdvPropIdempotent -AdapterName 'Ethernet' -Keyword $ck -TargetValue '3' -Description 'Restore Rx y Tx activado checksum offload'
            }
            $csSummary.Add("$ck=$($cVal.Value)")
        }
        Write-ApplyLog -Level 'INFO' -Message "Verified Ethernet checksum offloads preserved (target '3'): $($csSummary -join ', ')."
    }

    # Verify critical network services Dhcp & NlaSvc remain active/untouched
    $netSvcStates = @()
    foreach ($svcName in @('Dhcp', 'NlaSvc')) {
        $s = Get-Service -Name $svcName -ErrorAction SilentlyContinue
        if ($s) { $netSvcStates += "$svcName=$($s.Status)/$($s.StartType)" }
    }
    Write-ApplyLog -Level 'INFO' -Message "Protected core network services untouched: $($netSvcStates -join ', ')."

    # Preserve netsh int tcp global settings from optimize_network_gaming.ps1 (only set if drifted)
    $tcpGlobalRaw = (& netsh int tcp show global 2>&1) -join "`n"
    $tcpExpected = @(
        @{ Param = 'rsc'; Value = 'disabled'; Pattern = 'Receive Segment Coalescing State\s*:\s*(enabled|disabled)' },
        @{ Param = 'rss'; Value = 'enabled'; Pattern = 'Receive-Side Scaling State\s*:\s*(enabled|disabled)' },
        @{ Param = 'autotuninglevel'; Value = 'normal'; Pattern = 'Receive Window Auto-Tuning Level\s*:\s*(\S+)' },
        @{ Param = 'ecncapability'; Value = 'disabled'; Pattern = 'ECN Capability\s*:\s*(enabled|disabled)' },
        @{ Param = 'timestamps'; Value = 'disabled'; Pattern = 'RFC 1323 Timestamps\s*:\s*(enabled|disabled)' },
        @{ Param = 'nonsackrttresiliency'; Value = 'disabled'; Pattern = 'Non Sack Rtt Resiliency\s*:\s*(enabled|disabled)' }
    )
    foreach ($te in $tcpExpected) {
        $m = [regex]::Match($tcpGlobalRaw, "(?im)$($te.Pattern)")
        $cur = if ($m.Success) { $m.Groups[1].Value.ToLower() } else { '<unknown>' }
        if ($cur -eq $te.Value) {
            Write-ApplyLog -Level 'SKIP' -Message "TCP global $($te.Param) already '$($te.Value)'."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would run: netsh int tcp set global $($te.Param)=$($te.Value) (current: '$cur')."
        }
        elseif ($PSCmdlet.ShouldProcess("TCP global $($te.Param)", "Set to $($te.Value)")) {
            $tOut = (& netsh int tcp set global "$($te.Param)=$($te.Value)" 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0) {
                throw "netsh int tcp set global $($te.Param)=$($te.Value) failed ($LASTEXITCODE): $tOut"
            }
            Write-ApplyLog -Level 'APPLIED' -Message "Set TCP global $($te.Param)=$($te.Value) (previous: '$cur')."
        }
    }

    # Verify Wi-Fi RSC disabled (Disable-NetAdapterRsc only if enabled)
    if ($wifiNic) {
        $wifiRsc = Get-NetAdapterRsc -Name 'Wi-Fi' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $wifiRsc) {
            Write-ApplyLog -Level 'SKIP' -Message 'Get-NetAdapterRsc returned no CIM object for Wi-Fi; global rsc=disabled active.'
        }
        elseif (-not $wifiRsc.IPv4Enabled -and -not $wifiRsc.IPv6Enabled) {
            Write-ApplyLog -Level 'SKIP' -Message 'Wi-Fi RSC already disabled (IPv4Enabled=False, IPv6Enabled=False).'
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would run Disable-NetAdapterRsc -Name 'Wi-Fi' (IPv4=$($wifiRsc.IPv4Enabled), IPv6=$($wifiRsc.IPv6Enabled))."
        }
        elseif ($PSCmdlet.ShouldProcess('Wi-Fi RSC', 'Disable-NetAdapterRsc')) {
            Disable-NetAdapterRsc -Name 'Wi-Fi' -ErrorAction Stop
            Write-ApplyLog -Level 'APPLIED' -Message 'Disabled Wi-Fi RSC via Disable-NetAdapterRsc.'
        }
    }

    # TcpAckFrequency=1 & TCPNoDelay=1 on both Wi-Fi & Ethernet Tcpip\Parameters\Interfaces\{GUID}
    $tcpIfBase = 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces'
    foreach ($ifaceEntry in @(
        @{ Label = 'Wi-Fi'; Guid = $wifiGuidB },
        @{ Label = 'Ethernet'; Guid = $ethGuidB }
    )) {
        if ($ifaceEntry.Guid) {
            $ifPath = Join-Path $tcpIfBase $ifaceEntry.Guid
            if (Test-Path -LiteralPath $ifPath) {
                Set-HKLMValueIdempotent -Path $ifPath -Name 'TcpAckFrequency' -Value 1 -Type 'DWord'
                Set-HKLMValueIdempotent -Path $ifPath -Name 'TCPNoDelay' -Value 1 -Type 'DWord'
            }
            else {
                Write-ApplyLog -Level 'WARN' -Message "Tcpip interface key missing for $($ifaceEntry.Label): '$ifPath'."
            }
        }
    }

    # MMCSS NetworkThrottlingIndex=0xFFFFFFFF & SystemResponsiveness=0 (preserve; set only if drifted)
    $mmcssPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile'
    Set-HKLMValueIdempotent -Path $mmcssPath -Name 'NetworkThrottlingIndex' -Value 0xFFFFFFFF -Type 'DWord'
    Set-HKLMValueIdempotent -Path $mmcssPath -Name 'SystemResponsiveness' -Value 0 -Type 'DWord'

    # QoS policies preservation (Fortnite_Gaming_QoS & FortnitePriorizacion)
    $qosPolicies = @(Get-NetQosPolicy -ErrorAction SilentlyContinue)
    $fgQos = $qosPolicies | Where-Object { $_.Name -eq 'Fortnite_Gaming_QoS' } | Select-Object -First 1
    $fpQos = $qosPolicies | Where-Object { $_.Name -eq 'FortnitePriorizacion' } | Select-Object -First 1

    if ($null -ne $fgQos) {
        Write-ApplyLog -Level 'SKIP' -Message "QoS policy 'Fortnite_Gaming_QoS' preserved (DSCP=$($fgQos.DSCPValue), App=$($fgQos.AppPathName))."
    }
    elseif ($isDryRun) {
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would create QoS policy 'Fortnite_Gaming_QoS' (DSCPAction=46, FortniteClient-Win64-Shipping.exe)."
    }
    elseif ($PSCmdlet.ShouldProcess('Fortnite_Gaming_QoS', 'Create DSCP 46 QoS policy')) {
        New-NetQosPolicy -Name 'Fortnite_Gaming_QoS' -AppPathNameMatchCondition 'FortniteClient-Win64-Shipping.exe' -NetworkProfile All -DSCPAction 46 -IPProtocolMatchCondition Both -ErrorAction Stop | Out-Null
        Write-ApplyLog -Level 'APPLIED' -Message "Created QoS policy 'Fortnite_Gaming_QoS' (DSCP=46)."
    }

    if ($null -ne $fpQos) {
        Write-ApplyLog -Level 'INFO' -Message "Baseline QoS policy 'FortnitePriorizacion' preserved (DSCP=$($fpQos.DSCPValue), App=$($fpQos.AppPathName))."
    }
}
catch {
    Write-ApplyLog -Level 'ERROR' -Message "Unhandled error in apply_network_admin.ps1: $($_.Exception.Message)"
    exit 1
}

if ($script:errorCount -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Completed apply_network_admin.ps1 with $($script:errorCount) error(s)."
    exit 1
}

Write-ApplyLog -Level 'INFO' -Message "Completed apply_network_admin.ps1 cleanly (DryRun=$isDryRun)."
exit 0
