<#
.SYNOPSIS
    Applies non-elevated Milestone M2 OS, gaming, startup, and overlay optimizations (Features F5 & F6).
.DESCRIPTION
    Idempotent user-space optimization script for laptop_optimization:
      - F5: Disables GameDVR/AppCapture, sets FSE flags, disables window transparency & animation,
            sets VisualFXSetting=2 (Custom), and sets GPU preference=2 (High performance) for FIFA22.exe
            while preserving existing DirectX tokens and GameBar/Zen Browser configurations.
      - F6: Disables M2 HKCU StartupApproved\Run entries (including dynamic MicrosoftEdgeAutoLaunch_*)
            and safely stops idle M2 user-space overlays/launchers.
            Strictly preserves M3 items (Free Download Manager / fdm, utweb) and essential tools
            (FxSound, XMouseButtonControl, SecurityHealth, RtkAudUService, Zen Browser).
    Supports -DryRun and -WhatIf, and logs every check/change/skip with UTC timestamps to reports/apply_os_gpu.log.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$DryRun,
    [string]$ReportDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'reports'),
    [string]$BackupDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$script:isWhatIf = [bool]$WhatIfPreference
$isDryRun = [bool]($DryRun -or $WhatIfPreference)
$ReportDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReportDir)
$BackupDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BackupDir)
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

function Set-HKCUValueIdempotent {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$Value,
        [Parameter(Mandatory)][ValidateSet('DWord', 'String')][string]$Type
    )

    $info = Get-HKCUValueInfo -Path $Path -Name $Name
    $expectedKind = if ($Type -eq 'DWord') { [Microsoft.Win32.RegistryValueKind]::DWord } else { [Microsoft.Win32.RegistryValueKind]::String }

    $alreadyMatch = $false
    if ($info.ValueExists -and $info.Kind -eq $expectedKind) {
        if ($Type -eq 'DWord') {
            $alreadyMatch = ([int]$info.Value -eq [int]$Value)
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
        $subKeyPath = Convert-HKCUPathToSubKey -PSPath $Path
        $subKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($subKeyPath, $true)
        try {
            $typedVal = if ($Type -eq 'DWord') { [int]$Value } else { [string]$Value }
            $subKey.SetValue($Name, $typedVal, $expectedKind)
        }
        finally {
            $subKey.Close()
        }
        Write-ApplyLog -Level 'APPLIED' -Message "Set $Path\$Name = $Value ($Type) (previous: $currentDesc)."
    }
}

function Set-HKCUBinaryZero4Idempotent {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name
    )

    $info = Get-HKCUValueInfo -Path $Path -Name $Name
    $targetBytes = [byte[]](0, 0, 0, 0)
    $alreadyMatch = $false
    if ($info.ValueExists -and $info.Kind -eq [Microsoft.Win32.RegistryValueKind]::Binary -and $info.Value -is [byte[]]) {
        $cur = [byte[]]$info.Value
        if ($cur.Length -eq 4 -and $cur[0] -eq 0 -and $cur[1] -eq 0 -and $cur[2] -eq 0 -and $cur[3] -eq 0) {
            $alreadyMatch = $true
        }
    }

    if ($alreadyMatch) {
        Write-ApplyLog -Level 'SKIP' -Message "$Path\$Name already 00000000 (Binary)."
        return $false
    }

    $currentHex = if ($info.ValueExists -and $info.Value -is [byte[]]) {
        ([BitConverter]::ToString([byte[]]$info.Value)).Replace('-', '')
    }
    elseif ($info.ValueExists) {
        "$($info.Value) ($($info.Kind))"
    }
    else {
        '<missing>'
    }

    if ($isDryRun) {
        Write-ApplyLog -Level 'DRY-RUN' -Message "Would set $Path\$Name = 00000000 (Binary) (current: $currentHex)."
        return $false
    }

    if ($PSCmdlet.ShouldProcess("$Path\$Name", 'Set REG_BINARY to 00000000')) {
        $subKeyPath = Convert-HKCUPathToSubKey -PSPath $Path
        $subKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($subKeyPath, $true)
        try {
            $subKey.SetValue($Name, $targetBytes, [Microsoft.Win32.RegistryValueKind]::Binary)
        }
        finally {
            $subKey.Close()
        }
        Write-ApplyLog -Level 'APPLIED' -Message "Set $Path\$Name = 00000000 (Binary) (previous: $currentHex)."
        return $true
    }
    return $false
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

    if ($info.ValueExists -and $info.Value -is [byte[]] -and ([byte[]]$info.Value).Length -ge 1) {
        $bytes = [byte[]]$info.Value
        if (($bytes[0] % 2) -eq 1) {
            $hex = ([BitConverter]::ToString($bytes)).Replace('-', '')
            Write-ApplyLog -Level 'SKIP' -Message "StartupApproved\Run\'$Name' already disabled (byte0=0x$($bytes[0].ToString('X2')), data=$hex)."
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

Write-ApplyLog -Level 'INFO' -Message "Starting apply_os_gpu_user.ps1 (DryRun=$isDryRun, ReportDir='$ReportDir')"

try {
    # =========================================================================
    # F5: HKCU OS & Gaming Hooks
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F5: HKCU OS & Gaming Hooks ---'

    Set-HKCUValueIdempotent -Path 'HKCU:\System\GameConfigStore' -Name 'GameDVR_Enabled' -Value 0 -Type 'DWord'
    Set-HKCUValueIdempotent -Path 'HKCU:\System\GameConfigStore' -Name 'GameDVR_FSEBehaviorMode' -Value 2 -Type 'DWord'
    Set-HKCUValueIdempotent -Path 'HKCU:\System\GameConfigStore' -Name 'GameDVR_HonorUserFSEBehaviorMode' -Value 1 -Type 'DWord'
    Set-HKCUValueIdempotent -Path 'HKCU:\System\GameConfigStore' -Name 'GameDVR_DXGIHonorFSEWindowsCompatible' -Value 1 -Type 'DWord'
    Set-HKCUValueIdempotent -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR' -Name 'AppCaptureEnabled' -Value 0 -Type 'DWord'
    Set-HKCUValueIdempotent -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name 'EnableTransparency' -Value 0 -Type 'DWord'
    Set-HKCUValueIdempotent -Path 'HKCU:\Control Panel\Desktop\WindowMetrics' -Name 'MinAnimate' -Value '0' -Type 'String'
    Set-HKCUValueIdempotent -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' -Name 'VisualFXSetting' -Value 2 -Type 'DWord'

    # FIFA22.exe DirectX UserGpuPreferences (preserve existing tokens such as AppStatus=4096;)
    $gpuPrefPath = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
    $fifaExePath = 'C:\Users\Usuario\Downloads\fifa22\FIFA 22\FIFA22.exe'
    if (-not (Test-Path -LiteralPath $fifaExePath)) {
        Write-ApplyLog -Level 'INFO' -Message "Note: '$fifaExePath' is currently absent on disk; updating UserGpuPreferences registry entry while preserving tokens."
    }

    $fifaInfo = Get-HKCUValueInfo -Path $gpuPrefPath -Name $fifaExePath
    $currentPref = if ($fifaInfo.ValueExists) { [string]$fifaInfo.Value } else { '' }
    $tokens = @($currentPref -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' -and $_ -notmatch '^GpuPreference=' })
    $tokens += 'GpuPreference=2'
    $desiredPref = ($tokens -join ';') + ';'
    Set-HKCUValueIdempotent -Path $gpuPrefPath -Name $fifaExePath -Value $desiredPref -Type 'String'

    # Verify GameBar & Zen Browser preserved (read-only logging only)
    $gbAuto = Get-HKCUValueInfo -Path 'HKCU:\Software\Microsoft\GameBar' -Name 'AutoGameModeEnabled'
    $gbAllow = Get-HKCUValueInfo -Path 'HKCU:\Software\Microsoft\GameBar' -Name 'AllowAutoGameMode'
    Write-ApplyLog -Level 'INFO' -Message "Preserved GameBar (AutoGameModeEnabled=$($gbAuto.Value), AllowAutoGameMode=$($gbAllow.Value)) and Zen Browser (%APPDATA%\zen\...) untouched."

    # =========================================================================
    # F6: M2 Startup Bloat & Overlay Reduction
    # =========================================================================
    Write-ApplyLog -Level 'INFO' -Message '--- F6: M2 Startup Bloat & Overlay Reduction ---'

    $protectedStartupNames = @(
        'Free Download Manager',
        'utweb',
        'FxSound',
        'XMouseButtonControl',
        'SecurityHealth',
        'RtkAudUService'
    )

    $startupTargets = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @(
        'EpicGamesLauncher',
        'com.overwolf.curseforge',
        'Steam',
        'Docker Desktop',
        'AMDNoiseSuppression',
        'OneDriveSetup',
        'nefarius_HidHide_Updater'
    )) {
        $startupTargets.Add($name)
    }

    # Dynamically discover MicrosoftEdgeAutoLaunch_* in StartupApproved\Run and Run
    foreach ($scanKey in @(
        'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run',
        'Software\Microsoft\Windows\CurrentVersion\Run'
    )) {
        $sk = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($scanKey, $false)
        if ($null -ne $sk) {
            try {
                foreach ($valName in $sk.GetValueNames()) {
                    if ($valName -like 'MicrosoftEdgeAutoLaunch_*' -and -not $startupTargets.Contains($valName)) {
                        $startupTargets.Add($valName)
                    }
                }
            }
            finally {
                $sk.Close()
            }
        }
    }

    foreach ($target in $startupTargets) {
        Disable-StartupApprovedRunValue -Name $target -ProtectedNames $protectedStartupNames
    }
    Write-ApplyLog -Level 'INFO' -Message 'Protected/M3 startup items untouched: Free Download Manager, utweb, FxSound, XMouseButtonControl, SecurityHealth, RtkAudUService.'

    # Persistent user-space NVIDIA Overlay / ShadowPlay disable (NVSPCAPS + user-owned SPUser)
    $nvspcapsPath = 'HKCU:\Software\NVIDIA Corporation\Global\ShadowPlay\NVSPCAPS'
    $nvspcapsRegExePath = 'HKCU\Software\NVIDIA Corporation\Global\ShadowPlay\NVSPCAPS'
    $nvspcapsSubKey = Convert-HKCUPathToSubKey -PSPath $nvspcapsPath
    $nvspcapsKeyCheck = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($nvspcapsSubKey, $false)
    $nvspcapsKeyExists = ($null -ne $nvspcapsKeyCheck)
    if ($nvspcapsKeyCheck) { $nvspcapsKeyCheck.Close() }

    $nvspcapsMutated = $false
    if ($nvspcapsKeyExists) {
        $nvspcapsBackupDir = Join-Path $BackupDir 'system'
        $nvspcapsBackupFile = Join-Path $nvspcapsBackupDir 'hkcu_nvspcaps_before.reg'
        if (Test-Path -LiteralPath $nvspcapsBackupFile) {
            Write-ApplyLog -Level 'SKIP' -Message "NVSPCAPS backup '$nvspcapsBackupFile' already exists (preserving)."
        }
        elseif ($isDryRun) {
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would export '$nvspcapsRegExePath' -> '$nvspcapsBackupFile'."
        }
        elseif ($PSCmdlet.ShouldProcess($nvspcapsBackupFile, "Export $nvspcapsRegExePath backup")) {
            [void][System.IO.Directory]::CreateDirectory($nvspcapsBackupDir)
            $expOut = (& reg.exe export $nvspcapsRegExePath $nvspcapsBackupFile 2>&1) -join ' '
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $nvspcapsBackupFile)) {
                throw "Failed exporting '$nvspcapsRegExePath' to '$nvspcapsBackupFile' (rc=$LASTEXITCODE): $expOut"
            }
            Write-ApplyLog -Level 'APPLIED' -Message "Exported NVSPCAPS backup -> '$nvspcapsBackupFile'."
        }

        $m1 = Set-HKCUBinaryZero4Idempotent -Path $nvspcapsPath -Name 'IsShadowPlayEnabled'
        $m2 = Set-HKCUBinaryZero4Idempotent -Path $nvspcapsPath -Name 'IsShadowPlayEnabledUser'
        $nvspcapsMutated = ($m1 -or $m2)
    }
    else {
        Write-ApplyLog -Level 'SKIP' -Message "Registry key '$nvspcapsPath' not present."
    }

    # Stop idle M2 user-space overlay/bloat processes safely
    $protectedProcessPatterns = @(
        'fdm',
        'utweb',
        'FxSound',
        'XMouseButtonControl',
        'SecurityHealth*',
        'RtkAudUService*',
        'zen'
    )

    $overlayRunning = @(Get-Process -Name 'NVIDIA Overlay', 'nvsphelper64' -ErrorAction SilentlyContinue).Count -gt 0
    $nvidiaStopped = $false

    # Stop ONLY user-owned nvcontainer.exe matching *plugins\SPUser* (never SYSTEM or NVDisplay.ContainerLocalSystem)
    if ($nvspcapsMutated -or $overlayRunning) {
        $spUserProcs = @(
            Get-CimInstance -ClassName Win32_Process -Filter "Name='nvcontainer.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*plugins\SPUser*' }
        )
        foreach ($sp in $spUserProcs) {
            $owner = Invoke-CimMethod -InputObject $sp -MethodName GetOwner -ErrorAction SilentlyContinue
            $ownerUser = if ($owner -and $owner.ReturnValue -eq 0) { [string]$owner.User } else { '' }
            if ([string]::IsNullOrWhiteSpace($ownerUser) -or $ownerUser -ine $env:USERNAME -or $ownerUser -match '^(SYSTEM|LOCAL SERVICE|NETWORK SERVICE)$') {
                Write-ApplyLog -Level 'SKIP' -Message "Skipping non-current-user nvcontainer.exe (PID $($sp.ProcessId), Owner='$ownerUser')."
                continue
            }
            if ($isDryRun) {
                Write-ApplyLog -Level 'DRY-RUN' -Message "Would stop user-owned nvcontainer.exe SPUser (PID $($sp.ProcessId), Owner='$ownerUser')."
            }
            elseif ($PSCmdlet.ShouldProcess("nvcontainer.exe SPUser (PID $($sp.ProcessId))", 'Stop current-user SPUser container')) {
                try {
                    Stop-Process -Id ([int]$sp.ProcessId) -Force -ErrorAction Stop
                    $nvidiaStopped = $true
                    Write-ApplyLog -Level 'APPLIED' -Message "Stopped user-owned nvcontainer.exe SPUser (PID $($sp.ProcessId), Owner='$ownerUser')."
                }
                catch {
                    Write-ApplyLog -Level 'WARN' -Message "Could not stop nvcontainer.exe SPUser (PID $($sp.ProcessId)): $($_.Exception.Message)"
                }
            }
        }
    }

    $processTargets = @(
        'NVIDIA Overlay',
        'nvsphelper64',
        'RadeonSoftware',
        'AMDRSSrcExt',
        'EpicGamesLauncher',
        'EpicOnlineServicesUserHelper',
        'EpicWebHelper',
        'steam',
        'steamwebhelper',
        'CurseForge',
        'Overwolf',
        'OneDrive',
        'AMDNoiseSuppression'
    )

    foreach ($procName in $processTargets) {
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
            Write-ApplyLog -Level 'DRY-RUN' -Message "Would stop idle M2 process '$procName' (count=$($procs.Count), PIDs: $pids)."
            continue
        }

        foreach ($p in $procs) {
            if ($PSCmdlet.ShouldProcess("$procName (PID $($p.Id))", 'Stop idle M2 user-space process')) {
                try {
                    Stop-Process -Id $p.Id -Force -ErrorAction Stop
                    if ($procName -in @('NVIDIA Overlay', 'nvsphelper64')) {
                        $nvidiaStopped = $true
                    }
                    Write-ApplyLog -Level 'APPLIED' -Message "Stopped process '$procName' (PID $($p.Id))."
                }
                catch {
                    Write-ApplyLog -Level 'WARN' -Message "Could not stop '$procName' (PID $($p.Id)): $($_.Exception.Message)"
                }
            }
        }
    }

    if (-not $isDryRun -and ($nvidiaStopped -or $nvspcapsMutated)) {
        Start-Sleep -Seconds 5
        $respawned = @(Get-Process -Name 'NVIDIA Overlay' -ErrorAction SilentlyContinue)
        if ($respawned.Count -gt 0) {
            $rpids = ($respawned | ForEach-Object { $_.Id }) -join ', '
            Write-ApplyLog -Level 'ERROR' -Message "NVIDIA Overlay respawned after 5s (PIDs: $rpids)."
        }
        else {
            Write-ApplyLog -Level 'INFO' -Message 'Verified NVIDIA Overlay did not respawn after 5s.'
        }
    }
}
catch {
    Write-ApplyLog -Level 'ERROR' -Message "Unhandled error in apply_os_gpu_user.ps1: $($_.Exception.Message)"
    exit 1
}

if ($script:errorCount -gt 0) {
    Write-ApplyLog -Level 'ERROR' -Message "Completed apply_os_gpu_user.ps1 with $($script:errorCount) error(s)."
    exit 1
}

Write-ApplyLog -Level 'INFO' -Message "Completed apply_os_gpu_user.ps1 cleanly (DryRun=$isDryRun)."
exit 0
