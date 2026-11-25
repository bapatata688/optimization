"""Tier 2: Boundary, Corner & Negative Error-Handling Tests (>= 75 tests, >= 5 per F1-F15).

Includes strict boundary assertions, TempProjectSandbox negative/schema-rejection tests,
and isolated non-destructive CLI negative tests against scripts when present.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
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


# =============================================================================
# F1: Pre-Change System & Network Backups (Boundaries & Negative)
# =============================================================================

def t2_f01_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        bad_hdr = sb.root / "backups/system/corrupt.reg"
        bad_hdr.write_text("NOT A REGISTRY HEADER\n[HKEY_CURRENT_USER\\Foo]\n", encoding="utf-8")
        zero_reg = sb.root / "backups/system/empty.reg"
        zero_reg.write_bytes(b"")
        ok1, _ = lib.is_valid_reg_export(bad_hdr)
        ok2, _ = lib.is_valid_reg_export(zero_reg)
        if ok1 or ok2:
            return False, f"Validator failed to reject corrupt/empty .reg (ok1={ok1}, ok2={ok2})"
    return True, "Corrupt header and zero-byte .reg cleanly rejected in sandbox"


def t2_f01_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        zero_pow = sb.root / "backups/power/empty.pow"
        zero_pow.write_bytes(b"")
        ok_rp, _, _ = lib.load_json_file(sb.root / "backups/system/restore_point_info.json")
        if zero_pow.stat().st_size != 0 or ok_rp:
            return False, "Sandbox negative check failed"
    return True, "Zero-byte .pow and missing restore_point_info.json detected cleanly"


def t2_f01_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    sys_dir = ctx.project_root / "backups/system"
    pwr_dir = ctx.project_root / "backups/power"
    net_dir = ctx.project_root / "backups/network"
    if not (sys_dir.is_dir() and pwr_dir.is_dir() and net_dir.is_dir()):
        return False, "Backup directories missing"
    pows = list(pwr_dir.glob("*.pow"))
    if len(pows) == 0:
        return False, "No .pow files found in backups/power"
    sys_regs = list(sys_dir.glob("*.reg"))
    net_regs = list(net_dir.glob("*.reg"))
    if len(sys_regs) == 0 or len(net_regs) == 0:
        return False, f"Missing .reg backups (sys={len(sys_regs)}, net={len(net_regs)})"
    tiny = [p.name for p in sys_regs + net_regs + pows if p.stat().st_size < 100]
    invalid_regs = []
    for p in sys_regs + net_regs:
        ok_reg, reg_msg = lib.is_valid_reg_export(p)
        if not ok_reg:
            invalid_regs.append(reg_msg)
    ok_svc, svc_data, msg = lib.load_json_file(sys_dir / "services_baseline.json")
    if not ok_svc:
        return False, msg
    if tiny or invalid_regs or (not isinstance(svc_data, list) or len(svc_data) < 20):
        svc_diag = f"svc_count={len(svc_data)}" if isinstance(svc_data, list) else f"svc_type={type(svc_data).__name__}"
        return False, f"Undersized backups={tiny}, invalid_regs={invalid_regs}, {svc_diag}"
    return True, (
        f"All {len(sys_regs)+len(net_regs)} .reg (valid headers) and {len(pows)} .pow backups >= 100 bytes "
        f"and services_baseline.json >= 20 entries"
    )


def t2_f01_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/backup_baseline.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"backup_baseline.ps1 invalid or missing: {ast_res.get('errors')}"
    return True, f"backup_baseline.ps1 AST clean (params={ast_res.get('params')})"


def t2_f01_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        u16 = sb.root / "backups/system/u16.reg"
        u16.write_bytes("Windows Registry Editor Version 5.00\r\n; Configuración español áéíóú\r\n".encode("utf-16"))
        u8 = sb.root / "backups/system/u8.reg"
        u8.write_bytes("Windows Registry Editor Version 5.00\r\n; UTF-8 BOM áéíóú\r\n".encode("utf-8-sig"))
        ok1, m1 = lib.is_valid_reg_export(u16)
        ok2, m2 = lib.is_valid_reg_export(u8)
        if not (ok1 and ok2):
            return False, f"Encoding check failed: u16=({ok1},{m1}), u8=({ok2},{m2})"
    return True, "UTF-16LE BOM and UTF-8 BOM .reg exports with non-ASCII characters validated"


# =============================================================================
# F2: System & GPU Benchmark Suite (Boundaries & Negative)
# =============================================================================

def t2_f02_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    bad_missing = lib.TempProjectSandbox.sample_system_json()
    del bad_missing["gpu_bench"]
    ok1, _ = lib.validate_system_json(bad_missing)
    bad_nan = lib.TempProjectSandbox.sample_system_json()
    bad_nan["gpu_bench"]["cuda_fp32_tflops"] = -1.0
    bad_nan["timestamp_utc"] = "not-a-timestamp"
    ok2, _ = lib.validate_system_json(bad_nan)
    if ok1 or ok2:
        return False, f"System validator failed to reject invalid payloads (ok1={ok1}, ok2={ok2})"
    return True, "System schema validator rejects missing gpu_bench, negative FLOPS, and bad timestamp"


def t2_f02_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    bad = lib.TempProjectSandbox.sample_system_json()
    bad["timer_jitter_ms"] = {"mean": 1.0, "p95": 3.0, "p99": 2.0, "max": 4.0}
    bad["disk_bench"]["seq_read_mbps"] = 0
    ok, errs = lib.validate_system_json(bad)
    if ok or len(errs) < 2:
        return False, f"Expected >=2 errors for non-monotonic jitter + zero disk, got ok={ok}, errs={errs}"
    return True, "System schema validator rejects non-monotonic timer_jitter_ms and zero disk_bench"


def t2_f02_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/benchmark_system.py"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        rc, out, err = lib.run_cli_process(
            [sys.executable, str(script), "--tag", "invalid_tag_xyz", "--out", str(sb.root / "reports/bad.json")]
        )
        if rc == 0:
            return False, f"benchmark_system.py accepted invalid --tag (rc=0, out={out[:120]})"
    return True, f"benchmark_system.py rejected invalid --tag cleanly (rc={rc})"


def t2_f02_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/benchmark_system.py"
    if not script.is_file():
        return False, f"Missing {script}"
    rc1, _, _ = lib.run_cli_process([sys.executable, str(script), "--unknown-flag-xyz"])
    rc2, _, _ = lib.run_cli_process([sys.executable, str(script), "--tag"])
    if rc1 == 0 or rc2 == 0:
        return False, f"benchmark_system.py returned 0 on invalid CLI args (rc1={rc1}, rc2={rc2})"
    return True, f"benchmark_system.py rejected unknown/missing CLI args (rc1={rc1}, rc2={rc2})"


def t2_f02_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    import psutil
    vm = psutil.virtual_memory()
    avail_mb = vm.available / (1024 * 1024)
    logical = psutil.cpu_count(logical=True)
    ok_b, base_sys, msg = lib.load_json_file(ctx.project_root / "reports/baseline_system.json")
    if not ok_b:
        return False, msg
    if not isinstance(base_sys, dict):
        return False, f"baseline_system.json expected object, got {type(base_sys).__name__}"
    gb = base_sys.get("gpu_bench")
    raw_temp = gb.get("max_temp_c") if isinstance(gb, dict) else None
    if not lib.is_finite_num(raw_temp):
        return False, f"baseline_system.json invalid gpu_bench.max_temp_c: {raw_temp!r}"
    temp = float(raw_temp)
    ok = avail_mb > 4096 and logical == 12 and 20.0 <= temp <= 95.0
    return ok, f"avail_mb={avail_mb:.0f}, logical_cpus={logical}, baseline_gpu_temp={temp}C"


# =============================================================================
# F3: Network & Ping Benchmark Suite (Boundaries & Negative)
# =============================================================================

def t2_f03_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    bad = lib.TempProjectSandbox.sample_network_json()
    bad["gateway_ping"]["p50_ms"] = 10.0
    bad["gateway_ping"]["p95_ms"] = 5.0
    bad["public_pings"][0]["loss_pct"] = 105.0
    ok, errs = lib.validate_network_json(bad)
    if ok or len(errs) < 2:
        return False, f"Network validator failed to catch bad quantiles/loss: ok={ok}, errs={errs}"
    return True, "Network schema validator rejects non-monotonic ping quantiles and loss_pct > 100"


def t2_f03_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    bad = lib.TempProjectSandbox.sample_network_json()
    bad["public_pings"] = [p for p in bad["public_pings"] if p["target"] != "1.1.1.1"]
    bad["dns_bench"] = {}
    bad["gateway_ping"]["packet1_ms"] = 0.1
    ok, errs = lib.validate_network_json(bad)
    if ok or len(errs) < 3:
        return False, f"Expected >=3 errors, got ok={ok}, errs={errs}"
    return True, "Network schema validator rejects missing 1.1.1.1, empty dns_bench, and packet1_ms < min_ms"


def t2_f03_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/benchmark_network.ps1"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        rc, out, err = lib.run_cli_process(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-Tag", "invalid_tag_xyz", "-Out", str(sb.root / "reports/n.json")]
        )
        if rc == 0:
            return False, "benchmark_network.ps1 accepted invalid -Tag"
    return True, f"benchmark_network.ps1 rejected invalid -Tag cleanly (rc={rc})"


def t2_f03_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/benchmark_network.ps1"
    if not script.is_file():
        return False, f"Missing {script}"
    rc, _, _ = lib.run_cli_process(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-UnknownSwitchXYZ"]
    )
    if rc == 0:
        return False, "benchmark_network.ps1 accepted unknown switch"
    return True, f"benchmark_network.ps1 rejected unknown parameter cleanly (rc={rc})"


def t2_f03_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    def _eval(gw: dict) -> tuple[bool, str]:
        if not isinstance(gw, dict) or not gw.get("ok"):
            return False, f"Live gateway ping failed: {gw}"
        rec = gw.get("received")
        if not isinstance(rec, int) or isinstance(rec, bool) or rec <= 0:
            return False, f"Live gateway ping invalid received={rec!r}: {gw}"
        fields = ("loss_pct", "packet1_ms", "min_ms", "p50_ms", "p95_ms", "max_ms")
        for fk in fields:
            val = gw.get(fk)
            if not lib.is_finite_num(val) or float(val) < 0.0:
                return False, f"Invalid ping field {fk}={val!r}: {gw}"
        loss = float(gw["loss_pct"])
        p1 = float(gw["packet1_ms"])
        mn = float(gw["min_ms"])
        p50 = float(gw["p50_ms"])
        p95 = float(gw["p95_ms"])
        mx = float(gw["max_ms"])
        mono = mn <= p50 <= p95 <= mx
        wake_ok = p1 < 15.0 and p50 < 10.0 and p95 < 15.0
        ok = loss == 0.0 and mono and wake_ok
        return ok, f"gw loss={loss}%, p1={p1}ms, min={mn}ms, p50={p50}ms, p95={p95}ms, max={mx}ms"

    pings = ctx.live_pings()
    gw = pings.get("192.168.1.1", {})
    ok, detail = _eval(gw)
    gw_loss = gw.get("loss_pct") if isinstance(gw, dict) else None
    if not ok and isinstance(gw, dict) and gw.get("ok") and lib.is_finite_num(gw_loss) and float(gw_loss) == 0.0:
        time.sleep(0.2)
        gw_retry = lib.run_live_ping_burst("192.168.1.1", count=30, interval_ms=100)
        ok2, detail2 = _eval(gw_retry)
        return ok2, f"{detail2} (1-retry scan guard; initial: {detail})"
    return ok, detail


# =============================================================================
# F4: Rollback & Toggle Scripts (Boundaries & Negative)
# =============================================================================

def t2_f04_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/rollback_all.ps1"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        empty_bkp = sb.root / "empty_backups"
        empty_bkp.mkdir()
        sb_rep = sb.root / "reports_empty"
        rc, out, err = lib.run_cli_process(
            [
                "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script),
                "-BackupDir", str(empty_bkp), "-ReportDir", str(sb_rep), "-DryRun",
            ]
        )
        combined = f"{out}\n{err}".lower()
        log_path = sb_rep / "rollback.log"
        log_txt = log_path.read_text(encoding="utf-8", errors="replace").lower() if log_path.is_file() else ""
        full_diag = f"{combined}\n{log_txt}"
        if rc == 0:
            return False, "rollback_all.ps1 -DryRun returned rc=0 on empty -BackupDir"
        if "administrator privileges required" in full_diag:
            return False, f"rollback_all.ps1 failed via elevation gate instead of empty-backup check: {out[:200]}"
        if "no non-empty backup" not in full_diag:
            return False, f"rollback_all.ps1 did not report empty-backup error (rc={rc}): {out[:200]}"

        # Positive non-vacuity check: populated sandbox -BackupDir with -DryRun must exit 0
        pop_bkp = sb.root / "populated_backups"
        (pop_bkp / "system").mkdir(parents=True)
        (pop_bkp / "power").mkdir(parents=True)
        (pop_bkp / "network").mkdir(parents=True)
        (pop_bkp / "system" / "hkcu_gameconfigstore.reg").write_bytes(
            "Windows Registry Editor Version 5.00\r\n\r\n[HKEY_CURRENT_USER\\System\\GameConfigStore]\r\n\"GameDVR_Enabled\"=dword:00000001\r\n".encode("utf-16")
        )
        (pop_bkp / "system" / "services_baseline.json").write_text("[]", encoding="utf-8")
        (pop_bkp / "system" / "tasks_baseline.json").write_text("[]", encoding="utf-8")
        (pop_bkp / "network" / "dns_before.json").write_text('{"interfaces": []}', encoding="utf-8")
        sb_rep_ok = sb.root / "reports_populated"
        rc_ok, out_ok, err_ok = lib.run_cli_process(
            [
                "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script),
                "-BackupDir", str(pop_bkp), "-ReportDir", str(sb_rep_ok), "-DryRun",
            ]
        )
        if rc_ok != 0:
            return False, f"rollback_all.ps1 -DryRun failed on populated sandbox -BackupDir (rc={rc_ok}): {out_ok[:200]} {err_ok[:200]}"
    return True, f"rollback_all.ps1 -DryRun rejected empty -BackupDir (rc={rc}) and succeeded on populated -BackupDir (rc={rc_ok})"


def t2_f04_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/rollback_all.ps1"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        rc, _, _ = lib.run_cli_process(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-BackupDir", r"C:\nonexistent_backup_dir_xyz_999", "-ReportDir", str(sb.root / "reports")]
        )
        if rc == 0:
            return False, "rollback_all.ps1 returned rc=0 on non-existent -BackupDir"
    return True, f"rollback_all.ps1 rejected non-existent -BackupDir (rc={rc})"


def t2_f04_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/gaming_wlan_scan_toggle.ps1"
    if not script.is_file():
        return False, f"Missing {script}"
    rc, _, _ = lib.run_cli_process(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-Mode", "invalid_mode_xyz"]
    )
    if rc == 0:
        return False, "gaming_wlan_scan_toggle.ps1 accepted invalid -Mode"
    return True, f"gaming_wlan_scan_toggle.ps1 rejected invalid -Mode (rc={rc})"


def t2_f04_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/toggle_vbs_hypervisor.ps1"
    if not script.is_file():
        return False, f"Missing {script}"
    rc, _, _ = lib.run_cli_process(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), "-Mode", "invalid_mode_xyz"]
    )
    if rc == 0:
        return False, "toggle_vbs_hypervisor.ps1 accepted invalid -Mode"
    return True, f"toggle_vbs_hypervisor.ps1 rejected invalid -Mode (rc={rc})"


def t2_f04_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    missing_or_unsafe = []
    for name in ("rollback_all.ps1", "gaming_wlan_scan_toggle.ps1", "toggle_vbs_hypervisor.ps1"):
        p = ctx.project_root / "scripts" / name
        ast_res = lib.check_ps_ast(p)
        if not ast_res.get("exists") or not ast_res.get("ok"):
            missing_or_unsafe.append(f"{name}:missing/ast_err")
        elif not (ast_res.get("supports_should_process") or ast_res.get("has_dry_run")):
            missing_or_unsafe.append(f"{name}:no_WhatIf_or_DryRun")
    return len(missing_or_unsafe) == 0, (
        "All F4 toggle/rollback scripts declare WhatIf/DryRun" if not missing_or_unsafe else f"Issues: {missing_or_unsafe}"
    )


# =============================================================================
# F5: User-Space OS & Gaming Hooks (Boundaries)
# =============================================================================

def t2_f05_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, v1, t1 = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
    ex2, v2, t2 = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled")
    ok = ex1 and t1 == winreg.REG_DWORD and v1 == 0 and ex2 and t2 == winreg.REG_DWORD and v2 == 0
    return ok, f"Types/vals: GameDVR_Enabled=({v1!r},type={t1}), AppCaptureEnabled=({v2!r},type={t2})"


def t2_f05_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, tr, t1 = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency")
    ex2, vfx, t2 = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting")
    ok = ex1 and t1 == winreg.REG_DWORD and tr == 0 and ex2 and t2 == winreg.REG_DWORD and vfx == 2
    return ok, f"EnableTransparency={tr!r}(t={t1}), VisualFXSetting={vfx!r}(t={t2}, exp 2)"


def t2_f05_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, ma, rt = lib.read_reg_value(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics", "MinAnimate")
    ok = ex and rt == winreg.REG_SZ and ma == "0"
    return ok, f"MinAnimate={ma!r} (reg_type={rt}, expected REG_SZ '0')"


def t2_f05_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    vals = lib.enum_reg_values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences")
    fifa = [str(v[0]) for k, v in vals.items() if "fifa22.exe" in k.lower()]
    if not fifa:
        return False, "FIFA22.exe missing from UserGpuPreferences"
    v = fifa[0]
    ok = ("gpupreference=2;" in v.lower()) and ("gpupreference=1;" not in v.lower()) and ("gpupreference=0;" not in v.lower())
    return ok, f"FIFA22 UserGpuPreferences={v!r} (expected GpuPreference=2;)"


def t2_f05_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/apply_os_gpu_user.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"apply_os_gpu_user.ps1 missing/invalid: {ast_res.get('errors')}"
    return True, "apply_os_gpu_user.ps1 AST valid"


# =============================================================================
# F6: Startup Bloat & Overlay Reduction (Boundaries)
# =============================================================================

def t2_f06_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    # Boundary unit check on StartupApproved bitmask decoder
    cases = [
        (bytes([0x02] + [0] * 11), False),
        (bytes([0x00] + [0] * 11), False),
        (bytes([0x03] + [0] * 11), True),
        (bytes([0x01] + [0] * 11), True),
        (b"", False),
    ]
    for raw, exp in cases:
        if lib.is_startup_approved_disabled(raw) != exp:
            return False, f"Decoder mismatch on {raw.hex()}"
    return True, "StartupApproved byte[0] bitmask boundary checks (0x00/0x02 enabled vs 0x01/0x03 disabled) pass"


def t2_f06_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    app_vals = lib.enum_reg_values(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    )
    run_vals = lib.enum_reg_values(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
    targets = set(lib.TARGET_STARTUP_BLOAT + lib.TARGET_RUN_ONLY_BLOAT)
    for k in list(run_vals.keys()) + list(app_vals.keys()):
        if any(k.startswith(pfx) for pfx in lib.TARGET_STARTUP_PREFIXES):
            targets.add(k)
    bad = []
    for name in sorted(targets):
        in_run = name in run_vals
        in_app = name in app_vals
        if not in_run and not in_app:
            continue
        if in_run and not in_app:
            bad.append(f"{name}:missing_StartupApproved")
        elif in_app:
            raw, rt = app_vals[name]
            if rt != winreg.REG_BINARY or not isinstance(raw, bytes) or len(raw) < 12 or not lib.is_startup_approved_disabled(raw):
                b0 = raw[0] if isinstance(raw, bytes) and raw else None
                bad.append(f"{name}:len={len(raw) if isinstance(raw, bytes) else -1},b0={b0}")
    return len(bad) == 0, "All StartupApproved\\Run entries 12B REG_BINARY disabled" if not bad else f"Bad: {bad}"


def t2_f06_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    app_vals = lib.enum_reg_values(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    )
    bad = []
    for name in ("utweb", "Docker Desktop"):
        if name in app_vals and not lib.is_startup_approved_disabled(app_vals[name][0]):
            bad.append(name)
    return len(bad) == 0, "utweb & Docker Desktop remain disabled (no regression)" if not bad else f"Re-enabled: {bad}"


def t2_f06_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    procs = [p.get("ProcessName") for p in procs_raw if isinstance(p, dict) and p.get("ProcessName") in lib.TARGET_OVERLAY_PROCESSES]
    return len(procs) == 0, f"Zero target overlay/bloat process instances (found={len(procs)}: {procs})"


def t2_f06_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/apply_network_user.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"apply_network_user.ps1 missing/invalid: {ast_res.get('errors')}"
    return True, "apply_network_user.ps1 AST valid"


# =============================================================================
# F7: Elevated Power Plan & Hibernation (Boundaries)
# =============================================================================

def t2_f07_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    proc_min = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PROC_MIN)
    proc_max = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PROC_MAX)
    boost = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PERF_BOOST_MODE)
    cool = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_SYS_COOL_POL)
    sch = ctx.power_scheme()
    if not all(x.get("ok") for x in (proc_min, proc_max, boost, cool, sch)):
        return False, "powercfg query failed"
    guid = (sch.get("guid") or "").strip().lower()
    not_forbidden = guid != lib.BALANCED_SCHEME_GUID and guid not in lib.ULTIMATE_PERF_GUIDS
    is_target = guid == lib.GAMING_STABLE_SCHEME_GUID
    pmax_ac = proc_max.get("ac")
    pmin_ac = proc_min.get("ac")
    boost_ac = boost.get("ac")
    cool_ac = cool.get("ac")
    ok = (
        not_forbidden
        and is_target
        and isinstance(pmax_ac, int) and not isinstance(pmax_ac, bool) and 50 <= pmax_ac <= 99
        and isinstance(pmin_ac, int) and not isinstance(pmin_ac, bool) and 1 <= pmin_ac <= 10
        and isinstance(boost_ac, int) and not isinstance(boost_ac, bool) and boost_ac == 0
        and isinstance(cool_ac, int) and not isinstance(cool_ac, bool) and cool_ac == 1
    )
    return ok, (
        f"scheme={guid} ({sch.get('name')}), PROCTHROTTLEMAX_AC={pmax_ac}% (50..99), "
        f"PROCTHROTTLEMIN_AC={pmin_ac}%, PERFBOOSTMODE_AC={boost_ac} (exp 0), SYSCOOLPOL_AC={cool_ac} (exp 1)"
    )


def t2_f07_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    usb = ctx.powercfg_setting(lib.SUB_USB, lib.SET_USB_SUSPEND)
    if not usb.get("ok"):
        return False, f"USB GUID {lib.SET_USB_SUSPEND} query failed"
    ac = usb.get("ac")
    ok = lib.is_exact_int(ac) and ac == 0
    return ok, f"USB suspend AC={ac} DC={usb.get('dc')} (AC-only NoSystemBattery: AC must be 0)"


def t2_f07_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, w32, rt = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\PriorityControl", "Win32PrioritySeparation"
    )
    ok = ex and rt == winreg.REG_DWORD and w32 == 0x26
    return ok, f"Win32PrioritySeparation={hex(w32) if isinstance(w32, int) else w32!r} (exp 0x26, not 0x2)"


def t2_f07_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, pt, rt1 = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff"
    )
    ex2, hb, rt2 = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled"
    )
    ok = ex1 and rt1 == winreg.REG_DWORD and pt == 1 and ex2 and rt2 == winreg.REG_DWORD and hb == 0
    return ok, f"PowerThrottlingOff={pt!r}, HiberbootEnabled={hb!r}"


def t2_f07_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    hib = Path(r"C:\hiberfil.sys")
    avail = ctx.power_available()
    ok = (not hib.exists()) and avail.get("ok", False) and (not avail.get("hibernate_available", True))
    return ok, f"hiberfil.sys exists={hib.exists()}, s3_available={avail.get('s3_available')}, hib_available={avail.get('hibernate_available')}"


# =============================================================================
# F8: Stability-Safe GPU & Driver Tuning (Boundaries)
# =============================================================================

def t2_f08_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, drs.get("error", "DRS failed") if isinstance(drs, dict) else "DRS failed"
    sc = settings.get(lib.NVAPI_DRS_SHADER_CACHE_MB)
    val = sc.get("current_u32") if isinstance(sc, dict) else None
    ok = lib.is_exact_int(val) and (val == 0xFFFFFFFF or 0x2800 <= val < 0xFFFFFFFF)
    return ok, f"ShaderCache={hex(val) if lib.is_exact_int(val) else val} (boundary >= 0x2800 or 0xffffffff)"


def t2_f08_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, drs.get("error", "DRS failed") if isinstance(drs, dict) else "DRS failed"
    tex = settings.get(lib.NVAPI_DRS_TEX_FILTER_QUAL)
    val = tex.get("current_u32") if isinstance(tex, dict) else None
    return (lib.is_exact_int(val) and val == 0x14), f"TexFilterQual={val!r} (strictly 20/0x14 High Performance, not 0xa/0x0/0xfffffff6)"


def t2_f08_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    drs = ctx.nvapi_drs()
    settings = drs.get("settings") if isinstance(drs, dict) else None
    if not isinstance(drs, dict) or not drs.get("ok") or not isinstance(settings, dict):
        return False, drs.get("error", "DRS failed") if isinstance(drs, dict) else "DRS failed"
    p_ent = settings.get(lib.NVAPI_DRS_POWER_MGMT)
    ll_ent = settings.get(lib.NVAPI_DRS_LOW_LATENCY)
    to_ent = settings.get(lib.NVAPI_DRS_THREADED_OPT)
    p = p_ent.get("current_u32") if isinstance(p_ent, dict) else None
    ll = ll_ent.get("current_u32") if isinstance(ll_ent, dict) else None
    to = to_ent.get("current_u32") if isinstance(to_ent, dict) else None
    ok = (
        lib.is_exact_int(p) and p == 1
        and lib.is_exact_int(ll) and ll == 1
        and lib.is_exact_int(to) and to == 1
    )
    return ok, f"PowerMgmt={p}, LowLatency={ll}, ThreadedOpt={to} (all strictly 1)"


def t2_f08_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    smi = ctx.nvidia_smi()
    _, tdr, _ = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "TdrDelay"
    )
    if not smi.get("ok"):
        return False, f"nvidia-smi failed: {smi.get('error')}"
    temp = smi.get("temp_c")
    throt = str(smi.get("throttle_reasons", ""))
    tdr_ok = (tdr is None) or (isinstance(tdr, int) and not isinstance(tdr, bool) and tdr <= 8)
    # 0x0000000000000001 = GPU Idle (normal), 0x0000000000000000 = None; Thermal slowdown bits are 0x20 / 0x40
    bad_throt = any(bit in throt.lower() for bit in ("0x0000000000000020", "0x0000000000000040", "0x0000000000000060"))
    ok = lib.is_finite_num(temp) and float(temp) < 85.0 and tdr_ok and (not bad_throt)
    return ok, f"temp={temp}C (<85), throttle={throt}, TdrDelay={tdr!r}"


def t2_f08_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    amd_sub = lib.find_display_class_subkey("AMD")
    if not amd_sub:
        return False, "Could not dynamically locate AMD Display Class subkey"
    ex, ulps, rt = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, amd_sub, "EnableUlps")
    ok = ex and rt == winreg.REG_DWORD and ulps == 0
    return ok, f"Dynamic AMD subkey {amd_sub} EnableUlps={ulps!r} (type={rt}, exp 0)"


# =============================================================================
# F9: Services & Scheduled Tasks Cleanup (Boundaries & Guardrails)
# =============================================================================

def t2_f09_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    svcs = snap.get("services", {})
    if not isinstance(svcs, dict):
        return False, f"services snapshot expected dict, got {type(svcs).__name__}"
    bad = []
    exists_count = 0
    for s in ("PCManager Service Store", "MapsBroker", "NetworkDaemon"):
        st = svcs.get(s, {})
        if isinstance(st, dict) and st.get("Exists"):
            exists_count += 1
            if not (st.get("StartType") == "Disabled" and st.get("Status") == "Stopped"):
                bad.append(f"{s}={st.get('StartType')}/{st.get('Status')}")
    ok = exists_count >= 2 and len(bad) == 0
    return ok, (
        f"Disabled+Stopped strictly enforced (verified={exists_count}/3)"
        if ok
        else f"exists={exists_count}/3, Bad: {bad}"
    )


def t2_f09_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    svcs = snap.get("services")
    if not isinstance(svcs, dict):
        return False, f"services snapshot expected dict, got {type(svcs).__name__}"
    fax = svcs.get("Fax")
    if not isinstance(fax, dict):
        return False, f"Fax snapshot expected dict, got {type(fax).__name__}"
    ok = (fax.get("Status") == "NOT_FOUND") or (fax.get("StartType") == "Disabled")
    return ok, f"Fax edge-case handling: {fax}"


def t2_f09_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    # Safety guardrail: essential networking/DNS/WLAN services must NOT be disabled
    rc, out, err = lib.run_cli_process([
        "pwsh", "-NoProfile", "-NonInteractive", "-Command",
        "@('Dhcp','Dnscache','WlanSvc','nsi','WinDefend') | ForEach-Object { $s=Get-Service $_ -EA SilentlyContinue; if($s){ \"$($s.Name):$($s.StartType):$($s.Status)\" } } | ConvertTo-Json -Compress"
    ])
    if rc != 0 or not out.strip():
        return False, f"Essential service probe failed: {err}"
    try:
        items = json.loads(out)
    except json.JSONDecodeError as exc:
        return False, f"Essential service probe JSON error: {exc}"
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list) or len(items) < 4:
        return False, f"Essential service probe incomplete (count={len(items) if isinstance(items, list) else type(items).__name__}): {items}"
    bad = [x for x in items if ":Disabled:" in str(x)]
    return len(bad) == 0, f"Essential services intact: {items}" if not bad else f"Essential service disabled: {bad}"


def t2_f09_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    svcs = snap.get("services", {})
    if not isinstance(svcs, dict):
        return False, f"services snapshot expected dict, got {type(svcs).__name__}"
    exists_count = 0
    bad = []
    for s in lib.TARGET_SERVICES_MANUAL_OR_DISABLED:
        st = svcs.get(s, {})
        if isinstance(st, dict) and st.get("Exists"):
            exists_count += 1
            if st.get("StartType") not in ("Manual", "Disabled"):
                bad.append(f"{s}={st.get('StartType')}")
    ok = exists_count >= 3 and len(bad) == 0
    return ok, (
        f"No WSearch/TrkWks/Spooler/lfsvc left Automatic (verified={exists_count}/{len(lib.TARGET_SERVICES_MANUAL_OR_DISABLED)})"
        if ok
        else f"exists={exists_count}/{len(lib.TARGET_SERVICES_MANUAL_OR_DISABLED)}, Automatic/Bad: {bad}"
    )


def t2_f09_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    tasks_raw = snap.get("tasks")
    if not isinstance(tasks_raw, list):
        return False, f"tasks snapshot missing/invalid: {type(tasks_raw).__name__}"
    matched = [
        t
        for t in tasks_raw
        if isinstance(t, dict) and lib.is_target_disabled_task(str(t.get("TaskPath") or ""), str(t.get("TaskName") or ""))
    ]
    running_or_ready = [
        f"{t.get('TaskPath')}{t.get('TaskName')}:{t.get('State')}"
        for t in matched
        if str(t.get("State")) in ("Ready", "Running")
    ]
    ok = len(matched) >= 5 and len(running_or_ready) == 0
    return ok, (
        f"No target tasks Ready/Running (matched={len(matched)})"
        if ok
        else f"matched={len(matched)}, active={running_or_ready}"
    )


# =============================================================================
# F10: VBS / HVCI Gaming Optimization (Boundaries)
# =============================================================================

def t2_f10_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, rt = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    ok = ex and rt == winreg.REG_DWORD and val == 0
    return ok, f"EnableVirtualizationBasedSecurity={val!r} (type={rt}, exp REG_DWORD 0)"


def t2_f10_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex, val, rt = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled",
    )
    ok = (not ex) or (rt == winreg.REG_DWORD and val == 0)
    return ok, f"HVCI Enabled={val!r} (exists={ex}, type={rt})"


def t2_f10_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    dg = snap.get("device_guard") if isinstance(snap, dict) and snap.get("ok") else None
    if not isinstance(dg, dict):
        return False, "Win32_DeviceGuard unavailable"
    st = dg.get("VirtualizationBasedSecurityStatus")
    running = dg.get("SecurityServicesRunning")
    if not isinstance(running, list):
        return False, f"SecurityServicesRunning invalid: {running!r}"
    hyp = snap.get("hypervisor_present")
    ex_vbs, reg_vbs, rt_vbs = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\DeviceGuard", "EnableVirtualizationBasedSecurity"
    )
    ex_hvci, reg_hvci, rt_hvci = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
        "Enabled",
    )
    reg_vbs_off = ex_vbs and rt_vbs == winreg.REG_DWORD and lib.is_exact_int(reg_vbs) and reg_vbs == 0
    hvci_off = (not ex_hvci) or (rt_hvci == winreg.REG_DWORD and lib.is_exact_int(reg_hvci) and reg_hvci == 0)
    hvci_not_running = 2 not in running
    status_ok = lib.is_exact_int(st) and (
        st == 0 or (st == 2 and hyp is True and reg_vbs_off and hvci_off and hvci_not_running)
    )
    ok = reg_vbs_off and hvci_off and hvci_not_running and status_ok
    return ok, (
        f"VBS runtime status={st}, hypervisor_present={hyp}, reg_VBS={reg_vbs!r}(type={rt_vbs}), "
        f"HVCI_reg={reg_hvci!r}(ex={ex_hvci},type={rt_hvci}), running={running}"
    )


def t2_f10_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    dg = snap.get("device_guard") if isinstance(snap, dict) and snap.get("ok") else None
    if not isinstance(dg, dict):
        return False, "Win32_DeviceGuard unavailable"
    running = dg.get("SecurityServicesRunning")
    if not isinstance(running, list):
        return False, f"SecurityServicesRunning invalid: {running!r}"
    bad = [x for x in running if lib.is_exact_int(x) and x in (1, 2)]
    return len(bad) == 0, f"SecurityServicesRunning={running} (must exclude 1 & 2)"


def t2_f10_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/apply_os_gpu_admin.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"apply_os_gpu_admin.ps1 missing/invalid: {ast_res.get('errors')}"
    has_dry = bool(ast_res.get("supports_should_process") or ast_res.get("has_dry_run"))
    has_bkp = "BackupDir" in (ast_res.get("params") or [])
    return (has_dry and has_bkp), f"apply_os_gpu_admin.ps1 params={ast_res.get('params')}, dry={has_dry}"


# =============================================================================
# F11: Wi-Fi Power-Saving & Wake-Spike Fix (Boundaries)
# =============================================================================

def t2_f11_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    v = _get_adv_val(snap, "Wi-Fi", "LpsEn")
    return v == "0", f"Wi-Fi LpsEn={v!r} (must be '0', not '1' or '2')"


def t2_f11_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    guid = _get_adapter_guid(snap, "Wi-Fi")
    sub = lib.find_nic_class_subkey(guid or "")
    if not sub:
        return False, f"Wi-Fi Class key missing for {guid}"
    ex, pnp, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "PnPCapabilities")
    ok = ex and isinstance(pnp, int) and not isinstance(pnp, bool) and (pnp & 0x18) == 0x18
    return ok, f"Wi-Fi PnPCapabilities={pnp!r} (must include 0x18 / 24, not 16)"


def t2_f11_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    matches = [m for m in (snap.get("ms_power_enable") or []) if isinstance(m, dict) and "8852" in str(m.get("InstanceName") or "")]
    ok = bool(matches) and all(m.get("Enable") is False for m in matches)
    return ok, f"DEV_8852 MSPower_DeviceEnable={matches}"


def t2_f11_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    wl = ctx.powercfg_setting(lib.SUB_WIRELESS, lib.SET_WIRELESS_PS)
    if not wl.get("ok"):
        return False, "powercfg wireless failed"
    ac, dc = wl.get("ac"), wl.get("dc")
    ok = lib.is_exact_int(ac) and ac == 0 and lib.is_exact_int(dc) and dc == 0
    return ok, f"Wireless PS AC={ac}, DC={dc} (DC must be 0, not 2)"


def t2_f11_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    aspm = ctx.powercfg_setting(lib.SUB_PCIE, lib.SET_PCIE_ASPM)
    if not aspm.get("ok"):
        return False, "powercfg ASPM failed"
    ac, dc = aspm.get("ac"), aspm.get("dc")
    ok = lib.is_exact_int(ac) and ac == 0 and lib.is_exact_int(dc) and dc == 0
    return ok, f"PCIe ASPM AC={ac}, DC={dc} (DC must be 0, not 2)"


# =============================================================================
# F12: Wi-Fi Roaming Lock & 2.4 GHz Guard (Boundaries)
# =============================================================================

def t2_f12_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    v = _get_adv_val(snap, "Wi-Fi", "RegROAMSensitiveLevel")
    return v == "127", f"RegROAMSensitiveLevel={v!r} (strictly '127', not '80'/'70'/'65')"


def t2_f12_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    txt = str(snap.get("wlan_interfaces") or "")
    is_5g = "5 ghz" in txt.lower() and "2.4 ghz" not in txt.lower()
    m_sig = re.search(r"Signal\s*:\s*(\d+)%", txt, re.IGNORECASE)
    m_rssi = re.search(r"Rssi\s*:\s*(-\d+)", txt, re.IGNORECASE)
    sig = int(m_sig.group(1)) if m_sig else 0
    rssi = int(m_rssi.group(1)) if m_rssi else -100
    ok = is_5g and sig >= 60 and rssi > -80
    return ok, f"5GHz={is_5g}, Signal={sig}%, RSSI={rssi} dBm (bounds: >=60%, >-80 dBm)"


def t2_f12_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    pb = _get_adv_val(snap, "Wi-Fi", "PreferBand")
    return pb == "2", f"Wi-Fi PreferBand={pb!r} (expected '2' = 5G first, not '1' = 2.4G first)"


def t2_f12_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    c1 = str(snap.get("wlan_profile_claro1") or "").lower()
    c2 = str(snap.get("wlan_profile_claro2") or "").lower()
    c1_manual = ("not found" in c1) or ("no se encuentra" in c1) or ("connect manually" in c1) or ("conectar manualmente" in c1)
    c2_auto = ("connect automatically" in c2) or ("conectar autom" in c2)
    return (c1_manual and c2_auto), f"CLARO1_manual={c1_manual}, CLARO2_auto={c2_auto}"


def t2_f12_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    p = ctx.project_root / "scripts/apply_network_admin.ps1"
    ast_res = lib.check_ps_ast(p)
    if not ast_res.get("exists") or not ast_res.get("ok"):
        return False, f"apply_network_admin.ps1 missing/invalid: {ast_res.get('errors')}"
    has_dry = bool(ast_res.get("supports_should_process") or ast_res.get("has_dry_run"))
    has_bkp = "BackupDir" in (ast_res.get("params") or [])
    return (has_dry and has_bkp), f"apply_network_admin.ps1 params={ast_res.get('params')}, dry={has_dry}"


# =============================================================================
# F13: DNS & Background Bandwidth Optimization (Boundaries)
# =============================================================================

def t2_f13_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    srvs = [str(s) for s in (snap.get("dns_wifi") or [])]
    ok = lib.is_valid_static_gaming_dns(srvs) and not srvs[0].startswith("192.168.")
    return ok, f"Wi-Fi DNS={srvs} (must be static pair 1.1.1.1/1.0.0.1 or 9.9.9.9/8.8.8.8, not DHCP gateway)"


def t2_f13_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    ex1, v1, rt1 = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode"
    )
    ex2, v2, rt2 = lib.read_reg_value(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\DeliveryOptimization\Config", "DODownloadMode"
    )
    ok = (ex1 and rt1 == winreg.REG_DWORD and lib.is_exact_int(v1) and v1 == 0) or (ex2 and rt2 == winreg.REG_DWORD and lib.is_exact_int(v2) and v2 == 0)
    return ok, f"DODownloadMode policy={v1!r}(t={rt1}), config={v2!r}(t={rt2}); strictly 0 (not 1/2/3)"


def t2_f13_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    dns_wifi = snap.get("dns_wifi")
    if not isinstance(dns_wifi, list):
        return False, f"dns_wifi snapshot missing/invalid: {type(dns_wifi).__name__}"
    dns = ctx.live_dns()
    srvs = [str(s) for s in dns_wifi]
    primary = srvs[0] if srvs and isinstance(dns, dict) and srvs[0] in dns else "1.1.1.1"
    p_res = dns.get(primary) if isinstance(dns, dict) else None
    q9 = dns.get("9.9.9.9") if isinstance(dns, dict) else None
    if not isinstance(p_res, dict) or not isinstance(q9, dict) or not p_res.get("ok") or not q9.get("ok"):
        return False, f"Live DNS failed: {primary}={p_res}, 9.9.9.9={q9}"
    raw_vals = (p_res.get("avg_ms"), p_res.get("p95_ms"), q9.get("avg_ms"), q9.get("p95_ms"))
    if not all(lib.is_finite_num(v) and float(v) > 0.0 for v in raw_vals):
        return False, f"Invalid DNS latency values: {primary}={p_res}, 9.9.9.9={q9}"
    p_avg, p_p95, q_avg, q_p95 = [float(v) for v in raw_vals]
    ok = 0 < p_avg < 120.0 and p_p95 < 200.0 and 0 < q_avg < 120.0 and q_p95 < 200.0
    return ok, f"{primary} DNS avg={p_avg}ms/p95={p_p95}ms; 9.9.9.9 avg={q_avg}ms/p95={q_p95}ms (<120/<200)"


def t2_f13_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    https = ctx.live_https()
    if not isinstance(https, dict):
        return False, f"HTTPS probe invalid: {https!r}"
    sc = https.get("status_code")
    lat = https.get("latency_ms")
    ok = (
        bool(https.get("ok"))
        and lib.is_exact_int(sc) and sc == 204
        and lib.is_finite_num(lat) and 0.0 <= float(lat) < 3000.0
    )
    return ok, f"HTTPS probe status={sc}, latency={lat}ms, err={https.get('error')}"


def t2_f13_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    procs_raw = snap.get("processes")
    if not isinstance(procs_raw, list):
        return False, f"processes snapshot missing/invalid: {type(procs_raw).__name__}"
    hogs = [p.get("ProcessName") for p in procs_raw if isinstance(p, dict) and p.get("ProcessName") in lib.TARGET_P2P_PROCESSES]
    return len(hogs) == 0, f"Active P2P process count={len(hogs)} ({hogs})"


# =============================================================================
# F14: Ethernet Standby & TCP/Offload Tuning (Boundaries)
# =============================================================================

def t2_f14_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    kws = ["EnableGreenEthernet", "PowerSavingMode", "GigaLite", "*InterruptModeration", "*FlowControl"]
    bad = []
    for kw in kws:
        v = _get_adv_val(snap, "Ethernet", kw)
        if v != "0":
            bad.append(f"{kw}={v!r}")
    return len(bad) == 0, "All 5 Ethernet properties strictly '0'" if not bad else f"Non-zero: {bad}"


def t2_f14_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    bad = []
    for alias, desc_needle in (("Wi-Fi", "8852"), ("Ethernet", "GbE")):
        guid = _get_adapter_guid(snap, alias)
        sub = lib.find_nic_class_subkey(guid or "")
        if not sub:
            bad.append(f"{alias}:no_class_subkey")
            continue
        _, desc, _ = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "DriverDesc")
        if not desc or desc_needle.lower() not in str(desc).lower():
            bad.append(f"{alias}:DriverDesc={desc!r}")
    return len(bad) == 0, "Dynamic GUID->Class resolver matched Wi-Fi & Ethernet" if not bad else f"Bad: {bad}"


def t2_f14_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    bad = []
    for kw in ("*IPChecksumOffloadIPv4", "*TCPChecksumOffloadIPv4", "*UDPChecksumOffloadIPv4"):
        v = _get_adv_val(snap, "Ethernet", kw)
        if v != "3":
            bad.append(f"{kw}={v!r}")
    return len(bad) == 0, "Ethernet IPv4/TCP/UDP checksum offloads remain Rx&Tx ('3')" if not bad else f"Changed: {bad}"


def t2_f14_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    bad = []
    eth_present = _get_adapter_guid(snap, "Ethernet") is not None
    targets = ("Wi-Fi", "Ethernet") if eth_present else ("Wi-Fi",)
    for alias in targets:
        guid = _get_adapter_guid(snap, alias)
        if not guid:
            bad.append(alias)
            continue
        sub = rf"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{guid}"
        ex1, ack, rt1 = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "TcpAckFrequency")
        ex2, nd, rt2 = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, sub, "TCPNoDelay")
        if not (ex1 and rt1 == winreg.REG_DWORD and ack == 1 and ex2 and rt2 == winreg.REG_DWORD and nd == 1):
            bad.append(f"{alias}:ack={ack}(t={rt1}),nd={nd}(t={rt2})")
    return len(bad) == 0, f"Strict REG_DWORD==1 for TcpAckFrequency & TCPNoDelay on {list(targets)}" if not bad else f"Bad: {bad}"


def t2_f14_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    tcp = str(snap.get("tcp_global") or "")
    at_normal = bool(re.search(r"(?im)^\s*receive window auto-tuning level\s*:\s*normal\b", tcp))
    rss_on = bool(re.search(r"(?im)^\s*receive-side scaling state\s*:\s*enabled\b", tcp))
    rsc_off = bool(re.search(r"(?im)^\s*receive segment coalescing state\s*:\s*disabled\b", tcp))
    ecn_off = bool(re.search(r"(?im)^\s*ecn capability\s*:\s*disabled\b", tcp))
    ts_off = bool(re.search(r"(?im)^\s*rfc 1323 timestamps\s*:\s*disabled\b", tcp))
    ns_off = bool(re.search(r"(?im)^\s*non sack rtt resiliency\s*:\s*disabled\b", tcp))
    wifi_rsc = snap.get("wifi_rsc") or []
    nic_rsc_off = isinstance(wifi_rsc, list) and bool(wifi_rsc) and all(
        isinstance(item, dict) and item.get("IPv4Enabled") is False and item.get("IPv6Enabled") is False for item in wifi_rsc
    )
    ok = at_normal and rss_on and rsc_off and ecn_off and ts_off and ns_off and nic_rsc_off
    return ok, (
        f"AutoTuning={at_normal}, RSS={rss_on}, RSC_off={rsc_off}, ECN_off={ecn_off}, "
        f"Timestamps_off={ts_off}, NonSack_off={ns_off}, WiFi_RSC_off={nic_rsc_off}"
    )


def t2_f14_06(ctx: lib.ProbeContext) -> tuple[bool, str]:
    snap = ctx.pwsh_snapshot()
    if not snap.get("ok"):
        return False, f"pwsh error: {snap.get('error')}"
    mm_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    ex1, nti, rt1 = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "NetworkThrottlingIndex")
    ex2, sr, rt2 = lib.read_reg_value(winreg.HKEY_LOCAL_MACHINE, mm_path, "SystemResponsiveness")
    qos_ok, qos_msg = lib.check_fortnite_qos(snap)
    ok = (
        ex1 and rt1 == winreg.REG_DWORD and nti == 0xFFFFFFFF
        and ex2 and rt2 == winreg.REG_DWORD and sr == 0
        and qos_ok
    )
    return ok, f"NTI={hex(nti) if isinstance(nti, int) else nti!r}(t={rt1}), SR={sr!r}(t={rt2}), QoS=({qos_msg})"


# =============================================================================
# F15: Post-Optimization Benchmarks & E2E Pass (Boundaries & Negative)
# =============================================================================

def _run_report_gen_in_sandbox(script: Path, sb_root: Path) -> tuple[int, str, str]:
    rep = sb_root / "reports"
    # Try standard CLI flags (--reports-dir or --baseline-sys/--out); fall back to positional/help check
    args_list = [
        [
            sys.executable, str(script),
            "--baseline-system", str(rep / "baseline_system.json"),
            "--post-system", str(rep / "post_system.json"),
            "--baseline-network", str(rep / "baseline_network.json"),
            "--post-network", str(rep / "post_network.json"),
            "--out", str(rep / "comparison_report.md"),
        ],
        [sys.executable, str(script), "--reports-dir", str(rep), "--out", str(rep / "comparison_report.md")],
    ]
    # Check --help output to pick supported interface cleanly
    h_rc, h_out, _ = lib.run_cli_process([sys.executable, str(script), "--help"])
    if "--reports-dir" in h_out:
        return lib.run_cli_process(args_list[1])
    if "--baseline-system" in h_out:
        return lib.run_cli_process(args_list[0])
    # Try --project-root if supported
    if "--project-root" in h_out:
        return lib.run_cli_process([sys.executable, str(script), "--project-root", str(sb_root)])
    return lib.run_cli_process(args_list[1])


def t2_f15_01(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/generate_comparison_report.py"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        # Both baseline_system.json and baseline_network.json missing -> must exit non-zero
        rc, _, _ = _run_report_gen_in_sandbox(script, sb.root)
        if rc == 0:
            return False, "generate_comparison_report.py exited 0 despite missing all baseline JSONs"
    return True, f"generate_comparison_report.py exited non-zero ({rc}) on missing baseline JSONs"


def t2_f15_02(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/generate_comparison_report.py"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        (sb.root / "reports/baseline_system.json").write_text("{corrupt json", encoding="utf-8")
        (sb.root / "reports/post_system.json").write_text(json.dumps(sb.sample_system_json("post", "2026-11-21T16:10:00Z")), encoding="utf-8")
        (sb.root / "reports/baseline_network.json").write_text(json.dumps(sb.sample_network_json("baseline")), encoding="utf-8")
        (sb.root / "reports/post_network.json").write_text(json.dumps(sb.sample_network_json("post", "2026-11-21T16:11:00Z")), encoding="utf-8")
        rc, _, _ = _run_report_gen_in_sandbox(script, sb.root)
        if rc == 0:
            return False, "generate_comparison_report.py exited 0 on malformed JSON"
        # Also verify NaN / Infinity constants are rejected (G7)
        (sb.root / "reports/baseline_system.json").write_text(json.dumps(sb.sample_system_json("baseline")), encoding="utf-8")
        nan_post = sb.sample_system_json("post", "2026-11-21T16:10:00Z")
        nan_post["system_overhead"]["available_ram_mb"] = float("nan")
        (sb.root / "reports/post_system.json").write_text(json.dumps(nan_post, allow_nan=True), encoding="utf-8")
        rc_nan, _, _ = _run_report_gen_in_sandbox(script, sb.root)
        if rc_nan == 0:
            return False, "generate_comparison_report.py exited 0 on NaN JSON constant"
    return True, f"generate_comparison_report.py rejected malformed JSON (rc={rc}) and NaN constant (rc={rc_nan})"


def t2_f15_03(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/generate_comparison_report.py"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        bad_sys = sb.sample_system_json("post", "2026-11-21T16:10:00Z")
        del bad_sys["gpu_bench"]
        (sb.root / "reports" / "baseline_system.json").write_text(json.dumps(bad_sys), encoding="utf-8")
        ok_schema, errs = lib.validate_system_json(bad_sys, expected_tag="post")
        rc_gen, _, _ = _run_report_gen_in_sandbox(script, sb.root)
        rc_cli, _, _ = lib.run_cli_process([sys.executable, str(script), "--unknown-flag-xyz"])
        if ok_schema or rc_gen == 0 or rc_cli == 0:
            return False, (
                f"Schema/CLI rejection failed: ok_schema={ok_schema}, rc_gen={rc_gen}, rc_cli={rc_cli}"
            )
    return True, (
        f"Schema & report generator rejected missing gpu_bench ({errs[0]}, rc_gen={rc_gen}) "
        f"& report CLI rejected unknown flag (rc={rc_cli})"
    )


def t2_f15_04(ctx: lib.ProbeContext) -> tuple[bool, str]:
    script = ctx.project_root / "scripts/generate_comparison_report.py"
    if not script.is_file():
        return False, f"Missing {script}"
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        (sb.root / "reports/baseline_system.json").write_text(json.dumps(sb.sample_system_json("baseline", "2026-11-21T16:00:00Z")), encoding="utf-8")
        (sb.root / "reports/post_system.json").write_text(json.dumps(sb.sample_system_json("post", "2026-11-21T16:10:00Z")), encoding="utf-8")
        (sb.root / "reports/baseline_network.json").write_text(json.dumps(sb.sample_network_json("baseline", "2026-11-21T16:01:00Z")), encoding="utf-8")
        (sb.root / "reports/post_network.json").write_text(json.dumps(sb.sample_network_json("post", "2026-11-21T16:11:00Z")), encoding="utf-8")
        rc, out, err = _run_report_gen_in_sandbox(script, sb.root)
        rep_md = sb.root / "reports/comparison_report.md"
        if rc != 0 or not rep_md.is_file():
            return False, f"Sandbox comparison report generation failed: rc={rc}, err={err[:150]}"
        sz = rep_md.stat().st_size
        if sz < 100:
            return False, f"Sandbox comparison_report.md too small ({sz} bytes)"
    return True, f"Sandbox comparison_report.md generated ({sz} bytes)"


def t2_f15_05(ctx: lib.ProbeContext) -> tuple[bool, str]:
    pairs = [
        ("baseline_system.json", "post_system.json"),
        ("baseline_network.json", "post_network.json"),
    ]
    for b_name, p_name in pairs:
        ok_b, b_data, msg_b = lib.load_json_file(ctx.project_root / "reports" / b_name)
        ok_p, p_data, msg_p = lib.load_json_file(ctx.project_root / "reports" / p_name)
        if not ok_b:
            return False, msg_b
        if not ok_p:
            return False, msg_p
        if not isinstance(b_data, dict) or not isinstance(p_data, dict):
            return False, f"Invalid JSON root types for {b_name}/{p_name}: {type(b_data).__name__}/{type(p_data).__name__}"
        dt_b = lib.parse_utc_iso(b_data.get("timestamp_utc", ""))
        dt_p = lib.parse_utc_iso(p_data.get("timestamp_utc", ""))
        if dt_b is None or dt_p is None or dt_p <= dt_b:
            return False, f"Invalid timestamp ordering {b_name}({dt_b}) vs {p_name}({dt_p})"
    return True, "baseline < post UTC timestamps strictly ordered for system & network reports"


def get_tests() -> list[lib.TestCase]:
    return [
        # F1
        lib.TestCase("T2-F01-01", 2, "F1", "R3", "Sandbox rejects corrupt/empty .reg exports", t2_f01_01),
        lib.TestCase("T2-F01-02", 2, "F1", "R3", "Sandbox detects zero-byte .pow & missing restore_point_info.json", t2_f01_02),
        lib.TestCase("T2-F01-03", 2, "F1", "R3", "Live .reg/.pow backups exceed 100B & services >= 20 entries", t2_f01_03),
        lib.TestCase("T2-F01-04", 2, "F1", "R3", "backup_baseline.ps1 AST valid & parameter contract", t2_f01_04),
        lib.TestCase("T2-F01-05", 2, "F1", "R3", "UTF-16LE & UTF-8 BOM .reg exports with non-ASCII chars valid", t2_f01_05),
        # F2
        lib.TestCase("T2-F02-01", 2, "F2", "R3", "System schema rejects missing gpu_bench, negative FLOPS, bad ts", t2_f02_01),
        lib.TestCase("T2-F02-02", 2, "F2", "R3", "System schema rejects non-monotonic jitter & zero disk_bench", t2_f02_02),
        lib.TestCase("T2-F02-03", 2, "F2", "R3", "benchmark_system.py rejects invalid --tag with non-zero rc", t2_f02_03),
        lib.TestCase("T2-F02-04", 2, "F2", "R3", "benchmark_system.py rejects unknown CLI flag with non-zero rc", t2_f02_04),
        lib.TestCase("T2-F02-05", 2, "F2", "R3", "Hardware RAM/CPU/GPU temperature boundary checks", t2_f02_05),
        # F3
        lib.TestCase("T2-F03-01", 2, "F3", "R2,R3", "Network schema rejects bad ping quantiles & loss_pct > 100", t2_f03_01),
        lib.TestCase("T2-F03-02", 2, "F3", "R2,R3", "Network schema rejects missing 1.1.1.1, empty dns_bench, bad p1", t2_f03_02),
        lib.TestCase("T2-F03-03", 2, "F3", "R2,R3", "benchmark_network.ps1 rejects invalid -Tag with non-zero rc", t2_f03_03),
        lib.TestCase("T2-F03-04", 2, "F3", "R2,R3", "benchmark_network.ps1 rejects unknown switch with non-zero rc", t2_f03_04),
        lib.TestCase("T2-F03-05", 2, "F3", "R2,R3", "Live gateway ping 0% loss, monotonic quantiles, no wake-spike", t2_f03_05),
        # F4
        lib.TestCase("T2-F04-01", 2, "F4", "R3", "rollback_all.ps1 fails safely on empty sandbox -BackupDir", t2_f04_01),
        lib.TestCase("T2-F04-02", 2, "F4", "R3", "rollback_all.ps1 rejects non-existent -BackupDir", t2_f04_02),
        lib.TestCase("T2-F04-03", 2, "F4", "R2", "gaming_wlan_scan_toggle.ps1 rejects invalid -Mode", t2_f04_03),
        lib.TestCase("T2-F04-04", 2, "F4", "R1", "toggle_vbs_hypervisor.ps1 rejects invalid -Mode", t2_f04_04),
        lib.TestCase("T2-F04-05", 2, "F4", "R1,R2,R3", "All F4 scripts declare SupportsShouldProcess or -DryRun", t2_f04_05),
        # F5
        lib.TestCase("T2-F05-01", 2, "F5", "R1", "GameDVR_Enabled & AppCaptureEnabled strictly REG_DWORD 0", t2_f05_01),
        lib.TestCase("T2-F05-02", 2, "F5", "R1", "EnableTransparency==0 & VisualFXSetting==2 strictly REG_DWORD", t2_f05_02),
        lib.TestCase("T2-F05-03", 2, "F5", "R1", "MinAnimate strictly REG_SZ '0'", t2_f05_03),
        lib.TestCase("T2-F05-04", 2, "F5", "R1", "FIFA22.exe UserGpuPreferences strictly GpuPreference=2;", t2_f05_04),
        lib.TestCase("T2-F05-05", 2, "F5", "R1", "apply_os_gpu_user.ps1 AST valid", t2_f05_05),
        # F6
        lib.TestCase("T2-F06-01", 2, "F6", "R1", "StartupApproved byte[0] bitmask boundary decoder check", t2_f06_01),
        lib.TestCase("T2-F06-02", 2, "F6", "R1,R2", "All disabled StartupApproved\\Run values are >=12B REG_BINARY odd", t2_f06_02),
        lib.TestCase("T2-F06-03", 2, "F6", "R1,R2", "Pre-disabled utweb & Docker Desktop do not regress", t2_f06_03),
        lib.TestCase("T2-F06-04", 2, "F6", "R1,R2", "Zero instances of target overlay/bloat processes running", t2_f06_04),
        lib.TestCase("T2-F06-05", 2, "F6", "R2", "apply_network_user.ps1 AST valid", t2_f06_05),
        # F7
        lib.TestCase("T2-F07-01", 2, "F7", "R1", "Active plan strictly Gaming Estable (not Ultimate/Balanced), MAX<=99, BOOST=0, COOL=1", t2_f07_01),
        lib.TestCase("T2-F07-02", 2, "F7", "R1", "Exact USB selective suspend GUID AC==0 (AC-only NoSystemBattery)", t2_f07_02),
        lib.TestCase("T2-F07-03", 2, "F7", "R1", "Win32PrioritySeparation strictly REG_DWORD 0x26", t2_f07_03),
        lib.TestCase("T2-F07-04", 2, "F7", "R1", "PowerThrottlingOff==1 & HiberbootEnabled==0 strictly REG_DWORD", t2_f07_04),
        lib.TestCase("T2-F07-05", 2, "F7", "R1", "C:\\hiberfil.sys absent & Hibernate unavailable in powercfg /a", t2_f07_05),
        # F8
        lib.TestCase("T2-F08-01", 2, "F8", "R1", "NvAPI ShaderCache boundary (>=0x2800 or 0xffffffff)", t2_f08_01),
        lib.TestCase("T2-F08-02", 2, "F8", "R1", "NvAPI TexFilterQual strictly 0x14 (High Performance)", t2_f08_02),
        lib.TestCase("T2-F08-03", 2, "F8", "R1", "NvAPI PowerMgmt=1, LowLatency=1, ThreadedOpt=1 strictly", t2_f08_03),
        lib.TestCase("T2-F08-04", 2, "F8", "R1", "GTX 1650 temp <85C, no thermal throttle, safe TdrDelay", t2_f08_04),
        lib.TestCase("T2-F08-05", 2, "F8", "R1", "Dynamic AMD Display Class lookup & EnableUlps==0 REG_DWORD", t2_f08_05),
        # F9
        lib.TestCase("T2-F09-01", 2, "F9", "R1", "PCManager, MapsBroker, NetworkDaemon strictly Disabled+Stopped", t2_f09_01),
        lib.TestCase("T2-F09-02", 2, "F9", "R1", "Fax NOT_FOUND or Disabled handled cleanly", t2_f09_02),
        lib.TestCase("T2-F09-03", 2, "F9", "R1", "Essential services (Dhcp/Dnscache/WlanSvc/WinDefend) not disabled", t2_f09_03),
        lib.TestCase("T2-F09-04", 2, "F9", "R1", "WSearch/TrkWks/Spooler/lfsvc not left Automatic", t2_f09_04),
        lib.TestCase("T2-F09-05", 2, "F9", "R1", "Target scheduled tasks not Ready or Running", t2_f09_05),
        # F10
        lib.TestCase("T2-F10-01", 2, "F10", "R1", "EnableVirtualizationBasedSecurity strictly REG_DWORD 0", t2_f10_01),
        lib.TestCase("T2-F10-02", 2, "F10", "R1", "HVCI Enabled absent or strictly REG_DWORD 0", t2_f10_02),
        lib.TestCase("T2-F10-03", 2, "F10", "R1", "Win32_DeviceGuard runtime VBS status 0 (or 2 only when HypervisorPresent & HVCI/VBS reg=0)", t2_f10_03),
        lib.TestCase("T2-F10-04", 2, "F10", "R1", "DeviceGuard SecurityServicesRunning excludes 1 & 2", t2_f10_04),
        lib.TestCase("T2-F10-05", 2, "F10", "R1", "apply_os_gpu_admin.ps1 AST, -BackupDir & WhatIf/DryRun contract", t2_f10_05),
        # F11
        lib.TestCase("T2-F11-01", 2, "F11", "R2", "Wi-Fi LpsEn strictly '0' (not '1' or '2')", t2_f11_01),
        lib.TestCase("T2-F11-02", 2, "F11", "R2", "Wi-Fi PnPCapabilities bitmask includes 0x18 (24)", t2_f11_02),
        lib.TestCase("T2-F11-03", 2, "F11", "R2", "DEV_8852 MSPower_DeviceEnable strictly False", t2_f11_03),
        lib.TestCase("T2-F11-04", 2, "F11", "R2", "Wireless Power Saving strictly 0 on both AC & DC", t2_f11_04),
        lib.TestCase("T2-F11-05", 2, "F11", "R2", "PCIe ASPM strictly 0 on both AC & DC", t2_f11_05),
        # F12
        lib.TestCase("T2-F12-01", 2, "F12", "R2", "RegROAMSensitiveLevel strictly '127' (not 80/70/65)", t2_f12_01),
        lib.TestCase("T2-F12-02", 2, "F12", "R2", "Wi-Fi strictly 5 GHz (not 2.4 GHz), Signal >=60%, RSSI >-80", t2_f12_02),
        lib.TestCase("T2-F12-03", 2, "F12", "R2", "Wi-Fi PreferBand strictly '2' (5G first)", t2_f12_03),
        lib.TestCase("T2-F12-04", 2, "F12", "R2", "CLARO1 manual vs CLARO2 auto-connect strict split", t2_f12_04),
        lib.TestCase("T2-F12-05", 2, "F12", "R2", "apply_network_admin.ps1 AST, -BackupDir & WhatIf/DryRun contract", t2_f12_05),
        # F13
        lib.TestCase("T2-F13-01", 2, "F13", "R2", "Wi-Fi DNS strictly valid static gaming pair (1.1.1.1/1.0.0.1 or 9.9.9.9/8.8.8.8)", t2_f13_01),
        lib.TestCase("T2-F13-02", 2, "F13", "R2", "DODownloadMode strictly REG_DWORD 0 (in 0..3)", t2_f13_02),
        lib.TestCase("T2-F13-03", 2, "F13", "R2", "Live configured primary & 9.9.9.9 DNS avg <120ms & p95 <200ms", t2_f13_03),
        lib.TestCase("T2-F13-04", 2, "F13", "R2", "Live HTTPS 204 returns status 204 in <3000ms", t2_f13_04),
        lib.TestCase("T2-F13-05", 2, "F13", "R2", "Zero active P2P process instances running", t2_f13_05),
        # F14
        lib.TestCase("T2-F14-01", 2, "F14", "R2", "All 5 Ethernet advanced properties strictly '0'", t2_f14_01),
        lib.TestCase("T2-F14-02", 2, "F14", "R2", "Dynamic GUID->Class resolver matches Realtek Wi-Fi & GbE", t2_f14_02),
        lib.TestCase("T2-F14-03", 2, "F14", "R2", "Ethernet checksum offloads remain RxTx ('3')", t2_f14_03),
        lib.TestCase("T2-F14-04", 2, "F14", "R2", "TcpAckFrequency & TCPNoDelay strictly REG_DWORD 1", t2_f14_04),
        lib.TestCase("T2-F14-05", 2, "F14", "R2", "TCP AutoTuning=normal, RSS=enabled, RSC/ECN/Timestamps/NonSack=disabled", t2_f14_05),
        lib.TestCase("T2-F14-06", 2, "F14", "R2", "Multimedia SystemProfile REG_DWORDs & Fortnite_Gaming_QoS DSCP 46", t2_f14_06),
        # F15
        lib.TestCase("T2-F15-01", 2, "F15", "R3", "Sandbox: generate_comparison_report.py fails on missing baseline JSON", t2_f15_01),
        lib.TestCase("T2-F15-02", 2, "F15", "R3", "Sandbox: generate_comparison_report.py fails on malformed JSON", t2_f15_02),
        lib.TestCase("T2-F15-03", 2, "F15", "R3", "Schema rejects missing gpu_bench & report CLI rejects unknown flag", t2_f15_03),
        lib.TestCase("T2-F15-04", 2, "F15", "R3", "Sandbox: generate_comparison_report.py builds report from valid pair", t2_f15_04),
        lib.TestCase("T2-F15-05", 2, "F15", "R3", "Live baseline < post UTC timestamps strictly ordered", t2_f15_05),
    ]
