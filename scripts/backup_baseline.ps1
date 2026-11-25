<#
.SYNOPSIS
    Captures untouched pre-optimization baseline backups for OS, registry, power, services, tasks, startup, and network.
.DESCRIPTION
    Supports non-elevated execution (all HKCU/HKLM .reg exports, absent-key manifest, JSON/CSV/TXT snapshots,
    and PowerSchemes registry fallback) as well as elevated capture (.pow exports and System Restore point).
    Never dumps Wi-Fi plaintext passphrases (omits key=clear).
#>
[CmdletBinding()]
param(
    [string]$BackupRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups'),
    [switch]$IncludeElevated,
    [switch]$RefreshServicesOnly,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Write-Utf8NoBom {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
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

function Export-ServicesBaselineSnapshot {
    param([Parameter(Mandatory)][string]$OutFile)

    $services = @(Get-Service | ForEach-Object {
        $startType = if ($null -ne $_.StartType) { $_.StartType.ToString() } else { $null }
        $delayedAutostart = $false
        $svcReg = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$($_.Name)" -ErrorAction SilentlyContinue
        if ($svcReg -and $null -ne $svcReg.PSObject.Properties['DelayedAutostart']) {
            $delayedAutostart = ([int]$svcReg.DelayedAutostart -eq 1)
        }
        if ($startType -eq 'Automatic' -and $delayedAutostart) {
            $startType = 'AutomaticDelayedStart'
        }
        [PSCustomObject]@{
            Name             = $_.Name
            DisplayName      = $_.DisplayName
            Status           = $_.Status.ToString()
            StartType        = $startType
            DelayedAutostart = $delayedAutostart
        }
    })
    Write-Utf8NoBom -Path $OutFile -Content ($services | ConvertTo-Json -Depth 3)
    return $services.Count
}

$isAdmin = Test-IsAdmin
$timestampUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

$systemDir = Join-Path $BackupRoot 'system'
$powerDir = Join-Path $BackupRoot 'power'
$networkDir = Join-Path $BackupRoot 'network'

if ($RefreshServicesOnly) {
    if (-not (Test-Path -LiteralPath $systemDir)) {
        New-Item -ItemType Directory -Path $systemDir -Force | Out-Null
    }
    $svcOutPath = Join-Path $systemDir 'services_baseline.json'
    $svcCount = Export-ServicesBaselineSnapshot -OutFile $svcOutPath
    Write-Host "[OK] Refreshed $svcCount services (with DelayedAutostart fidelity) -> $svcOutPath"
    exit 0
}

$absentManifestPath = Join-Path $systemDir 'absent_keys.json'
$hasExistingBaseline = Test-Path -LiteralPath $absentManifestPath

$elevatedOnlyPass = $false
if ($hasExistingBaseline -and -not $Force) {
    if ($IncludeElevated) {
        $elevatedOnlyPass = $true
        Write-Host "[INFO] Existing baseline detected at '$BackupRoot'; preserving existing snapshots and running elevated power/restore capture only."
    }
    else {
        Write-Error "Baseline backup already exists at '$BackupRoot'. Pass -Force to overwrite, or -IncludeElevated (as Admin) to add .pow and System Restore point."
        exit 1
    }
}

foreach ($dir in @($BackupRoot, $systemDir, $powerDir, $networkDir)) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

$absentKeys = [System.Collections.Generic.List[object]]::new()
$exportErrors = [System.Collections.Generic.List[string]]::new()

function Export-RegKeySafe {
    param(
        [Parameter(Mandatory)][string]$RegPath,
        [Parameter(Mandatory)][string]$OutFile,
        [Parameter(Mandatory)][string]$Category
    )
    $psPath = Convert-RegPathToPSDrive -RegPath $RegPath
    if (-not (Test-Path -LiteralPath $psPath)) {
        $absentKeys.Add([PSCustomObject]@{
            registry_path        = $RegPath
            expected_backup_file = $OutFile
            category             = $Category
        })
        Write-Host "[ABSENT] $RegPath (recorded in absent_keys.json)"
        return
    }
    $out = & reg.exe export $RegPath $OutFile /y 2>&1
    if ($LASTEXITCODE -ne 0) {
        $msg = "reg export failed ($LASTEXITCODE) for '$RegPath': $($out -join ' ')"
        $exportErrors.Add($msg)
        Write-Warning $msg
    }
    else {
        Write-Host "[OK] Exported $RegPath -> $(Split-Path -Leaf $OutFile)"
    }
}

if (-not $elevatedOnlyPass) {
    # 1. Resolve dynamic network interface GUIDs and driver class subkeys
    $wifiAdapter = Get-NetAdapter -Name 'Wi-Fi' -ErrorAction SilentlyContinue
    $ethAdapter = Get-NetAdapter -Name 'Ethernet' -ErrorAction SilentlyContinue

    $wifiGuid = if ($wifiAdapter) { ([guid]::Parse([string]$wifiAdapter.InterfaceGuid)).ToString('B').ToUpper() } else { $null }
    $ethGuid = if ($ethAdapter) { ([guid]::Parse([string]$ethAdapter.InterfaceGuid)).ToString('B').ToUpper() } else { $null }

    $nicClassBase = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
    $wifiClassReg = 'HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0011'
    $ethClassReg = 'HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0002'

    Get-ChildItem -Path $nicClassBase -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' } | ForEach-Object {
        $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
        if ($props -and $null -ne $props.PSObject.Properties['NetCfgInstanceId'] -and $props.NetCfgInstanceId) {
            $id = $props.NetCfgInstanceId.ToString().ToUpper()
            $leaf = Split-Path -Leaf $_.PSPath
            if ($wifiGuid -and $id -eq $wifiGuid) {
                $wifiClassReg = "HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\$leaf"
            }
            elseif ($ethGuid -and $id -eq $ethGuid) {
                $ethClassReg = "HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\$leaf"
            }
        }
    }

    # 2. System HKCU exports
    $hkcuExports = @(
        @{ Path = 'HKCU\System\GameConfigStore'; File = 'hkcu_gameconfigstore.reg' },
        @{ Path = 'HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR'; File = 'hkcu_gamedvr.reg' },
        @{ Path = 'HKCU\Software\Microsoft\GameBar'; File = 'hkcu_gamebar.reg' },
        @{ Path = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'; File = 'hkcu_personalize.reg' },
        @{ Path = 'HKCU\Control Panel\Desktop'; File = 'hkcu_desktop.reg' },
        @{ Path = 'HKCU\Control Panel\Desktop\WindowMetrics'; File = 'hkcu_windowmetrics.reg' },
        @{ Path = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects'; File = 'hkcu_visualeffects.reg' },
        @{ Path = 'HKCU\Software\Microsoft\DirectX\UserGpuPreferences'; File = 'hkcu_usergpupreferences.reg' },
        @{ Path = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Run'; File = 'hkcu_run.reg' },
        @{ Path = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'; File = 'hkcu_startupapproved_run.reg' },
        @{ Path = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'; File = 'hkcu_startupapproved_startupfolder.reg' }
    )
    foreach ($item in $hkcuExports) {
        Export-RegKeySafe -RegPath $item.Path -OutFile (Join-Path $systemDir $item.File) -Category 'system_hkcu'
    }

    # 3. System HKLM exports (including keys expected absent at baseline)
    $hklmExports = @(
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard'; File = 'hklm_deviceguard.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'; File = 'hklm_hvci.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\PriorityControl'; File = 'hklm_prioritycontrol.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power'; File = 'hklm_sessionmanager_power.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling'; File = 'hklm_powerthrottling.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\PolicyManager\default\ApplicationManagement\AllowGameDVR'; File = 'hklm_policymanager_gamedvr.reg' },
        @{ Path = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR'; File = 'hklm_policies_gamedvr.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection'; File = 'hklm_datacollection.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'; File = 'hklm_graphicsdrivers.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Services\nvlddmkm\Parameters'; File = 'hklm_nvlddmkm_params.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0000'; File = 'hklm_igpu_0000.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0001'; File = 'hklm_dgpu_0001.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'; File = 'hklm_run.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'; File = 'hklm_startupapproved_run.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'; File = 'hklm_startupapproved_startupfolder.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes'; File = 'hklm_powerschemes_fallback.reg' }
    )
    foreach ($item in $hklmExports) {
        Export-RegKeySafe -RegPath $item.Path -OutFile (Join-Path $systemDir $item.File) -Category 'system_hklm'
    }

    # 4. Network HKLM exports
    $netExports = @(
        @{ Path = $wifiClassReg; File = 'wifi_driver_reg.reg' },
        @{ Path = $ethClassReg; File = 'eth_driver_reg.reg' },
        @{ Path = 'HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters'; File = 'tcpip_params.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile'; File = 'mmcss.reg' },
        @{ Path = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Psched'; File = 'psched.reg' },
        @{ Path = 'HKLM\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization'; File = 'delivery_opt_policy.reg' },
        @{ Path = 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\DeliveryOptimization\Config'; File = 'delivery_opt.reg' }
    )
    if ($wifiGuid) {
        $netExports += @{ Path = "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$wifiGuid"; File = 'tcpip_iface_wifi.reg' }
    }
    if ($ethGuid) {
        $netExports += @{ Path = "HKLM\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$ethGuid"; File = 'tcpip_iface_eth.reg' }
    }
    foreach ($item in $netExports) {
        Export-RegKeySafe -RegPath $item.Path -OutFile (Join-Path $networkDir $item.File) -Category 'network_hklm'
    }

    # 5. Write absent_keys.json
    $absentPayload = [PSCustomObject]@{
        timestamp_utc = $timestampUtc
        absent_count  = $absentKeys.Count
        absent_keys   = @($absentKeys)
    }
    Write-Utf8NoBom -Path $absentManifestPath -Content ($absentPayload | ConvertTo-Json -Depth 5)

    # 6. OS State snapshot (os_state_before.json)
    $activeSchemeRaw = (& powercfg /getactivescheme 2>&1) -join "`n"
    $activeGuid = $null
    $activeName = $null
    if ($activeSchemeRaw -match '([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\s*\((.+)\)\s*$') {
        $activeGuid = $Matches[1]
        $activeName = $Matches[2].Trim()
    }
    $hiberfilExists = [bool](Test-Path -LiteralPath 'C:\hiberfil.sys')
    $powerReg = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -ErrorAction SilentlyContinue
    $smPowerReg = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -ErrorAction SilentlyContinue
    $gameCfg = Get-ItemProperty -Path 'HKCU:\System\GameConfigStore' -ErrorAction SilentlyContinue
    $persReg = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -ErrorAction SilentlyContinue
    $dgReg = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard' -ErrorAction SilentlyContinue
    $dgCim = Get-CimInstance -Namespace 'root\Microsoft\Windows\DeviceGuard' -ClassName 'Win32_DeviceGuard' -ErrorAction SilentlyContinue | Select-Object -First 1

    $osStateBefore = [PSCustomObject]@{
        timestamp_utc                 = $timestampUtc
        is_admin                      = $isAdmin
        hiberfil_exists               = $hiberfilExists
        hibernate_enabled             = if ($null -ne $powerReg -and $null -ne $powerReg.PSObject.Properties['HibernateEnabled']) { [bool]($powerReg.HibernateEnabled -ne 0) } else { $hiberfilExists }
        fast_startup_hiberboot        = if ($null -ne $smPowerReg -and $null -ne $smPowerReg.PSObject.Properties['HiberbootEnabled']) { [int]$smPowerReg.HiberbootEnabled } else { $null }
        active_power_scheme_guid      = $activeGuid
        active_power_scheme_name      = $activeName
        gamedvr_enabled               = if ($null -ne $gameCfg -and $null -ne $gameCfg.PSObject.Properties['GameDVR_Enabled']) { [bool]($gameCfg.GameDVR_Enabled -eq 1) } else { $null }
        transparency_enabled          = if ($null -ne $persReg -and $null -ne $persReg.PSObject.Properties['EnableTransparency']) { [bool]($persReg.EnableTransparency -eq 1) } else { $null }
        enable_vbs_reg                = if ($null -ne $dgReg -and $null -ne $dgReg.PSObject.Properties['EnableVirtualizationBasedSecurity']) { [int]$dgReg.EnableVirtualizationBasedSecurity } else { $null }
        vbs_running_status            = if ($dgCim -and $null -ne $dgCim.PSObject.Properties['VirtualizationBasedSecurityStatus'] -and $null -ne $dgCim.VirtualizationBasedSecurityStatus) { [int]$dgCim.VirtualizationBasedSecurityStatus } else { $null }
    }
    Write-Utf8NoBom -Path (Join-Path $systemDir 'os_state_before.json') -Content ($osStateBefore | ConvertTo-Json -Depth 4)

    # 7. Services snapshot
    $svcCount = Export-ServicesBaselineSnapshot -OutFile (Join-Path $systemDir 'services_baseline.json')
    Write-Host "[OK] Captured $svcCount services -> services_baseline.json"

    # 8. Scheduled tasks snapshot
    $tasks = @(Get-ScheduledTask | ForEach-Object {
        [PSCustomObject]@{
            TaskPath = $_.TaskPath
            TaskName = $_.TaskName
            State    = $_.State.ToString()
        }
    })
    Write-Utf8NoBom -Path (Join-Path $systemDir 'tasks_baseline.json') -Content ($tasks | ConvertTo-Json -Depth 3)
    Write-Host "[OK] Captured $($tasks.Count) scheduled tasks -> tasks_baseline.json"

    # 9. Startup snapshot
    $wmiStartup = @(Get-CimInstance -ClassName Win32_StartupCommand -ErrorAction SilentlyContinue | ForEach-Object {
        [PSCustomObject]@{
            Name     = $_.Name
            Command  = $_.Command
            Location = $_.Location
            User     = $_.User
        }
    })
    $startupApprovedPaths = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'
    )
    $startupApprovedEntries = [System.Collections.Generic.List[object]]::new()
    foreach ($saPath in $startupApprovedPaths) {
        if (Test-Path -LiteralPath $saPath) {
            $item = Get-Item -LiteralPath $saPath -ErrorAction SilentlyContinue
            if ($item) {
                foreach ($valName in $item.GetValueNames()) {
                    if ([string]::IsNullOrEmpty($valName)) { continue }
                    $rawBytes = $item.GetValue($valName, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                    if ($rawBytes -is [byte[]]) {
                        $hexStr = ($rawBytes | ForEach-Object { $_.ToString('X2') }) -join ''
                        $isEnabled = ($rawBytes.Length -gt 0 -and (($rawBytes[0] % 2) -eq 0))
                        $startupApprovedEntries.Add([PSCustomObject]@{
                            registry_path = $saPath
                            name          = $valName
                            enabled       = $isEnabled
                            bytes_hex     = $hexStr
                        })
                    }
                }
            }
        }
    }
    $startupPayload = [PSCustomObject]@{
        timestamp_utc         = $timestampUtc
        win32_startup_command = $wmiStartup
        startup_approved      = @($startupApprovedEntries)
    }
    Write-Utf8NoBom -Path (Join-Path $systemDir 'startup_baseline.json') -Content ($startupPayload | ConvertTo-Json -Depth 5)
    Write-Host "[OK] Captured startup snapshot -> startup_baseline.json"

    # 10. Power active snapshot
    $powerTxtParts = @(
        "=== timestamp_utc: $timestampUtc ===",
        "--- powercfg /getactivescheme ---",
        ((& powercfg /getactivescheme 2>&1) -join "`n"),
        "",
        "--- powercfg /list ---",
        ((& powercfg /list 2>&1) -join "`n"),
        "",
        "--- powercfg /availablesleepstates ---",
        ((& powercfg /availablesleepstates 2>&1) -join "`n"),
        "",
        "--- powercfg /q SCHEME_CURRENT ---",
        ((& powercfg /q SCHEME_CURRENT 2>&1) -join "`n")
    )
    Write-Utf8NoBom -Path (Join-Path $powerDir 'power_active_before.txt') -Content ($powerTxtParts -join "`r`n")
    Write-Host "[OK] Captured active power state -> power_active_before.txt"

    # 11. Network snapshots: NIC advanced CSV
    $nicAdvRows = @(Get-NetAdapterAdvancedProperty -IncludeHidden | ForEach-Object {
        [PSCustomObject]@{
            Name                 = $_.Name
            InterfaceDescription = $_.InterfaceDescription
            DisplayName          = $_.DisplayName
            DisplayValue         = $_.DisplayValue
            RegistryKeyword      = $_.RegistryKeyword
            RegistryValue        = if ($null -ne $_.RegistryValue) { (@($_.RegistryValue) -join ',') } else { '' }
            ValidDisplayValues   = if ($null -ne $_.ValidDisplayValues) { (@($_.ValidDisplayValues) -join ';') } else { '' }
        }
    })
    $csvLines = $nicAdvRows | ConvertTo-Csv -NoTypeInformation
    Write-Utf8NoBom -Path (Join-Path $networkDir 'nic_advanced_before.csv') -Content ($csvLines -join "`r`n")
    Write-Host "[OK] Captured $($nicAdvRows.Count) NIC advanced rows -> nic_advanced_before.csv"

    # 12. Network DNS snapshot (with explicit IsStatic & NameServer from Tcpip Interfaces registry)
    $dnsEntries = @(Get-NetAdapter | ForEach-Object {
        $nic = $_
        $guidB = ([guid]::Parse([string]$nic.InterfaceGuid)).ToString('B')
        $tcpIfacePath = "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$guidB"
        $tcpReg = Get-ItemProperty -Path $tcpIfacePath -ErrorAction SilentlyContinue
        $regNameServer = if ($tcpReg -and $null -ne $tcpReg.PSObject.Properties['NameServer']) { [string]$tcpReg.NameServer } else { '' }
        $regDhcpNameServer = if ($tcpReg -and $null -ne $tcpReg.PSObject.Properties['DhcpNameServer']) { [string]$tcpReg.DhcpNameServer } else { '' }
        $enableDhcp = if ($tcpReg -and $null -ne $tcpReg.PSObject.Properties['EnableDHCP']) { [int]$tcpReg.EnableDHCP } else { $null }
        $dnsClient = Get-DnsClientServerAddress -InterfaceIndex $nic.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
        $serverAddrs = if ($dnsClient -and $dnsClient.ServerAddresses) { @($dnsClient.ServerAddresses) } else { @() }

        [PSCustomObject]@{
            InterfaceName   = $nic.Name
            InterfaceIndex  = [int]$nic.InterfaceIndex
            InterfaceGuid   = $guidB
            Status          = $nic.Status.ToString()
            EnableDHCP      = $enableDhcp
            IsStatic        = (-not [string]::IsNullOrWhiteSpace($regNameServer))
            NameServer      = $regNameServer
            DhcpNameServer  = $regDhcpNameServer
            ServerAddresses = $serverAddrs
        }
    })
    $dnsPayload = [PSCustomObject]@{
        timestamp_utc = $timestampUtc
        interfaces    = $dnsEntries
    }
    Write-Utf8NoBom -Path (Join-Path $networkDir 'dns_before.json') -Content ($dnsPayload | ConvertTo-Json -Depth 5)
    Write-Host "[OK] Captured DNS baseline -> dns_before.json"

    # 13. TCP global snapshot
    $tcpTxtParts = @(
        "=== timestamp_utc: $timestampUtc ===",
        "--- netsh int tcp show global ---",
        ((& netsh int tcp show global 2>&1) -join "`n"),
        "",
        "--- netsh int tcp show supplemental ---",
        ((& netsh int tcp show supplemental 2>&1) -join "`n")
    )
    Write-Utf8NoBom -Path (Join-Path $networkDir 'tcp_global_before.txt') -Content ($tcpTxtParts -join "`r`n")
    Write-Host "[OK] Captured TCP global settings -> tcp_global_before.txt"

    # 14. WLAN profiles snapshot (STRICTLY OMIT key=clear)
    $wlanInterfacesOut = (& netsh wlan show interfaces 2>&1) -join "`n"
    $wlanAutoconfigOut = (& netsh wlan show autoconfig 2>&1) -join "`n"
    $wlanProfilesListOut = (& netsh wlan show profiles 2>&1) -join "`n"
    $wlanParts = [System.Collections.Generic.List[string]]::new()
    $wlanParts.Add("=== timestamp_utc: $timestampUtc ===")
    $wlanParts.Add("--- netsh wlan show interfaces ---")
    $wlanParts.Add($wlanInterfacesOut)
    $wlanParts.Add("")
    $wlanParts.Add("--- netsh wlan show autoconfig ---")
    $wlanParts.Add($wlanAutoconfigOut)
    $wlanParts.Add("")
    $wlanParts.Add("--- netsh wlan show profiles ---")
    $wlanParts.Add($wlanProfilesListOut)

    $profileNames = [System.Collections.Generic.List[string]]::new()
    foreach ($line in ($wlanProfilesListOut -split "`r?`n")) {
        if ($line -match '^\s*(?:All User Profile|User Profile|Perfil de todos los usuarios|Perfil de usuario)\s*:\s*(.+?)\s*$') {
            $pName = $Matches[1].Trim()
            if (-not [string]::IsNullOrWhiteSpace($pName) -and -not $profileNames.Contains($pName)) {
                $profileNames.Add($pName)
            }
        }
    }
    foreach ($pName in $profileNames) {
        $wlanParts.Add("")
        $wlanParts.Add("--- netsh wlan show profile name=`"$pName`" ---")
        # NEVER pass key=clear here so Wi-Fi passphrases are never written to disk
        $profDetail = (& netsh wlan show profile name="$pName" 2>&1) -join "`n"
        $wlanParts.Add($profDetail)
    }
    Write-Utf8NoBom -Path (Join-Path $networkDir 'wlan_profiles_before.txt') -Content ($wlanParts -join "`r`n")
    Write-Host "[OK] Captured $($profileNames.Count) WLAN profiles (without key=clear) -> wlan_profiles_before.txt"
}

# 15. Elevated-only tasks: powercfg /export (.pow) + Checkpoint-Computer
$powerSchemeListRaw = (& powercfg /list 2>&1) -join "`n"
$schemeGuids = [System.Collections.Generic.List[string]]::new()
foreach ($m in [regex]::Matches($powerSchemeListRaw, '([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})')) {
    $g = $m.Groups[1].Value.ToLower()
    if (-not $schemeGuids.Contains($g)) {
        $schemeGuids.Add($g)
    }
}

$powerExportStatusPath = Join-Path $powerDir 'power_export_status.json'
$restoreInfoPath = Join-Path $systemDir 'restore_point_info.json'

if ($isAdmin) {
    $exportedPowFiles = [System.Collections.Generic.List[string]]::new()
    $powErrors = [System.Collections.Generic.List[string]]::new()
    foreach ($guid in $schemeGuids) {
        $powOut = Join-Path $powerDir "power_scheme_$guid.pow"
        $res = & powercfg /export $powOut $guid 2>&1
        if ($LASTEXITCODE -eq 0) {
            $exportedPowFiles.Add($powOut)
            Write-Host "[OK] Exported power scheme $guid -> power_scheme_$guid.pow"
        }
        else {
            $errMsg = "powercfg /export failed ($LASTEXITCODE) for ${guid}: $($res -join ' ')"
            $powErrors.Add($errMsg)
            $exportErrors.Add($errMsg)
            Write-Warning $errMsg
        }
    }
    $powStatusObj = [PSCustomObject]@{
        timestamp_utc = $timestampUtc
        is_admin      = $true
        status        = if ($powErrors.Count -eq 0) { 'exported' } else { 'partial_or_failed' }
        scheme_guids  = @($schemeGuids)
        exported_pow  = @($exportedPowFiles)
        errors        = @($powErrors)
    }
    Write-Utf8NoBom -Path $powerExportStatusPath -Content ($powStatusObj | ConvertTo-Json -Depth 4)

    $existingRestorePoints = @()
    $checkpointStatus = 'unknown'
    $checkpointError = $null
    try {
        $existingRestorePoints = @(Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Select-Object -Last 5 SequenceNumber, Description, CreationTime, RestorePointType)
        Checkpoint-Computer -Description 'laptop_optimization_baseline' -RestorePointType 'MODIFY_SETTINGS' -ErrorAction Stop
        $checkpointStatus = 'created'
        Write-Host "[OK] Created System Restore point 'laptop_optimization_baseline'"
    }
    catch {
        $checkpointStatus = 'failed'
        $checkpointError = $_.Exception.Message
        Write-Warning "System Restore point creation failed: $checkpointError"
    }
    $restorePayload = [PSCustomObject]@{
        timestamp_utc           = $timestampUtc
        is_admin                = $true
        checkpoint_status       = $checkpointStatus
        error                   = $checkpointError
        existing_restore_points = $existingRestorePoints
    }
    Write-Utf8NoBom -Path $restoreInfoPath -Content ($restorePayload | ConvertTo-Json -Depth 5)
}
else {
    $powStatusObj = [PSCustomObject]@{
        timestamp_utc = $timestampUtc
        is_admin      = $false
        status        = 'skipped_non_elevated'
        fallback_reg  = (Join-Path $systemDir 'hklm_powerschemes_fallback.reg')
        scheme_guids  = @($schemeGuids)
        note          = 'powercfg /export requires Administrator privileges (0x522 non-elevated). Re-run with -IncludeElevated as Administrator before applying changes.'
    }
    Write-Utf8NoBom -Path $powerExportStatusPath -Content ($powStatusObj | ConvertTo-Json -Depth 4)

    $restorePayload = [PSCustomObject]@{
        timestamp_utc           = $timestampUtc
        is_admin                = $false
        checkpoint_status       = 'skipped_non_elevated'
        error                   = 'Access denied without Administrator privileges'
        note                    = 'Run backup_baseline.ps1 -IncludeElevated as Administrator before M2/M3 changes to create a System Restore point and .pow exports.'
    }
    Write-Utf8NoBom -Path $restoreInfoPath -Content ($restorePayload | ConvertTo-Json -Depth 4)
    Write-Host "[SKIP] Elevated .pow export and System Restore point skipped (non-elevated; logged cleanly)."
}

if ($exportErrors.Count -gt 0) {
    Write-Error "Backup completed with $($exportErrors.Count) export error(s)."
    exit 1
}

Write-Host "[DONE] Baseline backup completed cleanly under '$BackupRoot'."
exit 0
