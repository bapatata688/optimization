<#
.SYNOPSIS
    Applies elevated Milestone M2 OS, power, GPU, service, scheduled task, and VBS/HVCI optimizations (Features F7-F10).
.DESCRIPTION
    Idempotent elevated optimization script for laptop_optimization:
      - Elevation Gate: Exits cleanly with code 2 when non-elevated unless -DryRun / -WhatIf is passed.
      - Pre-Mutation M1 Backup Gate: Runs backup_baseline.ps1 -IncludeElevated (never -Force) if .pow exports are absent.
      - F7: Preserves 'Gaming Estable (Sin Bateria)' (46476e86-f770-4378-857e-3a25bf0336b4) and MMCSS without overwriting,
            verifies anti-Kernel-Power-41 CPU guardrails (PROCTHROTTLEMAX=99%, PERFBOOSTMODE=0), disables hibernation
            & Fast Startup, enables PowerThrottlingOff=1, and sets Win32PrioritySeparation=0x26.
      - F8: Sets AllowGameDVR=0 policies, AMD iGPU EnableUlps=0, verifies GTX 1650 NVFBCEnable=1, configures
            NVIDIA Global Base Profile via native nvapi64.dll DRS, and logs Lenovo SmartFanMode read-only.
      - F9: Disables bloat/crashing services (keeping NetworkDaemon Disabled, never deleted), sets selected services to
            Manual, disables target telemetry/updater scheduled tasks, and protects critical services & NVIDIA Shader Compiler.
      - F10: Invokes toggle_vbs_hypervisor.ps1 -Mode gaming (unless -SkipVbs) and logs reboot/WSL2 notice.
    Supports -DryRun / -WhatIf, never triggers UAC dialogs, and appends UTC-timestamped logs to reports/apply_os_gpu.log.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$BackupDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups'),
    [string]$ReportDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'reports'),
    [switch]$DryRun,
    [switch]$SkipVbs,
    [switch]$KeepHypervisor
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$script:isWhatIf = [bool]$WhatIfPreference
$isDryRun = [bool]($DryRun -or $WhatIfPreference)
$ReportDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportDir)
$logPath = Join-Path $ReportDir 'apply_os_gpu.log'
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

function Set-HKLMValueIdempotent {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$Value,
        [Parameter(Mandatory)][ValidateSet('DWord', 'String')][string]$Type
    )

    $info = Get-HKLMValueInfo -Path $Path -Name $Name
    $expectedKind = if ($Type -eq 'DWord') { [Microsoft.Win32.RegistryValueKind]::DWord } else { [Microsoft.Win32.RegistryValueKind]::String }

    $tgtI32 = $null
    if ($Type -eq 'DWord') {
        $tgtI32 = if ($Value -is [int]) {
            [int]$Value
        }
        elseif ($Value -ge 0 -and $Value -le [uint32]::MaxValue) {
            [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$Value), 0)
        }
        else {
            [int]$Value
        }
    }

    $alreadyMatch = $false
    if ($info.ValueExists -and $info.Kind -eq $expectedKind) {
        if ($Type -eq 'DWord') {
            $curI32 = if ($info.Value -is [int]) { [int]$info.Value } else { [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]$info.Value), 0) }
            $curU32 = [BitConverter]::ToUInt32([BitConverter]::GetBytes($curI32), 0)
            $tgtU32 = [BitConverter]::ToUInt32([BitConverter]::GetBytes($tgtI32), 0)
            $alreadyMatch = ($curU32 -eq $tgtU32)
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
            $typedVal = if ($Type -eq 'DWord') { $tgtI32 } else { [string]$Value }
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

$isAdmin = Test-IsAdmin
Write-ApplyLog -Level 'INFO' -Message "Starting apply_os_gpu_admin.ps1 (IsAdmin=$isAdmin, DryRun=$isDryRun, SkipVbs=$SkipVbs, KeepHypervisor=$KeepHypervisor, BackupDir='$BackupDir')"

if (-not $isAdmin -and -not $isDryRun) {
    Write-ApplyLog -Level 'ERROR' -Message 'Administrator privileges required for apply_os_gpu_admin.ps1. Re-run in an elevated PowerShell session, or pass -DryRun / -WhatIf for read-only inspection.'
    exit 2
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
            $restoreInfoPath = Join-Path (Join-Path $BackupDir 'system') 'restore_point_info.json'
            if (Test-Path -LiteralPath $restoreInfoPath) {
                $restoreInfo = Get-Content -LiteralPath $restoreInfoPath -Raw -Encoding utf8 | ConvertFrom-Json
                $cpStatus = if ($null -ne $restoreInfo -and $null -ne $restoreInfo.PSObject.Properties['checkpoint_status']) { [string]$restoreInfo.checkpoint_status } else { 'unknown' }
                $cpErr = if ($null -ne $restoreInfo -and $null -ne $restoreInfo.PSObject.Properties['error']) { [string]$restoreInfo.error } else { '' }
                if ($cpStatus -ne 'created') {
                    Write-ApplyLog -Level 'WARN' -Message "System Restore point status='$cpStatus' ($cpErr); continuing because .pow exports succeeded ($($powAfter.Count) files)."
                }
                else {
                    Write-ApplyLog -Level 'APPLIED' -Message "Pre-mutation elevated backup complete ($($powAfter.Count) .pow files, System Restore point created)."
                }
            }
        }
    }
    else {
        Write-ApplyLog -Level 'SKIP' -Message "Pre-mutation elevated backup already present ($($powFiles.Count) .pow files in '$powerBackupDir')."
    }

    # =========================================================================
    # F7: Anti-Kernel-Power-41 Power & OS Tuning
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F7: Anti-Kernel-Power-41 Power & OS Tuning ---'

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
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would switch active power scheme from '$activeGuid' to 'Gaming Estable (Sin Bateria)' ($stableSchemeGuid)."
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
        Write-ApplyLog -Level 'WARN' -Message "Target scheme '$stableSchemeGuid' not found in powercfg /list; active scheme remains '$activeGuid' (will NOT switch to Ultimate Performance)."
    }

    if ($forbiddenSchemes -contains $activeGuid -and -not $stableExists) {
        Write-ApplyLog -Level 'WARN' -Message "Active scheme '$activeGuid' is an Ultimate Performance scheme and '$stableSchemeGuid' is missing."
    }

    $inspectScheme = if ($stableExists) { $stableSchemeGuid } else { 'SCHEME_CURRENT' }

    # Read-only verification of anti-crash CPU & Disk settings (never raise PROCTHROTTLEMAX > 99 or PERFBOOSTMODE > 0)
    $subProc = '54533251-82be-4824-96c1-47b60b740d00'
    $procMin = Get-PowerSettingIndices -SchemeGuid $inspectScheme -SubgroupGuid $subProc -SettingGuid '893dee8e-2bef-41e0-89c6-b55d0929964c'
    $procMax = Get-PowerSettingIndices -SchemeGuid $inspectScheme -SubgroupGuid $subProc -SettingGuid 'bc5038f7-23e0-4960-96da-33abaf5935ec'
    $perfBoost = Get-PowerSettingIndices -SchemeGuid $inspectScheme -SubgroupGuid $subProc -SettingGuid 'be337238-0d82-4146-a960-4f3749d470c7'
    $diskIdle = Get-PowerSettingIndices -SchemeGuid $inspectScheme -SubgroupGuid '0012ee47-9041-4b5d-9b77-535fba8b1442' -SettingGuid '6738e2c4-e8a5-4a42-b16a-e040e769756e'

    Write-ApplyLog -Level 'INFO' -Message "Power guardrails ($inspectScheme): PROCTHROTTLEMIN AC=$($procMin.AC)% (target 5%), PROCTHROTTLEMAX AC=$($procMax.AC)% (target 99%, never >99%), PERFBOOSTMODE AC=$($perfBoost.AC) (target 0, never >0), DISKIDLE AC=$($diskIdle.AC)s (target 0)."

    # Enforce SYSCOOLPOL AC=1, USB selective suspend AC=0, PCIe ASPM AC=0 ONLY if drifted (leave DC to M3)
    $enforcedAcSettings = @(
        @{ Name = 'SYSCOOLPOL'; Sub = $subProc; Set = '94d3a615-a899-4ac5-ae2b-e4d8f634367f'; Target = 1 },
        @{ Name = 'USB selective suspend'; Sub = '2a737441-1930-4402-8d77-b2bebba308a3'; Set = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'; Target = 0 },
        @{ Name = 'PCIe ASPM'; Sub = '501a4d13-42af-4429-9fd1-a8218c268e20'; Set = 'ee12f906-d277-404b-b6da-e5fa1a576df5'; Target = 0 }
    )

    $powerSchemeTouched = $false
    foreach ($ps in $enforcedAcSettings) {
        $idx = Get-PowerSettingIndices -SchemeGuid $inspectScheme -SubgroupGuid $ps.Sub -SettingGuid $ps.Set
        if (-not $idx.Found) {
            Write-ApplyLog -Level 'WARN' -Message "Could not parse power setting '$($ps.Name)' ($($ps.Set))."
            continue
        }
        if ($null -eq $idx.AC) {
            Write-ApplyLog -Level 'WARN' -Message "Could not parse AC index for power setting '$($ps.Name)' ($($ps.Set))."
            continue
        }
        if ([int]$idx.AC -eq [int]$ps.Target) {
            Write-ApplyLog -Level 'SKIP' -Message "Power setting '$($ps.Name)' AC already $($ps.Target) (DC=$($idx.DC) left for M3)."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would set '$($ps.Name)' AC from $($idx.AC) to $($ps.Target) on scheme $inspectScheme."
        }
        elseif ($PSCmdlet.ShouldProcess("$inspectScheme\$($ps.Name)", "Set AC index to $($ps.Target)")) {
            $out = (& powercfg /setacvalueindex $inspectScheme $ps.Sub $ps.Set $ps.Target 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0) {
                throw "powercfg /setacvalueindex failed for $($ps.Name): $out"
            }
            $powerSchemeTouched = $true
            Write-ApplyLog -Level 'APPLIED' -Message "Set '$($ps.Name)' AC from $($idx.AC) to $($ps.Target) on scheme $inspectScheme."
        }
    }
    if ($powerSchemeTouched -and -not $isDryRun -and $stableExists) {
        $setActOut = (& powercfg /setactive $inspectScheme 2>&1) -join ' '
        if ($LASTEXITCODE -ne 0) {
            throw "powercfg /setactive $inspectScheme failed ($LASTEXITCODE): $setActOut"
        }
    }

    # Assert/preserve MMCSS without overwriting
    $mmcssPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile'
    $netThrottle = Get-HKLMValueInfo -Path $mmcssPath -Name 'NetworkThrottlingIndex'
    $sysResp = Get-HKLMValueInfo -Path $mmcssPath -Name 'SystemResponsiveness'
    $netHex = if ($netThrottle.ValueExists) { '0x{0:X8}' -f ([BitConverter]::ToUInt32([BitConverter]::GetBytes([int]$netThrottle.Value), 0)) } else { '<missing>' }
    Write-ApplyLog -Level 'INFO' -Message "Preserved MMCSS untouched: NetworkThrottlingIndex=$netHex, SystemResponsiveness=$($sysResp.Value)."

    # Disable hibernation & Fast Startup
    $hiberfilExists = Test-Path -LiteralPath 'C:\hiberfil.sys'
    $hiberReg = Get-HKLMValueInfo -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -Name 'HibernateEnabled'
    $powerCfgA = (& powercfg /a 2>&1) -join "`n"
    $hiberEnabled = $hiberfilExists -or ($hiberReg.ValueExists -and [int]$hiberReg.Value -ne 0) -or ($powerCfgA -match '(?m)^\s*Hibernate\s*$')

    if (-not $hiberEnabled) {
        Write-ApplyLog -Level 'SKIP' -Message 'Hibernation already disabled (C:\hiberfil.sys absent and HibernateEnabled=0).'
    }
    elseif ($isDryRun) {
        $sizeInfo = if ($hiberfilExists) { "$([math]::Round((Get-Item -LiteralPath 'C:\hiberfil.sys' -Force).Length / 1GB, 2)) GB" } else { 'n/a' }
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would run 'powercfg /h off' (hiberfil.sys exists=$hiberfilExists, size=$sizeInfo, HibernateEnabled=$($hiberReg.Value))."
    }
    elseif ($PSCmdlet.ShouldProcess('Hibernation', 'powercfg /h off')) {
        $out = (& powercfg /h off 2>&1) -join ' '
        if ($LASTEXITCODE -ne 0) {
            throw "powercfg /h off failed ($LASTEXITCODE): $out"
        }
        Write-ApplyLog -Level 'APPLIED' -Message 'Disabled hibernation via powercfg /h off.'
    }

    Set-HKLMValueIdempotent -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name 'HiberbootEnabled' -Value 0 -Type 'DWord'
    Set-HKLMValueIdempotent -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling' -Name 'PowerThrottlingOff' -Value 1 -Type 'DWord'
    Set-HKLMValueIdempotent -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\PriorityControl' -Name 'Win32PrioritySeparation' -Value 0x26 -Type 'DWord'

    # =========================================================================
    # F8: Stability-Safe GPU & Driver Tuning
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F8: Stability-Safe GPU & Driver Tuning ---'

    Set-HKLMValueIdempotent -Path 'HKLM:\SOFTWARE\Microsoft\PolicyManager\default\ApplicationManagement\AllowGameDVR' -Name 'value' -Value 0 -Type 'DWord'
    Set-HKLMValueIdempotent -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Name 'AllowGameDVR' -Value 0 -Type 'DWord'

    $videoClassRoot = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    $videoSubKeys = @(Get-ChildItem -LiteralPath $videoClassRoot -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^000\d$' })

    $amdKey = $null
    $nvKey = $null
    foreach ($vk in $videoSubKeys) {
        $descInfo = Get-HKLMValueInfo -Path $vk.PSPath.Replace('Microsoft.PowerShell.Core\Registry::HKEY_LOCAL_MACHINE\', 'HKLM:\') -Name 'DriverDesc'
        if ($descInfo.ValueExists) {
            $desc = [string]$descInfo.Value
            if ($desc -like '*AMD*Radeon*' -and $null -eq $amdKey) {
                $amdKey = $vk.PSPath.Replace('Microsoft.PowerShell.Core\Registry::HKEY_LOCAL_MACHINE\', 'HKLM:\')
            }
            if ($desc -like '*GTX 1650*' -and $null -eq $nvKey) {
                $nvKey = $vk.PSPath.Replace('Microsoft.PowerShell.Core\Registry::HKEY_LOCAL_MACHINE\', 'HKLM:\')
            }
        }
    }

    if ($null -ne $amdKey) {
        Write-ApplyLog -Level 'INFO' -Message "Verified AMD Radeon iGPU class key: $amdKey"
        Set-HKLMValueIdempotent -Path $amdKey -Name 'EnableUlps' -Value 0 -Type 'DWord'
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message 'No AMD Radeon display class subkey (000*) found for EnableUlps.'
    }

    if ($null -ne $nvKey) {
        Write-ApplyLog -Level 'INFO' -Message "Verified NVIDIA GTX 1650 class key: $nvKey (no PowerMizer*/OC/UV/TdrDelay hacks applied)."
        Set-HKLMValueIdempotent -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\nvlddmkm\Parameters' -Name 'NVFBCEnable' -Value 1 -Type 'DWord'
    }
    else {
        Write-ApplyLog -Level 'WARN' -Message 'No NVIDIA GTX 1650 display class subkey (000*) found.'
    }

    # Native NVAPI DRS via nvapi64.dll (inline C# P/Invoke, 12320-byte NVDRS_SETTING layout)
    if (-not ('NvDrsManager' -as [type])) {
        $nvDrsCs = @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class NvDrsSettingInfo {
    public uint SettingId { get; set; }
    public int ReturnCode { get; set; }
    public uint SettingType { get; set; }
    public uint Location { get; set; }
    public uint IsPredefined { get; set; }
    public uint U32Value { get; set; }
}

public static class NvDrsManager {
    private const int NVDRS_SETTING_SIZE = 12320;
    private const uint NVDRS_SETTING_VER = (1u << 16) | (uint)NVDRS_SETTING_SIZE;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr LoadLibrary(string lpFileName);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr GetProcAddress(IntPtr hModule, string procName);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate IntPtr QueryInterfaceDelegate(uint id);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int NvInitDelegate();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int NvUnloadDelegate();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsCreateSessionDelegate(out IntPtr hSession);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsDestroySessionDelegate(IntPtr hSession);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsLoadSettingsDelegate(IntPtr hSession);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsSaveSettingsDelegate(IntPtr hSession);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsGetBaseProfileDelegate(IntPtr hSession, out IntPtr hProfile);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsGetSettingDelegate(IntPtr hSession, IntPtr hProfile, uint settingId, IntPtr pSetting);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int DrsSetSettingDelegate(IntPtr hSession, IntPtr hProfile, IntPtr pSetting);

    private static T GetApi<T>(QueryInterfaceDelegate qi, uint id) where T : Delegate {
        IntPtr p = qi(id);
        if (p == IntPtr.Zero) throw new InvalidOperationException(string.Format("nvapi_QueryInterface(0x{0:X8}) returned NULL", id));
        return Marshal.GetDelegateForFunctionPointer<T>(p);
    }

    public static NvDrsSettingInfo[] QueryBaseProfileSettings(uint[] settingIds) {
        IntPtr mod = LoadLibrary("nvapi64.dll");
        if (mod == IntPtr.Zero) throw new DllNotFoundException("Failed to load nvapi64.dll");
        IntPtr qiPtr = GetProcAddress(mod, "nvapi_QueryInterface");
        if (qiPtr == IntPtr.Zero) throw new EntryPointNotFoundException("nvapi_QueryInterface not found");
        var qi = Marshal.GetDelegateForFunctionPointer<QueryInterfaceDelegate>(qiPtr);

        var init = GetApi<NvInitDelegate>(qi, 0x0150E828);
        var unload = GetApi<NvUnloadDelegate>(qi, 0xD22BDD7E);
        var createSession = GetApi<DrsCreateSessionDelegate>(qi, 0x0694D52E);
        var destroySession = GetApi<DrsDestroySessionDelegate>(qi, 0xDAD9CFF8);
        var loadSettings = GetApi<DrsLoadSettingsDelegate>(qi, 0x375DBD6B);
        var getBaseProfile = GetApi<DrsGetBaseProfileDelegate>(qi, 0xDA8466A0);
        var getSetting = GetApi<DrsGetSettingDelegate>(qi, 0x73BF8338);

        int rc = init();
        if (rc != 0) throw new InvalidOperationException("NvAPI_Initialize failed: " + rc);

        IntPtr hSession = IntPtr.Zero;
        IntPtr buf = IntPtr.Zero;
        var results = new List<NvDrsSettingInfo>();
        try {
            rc = createSession(out hSession);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_CreateSession failed: " + rc);
            rc = loadSettings(hSession);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_LoadSettings failed: " + rc);
            IntPtr hProfile;
            rc = getBaseProfile(hSession, out hProfile);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_GetBaseProfile failed: " + rc);

            buf = Marshal.AllocHGlobal(NVDRS_SETTING_SIZE);
            byte[] zeros = new byte[NVDRS_SETTING_SIZE];
            foreach (uint id in settingIds) {
                Marshal.Copy(zeros, 0, buf, NVDRS_SETTING_SIZE);
                Marshal.WriteInt32(buf, 0, unchecked((int)NVDRS_SETTING_VER));
                int grc = getSetting(hSession, hProfile, id, buf);
                results.Add(new NvDrsSettingInfo {
                    SettingId = id,
                    ReturnCode = grc,
                    SettingType = (uint)Marshal.ReadInt32(buf, 4104),
                    Location = (uint)Marshal.ReadInt32(buf, 4108),
                    IsPredefined = (uint)Marshal.ReadInt32(buf, 4112),
                    U32Value = (uint)Marshal.ReadInt32(buf, 8220)
                });
            }
            return results.ToArray();
        } finally {
            if (buf != IntPtr.Zero) Marshal.FreeHGlobal(buf);
            if (hSession != IntPtr.Zero) destroySession(hSession);
            unload();
        }
    }

    public static void ApplyBaseProfileDwordSettings(uint[] settingIds, uint[] values) {
        if (settingIds.Length != values.Length) throw new ArgumentException("Length mismatch");
        if (settingIds.Length == 0) return;

        IntPtr mod = LoadLibrary("nvapi64.dll");
        if (mod == IntPtr.Zero) throw new DllNotFoundException("Failed to load nvapi64.dll");
        IntPtr qiPtr = GetProcAddress(mod, "nvapi_QueryInterface");
        if (qiPtr == IntPtr.Zero) throw new EntryPointNotFoundException("nvapi_QueryInterface not found");
        var qi = Marshal.GetDelegateForFunctionPointer<QueryInterfaceDelegate>(qiPtr);

        var init = GetApi<NvInitDelegate>(qi, 0x0150E828);
        var unload = GetApi<NvUnloadDelegate>(qi, 0xD22BDD7E);
        var createSession = GetApi<DrsCreateSessionDelegate>(qi, 0x0694D52E);
        var destroySession = GetApi<DrsDestroySessionDelegate>(qi, 0xDAD9CFF8);
        var loadSettings = GetApi<DrsLoadSettingsDelegate>(qi, 0x375DBD6B);
        var saveSettings = GetApi<DrsSaveSettingsDelegate>(qi, 0xFCBC7E14);
        var getBaseProfile = GetApi<DrsGetBaseProfileDelegate>(qi, 0xDA8466A0);
        var setSetting = GetApi<DrsSetSettingDelegate>(qi, 0x577DD202);

        int rc = init();
        if (rc != 0) throw new InvalidOperationException("NvAPI_Initialize failed: " + rc);

        IntPtr hSession = IntPtr.Zero;
        IntPtr buf = IntPtr.Zero;
        try {
            rc = createSession(out hSession);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_CreateSession failed: " + rc);
            rc = loadSettings(hSession);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_LoadSettings failed: " + rc);
            IntPtr hProfile;
            rc = getBaseProfile(hSession, out hProfile);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_GetBaseProfile failed: " + rc);

            buf = Marshal.AllocHGlobal(NVDRS_SETTING_SIZE);
            byte[] zeros = new byte[NVDRS_SETTING_SIZE];
            for (int i = 0; i < settingIds.Length; i++) {
                Marshal.Copy(zeros, 0, buf, NVDRS_SETTING_SIZE);
                Marshal.WriteInt32(buf, 0, unchecked((int)NVDRS_SETTING_VER));
                Marshal.WriteInt32(buf, 4100, unchecked((int)settingIds[i]));
                Marshal.WriteInt32(buf, 4104, 0); // NVDRS_DWORD_TYPE
                Marshal.WriteInt32(buf, 4108, 0); // NVDRS_CURRENT_PROFILE_LOCATION
                Marshal.WriteInt32(buf, 8220, unchecked((int)values[i]));
                rc = setSetting(hSession, hProfile, buf);
                if (rc != 0) {
                    throw new InvalidOperationException(string.Format("NvAPI_DRS_SetSetting(0x{0:X8}) failed: {1}", settingIds[i], rc));
                }
            }
            rc = saveSettings(hSession);
            if (rc != 0) throw new InvalidOperationException("NvAPI_DRS_SaveSettings failed: " + rc);
        } finally {
            if (buf != IntPtr.Zero) Marshal.FreeHGlobal(buf);
            if (hSession != IntPtr.Zero) destroySession(hSession);
            unload();
        }
    }
}
"@
        Add-Type -TypeDefinition $nvDrsCs
    }

    $drsSpecs = @(
        @{ Id = [uint32]0x1057EB71; Name = 'Power management mode'; Target = [uint32]0x1; Desc = 'Prefer maximum performance'; MinPreserve = $null },
        @{ Id = [uint32]0x007BA09E; Name = 'Maximum pre-rendered frames (Low Latency)'; Target = [uint32]0x1; Desc = 'On'; MinPreserve = $null },
        @{ Id = [uint32]0x00CE2691; Name = 'Texture filtering - Quality'; Target = [uint32]0x14; Desc = 'High Performance (20)'; MinPreserve = $null },
        @{ Id = [uint32]0x20C1221E; Name = 'Threaded optimization'; Target = [uint32]0x1; Desc = 'On'; MinPreserve = $null },
        @{ Id = [uint32]0x00AC8497; Name = 'Shader disk cache maximum size'; Target = [uint32]0x2800; Desc = '10240 MB (or Unlimited preserved)'; MinPreserve = [uint32]0x2800 }
    )

    $drsIds = [uint32[]]@($drsSpecs | ForEach-Object { $_.Id })
    $drsCurrent = [NvDrsManager]::QueryBaseProfileSettings($drsIds)

    $toApplyIds = [System.Collections.Generic.List[uint32]]::new()
    $toApplyVals = [System.Collections.Generic.List[uint32]]::new()
    $toApplyLogs = [System.Collections.Generic.List[string]]::new()

    for ($i = 0; $i -lt $drsSpecs.Count; $i++) {
        $spec = $drsSpecs[$i]
        $cur = $drsCurrent[$i]
        $idHex = '0x{0:X8}' -f $spec.Id
        $valHex = '0x{0:X8}' -f $cur.U32Value
        $targetHex = '0x{0:X8}' -f $spec.Target

        $isOverridePresent = ($cur.ReturnCode -eq 0 -and $cur.Location -eq 0)
        $matchesTarget = $false
        if ($null -ne $spec.MinPreserve) {
            if ($isOverridePresent -and ($cur.U32Value -eq 0xFFFFFFFFu -or $cur.U32Value -ge [uint32]$spec.MinPreserve)) {
                $matchesTarget = $true
            }
        }
        elseif ($isOverridePresent -and $cur.U32Value -eq $spec.Target) {
            $matchesTarget = $true
        }

        if ($matchesTarget) {
            Write-ApplyLog -Level 'SKIP' -Message "NVAPI DRS $idHex ($($spec.Name)) already compliant in Base Profile (val=$valHex, loc=$($cur.Location))."
        }
        else {
            $curStateStr = "rc=$($cur.ReturnCode), loc=$($cur.Location), val=$valHex"
            $toApplyIds.Add($spec.Id)
            $toApplyVals.Add($spec.Target)
            $toApplyLogs.Add("$idHex ($($spec.Name)): $curStateStr -> $targetHex ($($spec.Desc))")
            if ($isDryRun) {
                Write-ApplyLog -Level 'DRY-RUN' -Message "Would set NVAPI DRS $idHex ($($spec.Name)) = $targetHex ($($spec.Desc)) [current: $curStateStr]."
            }
        }
    }

    if ($toApplyIds.Count -gt 0 -and -not $isDryRun) {
        if ($PSCmdlet.ShouldProcess('NVIDIA Global Base Profile (nvapi64.dll)', "Apply $($toApplyIds.Count) DRS setting(s)")) {
            [NvDrsManager]::ApplyBaseProfileDwordSettings($toApplyIds.ToArray(), $toApplyVals.ToArray())
            foreach ($entry in $toApplyLogs) {
                Write-ApplyLog -Level 'APPLIED' -Message "NVAPI DRS Base Profile updated: $entry"
            }
        }
    }

    # Lenovo SmartFanMode (read-only logging only; never mutate)
    $smartFanReg = Get-HKLMValueInfo -Path 'HKLM:\SOFTWARE\Lenovo\VantageService\AddinData\LenovoGamingAddin\Event\FanManagerPlugin\RealStatusEvent' -Name 'SmartFanMode'
    $wmiSmartFan = 'not queried (non-elevated)'
    if ($isAdmin) {
        try {
            $gz = Get-CimInstance -Namespace 'root\wmi' -ClassName 'LENOVO_GAMEZONE_DATA' -ErrorAction Stop
            if ($null -ne $gz) {
                $res = Invoke-CimMethod -InputObject $gz -MethodName 'GetSmartFanMode' -WhatIf:$false -ErrorAction Stop
                $wmiSmartFan = if ($null -ne $res) { "$($res.Data)" } else { 'null response' }
            }
            else {
                $wmiSmartFan = 'LENOVO_GAMEZONE_DATA instance not found'
            }
        }
        catch {
            $wmiSmartFan = "error ($($_.Exception.Message))"
        }
    }
    Write-ApplyLog -Level 'INFO' -Message "Lenovo SmartFanMode (read-only, untouched): Registry=$($smartFanReg.Value), WMI=$wmiSmartFan."

    # =========================================================================
    # F9: Services & Scheduled Tasks Cleanup
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F9: Services & Scheduled Tasks Cleanup ---'

    $protectedServices = @(
        'WinDefend',
        'SecurityHealthService',
        'NVDisplay.ContainerLocalSystem',
        'LenovoVantageService',
        'AudioSrv',
        'Audiosrv',
        'RtkAudioUniversalService',
        'AMD External Events Utility',
        'WSLService'
    )

    $disableServices = @(
        'PCManager Service Store',
        'MapsBroker',
        'RemoteRegistry',
        'RetailDemo',
        'Fax',
        'WalletService',
        'PhoneSvc',
        'wisvc',
        'NetworkDaemon'
    )

    $manualServices = @(
        'WSearch',
        'TrkWks',
        'lfsvc',
        'Spooler'
    )

    foreach ($svc in ($disableServices + $manualServices)) {
        if ($protectedServices -contains $svc) {
            throw "Safety guard violation: Protected service '$svc' found in modification list."
        }
    }

    function Set-ServiceTargetState {
        param(
            [Parameter(Mandatory)][string]$Name,
            [Parameter(Mandatory)][ValidateSet('Disabled', 'Manual')][string]$TargetMode,
            [Parameter(Mandatory)][string[]]$GuardList
        )

        if ($GuardList -contains $Name) {
            throw "Safety guard violation: Attempted to modify protected service '$Name'."
        }

        $svcObj = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if ($null -eq $svcObj) {
            Write-ApplyLog -Level 'SKIP' -Message "Service '$Name' is absent in SCM; skipping cleanly."
            return
        }

        $targetStartInt = if ($TargetMode -eq 'Disabled') { 4 } else { 3 }
        $scMode = if ($TargetMode -eq 'Disabled') { 'disabled' } else { 'demand' }
        $svcRegInfo = Get-HKLMValueInfo -Path "HKLM:\SYSTEM\CurrentControlSet\Services\$Name" -Name 'Start'
        $curStartInt = if ($svcRegInfo.ValueExists) { [int]$svcRegInfo.Value } else { -1 }
        $isRunning = ($svcObj.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Running)

        if (-not $isRunning -and $curStartInt -eq $targetStartInt) {
            Write-ApplyLog -Level 'SKIP' -Message "Service '$Name' already Stopped and $TargetMode (Start=$curStartInt)."
            return
        }

        if ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would configure service '$Name' (Status=$($svcObj.Status), Start=$curStartInt) -> Stopped + $TargetMode (Start=$targetStartInt)."
            return
        }

        if ($PSCmdlet.ShouldProcess("Service '$Name'", "Stop and set $TargetMode")) {
            if ($isRunning) {
                try {
                    Stop-Service -Name $Name -Force -ErrorAction Stop
                    Write-ApplyLog -Level 'APPLIED' -Message "Stopped service '$Name'."
                }
                catch {
                    Write-ApplyLog -Level 'WARN' -Message "Could not stop service '$Name': $($_.Exception.Message)"
                }
            }
            if ($curStartInt -ne $targetStartInt) {
                $scOut = (& sc.exe config $Name start= $scMode 2>&1) -join ' '
                if ($LASTEXITCODE -ne 0) {
                    Write-ApplyLog -Level 'ERROR' -Message "sc.exe config '$Name' start= $scMode failed ($LASTEXITCODE): $scOut"
                }
                else {
                    Write-ApplyLog -Level 'APPLIED' -Message "Configured service '$Name' -> $TargetMode (Start=$targetStartInt, previous=$curStartInt)."
                }
            }
        }
    }

    foreach ($svc in $disableServices) {
        Set-ServiceTargetState -Name $svc -TargetMode 'Disabled' -GuardList $protectedServices
    }
    foreach ($svc in $manualServices) {
        Set-ServiceTargetState -Name $svc -TargetMode 'Manual' -GuardList $protectedServices
    }

    # Verify protected services remain untouched
    $protStatus = @()
    foreach ($psvc in @('WinDefend', 'SecurityHealthService', 'NVDisplay.ContainerLocalSystem', 'LenovoVantageService', 'Audiosrv', 'RtkAudioUniversalService', 'AMD External Events Utility', 'WSLService')) {
        $s = Get-Service -Name $psvc -ErrorAction SilentlyContinue
        if ($null -ne $s) {
            $protStatus += "$psvc=$($s.Status)/$($s.StartType)"
        }
    }
    Write-ApplyLog -Level 'INFO' -Message "Protected services untouched: $($protStatus -join '; ')."

    # Scheduled Tasks cleanup (strictly protect 'NVIDIA Shader Compiler')
    $allTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue)
    $matchedTasks = [System.Collections.Generic.List[object]]::new()
    foreach ($t in $allTasks) {
        if ($t.TaskName -eq 'NVIDIA Shader Compiler') {
            continue
        }
        $isMatch = $false
        if ($t.TaskPath -eq '\Microsoft\Windows\Application Experience\' -and $t.TaskName -in @('Microsoft Compatibility Appraiser Exp', 'MareBackup')) {
            $isMatch = $true
        }
        elseif ($t.TaskPath -eq '\' -and (
            $t.TaskName -in @('StartCN', 'StartDVR', 'AMDRyzenMasterSDKTask') -or
            $t.TaskName -like 'NvDriverUpdateCheckDaily_*' -or
            $t.TaskName -like 'NVIDIA App SelfUpdate_*' -or
            $t.TaskName -like 'OneDrive*'
        )) {
            $isMatch = $true
        }
        elseif ($t.TaskPath -like '\SoftLanding\*' -and $t.TaskName -eq 'SoftLandingCreativeManagementTask') {
            $isMatch = $true
        }

        if ($isMatch) {
            $matchedTasks.Add($t)
        }
    }

    foreach ($t in $matchedTasks) {
        if ($t.TaskName -eq 'NVIDIA Shader Compiler') {
            throw "Safety guard violation: Attempted to disable protected task 'NVIDIA Shader Compiler'."
        }
        $fullName = "$($t.TaskPath)$($t.TaskName)"
        if ($t.State -eq 'Disabled') {
            Write-ApplyLog -Level 'SKIP' -Message "Scheduled task '$fullName' already Disabled."
            continue
        }
        if ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would stop (if Running, current=$($t.State)) and disable scheduled task '$fullName'."
            continue
        }
        if ($PSCmdlet.ShouldProcess($fullName, 'Disable scheduled task')) {
            if ($t.State -eq 'Running') {
                try {
                    Stop-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName -ErrorAction Stop
                    Write-ApplyLog -Level 'APPLIED' -Message "Stopped running scheduled task '$fullName'."
                }
                catch {
                    Write-ApplyLog -Level 'WARN' -Message "Could not stop scheduled task '$fullName': $($_.Exception.Message)"
                }
            }
            Disable-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName -ErrorAction Stop | Out-Null
            Write-ApplyLog -Level 'APPLIED' -Message "Disabled scheduled task '$fullName' (previous state=$($t.State))."
        }
    }

    $nscTask = $allTasks | Where-Object { $_.TaskName -eq 'NVIDIA Shader Compiler' } | Select-Object -First 1
    if ($null -ne $nscTask) {
        Write-ApplyLog -Level 'INFO' -Message "Protected task '$($nscTask.TaskPath)$($nscTask.TaskName)' untouched (State=$($nscTask.State))."
    }

    # =========================================================================
    # F10: VBS / HVCI Gaming Optimization
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F10: VBS / HVCI Gaming Optimization ---'

    if ($SkipVbs) {
        Write-ApplyLog -Level 'SKIP' -Message 'VBS / HVCI gaming toggle skipped (-SkipVbs specified).'
    }
    else {
        $vbsScript = Join-Path $PSScriptRoot 'toggle_vbs_hypervisor.ps1'
        $vbsArgs = @{ Mode = 'gaming' }
        if ($KeepHypervisor) { $vbsArgs['KeepHypervisor'] = $true }
        if ($isDryRun) { $vbsArgs['DryRun'] = $true }

        $global:LASTEXITCODE = 0
        & $vbsScript @vbsArgs
        if ($LASTEXITCODE -ne 0) {
            throw "toggle_vbs_hypervisor.ps1 exited with code $LASTEXITCODE"
        }
        if ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Invoked toggle_vbs_hypervisor.ps1 -Mode gaming (KeepHypervisor=$KeepHypervisor, DryRun=True). Reboot required after live apply; disabling hypervisor disables WSL2/Docker until restored via '-Mode wsl_docker'."
        }
        else {
            Write-ApplyLog -Level 'APPLIED' -Message "Applied VBS/HVCI gaming mode via toggle_vbs_hypervisor.ps1 (KeepHypervisor=$KeepHypervisor). REBOOT REQUIRED for VBS/Hypervisor changes to take effect. Note: If Hypervisor was set off, WSL2/Docker require 'scripts/toggle_vbs_hypervisor.ps1 -Mode wsl_docker' + reboot to re-enable."
        }
    }
}
catch {
    Write-ApplyLog -Level 'ERROR' -Message "Unhandled error in apply_os_gpu_admin.ps1: $($_.Exception.Message)"
    exit 1
}

if ($script:errorCount -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Completed apply_os_gpu_admin.ps1 with $($script:errorCount) error(s)."
    exit 1
}

Write-ApplyLog -Level 'INFO' -Message "Completed apply_os_gpu_admin.ps1 cleanly (DryRun=$isDryRun)."
exit 0
