"""Opaque-box 4-Tier E2E Test Runner for laptop_optimization.

Usage:
    python tests/run_e2e_tests.py --tier 1
    python tests/run_e2e_tests.py --tier all --verbose --json-out out.json
    python tests/run_e2e_tests.py --self-test --verbose
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import e2e_lib as lib
import test_tier1_features as t1
import test_tier2_boundaries as t2
import test_tier3_pairwise as t3
import test_tier4_scenarios as t4


def collect_tiers() -> dict[int, list[lib.TestCase]]:
    return {
        1: t1.get_tests(),
        2: t2.get_tests(),
        3: t3.get_tests(),
        4: t4.get_tests(),
    }


def execute_cases(cases: list[lib.TestCase], ctx: lib.ProbeContext, verbose: bool = False) -> list[lib.TestResult]:
    results: list[lib.TestResult] = []
    for tc in cases:
        t0 = time.perf_counter()
        try:
            passed, detail = tc.fn(ctx)
            status = "PASS" if passed else "FAIL"
        except Exception as exc:
            status = "ERROR"
            detail = f"Unhandled {type(exc).__name__}: {exc} | {traceback.format_exc(limit=2).strip()}"
        dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        res = lib.TestResult(
            id=tc.id,
            tier=tc.tier,
            feature=tc.feature,
            requirement=tc.requirement,
            name=tc.name,
            status=status,
            duration_ms=dt_ms,
            detail=detail,
        )
        results.append(res)
        if verbose:
            print(f"[{res.status:5s}] {res.id} ({res.feature}) {res.name} [{res.duration_ms:.1f}ms] :: {res.detail}")
    return results


def summarize_results(results: list[lib.TestResult]) -> dict[str, Any]:
    counts = Counter(r.status for r in results)
    per_tier: dict[str, dict[str, int]] = {}
    for tier in sorted({r.tier for r in results}):
        sub = [r for r in results if r.tier == tier]
        c = Counter(r.status for r in sub)
        per_tier[str(tier)] = {
            "total": len(sub),
            "PASS": c.get("PASS", 0),
            "FAIL": c.get("FAIL", 0),
            "ERROR": c.get("ERROR", 0),
        }
    return {
        "total": len(results),
        "PASS": counts.get("PASS", 0),
        "FAIL": counts.get("FAIL", 0),
        "ERROR": counts.get("ERROR", 0),
        "per_tier": per_tier,
    }


def print_summary(summary: dict[str, Any], results: list[lib.TestResult], verbose: bool) -> None:
    print("\n=== E2E Test Summary ===")
    for t_str, stats in summary["per_tier"].items():
        print(
            f"  Tier {t_str}: total={stats['total']} | PASS={stats['PASS']} | FAIL={stats['FAIL']} | ERROR={stats['ERROR']}"
        )
    print(
        f"  OVERALL: total={summary['total']} | PASS={summary['PASS']} | FAIL={summary['FAIL']} | ERROR={summary['ERROR']}"
    )
    if not verbose:
        errs = [r for r in results if r.status == "ERROR"]
        if errs:
            print("\n=== Errors ===")
            for r in errs:
                print(f"  [ERROR] {r.id}: {r.detail}")


def run_self_test(project_root: Path, verbose: bool) -> int:
    print("=== Running E2E Suite Self-Test ===")
    tiers = collect_tiers()
    all_cases = [tc for k in (1, 2, 3, 4) for tc in tiers[k]]
    invariants_ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal invariants_ok
        tag = "OK" if cond else "BAD"
        if verbose or not cond:
            print(f"  [SELF-CHECK {tag}] {msg}")
        if not cond:
            invariants_ok = False

    # 1. Verify Tier counts & per-feature coverage >=5 for F1..F15 in Tier 1 & Tier 2
    check(len(tiers[1]) >= 75, f"Tier 1 count={len(tiers[1])} >= 75")
    check(len(tiers[2]) >= 75, f"Tier 2 count={len(tiers[2])} >= 75")
    check(len(tiers[3]) >= 15, f"Tier 3 count={len(tiers[3])} >= 15")
    check(len(tiers[4]) >= 8, f"Tier 4 count={len(tiers[4])} >= 8")

    ids = [tc.id for tc in all_cases]
    check(len(ids) == len(set(ids)), f"All {len(ids)} test IDs are unique")

    for t_num in (1, 2):
        feat_counts = Counter(tc.feature for tc in tiers[t_num])
        for i in range(1, 16):
            fk = f"F{i}"
            check(feat_counts[fk] >= 5, f"Tier {t_num} {fk} count={feat_counts[fk]} >= 5")

    # 2. Verify read-only probes execute cleanly under Medium Integrity without permission errors
    ctx = lib.ProbeContext(project_root)
    snap = ctx.pwsh_snapshot()
    check(bool(snap.get("ok")), f"Batched pwsh snapshot ok={snap.get('ok')} err={snap.get('error', '')}")
    sch = ctx.power_scheme()
    check(
        bool(sch.get("ok")) and sch.get("name") == "Gaming Estable (Sin Bateria)",
        f"powercfg active scheme ok={sch.get('ok')} guid={sch.get('guid')} name={sch.get('name')!r}",
    )
    pmax = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PROC_MAX)
    boost = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_PERF_BOOST_MODE)
    cool = ctx.powercfg_setting(lib.SUB_PROCESSOR, lib.SET_SYS_COOL_POL)
    check(
        bool(pmax.get("ok") and boost.get("ok") and cool.get("ok")),
        f"powercfg /qh processor probes ok (max={pmax.get('ac')}, boost={boost.get('ac')}, cool={cool.get('ac')})",
    )
    check(
        lib.is_gaming_stable_scheme({"ok": True, "guid": lib.GAMING_STABLE_SCHEME_GUID, "name": "Gaming Estable (Sin Bateria)"})
        and not lib.is_gaming_stable_scheme({"ok": True, "guid": "8ac65e32-075f-48d5-828b-02e93ad3c1f8", "name": "Ultimate Performance"}),
        "is_gaming_stable_scheme accepts Gaming Estable and rejects Ultimate Performance",
    )
    check(
        lib.is_valid_static_gaming_dns(["1.1.1.1", "1.0.0.1"])
        and lib.is_valid_static_gaming_dns(["9.9.9.9", "8.8.8.8"])
        and not lib.is_valid_static_gaming_dns(["192.168.1.1", "1.1.1.1"]),
        "is_valid_static_gaming_dns accepts 1.1.1.1/1.0.0.1 and 9.9.9.9/8.8.8.8 and rejects DHCP gateway",
    )
    usb = ctx.powercfg_setting(lib.SUB_USB, lib.SET_USB_SUSPEND)
    check(bool(usb.get("ok")), f"powercfg USB selective suspend ok={usb.get('ok')} ac={usb.get('ac')}")
    drs = ctx.nvapi_drs()
    check(bool(drs.get("ok")), f"NvAPI DRS ctypes probe ok={drs.get('ok')} err={drs.get('error', '')}")
    smi = ctx.nvidia_smi()
    check(bool(smi.get("ok")), f"nvidia-smi probe ok={smi.get('ok')} name={smi.get('name')}")
    pings = ctx.live_pings()
    gw_ping = pings.get("192.168.1.1", {})
    check(
        bool(gw_ping.get("ok")) and lib.is_exact_int(gw_ping.get("sent")) and gw_ping.get("sent", 0) >= 30,
        f"Live gateway ping ok (sent={gw_ping.get('sent')})",
    )
    dns = ctx.live_dns()
    check(bool(dns.get("1.1.1.1", {}).get("ok") and dns.get("9.9.9.9", {}).get("ok")), f"Live DNS 1.1.1.1 & 9.9.9.9 ok")
    https = ctx.live_https()
    check(bool(https.get("ok")), f"Live HTTPS 204 ok={https.get('ok')} status={https.get('status_code')}")

    # 3. Verify TempProjectSandbox & schema validators (positive + negative)
    with lib.TempProjectSandbox() as sb:
        assert sb.root is not None
        s_ok, s_errs = lib.validate_system_json(sb.sample_system_json("baseline"), "baseline")
        check(s_ok, f"Sandbox valid system JSON passed ({s_errs})")
        n_ok, n_errs = lib.validate_network_json(sb.sample_network_json("post"), "post")
        check(n_ok, f"Sandbox valid network JSON passed ({n_errs})")

        bad_sys = sb.sample_system_json("baseline")
        bad_sys["gpu_bench"]["cuda_fp32_tflops"] = -5.0
        bad_s_ok, _ = lib.validate_system_json(bad_sys)
        check(not bad_s_ok, "Sandbox negative system JSON rejected")

        bad_net = sb.sample_network_json("baseline")
        bad_net["gateway_ping"]["loss_pct"] = 120.0
        bad_n_ok, _ = lib.validate_network_json(bad_net)
        check(not bad_n_ok, "Sandbox negative network JSON rejected")

        # Tightened Iteration 2 negative validator & AST checks
        bad_jitter = sb.sample_system_json("baseline")
        bad_jitter["timer_jitter_ms"] = {"mean": 99.0, "p95": 1.0, "p99": 2.0, "max": 3.0}
        ok_j, _ = lib.validate_system_json(bad_jitter)
        check(not ok_j, "Validator rejects timer_jitter_ms.mean > max")

        bad_ping_avg = sb.sample_network_json("baseline")
        bad_ping_avg["gateway_ping"]["avg_ms"] = -50.0
        ok_pa, _ = lib.validate_network_json(bad_ping_avg)
        check(not ok_pa, "Validator rejects negative gateway_ping.avg_ms")

        bad_dns_neg = sb.sample_network_json("baseline")
        bad_dns_neg["dns_bench"] = {"9.9.9.9": {"avg_ms": -10.0, "p95_ms": -20.0}}
        ok_dn, _ = lib.validate_network_json(bad_dns_neg)
        check(not ok_dn, "Validator rejects negative dns_bench latency")

        bad_dns_srv = sb.sample_network_json("baseline")
        bad_dns_srv["dns_bench"] = [{"avg_ms": 10.0, "p95_ms": 15.0}]
        ok_ds, _ = lib.validate_network_json(bad_dns_srv)
        check(not ok_ds, "Validator rejects dns_bench list item missing 'server'")

        comment_ps1 = sb.root / "scripts" / "comment_only.ps1"
        comment_ps1.write_text("# SupportsShouldProcess -DryRun\nparam([string]$Mode)\nWrite-Host $Mode\n", encoding="utf-8")
        ast_comment = lib.check_ps_ast(comment_ps1)
        check(
            bool(ast_comment.get("ok"))
            and not ast_comment.get("supports_should_process")
            and not ast_comment.get("has_dry_run")
            and not ast_comment.get("has_whatif"),
            f"check_ps_ast rejects comment-only # SupportsShouldProcess -DryRun ({ast_comment})",
        )

        ev_bad = lib.query_events_since("invalid-ts'; whoami")
        check(not ev_bad.get("ok"), f"query_events_since rejects invalid since_iso ({ev_bad})")

        ping_bad_tgt = lib.run_live_ping_burst("127.0.0.1'; whoami", count=1)
        ping_bad_cnt = lib.run_live_ping_burst("127.0.0.1", count=True)  # type: ignore[arg-type]
        check(
            not ping_bad_tgt.get("ok") and not ping_bad_cnt.get("ok"),
            "run_live_ping_burst rejects injected target & bool count",
        )

        # Tier-5 offline mocked/sandbox unit checks (zero live OS/WAN calls)
        check(
            not t3._gw_ok({"ok": True, "loss_pct": False, "packet1_ms": 1.0, "p50_ms": 1.0, "p95_ms": 1.0})
            and not t3._gw_ok({"ok": True, "loss_pct": 0.0, "packet1_ms": float("-inf"), "p50_ms": 1.0, "p95_ms": 1.0})
            and not t3._gw_ok({"ok": True, "loss_pct": 0.0, "packet1_ms": "bad", "p50_ms": 1.0, "p95_ms": 1.0})
            and t3._gw_ok({"ok": True, "loss_pct": 0.0, "packet1_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0}),
            "t3._gw_ok rejects bool/-inf/str and accepts 0.0ms boundary",
        )

        # G4 Tier-5 quantile check: count=30 with 1 outlier tolerates p95 < max
        q30 = lib.compute_ping_stats("192.168.1.1", [1.0] * 29 + [25.0], 30, 0)
        check(
            q30["p95_ms"] == 1.0 and q30["max_ms"] == 25.0 and q30["p95_ms"] < q30["max_ms"],
            f"G4 count=30 quantile tolerates single outlier (p95={q30['p95_ms']} < max={q30['max_ms']})",
        )

        # G1 Tier-5 CLI probe: gaming_wlan_scan_toggle.ps1 -Mode restore -DryRun -Json vs -Mode normal
        toggle_ps1 = project_root / "scripts/gaming_wlan_scan_toggle.ps1"
        rc_tr, out_tr, err_tr = lib.run_cli_process(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(toggle_ps1), "-Mode", "restore", "-DryRun", "-Json"]
        )
        rc_tn, _, _ = lib.run_cli_process(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(toggle_ps1), "-Mode", "normal", "-DryRun"]
        )
        tr_json = json.loads(out_tr) if rc_tr == 0 and out_tr.strip() else {}
        check(
            rc_tr == 0 and isinstance(tr_json, dict) and tr_json.get("mode") == "restore" and tr_json.get("dry_run") is True and rc_tn != 0,
            f"G1 toggle CLI accepts -Mode restore -DryRun -Json (rc={rc_tr}) and rejects -Mode normal (rc={rc_tn}, err={err_tr[:80]})",
        )

        # G3 Tier-5 -WhatIf non-mutation probe across all 6 scripts (apply_* + rollback_all.ps1)
        whatif_bkp = sb.root / "whatif_bkp"
        for sub in ("system", "power", "network"):
            (whatif_bkp / sub).mkdir(parents=True, exist_ok=True)
        (whatif_bkp / "power" / "power_scheme_dummy.pow").write_bytes(b"X" * 200)
        (whatif_bkp / "system" / "restore_point_info.json").write_text('{"checkpoint_status": "created"}', encoding="utf-8")
        (whatif_bkp / "system" / "services_baseline.json").write_text("[]", encoding="utf-8")
        (whatif_bkp / "system" / "tasks_baseline.json").write_text("[]", encoding="utf-8")
        (whatif_bkp / "network" / "dns_before.json").write_text('{"interfaces": []}', encoding="utf-8")
        whatif_probes = [
            ("apply_os_gpu_user.ps1", ["-WhatIf"]),
            ("apply_network_user.ps1", ["-WhatIf"]),
            ("apply_os_gpu_admin.ps1", ["-BackupDir", str(whatif_bkp), "-WhatIf"]),
            ("apply_network_admin.ps1", ["-BackupDir", str(whatif_bkp), "-SkipDnsBenchmark", "-WhatIf"]),
            ("apply_all_admin.ps1", ["-BackupDir", str(whatif_bkp), "-SkipDnsBenchmark", "-WhatIf"]),
            ("rollback_all.ps1", ["-BackupDir", str(whatif_bkp), "-WhatIf"]),
        ]
        whatif_ok = True
        whatif_diag = []
        for s_name, extra_args in whatif_probes:
            rep_tmp = sb.root / f"whatif_rep_{s_name.replace('.ps1', '')}"
            rc_w, out_w, err_w = lib.run_cli_process(
                ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(project_root / "scripts" / s_name), "-ReportDir", str(rep_tmp)] + extra_args
            )
            created = rep_tmp.exists()
            if rc_w != 0 or created:
                whatif_ok = False
            whatif_diag.append(f"{s_name}:rc={rc_w},created={created}")
        check(whatif_ok, f"G3 -WhatIf non-mutation across 6 scripts: {whatif_diag}")

        # G7/G12 Tier-5 NaN/Infinity/overflow (1e309 / -1e309) rejection probe in generate_comparison_report.py
        gen_py = project_root / "scripts/generate_comparison_report.py"
        nan_dir = sb.root / "nan_probe"
        nan_dir.mkdir(parents=True, exist_ok=True)
        (nan_dir / "baseline_system.json").write_text(json.dumps(sb.sample_system_json("baseline")), encoding="utf-8")
        nan_post = sb.sample_system_json("post", "2026-11-21T16:10:00Z")
        nan_post["system_overhead"]["available_ram_mb"] = float("nan")
        (nan_dir / "post_system.json").write_text(json.dumps(nan_post, allow_nan=True), encoding="utf-8")
        inf_post = sb.sample_system_json("post", "2026-11-21T16:10:00Z")
        inf_post["system_overhead"]["available_ram_mb"] = float("inf")
        rc_nan, _, err_nan = lib.run_cli_process([
            sys.executable, str(gen_py),
            "--baseline-system", str(nan_dir / "baseline_system.json"),
            "--post-system", str(nan_dir / "post_system.json"),
            "--out", str(nan_dir / "out.md"),
        ])
        (nan_dir / "post_system.json").write_text(json.dumps(inf_post, allow_nan=True), encoding="utf-8")
        rc_inf, _, err_inf = lib.run_cli_process([
            sys.executable, str(gen_py),
            "--baseline-system", str(nan_dir / "baseline_system.json"),
            "--post-system", str(nan_dir / "post_system.json"),
            "--out", str(nan_dir / "out.md"),
        ])
        ovf_raw = json.dumps(sb.sample_system_json("post", "2026-11-21T16:10:00Z")).replace("14200.0", "1e309")
        (nan_dir / "post_system.json").write_text(ovf_raw, encoding="utf-8")
        rc_ovf, _, err_ovf = lib.run_cli_process([
            sys.executable, str(gen_py),
            "--baseline-system", str(nan_dir / "baseline_system.json"),
            "--post-system", str(nan_dir / "post_system.json"),
            "--out", str(nan_dir / "out.md"),
        ])
        novf_raw = json.dumps(sb.sample_system_json("post", "2026-11-21T16:10:00Z")).replace("14200.0", "-1e309")
        (nan_dir / "post_system.json").write_text(novf_raw, encoding="utf-8")
        rc_novf, _, err_novf = lib.run_cli_process([
            sys.executable, str(gen_py),
            "--baseline-system", str(nan_dir / "baseline_system.json"),
            "--post-system", str(nan_dir / "post_system.json"),
            "--out", str(nan_dir / "out.md"),
        ])
        check(
            rc_nan != 0 and rc_inf != 0 and rc_ovf != 0 and rc_novf != 0 and not (nan_dir / "out.md").exists(),
            f"G7/G12 generate_comparison_report.py rejects NaN (rc={rc_nan}), Infinity (rc={rc_inf}), 1e309 (rc={rc_ovf}), -1e309 (rc={rc_novf})",
        )

        # G8 Tier-5 rollback_all.ps1 -DryRun partial-JSON {} StrictMode resilience probe
        part_bkp = sb.root / "partial_bkp"
        part_rep = sb.root / "partial_rep"
        for sub in ("system", "power", "network"):
            (part_bkp / sub).mkdir(parents=True, exist_ok=True)
        (part_bkp / "system" / "absent_keys.json").write_text("{}", encoding="utf-8")
        (part_bkp / "system" / "os_state_before.json").write_text("{}", encoding="utf-8")
        (part_bkp / "network" / "dns_before.json").write_text("{}", encoding="utf-8")
        (part_bkp / "system" / "services_baseline.json").write_text("[{}]", encoding="utf-8")
        (part_bkp / "system" / "tasks_baseline.json").write_text("[{}]", encoding="utf-8")
        rc_part, out_part, err_part = lib.run_cli_process([
            "pwsh", "-NoProfile", "-NonInteractive", "-File", str(project_root / "scripts/rollback_all.ps1"),
            "-BackupDir", str(part_bkp), "-ReportDir", str(part_rep), "-DryRun",
        ])
        check(
            rc_part == 0,
            f"G8 rollback_all.ps1 -DryRun handles partial {{}} backup JSONs cleanly (rc={rc_part}, err={err_part[:120]})",
        )

        # G9 Tier-5 bool vs exact int boundary probes
        check(
            (not lib.is_exact_int(True)) and (not lib.is_exact_int(False)) and lib.is_exact_int(0) and lib.is_exact_int(1),
            "G9 is_exact_int rejects bool (True/False) and accepts int (0/1)",
        )
        bool_ctx = lib.ProbeContext(sb.root)
        bool_ctx._cache["nvapi_drs"] = {
            "ok": True,
            "settings": {
                lib.NVAPI_DRS_POWER_MGMT: {"found": True, "current_u32": True},
                lib.NVAPI_DRS_LOW_LATENCY: {"found": True, "current_u32": True},
                lib.NVAPI_DRS_THREADED_OPT: {"found": True, "current_u32": True},
                lib.NVAPI_DRS_TEX_FILTER_QUAL: {"found": True, "current_u32": True},
                lib.NVAPI_DRS_SHADER_CACHE_MB: {"found": True, "current_u32": True},
            },
        }
        bool_ctx._cache[f"powercfg:{lib.SUB_USB}:{lib.SET_USB_SUSPEND}"] = {"ok": True, "ac": False, "dc": False}
        bool_ctx._cache[f"powercfg:{lib.SUB_WIRELESS}:{lib.SET_WIRELESS_PS}"] = {"ok": True, "ac": False, "dc": False}
        bool_ctx._cache[f"powercfg:{lib.SUB_PCIE}:{lib.SET_PCIE_ASPM}"] = {"ok": True, "ac": False, "dc": False}
        check(
            (not t1.t1_f08_01(bool_ctx)[0])
            and (not t1.t1_f08_02(bool_ctx)[0])
            and (not t2.t2_f08_03(bool_ctx)[0])
            and (not t1.t1_f07_02(bool_ctx)[0])
            and (not t1.t1_f11_04(bool_ctx)[0])
            and (not t3.t3_pw_10(bool_ctx)[0]),
            "G9 Tier-5 bool traps (current_u32=True, ac=False) rejected by DRS & powercfg checks",
        )

        (sb.root / "backups/system/restore_point_info.json").write_text('{"bogus": 1}', encoding="utf-8")
        (sb.root / "backups/system/services_baseline.json").write_text('{"bogus": 1}', encoding="utf-8")
        (sb.root / "reports/post_system.json").write_text("[]", encoding="utf-8")
        (sb.root / "reports/baseline_system.json").write_text("[]", encoding="utf-8")
        (sb.root / "reports/baseline_network.json").write_text("[]", encoding="utf-8")
        (sb.root / "reports/post_network.json").write_text("[]", encoding="utf-8")
        (sb.root / "backups/power/dummy.pow").write_bytes(b"X" * 200)
        (sb.root / "backups/system/dummy.reg").write_text("Windows Registry Editor Version 5.00\r\n" + "A" * 120, encoding="utf-8")
        (sb.root / "backups/network/dummy.reg").write_text("Windows Registry Editor Version 5.00\r\n" + "A" * 120, encoding="utf-8")

        sb_ctx = lib.ProbeContext(sb.root)
        sb_ctx._cache["pwsh_snapshot"] = {
            "ok": True,
            "adapters": None,
            "adv_props": None,
            "ms_power_enable": None,
            "device_guard": None,
            "hypervisor_present": None,
            "services": None,
            "tasks": None,
            "processes": None,
            "dns_wifi": None,
            "dns_eth": None,
            "net_offload_global": None,
            "eth_checksum": None,
            "eth_lso": None,
            "wifi_rsc": None,
            "fortnite_qos": None,
            "wlan_interfaces": None,
            "wlan_settings": None,
            "wlan_profile_claro1": None,
            "wlan_profile_claro2": None,
            "tcp_global": None,
            "restore_events": None,
        }
        sb_ctx._cache["nvapi_drs"] = {"ok": True, "settings": None}
        sb_ctx._cache["nvidia_smi"] = {"ok": True, "temp_c": None, "throttle_reasons": "0x1"}
        sb_ctx._cache["live_pings"] = {
            "192.168.1.1": None,
            "9.9.9.9": {"ok": True, "loss_pct": False, "avg_ms": False},
            "8.8.8.8": {"ok": True, "loss_pct": 0.0, "avg_ms": True},
            "1.1.1.1": {"ok": True, "loss_pct": 0.0, "avg_ms": -5.0},
        }
        sb_ctx._cache["live_dns"] = {"1.1.1.1": None, "9.9.9.9": None, "8.8.8.8": None}
        sb_ctx._cache["live_https"] = {"ok": True, "status_code": 204, "latency_ms": None}
        sb_ctx._cache["power_scheme"] = {"ok": False}
        sb_ctx._cache["power_available"] = {"ok": False}
        for sub_g, set_g in (
            (lib.SUB_USB, lib.SET_USB_SUSPEND),
            (lib.SUB_WIRELESS, lib.SET_WIRELESS_PS),
            (lib.SUB_PCIE, lib.SET_PCIE_ASPM),
            (lib.SUB_PROCESSOR, lib.SET_PROC_MIN),
            (lib.SUB_PROCESSOR, lib.SET_PROC_MAX),
            (lib.SUB_PROCESSOR, lib.SET_PERF_BOOST_MODE),
            (lib.SUB_PROCESSOR, lib.SET_SYS_COOL_POL),
        ):
            sb_ctx._cache[f"powercfg:{sub_g}:{set_g}"] = {"ok": False}

        tier5_neg_results = [
            t1.t1_f01_05(sb_ctx)[0],
            t1.t1_f02_02(sb_ctx)[0],
            t1.t1_f02_04(sb_ctx)[0],
            t1.t1_f02_05(sb_ctx)[0],
            t1.t1_f03_02(sb_ctx)[0],
            t1.t1_f03_03(sb_ctx)[0],
            t1.t1_f03_04(sb_ctx)[0],
            t1.t1_f03_05(sb_ctx)[0],
            t1.t1_f04_05(sb_ctx)[0],
            t1.t1_f06_03(sb_ctx)[0],
            t1.t1_f06_04(sb_ctx)[0],
            t1.t1_f06_05(sb_ctx)[0],
            t1.t1_f07_01(sb_ctx)[0],
            t1.t1_f07_02(sb_ctx)[0],
            t1.t1_f07_03(sb_ctx)[0],
            t1.t1_f08_01(sb_ctx)[0],
            t1.t1_f08_02(sb_ctx)[0],
            t1.t1_f08_03(sb_ctx)[0],
            t1.t1_f08_04(sb_ctx)[0],
            t1.t1_f09_01(sb_ctx)[0],
            t1.t1_f09_02(sb_ctx)[0],
            t1.t1_f09_03(sb_ctx)[0],
            t1.t1_f09_04(sb_ctx)[0],
            t1.t1_f09_05(sb_ctx)[0],
            t1.t1_f10_03(sb_ctx)[0],
            t1.t1_f10_04(sb_ctx)[0],
            t1.t1_f11_01(sb_ctx)[0],
            t1.t1_f11_02(sb_ctx)[0],
            t1.t1_f11_03(sb_ctx)[0],
            t1.t1_f11_04(sb_ctx)[0],
            t1.t1_f11_05(sb_ctx)[0],
            t1.t1_f12_01(sb_ctx)[0],
            t1.t1_f12_02(sb_ctx)[0],
            t1.t1_f12_03(sb_ctx)[0],
            t1.t1_f12_04(sb_ctx)[0],
            t1.t1_f12_05(sb_ctx)[0],
            t1.t1_f13_01(sb_ctx)[0],
            t1.t1_f13_02(sb_ctx)[0],
            t1.t1_f13_05(sb_ctx)[0],
            t1.t1_f14_01(sb_ctx)[0],
            t1.t1_f14_02(sb_ctx)[0],
            t1.t1_f14_03(sb_ctx)[0],
            t1.t1_f14_04(sb_ctx)[0],
            t1.t1_f14_05(sb_ctx)[0],
            t1.t1_f15_01(sb_ctx)[0],
            t1.t1_f15_02(sb_ctx)[0],
            t1.t1_f15_03(sb_ctx)[0],
            t1.t1_f15_04(sb_ctx)[0],
            t2.t2_f01_03(sb_ctx)[0],
            t2.t2_f02_05(sb_ctx)[0],
            t2.t2_f03_05(sb_ctx)[0],
            t2.t2_f06_04(sb_ctx)[0],
            t2.t2_f07_01(sb_ctx)[0],
            t2.t2_f07_02(sb_ctx)[0],
            t2.t2_f07_05(sb_ctx)[0],
            t2.t2_f08_01(sb_ctx)[0],
            t2.t2_f08_02(sb_ctx)[0],
            t2.t2_f08_03(sb_ctx)[0],
            t2.t2_f08_04(sb_ctx)[0],
            t2.t2_f09_01(sb_ctx)[0],
            t2.t2_f09_02(sb_ctx)[0],
            t2.t2_f09_04(sb_ctx)[0],
            t2.t2_f09_05(sb_ctx)[0],
            t2.t2_f10_03(sb_ctx)[0],
            t2.t2_f10_04(sb_ctx)[0],
            t2.t2_f11_01(sb_ctx)[0],
            t2.t2_f11_02(sb_ctx)[0],
            t2.t2_f11_03(sb_ctx)[0],
            t2.t2_f11_04(sb_ctx)[0],
            t2.t2_f11_05(sb_ctx)[0],
            t2.t2_f12_01(sb_ctx)[0],
            t2.t2_f12_02(sb_ctx)[0],
            t2.t2_f12_03(sb_ctx)[0],
            t2.t2_f12_04(sb_ctx)[0],
            t2.t2_f13_01(sb_ctx)[0],
            t2.t2_f13_03(sb_ctx)[0],
            t2.t2_f13_04(sb_ctx)[0],
            t2.t2_f13_05(sb_ctx)[0],
            t2.t2_f14_01(sb_ctx)[0],
            t2.t2_f14_02(sb_ctx)[0],
            t2.t2_f14_03(sb_ctx)[0],
            t2.t2_f14_04(sb_ctx)[0],
            t2.t2_f14_05(sb_ctx)[0],
            t2.t2_f15_05(sb_ctx)[0],
            t3.t3_pw_02(sb_ctx)[0],
            t3.t3_pw_03(sb_ctx)[0],
            t3.t3_pw_04(sb_ctx)[0],
            t3.t3_pw_06(sb_ctx)[0],
            t3.t3_pw_07(sb_ctx)[0],
            t3.t3_pw_08(sb_ctx)[0],
            t3.t3_pw_09(sb_ctx)[0],
            t3.t3_pw_10(sb_ctx)[0],
            t3.t3_pw_11(sb_ctx)[0],
            t3.t3_pw_12(sb_ctx)[0],
            t3.t3_pw_13(sb_ctx)[0],
            t3.t3_pw_14(sb_ctx)[0],
            t3.t3_pw_15(sb_ctx)[0],
            t3.t3_pw_16(sb_ctx)[0],
            t4.t4_s01(sb_ctx)[0],
            t4.t4_s03(sb_ctx)[0],
            t4.t4_s04(sb_ctx)[0],
            t4.t4_s06(sb_ctx)[0],
            t4.t4_s07(sb_ctx)[0],
            t4.t4_s08(sb_ctx)[0],
        ]
        # Also verify t1_f13_04 does not raise on None services (may return True if live HKLM DODownloadMode==0)
        f13_04_res = t1.t1_f13_04(sb_ctx)
        check(
            (not any(tier5_neg_results)) and isinstance(f13_04_res[0], bool),
            f"All {len(tier5_neg_results)} Tier-5 None/corrupt/[] sandbox probes returned False with 0 exceptions",
        )

        # G2 Tier-5 null sub-object probe for t4_s03 (gpu_bench=null, adapter=null)
        null_sys = sb.sample_system_json("post", "2026-11-21T16:10:00Z")
        null_sys["gpu_bench"] = None
        null_net = sb.sample_network_json("post", "2026-11-21T16:11:00Z")
        null_net["adapter"] = None
        (sb.root / "reports/baseline_system.json").write_text(json.dumps(sb.sample_system_json("baseline")), encoding="utf-8")
        (sb.root / "reports/post_system.json").write_text(json.dumps(null_sys), encoding="utf-8")
        (sb.root / "reports/baseline_network.json").write_text(json.dumps(sb.sample_network_json("baseline")), encoding="utf-8")
        (sb.root / "reports/post_network.json").write_text(json.dumps(null_net), encoding="utf-8")
        s03_null_ok, s03_null_msg = t4.t4_s03(sb_ctx)
        check(not s03_null_ok, f"G2 t4_s03 rejects null gpu_bench/adapter without AttributeError ({s03_null_msg})")

        # G11 Tier-5 isolated single-field None assertions (processes=None, tasks=None, services=None)
        (sb.root / "reports/post_network.json").write_text(
            json.dumps(sb.sample_network_json("post", "2026-11-21T16:11:00Z")), encoding="utf-8"
        )
        g11_tasks_ctx = lib.ProbeContext(sb.root)
        g11_tasks_ctx._cache = dict(ctx._cache)
        g11_tasks_ctx._cache["pwsh_snapshot"] = {
            "ok": True,
            "processes": [],
            "services": {"PCManager Service Store": {"Exists": True, "Status": "Stopped", "StartType": "Disabled"}},
            "tasks": [{"TaskPath": "\\", "TaskName": "StartCN", "State": "Disabled"}],
        }
        t3_07_valid_ok, _ = t3.t3_pw_07(g11_tasks_ctx)
        g11_tasks_ctx._cache["pwsh_snapshot"] = {
            **g11_tasks_ctx._cache["pwsh_snapshot"],
            "tasks": None,
        }
        t3_07_none_ok, t3_07_none_msg = t3.t3_pw_07(g11_tasks_ctx)
        check(
            t3_07_valid_ok and (not t3_07_none_ok) and ("tasks snapshot missing/invalid" in t3_07_none_msg),
            f"G11 t3_pw_07 flips True->False on isolated tasks=None ({t3_07_none_msg})",
        )

        proc_none_ctx = lib.ProbeContext(sb.root)
        proc_none_ctx._cache = dict(ctx._cache)
        proc_none_ctx._cache["pwsh_snapshot"] = {**dict(snap), "processes": None}
        orig_burst = t4.lib.run_live_ping_burst
        t4.lib.run_live_ping_burst = lambda target, count=30, interval_ms=100, timeout_ms=1500: {
            "ok": True,
            "target": target,
            "sent": count,
            "received": count,
            "loss_pct": 0.0,
            "packet1_ms": 2.0,
            "min_ms": 1.0,
            "avg_ms": 2.0,
            "p50_ms": 2.0,
            "p95_ms": 3.0,
            "p99_ms": 4.0,
            "max_ms": 4.0,
            "jitter_ms": 0.5,
        }
        try:
            g11_proc_results = {
                "T3-PW-14": t3.t3_pw_14(proc_none_ctx),
                "T3-PW-16": t3.t3_pw_16(proc_none_ctx),
                "T4-S01": t4.t4_s01(proc_none_ctx),
                "T4-S02": t4.t4_s02(proc_none_ctx),
                "T4-S06": t4.t4_s06(proc_none_ctx),
            }
        finally:
            t4.lib.run_live_ping_burst = orig_burst
        g11_proc_bad = [
            f"{tid}:{res}"
            for tid, res in g11_proc_results.items()
            if res[0] or "processes snapshot missing/invalid" not in res[1]
        ]
        check(
            len(g11_proc_bad) == 0,
            f"G11 isolated processes=None rejected cleanly across 5 tests ({list(g11_proc_results.keys())})"
            if not g11_proc_bad
            else f"G11 processes=None failures: {g11_proc_bad}",
        )

        # Isolated single-field None sweep across remaining processes/tasks/services consumers
        tasks_none_ctx = lib.ProbeContext(sb.root)
        tasks_none_ctx._cache = dict(ctx._cache)
        tasks_none_ctx._cache["pwsh_snapshot"] = {**dict(snap), "tasks": None}
        svcs_none_ctx = lib.ProbeContext(sb.root)
        svcs_none_ctx._cache = dict(ctx._cache)
        svcs_none_ctx._cache["pwsh_snapshot"] = {**dict(snap), "services": None}
        extra_none_checks = {
            "T1-F06-03(processes=None)": t1.t1_f06_03(proc_none_ctx),
            "T1-F06-04(processes=None)": t1.t1_f06_04(proc_none_ctx),
            "T1-F06-05(processes=None)": t1.t1_f06_05(proc_none_ctx),
            "T1-F13-05(processes=None)": t1.t1_f13_05(proc_none_ctx),
            "T2-F06-04(processes=None)": t2.t2_f06_04(proc_none_ctx),
            "T2-F13-05(processes=None)": t2.t2_f13_05(proc_none_ctx),
            "T3-PW-06(processes=None)": t3.t3_pw_06(proc_none_ctx),
            "T3-PW-07(processes=None)": t3.t3_pw_07(proc_none_ctx),
            "T1-F09-05(tasks=None)": t1.t1_f09_05(tasks_none_ctx),
            "T2-F09-05(tasks=None)": t2.t2_f09_05(tasks_none_ctx),
            "T1-F09-01(services=None)": t1.t1_f09_01(svcs_none_ctx),
            "T1-F09-02(services=None)": t1.t1_f09_02(svcs_none_ctx),
            "T1-F09-03(services=None)": t1.t1_f09_03(svcs_none_ctx),
            "T1-F09-04(services=None)": t1.t1_f09_04(svcs_none_ctx),
            "T1-F13-04(services=None)": t1.t1_f13_04(svcs_none_ctx),
            "T2-F09-01(services=None)": t2.t2_f09_01(svcs_none_ctx),
            "T2-F09-02(services=None)": t2.t2_f09_02(svcs_none_ctx),
            "T2-F09-04(services=None)": t2.t2_f09_04(svcs_none_ctx),
            "T3-PW-07(services=None)": t3.t3_pw_07(svcs_none_ctx),
            "T4-S06(services=None)": t4.t4_s06(svcs_none_ctx),
        }
        extra_none_bad = [f"{k}:{v}" for k, v in extra_none_checks.items() if v[0]]
        check(
            len(extra_none_bad) == 0,
            f"G11 isolated single-field None sweep across {len(extra_none_checks)} remaining processes/tasks/services tests passed"
            if not extra_none_bad
            else f"G11 extra None failures: {extra_none_bad}",
        )

    # 4. Execute all Tiers 1-4 in dry-inspection mode confirming deterministic PASS/FAIL and 0 ERRORs
    results = execute_cases(all_cases, ctx, verbose=verbose)
    summary = summarize_results(results)
    print_summary(summary, results, verbose=verbose)
    check(summary["ERROR"] == 0, f"Zero unhandled ERRORs across Tiers 1-4 (ERROR={summary['ERROR']})")
    check(
        summary["PASS"] + summary["FAIL"] == len(all_cases),
        f"Deterministic PASS+FAIL ({summary['PASS']}+{summary['FAIL']}) == {len(all_cases)}",
    )

    print(f"\nSelf-test result: {'PASS' if invariants_ok else 'FAIL'}")
    return 0 if invariants_ok else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Opaque-box 4-Tier E2E Test Runner")
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "all"],
        help="Test tier to execute (required unless --self-test is specified)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run runner self-checks, probe sanity, sandbox negative tests, and dry-inspection across Tiers 1-4",
    )
    parser.add_argument(
        "--project-root",
        default=str(lib.DEFAULT_PROJECT_ROOT),
        help="Target project root directory",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write machine-readable JSON results",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-test diagnostic details",
    )
    args = parser.parse_args(argv)
    if not args.self_test and not args.tier:
        parser.error("Either --tier {1,2,3,4,all} or --self-test is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root)
    if not project_root.is_dir():
        print(f"ERROR: --project-root does not exist or is not a directory: {project_root}", file=sys.stderr)
        return 2

    if args.self_test:
        return run_self_test(project_root, verbose=args.verbose)

    tiers = collect_tiers()
    selected = [1, 2, 3, 4] if args.tier == "all" else [int(args.tier)]
    cases = [tc for t in selected for tc in tiers[t]]
    ctx = lib.ProbeContext(project_root)
    results = execute_cases(cases, ctx, verbose=args.verbose)
    summary = summarize_results(results)
    print_summary(summary, results, verbose=args.verbose)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root),
            "tier": args.tier,
            "summary": summary,
            "results": [
                {
                    "id": r.id,
                    "tier": r.tier,
                    "feature": r.feature,
                    "requirement": r.requirement,
                    "name": r.name,
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                    "detail": r.detail,
                }
                for r in results
            ],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return 0 if (summary["FAIL"] == 0 and summary["ERROR"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
