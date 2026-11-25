"""Opaque-box, strictly read-only E2E probe and validation library for laptop_optimization.

Constraints:
- Uses only Python 3.12 stdlib + optional psutil/torch (never cupy/wmi/pywin32).
- Never modifies live Windows registry, power plans, services, tasks, adapters, DNS, WLAN,
  or implementation-owned directories (scripts/, backups/, reports/).
- All probes operate safely under Medium Integrity (IsAdmin: False).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import winreg

DEFAULT_PROJECT_ROOT = Path(r"c:\Users\Usuario\teamwork_projects\laptop_optimization")

# Exact Powercfg GUIDs verified on target Windows 11 Home 25H2 hardware
SUB_USB = "2a737441-1930-4402-8d77-b2bebba308a3"
SET_USB_SUSPEND = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"

SUB_WIRELESS = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1"
SET_WIRELESS_PS = "12bbebe6-58d6-4636-95bb-3217ef867c1a"

SUB_PCIE = "501a4d13-42af-4429-9fd1-a8218c268e20"
SET_PCIE_ASPM = "ee12f906-d277-404b-b6da-e5fa1a576df5"

SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
SET_PROC_MIN = "893dee8e-2bef-41e0-89c6-b55d0929964c"
SET_PROC_MAX = "bc5038f7-23e0-4960-96da-33abaf5935ec"
SET_PERF_BOOST_MODE = "be337238-0d82-4146-a960-4f3749d470c7"
SET_SYS_COOL_POL = "94d3a615-a899-4ac5-ae2b-e4d8f634367f"

GAMING_STABLE_SCHEME_GUID = "46476e86-f770-4378-857e-3a25bf0336b4"
BALANCED_SCHEME_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"

ULTIMATE_PERF_GUIDS = {
    "8ac65e32-075f-48d5-828b-02e93ad3c1f8",
    "bb7e893a-b185-4dfa-8895-72bd923714aa",
    "e9a42b02-d5df-448d-aa00-03f14749eb61",
}

VALID_LOW_LATENCY_DNS_PAIRS = {
    ("1.1.1.1", "1.0.0.1"),
    ("9.9.9.9", "8.8.8.8"),
}

NIC_CLASS_REG_PATH = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
DISPLAY_CLASS_REG_PATH = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

# NvAPI DRS Setting IDs verified via nvapi64.dll
NVAPI_DRS_POWER_MGMT = 0x1057EB71          # 1 = Prefer maximum performance
NVAPI_DRS_LOW_LATENCY = 0x007BA09E         # 1 = On / 1 pre-rendered frame
NVAPI_DRS_TEX_FILTER_QUAL = 0x00CE2691     # 0x14 (20) = High Performance
NVAPI_DRS_THREADED_OPT = 0x20C1221E        # 1 = On
NVAPI_DRS_SHADER_CACHE_MB = 0x00AC8497     # >= 0x2800 (10240 MB) or 0xffffffff (Unlimited)

TARGET_SERVICES_DISABLED = [
    "PCManager Service Store",
    "MapsBroker",
    "NetworkDaemon",
    "RemoteRegistry",
    "DiagTrack",
    "dmwappushservice",
    "SysMain",
    "RetailDemo",
    "WalletService",
    "PhoneSvc",
    "wisvc",
]
TARGET_SERVICES_DISABLED_OR_MISSING = ["Fax"]
TARGET_SERVICES_MANUAL_OR_DISABLED = ["WSearch", "TrkWks", "Spooler", "lfsvc"]

TARGET_STARTUP_BLOAT = [
    "EpicGamesLauncher",
    "com.overwolf.curseforge",
    "Steam",
    "AMDNoiseSuppression",
    "OneDriveSetup",
    "utweb",
    "Docker Desktop",
]
TARGET_STARTUP_PREFIXES = ["MicrosoftEdgeAutoLaunch_"]
TARGET_RUN_ONLY_BLOAT = ["Free Download Manager", "nefarius_HidHide_Updater"]

TARGET_OVERLAY_PROCESSES = [
    "NVIDIA Overlay",
    "AMDRSSrcExt",
    "RadeonSoftware",
    "fdm",
    "steamwebhelper",
    "MSPCManagerService",
]
TARGET_P2P_PROCESSES = ["utweb", "fdm", "qbittorrent", "utorrent"]

TARGET_TASKS_DISABLED = [
    ("\\Microsoft\\Windows\\Application Experience\\", "MareBackup"),
    ("\\Microsoft\\Windows\\Application Experience\\", "Microsoft Compatibility Appraiser Exp"),
    ("\\", "AMDRyzenMasterSDKTask"),
    ("\\", "StartCN"),
    ("\\", "StartDVR"),
    ("\\SoftLanding\\", "SoftLandingCreativeManagementTask"),
]
TARGET_TASK_PREFIXES_DISABLED = [
    "NvDriverUpdateCheckDaily",
    "NVIDIA App SelfUpdate",
    "OneDrive Reporting Task",
    "OneDrive Standalone Update Task",
    "OneDrive Startup Task",
]


def is_target_disabled_task(task_path: str, task_name: str) -> bool:
    for pfx_path, name in TARGET_TASKS_DISABLED:
        if task_name == name and (task_path == pfx_path or (pfx_path != "\\" and task_path.startswith(pfx_path))):
            return True
    return any(task_name.startswith(pfx) for pfx in TARGET_TASK_PREFIXES_DISABLED)


@dataclass(frozen=True)
class TestCase:
    id: str
    tier: int
    feature: str
    requirement: str
    name: str
    fn: Callable[["ProbeContext"], tuple[bool, str]]


@dataclass
class TestResult:
    id: str
    tier: int
    feature: str
    requirement: str
    name: str
    status: str  # "PASS", "FAIL", "ERROR"
    duration_ms: float
    detail: str


class ProbeContext:
    """Per-run memoized read-only probe context bound to a project_root."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self._cache: dict[str, Any] = {}

    def cached(self, key: str, loader: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = loader()
        return self._cache[key]

    def pwsh_snapshot(self) -> dict[str, Any]:
        return self.cached("pwsh_snapshot", run_pwsh_batch_snapshot)

    def power_scheme(self) -> dict[str, Any]:
        return self.cached("power_scheme", query_powercfg_active_scheme)

    def power_available(self) -> dict[str, Any]:
        return self.cached("power_available", query_powercfg_available)

    def powercfg_setting(self, subgroup: str, setting: str) -> dict[str, Any]:
        return self.cached(
            f"powercfg:{subgroup}:{setting}",
            lambda: query_powercfg_setting(subgroup, setting),
        )

    def nvapi_drs(self) -> dict[str, Any]:
        return self.cached(
            "nvapi_drs",
            lambda: query_nvapi_drs_base([
                NVAPI_DRS_POWER_MGMT,
                NVAPI_DRS_LOW_LATENCY,
                NVAPI_DRS_TEX_FILTER_QUAL,
                NVAPI_DRS_THREADED_OPT,
                NVAPI_DRS_SHADER_CACHE_MB,
            ]),
        )

    def nvidia_smi(self) -> dict[str, Any]:
        return self.cached("nvidia_smi", query_nvidia_smi)

    def live_pings(self) -> dict[str, dict[str, Any]]:
        return self.cached("live_pings", run_default_live_pings)

    def live_dns(self) -> dict[str, dict[str, Any]]:
        return self.cached("live_dns", run_default_live_dns)

    def live_https(self) -> dict[str, Any]:
        return self.cached(
            "live_https",
            lambda: run_live_https_probe("https://www.google.com/generate_204"),
        )


def read_reg_value(hive: int, subkey: str, name: str) -> tuple[bool, Any, int | None]:
    """Read-only registry value lookup via winreg. Never throws on missing keys/values."""
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            val, rtype = winreg.QueryValueEx(key, name)
            return True, val, rtype
    except FileNotFoundError:
        return False, None, None
    except OSError:
        return False, None, None


def reg_key_exists(hive: int, subkey: str) -> bool:
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except OSError:
        return False


def enum_reg_values(hive: int, subkey: str) -> dict[str, tuple[Any, int]]:
    out: dict[str, tuple[Any, int]] = {}
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            i = 0
            while True:
                try:
                    name, val, rtype = winreg.EnumValue(key, i)
                    out[name] = (val, rtype)
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def enum_reg_subkeys(hive: int, subkey: str) -> list[str]:
    out: list[str] = []
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def find_nic_class_subkey(interface_guid: str) -> str | None:
    """Dynamically locate HKLM NIC Class 00xx subkey matching NetCfgInstanceId == interface_guid."""
    if not interface_guid:
        return None
    target = interface_guid.strip().lower()
    for sub in enum_reg_subkeys(winreg.HKEY_LOCAL_MACHINE, NIC_CLASS_REG_PATH):
        if not sub.isdigit():
            continue
        full_sub = f"{NIC_CLASS_REG_PATH}\\{sub}"
        exists, val, _ = read_reg_value(winreg.HKEY_LOCAL_MACHINE, full_sub, "NetCfgInstanceId")
        if exists and isinstance(val, str) and val.strip().lower() == target:
            return full_sub
    return None


def find_display_class_subkey(desc_substring: str) -> str | None:
    """Dynamically locate HKLM Display Class 00xx subkey matching DriverDesc substring."""
    needle = desc_substring.lower()
    for sub in enum_reg_subkeys(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS_REG_PATH):
        if not sub.isdigit():
            continue
        full_sub = f"{DISPLAY_CLASS_REG_PATH}\\{sub}"
        exists, val, _ = read_reg_value(winreg.HKEY_LOCAL_MACHINE, full_sub, "DriverDesc")
        if exists and isinstance(val, str) and needle in val.lower():
            return full_sub
    return None


def is_startup_approved_disabled(raw_bytes: Any) -> bool:
    """Windows StartupApproved\\Run binary entry: odd byte[0] (e.g. 0x03, 0x01) means disabled."""
    if isinstance(raw_bytes, (bytes, bytearray)) and len(raw_bytes) >= 1:
        return (raw_bytes[0] & 0x01) == 1
    return False


def is_gaming_stable_scheme(sch: dict[str, Any] | None) -> bool:
    """Return True if active power scheme is Gaming Estable (Sin Bateria) (46476e86-...)."""
    if not isinstance(sch, dict) or not sch.get("ok", True):
        return False
    guid = (sch.get("guid") or "").strip().lower()
    name = (sch.get("name") or "").strip().lower()
    return guid == GAMING_STABLE_SCHEME_GUID or "gaming estable" in name


def is_valid_static_gaming_dns(srvs: list[str] | tuple[str, ...] | None) -> bool:
    """Accept valid low-latency static DNS pairs: 1.1.1.1/1.0.0.1 or 9.9.9.9/8.8.8.8."""
    if not srvs or len(srvs) < 2:
        return False
    pair = (str(srvs[0]).strip(), str(srvs[1]).strip())
    return pair in VALID_LOW_LATENCY_DNS_PAIRS


def check_fortnite_qos(snap: dict[str, Any]) -> tuple[bool, str]:
    """Verify Fortnite_Gaming_QoS DSCP=46 via Get-NetQosPolicy snapshot or HKLM Policies QoS."""
    qos = snap.get("fortnite_qos") if isinstance(snap, dict) else None
    if isinstance(qos, dict) and qos.get("Exists"):
        dscp = qos.get("DSCPAction")
        app = str(qos.get("AppPath") or "")
        ok = is_exact_int(dscp) and dscp == 46 and "fortniteclient-win64-shipping.exe" in app.lower()
        return ok, f"Get-NetQosPolicy DSCP={dscp}, AppPath={app!r}"
    reg_path = r"SOFTWARE\Policies\Microsoft\Windows\QoS\Fortnite_Gaming_QoS"
    ex_d, dscp_val, _ = read_reg_value(winreg.HKEY_LOCAL_MACHINE, reg_path, "DSCP Value")
    ex_a, app_val, _ = read_reg_value(winreg.HKEY_LOCAL_MACHINE, reg_path, "Application Name")
    if ex_d:
        ok = str(dscp_val).strip() == "46" and (not ex_a or "fortniteclient-win64-shipping.exe" in str(app_val).lower())
        return ok, f"HKLM QoS reg DSCP={dscp_val!r}, App={app_val!r}"
    return False, "Fortnite_Gaming_QoS not found via Get-NetQosPolicy or HKLM QoS registry"


def _run_cmd(args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Timeout after {timeout}s"
    except OSError as exc:
        return -1, "", str(exc)


def query_powercfg_active_scheme() -> dict[str, Any]:
    rc, out, err = _run_cmd(["powercfg", "/getactivescheme"], timeout=10.0)
    m = re.search(
        r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\s*\((.+)\)\s*$",
        out.strip(),
        re.MULTILINE,
    )
    return {
        "ok": rc == 0 and m is not None,
        "guid": m.group(1).lower() if m else None,
        "name": m.group(2).strip() if m else None,
        "raw": out.strip(),
        "error": err.strip(),
    }


def query_powercfg_setting(subgroup_guid: str, setting_guid: str) -> dict[str, Any]:
    rc, out, err = _run_cmd(
        ["powercfg", "/qh", "SCHEME_CURRENT", subgroup_guid, setting_guid],
        timeout=10.0,
    )
    ac_m = re.search(r"AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out, re.IGNORECASE)
    dc_m = re.search(r"DC Power Setting Index:\s*0x([0-9a-fA-F]+)", out, re.IGNORECASE)
    # Fallback for localized Windows where label might vary: look for two 0x indices after GUID
    if ac_m is None or dc_m is None:
        idx_matches = re.findall(r":\s*0x([0-9a-fA-F]{8})\b", out)
        ac_val = int(idx_matches[0], 16) if len(idx_matches) >= 1 else None
        dc_val = int(idx_matches[1], 16) if len(idx_matches) >= 2 else None
    else:
        ac_val = int(ac_m.group(1), 16)
        dc_val = int(dc_m.group(1), 16)
    return {
        "ok": rc == 0 and ac_val is not None and dc_val is not None,
        "ac": ac_val,
        "dc": dc_val,
        "raw": out.strip(),
        "error": err.strip(),
    }


def query_powercfg_available() -> dict[str, Any]:
    rc, out, err = _run_cmd(["powercfg", "/a"], timeout=10.0)
    lines = out.splitlines()
    # In powercfg /a, available states appear BEFORE "The following sleep states are not available"
    avail_section = []
    for line in lines:
        low = line.lower()
        if "not available" in low or "no están disponibles" in low:
            break
        avail_section.append(low)
    avail_text = "\n".join(avail_section)
    hib_avail = ("hibernate" in avail_text) or ("hibernar" in avail_text)
    s3_avail = "standby (s3)" in avail_text or "suspensión (s3)" in avail_text
    return {
        "ok": rc == 0,
        "hibernate_available": hib_avail,
        "s3_available": s3_avail,
        "raw": out.strip(),
        "error": err.strip(),
    }


def query_nvapi_drs_base(setting_ids: list[int]) -> dict[str, Any]:
    """Read-only NvAPI DRS query via nvapi64.dll ctypes (no elevation required)."""
    try:
        nv = ctypes.WinDLL("nvapi64.dll")
        qi = nv.nvapi_QueryInterface
        qi.restype = ctypes.c_void_p
        qi.argtypes = [ctypes.c_uint32]

        def resolve(fid: int, restype: Any, *argtypes: Any) -> Any:
            addr = qi(fid)
            if not addr:
                raise RuntimeError(f"NvAPI function 0x{fid:08X} unavailable")
            return ctypes.CFUNCTYPE(restype, *argtypes)(addr)

        nv_init = resolve(0x0150E828, ctypes.c_int)
        drs_create = resolve(0x0694D52E, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        drs_load = resolve(0x375DBD6B, ctypes.c_int, ctypes.c_void_p)
        drs_get_base = resolve(0xDA8466A0, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        drs_get_setting = resolve(
            0x73BF8338, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p
        )
        drs_destroy = resolve(0xDAD9CFF8, ctypes.c_int, ctypes.c_void_p)

        rc = nv_init()
        if rc != 0:
            return {"ok": False, "error": f"NvAPI_Initialize failed: {rc}", "settings": {}}

        sess = ctypes.c_void_p()
        rc = drs_create(ctypes.byref(sess))
        if rc != 0:
            return {"ok": False, "error": f"NvAPI_DRS_CreateSession failed: {rc}", "settings": {}}

        try:
            rc = drs_load(sess)
            if rc != 0:
                return {"ok": False, "error": f"NvAPI_DRS_LoadSettings failed: {rc}", "settings": {}}
            prof = ctypes.c_void_p()
            rc = drs_get_base(sess, ctypes.byref(prof))
            if rc != 0:
                return {"ok": False, "error": f"NvAPI_DRS_GetBaseProfile failed: {rc}", "settings": {}}

            # NVDRS_SETTING_V1 exact verified layout: version=(1<<16)|12320
            sz = 12320
            version_val = (1 << 16) | sz
            results: dict[int, dict[str, Any]] = {}
            for sid in setting_ids:
                buf = (ctypes.c_ubyte * sz)()
                u32s = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))
                u32s[0] = version_val
                src = drs_get_setting(sess, prof, sid, buf)
                if src == 0:
                    results[sid] = {
                        "found": True,
                        "status": 0,
                        "setting_type": int(u32s[1026]),
                        "is_current_predefined": bool(u32s[1028]),
                        "predefined_u32": int(u32s[1030]),
                        "current_u32": int(u32s[2055]),
                    }
                else:
                    results[sid] = {
                        "found": False,
                        "status": int(src),
                        "current_u32": None,
                    }
            return {"ok": True, "error": "", "settings": results}
        finally:
            drs_destroy(sess)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "settings": {}}


def query_nvidia_smi() -> dict[str, Any]:
    fields = [
        "name",
        "driver_version",
        "pstate",
        "clocks.gr",
        "clocks.mem",
        "clocks.max.gr",
        "power.draw",
        "temperature.gpu",
        "clocks_throttle_reasons.active",
    ]
    rc, out, err = _run_cmd(
        ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
        timeout=10.0,
    )
    if rc != 0 or not out.strip():
        return {"ok": False, "error": err.strip() or out.strip()}
    parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]
    if len(parts) < 9:
        return {"ok": False, "error": f"Unexpected nvidia-smi columns: {out.strip()}"}

    def to_float(s: str) -> float | None:
        try:
            return float(s)
        except ValueError:
            return None

    return {
        "ok": True,
        "name": parts[0],
        "driver_version": parts[1],
        "pstate": parts[2],
        "clock_gr_mhz": to_float(parts[3]),
        "clock_mem_mhz": to_float(parts[4]),
        "max_clock_gr_mhz": to_float(parts[5]),
        "power_w": to_float(parts[6]),
        "temp_c": to_float(parts[7]),
        "throttle_reasons": parts[8],
    }


def run_pwsh_batch_snapshot() -> dict[str, Any]:
    """Single batched non-admin PowerShell 7 query collecting read-only OS & Network state."""
    ps_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$res = @{}

$adapters = @(Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, LinkSpeed, InterfaceGuid, PnPDeviceID)
$res.adapters = $adapters

$adv = @(Get-NetAdapterAdvancedProperty -Name 'Wi-Fi','Ethernet' | Select-Object Name, RegistryKeyword, RegistryValue)
$res.adv_props = $adv

$res.ms_power_enable = @(Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceEnable | Where-Object { $_.InstanceName -match '8852|8168' } | Select-Object InstanceName, Enable)

$dg = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard
$res.device_guard = if ($dg) {
    @{
        VirtualizationBasedSecurityStatus = $dg.VirtualizationBasedSecurityStatus
        CodeIntegrityPolicyEnforcementStatus = $dg.CodeIntegrityPolicyEnforcementStatus
        SecurityServicesConfigured = @($dg.SecurityServicesConfigured)
        SecurityServicesRunning = @($dg.SecurityServicesRunning)
    }
} else { $null }

$cs = Get-CimInstance -ClassName Win32_ComputerSystem
$res.hypervisor_present = if ($cs) { [bool]$cs.HypervisorPresent } else { $null }

$svcNames = @('PCManager Service Store','MapsBroker','NetworkDaemon','RemoteRegistry','DiagTrack','dmwappushservice','SysMain','RetailDemo','WalletService','PhoneSvc','wisvc','Fax','WSearch','TrkWks','Spooler','lfsvc','DoSvc')
$svcs = @{}
foreach ($sn in $svcNames) {
    $s = Get-Service -Name $sn -ErrorAction SilentlyContinue
    if ($s) {
        $svcs[$sn] = @{ Exists = $true; Status = [string]$s.Status; StartType = [string]$s.StartType }
    } else {
        $svcs[$sn] = @{ Exists = $false; Status = 'NOT_FOUND'; StartType = 'NOT_FOUND' }
    }
}
$res.services = $svcs

$tasks = @(Get-ScheduledTask | Where-Object {
    $_.TaskPath -match 'Application Experience|SoftLanding' -or
    ($_.TaskPath -eq '\' -and $_.TaskName -in @('AMDRyzenMasterSDKTask','StartCN','StartDVR')) -or
    $_.TaskName -match 'NvDriverUpdateCheckDaily|NVIDIA App SelfUpdate|OneDrive'
} | Select-Object TaskPath, TaskName, @{N='State';E={[string]$_.State}})
$res.tasks = $tasks

$procTargets = @('NVIDIA Overlay','AMDRSSrcExt','RadeonSoftware','fdm','steam','steamwebhelper','MSPCManagerService','utweb','qbittorrent','utorrent')
$procs = @(Get-Process | Where-Object { $procTargets -contains $_.ProcessName } | Select-Object ProcessName, Id)
$res.processes = $procs

$res.dns_wifi = @(Get-DnsClientServerAddress -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 | Select-Object -ExpandProperty ServerAddresses)
$res.dns_eth = @(Get-DnsClientServerAddress -InterfaceAlias 'Ethernet' -AddressFamily IPv4 | Select-Object -ExpandProperty ServerAddresses)

$offGlobal = Get-NetOffloadGlobalSetting
$res.net_offload_global = if ($offGlobal) {
    @{
        ReceiveSideScaling = [string]$offGlobal.ReceiveSideScaling
        ReceiveSegmentCoalescing = [string]$offGlobal.ReceiveSegmentCoalescing
        TaskOffload = [string]$offGlobal.TaskOffload
    }
} else { $null }

$res.eth_checksum = @(Get-NetAdapterChecksumOffload -Name 'Ethernet' | Select-Object IpIPv4Enabled, TcpIPv4Enabled, TcpIPv6Enabled, UdpIPv4Enabled, UdpIPv6Enabled)
$res.eth_lso = @(Get-NetAdapterLso -Name 'Ethernet' | Select-Object V1IPv4Enabled, IPv4Enabled, IPv6Enabled)
$res.wifi_rsc = @(Get-NetAdapterRsc -Name 'Wi-Fi' -ErrorAction SilentlyContinue | Select-Object Name, IPv4Enabled, IPv6Enabled)

$qos = Get-NetQosPolicy -Name 'Fortnite_Gaming_QoS' -ErrorAction SilentlyContinue
$res.fortnite_qos = if ($qos) {
    @{
        Exists = $true
        Name = [string]$qos.Name
        DSCPAction = [int]$qos.DSCPAction
        AppPath = [string]$qos.AppPathNameMatchCondition
    }
} else {
    @{ Exists = $false }
}

$res.wlan_interfaces = (netsh wlan show interfaces 2>&1) -join "`n"
$res.wlan_settings = (netsh wlan show settings 2>&1) -join "`n"
$res.wlan_profile_claro1 = (netsh wlan show profile name="CLARO1_F3E3B9" 2>&1) -join "`n"
$res.wlan_profile_claro2 = (netsh wlan show profile name="CLARO2_F3E3B9" 2>&1) -join "`n"
$res.tcp_global = (netsh int tcp show global 2>&1) -join "`n"

$restoreEvents = @(Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='System Restore'} -MaxEvents 10 -ErrorAction SilentlyContinue | Select-Object Id, TimeCreated)
$res.restore_events = @($restoreEvents | ForEach-Object { @{ Id = $_.Id; TimeCreated = $_.TimeCreated.ToUniversalTime().ToString('o') } })

$start30 = (Get-Date).AddDays(-30)
$nv30 = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'; StartTime=$start30} -ErrorAction SilentlyContinue | Where-Object { $_.Id -in 13, 153 })
$res.nvlddmkm_30d_count = $nv30.Count

$kp30 = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41; StartTime=$start30} -ErrorAction SilentlyContinue)
$res.kp41_30d_count = $kp30.Count

$res | ConvertTo-Json -Depth 6 -Compress
"""
    rc, out, err = _run_cmd(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=45.0,
    )
    if rc != 0 or not out.strip():
        return {"ok": False, "error": err.strip() or f"pwsh exit {rc}"}
    try:
        data = json.loads(out)
        if not isinstance(data, dict):
            return {"ok": False, "error": f"Expected JSON object, got {type(data).__name__}"}
        data["ok"] = True
        return data
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSON parse error: {exc}", "raw": out[:300]}


def query_events_since(since_iso: str) -> dict[str, Any]:
    """Query System Event Log strictly AFTER since_iso UTC timestamp."""
    dt = parse_utc_iso(since_iso)
    if dt is None:
        return {
            "ok": False,
            "error": f"invalid since_iso: {since_iso!r}",
            "nvlddmkm_count": -1,
            "kernel_power_count": -1,
        }
    iso_norm = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$since = [DateTimeOffset]::Parse('{iso_norm}').UtcDateTime
$nv = @(Get-WinEvent -FilterHashtable @{{LogName='System'; ProviderName='nvlddmkm'; StartTime=$since}} -ErrorAction SilentlyContinue | Where-Object {{ $_.Id -in 13, 153 -and $_.TimeCreated.ToUniversalTime() -gt $since }})
$kp = @(Get-WinEvent -FilterHashtable @{{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41; StartTime=$since}} -ErrorAction SilentlyContinue | Where-Object {{ $_.TimeCreated.ToUniversalTime() -gt $since }})
@{{
    nvlddmkm_count = $nv.Count
    kernel_power_count = $kp.Count
}} | ConvertTo-Json -Compress
"""
    rc, out, err = _run_cmd(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=20.0,
    )
    if rc != 0 or not out.strip():
        return {"ok": False, "error": err.strip(), "nvlddmkm_count": -1, "kernel_power_count": -1}
    try:
        d = json.loads(out)
        if not isinstance(d, dict):
            return {"ok": False, "error": f"Expected JSON object, got {type(d).__name__}", "nvlddmkm_count": -1, "kernel_power_count": -1}
        d["ok"] = True
        return d
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": str(exc), "nvlddmkm_count": -1, "kernel_power_count": -1}


def compute_ping_stats(target: str, rtts: list[float], sent: int, lost: int | None = None) -> dict[str, Any]:
    """Compute latency percentiles, jitter, and packet loss from RTT samples."""
    if lost is None:
        lost = max(0, sent - len(rtts))
    loss_pct = round(100.0 * lost / sent, 2) if sent > 0 else 100.0
    if not rtts:
        return {
            "ok": True,
            "target": target,
            "sent": sent,
            "received": 0,
            "loss_pct": loss_pct,
            "packet1_ms": None,
            "min_ms": None,
            "avg_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
            "jitter_ms": None,
        }
    s = sorted(rtts)

    def pct(p: float) -> float:
        idx = min(len(s) - 1, max(0, int(math.ceil(p * len(s))) - 1))
        return round(s[idx], 3)

    diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
    jitter = round(sum(diffs) / len(diffs), 3) if diffs else 0.0
    return {
        "ok": True,
        "target": target,
        "sent": sent,
        "received": len(rtts),
        "loss_pct": loss_pct,
        "packet1_ms": round(rtts[0], 3),
        "min_ms": round(s[0], 3),
        "avg_ms": round(sum(rtts) / len(rtts), 3),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": round(s[-1], 3),
        "jitter_ms": jitter,
    }


def run_live_ping_burst(
    target: str, count: int = 30, interval_ms: int = 100, timeout_ms: int = 1500
) -> dict[str, Any]:
    """Genuine live .NET Ping + Stopwatch sub-ms probe via PowerShell 7."""
    if not isinstance(target, str) or not re.fullmatch(r"[0-9A-Za-z._:-]+", target):
        return {"ok": False, "target": str(target), "error": f"invalid target: {target!r}"}
    for pname, pval in (("count", count), ("interval_ms", interval_ms), ("timeout_ms", timeout_ms)):
        if not isinstance(pval, int) or isinstance(pval, bool) or pval <= 0:
            return {"ok": False, "target": target, "error": f"invalid {pname}: {pval!r}"}
    ps_script = f"""
$ErrorActionPreference = 'Stop'
$ping = [System.Net.NetworkInformation.Ping]::new()
$null = $ping.Send('127.0.0.1', 500)
$rtts = [System.Collections.Generic.List[double]]::new()
$sent = {count}
$lost = 0
for ($i = 0; $i -lt $sent; $i++) {{
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {{
        $reply = $ping.Send('{target}', {timeout_ms})
        $sw.Stop()
        if ($reply.Status -eq [System.Net.NetworkInformation.IPStatus]::Success) {{
            $rtts.Add([Math]::Round($sw.Elapsed.TotalMilliseconds, 3))
        }} else {{
            $lost++
        }}
    }} catch {{
        $sw.Stop()
        $lost++
    }}
    if ($i -lt ($sent - 1)) {{ Start-Sleep -Milliseconds {interval_ms} }}
}}
$ping.Dispose()
@{{ target = '{target}'; sent = $sent; lost = $lost; rtts = @($rtts) }} | ConvertTo-Json -Compress
"""
    rc, out, err = _run_cmd(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=max(20.0, count * (interval_ms + timeout_ms) / 1000.0 + 10.0),
    )
    if rc != 0 or not out.strip():
        return {"ok": False, "target": target, "error": err.strip() or f"exit {rc}"}
    try:
        raw = json.loads(out)
    except json.JSONDecodeError as exc:
        return {"ok": False, "target": target, "error": str(exc)}
    if not isinstance(raw, dict):
        return {"ok": False, "target": target, "error": f"Expected JSON object, got {type(raw).__name__}"}
    rtts: list[float] = [float(x) for x in (raw.get("rtts") or [])]
    sent = int(raw.get("sent", count))
    lost = int(raw.get("lost", sent))
    return compute_ping_stats(target, rtts, sent, lost)


def run_default_live_pings() -> dict[str, dict[str, Any]]:
    """Run live 30-sample ping bursts against gateway + Quad9 + Google + Cloudflare in one PowerShell call."""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$targets = @('192.168.1.1', '9.9.9.9', '8.8.8.8', '1.1.1.1')
$out = @{}
$ping = [System.Net.NetworkInformation.Ping]::new()
$null = $ping.Send('127.0.0.1', 500)
foreach ($t in $targets) {
    $rtts = [System.Collections.Generic.List[double]]::new()
    $sent = 30
    $lost = 0
    for ($i = 0; $i -lt $sent; $i++) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $reply = $ping.Send($t, 1500)
            $sw.Stop()
            if ($reply.Status -eq [System.Net.NetworkInformation.IPStatus]::Success) {
                $rtts.Add([Math]::Round($sw.Elapsed.TotalMilliseconds, 3))
            } else { $lost++ }
        } catch {
            $sw.Stop()
            $lost++
        }
        if ($i -lt ($sent - 1)) { Start-Sleep -Milliseconds 60 }
    }
    $out[$t] = @{ target = $t; sent = $sent; lost = $lost; rtts = @($rtts) }
}
$ping.Dispose()
$out | ConvertTo-Json -Depth 4 -Compress
"""
    rc, out, err = _run_cmd(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=45.0,
    )
    results: dict[str, dict[str, Any]] = {}
    if rc != 0 or not out.strip():
        for t in ("192.168.1.1", "9.9.9.9", "8.8.8.8", "1.1.1.1"):
            results[t] = {"ok": False, "target": t, "error": err.strip() or f"exit {rc}"}
        return results
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        for t in ("192.168.1.1", "9.9.9.9", "8.8.8.8", "1.1.1.1"):
            results[t] = {"ok": False, "target": t, "error": str(exc)}
        return results
    if not isinstance(parsed, dict):
        for t in ("192.168.1.1", "9.9.9.9", "8.8.8.8", "1.1.1.1"):
            results[t] = {"ok": False, "target": t, "error": f"Expected JSON object, got {type(parsed).__name__}"}
        return results

    for t, raw in parsed.items():
        if not isinstance(raw, dict):
            results[t] = {"ok": False, "target": t, "error": f"Expected object for {t}, got {type(raw).__name__}"}
            continue
        rtts = [float(x) for x in (raw.get("rtts") or [])]
        sent = int(raw.get("sent", 30))
        lost = int(raw.get("lost", sent))
        results[t] = compute_ping_stats(t, rtts, sent, lost)
    return results


def run_default_live_dns() -> dict[str, dict[str, Any]]:
    """Run live Resolve-DnsName with 1 warmup query before timing per DNS server."""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$servers = @('9.9.9.9', '8.8.8.8', '1.1.1.1')
$domains = @('google.com', 'cloudflare.com', 'steampowered.com')
$out = @{}
# 1 module/DNS warmup query (untimed)
$null = Resolve-DnsName -Name 'example.com' -Server '9.9.9.9' -DnsOnly -NoHostsFile -ErrorAction SilentlyContinue
foreach ($srv in $servers) {
    $times = [System.Collections.Generic.List[double]]::new()
    $ok = $true
    foreach ($dom in $domains) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $r = Resolve-DnsName -Name $dom -Server $srv -DnsOnly -NoHostsFile -ErrorAction Stop
            $sw.Stop()
            if (-not $r) { $ok = $false }
            $times.Add([Math]::Round($sw.Elapsed.TotalMilliseconds, 2))
        } catch {
            $sw.Stop()
            $ok = $false
        }
    }
    $out[$srv] = @{ ok = $ok; samples_ms = @($times) }
}
$out | ConvertTo-Json -Depth 4 -Compress
"""
    rc, out, err = _run_cmd(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=25.0,
    )
    results: dict[str, dict[str, Any]] = {}
    if rc != 0 or not out.strip():
        for s in ("9.9.9.9", "8.8.8.8", "1.1.1.1"):
            results[s] = {"ok": False, "error": err.strip() or f"exit {rc}"}
        return results
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        for s in ("9.9.9.9", "8.8.8.8", "1.1.1.1"):
            results[s] = {"ok": False, "error": str(exc)}
        return results
    if not isinstance(parsed, dict):
        for s in ("9.9.9.9", "8.8.8.8", "1.1.1.1"):
            results[s] = {"ok": False, "error": f"Expected JSON object, got {type(parsed).__name__}"}
        return results
    for s, d in parsed.items():
        if not isinstance(d, dict):
            results[s] = {"ok": False, "error": f"Expected object for {s}, got {type(d).__name__}"}
            continue
        samples = [float(x) for x in (d.get("samples_ms") or [])]
        if not samples:
            results[s] = {"ok": False, "samples_ms": [], "avg_ms": None, "p95_ms": None}
            continue
        srt = sorted(samples)
        p95 = srt[min(len(srt) - 1, max(0, int(math.ceil(0.95 * len(srt))) - 1))]
        results[s] = {
            "ok": bool(d.get("ok")) and len(samples) == 3,
            "samples_ms": samples,
            "avg_ms": round(sum(samples) / len(samples), 2),
            "p95_ms": round(p95, 2),
        }
    return results


def run_live_https_probe(
    url: str = "https://www.google.com/generate_204", timeout_s: float = 6.0
) -> dict[str, Any]:
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": "laptop-optimization-e2e/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            dt = round((time.perf_counter() - t0) * 1000.0, 2)
            return {
                "ok": resp.status in (200, 204),
                "status_code": resp.status,
                "latency_ms": dt,
                "url": url,
                "error": "",
            }
    except Exception as exc:
        dt = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": dt,
            "url": url,
            "error": str(exc),
        }


def check_ps_ast(ps1_path: Path) -> dict[str, Any]:
    """Parse a PowerShell script AST without executing it."""
    if not ps1_path.is_file():
        return {"ok": False, "exists": False, "errors": [f"Missing file: {ps1_path}"]}
    escaped = str(ps1_path).replace("'", "''")
    ps_script = f"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{escaped}', [ref]$tokens, [ref]$errors)
$params = @()
$ssp = $false
if ($ast -and $ast.ParamBlock) {{
    $params = @($ast.ParamBlock.Parameters | ForEach-Object {{ $_.Name.VariablePath.UserPath }})
    $cbAttrs = @($ast.ParamBlock.Attributes | Where-Object {{ $_.TypeName.Name -match '^(System\\.Management\\.Automation\\.)?CmdletBinding(Attribute)?$' }})
    foreach ($attr in $cbAttrs) {{
        foreach ($na in $attr.NamedArguments) {{
            if ($na.ArgumentName -eq 'SupportsShouldProcess') {{
                if ($na.ExpressionOmitted -or ($na.Argument -and $na.Argument.Extent.Text -match '^(?i)\\$true$')) {{
                    $ssp = $true
                }}
            }}
        }}
    }}
}}
@{{
    error_count = $errors.Count
    errors = @($errors | ForEach-Object {{ $_.Message }})
    supports_should_process = $ssp
    has_dry_run = ($params -contains 'DryRun')
    has_whatif = $ssp -or ($params -contains 'WhatIf')
    params = $params
}} | ConvertTo-Json -Compress
"""
    rc, out, err = _run_cmd(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=15.0,
    )
    if rc != 0 or not out.strip():
        return {"ok": False, "exists": True, "errors": [err.strip() or f"pwsh exit {rc}"]}
    try:
        d = json.loads(out)
        if not isinstance(d, dict):
            return {"ok": False, "exists": True, "errors": [f"Expected JSON object, got {type(d).__name__}"]}
        d["exists"] = True
        d["ok"] = int(d.get("error_count", 1)) == 0
        return d
    except json.JSONDecodeError as exc:
        return {"ok": False, "exists": True, "errors": [str(exc)]}


def check_py_compile(py_path: Path) -> tuple[bool, str]:
    if not py_path.is_file():
        return False, f"Missing file: {py_path}"
    rc, out, err = _run_cmd([sys.executable, "-m", "py_compile", str(py_path)], timeout=10.0)
    if rc == 0:
        return True, "py_compile clean"
    return False, (err or out or f"py_compile failed rc={rc}").strip()


def read_text_auto(path: Path) -> str:
    """Read UTF-8 or UTF-16 (.reg) files cleanly."""
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8", errors="replace")


def is_valid_reg_export(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"Missing .reg file: {path}"
    sz = path.stat().st_size
    if sz < 40:
        return False, f".reg file too small ({sz} bytes): {path.name}"
    text = read_text_auto(path)
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if "Windows Registry Editor Version 5.00" not in first_line and "REGEDIT4" not in first_line:
        return False, f"Invalid .reg header in {path.name}: {first_line!r}"
    return True, f"Valid .reg ({sz} bytes)"


def parse_utc_iso(ts: str) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def is_exact_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_finite_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


_is_finite_num = is_finite_num


def validate_system_json(data: Any, expected_tag: str | None = None) -> tuple[bool, list[str]]:
    """Validate benchmark_system.py JSON against PROJECT.md § Interface Contracts."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return False, ["Root JSON must be an object"]

    required_top = {
        "timestamp_utc": str,
        "tag": str,
        "is_admin": bool,
        "system_overhead": dict,
        "timer_jitter_ms": dict,
        "cpu_bench": dict,
        "gpu_bench": dict,
        "disk_bench": dict,
        "os_state": dict,
        "event_errors": dict,
    }
    for k, typ in required_top.items():
        if k not in data:
            errors.append(f"Missing top-level key: {k}")
        elif not isinstance(data[k], typ):
            errors.append(f"Key {k} expected {typ.__name__}, got {type(data[k]).__name__}")

    if "timestamp_utc" in data and parse_utc_iso(data["timestamp_utc"]) is None:
        errors.append(f"Invalid timestamp_utc: {data.get('timestamp_utc')!r}")
    if expected_tag and data.get("tag") != expected_tag:
        errors.append(f"Expected tag={expected_tag!r}, got {data.get('tag')!r}")

    so = data.get("system_overhead")
    if isinstance(so, dict):
        for k in ("process_count", "available_ram_mb", "pagefile_used_mb", "ctx_switches"):
            if k not in so or not _is_finite_num(so[k]):
                errors.append(f"system_overhead.{k} missing or non-numeric")
            elif float(so[k]) < 0 or (k in ("process_count", "available_ram_mb") and float(so[k]) <= 0):
                errors.append(f"system_overhead.{k} out of bounds: {so[k]}")

    tj = data.get("timer_jitter_ms")
    if isinstance(tj, dict):
        vals = {}
        for k in ("mean", "p95", "p99", "max"):
            if k not in tj or not _is_finite_num(tj[k]) or float(tj[k]) < 0:
                errors.append(f"timer_jitter_ms.{k} invalid: {tj.get(k)!r}")
            else:
                vals[k] = float(tj[k])
        if len(vals) == 4 and (
            vals["mean"] > vals["max"] + 1e-6
            or vals["mean"] > vals["p95"] + 1e-6
            or vals["p95"] > vals["p99"] + 1e-6
            or vals["p99"] > vals["max"] + 1e-6
        ):
            errors.append(f"timer_jitter_ms quantiles non-monotonic or mean>max: {vals}")

    cb = data.get("cpu_bench")
    if isinstance(cb, dict):
        for k in ("single_thread_ms", "multi_thread_gflops"):
            if k not in cb or not _is_finite_num(cb[k]) or float(cb[k]) <= 0:
                errors.append(f"cpu_bench.{k} must be > 0, got {cb.get(k)!r}")

    gb = data.get("gpu_bench")
    if isinstance(gb, dict):
        if not isinstance(gb.get("device_name"), str) or not gb.get("device_name", "").strip():
            errors.append("gpu_bench.device_name missing/empty")
        for k in ("cuda_fp32_tflops", "h2d_gbps", "kernel_launch_us", "avg_clock_mhz", "max_power_w", "max_temp_c"):
            if k not in gb or not _is_finite_num(gb[k]) or float(gb[k]) <= 0:
                errors.append(f"gpu_bench.{k} must be > 0, got {gb.get(k)!r}")
        if "throttle_reasons" not in gb:
            errors.append("gpu_bench.throttle_reasons missing")

    db = data.get("disk_bench")
    if isinstance(db, dict):
        for k in ("seq_read_mbps", "seq_write_mbps", "rand4k_read_iops"):
            if k not in db or not _is_finite_num(db[k]) or float(db[k]) <= 0:
                errors.append(f"disk_bench.{k} must be > 0, got {db.get(k)!r}")

    os_st = data.get("os_state")
    if isinstance(os_st, dict):
        for k in ("active_power_plan", "usb_suspend_ac", "gamedvr_enabled", "transparency_enabled", "hiberfil_exists", "vbs_status"):
            if k not in os_st:
                errors.append(f"os_state.{k} missing")

    ev = data.get("event_errors")
    if isinstance(ev, dict):
        for k in ("nvlddmkm_count", "kernel_power_count", "scm_error_count"):
            if k not in ev or not _is_finite_num(ev[k]) or float(ev[k]) < 0:
                errors.append(f"event_errors.{k} must be >= 0, got {ev.get(k)!r}")

    return len(errors) == 0, errors


def _validate_ping_obj(obj: Any, label: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(obj, dict):
        return [f"{label} must be an object"]
    if not isinstance(obj.get("target"), str) or not obj["target"].strip():
        errs.append(f"{label}.target missing")
    num_keys = ("packet1_ms", "min_ms", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "jitter_ms", "loss_pct")
    for k in num_keys:
        if k not in obj or not _is_finite_num(obj[k]):
            errs.append(f"{label}.{k} missing or non-numeric")
    if all(k in obj and _is_finite_num(obj[k]) for k in num_keys):
        loss = float(obj["loss_pct"])
        if not (0.0 <= loss <= 100.0):
            errs.append(f"{label}.loss_pct out of [0,100]: {loss}")
        if float(obj["jitter_ms"]) < 0:
            errs.append(f"{label}.jitter_ms < 0: {obj['jitter_ms']}")
        if loss < 100.0:
            mn = float(obj["min_ms"])
            avg = float(obj["avg_ms"])
            p50 = float(obj["p50_ms"])
            p95 = float(obj["p95_ms"])
            p99 = float(obj["p99_ms"])
            mx = float(obj["max_ms"])
            p1 = float(obj["packet1_ms"])
            if mn <= 0:
                errs.append(f"{label}.min_ms must be > 0, got {mn}")
            if not (mn - 1e-4 <= avg <= mx + 1e-4):
                errs.append(f"{label}.avg_ms ({avg}) outside [min={mn}, max={mx}]")
            if not (mn - 1e-4 <= p50 <= p95 + 1e-4 <= p99 + 2e-4 <= mx + 3e-4):
                errs.append(f"{label} ping quantiles non-monotonic: min={mn}, p50={p50}, p95={p95}, p99={p99}, max={mx}")
            if p1 < mn - 1e-4 or p1 > mx + 1e-4:
                errs.append(f"{label}.packet1_ms ({p1}) outside [min={mn}, max={mx}]")
    return errs


def validate_network_json(data: Any, expected_tag: str | None = None) -> tuple[bool, list[str]]:
    """Validate benchmark_network.ps1 JSON against PROJECT.md § Interface Contracts."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return False, ["Root JSON must be an object"]

    required_top = {
        "timestamp_utc": str,
        "tag": str,
        "adapter": dict,
        "gateway_ping": dict,
        "public_pings": list,
        "nic_power_state": dict,
        "connectivity_ok": bool,
    }
    for k, typ in required_top.items():
        if k not in data:
            errors.append(f"Missing top-level key: {k}")
        elif not isinstance(data[k], typ):
            errors.append(f"Key {k} expected {typ.__name__}, got {type(data[k]).__name__}")

    if "dns_bench" not in data or not isinstance(data["dns_bench"], (dict, list)):
        errors.append("Missing or invalid dns_bench (expected dict or list)")

    if "timestamp_utc" in data and parse_utc_iso(data["timestamp_utc"]) is None:
        errors.append(f"Invalid timestamp_utc: {data.get('timestamp_utc')!r}")
    if expected_tag and data.get("tag") != expected_tag:
        errors.append(f"Expected tag={expected_tag!r}, got {data.get('tag')!r}")

    ad = data.get("adapter")
    if isinstance(ad, dict):
        for k in ("name", "status", "link_speed", "ssid", "band", "channel", "rssi_dbm", "dns_servers"):
            if k not in ad:
                errors.append(f"adapter.{k} missing")

    if isinstance(data.get("gateway_ping"), dict):
        errors.extend(_validate_ping_obj(data["gateway_ping"], "gateway_ping"))

    pubs = data.get("public_pings")
    if isinstance(pubs, list):
        targets_seen = set()
        for idx, item in enumerate(pubs):
            errors.extend(_validate_ping_obj(item, f"public_pings[{idx}]"))
            if isinstance(item, dict) and isinstance(item.get("target"), str):
                targets_seen.add(item["target"])
        for req_ip in ("9.9.9.9", "8.8.8.8", "1.1.1.1"):
            if req_ip not in targets_seen:
                errors.append(f"public_pings missing target {req_ip}")

    dns = data.get("dns_bench")
    if isinstance(dns, dict):
        if len(dns) == 0:
            errors.append("dns_bench dict is empty")
        for srv, vals in dns.items():
            if not isinstance(vals, dict) or not _is_finite_num(vals.get("avg_ms")) or not _is_finite_num(vals.get("p95_ms")):
                errors.append(f"dns_bench[{srv}] missing numeric avg_ms/p95_ms")
            elif float(vals["avg_ms"]) <= 0 or float(vals["p95_ms"]) <= 0:
                errors.append(f"dns_bench[{srv}] avg_ms/p95_ms must be > 0")
    elif isinstance(dns, list):
        if len(dns) == 0:
            errors.append("dns_bench list is empty")
        for idx, item in enumerate(dns):
            if not isinstance(item, dict):
                errors.append(f"dns_bench[{idx}] must be an object")
                continue
            if not isinstance(item.get("server"), str) or not item.get("server", "").strip():
                errors.append(f"dns_bench[{idx}] missing/empty 'server'")
            if not _is_finite_num(item.get("avg_ms")) or not _is_finite_num(item.get("p95_ms")):
                errors.append(f"dns_bench[{idx}] missing numeric avg_ms/p95_ms")
            elif float(item["avg_ms"]) <= 0 or float(item["p95_ms"]) <= 0:
                errors.append(f"dns_bench[{idx}] avg_ms/p95_ms must be > 0")

    nps = data.get("nic_power_state")
    if isinstance(nps, dict):
        for k in ("lps_en", "roam_level", "ms_power_device_enable", "pnp_capabilities", "dc_wireless_ps", "dc_pcie_aspm"):
            if k not in nps:
                errors.append(f"nic_power_state.{k} missing")

    return len(errors) == 0, errors


def load_json_file(path: Path) -> tuple[bool, Any, str]:
    if not path.is_file():
        return False, None, f"Missing file: {path}"
    try:
        text = path.read_text(encoding="utf-8-sig")
        return True, json.loads(text), ""
    except Exception as exc:
        return False, None, f"Invalid JSON in {path.name}: {exc}"


class TempProjectSandbox:
    """Isolated temporary project root for negative, schema, and non-destructive CLI tests."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProjectSandbox":
        kwargs: dict[str, Any] = {"prefix": "laptop_opt_e2e_"}
        if self._base_dir is not None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            kwargs["dir"] = str(self._base_dir)
        self._tmp = tempfile.TemporaryDirectory(**kwargs)
        self.root = Path(self._tmp.name)
        for sub in ("backups/system", "backups/power", "backups/network", "scripts", "reports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None
            self.root = None

    @staticmethod
    def sample_system_json(tag: str = "baseline", ts: str = "2026-11-21T16:00:00Z") -> dict[str, Any]:
        return {
            "timestamp_utc": ts,
            "tag": tag,
            "is_admin": False,
            "system_overhead": {
                "process_count": 180 if tag == "baseline" else 145,
                "available_ram_mb": 12500.0 if tag == "baseline" else 14200.0,
                "pagefile_used_mb": 1024.0,
                "ctx_switches": 12000,
            },
            "timer_jitter_ms": (
                {"mean": 0.45, "p95": 0.90, "p99": 1.10, "max": 1.50}
                if tag == "baseline"
                else {"mean": 0.31, "p95": 0.62, "p99": 0.85, "max": 1.15}
            ),
            "cpu_bench": (
                {"single_thread_ms": 420.0, "multi_thread_gflops": 185.0}
                if tag == "baseline"
                else {"single_thread_ms": 395.0, "multi_thread_gflops": 205.0}
            ),
            "gpu_bench": {
                "device_name": "NVIDIA GeForce GTX 1650",
                "cuda_fp32_tflops": 2.95 if tag == "baseline" else 3.08,
                "h2d_gbps": 11.2 if tag == "baseline" else 11.5,
                "kernel_launch_us": 6.5 if tag == "baseline" else 5.8,
                "avg_clock_mhz": 1725.0 if tag == "baseline" else 1785.0,
                "max_power_w": 50.0,
                "max_temp_c": 64.0,
                "throttle_reasons": "0x0000000000000001",
            },
            "disk_bench": {"seq_read_mbps": 520.0, "seq_write_mbps": 460.0, "rand4k_read_iops": 35000.0},
            "os_state": {
                "active_power_plan": BALANCED_SCHEME_GUID if tag == "baseline" else GAMING_STABLE_SCHEME_GUID,
                "usb_suspend_ac": 0,
                "gamedvr_enabled": 1 if tag == "baseline" else 0,
                "transparency_enabled": 1 if tag == "baseline" else 0,
                "hiberfil_exists": tag == "baseline",
                "vbs_status": 2 if tag == "baseline" else 0,
            },
            "event_errors": {"nvlddmkm_count": 0, "kernel_power_count": 0, "scm_error_count": 0},
        }

    @staticmethod
    def sample_network_json(tag: str = "baseline", ts: str = "2026-11-21T16:01:00Z") -> dict[str, Any]:
        p1 = 22.4 if tag == "baseline" else 2.1
        return {
            "timestamp_utc": ts,
            "tag": tag,
            "adapter": {
                "name": "Wi-Fi",
                "status": "Up",
                "link_speed": "866.7 Mbps",
                "ssid": "CLARO2_F3E3B9",
                "band": "5 GHz",
                "channel": 36,
                "rssi_dbm": -62,
                "dns_servers": ["1.1.1.1", "1.0.0.1"] if tag == "baseline" else ["9.9.9.9", "8.8.8.8"],
            },
            "gateway_ping": {
                "target": "192.168.1.1",
                "packet1_ms": p1,
                "min_ms": 1.2,
                "avg_ms": 4.1 if tag == "baseline" else 2.0,
                "p50_ms": 2.0,
                "p95_ms": 18.0 if tag == "baseline" else 3.5,
                "p99_ms": 22.4 if tag == "baseline" else 4.2,
                "max_ms": 22.4 if tag == "baseline" else 4.5,
                "jitter_ms": 2.8 if tag == "baseline" else 0.4,
                "loss_pct": 0.0,
            },
            "public_pings": [
                {"target": "9.9.9.9", "packet1_ms": 40.0 if tag == "baseline" else 39.2, "min_ms": 39.0, "avg_ms": 42.0 if tag == "baseline" else 40.5, "p50_ms": 41.0, "p95_ms": 46.0, "p99_ms": 48.0, "max_ms": 48.0, "jitter_ms": 1.5, "loss_pct": 0.0},
                {"target": "8.8.8.8", "packet1_ms": 46.0 if tag == "baseline" else 45.4, "min_ms": 45.0, "avg_ms": 47.0 if tag == "baseline" else 46.2, "p50_ms": 46.5, "p95_ms": 50.0, "p99_ms": 52.0, "max_ms": 52.0, "jitter_ms": 1.2, "loss_pct": 0.0},
                {"target": "1.1.1.1", "packet1_ms": 66.0 if tag == "baseline" else 65.5, "min_ms": 65.0, "avg_ms": 66.5 if tag == "baseline" else 66.0, "p50_ms": 66.2, "p95_ms": 68.0, "p99_ms": 69.0, "max_ms": 69.0, "jitter_ms": 0.9, "loss_pct": 0.0},
            ],
            "dns_bench": {
                "9.9.9.9": {"avg_ms": 41.5, "p95_ms": 44.0},
                "8.8.8.8": {"avg_ms": 49.0, "p95_ms": 53.0},
                "1.1.1.1": {"avg_ms": 68.0, "p95_ms": 70.0},
            },
            "nic_power_state": {
                "lps_en": 2 if tag == "baseline" else 0,
                "roam_level": 80 if tag == "baseline" else 127,
                "ms_power_device_enable": tag == "baseline",
                "pnp_capabilities": 16 if tag == "baseline" else 24,
                "dc_wireless_ps": 2 if tag == "baseline" else 0,
                "dc_pcie_aspm": 2 if tag == "baseline" else 0,
            },
            "connectivity_ok": True,
        }


def run_cli_process(args: list[str], timeout: float = 40.0) -> tuple[int, str, str]:
    return _run_cmd(args, timeout=timeout)
