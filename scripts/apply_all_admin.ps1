<#
.SYNOPSIS
    Consolidated, idempotent elevated wrapper script to apply all laptop_optimization admin steps in a single UAC session.
.DESCRIPTION
    Orchestrates the three elevated scripts in order so the user only needs ONE elevated PowerShell prompt:
      1. Pre-mutation elevated baseline backup (backup_baseline.ps1 -IncludeElevated, never -Force)
         only if backups/power/power_scheme_*.pow is missing or backups/system/restore_point_info.json
         indicates skipped (skipped under -DryRun / -WhatIf with [DRY-RUN] log).
      2. OS, power, GPU, services, tasks, and VBS/HVCI optimization (apply_os_gpu_admin.ps1).
      3. Network, Wi-Fi roaming/power, Delivery Optimization, TCP/MMCSS/QoS optimization (apply_network_admin.ps1).

    Elevation Guard:
      - If not running as Administrator and neither -DryRun nor -WhatIf is passed, logs clearly and exits with code 2.
      - Never launches UAC elevation dialogs automatically.

    Supports -DryRun and -WhatIf, uses strict mode, and logs UTF-8 (without BOM) to reports/apply_all_admin.log.
#>
[CmdletBinding(SupportsShouldProcess = $true, PositionalBinding = $false)]
param(
    [string]$BackupDir = (Join-Path $PSScriptRoot '..\backups'),
    [string]$ReportDir = (Join-Path $PSScriptRoot '..\reports'),
    [string[]]$DnsServers = @(),
    [switch]$SkipVbs,
    [switch]$KeepHypervisor,
    [switch]$SkipDnsBenchmark,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$script:isWhatIf = [bool]$WhatIfPreference
$isDryRun = [bool]($DryRun -or $WhatIfPreference)
$BackupDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BackupDir)
$ReportDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportDir)

$logPath = Join-Path $ReportDir 'apply_all_admin.log'
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

$isAdmin = Test-IsAdmin
$dnsDisplay = if ($null -ne $DnsServers -and @($DnsServers).Count -gt 0) { $DnsServers -join ', ' } else { '<preserve existing>' }

Write-ApplyLog -Level 'INFO' -Message "Starting apply_all_admin.ps1 (IsAdmin=$isAdmin, DryRun=$isDryRun, WhatIf=$WhatIfPreference, SkipVbs=$SkipVbs, KeepHypervisor=$KeepHypervisor, SkipDnsBenchmark=$SkipDnsBenchmark, DnsServers=[$dnsDisplay], BackupDir='$BackupDir', ReportDir='$ReportDir')"

# -----------------------------------------------------------------------------
# Elevation Guard (never pop up UAC automatically)
# -----------------------------------------------------------------------------
if (-not $isAdmin -and -not $isDryRun) {
    Write-ApplyLog -Level 'ERROR' -Message 'Administrator privileges required (Se requieren permisos de Administrador). Open ONE elevated PowerShell window ("Run as administrator") and re-run apply_all_admin.ps1, or pass -DryRun / -WhatIf for read-only simulation. Exiting with code 2.'
    exit 2
}

# -----------------------------------------------------------------------------
# Pre-Flight Check: Verify all 3 target scripts exist
# -----------------------------------------------------------------------------
$backupScript = Join-Path $PSScriptRoot 'backup_baseline.ps1'
$osGpuScript = Join-Path $PSScriptRoot 'apply_os_gpu_admin.ps1'
$netScript = Join-Path $PSScriptRoot 'apply_network_admin.ps1'

$requiredScripts = @(
    @{ Name = 'backup_baseline.ps1'; Path = $backupScript },
    @{ Name = 'apply_os_gpu_admin.ps1'; Path = $osGpuScript },
    @{ Name = 'apply_network_admin.ps1'; Path = $netScript }
)

$missingScripts = [System.Collections.Generic.List[string]]::new()
foreach ($req in $requiredScripts) {
    if (-not (Test-Path -LiteralPath $req.Path -PathType Leaf)) {
        $missingScripts.Add("$($req.Name) ('$($req.Path)')")
    }
}
if ($missingScripts.Count -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Pre-flight check failed; missing required script(s): $($missingScripts -join '; ')."
    exit 1
}
Write-ApplyLog -Level 'INFO' -Message 'Pre-flight check passed: all 3 target scripts found (backup_baseline.ps1, apply_os_gpu_admin.ps1, apply_network_admin.ps1).'

$step1Status = 'PENDING'
$step2Status = 'PENDING'
$step3Status = 'PENDING'

try {
    # =========================================================================
    # Step 1: Elevated Baseline Backup (.pow + System Restore point)
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '=== Step 1/3: Pre-mutation elevated baseline backup (backup_baseline.ps1) ==='

    $powerBackupDir = Join-Path $BackupDir 'power'
    $powFiles = @(Get-ChildItem -Path $powerBackupDir -Filter 'power_scheme_*.pow' -ErrorAction SilentlyContinue)
    $powMissing = ($powFiles.Count -eq 0)

    $restoreInfoPath = Join-Path (Join-Path $BackupDir 'system') 'restore_point_info.json'
    $restoreSkipped = $false
    $restoreStatusStr = '<missing>'
    if (Test-Path -LiteralPath $restoreInfoPath -PathType Leaf) {
        try {
            $restoreInfo = Get-Content -LiteralPath $restoreInfoPath -Raw -Encoding utf8 | ConvertFrom-Json
            if ($null -ne $restoreInfo -and $null -ne $restoreInfo.PSObject.Properties['checkpoint_status']) {
                $restoreStatusStr = [string]$restoreInfo.checkpoint_status
                if ($restoreStatusStr -like '*skipped*') {
                    $restoreSkipped = $true
                }
            }
        }
        catch {
            Write-ApplyLog -Level 'WARN' -Message "Could not parse '$restoreInfoPath': $($_.Exception.Message)"
        }
    }

    $needsElevatedBackup = ($powMissing -or $restoreSkipped)
    if (-not $needsElevatedBackup) {
        $step1Status = "SKIPPED (already present: $($powFiles.Count) .pow file(s), restore_point_info checkpoint_status='$restoreStatusStr')"
        Write-ApplyLog -Level 'SKIP' -Message "Step 1 skipped: $($powFiles.Count) .pow file(s) present and restore_point_info checkpoint_status='$restoreStatusStr'."
    }
    elseif ($isDryRun) {
        $reasons = [System.Collections.Generic.List[string]]::new()
        if ($powMissing) { $reasons.Add("no power_scheme_*.pow in '$powerBackupDir'") }
        if ($restoreSkipped) { $reasons.Add("restore_point_info.json checkpoint_status='$restoreStatusStr'") }
        $reasonStr = $reasons -join '; '
        $step1Status = "DRY-RUN (would invoke backup_baseline.ps1 -BackupRoot '$BackupDir' -IncludeElevated; reason: $reasonStr)"
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would run '& `"$backupScript`" -BackupRoot `"$BackupDir`" -IncludeElevated' (never -Force). Reason: $reasonStr."
    }
    elseif ($PSCmdlet.ShouldProcess($BackupDir, 'Run backup_baseline.ps1 -IncludeElevated')) {
        Write-ApplyLog -Level 'INFO' -Message "Running backup_baseline.ps1 -BackupRoot '$BackupDir' -IncludeElevated (never -Force)..."
        $global:LASTEXITCODE = 0
        & $backupScript -BackupRoot $BackupDir -IncludeElevated
        $step1Exit = if (Test-Path 'variable:global:LASTEXITCODE') { [int]$LASTEXITCODE } else { 0 }
        if ($step1Exit -ne 0) {
            Write-ApplyLog -Level 'ERROR' -Message "Step 1 failed: backup_baseline.ps1 exited with code $step1Exit."
            exit 1
        }
        $powAfter = @(Get-ChildItem -Path $powerBackupDir -Filter 'power_scheme_*.pow' -ErrorAction SilentlyContinue)
        $step1Status = "APPLIED (exit=0, $($powAfter.Count) .pow file(s))"
        Write-ApplyLog -Level 'APPLIED' -Message "Step 1 complete: backup_baseline.ps1 exited 0 ($($powAfter.Count) .pow file(s) in '$powerBackupDir')."
    }

    # =========================================================================
    # Step 2: OS, Power, GPU, Services, Tasks & VBS/HVCI (apply_os_gpu_admin.ps1)
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '=== Step 2/3: OS, Power, GPU, Services & VBS/HVCI (apply_os_gpu_admin.ps1) ==='

    $osGpuArgs = @{
        BackupDir = $BackupDir
        ReportDir = $ReportDir
        WhatIf    = [bool]$WhatIfPreference
    }
    if ($SkipVbs) { $osGpuArgs['SkipVbs'] = $true }
    if ($KeepHypervisor) { $osGpuArgs['KeepHypervisor'] = $true }
    if ($DryRun) { $osGpuArgs['DryRun'] = $true }

    $global:LASTEXITCODE = 0
    & $osGpuScript @osGpuArgs
    $step2Exit = if (Test-Path 'variable:global:LASTEXITCODE') { [int]$LASTEXITCODE } else { 0 }
    if ($step2Exit -ne 0) {
        Write-ApplyLog -Level 'ERROR' -Message "Step 2 failed: apply_os_gpu_admin.ps1 exited with code $step2Exit."
        exit 1
    }
    $step2Status = if ($isDryRun) { 'DRY-RUN (exit=0)' } else { 'APPLIED (exit=0)' }
    Write-ApplyLog -Level 'INFO' -Message "Step 2 completed cleanly (exit=0)."

    # =========================================================================
    # Step 3: Network, Wi-Fi, DoSvc, TCP/MMCSS/QoS (apply_network_admin.ps1)
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '=== Step 3/3: Network & Ping Optimizations (apply_network_admin.ps1) ==='

    $netArgs = @{
        BackupDir = $BackupDir
        ReportDir = $ReportDir
        WhatIf    = [bool]$WhatIfPreference
    }
    if ($null -ne $DnsServers -and @($DnsServers).Count -gt 0) {
        $netArgs['DnsServers'] = $DnsServers
    }
    if ($SkipDnsBenchmark) { $netArgs['SkipDnsBenchmark'] = $true }
    if ($DryRun) { $netArgs['DryRun'] = $true }

    $global:LASTEXITCODE = 0
    & $netScript @netArgs
    $step3Exit = if (Test-Path 'variable:global:LASTEXITCODE') { [int]$LASTEXITCODE } else { 0 }
    if ($step3Exit -ne 0) {
        Write-ApplyLog -Level 'ERROR' -Message "Step 3 failed: apply_network_admin.ps1 exited with code $step3Exit."
        exit 1
    }
    $step3Status = if ($isDryRun) { 'DRY-RUN (exit=0)' } else { 'APPLIED (exit=0)' }
    Write-ApplyLog -Level 'INFO' -Message "Step 3 completed cleanly (exit=0)."
}
catch {
    Write-ApplyLog -Level 'ERROR' -Message "Unhandled error in apply_all_admin.ps1: $($_.Exception.Message)"
    exit 1
}

if ($script:errorCount -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Completed apply_all_admin.ps1 with $($script:errorCount) error(s)."
    exit 1
}

# =============================================================================
# Summary (English / Español)
# =============================================================================
$modeLabel = if ($isDryRun) { 'DRY-RUN / SIMULACIÓN (sin cambios reales al sistema)' } else { 'LIVE ELEVATED / APLICADO EN VIVO' }

Write-Host ''
Write-Host '========================================================================='
Write-Host '  RESUMEN / SUMMARY - apply_all_admin.ps1'
Write-Host '========================================================================='
Write-Host "  Mode / Modo       : $modeLabel"
Write-Host "  Step 1 (Backup)   : $step1Status"
Write-Host "  Step 2 (OS/GPU)   : $step2Status"
Write-Host "  Step 3 (Network)  : $step3Status"
Write-Host "  Logs / Registros  :"
Write-Host "    - $(Join-Path $ReportDir 'apply_all_admin.log')"
Write-Host "    - $(Join-Path $ReportDir 'apply_os_gpu.log')"
Write-Host "    - $(Join-Path $ReportDir 'apply_network.log')"

if ($SkipVbs) {
    Write-Host '  VBS / HVCI        : Skipped (-SkipVbs). No VBS/HVCI reboot required.'
    Write-Host '                      Omitido (-SkipVbs). No requiere reinicio por VBS/HVCI.'
}
elseif ($isDryRun) {
    Write-Host "  VBS / HVCI        : Simulated (Mode=gaming, KeepHypervisor=$KeepHypervisor)."
    Write-Host '  Reboot / Reinicio : NOT required yet (dry-run). When run live as Admin without -SkipVbs,'
    Write-Host '                      a Windows reboot IS REQUIRED for VBS/HVCI changes to take effect.'
    Write-Host '                      (En ejecución real como Admin sin -SkipVbs, SE REQUIERE REINICIAR Windows).'
}
else {
    Write-Host "  VBS / HVCI        : Configured for gaming (KeepHypervisor=$KeepHypervisor)."
    Write-Host '  REBOOT REQUIRED   : Restart Windows now for VBS/HVCI/Hypervisor changes to take effect!'
    Write-Host '  REINICIO REQUERIDO: Reinicia Windows para que los cambios de VBS/HVCI surtan efecto.'
    if (-not $KeepHypervisor) {
        Write-Host '  WSL2 / Docker     : Hypervisor set to off; to re-enable WSL2/Docker later run:'
        Write-Host '                      scripts\toggle_vbs_hypervisor.ps1 -Mode wsl_docker (+ reboot)'
    }
}
Write-Host '========================================================================='
Write-Host ''

Write-ApplyLog -Level 'INFO' -Message "Completed apply_all_admin.ps1 cleanly (DryRun=$isDryRun, Step1='$step1Status', Step2='$step2Status', Step3='$step3Status')."
exit 0
