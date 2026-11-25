"""Tier 3: Cross-Feature Pairwise Combinatorial Tests (>= 15 tests).

Covers interactions across R1<->R2, R1<->R3, R2<->R3, and intra-track pairs.
"""

from __future__ import annotations

from pathlib import Path
import re
import time
import winreg

import e2e_lib as lib


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


def _check_do_download_mode() -> tuple[bool, tuple[object, object]]:
    ex1, v1, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode"
    )
    ex2, v2, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\DeliveryOptimization\Config", "DODownloadMode"
    )
    has_zero = (ex1 and lib.is_exact_int(v1) and v1 == 0) or (ex2 and lib.is_exact_int(v2) and v2 == 0)
    neither_nonzero = (not ex1 or (lib.is_exact_int(v1) and v1 == 0)) and (not ex2 or (lib.is_exact_int(v2) and v2 == 0))
    return (has_zero and neither_nonzero), (v1, v2)


def _gw_ok(gw: dict) -> bool:
    if not isinstance(gw, dict) or not gw.get("ok"):
        return False
    loss = gw.get("loss_pct")
    p1 = gw.get("packet1_ms")
    p50 = gw.get("p50_ms")
    p95 = gw.get("p95_ms")
    return bool(
        lib.is_finite_num(loss)
        and float(loss) == 0.0
        and lib.is_finite_num(p1)
        and 0.0 <= float(p1) < 15.0
        and lib.is_finite_num(p50)
        and 0.0 <= float(p50) < 10.0
        and lib.is_finite_num(p95)
        and 0.0 <= float(p95) < 15.0
    )


def t3_pw_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F1 x F4: Backup-to-rollback completeness."""
    rb = ctx.project_root / "scripts/rollback_all.ps1"
    if not rb.is_file():
        return False, "Missing scripts/rollback_all.ps1"
    ast_res = lib.check_ps_ast(rb)
    ast_ok = bool(ast_res.get("ok"))
    txt = lib.read_text_auto(rb).lower()
    expected_artifacts = [
        ctx.project_root / "backups/network/wifi_driver_reg.reg",
        ctx.project_root / "backups/network/eth_driver_reg.reg",
        ctx.project_root / "backups/network/tcpip_params.reg",
        ctx.project_root / "backups/network/dns_before.json",
        ctx.project_root / "backups/network/wlan_profiles_before.txt",
        ctx.project_root / "backups/system/services_baseline.json",
        ctx.project_root / "backups/system/startup_baseline.json",
    ]
    missing = [p.name for p in expected_artifacts if not p.is_file() or p.stat().st_size == 0]
    for p in expected_artifacts:
        if p.suffix.lower() == ".reg" and p.is_file() and p.stat().st_size > 0:
            reg_ok, _ = lib.is_valid_reg_export(p)
            if not reg_ok and p.name not in missing:
                missing.append(f"{p.name}(invalid_reg)")
    pows = list((ctx.project_root / "backups/power").glob("*.pow")) if (ctx.project_root / "backups/power").is_dir() else []
    valid_pows = [p for p in pows if p.is_file() and p.stat().st_size > 0]
    if not pows or len(valid_pows) != len(pows):
        missing.append("*.pow")
    refs_ok = ("backups" in txt) and (".reg" in txt) and (".pow" in txt)
    ok = len(missing) == 0 and refs_ok and ast_ok
    return ok, f"missing_backups={missing}, rollback_refs_ok={refs_ok}, rollback_ast_ok={ast_ok}"


def t3_pw_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F2 x F15: System baseline & post schema parity + timestamp progression."""
    ok1, b_sys, m1 = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    ok2, p_sys, m2 = lib.load_json_file(ctx.project_root / "reports/post_system.json")
    if not ok1 or not ok2:
        return False, m1 or m2
    if not isinstance(b_sys, dict) or not isinstance(p_sys, dict):
        return False, f"Invalid JSON root types: base={type(b_sys).__name__}, post={type(p_sys).__name__}"
    v1, e1 = lib.validate_system_json(b_sys, "baseline")
    v2, e2 = lib.validate_system_json(p_sys, "post")
    dt1 = lib.parse_utc_iso(b_sys.get("timestamp_utc", ""))
    dt2 = lib.parse_utc_iso(p_sys.get("timestamp_utc", ""))
    ok = v1 and v2 and dt1 is not None and dt2 is not None and dt2 > dt1
    return ok, f"base_valid={v1}, post_valid={v2}, ts_ordered={dt1 is not None and dt2 is not None and dt2 > dt1}"


def t3_pw_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F3 x F15: Network baseline & post schema parity + timestamp progression."""
    ok1, b_net, m1 = lib.load_json_file(ctx.project_root / "reports/baseline_network.json")
    ok2, p_net, m2 = lib.load_json_file(ctx.project_root / "reports/post_network.json")
    if not ok1 or not ok2:
        return False, m1 or m2
    if not isinstance(b_net, dict) or not isinstance(p_net, dict):
        return False, f"Invalid JSON root types: base={type(b_net).__name__}, post={type(p_net).__name__}"
    v1, _ = lib.validate_network_json(b_net, "baseline")
    v2, _ = lib.validate_network_json(p_net, "post")
    dt1 = lib.parse_utc_iso(b_net.get("timestamp_utc", ""))
    dt2 = lib.parse_utc_iso(p_net.get("timestamp_utc", ""))
    ts_ordered = dt1 is not None and dt2 is not None and dt2 > dt1
    conn_ok = p_net.get("connectivity_ok") is True
    gw_ping = p_net.get("gateway_ping")
    gw_loss = gw_ping.get("loss_pct") if isinstance(gw_ping, dict) else None
    gw_ok = lib.is_finite_num(gw_loss) and float(gw_loss) < 100.0
    pubs = p_net.get("public_pings", [])
    pub_ok = isinstance(pubs, list) and any(
        isinstance(item, dict)
        and isinstance(item.get("loss_pct"), (int, float))
        and not isinstance(item.get("loss_pct"), bool)
        and float(item["loss_pct"]) < 100.0
        for item in pubs
    )
    ok = v1 and v2 and ts_ordered and conn_ok and gw_ok and pub_ok
    return ok, (
        f"net_base_valid={v1}, net_post_valid={v2}, ts_ordered={ts_ordered}, "
        f"post_conn={conn_ok}, gw_loss={gw_loss}, pub_reachable={pub_ok}"
    )


def t3_pw_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F1 x F2 x F3: Complete baseline package (backups + both baseline JSONs)."""
    sys_dir = ctx.project_root / "backups/system"
    net_dir = ctx.project_root / "backups/network"
    pow_dir = ctx.project_root / "backups/power"
    sys_regs = list(sys_dir.glob("*.reg")) if sys_dir.is_dir() else []
    net_regs = list(net_dir.glob("*.reg")) if net_dir.is_dir() else []
    pows = list(pow_dir.glob("*.pow")) if pow_dir.is_dir() else []
    sys_bkp_ok = bool(sys_regs) and all(lib.is_valid_reg_export(p)[0] for p in sys_regs)
    net_bkp_ok = bool(net_regs) and all(lib.is_valid_reg_export(p)[0] for p in net_regs)
    pow_bkp_ok = bool(pows) and all(p.is_file() and p.stat().st_size > 0 for p in pows)
    has_bkp = sys_bkp_ok and net_bkp_ok and pow_bkp_ok
    ok_s, b_sys, _ = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    ok_n, b_net, _ = lib.load_json_file(ctx.project_root / "reports/baseline_network.json")
    v_s, _ = lib.validate_system_json(b_sys, "baseline") if ok_s else (False, [])
    v_n, _ = lib.validate_network_json(b_net, "baseline") if ok_n else (False, [])
    ok = has_bkp and v_s and v_n
    return ok, (
        f"backups_valid={has_bkp}(sys={sys_bkp_ok},net={net_bkp_ok},pow={pow_bkp_ok}), "
        f"baseline_sys={v_s}, baseline_net={v_n}"
    )


def t3_pw_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F5 x F8: Dual-track GameDVR suppression (HKCU hooks + HKLM policy)."""
    _, gd_user, _ = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
    _, ac_user, _ = lib.read_reg_value(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled"
    )
    _, allow_pol, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR"
    )
    ok = (
        lib.is_exact_int(gd_user) and gd_user == 0
        and lib.is_exact_int(ac_user) and ac_user == 0
        and lib.is_exact_int(allow_pol) and allow_pol == 0
    )
    return ok, f"HKCU GameDVR={gd_user}, AppCapture={ac_user}, HKLM AllowGameDVR={allow_pol}"


def t3_pw_06(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F5 x F6: Visual/GPU user hooks paired with absent overlays."""
    _, tr, _ = lib.read_reg_value(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency"
    )
    _, vfx, _ = lib.read_reg_value(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting"
    )
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh_snapshot failed: {snap.get('error', 'unknown')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    overlays = sorted(running & {"NVIDIA Overlay", "AMDRSSrcExt", "RadeonSoftware"})
    ok = lib.is_exact_int(tr) and tr == 0 and lib.is_exact_int(vfx) and vfx == 2 and len(overlays) == 0
    return ok, f"EnableTransparency={tr}, VisualFXSetting={vfx}, overlays={overlays}"


def t3_pw_07(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F6 x F9: Bloat process removal paired with bloat service & task disablement."""
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, snap.get("error", "pwsh error")
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    svcs = snap.get("services")
    if not isinstance(svcs, dict):
        return False, f"services snapshot missing/invalid: {type(svcs).__name__}"
    pcm = svcs.get("PCManager Service Store")
    if not isinstance(pcm, dict):
        pcm = {}
    tasks_raw = snap.get("tasks")
    if not isinstance(tasks_raw, list):
        return False, f"tasks snapshot missing/invalid: {type(tasks_raw).__name__}"
    od_tasks = [
        t for t in tasks_raw
        if isinstance(t, dict) and str(t.get("TaskName") or "").startswith("OneDrive") and t.get("State") != "Disabled"
    ]
    ok = ("MSPCManagerService" not in running) and (pcm.get("StartType") in ("Disabled", "NOT_FOUND")) and len(od_tasks) == 0
    return ok, f"MSPCManager running={'MSPCManagerService' in running}, PCManager svc={pcm}, active_OneDrive_tasks={len(od_tasks)}"


def t3_pw_08(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F7 x F8: Gaming Estable power (PROCTHROTTLEMAX<=99, PERFBOOSTMODE=0, SYSCOOLPOL=1) + PowerThrottlingOff paired with NVIDIA DRS MaxPerf & AMD ULPS off."""
    sch = ctx.power_scheme()
    proc_max = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PROC_MAX)
    boost = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PERF_BOOST_MODE)
    cool = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_SYS_COOL_POL)
    _, pt, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff"
    )
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    pm_ent = settings.get(lib.NVAPI_DRS_POWER_MGMT) if isinstance(drs, dict) and drs.get("ok") and isinstance(settings, dict) else None
    pwr_mgmt = pm_ent.get("current_u32") if isinstance(pm_ent, dict) else None
    amd_sub = lib.find_display_class_subkey("AMD")
    _, ulps, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, amd_sub, "EnableUlps") if amd_sub else (False, None, None)
    pmax = proc_max.get("ac")
    boost_ac = boost.get("ac")
    cool_ac = cool.get("ac")
    ok = (
        lib.is_gaming_stable_scheme(sch)
        and bool(proc_max.get("ok"))
        and lib.is_exact_int(pmax) and 0 <= pmax <= 99
        and bool(boost.get("ok"))
        and lib.is_exact_int(boost_ac) and boost_ac == 0
        and bool(cool.get("ok"))
        and lib.is_exact_int(cool_ac) and cool_ac == 1
        and lib.is_exact_int(pt) and pt == 1
        and lib.is_exact_int(pwr_mgmt) and pwr_mgmt == 1
        and lib.is_exact_int(ulps) and ulps == 0
    )
    return ok, (
        f"scheme={sch.get('guid')} ({sch.get('name')}), MAX={proc_max.get('ac')}, BOOST={boost.get('ac')}, "
        f"COOL={cool.get('ac')}, PowerThrottlingOff={pt}, DRS_Power={pwr_mgmt}, EnableUlps={ulps}"
    )


def t3_pw_09(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F7 x F10: Hibernation off & Win32PrioritySeparation=0x26 paired with VBS/HVCI disabled (or -KeepHypervisor VSM=2 with HVCI off)."""
    hib_absent = not Path(r"C:\hiberfil.sys").exists()
    _, w32, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation"
    )
    ex_vbs, vbs_reg, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    ex_hvci, hvci_reg, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled",
    )
    snap = ctx.pwsh_snapshot()
    dg = snap.get("device_guard") if isinstance(snap, dict) and snap.get("ok") else None
    vbs_rt = dg.get("VirtualizationBasedSecurityStatus") if isinstance(dg, dict) else None
    running_raw = dg.get("SecurityServicesRunning") if isinstance(dg, dict) else None
    running = running_raw if isinstance(running_raw, list) else [2]
    hyp = snap.get("hypervisor_present") if isinstance(snap, dict) and snap.get("ok") else None
    vbs_reg_off = ex_vbs and lib.is_exact_int(vbs_reg) and vbs_reg == 0
    hvci_off = (not ex_hvci) or (lib.is_exact_int(hvci_reg) and hvci_reg == 0)
    hvci_not_running = isinstance(dg, dict) and isinstance(running_raw, list) and (2 not in running)
    vbs_rt_ok = lib.is_exact_int(vbs_rt) and (
        vbs_rt == 0 or (vbs_rt == 2 and hyp is True and vbs_reg_off and hvci_off and hvci_not_running)
    )
    ok = hib_absent and lib.is_exact_int(w32) and w32 == 0x26 and vbs_reg_off and hvci_off and hvci_not_running and vbs_rt_ok
    return ok, (
        f"hiberfil_absent={hib_absent}, Win32Prio={w32}, VBS_reg={vbs_reg}, HVCI_reg={hvci_reg!r}(ex={ex_hvci}), "
        f"VBS_runtime={vbs_rt}, hypervisor_present={hyp}, running={running}"
    )


def t3_pw_10(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F7 x F11: Active power scheme USB suspend off (AC) paired with Wireless PS & PCIe ASPM off (AC & DC)."""
    usb = ctx.powercfg_setting(lib.SUB_USB, lib.SET_USB_SUSPEND)
    wl = ctx.powercfg_setting(lib.SUB_WIRELESS, lib.SET_WIRELESS_PS)
    aspm = ctx.powercfg_setting(lib.SUB_PCIE, lib.SET_PCIE_ASPM)
    u_ac = usb.get("ac")
    w_ac, w_dc = wl.get("ac"), wl.get("dc")
    a_ac, a_dc = aspm.get("ac"), aspm.get("dc")
    ok = (
        bool(usb.get("ok")) and lib.is_exact_int(u_ac) and u_ac == 0
        and bool(wl.get("ok")) and lib.is_exact_int(w_ac) and w_ac == 0 and lib.is_exact_int(w_dc) and w_dc == 0
        and bool(aspm.get("ok")) and lib.is_exact_int(a_ac) and a_ac == 0 and lib.is_exact_int(a_dc) and a_dc == 0
    )
    return ok, f"USB_AC={u_ac}, Wireless={w_ac}/{w_dc}, ASPM={a_ac}/{a_dc}"


def t3_pw_11(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F8 x F15: GPU DRS tuning + healthy nvidia-smi + zero new nvlddmkm 153/13 errors post-apply."""
    smi = ctx.nvidia_smi()
    drs = ctx.nvapi_drs()
    ok_p, post_sys, _ = lib.load_json_file(ctx.project_root / "reports/post_system.json")
    if not ok_p or not isinstance(post_sys, dict):
        return False, "post_system.json missing or non-object for timestamp-scoped nvlddmkm check"
    ts = post_sys.get("timestamp_utc", "")
    ev = lib.query_events_since(ts)
    settings = drs.get("settings") if isinstance(drs, dict) else None
    tex_ent = settings.get(lib.NVAPI_DRS_TEX_FILTER_QUAL) if isinstance(drs, dict) and drs.get("ok") and isinstance(settings, dict) else None
    tex_u32 = tex_ent.get("current_u32") if isinstance(tex_ent, dict) else None
    drs_ok = lib.is_exact_int(tex_u32) and tex_u32 == 0x14
    temp_val = smi.get("temp_c") if isinstance(smi, dict) and smi.get("ok") else None
    temp_ok = lib.is_finite_num(temp_val) and float(temp_val) < 85.0
    nv_cnt = ev.get("nvlddmkm_count")
    ok = bool(isinstance(smi, dict) and smi.get("ok")) and temp_ok and drs_ok and bool(ev.get("ok")) and lib.is_exact_int(nv_cnt) and nv_cnt == 0
    return ok, f"temp={temp_val}C, drs_ok={drs_ok}, new_nvlddmkm={nv_cnt}"


def t3_pw_12(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F10 x F15: VBS off paired with post_system RAM/CPU integrity & zero new Kernel-Power 41."""
    _, vbs_reg, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    ok_p, post_sys, msg = lib.load_json_file(ctx.project_root / "reports/post_system.json")
    if not ok_p:
        return False, msg
    if not isinstance(post_sys, dict):
        return False, f"post_system.json root expected object, got {type(post_sys).__name__}"
    so = post_sys.get("system_overhead")
    if not isinstance(so, dict):
        return False, f"post_system.json system_overhead missing or non-object: {type(so).__name__}"
    ram_raw = so.get("available_ram_mb")
    if not lib.is_finite_num(ram_raw):
        return False, f"post_system.json system_overhead.available_ram_mb invalid: {ram_raw!r}"
    ram = float(ram_raw)
    ev = lib.query_events_since(post_sys.get("timestamp_utc", ""))
    kp_cnt = ev.get("kernel_power_count")
    ok = (
        isinstance(vbs_reg, int)
        and not isinstance(vbs_reg, bool)
        and vbs_reg == 0
        and ram > 8000
        and bool(ev.get("ok"))
        and isinstance(kp_cnt, int)
        and not isinstance(kp_cnt, bool)
        and kp_cnt == 0
    )
    return ok, f"VBS_reg={vbs_reg}, post_avail_ram={ram}MB, new_KP41={kp_cnt}"


def t3_pw_13(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F11 x F12: Wi-Fi LpsEn=0 + PnP=24 paired with Roam=127 & 5 GHz lock -> clean live gateway ping."""
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, snap.get("error", "pwsh error")
    lps = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    roam = _get_adv_val(snap, "Wi-Fi", "RegROAMSensitiveLevel")
    is_5g = "5 ghz" in str(snap.get("wlan_interfaces") or "").lower()

    pings = ctx.live_pings()
    gw = pings.get("192.168.1.1") if isinstance(pings, dict) else None
    if not isinstance(gw, dict):
        gw = {}
    cfg_ok = lps == "0" and roam == "127" and is_5g
    gw_pass = _gw_ok(gw)
    retried = False
    if cfg_ok and not gw_pass and gw.get("ok") and lib.is_finite_num(gw.get("loss_pct")) and float(gw.get("loss_pct")) == 0.0:
        time.sleep(0.2)
        gw = lib.run_live_ping_burst("192.168.1.1", count=30, interval_ms=100)
        gw_pass = _gw_ok(gw)
        retried = True
        if gw_pass and isinstance(pings, dict):
            pings["192.168.1.1"] = gw
    ok = cfg_ok and gw_pass
    return ok, (
        f"LpsEn={lps}, Roam={roam}, 5GHz={is_5g}, gw_p1={gw.get('packet1_ms')}ms, "
        f"p50={gw.get('p50_ms')}ms, p95={gw.get('p95_ms')}ms, loss={gw.get('loss_pct')}%, retried={retried}"
    )


def t3_pw_14(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F11 x F13: Wi-Fi power-saving off paired with valid static gaming DNS (1.1.1.1/1.0.0.1 or 9.9.9.9/8.8.8.8), DoSvc=0, zero P2P."""
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, snap.get("error", "pwsh error")
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    lps = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    do_ok, do_vals = _check_do_download_mode()
    hogs = [p.get("ProcessName") for p in procs_raw if isinstance(p, dict) and p.get("ProcessName") in lib.TARGET_P2P_PROCESSES]
    ok = lps == "0" and lib.is_valid_static_gaming_dns(srvs) and do_ok and len(hogs) == 0
    return ok, f"LpsEn={lps}, DNS={srvs[:2]}, DoSvc=({do_vals[0]}/{do_vals[1]}), P2P_hogs={hogs}"


def t3_pw_15(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F13 x F14: Static gaming DNS/DoSvc + RSC off/AutoTuning=normal/Nagle off/MMCSS/QoS -> fast DNS & HTTPS."""
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, snap.get("error", "pwsh error")
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    dns_cfg_ok = lib.is_valid_static_gaming_dns(srvs)
    do_ok, (v1, v2) = _check_do_download_mode()
    tcp = str(snap.get("tcp_global") or "")
    at_normal = bool(re.search(r"(?im)^\s*receive window auto-tuning level\s*:\s*normal\b", tcp))
    rsc_off = bool(re.search(r"(?im)^\s*receive segment coalescing state\s*:\s*disabled\b", tcp))
    wifi_rsc = snap.get("wifi_rsc") or []
    nic_rsc_off = isinstance(wifi_rsc, list) and bool(wifi_rsc) and all(
        isinstance(item, dict) and item.get("IPv4Enabled") is False and item.get("IPv6Enabled") is False for item in wifi_rsc
    )
    guid_w = _get_adapter_guid(snap, "Wi-Fi") or ""
    _, ack, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{guid_w}", "TcpAckFrequency"
    )
    _, nd, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{guid_w}", "TCPNoDelay"
    )
    mm_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    _, nti, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "NetworkThrottlingIndex")
    _, sr, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "SystemResponsiveness")
    qos_ok, qos_msg = lib.check_fortnite_qos(snap)
    dns = ctx.live_dns()
    primary_srv = srvs[0] if srvs and isinstance(dns, dict) and srvs[0] in dns else "1.1.1.1"
    dns_ent = dns.get(primary_srv) if isinstance(dns, dict) else None
    https = ctx.live_https()
    ok = (
        dns_cfg_ok
        and do_ok
        and at_normal
        and rsc_off
        and nic_rsc_off
        and lib.is_exact_int(ack) and ack == 1
        and lib.is_exact_int(nd) and nd == 1
        and lib.is_exact_int(nti) and nti == 0xFFFFFFFF
        and lib.is_exact_int(sr) and sr == 0
        and qos_ok
        and isinstance(dns_ent, dict) and bool(dns_ent.get("ok"))
        and isinstance(https, dict) and bool(https.get("ok"))
    )
    return ok, (
        f"DNS_wifi={srvs[:2]}, DoSvc=({v1}/{v2}), AutoTuning={at_normal}, RSC_off={rsc_off}, WiFi_RSC_off={nic_rsc_off}, "
        f"TcpAckFrequency={ack}, TCPNoDelay={nd}, NTI={nti}, SR={sr}, QoS=({qos_msg}), "
        f"DNS({primary_srv})={dns_ent.get('avg_ms') if isinstance(dns_ent, dict) else None}ms, "
        f"HTTPS={https.get('status_code') if isinstance(https, dict) else None}"
    )


def t3_pw_16(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """F3 x F11 x F13: Wi-Fi LPS=0 + static DNS/DoSvc=0/zero P2P + valid post_network.json -> clean gateway & public DNS pings."""
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh_snapshot failed: {snap.get('error', 'unknown')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    lps = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    do_ok, do_vals = _check_do_download_mode()
    hogs = [p.get("ProcessName") for p in procs_raw if isinstance(p, dict) and p.get("ProcessName") in lib.TARGET_P2P_PROCESSES]
    ok_p, p_net, _ = lib.load_json_file(ctx.project_root / "reports/post_network.json")
    v_net, _ = lib.validate_network_json(p_net, "post") if ok_p else (False, [])
    post_net_ok = v_net and isinstance(p_net, dict) and (p_net.get("connectivity_ok") is True)
    cfg_ok = (lps == "0") and lib.is_valid_static_gaming_dns(srvs) and do_ok and (len(hogs) == 0) and post_net_ok

    pings = ctx.live_pings()
    if not isinstance(pings, dict):
        return False, "Live ping burst invalid"
    gw = pings.get("192.168.1.1")
    q9 = pings.get("9.9.9.9")
    g8 = pings.get("8.8.8.8")
    cf = pings.get("1.1.1.1")
    if not (
        isinstance(gw, dict) and gw.get("ok")
        and isinstance(q9, dict) and q9.get("ok")
        and isinstance(g8, dict) and g8.get("ok")
        and isinstance(cf, dict) and cf.get("ok")
    ):
        return False, "Live ping burst incomplete"

    gw_ok = _gw_ok(gw)
    gw_retried = False
    if cfg_ok and not gw_ok and gw.get("ok") and lib.is_finite_num(gw.get("loss_pct")) and float(gw.get("loss_pct")) == 0.0:
        time.sleep(0.2)
        gw = lib.run_live_ping_burst("192.168.1.1", count=30, interval_ms=100)
        gw_ok = _gw_ok(gw)
        gw_retried = True
        if gw_ok:
            pings["192.168.1.1"] = gw

    pub_map = {"9.9.9.9": dict(q9), "8.8.8.8": dict(g8), "1.1.1.1": dict(cf)}

    def _pub_eval(pmap: dict[str, dict]) -> tuple[bool, list[float], list[object]]:
        avgs: list[float] = []
        losses: list[object] = []
        all_valid = True
        for ip in ("9.9.9.9", "8.8.8.8", "1.1.1.1"):
            entry = pmap[ip]
            if not isinstance(entry, dict) or not entry.get("ok"):
                all_valid = False
                losses.append(entry.get("loss_pct") if isinstance(entry, dict) else None)
                continue
            loss_raw = entry.get("loss_pct")
            avg_raw = entry.get("avg_ms")
            losses.append(loss_raw)
            if not lib.is_finite_num(loss_raw) or float(loss_raw) != 0.0:
                all_valid = False
            if not lib.is_finite_num(avg_raw) or float(avg_raw) < 0.0:
                all_valid = False
            else:
                avgs.append(float(avg_raw))
        passed = all_valid and len(avgs) == 3 and min(avgs) < 80.0 and max(avgs) < 120.0
        return passed, avgs, losses

    pub_ok, avgs, _ = _pub_eval(pub_map)
    pub_retried: list[str] = []
    if cfg_ok and not pub_ok:
        for ip in ("9.9.9.9", "8.8.8.8", "1.1.1.1"):
            entry = pub_map[ip]
            loss = entry.get("loss_pct") if isinstance(entry, dict) else None
            avg = entry.get("avg_ms") if isinstance(entry, dict) else None
            is_transient_loss = (
                isinstance(entry, dict)
                and bool(entry.get("ok"))
                and lib.is_finite_num(loss)
                and 0.0 < float(loss) < 50.0
            )
            is_transient_spike = (
                isinstance(entry, dict)
                and bool(entry.get("ok"))
                and lib.is_finite_num(loss)
                and float(loss) == 0.0
                and lib.is_finite_num(avg)
                and float(avg) >= 0.0
                and (float(avg) >= 120.0 or (len(avgs) == 3 and min(avgs) >= 80.0))
            )
            if is_transient_loss or is_transient_spike:
                time.sleep(0.2)
                pub_map[ip] = lib.run_live_ping_burst(ip, count=30, interval_ms=80)
                pub_retried.append(ip)
        if pub_retried:
            pub_ok, avgs, _ = _pub_eval(pub_map)

    ok = cfg_ok and gw_ok and pub_ok
    return ok, (
        f"LpsEn={lps}, DNS={srvs[:2]}, DoSvc=({do_vals[0]}/{do_vals[1]}), P2P_hogs={hogs}, post_net_ok={post_net_ok}, "
        f"gw_p1={gw.get('packet1_ms')}ms(p50={gw.get('p50_ms')},p95={gw.get('p95_ms')},retried={gw_retried}), "
        f"9.9.9.9={pub_map['9.9.9.9'].get('avg_ms')}ms(loss={pub_map['9.9.9.9'].get('loss_pct')}%), "
        f"8.8.8.8={pub_map['8.8.8.8'].get('avg_ms')}ms(loss={pub_map['8.8.8.8'].get('loss_pct')}%), "
        f"1.1.1.1={pub_map['1.1.1.1'].get('avg_ms')}ms(loss={pub_map['1.1.1.1'].get('loss_pct')}%), "
        f"pub_retried={pub_retried}"
    )


def get_tests() -> list[lib.TestCase]:
    return [
        lib.TestCase("T3-PW-01", 3, "F1xF4", "R3", "Backup-to-rollback completeness", t3_pw_01),
        lib.TestCase("T3-PW-02", 3, "F2xF15", "R3", "System baseline & post schema parity + ordering", t3_pw_02),
        lib.TestCase("T3-PW-03", 3, "F3xF15", "R2,R3", "Network baseline & post schema parity + ordering", t3_pw_03),
        lib.TestCase("T3-PW-04", 3, "F1xF2xF3", "R3", "Complete baseline package (backups + sys + net)", t3_pw_04),
        lib.TestCase("T3-PW-05", 3, "F5xF8", "R1", "Dual-track GameDVR suppression (HKCU + HKLM)", t3_pw_05),
        lib.TestCase("T3-PW-06", 3, "F5xF6", "R1", "Visual/GPU user hooks + absent idle overlays", t3_pw_06),
        lib.TestCase("T3-PW-07", 3, "F6xF9", "R1", "Startup/process bloat + service/task cleanup", t3_pw_07),
        lib.TestCase("T3-PW-08", 3, "F7xF8", "R1", "Gaming Estable power (MAX<=99, BOOST=0, COOL=1) + DRS MaxPerf + AMD ULPS off", t3_pw_08),
        lib.TestCase("T3-PW-09", 3, "F7xF10", "R1", "Hibernation off + Win32Prio=0x26 + VBS/HVCI off (-KeepHypervisor aligned)", t3_pw_09),
        lib.TestCase("T3-PW-10", 3, "F7xF11", "R1,R2", "USB suspend AC=0 + Wireless PS/ASPM DC=0", t3_pw_10),
        lib.TestCase("T3-PW-11", 3, "F8xF15", "R1,R3", "GPU DRS + healthy nvidia-smi + zero new nvlddmkm", t3_pw_11),
        lib.TestCase("T3-PW-12", 3, "F10xF15", "R1,R3", "VBS off + post RAM health + zero new Kernel-Power 41", t3_pw_12),
        lib.TestCase("T3-PW-13", 3, "F11xF12", "R2", "Wi-Fi LPS off + Roam=127 + 5 GHz -> clean gateway ping", t3_pw_13),
        lib.TestCase("T3-PW-14", 3, "F11xF13", "R2", "Wi-Fi LPS off + static gaming DNS + DoSvc=0 + zero P2P", t3_pw_14),
        lib.TestCase("T3-PW-15", 3, "F13xF14", "R2", "Static DNS/DoSvc + RSC off/TCP/MMCSS/QoS -> fast DNS & HTTPS 204", t3_pw_15),
        lib.TestCase("T3-PW-16", 3, "F3xF11xF13", "R2,R3", "Live ping wake-spike eliminated & public DNS targets healthy", t3_pw_16),
    ]
