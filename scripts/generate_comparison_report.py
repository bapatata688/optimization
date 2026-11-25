#!/usr/bin/env python3
"""
Generates a Markdown comparison report from baseline and post system/network benchmark JSONs.
Supports baseline-only inspection mode when post_*.json files do not yet exist.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any


SYSTEM_REQUIRED_KEYS: dict[str, type | tuple[type, ...]] = {
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

NETWORK_REQUIRED_KEYS: dict[str, type | tuple[type, ...]] = {
    "timestamp_utc": str,
    "tag": str,
    "adapter": dict,
    "gateway_ping": dict,
    "public_pings": list,
    "dns_bench": (dict, list),
    "nic_power_state": dict,
    "connectivity_ok": bool,
}


def _reject_nonfinite_json(constant: str) -> Any:
    raise ValueError(f"Non-standard JSON constant not allowed: {constant}")


def _parse_finite_float(raw: str) -> float:
    val = float(raw)
    if not math.isfinite(val):
        raise ValueError(f"Non-finite JSON float literal not allowed: {raw}")
    return val


def _ensure_finite_json(obj: Any, loc: str = "root") -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"Non-finite float not allowed at {loc}: {obj}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _ensure_finite_json(v, f"{loc}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _ensure_finite_json(v, f"{loc}[{i}]")


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(
        path.read_text(encoding="utf-8-sig"),
        parse_constant=_reject_nonfinite_json,
        parse_float=_parse_finite_float,
    )
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected JSON object, got {type(data).__name__}")
    _ensure_finite_json(data, path.name)
    return data


def validate_report_schema(
    data: dict[str, Any],
    required: dict[str, type | tuple[type, ...]],
    label: str,
) -> list[str]:
    errs: list[str] = []
    for key, expected_type in required.items():
        if key not in data:
            errs.append(f"{label}: missing required top-level key '{key}'")
        elif expected_type is bool:
            if not isinstance(data[key], bool):
                errs.append(f"{label}: key '{key}' expected bool, got {type(data[key]).__name__}")
        elif not isinstance(data[key], expected_type):
            errs.append(f"{label}: key '{key}' has invalid type {type(data[key]).__name__}")
    return errs


def fmt_val(val: Any, unit: str = "") -> str:
    if val is None:
        return "N/A"
    if isinstance(val, bool):
        return "True" if val else "False"
    if isinstance(val, list):
        return ", ".join(str(x) for x in val) if val else "(none)"
    if isinstance(val, float):
        s = f"{val:.3f}".rstrip("0").rstrip(".")
        return f"{s}{unit}"
    return f"{val}{unit}"


def fmt_wifi_meta(ad: dict[str, Any]) -> str | None:
    if not ad:
        return None
    ssid = ad.get("ssid")
    band = ad.get("band")
    channel = ad.get("channel")
    if ssid is None and band is None and channel is None:
        return None
    return " / ".join([
        str(ssid) if ssid is not None else "N/A",
        str(band) if band is not None else "N/A",
        f"ch {channel}" if channel is not None else "ch N/A",
    ])


def fmt_delta(base: Any, post: Any, direction: str, unit: str = "", baseline_only: bool = False) -> str:
    """
    direction: 'higher' (higher is better), 'lower' (lower is better), 'neutral'
    """
    if baseline_only:
        return "N/A (pending)"
    if post is None or base is None:
        return "N/A"
    if isinstance(base, bool) or isinstance(post, bool):
        return "Unchanged" if base == post else f"Changed ({base} -> {post})"
    if not isinstance(base, (int, float)) or not isinstance(post, (int, float)):
        return "Unchanged" if str(base) == str(post) else "Changed"
    if not math.isfinite(float(base)) or not math.isfinite(float(post)):
        return "N/A (non-finite)"

    diff = post - base
    pct = f" ({(diff / base) * 100.0:+.2f}%)" if base != 0 else ""
    diff_str = f"{diff:+.3f}".rstrip("0").rstrip(".")
    if diff_str in ("+0", "-0", "0"):
        diff_str = "0"
    if direction == "neutral" or diff == 0:
        tag = "Neutral"
    elif (direction == "higher" and diff > 0) or (direction == "lower" and diff < 0):
        tag = "Improved"
    else:
        tag = "Regressed"
    return f"{diff_str}{unit}{pct} [{tag}]"


def add_row(lines: list[str], metric: str, base: Any, post: Any, direction: str, unit: str = "", baseline_only: bool = False) -> None:
    b_str = fmt_val(base, unit)
    p_str = "N/A (pending)" if baseline_only else fmt_val(post, unit)
    d_str = "N/A (pending)" if baseline_only else fmt_delta(base, post, direction, unit, baseline_only=baseline_only)
    lines.append(f"| {metric} | {b_str} | {p_str} | {d_str} |")


def normalize_dns_bench(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        out = {}
        for item in raw:
            if isinstance(item, dict) and "server" in item:
                out[str(item["server"])] = item
        return out
    return {}


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Generate baseline vs post Markdown comparison report.")
    parser.add_argument("--baseline-system", default=str(repo_root / "reports" / "baseline_system.json"))
    parser.add_argument("--baseline-network", default=str(repo_root / "reports" / "baseline_network.json"))
    parser.add_argument("--post-system", default=str(repo_root / "reports" / "post_system.json"))
    parser.add_argument("--post-network", default=str(repo_root / "reports" / "post_network.json"))
    parser.add_argument("--out", default=str(repo_root / "reports" / "comparison_report.md"))
    args = parser.parse_args()

    try:
        b_sys = load_json_if_exists(Path(args.baseline_system))
        b_net = load_json_if_exists(Path(args.baseline_network))
        p_sys = load_json_if_exists(Path(args.post_system))
        p_net = load_json_if_exists(Path(args.post_network))
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: Invalid benchmark JSON input: {exc}", file=sys.stderr)
        return 1

    if b_sys is None and b_net is None and p_sys is None and p_net is None:
        print("Error: No system or network benchmark JSON files exist.", file=sys.stderr)
        return 1

    schema_errors: list[str] = []
    if b_sys is not None:
        schema_errors.extend(validate_report_schema(b_sys, SYSTEM_REQUIRED_KEYS, "baseline_system.json"))
    if p_sys is not None:
        schema_errors.extend(validate_report_schema(p_sys, SYSTEM_REQUIRED_KEYS, "post_system.json"))
    if b_net is not None:
        schema_errors.extend(validate_report_schema(b_net, NETWORK_REQUIRED_KEYS, "baseline_network.json"))
    if p_net is not None:
        schema_errors.extend(validate_report_schema(p_net, NETWORK_REQUIRED_KEYS, "post_network.json"))
    if schema_errors:
        for msg in schema_errors:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    sys_present = (b_sys is not None) or (p_sys is not None)
    net_present = (b_net is not None) or (p_net is not None)
    sys_baseline_only = (b_sys is not None) and (p_sys is None)
    net_baseline_only = (b_net is not None) and (p_net is None)
    overall_baseline_only = sys_baseline_only or net_baseline_only
    missing_baseline = (p_sys is not None and b_sys is None) or (p_net is not None and b_net is None)

    lines: list[str] = []
    lines.append("# Laptop Optimization — Before / After Comparison Report")
    lines.append("")
    lines.append(f"- **Generated (UTC):** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    if overall_baseline_only:
        lines.append("")
        lines.append("> **MODE: BASELINE-ONLY INSPECTION (`post_*.json` not yet captured)**  ")
        lines.append("> Post-optimization metrics and deltas are marked `N/A (pending)` until M2/M3 optimizations are applied and `post_system.json` / `post_network.json` are generated.")
    elif missing_baseline:
        lines.append("- **Mode:** Partial Comparison (baseline JSON missing for one or more sections)")
    else:
        lines.append("- **Mode:** Full Before / After Comparison")
    lines.append("")

    # Stability & Connectivity Summary
    lines.append("## 1. Stability & Connectivity Summary")
    lines.append("")
    lines.append("| Check | Baseline | Post | Status |")
    lines.append("|-------|----------|------|--------|")
    if sys_present:
        b_ev = (b_sys or {}).get("event_errors") or {}
        p_ev = (p_sys or {}).get("event_errors") or {}
        add_row(lines, "nvlddmkm Errors (7d)", b_ev.get("nvlddmkm_count"), p_ev.get("nvlddmkm_count"), "lower", "", sys_baseline_only)
        add_row(lines, "Kernel-Power 41 (7d)", b_ev.get("kernel_power_count"), p_ev.get("kernel_power_count"), "lower", "", sys_baseline_only)
        add_row(lines, "SCM Errors (7d)", b_ev.get("scm_error_count"), p_ev.get("scm_error_count"), "lower", "", sys_baseline_only)
    if net_present:
        b_conn = (b_net or {}).get("connectivity_ok")
        p_conn = (p_net or {}).get("connectivity_ok")
        add_row(lines, "Connectivity OK", b_conn, p_conn, "neutral", "", net_baseline_only)
    lines.append("")

    # System & GPU Benchmarks
    if sys_present:
        lines.append("## 2. System, CPU, GPU & Disk Performance")
        lines.append("")
        if b_sys is not None:
            lines.append(f"- **Baseline Timestamp:** `{b_sys.get('timestamp_utc', 'N/A')}` (Admin: `{b_sys.get('is_admin')}`)")
        else:
            lines.append("- **Baseline Timestamp:** `N/A (missing)`")
        if not sys_baseline_only and p_sys is not None:
            lines.append(f"- **Post Timestamp:** `{p_sys.get('timestamp_utc', 'N/A')}` (Admin: `{p_sys.get('is_admin')}`)")
        lines.append("")
        lines.append("| Metric | Baseline | Post | Delta / Status |")
        lines.append("|--------|----------|------|----------------|")

        b_ov = (b_sys or {}).get("system_overhead") or {}
        p_ov = (p_sys or {}).get("system_overhead") or {}
        add_row(lines, "Process Count", b_ov.get("process_count"), p_ov.get("process_count"), "lower", "", sys_baseline_only)
        add_row(lines, "Available RAM", b_ov.get("available_ram_mb"), p_ov.get("available_ram_mb"), "higher", " MB", sys_baseline_only)
        add_row(lines, "Pagefile Used", b_ov.get("pagefile_used_mb"), p_ov.get("pagefile_used_mb"), "lower", " MB", sys_baseline_only)
        add_row(lines, "Context Switches/s", b_ov.get("ctx_switches"), p_ov.get("ctx_switches"), "lower", "/s", sys_baseline_only)

        b_tm = (b_sys or {}).get("timer_jitter_ms") or {}
        p_tm = (p_sys or {}).get("timer_jitter_ms") or {}
        add_row(lines, "Timer Jitter (mean)", b_tm.get("mean"), p_tm.get("mean"), "lower", " ms", sys_baseline_only)
        add_row(lines, "Timer Jitter (p95)", b_tm.get("p95"), p_tm.get("p95"), "lower", " ms", sys_baseline_only)
        add_row(lines, "Timer Jitter (p99)", b_tm.get("p99"), p_tm.get("p99"), "lower", " ms", sys_baseline_only)
        add_row(lines, "Timer Jitter (max)", b_tm.get("max"), p_tm.get("max"), "lower", " ms", sys_baseline_only)

        b_cpu = (b_sys or {}).get("cpu_bench") or {}
        p_cpu = (p_sys or {}).get("cpu_bench") or {}
        add_row(lines, "CPU Single-Thread (gcc -O3)", b_cpu.get("single_thread_ms"), p_cpu.get("single_thread_ms"), "lower", " ms", sys_baseline_only)
        add_row(lines, "CPU Multi-Thread GEMM", b_cpu.get("multi_thread_gflops"), p_cpu.get("multi_thread_gflops"), "higher", " GFLOPS", sys_baseline_only)

        b_gpu = (b_sys or {}).get("gpu_bench") or {}
        p_gpu = (p_sys or {}).get("gpu_bench") or {}
        add_row(lines, "GPU Device", b_gpu.get("device_name"), p_gpu.get("device_name"), "neutral", "", sys_baseline_only)
        add_row(lines, "CUDA FP32 GEMM", b_gpu.get("cuda_fp32_tflops"), p_gpu.get("cuda_fp32_tflops"), "higher", " TFLOPS", sys_baseline_only)
        add_row(lines, "CUDA Pinned H2D Bandwidth", b_gpu.get("h2d_gbps"), p_gpu.get("h2d_gbps"), "higher", " GB/s", sys_baseline_only)
        add_row(lines, "CUDA Kernel Launch Latency", b_gpu.get("kernel_launch_us"), p_gpu.get("kernel_launch_us"), "lower", " us", sys_baseline_only)
        add_row(lines, "GPU Avg Graphics Clock", b_gpu.get("avg_clock_mhz"), p_gpu.get("avg_clock_mhz"), "higher", " MHz", sys_baseline_only)
        add_row(lines, "GPU Max Power Draw", b_gpu.get("max_power_w"), p_gpu.get("max_power_w"), "neutral", " W", sys_baseline_only)
        add_row(lines, "GPU Max Temp", b_gpu.get("max_temp_c"), p_gpu.get("max_temp_c"), "lower", " C", sys_baseline_only)
        add_row(lines, "GPU Throttle Reasons", b_gpu.get("throttle_reasons"), p_gpu.get("throttle_reasons"), "neutral", "", sys_baseline_only)

        b_disk = (b_sys or {}).get("disk_bench") or {}
        p_disk = (p_sys or {}).get("disk_bench") or {}
        add_row(lines, "SSD Unbuffered Seq Read", b_disk.get("seq_read_mbps"), p_disk.get("seq_read_mbps"), "higher", " MB/s", sys_baseline_only)
        add_row(lines, "SSD Unbuffered Seq Write", b_disk.get("seq_write_mbps"), p_disk.get("seq_write_mbps"), "higher", " MB/s", sys_baseline_only)
        add_row(lines, "SSD Unbuffered 4K Rand Read", b_disk.get("rand4k_read_iops"), p_disk.get("rand4k_read_iops"), "higher", " IOPS", sys_baseline_only)
        lines.append("")

        lines.append("### OS State Flags")
        lines.append("")
        lines.append("| Setting | Baseline | Post | Status |")
        lines.append("|---------|----------|------|--------|")
        b_os = (b_sys or {}).get("os_state") or {}
        p_os = (p_sys or {}).get("os_state") or {}
        add_row(lines, "Active Power Plan", b_os.get("active_power_plan"), p_os.get("active_power_plan"), "neutral", "", sys_baseline_only)
        add_row(lines, "USB Selective Suspend (AC)", b_os.get("usb_suspend_ac"), p_os.get("usb_suspend_ac"), "neutral", "", sys_baseline_only)
        add_row(lines, "GameDVR Enabled", b_os.get("gamedvr_enabled"), p_os.get("gamedvr_enabled"), "neutral", "", sys_baseline_only)
        add_row(lines, "Transparency Enabled", b_os.get("transparency_enabled"), p_os.get("transparency_enabled"), "neutral", "", sys_baseline_only)
        add_row(lines, "hiberfil.sys Exists", b_os.get("hiberfil_exists"), p_os.get("hiberfil_exists"), "neutral", "", sys_baseline_only)
        add_row(lines, "VBS Status (2=Running, 0=Off)", b_os.get("vbs_status"), p_os.get("vbs_status"), "lower", "", sys_baseline_only)
        lines.append("")

    # Network & Ping Benchmarks
    if net_present:
        lines.append("## 3. Network, Latency (Ping) & DNS Performance")
        lines.append("")
        if b_net is not None:
            lines.append(f"- **Baseline Timestamp:** `{b_net.get('timestamp_utc', 'N/A')}`")
        else:
            lines.append("- **Baseline Timestamp:** `N/A (missing)`")
        if not net_baseline_only and p_net is not None:
            lines.append(f"- **Post Timestamp:** `{p_net.get('timestamp_utc', 'N/A')}`")
        lines.append("")

        b_ad = (b_net or {}).get("adapter") or {}
        p_ad = (p_net or {}).get("adapter") or {}
        lines.append("### Adapter & NIC Power State")
        lines.append("")
        lines.append("| Property | Baseline | Post | Status |")
        lines.append("|----------|----------|------|--------|")
        add_row(lines, "Adapter Name", b_ad.get("name"), p_ad.get("name"), "neutral", "", net_baseline_only)
        add_row(lines, "Status", b_ad.get("status"), p_ad.get("status"), "neutral", "", net_baseline_only)
        add_row(lines, "Link Speed", b_ad.get("link_speed"), p_ad.get("link_speed"), "neutral", "", net_baseline_only)
        b_wifi_meta = fmt_wifi_meta(b_ad)
        p_wifi_meta = fmt_wifi_meta(p_ad) if not net_baseline_only else None
        add_row(lines, "SSID / Band / Channel", b_wifi_meta, p_wifi_meta, "neutral", "", net_baseline_only)
        add_row(lines, "RSSI", b_ad.get("rssi_dbm"), p_ad.get("rssi_dbm"), "higher", " dBm", net_baseline_only)
        add_row(lines, "DNS Servers", b_ad.get("dns_servers"), p_ad.get("dns_servers"), "neutral", "", net_baseline_only)

        b_np = (b_net or {}).get("nic_power_state") or {}
        p_np = (p_net or {}).get("nic_power_state") or {}
        add_row(lines, "NIC LpsEn", b_np.get("lps_en"), p_np.get("lps_en"), "neutral", "", net_baseline_only)
        add_row(lines, "NIC RegROAMSensitiveLevel", b_np.get("roam_level"), p_np.get("roam_level"), "neutral", "", net_baseline_only)
        add_row(lines, "MSPower_DeviceEnable", b_np.get("ms_power_device_enable"), p_np.get("ms_power_device_enable"), "neutral", "", net_baseline_only)
        add_row(lines, "PnPCapabilities", b_np.get("pnp_capabilities"), p_np.get("pnp_capabilities"), "neutral", "", net_baseline_only)
        add_row(lines, "DC Wireless Power Saving", b_np.get("dc_wireless_ps"), p_np.get("dc_wireless_ps"), "neutral", "", net_baseline_only)
        add_row(lines, "DC PCIe ASPM", b_np.get("dc_pcie_aspm"), p_np.get("dc_pcie_aspm"), "neutral", "", net_baseline_only)
        lines.append("")

        lines.append("### ICMP Latency & Jitter")
        lines.append("")
        lines.append("| Target & Metric | Baseline | Post | Delta / Status |")
        lines.append("|-----------------|----------|------|----------------|")

        b_gw = (b_net or {}).get("gateway_ping") or {}
        p_gw = (p_net or {}).get("gateway_ping") or {}
        ping_targets = [("Gateway", b_gw, p_gw)]
        b_pub_list = (b_net or {}).get("public_pings") or []
        p_pub_list = (p_net or {}).get("public_pings") or []
        b_pub_map = {str(item.get("target")): item for item in b_pub_list if isinstance(item, dict) and item.get("target") is not None}
        p_pub_map = {str(item.get("target")): item for item in p_pub_list if isinstance(item, dict) and item.get("target") is not None}
        all_pub_targets = list(b_pub_map.keys()) + [t for t in p_pub_map.keys() if t not in b_pub_map]
        for tgt in all_pub_targets:
            ping_targets.append((tgt, b_pub_map.get(tgt) or {}, p_pub_map.get(tgt) or {}))

        for label, b_p, p_p in ping_targets:
            t_name = b_p.get("target") or p_p.get("target") or label
            for stat_key, stat_label, unit in [
                ("packet1_ms", "Packet 1 (Wake)", " ms"),
                ("avg_ms", "Avg RTT", " ms"),
                ("p50_ms", "P50 RTT", " ms"),
                ("p95_ms", "P95 RTT", " ms"),
                ("p99_ms", "P99 RTT", " ms"),
                ("max_ms", "Max RTT", " ms"),
                ("jitter_ms", "Jitter", " ms"),
                ("loss_pct", "Packet Loss", "%"),
            ]:
                add_row(lines, f"{t_name} — {stat_label}", b_p.get(stat_key), p_p.get(stat_key), "lower", unit, net_baseline_only)
        lines.append("")

        lines.append("### DNS Resolution Benchmark")
        lines.append("")
        lines.append("| DNS Server & Metric | Baseline | Post | Delta / Status |")
        lines.append("|---------------------|----------|------|----------------|")
        b_dns_map = normalize_dns_bench((b_net or {}).get("dns_bench"))
        p_dns_map = normalize_dns_bench((p_net or {}).get("dns_bench"))
        all_dns_servers = list(b_dns_map.keys()) + [s for s in p_dns_map.keys() if s not in b_dns_map]
        for srv in all_dns_servers:
            b_d = b_dns_map.get(srv) or {}
            p_d = p_dns_map.get(srv) or {}
            add_row(lines, f"{srv} — Avg", b_d.get("avg_ms"), p_d.get("avg_ms"), "lower", " ms", net_baseline_only)
            add_row(lines, f"{srv} — P95", b_d.get("p95_ms"), p_d.get("p95_ms"), "lower", " ms", net_baseline_only)
            add_row(lines, f"{srv} — Loss", b_d.get("loss_pct"), p_d.get("loss_pct"), "lower", "%", net_baseline_only)
        lines.append("")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    if overall_baseline_only:
        mode_label = "baseline-only"
    elif missing_baseline:
        mode_label = "partial-comparison"
    else:
        mode_label = "full-comparison"
    print(f"[OK] Comparison report ({mode_label}) written -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
