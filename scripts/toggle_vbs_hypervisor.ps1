<#
.SYNOPSIS
    Inspects or toggles Windows VBS, HVCI, and Hypervisor launch type for gaming vs WSL2/Docker workflows.
.DESCRIPTION
    Non-elevated -Mode status uses Win32_DeviceGuard and HKLM DeviceGuard registry (never calling bcdedit
    non-elevated, which fails with exit code 1). Mutating modes require Administrator privileges unless -DryRun.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('gaming', 'wsl_docker', 'status')]
    [string]$Mode = 'status',
    [switch]$KeepHypervisor,
    [switch]$EnableHvci,
    [switch]$DryRun,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Test-IsAdmin {
    $principal = [Security.Principal.WindowsPrincipal]([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-VbsHypervisorState {
    $isAdmin = Test-IsAdmin
    $dgCim = Get-CimInstance -Namespace 'root\Microsoft\Windows\DeviceGuard' -ClassName 'Win32_DeviceGuard' -ErrorAction SilentlyContinue | Select-Object -First 1

    $dgRegPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard'
    $hvciRegPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'

    $dgProps = Get-ItemProperty -Path $dgRegPath -ErrorAction SilentlyContinue
    $hvciProps = Get-ItemProperty -Path $hvciRegPath -ErrorAction SilentlyContinue

    $enableVbs = if ($dgProps -and $null -ne $dgProps.PSObject.Properties['EnableVirtualizationBasedSecurity']) {
        [int]$dgProps.EnableVirtualizationBasedSecurity
    } else { $null }

    $hvciEnabled = if ($hvciProps -and $null -ne $hvciProps.PSObject.Properties['Enabled']) {
        [int]$hvciProps.Enabled
    } else { $null }

    $hypervisorLaunch = 'Unknown (requires elevation to query BCD)'
    if ($isAdmin) {
        $bcdOut = (& bcdedit /enum "{current}" 2>&1) -join "`n"
        if ($LASTEXITCODE -eq 0 -and $bcdOut -match 'hypervisorlaunchtype\s+(\S+)') {
            $hypervisorLaunch = $Matches[1].Trim()
        }
        elseif ($LASTEXITCODE -eq 0) {
            $hypervisorLaunch = 'not_set (default: auto)'
        }
        else {
            $hypervisorLaunch = "bcdedit_error ($LASTEXITCODE)"
        }
    }

    return [PSCustomObject]@{
        is_admin                              = $isAdmin
        vbs_running_status                    = if ($dgCim -and $null -ne $dgCim.PSObject.Properties['VirtualizationBasedSecurityStatus'] -and $null -ne $dgCim.VirtualizationBasedSecurityStatus) { [int]$dgCim.VirtualizationBasedSecurityStatus } else { $null }
        code_integrity_enforcement_status     = if ($dgCim -and $null -ne $dgCim.PSObject.Properties['CodeIntegrityPolicyEnforcementStatus'] -and $null -ne $dgCim.CodeIntegrityPolicyEnforcementStatus) { [int]$dgCim.CodeIntegrityPolicyEnforcementStatus } else { $null }
        enable_vbs_registry                   = $enableVbs
        hvci_enabled_registry                 = $hvciEnabled
        hypervisor_launch_type                = $hypervisorLaunch
    }
}

$state = Get-VbsHypervisorState

if ($Mode -eq 'status') {
    if ($Json) {
        $state | ConvertTo-Json -Depth 4
    }
    else {
        Write-Host "VBS/Hypervisor Status: VbsRunningStatus=$($state.vbs_running_status), EnableVBS_Reg=$($state.enable_vbs_registry), HVCI_Reg=$($state.hvci_enabled_registry), HypervisorLaunchType=$($state.hypervisor_launch_type), IsAdmin=$($state.is_admin)"
    }
    exit 0
}

$dgRegPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard'
$hvciRegPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'

$plannedActions = [System.Collections.Generic.List[string]]::new()
if ($Mode -eq 'gaming') {
    $plannedActions.Add("Set $dgRegPath\EnableVirtualizationBasedSecurity = 0 (DWORD)")
    $plannedActions.Add("Ensure $hvciRegPath and set Enabled = 0 (DWORD)")
    if (-not $KeepHypervisor) {
        $plannedActions.Add('bcdedit /set "{current}" hypervisorlaunchtype off')
    }
    else {
        $plannedActions.Add('Keep hypervisorlaunchtype unchanged (-KeepHypervisor specified)')
    }
}
else {
    $plannedActions.Add("Set $dgRegPath\EnableVirtualizationBasedSecurity = 1 (DWORD)")
    $hvciTarget = if ($EnableHvci) { 1 } else { 0 }
    $plannedActions.Add("Ensure $hvciRegPath and set Enabled = $hvciTarget (DWORD)")
    $plannedActions.Add('bcdedit /set "{current}" hypervisorlaunchtype auto')
}

if ($DryRun -or $WhatIfPreference) {
    if ($Json) {
        [PSCustomObject]@{
            mode            = $Mode
            dry_run         = $true
            keep_hypervisor = [bool]$KeepHypervisor
            planned_actions = @($plannedActions)
            current_state   = $state
            reboot_required = $true
        } | ConvertTo-Json -Depth 4
    }
    else {
        foreach ($act in $plannedActions) {
            Write-Host "[DRY-RUN] $act"
        }
        Write-Host "[DRY-RUN] Note: Reboot required after applying VBS/hypervisor changes."
    }
    exit 0
}

if (-not $state.is_admin) {
    Write-Error "Administrator privileges required to modify HKLM DeviceGuard registry and run bcdedit. Re-run in an elevated PowerShell session."
    exit 1
}

if ($PSCmdlet.ShouldProcess('HKLM DeviceGuard & BCD', "Apply VBS/Hypervisor mode '$Mode'")) {
    if (-not (Test-Path -LiteralPath $dgRegPath)) {
        New-Item -Path $dgRegPath -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $hvciRegPath)) {
        New-Item -Path $hvciRegPath -Force | Out-Null
    }

    if ($Mode -eq 'gaming') {
        Set-ItemProperty -Path $dgRegPath -Name 'EnableVirtualizationBasedSecurity' -Value 0 -Type DWord
        Set-ItemProperty -Path $hvciRegPath -Name 'Enabled' -Value 0 -Type DWord
        if (-not $KeepHypervisor) {
            $bcdOut = (& bcdedit /set "{current}" hypervisorlaunchtype off 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                Write-Error "bcdedit failed ($LASTEXITCODE): $bcdOut"
                exit 1
            }
        }
    }
    else {
        Set-ItemProperty -Path $dgRegPath -Name 'EnableVirtualizationBasedSecurity' -Value 1 -Type DWord
        $hvciVal = if ($EnableHvci) { 1 } else { 0 }
        Set-ItemProperty -Path $hvciRegPath -Name 'Enabled' -Value $hvciVal -Type DWord
        $bcdOut = (& bcdedit /set "{current}" hypervisorlaunchtype auto 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "bcdedit failed ($LASTEXITCODE): $bcdOut"
            exit 1
        }
    }
}

$afterState = Get-VbsHypervisorState
if ($Json) {
    [PSCustomObject]@{
        mode            = $Mode
        applied_actions = @($plannedActions)
        after_state     = $afterState
        reboot_required = $true
        notice          = 'Restart Windows for VBS and hypervisorlaunchtype changes to take effect; Win32_DeviceGuard reflects current boot state until reboot.'
    } | ConvertTo-Json -Depth 4
}
else {
    Write-Host "[OK] Applied VBS/Hypervisor mode '$Mode'."
    if ($Mode -eq 'gaming' -and -not $KeepHypervisor) {
        Write-Warning "WSL2 and Docker Desktop Hyper-V backend will not start until '-Mode wsl_docker' is run."
    }
    Write-Warning "REBOOT REQUIRED: Restart Windows for VBS and hypervisorlaunchtype changes to take effect (Win32_DeviceGuard reflects current boot state until reboot)."
}
exit 0
