"""Tier 4: Real-World Application Scenarios (S1-S8 from TEST_INFRA.md)."""

from __future__ import annotations

import json
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


def t4_s01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S1: Competitive Online Gaming Session Readiness (F5, F6, F7, F8, F11, F12, F13)."""
    failures = []

    # F5: GameDVR + VisualFX + dGPU preference
    _, gd, _ = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
    _, ac, _ = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled")
    _, vfx, _ = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting")
    gpu_prefs = lib.enum_reg_values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences")
    has_dgpu = any("fifa22.exe" in k.lower() and "gpupreference=2;" in str(v[0]).lower() for k, v in gpu_prefs.items())
    if (
        not lib.is_exact_int(gd) or gd != 0
        or not lib.is_exact_int(ac) or ac != 0
        or not lib.is_exact_int(vfx) or vfx != 2
        or not has_dgpu
    ):
        failures.append(f"F5(gd={gd},ac={ac},vfx={vfx},dgpu={has_dgpu})")

    # F6: Overlays absent
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot failed: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    bad_procs = sorted(running & (set(lib.TARGET_OVERLAY_PROCESSES) | set(lib.TARGET_P2P_PROCESSES)))
    if bad_procs:
        failures.append(f"F6/F13_procs={bad_procs}")

    # F7: Gaming Estable (Sin Bateria) anti-shutdown power plan + USB AC off + Win32Prio=0x26
    sch = ctx.power_scheme()
    proc_max = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PROC_MAX)
    boost = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PERF_BOOST_MODE)
    cool = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_SYS_COOL_POL)
    usb = ctx.powercfg_setting(lib.SUB_USB, lib.SET_USB_SUSPEND)
    _, w32, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation")
    pmax = proc_max.get("ac")
    boost_ac = boost.get("ac")
    cool_ac = cool.get("ac")
    usb_ac = usb.get("ac")
    if (
        not lib.is_gaming_stable_scheme(sch)
        or not proc_max.get("ok")
        or not (lib.is_exact_int(pmax) and 0 <= pmax <= 99)
        or not boost.get("ok")
        or not (lib.is_exact_int(boost_ac) and boost_ac == 0)
        or not cool.get("ok")
        or not (lib.is_exact_int(cool_ac) and cool_ac == 1)
        or not usb.get("ok")
        or not (lib.is_exact_int(usb_ac) and usb_ac == 0)
        or not (lib.is_exact_int(w32) and w32 == 0x26)
    ):
        failures.append(
            f"F7(sch={sch.get('guid')} ({sch.get('name')}),max={proc_max.get('ac')},"
            f"boost={boost.get('ac')},cool={cool.get('ac')},usb_ac={usb.get('ac')},w32={w32})"
        )

    # F8: NVIDIA DRS MaxPerf + LowLatency + TexQual
    drs = ctx.nvapi_drs()
    st = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(st, dict):
        failures.append("F8_drs_unavailable")
    else:
        pm_ent = st.get(lib.NVAPI_DRS_POWER_MGMT)
        ll_ent = st.get(lib.NVAPI_DRS_LOW_LATENCY)
        pm_u32 = pm_ent.get("current_u32") if isinstance(pm_ent, dict) else None
        ll_u32 = ll_ent.get("current_u32") if isinstance(ll_ent, dict) else None
        if not lib.is_exact_int(pm_u32) or pm_u32 != 1 or not lib.is_exact_int(ll_u32) or ll_u32 != 1:
            failures.append("F8_drs_not_maxperf_lowlat")

    # F11/F12/F13/QoS: Wi-Fi LpsEn=0, Roam=127, 5 GHz, valid static DNS, QoS DSCP 46
    lps = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    roam = _get_adv_val(snap, "Wi-Fi", "RegROAMSensitiveLevel")
    is_5g = "5 ghz" in str(snap.get("wlan_interfaces") or "").lower()
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    qos_ok, qos_msg = lib.check_fortnite_qos(snap)
    if lps != "0" or roam != "127" or not is_5g or not lib.is_valid_static_gaming_dns(srvs) or not qos_ok:
        failures.append(f"Net(lps={lps},roam={roam},5g={is_5g},dns={srvs[:2]},qos={qos_msg})")

    return len(failures) == 0, "Gaming session readiness 100% verified" if not failures else "Failures: " + "; ".join(failures)


def _num_lt(v: object, upper: float) -> bool:
    return lib.is_finite_num(v) and 0.0 <= float(v) < upper


def _gw_ping_ok(gw: dict) -> bool:
    return (
        isinstance(gw, dict)
        and bool(gw.get("ok"))
        and lib.is_finite_num(gw.get("loss_pct"))
        and float(gw.get("loss_pct")) == 0.0
        and _num_lt(gw.get("packet1_ms"), 15.0)
        and _num_lt(gw.get("p50_ms"), 10.0)
        and _num_lt(gw.get("p95_ms"), 15.0)
    )


def t4_s02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S2: Low-Jitter Ping & DNS Resolution Under Active Load (F3, F11, F12, F13)."""
    gw = lib.run_live_ping_burst("192.168.1.1", count=30, interval_ms=100)
    retried = False
    if (
        isinstance(gw, dict)
        and gw.get("ok")
        and lib.is_finite_num(gw.get("loss_pct"))
        and float(gw.get("loss_pct")) == 0.0
        and not _gw_ping_ok(gw)
    ):
        time.sleep(0.2)
        gw = lib.run_live_ping_burst("192.168.1.1", count=30, interval_ms=100)
        retried = True
    q9 = lib.run_live_ping_burst("9.9.9.9", count=30, interval_ms=100)
    dns = ctx.live_dns()
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh snapshot error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    _, do_pol, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode"
    )
    hogs = [p.get("ProcessName") for p in procs_raw if isinstance(p, dict) and p.get("ProcessName") in lib.TARGET_P2P_PROCESSES]

    errs = []
    gw_loss = gw.get("loss_pct") if isinstance(gw, dict) else None
    if not isinstance(gw, dict) or not gw.get("ok") or not lib.is_finite_num(gw_loss) or float(gw_loss) != 0.0:
        errs.append(f"gw_loss={gw_loss}%")
    elif not _gw_ping_ok(gw):
        errs.append(f"gw_spike/high(p1={gw.get('packet1_ms')},p50={gw.get('p50_ms')},p95={gw.get('p95_ms')})")
    q9_loss = q9.get("loss_pct") if isinstance(q9, dict) else None
    if not isinstance(q9, dict) or not q9.get("ok") or not lib.is_finite_num(q9_loss) or float(q9_loss) != 0.0:
        errs.append(f"q9_loss={q9_loss}%")
    elif not _num_lt(q9.get("p95_ms"), 120.0):
        errs.append(f"q9_high_p95={q9.get('p95_ms')}")
    q9_dns = dns.get("9.9.9.9") if isinstance(dns, dict) else None
    cf_dns = dns.get("1.1.1.1") if isinstance(dns, dict) else None
    if not isinstance(q9_dns, dict) or not q9_dns.get("ok") or not isinstance(cf_dns, dict) or not cf_dns.get("ok"):
        errs.append("dns_9.9.9.9_or_1.1.1.1_failed")
    if not lib.is_exact_int(do_pol) or do_pol != 0:
        errs.append(f"DODownloadMode={do_pol}")
    if hogs:
        errs.append(f"P2P_hogs={hogs}")

    return len(errs) == 0, (
        f"gw(p1={gw.get('packet1_ms')},p50={gw.get('p50_ms')},p95={gw.get('p95_ms')},jit={gw.get('jitter_ms')},retried={retried}), "
        f"9.9.9.9(avg={q9.get('avg_ms')},p95={q9.get('p95_ms')}), dns={q9_dns.get('avg_ms') if isinstance(q9_dns, dict) else None}ms"
        + ("" if not errs else f" | ERRORS: {errs}")
    )


def t4_s03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S3: Before-vs-After Benchmark Integrity & Comparison Pipeline (F2, F3, F15)."""
    rep = ctx.project_root / "reports"
    files = ["baseline_system.json", "post_system.json", "baseline_network.json", "post_network.json"]
    loaded = {}
    for fn in files:
        ok, data, msg = lib.load_json_file(rep / fn)
        if not ok:
            return False, msg
        loaded[fn] = data

    errs = []
    for fn, tag in [("baseline_system.json", "baseline"), ("post_system.json", "post")]:
        doc = loaded[fn]
        if not isinstance(doc, dict):
            errs.append(f"{fn}:non_object_root")
            continue
        v, e = lib.validate_system_json(doc, tag)
        if not v:
            errs.append(f"{fn}:{e[:2]}")
        gb = doc.get("gpu_bench")
        if not isinstance(gb, dict) or "1650" not in str(gb.get("device_name") or ""):
            errs.append(f"{fn}:wrong_gpu")

    for fn, tag in [("baseline_network.json", "baseline"), ("post_network.json", "post")]:
        doc = loaded[fn]
        if not isinstance(doc, dict):
            errs.append(f"{fn}:non_object_root")
            continue
        v, e = lib.validate_network_json(doc, tag)
        if not v:
            errs.append(f"{fn}:{e[:2]}")
        ad = doc.get("adapter")
        if not isinstance(ad, dict) or str(ad.get("name") or "") != "Wi-Fi":
            errs.append(f"{fn}:wrong_adapter")

    # Strict UTC timestamp ordering: dt_post > dt_base for BOTH system and network
    b_sys = loaded["baseline_system.json"]
    p_sys = loaded["post_system.json"]
    if isinstance(b_sys, dict) and isinstance(p_sys, dict):
        dt_b_sys = lib.parse_utc_iso(b_sys.get("timestamp_utc", ""))
        dt_p_sys = lib.parse_utc_iso(p_sys.get("timestamp_utc", ""))
        if dt_b_sys is None or dt_p_sys is None or dt_p_sys <= dt_b_sys:
            errs.append(f"non_increasing_system_timestamps({dt_b_sys} -> {dt_p_sys})")
        for sec in ("cpu_bench", "gpu_bench", "timer_jitter_ms"):
            if b_sys.get(sec) == p_sys.get(sec):
                errs.append(f"fabricated_identical_system_{sec}")

    b_net = loaded["baseline_network.json"]
    p_net = loaded["post_network.json"]
    if isinstance(b_net, dict) and isinstance(p_net, dict):
        dt_b_net = lib.parse_utc_iso(b_net.get("timestamp_utc", ""))
        dt_p_net = lib.parse_utc_iso(p_net.get("timestamp_utc", ""))
        if dt_b_net is None or dt_p_net is None or dt_p_net <= dt_b_net:
            errs.append(f"non_increasing_network_timestamps({dt_b_net} -> {dt_p_net})")
        for sec in ("gateway_ping", "public_pings"):
            if b_net.get(sec) == p_net.get(sec):
                errs.append(f"fabricated_identical_network_{sec}")

    md_path = rep / "comparison_report.md"
    if not md_path.is_file() or md_path.stat().st_size < 200:
        errs.append("missing_or_short_comparison_report.md")
    else:
        md = md_path.read_text(encoding="utf-8", errors="replace").lower()
        if "baseline-only inspection" in md or "n/a (pending)" in md:
            errs.append("comparison_report_still_baseline_only_pending")
        if "post" not in md or not any(k in md for k in ("delta", "improved", "regressed", "unchanged", "comparison", "changed")):
            errs.append("comparison_report_missing_post_or_delta")
        if "1650" not in md or not any(ip in md for ip in ("9.9.9.9", "1.1.1.1")):
            errs.append("comparison_report_missing_hardware_or_dns_refs")

    return len(errs) == 0, "Before/after benchmark pipeline & comparison_report.md verified" if not errs else f"Errors: {errs}"


def t4_s04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S4: GPU Compute & Stability Health Check (F2, F8, F10, F15)."""
    smi = ctx.nvidia_smi()
    if not smi.get("ok"):
        return False, f"nvidia-smi failed: {smi.get('error')}"
    try:
        import torch
        if not torch.cuda.is_available():
            return False, "torch.cuda.is_available() is False"
        a = torch.randn((512, 512), device="cuda", dtype=torch.float32)
        b = torch.randn((512, 512), device="cuda", dtype=torch.float32)
        c = a @ b
        torch.cuda.synchronize()
        finite = bool(torch.isfinite(c).all().item())
    except Exception as exc:
        return False, f"Live CUDA matmul failed: {exc}"

    amd_sub = lib.find_display_class_subkey("AMD")
    _, ulps, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, amd_sub, "EnableUlps") if amd_sub else (False, None, None)
    _, tdr, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "TdrDelay")
    _, vbs_reg, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity")

    ok_p, post_sys, _ = lib.load_json_file(ctx.project_root / "reports/post_system.json")
    ts = post_sys.get("timestamp_utc") if ok_p and isinstance(post_sys, dict) else None
    ev = lib.query_events_since(ts) if isinstance(ts, str) and ts else {"ok": False}

    errs = []
    if not finite:
        errs.append("cuda_non_finite")
    temp_c = smi.get("temp_c")
    if not lib.is_finite_num(temp_c) or float(temp_c) >= 85.0:
        errs.append(f"bad_or_high_temp={temp_c}")
    if isinstance(ulps, bool) or ulps != 0:
        errs.append(f"AMD_EnableUlps={ulps}")
    if tdr is not None and (isinstance(tdr, bool) or (isinstance(tdr, int) and tdr > 8)):
        errs.append(f"unsafe_TdrDelay={tdr}")
    if isinstance(vbs_reg, bool) or vbs_reg != 0:
        errs.append(f"VBS_reg={vbs_reg}")
    nv_cnt = ev.get("nvlddmkm_count")
    kp_cnt = ev.get("kernel_power_count")
    if (
        not ev.get("ok")
        or isinstance(nv_cnt, bool) or nv_cnt != 0
        or isinstance(kp_cnt, bool) or kp_cnt != 0
    ):
        errs.append(f"post_event_errors={ev}")

    return len(errs) == 0, f"CUDA matmul ok, temp={smi.get('temp_c')}C, ULPS={ulps}, VBS={vbs_reg}, events={ev}" if not errs else f"Errors: {errs}"


def t4_s05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S5: Full Backup & Rollback Readiness Audit (F1, F4, F15)."""
    errs = []
    req_files = [
        ctx.project_root / "backups/system/services_baseline.json",
        ctx.project_root / "backups/system/tasks_baseline.json",
        ctx.project_root / "backups/system/startup_baseline.json",
        ctx.project_root / "backups/system/restore_point_info.json",
        ctx.project_root / "backups/power/power_active_before.txt",
        ctx.project_root / "backups/network/nic_advanced_before.csv",
        ctx.project_root / "backups/network/dns_before.json",
        ctx.project_root / "backups/network/wlan_profiles_before.txt",
        ctx.project_root / "backups/network/wifi_driver_reg.reg",
        ctx.project_root / "backups/network/eth_driver_reg.reg",
        ctx.project_root / "backups/network/tcpip_params.reg",
    ]
    for f in req_files:
        if not f.is_file() or f.stat().st_size == 0:
            errs.append(f"missing:{f.name}")
    if not list((ctx.project_root / "backups/power").glob("*.pow")):
        errs.append("missing:*.pow")
    for s in ("rollback_all.ps1", "gaming_wlan_scan_toggle.ps1", "toggle_vbs_hypervisor.ps1"):
        ast_res = lib.check_ps_ast(ctx.project_root / "scripts" / s)
        if not ast_res.get("exists") or not ast_res.get("ok"):
            errs.append(f"ps_err:{s}")
    return len(errs) == 0, "Full backup & rollback readiness verified" if not errs else f"Issues: {errs}"


def t4_s06(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S6: Background Overhead & Startup Hygiene Audit (F6, F7, F9, F10)."""
    import psutil
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    errs = []
    running = {p.get("ProcessName") for p in procs_raw if isinstance(p, dict)}
    bloat = sorted(running & set(lib.TARGET_OVERLAY_PROCESSES))
    if bloat:
        errs.append(f"overlays_running={bloat}")
    svcs = snap.get("services")
    if not isinstance(svcs, dict):
        errs.append(f"services_snapshot_invalid={type(svcs).__name__}")
    else:
        exists_count = 0
        for svc in ("PCManager Service Store", "MapsBroker", "NetworkDaemon", "DiagTrack", "SysMain"):
            st = svcs.get(svc)
            if isinstance(st, dict) and st.get("Exists"):
                exists_count += 1
                if st.get("StartType") != "Disabled":
                    errs.append(f"{svc}={st.get('StartType')}")
        if exists_count < 4:
            errs.append(f"missing_services(exists={exists_count}/5)")
    if Path(r"C:\hiberfil.sys").exists():
        errs.append("hiberfil_exists")
    _, vbs_reg, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    if not lib.is_exact_int(vbs_reg) or vbs_reg != 0:
        errs.append(f"VBS_reg={vbs_reg}")
    avail_mb = psutil.virtual_memory().available / (1024 * 1024)
    if not lib.is_finite_num(avail_mb):
        errs.append(f"invalid_avail_ram={avail_mb!r}")
    elif avail_mb < 8000:
        errs.append(f"low_avail_ram={avail_mb:.2f}MB<8000MB")

    ok_b, base_sys, b_msg = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    base_avail_mb: float | None = None
    if not ok_b:
        errs.append(f"baseline_system_err={b_msg}")
    elif not isinstance(base_sys, dict):
        errs.append(f"baseline_system_non_object={type(base_sys).__name__}")
    else:
        so = base_sys.get("system_overhead")
        if not isinstance(so, dict):
            errs.append(f"baseline_system_overhead_invalid={type(so).__name__}")
        else:
            base_raw = so.get("available_ram_mb")
            if not lib.is_finite_num(base_raw) or float(base_raw) <= 0:
                errs.append(f"baseline_avail_ram_invalid={base_raw!r}")
            else:
                base_avail_mb = float(base_raw)
                if lib.is_finite_num(avail_mb) and avail_mb <= base_avail_mb:
                    errs.append(f"avail_ram_not_above_baseline={avail_mb:.2f}MB<={base_avail_mb:.2f}MB")

    detail = (
        f"avail_ram={avail_mb:.2f}MB (baseline={base_avail_mb:.2f}MB), hygiene clean"
        if not errs and base_avail_mb is not None
        else f"Issues: {errs}"
    )
    return len(errs) == 0, detail


def t4_s07(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S7: Dual-Interface Network Readiness (F11, F12, F13, F14)."""
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    errs = []
    lps = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    roam = _get_adv_val(snap, "Wi-Fi", "RegROAMSensitiveLevel")
    is_5g = "5 ghz" in str(snap.get("wlan_interfaces") or "").lower()
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    if lps != "0" or roam != "127" or not is_5g or not lib.is_valid_static_gaming_dns(srvs):
        errs.append(f"wifi(lps={lps},roam={roam},5g={is_5g},dns={srvs[:2]})")
    c1 = str(snap.get("wlan_profile_claro1") or "").lower()
    if "not found" not in c1 and "no se encuentra" not in c1 and "connect manually" not in c1 and "conectar manualmente" not in c1:
        errs.append("CLARO1_not_manual")
    eth_present = _get_adapter_guid(snap, "Ethernet") is not None
    if eth_present:
        for kw in ("EnableGreenEthernet", "PowerSavingMode", "GigaLite", "*InterruptModeration", "*FlowControl"):
            if _get_adv_val(snap, "Ethernet", kw) != "0":
                errs.append(f"eth_{kw}!=0")
    targets = ("Wi-Fi", "Ethernet") if eth_present else ("Wi-Fi",)
    for alias in targets:
        g = _get_adapter_guid(snap, alias) or ""
        _, ack, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{g}", "TcpAckFrequency")
        _, nd, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{g}", "TCPNoDelay")
        if not lib.is_exact_int(ack) or ack != 1 or not lib.is_exact_int(nd) or nd != 1:
            errs.append(f"{alias}_tcp(ack={ack},nd={nd})")
    tcp = str(snap.get("tcp_global") or "")
    for label, pat in (
        ("rsc_disabled", r"(?im)^\s*receive segment coalescing state\s*:\s*disabled\b"),
        ("rss_enabled", r"(?im)^\s*receive-side scaling state\s*:\s*enabled\b"),
        ("autotuning_normal", r"(?im)^\s*receive window auto-tuning level\s*:\s*normal\b"),
        ("ecn_disabled", r"(?im)^\s*ecn capability\s*:\s*disabled\b"),
        ("timestamps_disabled", r"(?im)^\s*rfc 1323 timestamps\s*:\s*disabled\b"),
        ("nonsack_disabled", r"(?im)^\s*non sack rtt resiliency\s*:\s*disabled\b"),
    ):
        if not re.search(pat, tcp):
            errs.append(f"tcp_{label}_fail")
    wifi_rsc = snap.get("wifi_rsc") or []
    if not isinstance(wifi_rsc, list) or not wifi_rsc or not all(
        isinstance(item, dict) and item.get("IPv4Enabled") is False and item.get("IPv6Enabled") is False for item in wifi_rsc
    ):
        errs.append(f"wifi_rsc_enabled({wifi_rsc})")
    mm_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    _, nti, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "NetworkThrottlingIndex")
    _, sr, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "SystemResponsiveness")
    if not lib.is_exact_int(nti) or nti != 0xFFFFFFFF or not lib.is_exact_int(sr) or sr != 0:
        errs.append(f"mmcss(nti={nti},sr={sr})")
    qos_ok, qos_msg = lib.check_fortnite_qos(snap)
    if not qos_ok:
        errs.append(f"qos({qos_msg})")
    https = ctx.live_https()
    if not isinstance(https, dict) or not https.get("ok"):
        errs.append(f"https_fail:{https.get('error') if isinstance(https, dict) else https}")
    return len(errs) == 0, f"Dual-interface Wi-Fi+Ethernet tuned & HTTPS {https.get('status_code') if isinstance(https, dict) else None}" if not errs else f"Issues: {errs}"


def t4_s08(ctx: lib.ProbeContext) -> tuple[bool, str]:
    """S8: Workspace Hygiene & Complete Requirement Traceability (F1, F2, F3, F4, F15)."""
    errs = []
    for sub in ("backups", "scripts", "reports", "tests"):
        if not (ctx.project_root / sub).is_dir():
            errs.append(f"missing_dir:{sub}")
    expected_scripts = [
        "backup_baseline.ps1",
        "benchmark_system.py",
        "benchmark_network.ps1",
        "generate_comparison_report.py",
        "apply_os_gpu_user.ps1",
        "apply_os_gpu_admin.ps1",
        "apply_network_user.ps1",
        "apply_network_admin.ps1",
        "gaming_wlan_scan_toggle.ps1",
        "toggle_vbs_hypervisor.ps1",
        "rollback_all.ps1",
    ]
    for s in expected_scripts:
        p = ctx.project_root / "scripts" / s
        if not p.is_file() or p.stat().st_size == 0:
            errs.append(f"missing_script:{s}")
    expected_reports = [
        "baseline_system.json",
        "baseline_network.json",
        "post_system.json",
        "post_network.json",
        "comparison_report.md",
    ]
    for r in expected_reports:
        p = ctx.project_root / "reports" / r
        if not p.is_file() or p.stat().st_size == 0:
            errs.append(f"missing_report:{r}")
    return len(errs) == 0, "All R1/R2/R3 workspace directories, scripts & reports present" if not errs else f"Missing: {errs}"


def get_tests() -> list[lib.TestCase]:
    return [
        lib.TestCase("T4-S01", 4, "F5,F6,F7,F8,F11,F12,F13", "R1,R2", "S1: Competitive Online Gaming Session Readiness", t4_s01),
        lib.TestCase("T4-S02", 4, "F3,F11,F12,F13", "R2,R3", "S2: Low-Jitter Ping & DNS Resolution Under Active Load", t4_s02),
        lib.TestCase("T4-S03", 4, "F2,F3,F15", "R3", "S3: Before-vs-After Benchmark Integrity & Comparison Pipeline", t4_s03),
        lib.TestCase("T4-S04", 4, "F2,F8,F10,F15", "R1,R3", "S4: GPU Compute & Stability Health Check", t4_s04),
        lib.TestCase("T4-S05", 4, "F1,F4,F15", "R1,R2,R3", "S5: Full Backup & Rollback Readiness Audit", t4_s05),
        lib.TestCase("T4-S06", 4, "F6,F7,F9,F10", "R1", "S6: Background Overhead & Startup Hygiene Audit", t4_s06),
        lib.TestCase("T4-S07", 4, "F11,F12,F13,F14", "R2", "S7: Dual-Interface Network Readiness", t4_s07),
        lib.TestCase("T4-S08", 4, "F1,F2,F3,F4,F15", "R1,R2,R3", "S8: Workspace Hygiene & Complete Requirement Traceability", t4_s08),
    ]
