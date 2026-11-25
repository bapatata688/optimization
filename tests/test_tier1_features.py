"""Tier 1: Feature Coverage Tests (>= 75 tests, >= 5 per feature F1-F15).

All tests are strictly opaque-box, read-only, non-destructive, and assert target optimized state.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import winreg

import e2e_lib as lib


def _svc_state(snap: dict, name: str) -> dict:
    svcs = snap.get("services") if isinstance(snap, dict) else None
    if not isinstance(svcs, dict):
        return {"Exists": False, "Status": "NOT_FOUND", "StartType": "NOT_FOUND"}
    st = svcs.get(name)
    return st if isinstance(st, dict) else {"Exists": False, "Status": "NOT_FOUND", "StartType": "NOT_FOUND"}


def _get_adapter_guid(snap: dict, alias: str) -> str | None:
    for ad in (snap.get("adapters") or []):
        if isinstance(ad, dict) and str(ad.get("Name") or "").lower() == alias.lower():
            return ad.get("InterfaceGuid")
    return None


def _get_adv_val(snap: dict, alias: str, keyword: str) -> str | None:
    for prop in (snap.get("adv_props") or []):
        if isinstance(prop, dict) and str(prop.get("Name") or "").lower() == alias.lower() and prop.get("RegistryKeyword") == keyword:
            rv = prop.get("RegistryValue")
            if isinstance(rv, list) and rv:
                return str(rv[0])
            if rv is not None:
                return str(rv)
    return None


def _get_ms_power_enable(snap: dict, pattern: str) -> bool | None:
    for item in (snap.get("ms_power_enable") or []):
        if not isinstance(item, dict):
            continue
        inst = str(item.get("InstanceName") or "")
        if pattern.lower() in inst.lower():
            en = item.get("Enable")
            return en if isinstance(en, bool) else None
    return None


# =============================================================================
# F1: Pre-Change System & Network Backups
# =============================================================================

def t1_f01_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    missing = [sub for sub in ("backups/system", "backups/power", "backups/network") if not (ctx.project_root / sub).is_dir()]
    if missing:
        return False, f"Missing backup directories: {missing}"
    return True, "All backup subdirectories (system, power, network) exist"


def t1_f01_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    sys_dir = ctx.project_root / "backups/system"
    net_dir = ctx.project_root / "backups/network"
    sys_regs = list(sys_dir.glob("*.reg")) if sys_dir.is_dir() else []
    if not sys_regs:
        return False, f"No .reg backup files in {sys_dir}"
    required_net_regs = ["wifi_driver_reg.reg", "eth_driver_reg.reg", "tcpip_params.reg"]
    bad = []
    for p in sys_regs:
        ok, msg = lib.is_valid_reg_export(p)
        if not ok:
            bad.append(msg)
    for rn in required_net_regs:
        ok, msg = lib.is_valid_reg_export(net_dir / rn)
        if not ok:
            bad.append(msg)
    if bad:
        return False, "; ".join(bad)
    return True, f"Verified {len(sys_regs)} system .reg + {len(required_net_regs)} network .reg backups"


def t1_f01_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    pwr_dir = ctx.project_root / "backups/power"
    if not pwr_dir.is_dir():
        return False, f"Missing {pwr_dir}"
    pows = [p for p in pwr_dir.glob("*.pow") if p.stat().st_size > 100]
    active_txt = pwr_dir / "power_active_before.txt"
    if not pows:
        return False, "No non-empty .pow scheme backup files found"
    if not active_txt.is_file() or active_txt.stat().st_size < 10:
        return False, f"Missing or empty {active_txt.name}"
    return True, f"Found {len(pows)} .pow backup(s) and power_active_before.txt"


def t1_f01_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    required_json = [
        ctx.project_root / "backups/system/services_baseline.json",
        ctx.project_root / "backups/system/tasks_baseline.json",
        ctx.project_root / "backups/system/startup_baseline.json",
        ctx.project_root / "backups/network/dns_before.json",
    ]
    required_txt = [
        ctx.project_root / "backups/network/nic_advanced_before.csv",
        ctx.project_root / "backups/network/wlan_profiles_before.txt",
    ]
    errs = []
    for jp in required_json:
        ok, data, msg = lib.load_json_file(jp)
        if not ok or not data:
            errs.append(msg or f"Empty JSON in {jp.name}")
    for tp in required_txt:
        if not tp.is_file() or tp.stat().st_size < 20:
            errs.append(f"Missing/empty {tp.name}")
    if errs:
        return False, "; ".join(errs)
    return True, "All baseline JSON/CSV/TXT snapshots exist and parse cleanly"


def t1_f01_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    rp_json = ctx.project_root / "backups/system/restore_point_info.json"
    ok, data, msg = lib.load_json_file(rp_json)
    if not ok:
        return False, msg
    if not isinstance(data, dict) or not data:
        return False, "restore_point_info.json is empty or non-object"
    req_keys = ("timestamp_utc", "is_admin", "checkpoint_status")
    missing = [k for k in req_keys if k not in data]
    if missing:
        return False, f"restore_point_info.json missing keys: {missing}"
    if lib.parse_utc_iso(data.get("timestamp_utc", "")) is None:
        return False, f"restore_point_info.json invalid timestamp_utc: {data.get('timestamp_utc')!r}"
    if not isinstance(data.get("is_admin"), bool):
        return False, f"restore_point_info.json is_admin must be bool, got {data.get('is_admin')!r}"
    if data.get("checkpoint_status") != "created":
        return False, f"restore_point_info.json checkpoint_status={data.get('checkpoint_status')!r} (expected 'created')"
    snap = ctx.pwsh_snapshot()
    ev_ids = [e.get("Id") for e in (snap.get("restore_events") or []) if isinstance(e, dict)] if snap.get("ok") else []
    return True, f"restore_point_info.json valid (status={data.get('checkpoint_status')}); System Restore events={ev_ids}"


# =============================================================================
# F2: System & GPU Benchmark Suite
# =============================================================================

def t1_f02_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    return lib.check_py_compile(ctx.project_root / "scripts/benchmark_system.py")


def t1_f02_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    if not ok:
        return False, msg
    valid, errs = lib.validate_system_json(data, expected_tag="baseline")
    return valid, "baseline_system.json schema valid" if valid else "; ".join(errs)


def t1_f02_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    smi = ctx.nvidia_smi()
    if not smi.get("ok"):
        return False, f"nvidia-smi failed: {smi.get('error')}"
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        dev_name = torch.cuda.get_device_name(0) if cuda_ok else "NONE"
    except Exception as exc:
        return False, f"torch CUDA probe failed: {exc}"
    if not cuda_ok or "GTX 1650" not in smi.get("name", ""):
        return False, f"CUDA={cuda_ok}, smi_name={smi.get('name')}"
    return True, f"GPU={dev_name}, temp={smi.get('temp_c')}C, pstate={smi.get('pstate')}"


def t1_f02_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    if not ok:
        return False, msg
    if not isinstance(data, dict):
        return False, f"baseline_system.json expected object, got {type(data).__name__}"
    cb = data.get("cpu_bench")
    gb = data.get("gpu_bench")
    db = data.get("disk_bench")
    if not isinstance(cb, dict) or not isinstance(gb, dict) or not isinstance(db, dict):
        return False, f"Invalid benchmark section types in baseline_system.json"
    vals = [
        cb.get("single_thread_ms"),
        cb.get("multi_thread_gflops"),
        gb.get("cuda_fp32_tflops"),
        gb.get("h2d_gbps"),
        db.get("seq_read_mbps"),
        db.get("seq_write_mbps"),
        db.get("rand4k_read_iops"),
    ]
    if not all(lib.is_finite_num(v) and float(v) > 0 for v in vals):
        return False, f"Non-positive or invalid benchmark values in baseline_system.json: cpu={cb}, gpu={gb}, disk={db}"
    return True, f"CPU={cb.get('multi_thread_gflops')} GFLOPS, GPU={gb.get('cuda_fp32_tflops')} TFLOPS, Disk={db.get('seq_read_mbps')} MB/s"


def t1_f02_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    if not ok:
        return False, msg
    if not isinstance(data, dict):
        return False, f"baseline_system.json expected object, got {type(data).__name__}"
    tj = data.get("timer_jitter_ms")
    if not isinstance(tj, dict):
        return False, f"timer_jitter_ms must be object, got {type(tj).__name__}"
    if not isinstance(data.get("is_admin"), bool):
        return False, f"is_admin not bool: {data.get('is_admin')!r}"
    raw_vals = [tj.get("mean"), tj.get("p95"), tj.get("p99"), tj.get("max")]
    if not all(lib.is_finite_num(v) for v in raw_vals):
        return False, f"Invalid/non-finite timer_jitter_ms fields: {tj}"
    vals = [float(v) for v in raw_vals]
    if not (0 <= vals[0] <= vals[1] <= vals[2] <= vals[3] + 1e-6):
        return False, f"Non-monotonic timer_jitter_ms: {tj}"
    return True, f"timer_jitter_ms monotonic: {tj}"


# =============================================================================
# F3: Network & Ping Benchmark Suite
# =============================================================================

def t1_f03_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ast_res = lib.check_ps_ast(ctx.project_root / "scripts/benchmark_network.ps1")
    if not ast_res.get("exists"):
        return False, "Missing scripts/benchmark_network.ps1"
    if not ast_res.get("ok"):
        return False, f"PS AST errors: {ast_res.get('errors')}"
    return True, f"benchmark_network.ps1 AST valid (params={ast_res.get('params')})"


def t1_f03_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/baseline_network.json")
    if not ok:
        return False, msg
    valid, errs = lib.validate_network_json(data, expected_tag="baseline")
    return valid, "baseline_network.json schema valid" if valid else "; ".join(errs)


def t1_f03_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/baseline_network.json")
    if not ok:
        return False, msg
    if not isinstance(data, dict):
        return False, f"baseline_network.json expected object, got {type(data).__name__}"
    gw_obj = data.get("gateway_ping")
    pub_list = data.get("public_pings")
    dns = data.get("dns_bench")
    if not isinstance(gw_obj, dict) or not isinstance(pub_list, list) or not isinstance(dns, (dict, list)):
        return False, "baseline_network.json missing/invalid gateway_ping, public_pings, or dns_bench"
    gw = gw_obj.get("target")
    pubs = {p.get("target") for p in pub_list if isinstance(p, dict)}
    missing = {"9.9.9.9", "8.8.8.8", "1.1.1.1"} - pubs
    if not gw or missing or not dns:
        return False, f"gw={gw}, missing_public={missing}, dns_keys={list(dns) if isinstance(dns, dict) else len(dns)}"
    return True, f"Covers gateway {gw}, public {sorted(pubs)}, dns_bench present"


def t1_f03_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    pings = ctx.live_pings()
    gw = pings.get("192.168.1.1") if isinstance(pings, dict) else None
    q9 = pings.get("9.9.9.9") if isinstance(pings, dict) else None
    gw_rec = gw.get("received") if isinstance(gw, dict) else None
    q9_rec = q9.get("received") if isinstance(q9, dict) else None
    if not isinstance(gw, dict) or not gw.get("ok") or not lib.is_exact_int(gw_rec) or gw_rec <= 0:
        return False, f"Live gateway ping failed: {gw}"
    if not isinstance(q9, dict) or not q9.get("ok") or not lib.is_exact_int(q9_rec) or q9_rec <= 0:
        return False, f"Live Quad9 ping failed: {q9}"
    return True, f"Live pings ok: gw_p1={gw.get('packet1_ms')}ms avg={gw.get('avg_ms')}ms; 9.9.9.9 avg={q9.get('avg_ms')}ms"


def t1_f03_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    dns = ctx.live_dns()
    https = ctx.live_https()
    q9 = dns.get("9.9.9.9") if isinstance(dns, dict) else None
    if not isinstance(q9, dict) or not q9.get("ok"):
        return False, f"Live DNS failed on 9.9.9.9: {q9}"
    if not isinstance(https, dict) or not https.get("ok"):
        return False, f"Live HTTPS probe failed: {https}"
    return True, f"DNS 9.9.9.9 avg={q9.get('avg_ms')}ms, HTTPS {https.get('status_code')} ({https.get('latency_ms')}ms)"


# =============================================================================
# F4: Rollback & Toggle Scripts
# =============================================================================

def t1_f04_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ast_res = lib.check_ps_ast(ctx.project_root / "scripts/rollback_all.ps1")
    if not ast_res.get("exists"):
        return False, "Missing scripts/rollback_all.ps1"
    if not ast_res.get("ok"):
        return False, f"AST errors: {ast_res.get('errors')}"
    return True, f"rollback_all.ps1 AST valid (params={ast_res.get('params')})"


def t1_f04_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/gaming_wlan_scan_toggle.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"gaming_wlan_scan_toggle.ps1 invalid: {ast_res.get('errors')}"
    txt = lib.read_text_auto(p).lower()
    if "autoconfig" not in txt:
        return False, "gaming_wlan_scan_toggle.ps1 missing netsh wlan autoconfig logic"
    return True, f"gaming_wlan_scan_toggle.ps1 valid (params={ast_res.get('params')})"


def t1_f04_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/toggle_vbs_hypervisor.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"toggle_vbs_hypervisor.ps1 invalid: {ast_res.get('errors')}"
    return True, f"toggle_vbs_hypervisor.ps1 valid (params={ast_res.get('params')})"


def t1_f04_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/rollback_all.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists"):
        return False, "Missing scripts/rollback_all.ps1"
    has_dry = bool(ast_res.get("supports_should_process") or ast_res.get("has_dry_run"))
    txt = lib.read_text_auto(p).lower()
    refs_backups = "backups" in txt and ".reg" in txt and ".pow" in txt
    if not has_dry or not refs_backups:
        return False, f"has_dry={has_dry}, refs_backups={refs_backups}"
    return True, "rollback_all.ps1 declares WhatIf/DryRun and references .reg/.pow backups"


def t1_f04_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    settings = str(snap.get("wlan_settings") or "").lower()
    enabled = ("auto configuration logic is enabled" in settings) or ("habilitada" in settings)
    if not enabled:
        return False, f"WLAN autoconfig not enabled in steady state: {settings[:120]}"
    return True, "WLAN auto-configuration logic enabled in steady state"


# =============================================================================
# F5: User-Space OS & Gaming Hooks
# =============================================================================

def t1_f05_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, _ = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
    return (ex and lib.is_exact_int(val) and val == 0), f"GameDVR_Enabled={val!r} (exists={ex}, expected 0)"


def t1_f05_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, _ = lib.read_reg_value(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled"
    )
    return (ex and lib.is_exact_int(val) and val == 0), f"AppCaptureEnabled={val!r} (exists={ex}, expected 0)"


def t1_f05_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, _ = lib.read_reg_value(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency"
    )
    return (ex and lib.is_exact_int(val) and val == 0), f"EnableTransparency={val!r} (exists={ex}, expected 0)"


def t1_f05_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, min_anim, _ = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics", "MinAnimate")
    ex2, vfx, _ = lib.read_reg_value(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting"
    )
    ok = ex1 and str(min_anim) == "0" and ex2 and lib.is_exact_int(vfx) and vfx == 2
    return ok, f"MinAnimate={min_anim!r} (exp '0'), VisualFXSetting={vfx!r} (exp 2)"


def t1_f05_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    vals = lib.enum_reg_values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences")
    fifa_entries = {k: v[0] for k, v in vals.items() if "fifa22.exe" in k.lower()}
    if not fifa_entries:
        return False, "No FIFA22.exe entry in HKCU UserGpuPreferences"
    ok = any("gpupreference=2;" in str(v).lower() for v in fifa_entries.values())
    return ok, f"UserGpuPreferences FIFA22={fifa_entries}"


# =============================================================================
# F6: Startup Bloat & Overlay Reduction
# =============================================================================

def _check_run_entries_disabled(names: list[str], prefixes: list[str]) -> tuple[bool, list[str]]:
    run_vals = lib.enum_reg_values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
    app_vals = lib.enum_reg_values(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    )
    targets = set(names)
    for k in list(run_vals.keys()) + list(app_vals.keys()):
        if any(k.startswith(pfx) for pfx in prefixes):
            targets.add(k)
    still_enabled = []
    for name in sorted(targets):
        in_run = name in run_vals
        in_app = name in app_vals
        if not in_run and not in_app:
            continue  # Absent from both Run and StartupApproved\Run -> removed/disabled
        if in_app:
            if not lib.is_startup_approved_disabled(app_vals[name][0]):
                suffix = "" if in_run else " [StartupApproved-only]"
                still_enabled.append(f"{name}(enabled{suffix})")
        elif in_run:
            still_enabled.append(f"{name}(Run present, no StartupApproved)")
    return len(still_enabled) == 0, still_enabled


def t1_f06_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, bad = _check_run_entries_disabled(lib.TARGET_STARTUP_BLOAT, lib.TARGET_STARTUP_PREFIXES)
    return ok, "All primary HKCU startup bloat disabled" if ok else f"Still enabled: {bad}"


def t1_f06_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, bad = _check_run_entries_disabled(lib.TARGET_RUN_ONLY_BLOAT, [])
    return ok, "FDM & HidHide_Updater startup disabled/removed" if ok else f"Still enabled: {bad}"


def t1_f06_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    overlays = {"NVIDIA Overlay", "AMDRSSrcExt", "RadeonSoftware"}
    found = sorted(running & overlays)
    return len(found) == 0, "No GPU overlay processes running" if not found else f"Running overlays: {found}"


def t1_f06_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    found = sorted(running & {"fdm", "steamwebhelper"})
    return len(found) == 0, "No idle fdm/steamwebhelper processes" if not found else f"Running: {found}"


def t1_f06_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    return "MSPCManagerService" not in running, (
        "MSPCManagerService not running" if "MSPCManagerService" not in running else "MSPCManagerService running"
    )


# =============================================================================
# F7: Elevated Power Plan & Hibernation
# =============================================================================

def t1_f07_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    sch = ctx.power_scheme()
    if not sch.get("ok"):
        return False, f"powercfg /getactivescheme failed: {sch.get('error')}"
    guid = sch.get("guid", "")
    name = sch.get("name", "")
    proc_max = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PROC_MAX)
    boost = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PERF_BOOST_MODE)
    cool = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_SYS_COOL_POL)
    pmax_ac = proc_max.get("ac")
    boost_ac = boost.get("ac")
    cool_ac = cool.get("ac")
    ok = (
        lib.is_gaming_stable_scheme(sch)
        and proc_max.get("ok")
        and isinstance(pmax_ac, int) and not isinstance(pmax_ac, bool) and 0 <= pmax_ac <= 99
        and boost.get("ok")
        and isinstance(boost_ac, int) and not isinstance(boost_ac, bool) and boost_ac == 0
        and cool.get("ok")
        and isinstance(cool_ac, int) and not isinstance(cool_ac, bool) and cool_ac == 1
    )
    return ok, (
        f"Active scheme={guid} ({name}), PROCTHROTTLEMAX_AC={proc_max.get('ac')} (<=99), "
        f"PERFBOOSTMODE_AC={boost.get('ac')} (0=Disabled), SYSCOOLPOL_AC={cool.get('ac')} (1=Active)"
    )


def t1_f07_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    usb = ctx.powercfg_setting(lib.SUB_USB, lib.SET_USB_SUSPEND)
    if not usb.get("ok"):
        return False, f"powercfg USB query failed: {usb.get('error')}"
    ac_val = usb.get("ac")
    return (lib.is_exact_int(ac_val) and ac_val == 0), f"USB selective suspend AC={ac_val} DC={usb.get('dc')} (expected AC=0)"


def t1_f07_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    hib_file = Path(r"C:\hiberfil.sys")
    avail = ctx.power_available()
    hib_exists = hib_file.exists()
    hib_avail = avail.get("hibernate_available", True)
    ok = (not hib_exists) and avail.get("ok", False) and (not hib_avail)
    return ok, f"hiberfil.sys exists={hib_exists}, powercfg /a hibernate_available={hib_avail}"


def t1_f07_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled"
    )
    return (ex and lib.is_exact_int(val) and val == 0), f"HiberbootEnabled={val!r} (exists={ex}, expected 0)"


def t1_f07_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, pt_off, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff"
    )
    ex2, w32, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation"
    )
    ok = ex1 and lib.is_exact_int(pt_off) and pt_off == 1 and ex2 and lib.is_exact_int(w32) and w32 == 0x26
    return ok, f"PowerThrottlingOff={pt_off!r} (exp 1), Win32PrioritySeparation={hex(w32) if lib.is_exact_int(w32) else w32!r} (exp 0x26)"


# =============================================================================
# F8: Stability-Safe GPU & Driver Tuning
# =============================================================================

def t1_f08_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, f"NvAPI DRS error: {drs.get('error') if isinstance(drs, dict) else drs}"
    s = settings.get(lib.NVAPI_DRS_POWER_MGMT)
    if not isinstance(s, dict):
        s = {}
    val = s.get("current_u32")
    ok = bool(s.get("found")) and lib.is_exact_int(val) and val == 1
    return ok, f"DRS 0x1057EB71 (PowerMgmt)={val} (exp 1)"


def t1_f08_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, f"NvAPI DRS error: {drs.get('error') if isinstance(drs, dict) else drs}"
    s = settings.get(lib.NVAPI_DRS_LOW_LATENCY)
    if not isinstance(s, dict):
        s = {}
    val = s.get("current_u32")
    ok = bool(s.get("found")) and lib.is_exact_int(val) and val == 1
    return ok, f"DRS 0x007BA09E (LowLatency)={val} (exp 1)"


def t1_f08_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, f"NvAPI DRS error: {drs.get('error') if isinstance(drs, dict) else drs}"
    tex = settings.get(lib.NVAPI_DRS_TEX_FILTER_QUAL)
    thr = settings.get(lib.NVAPI_DRS_THREADED_OPT)
    if not isinstance(tex, dict):
        tex = {}
    if not isinstance(thr, dict):
        thr = {}
    tex_v = tex.get("current_u32")
    thr_v = thr.get("current_u32")
    ok = (
        bool(tex.get("found")) and lib.is_exact_int(tex_v) and tex_v == 0x14
        and bool(thr.get("found")) and lib.is_exact_int(thr_v) and thr_v == 1
    )
    return ok, f"DRS TexQual={tex_v} (exp 20/0x14), ThreadedOpt={thr_v} (exp 1)"


def t1_f08_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, f"NvAPI DRS error: {drs.get('error') if isinstance(drs, dict) else drs}"
    sc = settings.get(lib.NVAPI_DRS_SHADER_CACHE_MB)
    if not isinstance(sc, dict):
        sc = {}
    val = sc.get("current_u32")
    ok = bool(sc.get("found")) and lib.is_exact_int(val) and (val == 0xFFFFFFFF or val >= 0x2800)
    return ok, f"DRS ShaderCache={hex(val) if lib.is_exact_int(val) else val} (exp >=0x2800 or 0xffffffff)"


def t1_f08_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex_gdvr, allow_gdvr, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR"
    )
    amd_sub = lib.find_display_class_subkey("AMD")
    ulps_val = None
    if amd_sub:
        _, ulps_val, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, amd_sub, "EnableUlps")
    _, tdr_val, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "TdrDelay"
    )
    tdr_safe = (tdr_val is None) or (isinstance(tdr_val, int) and not isinstance(tdr_val, bool) and tdr_val <= 8)
    ok = (
        ex_gdvr
        and isinstance(allow_gdvr, int) and not isinstance(allow_gdvr, bool) and allow_gdvr == 0
        and (amd_sub is not None)
        and isinstance(ulps_val, int) and not isinstance(ulps_val, bool) and ulps_val == 0
        and tdr_safe
    )
    return ok, f"AllowGameDVR={allow_gdvr!r}, AMD({amd_sub}) EnableUlps={ulps_val!r}, TdrDelay={tdr_val!r}"


# =============================================================================
# F9: Services & Scheduled Tasks Cleanup
# =============================================================================

def t1_f09_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    if not isinstance(snap.get("services"), dict):
        return False, f"services snapshot missing/invalid: {type(snap.get('services')).__name__}"
    bad = []
    exists_count = 0
    for svc in ("PCManager Service Store", "MapsBroker", "NetworkDaemon"):
        st = _svc_state(snap, svc)
        if st.get("Exists"):
            exists_count += 1
            if st.get("StartType") != "Disabled" or st.get("Status") != "Stopped":
                bad.append(f"{svc}={st.get('StartType')}/{st.get('Status')}")
    ok = exists_count >= 2 and len(bad) == 0
    return ok, (
        f"Target bloat services Disabled+Stopped (verified={exists_count}/3)"
        if ok
        else f"exists={exists_count}/3, Not disabled/stopped: {bad}"
    )


def t1_f09_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    if not isinstance(snap.get("services"), dict):
        return False, f"services snapshot missing/invalid: {type(snap.get('services')).__name__}"
    bad = []
    exists_count = 0
    for svc in ("RemoteRegistry", "DiagTrack", "dmwappushservice", "SysMain"):
        st = _svc_state(snap, svc)
        if st.get("Exists"):
            exists_count += 1
            if st.get("StartType") != "Disabled" or st.get("Status") != "Stopped":
                bad.append(f"{svc}={st.get('StartType')}/{st.get('Status')}")
    ok = exists_count >= 3 and len(bad) == 0
    return ok, (
        f"Telemetry/SysMain services Disabled+Stopped (verified={exists_count}/4)"
        if ok
        else f"exists={exists_count}/4, Bad: {bad}"
    )


def t1_f09_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    if not isinstance(snap.get("services"), dict):
        return False, f"services snapshot missing/invalid: {type(snap.get('services')).__name__}"
    bad = []
    exists_count = 0
    for svc in ("RetailDemo", "WalletService", "PhoneSvc", "wisvc"):
        st = _svc_state(snap, svc)
        if st.get("Exists"):
            exists_count += 1
            if st.get("StartType") != "Disabled":
                bad.append(f"{svc}={st.get('StartType')}")
    fax = _svc_state(snap, "Fax")
    if fax.get("Exists") and fax.get("StartType") != "Disabled":
        bad.append(f"Fax={fax.get('StartType')}")
    ok = exists_count >= 3 and len(bad) == 0
    return ok, (
        f"Unused SKU services Disabled (verified={exists_count}/4; Fax absent/Disabled)"
        if ok
        else f"exists={exists_count}/4, Bad: {bad}"
    )


def t1_f09_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    if not isinstance(snap.get("services"), dict):
        return False, f"services snapshot missing/invalid: {type(snap.get('services')).__name__}"
    bad = []
    exists_count = 0
    for svc in lib.TARGET_SERVICES_MANUAL_OR_DISABLED:
        st = _svc_state(snap, svc)
        if st.get("Exists"):
            exists_count += 1
            if st.get("StartType") not in ("Manual", "Disabled"):
                bad.append(f"{svc}={st.get('StartType')}")
    ok = exists_count >= 3 and len(bad) == 0
    return ok, (
        f"On-demand services set Manual/Disabled (verified={exists_count}/{len(lib.TARGET_SERVICES_MANUAL_OR_DISABLED)})"
        if ok
        else f"exists={exists_count}/{len(lib.TARGET_SERVICES_MANUAL_OR_DISABLED)}, Still Automatic: {bad}"
    )


def t1_f09_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    tasks = snap.get("tasks")
    if not isinstance(tasks, list):
        return False, f"tasks snapshot missing/invalid: {type(tasks).__name__}"
    matched_count = 0
    still_ready = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        tn = str(t.get("TaskName") or "")
        tp = str(t.get("TaskPath") or "")
        st = str(t.get("State") or "")
        if lib.is_target_disabled_task(tp, tn):
            matched_count += 1
            if st != "Disabled":
                still_ready.append(f"{tp}{tn}={st}")
    ok = matched_count >= 5 and len(still_ready) == 0
    return ok, (
        f"All {matched_count} target telemetry/updater tasks Disabled"
        if ok
        else f"matched={matched_count}, still_enabled={still_ready}"
    )


# =============================================================================
# F10: VBS / HVCI Gaming Optimization
# =============================================================================

def t1_f10_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    return (ex and lib.is_exact_int(val) and val == 0), f"EnableVirtualizationBasedSecurity={val!r} (exists={ex}, exp 0)"


def t1_f10_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled",
    )
    ok = (not ex) or (lib.is_exact_int(val) and val == 0)
    return ok, f"HVCI Enabled={val!r} (exists={ex}, exp absent or 0)"


def t1_f10_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    dg = snap.get("device_guard") if isinstance(snap, dict) and snap.get("ok") else None
    if not isinstance(dg, dict):
        return False, "Win32_DeviceGuard query failed"
    vbs = dg.get("VirtualizationBasedSecurityStatus")
    running = dg.get("SecurityServicesRunning")
    if not isinstance(running, list):
        return False, f"Win32_DeviceGuard SecurityServicesRunning invalid: {running!r}"
    hyp = snap.get("hypervisor_present")
    ex_vbs, reg_vbs, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    ex_hvci, reg_hvci, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled",
    )
    reg_vbs_off = ex_vbs and lib.is_exact_int(reg_vbs) and reg_vbs == 0
    hvci_off = (not ex_hvci) or (lib.is_exact_int(reg_hvci) and reg_hvci == 0)
    hvci_not_running = 2 not in running
    status_ok = lib.is_exact_int(vbs) and (
        vbs == 0 or (vbs == 2 and hyp is True and reg_vbs_off and hvci_off and hvci_not_running)
    )
    ok = reg_vbs_off and hvci_off and hvci_not_running and status_ok
    return ok, (
        f"VBS_status={vbs}, hypervisor_present={hyp}, reg_VBS={reg_vbs!r}(ex={ex_vbs}), "
        f"HVCI_reg={reg_hvci!r}(ex={ex_hvci}), SecurityServicesRunning={running}"
    )


def t1_f10_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    dg = snap.get("device_guard") if isinstance(snap, dict) and snap.get("ok") else None
    if not isinstance(dg, dict):
        return False, "Win32_DeviceGuard query failed"
    running = dg.get("SecurityServicesRunning")
    if not isinstance(running, list):
        return False, f"Win32_DeviceGuard SecurityServicesRunning invalid: {running!r}"
    ok = 2 not in running
    return ok, f"DeviceGuard SecurityServicesRunning={running} (exp 2/HVCI absent)"


def t1_f10_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/toggle_vbs_hypervisor.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"toggle_vbs_hypervisor.ps1 invalid: {ast_res.get('errors')}"
    txt = lib.read_text_auto(p).lower()
    ok = ("gaming" in txt) and ("wsl_docker" in txt)
    return ok, "toggle_vbs_hypervisor.ps1 exposes gaming and wsl_docker modes" if ok else "Missing gaming/wsl_docker modes"


# =============================================================================
# F11: Wi-Fi Power-Saving & Wake-Spike Fix
# =============================================================================

def t1_f11_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    adv_lps = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    guid = _get_adapter_guid(snap, "Wi-Fi")
    sub = lib.find_nic_class_subkey(guid or "")
    reg_lps = None
    if sub:
        _, reg_lps, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "LpsEn")
    ok = str(adv_lps) == "0" and (sub is not None) and str(reg_lps) == "0"
    return ok, f"Wi-Fi LpsEn adv={adv_lps!r}, class({sub})={reg_lps!r} (exp '0')"


def t1_f11_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    guid = _get_adapter_guid(snap, "Wi-Fi")
    sub = lib.find_nic_class_subkey(guid or "")
    if not sub:
        return False, f"Could not resolve Wi-Fi NIC Class subkey for GUID={guid}"
    ex, pnp, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "PnPCapabilities")
    ok = ex and lib.is_exact_int(pnp) and pnp == 24
    return ok, f"Wi-Fi Class({sub}) PnPCapabilities={pnp!r} (exp 24 / 0x18)"


def t1_f11_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    en = _get_ms_power_enable(snap, "DEV_8852")
    if en is None:
        return False, "MSPower_DeviceEnable entry for DEV_8852 not found"
    return (en is False), f"Wi-Fi MSPower_DeviceEnable.Enable={en} (exp False)"


def t1_f11_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    wl = ctx.powercfg_setting(lib.SUB_WIRELESS, lib.SET_WIRELESS_PS)
    if not wl.get("ok"):
        return False, f"powercfg wireless query failed: {wl.get('error')}"
    ac, dc = wl.get("ac"), wl.get("dc")
    ok = lib.is_exact_int(ac) and ac == 0 and lib.is_exact_int(dc) and dc == 0
    return ok, f"Wireless Power Saving AC={ac} DC={dc} (exp 0/0)"


def t1_f11_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    aspm = ctx.powercfg_setting(lib.SUB_PCIE, lib.SET_PCIE_ASPM)
    if not aspm.get("ok"):
        return False, f"powercfg PCIe ASPM query failed: {aspm.get('error')}"
    ac, dc = aspm.get("ac"), aspm.get("dc")
    ok = lib.is_exact_int(ac) and ac == 0 and lib.is_exact_int(dc) and dc == 0
    return ok, f"PCIe ASPM AC={ac} DC={dc} (exp 0/0)"


# =============================================================================
# F12: Wi-Fi Roaming Lock & 2.4 GHz Guard
# =============================================================================

def t1_f12_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    roam = _get_adv_val(snap, "Wi-Fi", "RegROAMSensitiveLevel")
    guid = _get_adapter_guid(snap, "Wi-Fi")
    sub = lib.find_nic_class_subkey(guid or "")
    reg_roam = None
    if sub:
        _, reg_roam, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "RegROAMSensitiveLevel")
    ok = str(roam) == "127" and str(reg_roam) == "127"
    return ok, f"RegROAMSensitiveLevel adv={roam!r}, class={reg_roam!r} (exp '127')"


def t1_f12_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    txt = str(snap.get("wlan_interfaces") or "")
    connected = bool(re.search(r"State\s*:\s*connected", txt, re.IGNORECASE))
    is_5g = "5 ghz" in txt.lower()
    is_claro2 = "claro2_f3e3b9" in txt.lower()
    ok = connected and is_5g and is_claro2
    return ok, f"WLAN connected={connected}, 5GHz={is_5g}, CLARO2_F3E3B9={is_claro2}"


def t1_f12_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    txt = str(snap.get("wlan_profile_claro1") or "").lower()
    if "not found" in txt or "no se encuentra" in txt:
        return True, "CLARO1_F3E3B9 profile not present (no 2.4 GHz auto-connect)"
    manual = ("connect manually" in txt) or ("conectar manualmente" in txt)
    return manual, "CLARO1_F3E3B9 set to manual" if manual else "CLARO1_F3E3B9 still Connect automatically"


def t1_f12_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    txt = str(snap.get("wlan_profile_claro2") or "").lower()
    auto = ("connect automatically" in txt) or ("conectar autom" in txt)
    return auto, "CLARO2_F3E3B9 set to auto-connect" if auto else f"CLARO2_F3E3B9 not auto-connect: {txt[:120]}"


def t1_f12_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/gaming_wlan_scan_toggle.ps1"
    ast_res = lib.check_ps_ast(p)
    snap = ctx.pwsh_snapshot()
    settings = str(snap.get("wlan_settings") or "").lower() if snap.get("ok") else ""
    enabled = ("auto configuration logic is enabled" in settings) or ("habilitada" in settings)
    ok = bool(ast_res.get("exists") and ast_res.get("ok") and enabled)
    return ok, f"toggle script ok={ast_res.get('ok')}, steady-state autoconfig enabled={enabled}"


# =============================================================================
# F13: DNS & Background Bandwidth Optimization
# =============================================================================

def t1_f13_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    ok = lib.is_valid_static_gaming_dns(srvs)
    return ok, f"Wi-Fi IPv4 DNS={srvs} (expected ['1.1.1.1', '1.0.0.1'] or ['9.9.9.9', '8.8.8.8'])"


def t1_f13_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    dns = ctx.live_dns()
    cf = dns.get("1.1.1.1") if isinstance(dns, dict) else None
    q9 = dns.get("9.9.9.9") if isinstance(dns, dict) else None
    g8 = dns.get("8.8.8.8") if isinstance(dns, dict) else None
    if not isinstance(cf, dict):
        cf = {}
    if not isinstance(q9, dict):
        q9 = {}
    if not isinstance(g8, dict):
        g8 = {}
    ok = bool(cf.get("ok") and q9.get("ok") and g8.get("ok"))
    return ok, (
        f"Live DNS 1.1.1.1={cf.get('avg_ms')}ms (ok={cf.get('ok')}), "
        f"9.9.9.9={q9.get('avg_ms')}ms (ok={q9.get('ok')}), 8.8.8.8={g8.get('avg_ms')}ms (ok={g8.get('ok')})"
    )


def t1_f13_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, v1, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode"
    )
    ex2, v2, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\DeliveryOptimization\Config", "DODownloadMode"
    )
    ok = (ex1 and lib.is_exact_int(v1) and v1 == 0) or (ex2 and lib.is_exact_int(v2) and v2 == 0)
    return ok, f"DODownloadMode policy={v1!r} (ex={ex1}), config={v2!r} (ex={ex2}); exp 0"


def t1_f13_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    svcs = snap.get("services")
    if not isinstance(svcs, dict):
        return False, f"services snapshot missing/invalid: {type(svcs).__name__}"
    dosvc = _svc_state(snap, "DoSvc")
    ex1, v1, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode"
    )
    ok = (dosvc.get("Status") == "Stopped") or (ex1 and lib.is_exact_int(v1) and v1 == 0)
    return ok, f"DoSvc Status={dosvc.get('Status')}/{dosvc.get('StartType')}, policy DODownloadMode={v1!r}"


def t1_f13_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    hogs = sorted(running & set(lib.TARGET_P2P_PROCESSES))
    return len(hogs) == 0, "Zero P2P/background bandwidth hogs running" if not hogs else f"Active P2P hogs: {hogs}"


# =============================================================================
# F14: Ethernet Standby & TCP/Offload Tuning
# =============================================================================

def t1_f14_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    if not isinstance(snap.get("adapters"), list):
        return False, f"adapters snapshot missing/invalid: {type(snap.get('adapters')).__name__}"
    guid = _get_adapter_guid(snap, "Ethernet")
    sub = lib.find_nic_class_subkey(guid or "")
    kws = ["EnableGreenEthernet", "PowerSavingMode", "GigaLite", "*InterruptModeration", "*FlowControl"]
    bad = []
    for kw in kws:
        adv_v = _get_adv_val(snap, "Ethernet", kw)
        reg_v = None
        if sub:
            _, reg_v, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, kw)
        if str(adv_v) != "0" or (sub is not None and str(reg_v) != "0"):
            bad.append(f"{kw}(adv={adv_v},reg={reg_v})")
    return len(bad) == 0, f"All 5 Ethernet flags == 0 (class={sub})" if not bad else f"Non-zero Ethernet flags: {bad}"


def t1_f14_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    if not isinstance(snap.get("ms_power_enable"), list):
        return False, f"ms_power_enable snapshot missing/invalid: {type(snap.get('ms_power_enable')).__name__}"
    en = _get_ms_power_enable(snap, "DEV_8168")
    guid = _get_adapter_guid(snap, "Ethernet")
    sub = lib.find_nic_class_subkey(guid or "")
    pnp = None
    if sub:
        _, pnp, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "PnPCapabilities")
    ok = (en is False) or (lib.is_exact_int(pnp) and pnp == 24)
    return ok, f"Ethernet MSPower_DeviceEnable={en}, PnPCapabilities={pnp!r}"


def t1_f14_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    tcp = str(snap.get("tcp_global") or "")
    rss_ok = bool(re.search(r"(?im)^\s*receive-side scaling state\s*:\s*enabled\b", tcp))
    at_ok = bool(re.search(r"(?im)^\s*receive window auto-tuning level\s*:\s*normal\b", tcp))
    rsc_ok = bool(re.search(r"(?im)^\s*receive segment coalescing state\s*:\s*disabled\b", tcp))
    ecn_ok = bool(re.search(r"(?im)^\s*ecn capability\s*:\s*disabled\b", tcp))
    ts_ok = bool(re.search(r"(?im)^\s*rfc 1323 timestamps\s*:\s*disabled\b", tcp))
    ns_ok = bool(re.search(r"(?im)^\s*non sack rtt resiliency\s*:\s*disabled\b", tcp))
    wifi_rsc = snap.get("wifi_rsc") or []
    nic_rsc_off = isinstance(wifi_rsc, list) and bool(wifi_rsc) and all(
        isinstance(item, dict) and item.get("IPv4Enabled") is False and item.get("IPv6Enabled") is False for item in wifi_rsc
    )
    ok = rss_ok and at_ok and rsc_ok and ecn_ok and ts_ok and ns_ok and nic_rsc_off
    return ok, (
        f"TCP global RSS={rss_ok}, AutoTuning=normal({at_ok}), RSC=disabled({rsc_ok}), "
        f"ECN=disabled({ecn_ok}), Timestamps=disabled({ts_ok}), NonSack=disabled({ns_ok}), WiFi_RSC_off={nic_rsc_off}"
    )


def t1_f14_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    off = snap.get("net_offload_global")
    if not isinstance(off, dict):
        off = {}
    rss_global = str(off.get("ReceiveSideScaling") or "").lower() == "enabled"
    chk_list = snap.get("eth_checksum")
    chk = chk_list[0] if isinstance(chk_list, list) and chk_list and isinstance(chk_list[0], dict) else {}
    # PowerShell enums serialize either as string ("RxTxEnabled") or int (3)
    def _is_rxtx(v: object) -> bool:
        return str(v) == "3" or "rxtx" in str(v).lower()
    chk_ok = bool(chk) and all(
        _is_rxtx(chk.get(k)) for k in ("IpIPv4Enabled", "TcpIPv4Enabled", "TcpIPv6Enabled", "UdpIPv4Enabled", "UdpIPv6Enabled")
    )
    return (rss_global and chk_ok), f"Global RSS={off.get('ReceiveSideScaling')}, Eth Checksum={chk}"


def t1_f14_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    bad = []
    eth_present = _get_adapter_guid(snap, "Ethernet") is not None
    targets = ("Wi-Fi", "Ethernet") if eth_present else ("Wi-Fi",)
    for alias in targets:
        guid = _get_adapter_guid(snap, alias)
        if not guid:
            bad.append(f"{alias}:missing_guid")
            continue
        sub = rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{guid}"
        _, ack, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "TcpAckFrequency")
        _, nodelay, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "TCPNoDelay")
        if not (lib.is_exact_int(ack) and ack == 1 and lib.is_exact_int(nodelay) and nodelay == 1):
            bad.append(f"{alias}(TcpAckFrequency={ack},TCPNoDelay={nodelay})")
    return len(bad) == 0, f"TcpAckFrequency=1 & TCPNoDelay=1 on {list(targets)}" if not bad else f"Bad: {bad}"


def t1_f14_06(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    mm_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    ex1, nti, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "NetworkThrottlingIndex")
    ex2, sr, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "SystemResponsiveness")
    qos_ok, qos_msg = lib.check_fortnite_qos(snap)
    ok = ex1 and lib.is_exact_int(nti) and nti == 0xFFFFFFFF and ex2 and lib.is_exact_int(sr) and sr == 0 and qos_ok
    return ok, f"NetworkThrottlingIndex={hex(nti) if lib.is_exact_int(nti) else nti!r}, SystemResponsiveness={sr!r}, QoS=({qos_msg})"


# =============================================================================
# F15: Post-Optimization Benchmarks & E2E Pass
# =============================================================================

def t1_f15_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/post_system.json")
    if not ok:
        return False, msg
    valid, errs = lib.validate_system_json(data, expected_tag="post")
    return valid, "post_system.json schema valid" if valid else "; ".join(errs)


def t1_f15_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, data, msg = lib.load_json_file(ctx.project_root / "reports/post_network.json")
    if not ok:
        return False, msg
    valid, errs = lib.validate_network_json(data, expected_tag="post")
    return valid, "post_network.json schema valid" if valid else "; ".join(errs)


def t1_f15_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    comp = ctx.project_root / "reports/comparison_report.md"
    if not comp.is_file():
        return False, f"Missing {comp}"
    txt = comp.read_text(encoding="utf-8", errors="replace")
    low = txt.lower()
    if "baseline-only inspection" in low or "n/a (pending)" in low:
        return False, "comparison_report.md is still in baseline-only/pending inspection mode"
    has_sys = any(k in low for k in ("tflops", "gflops", "gpu", "cpu", "timer"))
    has_net = any(k in low for k in ("ping", "latency", "jitter", "dns", "9.9.9.9"))
    has_post = "post" in low
    has_delta = any(k in low for k in ("delta", "improved", "regressed", "unchanged", "comparison", "changed"))
    ok = len(txt.strip()) >= 200 and has_sys and has_net and has_post and has_delta
    return ok, f"comparison_report.md len={len(txt)}, sys={has_sys}, net={has_net}, post={has_post}, delta={has_delta}"


def t1_f15_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok, post_sys, msg = lib.load_json_file(ctx.project_root / "reports/post_system.json")
    if not ok:
        return False, msg
    if not isinstance(post_sys, dict):
        return False, f"post_system.json expected object, got {type(post_sys).__name__}"
    ts = post_sys.get("timestamp_utc", "")
    dt = lib.parse_utc_iso(ts)
    if dt is None:
        return False, f"Invalid post_system.json timestamp_utc: {ts!r}"
    ev = lib.query_events_since(ts)
    if not ev.get("ok"):
        return False, f"Event log query failed: {ev.get('error')}"
    nv_raw = ev.get("nvlddmkm_count", -1)
    kp_raw = ev.get("kernel_power_count", -1)
    ok_ev = (
        isinstance(nv_raw, int) and not isinstance(nv_raw, bool) and nv_raw == 0
        and isinstance(kp_raw, int) and not isinstance(kp_raw, bool) and kp_raw == 0
    )
    return ok_ev, f"Post-timestamp ({ts}) events: nvlddmkm(13/153)={nv_raw}, Kernel-Power(41)={kp_raw}"


def t1_f15_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ok_py, msg_py = lib.check_py_compile(ctx.project_root / "scripts/generate_comparison_report.py")
    if not ok_py:
        return False, msg_py
    rep_dir = ctx.project_root / "reports"
    logs = list(rep_dir.glob("apply_*.log")) + list(rep_dir.glob("verification_report.md"))
    non_empty = [p.name for p in logs if p.name.lower() != "rollback.log" and p.stat().st_size > 0]
    if not non_empty:
        return False, "No non-empty apply_*.log or verification_report.md in reports/ (rollback.log excluded)"
    return True, f"generate_comparison_report.py compiles; post-apply artifacts={non_empty}"


def get_tests() -> list[lib.TestCase]:
    return [
        # F1
        lib.TestCase("T1-F01-01", 1, "F1", "R3", "Backup subdirectories exist", t1_f01_01),
        lib.TestCase("T1-F01-02", 1, "F1", "R3", "System & network .reg backups valid", t1_f01_02),
        lib.TestCase("T1-F01-03", 1, "F1", "R3", "Power scheme .pow & active plan backups exist", t1_f01_03),
        lib.TestCase("T1-F01-04", 1, "F1", "R3", "Baseline JSON/CSV/TXT snapshots valid", t1_f01_04),
        lib.TestCase("T1-F01-05", 1, "F1", "R3", "Restore point info & System Restore log valid", t1_f01_05),
        # F2
        lib.TestCase("T1-F02-01", 1, "F2", "R3", "benchmark_system.py compiles cleanly", t1_f02_01),
        lib.TestCase("T1-F02-02", 1, "F2", "R3", "baseline_system.json satisfies schema contract", t1_f02_02),
        lib.TestCase("T1-F02-03", 1, "F2", "R3", "Live GTX 1650 CUDA & nvidia-smi probe healthy", t1_f02_03),
        lib.TestCase("T1-F02-04", 1, "F2", "R3", "baseline_system.json CPU/GPU/Disk metrics positive", t1_f02_04),
        lib.TestCase("T1-F02-05", 1, "F2", "R3", "baseline_system.json timer jitter monotonic & is_admin bool", t1_f02_05),
        # F3
        lib.TestCase("T1-F03-01", 1, "F3", "R2,R3", "benchmark_network.ps1 AST parses with 0 errors", t1_f03_01),
        lib.TestCase("T1-F03-02", 1, "F3", "R2,R3", "baseline_network.json satisfies schema contract", t1_f03_02),
        lib.TestCase("T1-F03-03", 1, "F3", "R2,R3", "baseline_network.json covers gateway, 9.9.9.9/8.8.8.8/1.1.1.1 & DNS", t1_f03_03),
        lib.TestCase("T1-F03-04", 1, "F3", "R2,R3", "Live .NET Ping burst succeeds against gateway & 9.9.9.9", t1_f03_04),
        lib.TestCase("T1-F03-05", 1, "F3", "R2,R3", "Live warmed Resolve-DnsName & HTTPS 204 succeed", t1_f03_05),
        # F4
        lib.TestCase("T1-F04-01", 1, "F4", "R1,R2,R3", "rollback_all.ps1 AST parses with 0 errors", t1_f04_01),
        lib.TestCase("T1-F04-02", 1, "F4", "R2", "gaming_wlan_scan_toggle.ps1 AST & autoconfig logic valid", t1_f04_02),
        lib.TestCase("T1-F04-03", 1, "F4", "R1", "toggle_vbs_hypervisor.ps1 AST parses with 0 errors", t1_f04_03),
        lib.TestCase("T1-F04-04", 1, "F4", "R3", "rollback_all.ps1 supports WhatIf/DryRun and references backups", t1_f04_04),
        lib.TestCase("T1-F04-05", 1, "F4", "R2", "WLAN autoconfig enabled in steady state", t1_f04_05),
        # F5
        lib.TestCase("T1-F05-01", 1, "F5", "R1", "HKCU GameDVR_Enabled == 0", t1_f05_01),
        lib.TestCase("T1-F05-02", 1, "F5", "R1", "HKCU AppCaptureEnabled == 0", t1_f05_02),
        lib.TestCase("T1-F05-03", 1, "F5", "R1", "HKCU EnableTransparency == 0", t1_f05_03),
        lib.TestCase("T1-F05-04", 1, "F5", "R1", "HKCU MinAnimate == '0' and VisualFXSetting == 2", t1_f05_04),
        lib.TestCase("T1-F05-05", 1, "F5", "R1", "HKCU UserGpuPreferences FIFA22.exe GpuPreference=2;", t1_f05_05),
        # F6
        lib.TestCase("T1-F06-01", 1, "F6", "R1", "Primary HKCU startup bloat disabled", t1_f06_01),
        lib.TestCase("T1-F06-02", 1, "F6", "R1,R2", "FDM & HidHide_Updater startup disabled/removed", t1_f06_02),
        lib.TestCase("T1-F06-03", 1, "F6", "R1", "Idle GPU overlays not running", t1_f06_03),
        lib.TestCase("T1-F06-04", 1, "F6", "R1,R2", "Idle fdm & steamwebhelper not running", t1_f06_04),
        lib.TestCase("T1-F06-05", 1, "F6", "R1", "MSPCManagerService not running", t1_f06_05),
        # F7
        lib.TestCase("T1-F07-01", 1, "F7", "R1", "Gaming Estable (Sin Bateria) active (PROCTHROTTLEMAX<=99, PERFBOOSTMODE=0, SYSCOOLPOL=1)", t1_f07_01),
        lib.TestCase("T1-F07-02", 1, "F7", "R1", "USB selective suspend AC disabled (GUID 48e6b7a6-50f5-...)", t1_f07_02),
        lib.TestCase("T1-F07-03", 1, "F7", "R1", "Hibernation disabled & C:\\hiberfil.sys removed", t1_f07_03),
        lib.TestCase("T1-F07-04", 1, "F7", "R1", "Fast Startup HiberbootEnabled == 0", t1_f07_04),
        lib.TestCase("T1-F07-05", 1, "F7", "R1", "PowerThrottlingOff == 1 and Win32PrioritySeparation == 0x26", t1_f07_05),
        # F8
        lib.TestCase("T1-F08-01", 1, "F8", "R1", "NvAPI DRS Power Management == Prefer Max Performance (1)", t1_f08_01),
        lib.TestCase("T1-F08-02", 1, "F8", "R1", "NvAPI DRS Low Latency Mode == On (1)", t1_f08_02),
        lib.TestCase("T1-F08-03", 1, "F8", "R1", "NvAPI DRS Texture Filtering High Perf (0x14) & Threaded Opt On (1)", t1_f08_03),
        lib.TestCase("T1-F08-04", 1, "F8", "R1", "NvAPI DRS Shader Cache >= 10 GB (0x2800) or Unlimited", t1_f08_04),
        lib.TestCase("T1-F08-05", 1, "F8", "R1", "HKLM AllowGameDVR=0, AMD EnableUlps=0, safe TdrDelay", t1_f08_05),
        # F9
        lib.TestCase("T1-F09-01", 1, "F9", "R1", "PCManager, MapsBroker, NetworkDaemon Disabled+Stopped", t1_f09_01),
        lib.TestCase("T1-F09-02", 1, "F9", "R1", "RemoteRegistry, DiagTrack, dmwappushservice, SysMain Disabled", t1_f09_02),
        lib.TestCase("T1-F09-03", 1, "F9", "R1", "RetailDemo, WalletService, PhoneSvc, wisvc Disabled (Fax absent/Disabled)", t1_f09_03),
        lib.TestCase("T1-F09-04", 1, "F9", "R1", "WSearch, TrkWks, Spooler, lfsvc set Manual or Disabled", t1_f09_04),
        lib.TestCase("T1-F09-05", 1, "F9", "R1", "Telemetry & updater scheduled tasks Disabled", t1_f09_05),
        # F10
        lib.TestCase("T1-F10-01", 1, "F10", "R1", "DeviceGuard EnableVirtualizationBasedSecurity == 0", t1_f10_01),
        lib.TestCase("T1-F10-02", 1, "F10", "R1", "HVCI Enabled absent or == 0", t1_f10_02),
        lib.TestCase("T1-F10-03", 1, "F10", "R1", "Win32_DeviceGuard runtime VBS status == 0 (or 2 with -KeepHypervisor & HVCI off)", t1_f10_03),
        lib.TestCase("T1-F10-04", 1, "F10", "R1", "Win32_DeviceGuard SecurityServicesRunning excludes HVCI", t1_f10_04),
        lib.TestCase("T1-F10-05", 1, "F10", "R1", "toggle_vbs_hypervisor.ps1 supports gaming & wsl_docker modes", t1_f10_05),
        # F11
        lib.TestCase("T1-F11-01", 1, "F11", "R2", "Wi-Fi LpsEn == 0 (AdvancedProperty & NIC Class reg)", t1_f11_01),
        lib.TestCase("T1-F11-02", 1, "F11", "R2", "Wi-Fi NIC Class PnPCapabilities == 24 (0x18)", t1_f11_02),
        lib.TestCase("T1-F11-03", 1, "F11", "R2", "Wi-Fi MSPower_DeviceEnable (DEV_8852) == False", t1_f11_03),
        lib.TestCase("T1-F11-04", 1, "F11", "R2", "Wireless Power Saving Mode AC=0 & DC=0", t1_f11_04),
        lib.TestCase("T1-F11-05", 1, "F11", "R2", "PCIe ASPM AC=0 & DC=0", t1_f11_05),
        # F12
        lib.TestCase("T1-F12-01", 1, "F12", "R2", "Wi-Fi RegROAMSensitiveLevel == 127 (Deshabilitar)", t1_f12_01),
        lib.TestCase("T1-F12-02", 1, "F12", "R2", "Wi-Fi connected on 5 GHz to CLARO2_F3E3B9", t1_f12_02),
        lib.TestCase("T1-F12-03", 1, "F12", "R2", "2.4 GHz CLARO1_F3E3B9 profile set to manual connect", t1_f12_03),
        lib.TestCase("T1-F12-04", 1, "F12", "R2", "5 GHz CLARO2_F3E3B9 profile kept on auto-connect", t1_f12_04),
        lib.TestCase("T1-F12-05", 1, "F12", "R2", "gaming_wlan_scan_toggle.ps1 valid & steady-state autoconfig on", t1_f12_05),
        # F13
        lib.TestCase("T1-F13-01", 1, "F13", "R2", "Wi-Fi DNS configured as valid low-latency static pair (1.1.1.1/1.0.0.1 or 9.9.9.9/8.8.8.8)", t1_f13_01),
        lib.TestCase("T1-F13-02", 1, "F13", "R2", "Live warmed Resolve-DnsName succeeds on 1.1.1.1, 9.9.9.9 & 8.8.8.8", t1_f13_02),
        lib.TestCase("T1-F13-03", 1, "F13", "R2", "Delivery Optimization DODownloadMode == 0", t1_f13_03),
        lib.TestCase("T1-F13-04", 1, "F13", "R2", "DoSvc P2P disabled or stopped", t1_f13_04),
        lib.TestCase("T1-F13-05", 1, "F13", "R2", "Zero active P2P/background bandwidth hogs", t1_f13_05),
        # F14
        lib.TestCase("T1-F14-01", 1, "F14", "R2", "Ethernet Green/PowerSaving/GigaLite/IntMod/FlowControl all 0", t1_f14_01),
        lib.TestCase("T1-F14-02", 1, "F14", "R2", "Ethernet MSPower_DeviceEnable False or PnPCapabilities=24", t1_f14_02),
        lib.TestCase("T1-F14-03", 1, "F14", "R2", "TCP global RSS=enabled, AutoTuning=normal, RSC/ECN/Timestamps/NonSack=disabled & Wi-Fi RSC off", t1_f14_03),
        lib.TestCase("T1-F14-04", 1, "F14", "R2", "Ethernet Checksum Offload RxTxEnabled & global RSS Enabled", t1_f14_04),
        lib.TestCase("T1-F14-05", 1, "F14", "R2", "TcpAckFrequency=1 & TCPNoDelay=1 on Wi-Fi (and Ethernet if present)", t1_f14_05),
        lib.TestCase("T1-F14-06", 1, "F14", "R2", "Multimedia SystemProfile & Fortnite_Gaming_QoS DSCP 46 active", t1_f14_06),
        # F15
        lib.TestCase("T1-F15-01", 1, "F15", "R3", "post_system.json exists & matches schema contract", t1_f15_01),
        lib.TestCase("T1-F15-02", 1, "F15", "R3", "post_network.json exists & matches schema contract", t1_f15_02),
        lib.TestCase("T1-F15-03", 1, "F15", "R3", "comparison_report.md exists with system & network metrics", t1_f15_03),
        lib.TestCase("T1-F15-04", 1, "F15", "R1,R3", "Zero new nvlddmkm 153/13 or Kernel-Power 41 post-apply", t1_f15_04),
        lib.TestCase("T1-F15-05", 1, "F15", "R3", "generate_comparison_report.py compiles & apply/verify logs exist", t1_f15_05),
    ]
