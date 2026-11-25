<#
.SYNOPSIS
    Restores system, registry, power plans, hibernation, DNS, NIC, WLAN, TCP, services, and tasks from backups/.
.DESCRIPTION
    Supports -DryRun / -WhatIf for safe non-mutating validation, writes timestamped logs to reports/rollback.log,
    enforces Administrator privileges for live restoration, handles absent_keys.json cleanup, performs temp-scheme
    swap when re-importing .pow files, and distinguishes DHCP vs static DNS via dns_before.json.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$BackupDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups'),
    [string]$ReportDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'reports'),
    [switch]$DryRun,
    [switch]$SkipPowerSchemes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$script:isWhatIf = [bool]$WhatIfPreference
$isDryRun = [bool]($DryRun -or $WhatIfPreference)
$logPath = Join-Path $ReportDir 'rollback.log'
if (-not $script:isWhatIf) {
    if (-not (Test-Path -LiteralPath $ReportDir)) {
        New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($logPath, "", $utf8NoBom)
}

$script:errorCount = 0

function Write-RollbackLog {
    param(
        [Parameter(Mandatory)][ValidateSet('INFO', 'DRY-RUN', 'APPLIED', 'SKIP', 'WARN', 'ERROR')][string]$Level,
        [Parameter(Mandatory)][string]$Message
    )
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $line = "[$ts] [$Level] $Message"
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

function Convert-RegPathToPSDrive {
    param([Parameter(Mandatory)][string]$RegPath)
    if ($RegPath -match '^HKCU\\(.+)$') {
        return "Registry::HKEY_CURRENT_USER\$($Matches[1])"
    }
    elseif ($RegPath -match '^HKLM\\(.+)$') {
        return "Registry::HKEY_LOCAL_MACHINE\$($Matches[1])"
    }
    return "Registry::$RegPath"
}

function Get-OptionalProp {
    param(
        [AllowNull()][object]$Obj,
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()]$Default = $null
    )
    if ($null -eq $Obj) { return $Default }
    $prop = $Obj.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $Default }
    return $prop.Value
}

$isAdmin = Test-IsAdmin
Write-RollbackLog -Level 'INFO' -Message "Starting rollback_all.ps1 (BackupDir='$BackupDir', DryRun=$isDryRun, SkipPowerSchemes=$SkipPowerSchemes, IsAdmin=$isAdmin)"

if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) {
    Write-RollbackLog -Level 'ERROR' -Message "Backup directory '$BackupDir' does not exist."
    exit 1
}

$systemDir = Join-Path $BackupDir 'system'
$powerDir = Join-Path $BackupDir 'power'
$networkDir = Join-Path $BackupDir 'network'

$validBackupExts = @('.reg', '.pow', '.json', '.csv', '.txt')
$backupSearchDirs = @(@($BackupDir, $systemDir, $powerDir, $networkDir) | Where-Object { Test-Path -LiteralPath $_ -PathType Container })
$backupArtifacts = @(
    foreach ($dir in $backupSearchDirs) {
        Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue | Where-Object {
            ($validBackupExts -contains $_.Extension.ToLowerInvariant()) -and ($_.Length -gt 0)
        }
    }
)
if ($backupArtifacts.Count -eq 0) {
    Write-RollbackLog -Level 'ERROR' -Message "Backup directory '$BackupDir' contains no non-empty backup artifacts (.reg, .pow, .json, .csv, .txt)."
    exit 1
}

if (-not $isAdmin -and -not $isDryRun) {
    Write-RollbackLog -Level 'ERROR' -Message "Administrator privileges required for live rollback (HKLM .reg, .pow, services, tasks, hibernation, WLAN). Re-run elevated or pass -DryRun."
    exit 1
}

# Step 1: Registry .reg imports + removal of keys that were absent at baseline
$regFiles = @()
if (Test-Path -LiteralPath $systemDir) {
    $regFiles += Get-ChildItem -Path $systemDir -Filter 'hkcu_*.reg' | Sort-Object Name
    $regFiles += Get-ChildItem -Path $systemDir -Filter 'hklm_*.reg' | Where-Object { $_.Name -ne 'hklm_powerschemes_fallback.reg' } | Sort-Object Name
}
if (Test-Path -LiteralPath $networkDir) {
    $regFiles += Get-ChildItem -Path $networkDir -Filter '*.reg' | Sort-Object Name
}

foreach ($rf in $regFiles) {
    if ($isDryRun) {
        Write-RollbackLog -Level 'DRY-RUN' -Message "Would run: reg.exe import `"$($rf.FullName)`""
    }
    elseif ($PSCmdlet.ShouldProcess($rf.FullName, 'Import registry backup')) {
        $out = (& reg.exe import $rf.FullName 2>&1) -join ' '
        if ($LASTEXITCODE -eq 0) {
            Write-RollbackLog -Level 'APPLIED' -Message "Imported registry backup: $($rf.Name)"
        }
        else {
            Write-RollbackLog -Level 'ERROR' -Message "reg import failed ($LASTEXITCODE) for $($rf.Name): $out"
        }
    }
}

$absentPath = Join-Path $systemDir 'absent_keys.json'
if (Test-Path -LiteralPath $absentPath) {
    $absentJson = Get-Content -LiteralPath $absentPath -Raw -Encoding utf8 | ConvertFrom-Json
    $absentEntries = @(Get-OptionalProp -Obj $absentJson -Name 'absent_keys' -Default @() | Where-Object { $null -ne $_ })
    foreach ($entry in $absentEntries) {
        $regKey = [string](Get-OptionalProp -Obj $entry -Name 'registry_path' -Default '')
        if ([string]::IsNullOrWhiteSpace($regKey)) { continue }
        $psKey = Convert-RegPathToPSDrive -RegPath $regKey
        if (Test-Path -LiteralPath $psKey) {
            if ($isDryRun) {
                Write-RollbackLog -Level 'DRY-RUN' -Message "Would remove baseline-absent key: $regKey"
            }
            elseif ($PSCmdlet.ShouldProcess($regKey, 'Delete baseline-absent registry key')) {
                $out = (& reg.exe delete $regKey /f 2>&1) -join ' '
                if ($LASTEXITCODE -eq 0) {
                    Write-RollbackLog -Level 'APPLIED' -Message "Removed baseline-absent key: $regKey"
                }
                else {
                    Write-RollbackLog -Level 'WARN' -Message "Failed removing $regKey ($LASTEXITCODE): $out"
                }
            }
        }
        else {
            Write-RollbackLog -Level 'SKIP' -Message "Baseline-absent key already absent: $regKey"
        }
    }
}
else {
    Write-RollbackLog -Level 'SKIP' -Message "absent_keys.json not found; skipping absent-key cleanup."
}

# Step 2: Power Schemes (.pow) & Active Plan
$powerActivePath = Join-Path $powerDir 'power_active_before.txt'
$targetActiveGuid = $null
if (Test-Path -LiteralPath $powerActivePath) {
    $powerActiveRaw = Get-Content -LiteralPath $powerActivePath -Raw -Encoding utf8
    if ($powerActiveRaw -match 'Power Scheme GUID:\s*([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})') {
        $targetActiveGuid = $Matches[1].ToLower()
    }
}

$powFiles = @(if (Test-Path -LiteralPath $powerDir) { Get-ChildItem -Path $powerDir -Filter 'power_scheme_*.pow' | Sort-Object Name })
if ($SkipPowerSchemes) {
    Write-RollbackLog -Level 'SKIP' -Message "Skipping .pow power scheme restore (-SkipPowerSchemes specified)."
}
elseif ($powFiles.Count -eq 0) {
    Write-RollbackLog -Level 'WARN' -Message "No .pow files found in '$powerDir' (backup may have been captured non-elevated)."
    if ($targetActiveGuid) {
        if ($isDryRun) {
            Write-RollbackLog -Level 'DRY-RUN' -Message "Would set active power scheme: powercfg /setactive $targetActiveGuid"
        }
        else {
            $setOut = (& powercfg /setactive $targetActiveGuid 2>&1) -join ' '
            if ($LASTEXITCODE -eq 0) {
                Write-RollbackLog -Level 'APPLIED' -Message "Restored active power scheme GUID $targetActiveGuid (without .pow re-import)."
            }
            else {
                Write-RollbackLog -Level 'ERROR' -Message "powercfg /setactive $targetActiveGuid failed ($LASTEXITCODE): $setOut"
            }
        }
    }
}
else {
    if ($isDryRun) {
        Write-RollbackLog -Level 'DRY-RUN' -Message "Would import temporary clone of $($powFiles[0].Name), swap active scheme, delete+reimport $($powFiles.Count) .pow file(s), set active '$targetActiveGuid', and delete temporary scheme."
    }
    else {
        $tempGuid = $null
        try {
            $tmpImportOut = (& powercfg /import $powFiles[0].FullName 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                Write-RollbackLog -Level 'ERROR' -Message "Temporary powercfg /import failed ($LASTEXITCODE) for $($powFiles[0].Name): $tmpImportOut"
            }
            elseif ($tmpImportOut -match '([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})') {
                $tempGuid = $Matches[1].ToLower()
                $tmpActOut = (& powercfg /setactive $tempGuid 2>&1) -join ' '
                if ($LASTEXITCODE -ne 0) {
                    Write-RollbackLog -Level 'ERROR' -Message "powercfg /setactive failed ($LASTEXITCODE) for temporary scheme ${tempGuid}: $tmpActOut"
                }
            }
            foreach ($pf in $powFiles) {
                if ($pf.BaseName -match 'power_scheme_([0-9a-fA-F-]+)$') {
                    $g = $Matches[1].ToLower()
                    & powercfg /delete $g 2>&1 | Out-Null
                    $impOut = (& powercfg /import $pf.FullName $g 2>&1) -join ' '
                    if ($LASTEXITCODE -eq 0) {
                        Write-RollbackLog -Level 'APPLIED' -Message "Imported power scheme $g from $($pf.Name)"
                    }
                    else {
                        Write-RollbackLog -Level 'ERROR' -Message "powercfg /import failed ($LASTEXITCODE) for ${g}: $impOut"
                    }
                }
            }
            if ($targetActiveGuid) {
                $actOut = (& powercfg /setactive $targetActiveGuid 2>&1) -join ' '
                if ($LASTEXITCODE -eq 0) {
                    Write-RollbackLog -Level 'APPLIED' -Message "Activated baseline power scheme $targetActiveGuid"
                }
                else {
                    Write-RollbackLog -Level 'ERROR' -Message "powercfg /setactive $targetActiveGuid failed ($LASTEXITCODE): $actOut"
                }
            }
        }
        finally {
            if ($tempGuid -and $tempGuid -ne $targetActiveGuid) {
                $curActiveRaw = (& powercfg /getactivescheme 2>&1) -join ' '
                $curActiveGuid = $null
                if ($curActiveRaw -match '([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})') {
                    $curActiveGuid = $Matches[1].ToLower()
                }
                if ($curActiveGuid -eq $tempGuid) {
                    $switchedAway = $false
                    if ($targetActiveGuid -and $targetActiveGuid -ne $tempGuid) {
                        & powercfg /setactive $targetActiveGuid 2>&1 | Out-Null
                        if ($LASTEXITCODE -eq 0) { $switchedAway = $true }
                    }
                    if (-not $switchedAway) {
                        $listRaw = (& powercfg /list 2>&1) -join "`n"
                        foreach ($m in [regex]::Matches($listRaw, '([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})')) {
                            $cand = $m.Groups[1].Value.ToLower()
                            if ($cand -ne $tempGuid) {
                                & powercfg /setactive $cand 2>&1 | Out-Null
                                if ($LASTEXITCODE -eq 0) {
                                    $switchedAway = $true
                                    break
                                }
                            }
                        }
                    }
                }
                $delOut = (& powercfg /delete $tempGuid 2>&1) -join ' '
                if ($LASTEXITCODE -ne 0) {
                    Write-RollbackLog -Level 'ERROR' -Message "Failed to delete temporary power scheme ${tempGuid} ($LASTEXITCODE): $delOut"
                }
            }
        }
    }
}

# Step 3: Hibernation
$osStatePath = Join-Path $systemDir 'os_state_before.json'
if (Test-Path -LiteralPath $osStatePath) {
    $osState = Get-Content -LiteralPath $osStatePath -Raw -Encoding utf8 | ConvertFrom-Json
    $hiberfilVal = [bool](Get-OptionalProp -Obj $osState -Name 'hiberfil_exists' -Default $false)
    $hiberEnabledVal = [bool](Get-OptionalProp -Obj $osState -Name 'hibernate_enabled' -Default $false)
    $shouldEnableHiber = ($hiberfilVal -or $hiberEnabledVal)
    if ($shouldEnableHiber) {
        if ($isDryRun) {
            Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore hibernation: powercfg /h on"
        }
        else {
            & powercfg /h on 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-RollbackLog -Level 'APPLIED' -Message "Restored hibernation (powercfg /h on)"
            }
            else {
                Write-RollbackLog -Level 'WARN' -Message "powercfg /h on returned exit code $LASTEXITCODE"
            }
        }
    }
    else {
        Write-RollbackLog -Level 'SKIP' -Message "Hibernation was disabled at baseline; leaving disabled."
    }
}
else {
    Write-RollbackLog -Level 'SKIP' -Message "os_state_before.json not found; skipping hibernation restore."
}

# Step 4: DNS Client restoration (Static vs DHCP unambiguous)
$dnsPath = Join-Path $networkDir 'dns_before.json'
if (Test-Path -LiteralPath $dnsPath) {
    $dnsJson = Get-Content -LiteralPath $dnsPath -Raw -Encoding utf8 | ConvertFrom-Json
    $dnsIfaces = @(Get-OptionalProp -Obj $dnsJson -Name 'interfaces' -Default @() | Where-Object { $null -ne $_ })
    foreach ($iface in $dnsIfaces) {
        $ifaceName = [string](Get-OptionalProp -Obj $iface -Name 'InterfaceName' -Default '')
        if ([string]::IsNullOrWhiteSpace($ifaceName)) { continue }
        $nicNow = Get-NetAdapter -Name $ifaceName -ErrorAction SilentlyContinue
        if (-not $nicNow) {
            Write-RollbackLog -Level 'SKIP' -Message "Adapter '$ifaceName' not present; skipping DNS restore."
            continue
        }
        $isStaticVal = [bool](Get-OptionalProp -Obj $iface -Name 'IsStatic' -Default $false)
        $nsVal = [string](Get-OptionalProp -Obj $iface -Name 'NameServer' -Default '')
        $hasStatic = ($isStaticVal -and -not [string]::IsNullOrWhiteSpace($nsVal))
        if ($hasStatic) {
            $addrs = @($nsVal -split '[,\s]+' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
            if ($isDryRun) {
                Write-RollbackLog -Level 'DRY-RUN' -Message "Would set static DNS on '$ifaceName' -> $($addrs -join ', ')"
            }
            else {
                try {
                    Set-DnsClientServerAddress -InterfaceIndex $nicNow.InterfaceIndex -ServerAddresses $addrs -ErrorAction Stop
                    Write-RollbackLog -Level 'APPLIED' -Message "Restored static DNS on '$ifaceName' -> $($addrs -join ', ')"
                }
                catch {
                    Write-RollbackLog -Level 'ERROR' -Message "Failed restoring static DNS on '$ifaceName': $($_.Exception.Message)"
                }
            }
        }
        else {
            if ($isDryRun) {
                Write-RollbackLog -Level 'DRY-RUN' -Message "Would reset DNS to DHCP on '$ifaceName' (-ResetServerAddresses)"
            }
            else {
                try {
                    Set-DnsClientServerAddress -InterfaceIndex $nicNow.InterfaceIndex -ResetServerAddresses -ErrorAction Stop
                    Write-RollbackLog -Level 'APPLIED' -Message "Restored DHCP DNS on '$ifaceName'"
                }
                catch {
                    Write-RollbackLog -Level 'ERROR' -Message "Failed restoring DHCP DNS on '$ifaceName': $($_.Exception.Message)"
                }
            }
        }
    }
}
else {
    Write-RollbackLog -Level 'SKIP' -Message "dns_before.json not found; skipping DNS restore."
}

# Step 5: NIC Advanced Properties & MSPower_DeviceEnable
$nicCsvPath = Join-Path $networkDir 'nic_advanced_before.csv'
if (Test-Path -LiteralPath $nicCsvPath) {
    $baseRows = @(Import-Csv -LiteralPath $nicCsvPath -Encoding utf8 | Where-Object { $null -ne $_ })
    $currentProps = @(Get-NetAdapterAdvancedProperty -IncludeHidden -ErrorAction SilentlyContinue)
    foreach ($row in $baseRows) {
        $rowKw = [string](Get-OptionalProp -Obj $row -Name 'RegistryKeyword' -Default '')
        $rowName = [string](Get-OptionalProp -Obj $row -Name 'Name' -Default '')
        if ([string]::IsNullOrWhiteSpace($rowKw) -or [string]::IsNullOrWhiteSpace($rowName)) { continue }
        $rowVal = [string](Get-OptionalProp -Obj $row -Name 'RegistryValue' -Default '')
        $match = $currentProps | Where-Object { $_.Name -eq $rowName -and $_.RegistryKeyword -eq $rowKw } | Select-Object -First 1
        if (-not $match) { continue }
        $curVal = if ($null -ne $match.RegistryValue) { (@($match.RegistryValue) -join ',') } else { '' }
        if ($curVal -ne $rowVal) {
            if ($isDryRun) {
                Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore NIC '$rowName' [$rowKw]: '$curVal' -> '$rowVal'"
            }
            else {
                try {
                    Set-NetAdapterAdvancedProperty -Name $rowName -RegistryKeyword $rowKw -RegistryValue $rowVal -NoRestart -ErrorAction Stop
                    Write-RollbackLog -Level 'APPLIED' -Message "Restored NIC '$rowName' [$rowKw] -> '$rowVal'"
                }
                catch {
                    Write-RollbackLog -Level 'WARN' -Message "Could not restore NIC '$rowName' [$rowKw]: $($_.Exception.Message)"
                }
            }
        }
    }
    Write-RollbackLog -Level 'INFO' -Message "Checked $($baseRows.Count) NIC advanced property rows against baseline."
}

function Get-BaselineMsPowerEnableTarget {
    param(
        [Parameter(Mandatory)][string]$AdapterName,
        [Parameter(Mandatory)][string]$NetworkDir,
        [Parameter(Mandatory)][string]$BackupRoot
    )
    $nicPowerBaselinePath = Join-Path $NetworkDir 'nic_power_baseline.json'
    if (Test-Path -LiteralPath $nicPowerBaselinePath) {
        try {
            $candJson = Get-Content -LiteralPath $nicPowerBaselinePath -Raw -Encoding utf8 | ConvertFrom-Json
            if ($null -ne $candJson) {
                if ($null -ne $candJson.PSObject.Properties[$AdapterName] -and $null -ne $candJson.$AdapterName) {
                    $sub = $candJson.$AdapterName
                    if ($null -ne $sub.PSObject.Properties['ms_power_device_enable'] -and $null -ne $sub.ms_power_device_enable) {
                        return [bool]$sub.ms_power_device_enable
                    }
                }
                if ($null -ne $candJson.PSObject.Properties['adapters'] -and $null -ne $candJson.adapters -and $null -ne $candJson.adapters.PSObject.Properties[$AdapterName] -and $null -ne $candJson.adapters.$AdapterName) {
                    $sub = $candJson.adapters.$AdapterName
                    if ($null -ne $sub.PSObject.Properties['ms_power_device_enable'] -and $null -ne $sub.ms_power_device_enable) {
                        return [bool]$sub.ms_power_device_enable
                    }
                }
                if ($null -ne $candJson.PSObject.Properties['ms_power_device_enable'] -and $null -ne $candJson.ms_power_device_enable) {
                    $hasAdapterObj = ($null -ne $candJson.PSObject.Properties['adapter'] -and $null -ne $candJson.adapter -and $null -ne $candJson.adapter.PSObject.Properties['name'])
                    if (-not $hasAdapterObj -or [string]$candJson.adapter.name -eq $AdapterName) {
                        return [bool]$candJson.ms_power_device_enable
                    }
                }
            }
        }
        catch {
            # Safe fallback below if baseline JSON cannot be parsed
        }
    }

    $baselineNetReportPath = Join-Path (Split-Path -Parent $BackupRoot) 'reports\baseline_network.json'
    if (Test-Path -LiteralPath $baselineNetReportPath) {
        try {
            $repJson = Get-Content -LiteralPath $baselineNetReportPath -Raw -Encoding utf8 | ConvertFrom-Json
            if ($null -ne $repJson -and
                $null -ne $repJson.PSObject.Properties['adapter'] -and $null -ne $repJson.adapter -and
                $null -ne $repJson.adapter.PSObject.Properties['name'] -and [string]$repJson.adapter.name -eq $AdapterName -and
                $null -ne $repJson.PSObject.Properties['nic_power_state'] -and $null -ne $repJson.nic_power_state -and
                $null -ne $repJson.nic_power_state.PSObject.Properties['ms_power_device_enable'] -and $null -ne $repJson.nic_power_state.ms_power_device_enable) {
                return [bool]$repJson.nic_power_state.ms_power_device_enable
            }
        }
        catch {
            # Safe fallback to $true if baseline report JSON cannot be parsed
        }
    }

    return $true
}

foreach ($nicName in @('Wi-Fi', 'Ethernet')) {
    $nic = Get-NetAdapter -Name $nicName -ErrorAction SilentlyContinue
    if ($nic -and -not [string]::IsNullOrWhiteSpace([string]$nic.PnPDeviceID)) {
        $targetMsPowerEnable = Get-BaselineMsPowerEnableTarget -AdapterName $nicName -NetworkDir $networkDir -BackupRoot $BackupDir
        $msPower = Get-CimInstance -Namespace 'root\wmi' -ClassName 'MSPower_DeviceEnable' -ErrorAction SilentlyContinue |
            Where-Object { $_.InstanceName -like "$($nic.PnPDeviceID)*" } | Select-Object -First 1
        if ($msPower -and ([bool]$msPower.Enable -ne $targetMsPowerEnable)) {
            if ($isDryRun) {
                Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore $nicName MSPower_DeviceEnable = $targetMsPowerEnable"
            }
            elseif ($PSCmdlet.ShouldProcess("$nicName MSPower_DeviceEnable", "Set Enable = $targetMsPowerEnable")) {
                try {
                    $msPower.Enable = $targetMsPowerEnable
                    Set-CimInstance -InputObject $msPower -ErrorAction Stop
                    Write-RollbackLog -Level 'APPLIED' -Message "Restored $nicName MSPower_DeviceEnable = $targetMsPowerEnable"
                }
                catch {
                    Write-RollbackLog -Level 'WARN' -Message "Failed restoring $nicName MSPower_DeviceEnable: $($_.Exception.Message)"
                }
            }
        }
    }
}

$ethRegPath = Join-Path $networkDir 'eth_driver_reg.reg'
if (Test-Path -LiteralPath $ethRegPath) {
    $ethRegRaw = Get-Content -LiteralPath $ethRegPath -Raw -ErrorAction SilentlyContinue
    if ($null -ne $ethRegRaw -and -not ($ethRegRaw -match '"PnPCapabilities"=')) {
        $ethNic = Get-NetAdapter -Name 'Ethernet' -ErrorAction SilentlyContinue
        if ($ethNic -and -not [string]::IsNullOrWhiteSpace([string]$ethNic.InterfaceGuid)) {
            $ethGuidB = ([guid]::Parse([string]$ethNic.InterfaceGuid)).ToString('B')
            $classBase = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
            Get-ChildItem -Path $classBase -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' } | ForEach-Object {
                $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
                if ($props -and $null -ne $props.PSObject.Properties['NetCfgInstanceId'] -and $props.NetCfgInstanceId -and
                    $props.NetCfgInstanceId.ToString().Equals($ethGuidB, [System.StringComparison]::OrdinalIgnoreCase)) {
                    if ($null -ne $props.PSObject.Properties['PnPCapabilities']) {
                        if ($isDryRun) {
                            Write-RollbackLog -Level 'DRY-RUN' -Message "Would remove Ethernet PnPCapabilities (absent in eth_driver_reg.reg) from $($_.PSPath)"
                        }
                        elseif ($PSCmdlet.ShouldProcess("$($_.PSPath)\PnPCapabilities", 'Remove baseline-absent PnPCapabilities')) {
                            try {
                                Remove-ItemProperty -LiteralPath $_.PSPath -Name 'PnPCapabilities' -ErrorAction Stop
                                Write-RollbackLog -Level 'APPLIED' -Message "Removed Ethernet PnPCapabilities (absent in eth_driver_reg.reg)"
                            }
                            catch {
                                Write-RollbackLog -Level 'WARN' -Message "Failed removing Ethernet PnPCapabilities: $($_.Exception.Message)"
                            }
                        }
                    }
                }
            }
        }
    }
}

# Step 6: WLAN Autoconfig & Profile Connection Mode
$wlanBackupPath = Join-Path $networkDir 'wlan_profiles_before.txt'
if ($isDryRun) {
    Write-RollbackLog -Level 'DRY-RUN' -Message "Would ensure WLAN autoconfig enabled: netsh wlan set autoconfig enabled=yes interface=`"Wi-Fi`""
}
else {
    & netsh wlan set autoconfig enabled=yes interface="Wi-Fi" 2>&1 | Out-Null
    Write-RollbackLog -Level 'APPLIED' -Message "Ensured WLAN autoconfig enabled on Wi-Fi"
}

if (Test-Path -LiteralPath $wlanBackupPath) {
    $wlanRaw = Get-Content -LiteralPath $wlanBackupPath -Raw -Encoding utf8
    # Parse each profile block to see if Connection mode was Connect automatically
    $blocks = $wlanRaw -split '--- netsh wlan show profile name="'
    foreach ($blk in ($blocks | Select-Object -Skip 1)) {
        if ($blk -match '^([^"]+)" ---') {
            $profName = $Matches[1]
            if ($blk -match '(?:Connection mode|Modo de conexi[oó]n)\s*:\s*(?:Connect automatically|Conectar autom[aá]ticamente)') {
                if ($isDryRun) {
                    Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore WLAN profile '$profName' connectionmode=auto"
                }
                else {
                    & netsh wlan set profileparameter name="$profName" connectionmode=auto 2>&1 | Out-Null
                    Write-RollbackLog -Level 'INFO' -Message "Verified/restored WLAN profile '$profName' connectionmode=auto"
                }
            }
        }
    }
}

# Step 7: TCP Global (e.g. RSC)
$tcpBackupPath = Join-Path $networkDir 'tcp_global_before.txt'
if (Test-Path -LiteralPath $tcpBackupPath) {
    $tcpBase = Get-Content -LiteralPath $tcpBackupPath -Raw -Encoding utf8
    if ($tcpBase -match 'Receive Segment Coalescing State\s*:\s*(enabled|disabled)') {
        $baseRsc = $Matches[1].ToLower()
        $curTcp = (& netsh int tcp show global 2>&1) -join "`n"
        if ($curTcp -match 'Receive Segment Coalescing State\s*:\s*(enabled|disabled)') {
            $curRsc = $Matches[1].ToLower()
            if ($curRsc -ne $baseRsc) {
                if ($isDryRun) {
                    Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore TCP RSC: $curRsc -> $baseRsc"
                }
                else {
                    & netsh int tcp set global rsc=$baseRsc 2>&1 | Out-Null
                    Write-RollbackLog -Level 'APPLIED' -Message "Restored TCP RSC -> $baseRsc"
                }
            }
        }
    }
}

# Step 8: Services (diff-based, checks DelayedAutostart, skips null StartType & dynamic per-user services)
$servicesPath = Join-Path $systemDir 'services_baseline.json'
if (Test-Path -LiteralPath $servicesPath) {
    $baseServices = @(Get-Content -LiteralPath $servicesPath -Raw -Encoding utf8 | ConvertFrom-Json | Where-Object { $null -ne $_ })
    $currentSvcMap = @{}
    foreach ($s in (Get-Service)) {
        $currentSvcMap[$s.Name] = $s
    }
    $svcChanges = 0
    foreach ($bs in $baseServices) {
        $bsName = [string](Get-OptionalProp -Obj $bs -Name 'Name' -Default '')
        $bsStartType = [string](Get-OptionalProp -Obj $bs -Name 'StartType' -Default '')
        if ([string]::IsNullOrWhiteSpace($bsName) -or [string]::IsNullOrWhiteSpace($bsStartType)) { continue }
        if (-not $currentSvcMap.ContainsKey($bsName)) { continue }
        $curSvc = $currentSvcMap[$bsName]
        $curStart = if ($null -ne $curSvc.StartType) { $curSvc.StartType.ToString() } else { $null }
        $svcRegPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$bsName"
        $svcReg = Get-ItemProperty -LiteralPath $svcRegPath -ErrorAction SilentlyContinue
        $curDelayed = ($null -ne $svcReg -and $null -ne $svcReg.PSObject.Properties['DelayedAutostart'] -and [int]$svcReg.DelayedAutostart -eq 1)
        if ($curStart -eq 'Automatic' -and $curDelayed) {
            $curStart = 'AutomaticDelayedStart'
        }
        $targetDelayed = [bool](Get-OptionalProp -Obj $bs -Name 'DelayedAutostart' -Default $false)
        $targetStart = $bsStartType
        if ($targetStart -eq 'Automatic' -and $targetDelayed) {
            $targetStart = 'AutomaticDelayedStart'
        }
        $needsClearDelayed = ($curDelayed -and -not $targetDelayed -and $targetStart -in @('Automatic', 'Manual', 'Disabled'))
        if (([string]::IsNullOrWhiteSpace($curStart) -or $curStart -eq $targetStart) -and -not ($targetStart -eq 'Automatic' -and $curDelayed)) { continue }

        $svcChanges++
        $delayedSuffix = if ($needsClearDelayed) { ' (and clear stale DelayedAutostart=1)' } else { '' }
        if ($isDryRun) {
            Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore service '$bsName' StartType: $curStart -> $targetStart$delayedSuffix"
        }
        elseif ($PSCmdlet.ShouldProcess("Service '$bsName'", "Restore StartType -> $targetStart$delayedSuffix")) {
            try {
                if ($targetStart -eq 'AutomaticDelayedStart') {
                    $scOut = (& sc.exe config "$bsName" start= delayed-auto 2>&1) -join ' '
                    if ($LASTEXITCODE -ne 0) {
                        Write-RollbackLog -Level 'ERROR' -Message "sc.exe config '$bsName' start= delayed-auto failed ($LASTEXITCODE): $scOut"
                    }
                    else {
                        Write-RollbackLog -Level 'APPLIED' -Message "Restored service '$bsName' StartType -> AutomaticDelayedStart"
                    }
                }
                elseif ($targetStart -eq 'Automatic' -and ($curStart -eq 'AutomaticDelayedStart' -or $curDelayed)) {
                    $scOut = (& sc.exe config "$bsName" start= auto 2>&1) -join ' '
                    if ($LASTEXITCODE -ne 0) {
                        Write-RollbackLog -Level 'ERROR' -Message "sc.exe config '$bsName' start= auto failed ($LASTEXITCODE): $scOut"
                    }
                    else {
                        $postReg = Get-ItemProperty -LiteralPath $svcRegPath -ErrorAction SilentlyContinue
                        if ($postReg -and $null -ne $postReg.PSObject.Properties['DelayedAutostart'] -and [int]$postReg.DelayedAutostart -eq 1) {
                            Set-ItemProperty -LiteralPath $svcRegPath -Name 'DelayedAutostart' -Value 0 -Type DWord -ErrorAction Stop
                        }
                        Write-RollbackLog -Level 'APPLIED' -Message "Restored service '$bsName' StartType -> Automatic$delayedSuffix"
                    }
                }
                else {
                    Set-Service -Name $bsName -StartupType $targetStart -ErrorAction Stop
                    if ($needsClearDelayed) {
                        $postReg = Get-ItemProperty -LiteralPath $svcRegPath -ErrorAction SilentlyContinue
                        if ($postReg -and $null -ne $postReg.PSObject.Properties['DelayedAutostart'] -and [int]$postReg.DelayedAutostart -eq 1) {
                            Set-ItemProperty -LiteralPath $svcRegPath -Name 'DelayedAutostart' -Value 0 -Type DWord -ErrorAction Stop
                        }
                    }
                    Write-RollbackLog -Level 'APPLIED' -Message "Restored service '$bsName' StartType -> $targetStart$delayedSuffix"
                }
            }
            catch {
                Write-RollbackLog -Level 'WARN' -Message "Skipped protected/locked service '$bsName': $($_.Exception.Message)"
            }
        }
    }
    Write-RollbackLog -Level 'INFO' -Message "Checked $($baseServices.Count) baseline services ($svcChanges difference(s) found)."
}

# Step 9: Scheduled Tasks (diff-based; baseline Running treated as Ready for re-enablement)
$tasksPath = Join-Path $systemDir 'tasks_baseline.json'
if (Test-Path -LiteralPath $tasksPath) {
    $baseTasks = @(Get-Content -LiteralPath $tasksPath -Raw -Encoding utf8 | ConvertFrom-Json | Where-Object { $null -ne $_ })
    $curTaskMap = @{}
    foreach ($t in (Get-ScheduledTask)) {
        $curTaskMap["$($t.TaskPath)$($t.TaskName)"] = $t
    }
    $taskChanges = 0
    foreach ($bt in $baseTasks) {
        $btPath = [string](Get-OptionalProp -Obj $bt -Name 'TaskPath' -Default '')
        $btName = [string](Get-OptionalProp -Obj $bt -Name 'TaskName' -Default '')
        $btState = [string](Get-OptionalProp -Obj $bt -Name 'State' -Default '')
        if ([string]::IsNullOrWhiteSpace($btName) -or [string]::IsNullOrWhiteSpace($btState)) { continue }
        $key = "$btPath$btName"
        if (-not $curTaskMap.ContainsKey($key)) { continue }
        $curT = $curTaskMap[$key]
        $curState = $curT.State.ToString()
        $targetState = if ($btState -eq 'Running') { 'Ready' } else { $btState }
        if ($targetState -notin @('Ready', 'Disabled')) { continue }
        if ($targetState -eq 'Ready' -and $curState -in @('Ready', 'Running')) { continue }
        if ($curState -eq $targetState) { continue }

        $taskChanges++
        if ($isDryRun) {
            Write-RollbackLog -Level 'DRY-RUN' -Message "Would restore task '$key' State: $curState -> $targetState"
        }
        else {
            try {
                if ($targetState -eq 'Disabled') {
                    Disable-ScheduledTask -TaskPath $btPath -TaskName $btName -ErrorAction Stop | Out-Null
                }
                else {
                    Enable-ScheduledTask -TaskPath $btPath -TaskName $btName -ErrorAction Stop | Out-Null
                }
                Write-RollbackLog -Level 'APPLIED' -Message "Restored task '$key' -> $targetState"
            }
            catch {
                Write-RollbackLog -Level 'WARN' -Message "Could not restore task '$key': $($_.Exception.Message)"
            }
        }
    }
    Write-RollbackLog -Level 'INFO' -Message "Checked $($baseTasks.Count) baseline scheduled tasks ($taskChanges difference(s) found)."
}

Write-RollbackLog -Level 'INFO' -Message "Rollback finished with $($script:errorCount) critical error(s)."
if ($script:errorCount -gt 0) { exit 1 }
exit 0
