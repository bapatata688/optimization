<#
.SYNOPSIS
    Toggles WLAN background autoconfig scanning on/off for gaming sessions or reports current status.
.DESCRIPTION
    Supports -Mode status (non-elevated safe), -Mode gaming (disables background scanning via netsh),
    and -Mode restore (re-enables autoconfig). Uses bilingual regex and checks disabled/deshabilitada
    before enabled/habilitada.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('gaming', 'restore', 'status')]
    [string]$Mode = 'status',
    [string]$Interface = 'Wi-Fi',
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

function Get-WlanAutoconfigState {
    param([Parameter(Mandatory)][string]$IfaceName)

    $isAdmin = Test-IsAdmin
    $wlanSvc = Get-Service -Name 'WlanSvc' -ErrorAction SilentlyContinue
    $nic = Get-NetAdapter -Name $IfaceName -ErrorAction SilentlyContinue
    $autoOut = (& netsh wlan show autoconfig 2>&1) -join "`n"

    $autoconfigEnabled = $null
    $escaped = [regex]::Escape($IfaceName)
    foreach ($line in ($autoOut -split "`r?`n")) {
        if ($line -match "`"$escaped`"") {
            if ($line -match '\b(disabled|deshabilitada)\b') {
                $autoconfigEnabled = $false
                break
            }
            elseif ($line -match '\b(enabled|habilitada)\b') {
                $autoconfigEnabled = $true
                break
            }
        }
    }

    return [PSCustomObject]@{
        interface          = $IfaceName
        autoconfig_enabled = $autoconfigEnabled
        wlan_svc_status    = if ($wlanSvc) { $wlanSvc.Status.ToString() } else { 'NotFound' }
        adapter_status     = if ($nic) { $nic.Status.ToString() } else { 'NotFound' }
        is_admin           = $isAdmin
    }
}

$state = Get-WlanAutoconfigState -IfaceName $Interface

if ($Mode -eq 'status') {
    if ($Json) {
        $state | ConvertTo-Json -Depth 3
    }
    else {
        Write-Host "WLAN Autoconfig Status ($($state.interface)): autoconfig_enabled=$($state.autoconfig_enabled), WlanSvc=$($state.wlan_svc_status), Adapter=$($state.adapter_status), IsAdmin=$($state.is_admin)"
    }
    exit 0
}

$targetEnabled = if ($Mode -eq 'gaming') { 'no' } else { 'yes' }
$cmdPreview = "netsh wlan set autoconfig enabled=$targetEnabled interface=`"$Interface`""

if ($DryRun -or $WhatIfPreference) {
    if ($Json) {
        [PSCustomObject]@{
            mode    = $Mode
            dry_run = $true
            command = $cmdPreview
            before  = $state
        } | ConvertTo-Json -Depth 4
    }
    else {
        Write-Host "[DRY-RUN] Would execute: $cmdPreview"
    }
    exit 0
}

if (-not $state.is_admin) {
    Write-Error "Administrator privileges required to run '$cmdPreview'. Re-run in an elevated PowerShell session."
    exit 1
}

if ($PSCmdlet.ShouldProcess($Interface, "Set WLAN autoconfig enabled=$targetEnabled")) {
    $setOut = (& netsh wlan set autoconfig enabled=$targetEnabled interface="$Interface" 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to execute '$cmdPreview' (exit $LASTEXITCODE): $setOut"
        exit 1
    }
}

$afterState = Get-WlanAutoconfigState -IfaceName $Interface
if ($Json) {
    [PSCustomObject]@{
        mode   = $Mode
        before = $state
        after  = $afterState
    } | ConvertTo-Json -Depth 4
}
else {
    Write-Host "[OK] $cmdPreview -> autoconfig_enabled=$($afterState.autoconfig_enabled)"
    if ($Mode -eq 'gaming') {
        Write-Warning "Background WLAN scanning is now PAUSED. Run 'gaming_wlan_scan_toggle.ps1 -Mode restore' before rebooting or ending your gaming session so Windows auto-connects to Wi-Fi on next boot."
    }
}
exit 0
