<#
.SYNOPSIS
    Reproducible network latency, jitter, packet-loss, DNS, and adapter power-state benchmark.
.DESCRIPTION
    Performs a loopback .NET Ping JIT warmup before the pre-gateway idle pause, measures sub-ms RTT via
    System.Diagnostics.Stopwatch across gateway and public targets, warms up DnsClient before Resolve-DnsName
    queries, inspects locale-independent NIC power settings, and writes UTF-8 JSON without BOM.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('baseline', 'post')]
    [string]$Tag,
    [string]$Out,
    [string]$Interface = 'Wi-Fi',
    [ValidateRange(1, 10000)]
    [int]$PingCount = 50,
    [ValidateRange(0, 300)]
    [int]$IdleWaitSec = 5,
    [ValidateRange(1, 60000)]
    [int]$TimeoutMs = 2000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Out)) {
    $Out = Join-Path $repoRoot "reports\${Tag}_network.json"
}

function Get-Percentile {
    param(
        [Parameter(Mandatory)][double[]]$SortedValues,
        [Parameter(Mandatory)][double]$Percentile
    )
    if ($SortedValues.Count -eq 0) { return $null }
    if ($SortedValues.Count -eq 1) { return [Math]::Round($SortedValues[0], 3) }
    $idx = ($Percentile / 100.0) * ($SortedValues.Count - 1)
    $lo = [int][Math]::Floor($idx)
    $hi = [int][Math]::Ceiling($idx)
    $frac = $idx - $lo
    $val = $SortedValues[$lo] + ($SortedValues[$hi] - $SortedValues[$lo]) * $frac
    return [Math]::Round($val, 3)
}

function Measure-IcmpTarget {
    param(
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][int]$Count,
        [Parameter(Mandatory)][int]$Timeout,
        [Parameter(Mandatory)][System.Net.NetworkInformation.Ping]$PingClient
    )
    $allRtts = [System.Collections.Generic.List[double]]::new()
    $packet1Ms = $null
    $successCount = 0
    $sw = [System.Diagnostics.Stopwatch]::new()

    for ($i = 1; $i -le $Count; $i++) {
        $sw.Restart()
        $reply = $null
        try {
            $reply = $PingClient.Send($Target, $Timeout)
        }
        catch {
            $reply = $null
        }
        $sw.Stop()
        $elapsedMs = [Math]::Round($sw.Elapsed.TotalMilliseconds, 3)

        if ($null -ne $reply -and $reply.Status -eq [System.Net.NetworkInformation.IPStatus]::Success) {
            $successCount++
            $allRtts.Add($elapsedMs)
            if ($i -eq 1) {
                $packet1Ms = $elapsedMs
            }
        }
        if ($i -lt $Count) {
            Start-Sleep -Milliseconds 100
        }
    }

    $lossPct = [Math]::Round((($Count - $successCount) / [double]$Count) * 100.0, 2)
    if ($allRtts.Count -eq 0) {
        return [PSCustomObject]@{
            target     = $Target
            packet1_ms = $null
            min_ms     = $null
            avg_ms     = $null
            p50_ms     = $null
            p95_ms     = $null
            p99_ms     = $null
            max_ms     = $null
            jitter_ms  = $null
            loss_pct   = $lossPct
        }
    }

    $sorted = @($allRtts | Sort-Object)
    $sum = 0.0
    foreach ($v in $allRtts) { $sum += $v }
    $avg = [Math]::Round($sum / $allRtts.Count, 3)

    $jitter = 0.0
    if ($allRtts.Count -ge 2) {
        $diffSum = 0.0
        for ($j = 1; $j -lt $allRtts.Count; $j++) {
            $diffSum += [Math]::Abs($allRtts[$j] - $allRtts[$j - 1])
        }
        $jitter = [Math]::Round($diffSum / ($allRtts.Count - 1), 3)
    }

    return [PSCustomObject]@{
        target     = $Target
        packet1_ms = $packet1Ms
        min_ms     = [Math]::Round($sorted[0], 3)
        avg_ms     = $avg
        p50_ms     = Get-Percentile -SortedValues $sorted -Percentile 50
        p95_ms     = Get-Percentile -SortedValues $sorted -Percentile 95
        p99_ms     = Get-Percentile -SortedValues $sorted -Percentile 99
        max_ms     = [Math]::Round($sorted[$sorted.Count - 1], 3)
        jitter_ms  = $jitter
        loss_pct   = $lossPct
    }
}

# 1. Resolve Adapter & IP/WLAN Metadata
if ($Interface -eq 'auto') {
    $defaultRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1
    if ($defaultRoute) {
        $nic = Get-NetAdapter -InterfaceIndex $defaultRoute.InterfaceIndex -ErrorAction Stop | Select-Object -First 1
    }
    else {
        $nic = Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object -First 1
    }
}
else {
    $nic = Get-NetAdapter -Name $Interface -ErrorAction Stop | Select-Object -First 1
}

if ($null -eq $nic) {
    throw "No suitable network adapter found (Interface='$Interface')."
}

$ipCfg = Get-NetIPConfiguration -InterfaceIndex $nic.InterfaceIndex -ErrorAction SilentlyContinue | Select-Object -First 1
$gateway = $null
if ($null -ne $ipCfg -and $null -ne $ipCfg.PSObject.Properties['IPv4DefaultGateway'] -and $null -ne $ipCfg.IPv4DefaultGateway) {
    $gwItems = @($ipCfg.IPv4DefaultGateway | Where-Object { $null -ne $_ })
    if ($gwItems.Count -gt 0 -and $null -ne $gwItems[0].PSObject.Properties['NextHop']) {
        $gateway = [string]$gwItems[0].NextHop
    }
}
if ([string]::IsNullOrWhiteSpace($gateway)) {
    $route = Get-NetRoute -InterfaceIndex $nic.InterfaceIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($route -and $null -ne $route.PSObject.Properties['NextHop']) { $gateway = [string]$route.NextHop }
}

$dnsObj = Get-DnsClientServerAddress -InterfaceIndex $nic.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
$dnsServers = if ($null -ne $dnsObj -and $null -ne $dnsObj.PSObject.Properties['ServerAddresses'] -and $null -ne $dnsObj.ServerAddresses) { @($dnsObj.ServerAddresses) } else { @() }

$ssid = $null
$band = $null
$channel = $null
$rssiDbm = $null

$wlanOut = (& netsh wlan show interfaces 2>&1) -join "`n"
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($wlanOut)) {
    $inMatchingBlock = $false
    foreach ($line in ($wlanOut -split "`r?`n")) {
        if ($line -match '^\s*(?:Name|Nombre)\s*:\s*(.+?)\s*$') {
            $inMatchingBlock = ($Matches[1].Trim() -eq $nic.Name)
            continue
        }
        if (-not $inMatchingBlock) { continue }
        if ($line -match '^\s*SSID\s*:\s*(.+?)\s*$') {
            $ssid = $Matches[1].Trim()
        }
        elseif ($line -match '^\s*(?:Band|Banda)\s*:\s*(.+?)\s*$') {
            $band = $Matches[1].Trim()
        }
        elseif ($line -match '^\s*(?:Channel|Canal)\s*:\s*(\d+)\s*$') {
            $channel = [int]$Matches[1]
        }
        elseif ($line -match '^\s*(?:Rssi|RSSI)\s*:\s*(-?\d+)\s*$') {
            $rssiDbm = [int]$Matches[1]
        }
    }
}

$adapterObj = [PSCustomObject]@{
    name        = $nic.Name
    status      = $nic.Status.ToString()
    link_speed  = $nic.LinkSpeed
    ssid        = $ssid
    band        = $band
    channel     = $channel
    rssi_dbm    = $rssiDbm
    dns_servers = @($dnsServers)
}

# 2. Locale-independent NIC & Power State
function Get-FirstIntRegistryValue {
    param($Prop)
    if ($null -eq $Prop -or $null -eq $Prop.RegistryValue) { return $null }
    $vals = @($Prop.RegistryValue | Where-Object { $null -ne $_ -and -not [string]::IsNullOrWhiteSpace([string]$_) })
    if ($vals.Count -eq 0) { return $null }
    $parsed = 0
    if ([int]::TryParse([string]$vals[0], [ref]$parsed)) {
        return $parsed
    }
    return $null
}

$advProps = @(Get-NetAdapterAdvancedProperty -Name $nic.Name -IncludeHidden -ErrorAction SilentlyContinue)
$lpsProp = $advProps | Where-Object RegistryKeyword -eq 'LpsEn' | Select-Object -First 1
$roamProp = $advProps | Where-Object RegistryKeyword -eq 'RegROAMSensitiveLevel' | Select-Object -First 1

$lpsEn = Get-FirstIntRegistryValue -Prop $lpsProp
$roamLevel = Get-FirstIntRegistryValue -Prop $roamProp

$msPowerDev = $null
if (-not [string]::IsNullOrWhiteSpace([string]$nic.PnPDeviceID)) {
    $msPowerDev = Get-CimInstance -Namespace 'root\wmi' -ClassName 'MSPower_DeviceEnable' -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceName -like "$($nic.PnPDeviceID)*" } |
        Select-Object -First 1
}
$msPowerEnable = if ($null -ne $msPowerDev -and $null -ne $msPowerDev.PSObject.Properties['Enable']) { [bool]$msPowerDev.Enable } else { $null }

$pnpCap = $null
$nicGuidB = ([guid]::Parse([string]$nic.InterfaceGuid)).ToString('B')
$classBase = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
Get-ChildItem -Path $classBase -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' } | ForEach-Object {
    $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
    if ($props -and $null -ne $props.PSObject.Properties['NetCfgInstanceId'] -and $props.NetCfgInstanceId -and ($props.NetCfgInstanceId.ToString().Equals($nicGuidB, [System.StringComparison]::OrdinalIgnoreCase))) {
        if ($null -ne $props.PSObject.Properties['PnPCapabilities']) {
            $pnpCap = [int]$props.PnPCapabilities
        }
    }
}

function Get-PowerCfgDcValue {
    param(
        [Parameter(Mandatory)][string]$SubGuid,
        [Parameter(Mandatory)][string]$SettingGuid
    )
    $qOut = (& powercfg /q SCHEME_CURRENT $SubGuid $SettingGuid 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    $hexMatches = [regex]::Matches($qOut, '0x([0-9a-fA-F]+)')
    if ($hexMatches.Count -ge 2) {
        return [Convert]::ToInt32($hexMatches[1].Groups[1].Value, 16)
    }
    elseif ($hexMatches.Count -eq 1) {
        return [Convert]::ToInt32($hexMatches[0].Groups[1].Value, 16)
    }
    return $null
}

$dcWirelessPs = Get-PowerCfgDcValue -SubGuid '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1' -SettingGuid '12bbebe6-58d6-4636-95bb-3217ef867c1a'
$dcPcieAspm = Get-PowerCfgDcValue -SubGuid '501a4d13-42af-4429-9fd1-a8218c268e20' -SettingGuid 'ee12f906-d277-404b-b6da-e5fa1a576df5'

$nicPowerState = [PSCustomObject]@{
    lps_en                 = $lpsEn
    roam_level             = $roamLevel
    ms_power_device_enable = $msPowerEnable
    pnp_capabilities       = $pnpCap
    dc_wireless_ps         = $dcWirelessPs
    dc_pcie_aspm           = $dcPcieAspm
}

# 3. .NET Ping JIT Warmup + Pre-Gateway Idle Wait + ICMP Probes
$ping = [System.Net.NetworkInformation.Ping]::new()
try {
    try { $null = $ping.Send('127.0.0.1', 500) } catch {}

    if ($IdleWaitSec -gt 0) {
        Start-Sleep -Seconds $IdleWaitSec
    }

    if ([string]::IsNullOrWhiteSpace($gateway)) {
        $gatewayPing = [PSCustomObject]@{
            target = $null; packet1_ms = $null; min_ms = $null; avg_ms = $null; p50_ms = $null
            p95_ms = $null; p99_ms = $null; max_ms = $null; jitter_ms = $null; loss_pct = 100.0
        }
    }
    else {
        $gatewayPing = Measure-IcmpTarget -Target $gateway -Count $PingCount -Timeout $TimeoutMs -PingClient $ping
    }

    $publicTargets = @('9.9.9.9', '8.8.8.8', '1.1.1.1')
    $publicPings = @()
    foreach ($tgt in $publicTargets) {
        $publicPings += Measure-IcmpTarget -Target $tgt -Count $PingCount -Timeout $TimeoutMs -PingClient $ping
    }
}
finally {
    $ping.Dispose()
}

# 4. DNS Benchmark (with DnsClient warmup)
Import-Module DnsClient -ErrorAction SilentlyContinue
$null = Resolve-DnsName -Name 'localhost' -DnsOnly -NoHostsFile -ErrorAction SilentlyContinue

$tcpIfacePath = "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$nicGuidB"
$tcpReg = Get-ItemProperty -Path $tcpIfacePath -ErrorAction SilentlyContinue
$dhcpDnsList = @()
if ($tcpReg -and $null -ne $tcpReg.PSObject.Properties['DhcpNameServer'] -and -not [string]::IsNullOrWhiteSpace([string]$tcpReg.DhcpNameServer)) {
    $dhcpDnsList = @(([string]$tcpReg.DhcpNameServer) -split '\s+' | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' })
}

$dnsTargetServers = [System.Collections.Generic.List[string]]::new()
foreach ($srv in @('9.9.9.9', '8.8.8.8', '1.1.1.1')) {
    if (-not $dnsTargetServers.Contains($srv)) { $dnsTargetServers.Add($srv) }
}
foreach ($srv in ($dnsServers + $dhcpDnsList)) {
    if (-not [string]::IsNullOrWhiteSpace($srv) -and $srv -match '^\d+\.\d+\.\d+\.\d+$' -and -not $dnsTargetServers.Contains($srv)) {
        $dnsTargetServers.Add($srv)
    }
}

$domains = @('cloudflare.com', 'google.com', 'epicgames.com', 'steampowered.com', 'riotgames.com')
$dnsRounds = 2
$dnsBench = [System.Collections.Generic.List[object]]::new()
$dnsSw = [System.Diagnostics.Stopwatch]::new()

foreach ($srv in $dnsTargetServers) {
    $latencies = [System.Collections.Generic.List[double]]::new()
    $totalQueries = $domains.Count * $dnsRounds
    $successQueries = 0

    for ($r = 1; $r -le $dnsRounds; $r++) {
        foreach ($dom in $domains) {
            $dnsSw.Restart()
            $ok = $false
            try {
                $null = Resolve-DnsName -Name $dom -Server $srv -Type A -DnsOnly -NoHostsFile -ErrorAction Stop
                $ok = $true
            }
            catch {
                $ok = $false
            }
            $dnsSw.Stop()
            if ($ok) {
                $successQueries++
                $latencies.Add([Math]::Round($dnsSw.Elapsed.TotalMilliseconds, 3))
            }
        }
    }

    $dnsLoss = [Math]::Round((($totalQueries - $successQueries) / [double]$totalQueries) * 100.0, 2)
    if ($latencies.Count -eq 0) {
        $dnsBench.Add([PSCustomObject]@{
            server         = $srv
            is_current_dns = ($dnsServers -contains $srv)
            avg_ms         = $null
            p95_ms         = $null
            min_ms         = $null
            max_ms         = $null
            loss_pct       = $dnsLoss
        })
    }
    else {
        $sortedDns = @($latencies | Sort-Object)
        $sumDns = 0.0
        foreach ($v in $latencies) { $sumDns += $v }
        $dnsBench.Add([PSCustomObject]@{
            server         = $srv
            is_current_dns = ($dnsServers -contains $srv)
            avg_ms         = [Math]::Round($sumDns / $latencies.Count, 3)
            p95_ms         = Get-Percentile -SortedValues $sortedDns -Percentile 95
            min_ms         = [Math]::Round($sortedDns[0], 3)
            max_ms         = [Math]::Round($sortedDns[$sortedDns.Count - 1], 3)
            loss_pct       = $dnsLoss
        })
    }
}

# 5. Connectivity Verification
$connProfile = Get-NetConnectionProfile -InterfaceIndex $nic.InterfaceIndex -ErrorAction SilentlyContinue | Select-Object -First 1
$hasInternetProfile = ($null -ne $connProfile -and $null -ne $connProfile.PSObject.Properties['IPv4Connectivity'] -and $null -ne $connProfile.IPv4Connectivity -and $connProfile.IPv4Connectivity.ToString() -eq 'Internet')
$gwOk = ($null -ne $gatewayPing.loss_pct -and $gatewayPing.loss_pct -lt 100.0)
$pubOk = ((@($publicPings | Where-Object { $_.loss_pct -lt 100.0 })).Count -ge 1)
$dnsOk = ((@($dnsBench | Where-Object { $_.loss_pct -lt 100.0 })).Count -ge 1)

$tcp443Ok = $false
foreach ($hostIp in @('9.9.9.9', '8.8.8.8')) {
    $tcpClient = [System.Net.Sockets.TcpClient]::new()
    try {
        $iar = $tcpClient.BeginConnect($hostIp, 443, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(2000, $false)) {
            $tcpClient.EndConnect($iar)
            $tcp443Ok = $true
            break
        }
    }
    catch {
        $tcp443Ok = $false
    }
    finally {
        $tcpClient.Dispose()
    }
}

$connectivityOk = [bool](($nic.Status.ToString() -eq 'Up') -and $hasInternetProfile -and $gwOk -and $pubOk -and $dnsOk -and $tcp443Ok)

$result = [PSCustomObject]@{
    timestamp_utc   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    tag             = $Tag
    adapter         = $adapterObj
    gateway_ping    = $gatewayPing
    public_pings    = @($publicPings)
    dns_bench       = @($dnsBench)
    nic_power_state = $nicPowerState
    connectivity_ok = $connectivityOk
}

$outParent = Split-Path -Parent $Out
if ($outParent -and -not (Test-Path -LiteralPath $outParent)) {
    New-Item -ItemType Directory -Path $outParent -Force | Out-Null
}
$jsonOut = $result | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($Out, $jsonOut, $utf8NoBom)

Write-Host "[OK] Network benchmark ($Tag) saved -> $Out (connectivity_ok=$connectivityOk)"
exit 0
