#!/usr/bin/env python3
"""
Reproducible system, CPU, timer, GPU (CUDA + nvidia-smi), unbuffered disk I/O,
OS state, and Event Log benchmark suite for laptop_optimization.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import threading
import time
import winreg
import xml.etree.ElementTree as ET

import numpy as np
import psutil
import torch


C_BENCH_SOURCE = r"""
#include <stdio.h>
#include <math.h>
#include <windows.h>

static double run_kernel(int n) {
    double acc = 1.000123;
    double step = 0.9999997;
    for (int i = 1; i <= n; ++i) {
        acc = acc * step + sin((double)(i & 1023) * 0.001) * 0.0001 + 1.0e-7;
        if (acc > 1.0e6) {
            acc *= 1.0e-6;
        }
    }
    return acc;
}

int main(void) {
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);

    /* Warmup */
    volatile double sink = run_kernel(2000000);

    const int reps = 5;
    const int iters = 25000000;
    double total_ms = 0.0;
    for (int r = 0; r < reps; ++r) {
        QueryPerformanceCounter(&t0);
        sink += run_kernel(iters);
        QueryPerformanceCounter(&t1);
        total_ms += ((double)(t1.QuadPart - t0.QuadPart) * 1000.0) / (double)freq.QuadPart;
    }
    printf("MS=%.4f CHECKSUM=%.9f\n", total_ms / (double)reps, sink);
    return 0;
}
"""


class SYSTEM_INFO(ctypes.Structure):
    _fields_ = [
        ("wProcessorArchitecture", wintypes.WORD),
        ("wReserved", wintypes.WORD),
        ("dwPageSize", wintypes.DWORD),
        ("lpMinimumApplicationAddress", ctypes.c_void_p),
        ("lpMaximumApplicationAddress", ctypes.c_void_p),
        ("dwActiveProcessorMask", ctypes.c_size_t),
        ("dwNumberOfProcessors", wintypes.DWORD),
        ("dwProcessorType", wintypes.DWORD),
        ("dwAllocationGranularity", wintypes.DWORD),
        ("wProcessorLevel", wintypes.WORD),
        ("wProcessorRevision", wintypes.WORD),
    ]


class ENUM_PAGE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
        ("TotalSize", ctypes.c_size_t),
        ("TotalInUse", ctypes.c_size_t),
        ("PeakUsage", ctypes.c_size_t),
    ]


def get_pagefile_used_mb() -> float:
    """Query used pagefile via Win32 psapi.EnumPageFilesW (avoids broken PDH counters)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    sys_info = SYSTEM_INFO()
    kernel32.GetSystemInfo(ctypes.byref(sys_info))
    page_size = int(sys_info.dwPageSize) or 4096

    total_in_use_pages = 0

    CALLBACK = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.LPVOID,
        ctypes.POINTER(ENUM_PAGE_FILE_INFORMATION),
        wintypes.LPCWSTR,
    )

    @CALLBACK
    def _cb(_ctx, p_info, _filename):
        nonlocal total_in_use_pages
        if p_info:
            total_in_use_pages += int(p_info.contents.TotalInUse)
        return True

    psapi.EnumPageFilesW.argtypes = [CALLBACK, wintypes.LPVOID]
    psapi.EnumPageFilesW.restype = wintypes.BOOL
    ok = psapi.EnumPageFilesW(_cb, None)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    return round((total_in_use_pages * page_size) / (1024.0 * 1024.0), 2)


def measure_system_overhead() -> dict:
    proc_count = len(psutil.pids())
    avail_ram_mb = round(psutil.virtual_memory().available / (1024.0 * 1024.0), 2)
    pagefile_used_mb = get_pagefile_used_mb()

    cs0 = psutil.cpu_stats().ctx_switches
    time.sleep(2.0)
    cs1 = psutil.cpu_stats().ctx_switches
    ctx_switches_per_sec = int(round((cs1 - cs0) / 2.0))

    return {
        "process_count": int(proc_count),
        "available_ram_mb": float(avail_ram_mb),
        "pagefile_used_mb": float(pagefile_used_mb),
        "ctx_switches": ctx_switches_per_sec,
        "ctx_switches_total": int(cs1),
    }


def measure_timer_jitter_ms(samples: int = 500) -> dict:
    jitters = []
    # Warmup
    for _ in range(10):
        time.sleep(0.001)

    for _ in range(samples):
        t0 = time.perf_counter_ns()
        time.sleep(0.001)
        t1 = time.perf_counter_ns()
        dt_ms = (t1 - t0) / 1_000_000.0
        jitters.append(abs(dt_ms - 1.0))

    arr = np.array(jitters, dtype=np.float64)
    return {
        "mean": round(float(np.mean(arr)), 4),
        "p95": round(float(np.percentile(arr, 95)), 4),
        "p99": round(float(np.percentile(arr, 99)), 4),
        "max": round(float(np.max(arr)), 4),
    }


def measure_cpu_bench(scratch_dir: Path) -> dict:
    pid = os.getpid()
    c_path = scratch_dir / f"_tmp_cpu_bench_{pid}.c"
    exe_path = scratch_dir / f"_tmp_cpu_bench_{pid}.exe"

    single_thread_ms = None
    try:
        c_path.write_text(C_BENCH_SOURCE, encoding="utf-8")
        compile_res = subprocess.run(
            ["gcc", "-O3", str(c_path), "-o", str(exe_path), "-lm"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if compile_res.returncode == 0 and exe_path.exists():
            run_res = subprocess.run(
                [str(exe_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if run_res.returncode == 0:
                m = re.search(r"MS=([0-9]+(?:\.[0-9]+)?)", run_res.stdout)
                if m:
                    single_thread_ms = round(float(m.group(1)), 3)
    finally:
        for p in (exe_path, c_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    if single_thread_ms is None:
        raise RuntimeError("gcc single-thread CPU benchmark failed to compile or execute")

    # Multi-thread NumPy FP32 GEMM
    n = 2048
    rng = np.random.default_rng(42)
    a = rng.standard_normal((n, n), dtype=np.float32)
    b = rng.standard_normal((n, n), dtype=np.float32)

    for _ in range(2):
        _ = a @ b

    dts = []
    for _ in range(8):
        t0 = time.perf_counter()
        _ = a @ b
        t1 = time.perf_counter()
        dts.append(t1 - t0)

    mean_dt = sum(dts) / len(dts)
    flops = 2.0 * (n ** 3)
    multi_thread_gflops = round((flops / mean_dt) / 1e9, 2)

    return {
        "single_thread_ms": float(single_thread_ms),
        "multi_thread_gflops": float(multi_thread_gflops),
    }


def _decode_throttle_reasons(masks: list[int]) -> str:
    # Filter out bit 0 (GpuIdle) when active load masks exist
    non_idle_masks = [m & ~0x1 for m in masks if (m & ~0x1) != 0]
    if not non_idle_masks:
        return "none (0x0)"
    combined = 0
    for m in non_idle_masks:
        combined |= m
    bit_names = {
        0x0000000000000002: "applications_clocks_setting",
        0x0000000000000004: "sw_power_cap",
        0x0000000000000008: "hw_slowdown",
        0x0000000000000010: "sync_boost",
        0x0000000000000020: "sw_thermal_slowdown",
        0x0000000000000040: "hw_thermal_slowdown",
        0x0000000000000080: "hw_power_brake_slowdown",
        0x0000000000000100: "display_clock_setting",
    }
    labels = [name for bit, name in bit_names.items() if (combined & bit) != 0]
    if labels:
        return f"0x{combined:x} ({', '.join(labels)})"
    return f"0x{combined:x}"


def measure_gpu_bench() -> dict:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available in PyTorch")

    device_name = str(torch.cuda.get_device_name(0))
    device = torch.device("cuda:0")

    n = 4096
    a = torch.randn((n, n), dtype=torch.float32, device=device)
    b = torch.randn((n, n), dtype=torch.float32, device=device)

    for _ in range(3):
        _ = torch.matmul(a, b)
    torch.cuda.synchronize()

    clocks = []
    powers = []
    temps = []
    masks = []
    stop_event = threading.Event()

    def _poll_nvidia_smi():
        while not stop_event.is_set():
            try:
                res = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=clocks.gr,power.draw,temperature.gpu,clocks_event_reasons.active",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0 and res.stdout.strip():
                    line = res.stdout.strip().splitlines()[0]
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        try:
                            clocks.append(float(parts[0]))
                        except ValueError:
                            pass
                        try:
                            powers.append(float(parts[1]))
                        except ValueError:
                            pass
                        try:
                            temps.append(float(parts[2]))
                        except ValueError:
                            pass
                        try:
                            masks.append(int(parts[3], 16))
                        except ValueError:
                            pass
            except Exception:
                pass
            stop_event.wait(0.1)

    sampler = threading.Thread(target=_poll_nvidia_smi, daemon=True)
    sampler.start()
    try:
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        iters = 15
        for _ in range(iters):
            _ = torch.matmul(a, b)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
    finally:
        stop_event.set()
        sampler.join(timeout=3.0)

    mean_dt = (t1 - t0) / iters
    cuda_fp32_tflops = round(((2.0 * (n ** 3)) / mean_dt) / 1e12, 3)

    # Pinned Host-to-Device transfer benchmark (128 MiB)
    h_tensor = torch.empty((8192, 4096), dtype=torch.float32, pin_memory=True)
    h_tensor.fill_(1.0)
    byte_size = h_tensor.numel() * h_tensor.element_size()

    for _ in range(2):
        _ = h_tensor.to(device, non_blocking=False)
    torch.cuda.synchronize()

    h2d_dts = []
    for _ in range(10):
        t0 = time.perf_counter()
        _ = h_tensor.to(device, non_blocking=False)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
        h2d_dts.append(t1 - t0)
    h2d_gbps = round((byte_size / (sum(h2d_dts) / len(h2d_dts))) / 1e9, 3)

    # Tiny kernel launch latency (us)
    tiny = torch.ones((1, 1), dtype=torch.float32, device=device)
    for _ in range(50):
        tiny.add_(1.0)
    torch.cuda.synchronize()

    launches = 1000
    t0 = time.perf_counter()
    for _ in range(launches):
        tiny.add_(1.0)
    torch.cuda.synchronize()
    t1 = time.perf_counter()
    kernel_launch_us = round(((t1 - t0) * 1e6) / launches, 3)

    del a, b, h_tensor, tiny
    torch.cuda.empty_cache()

    avg_clock_mhz = round(sum(clocks) / len(clocks), 1) if clocks else 0.0
    max_power_w = round(max(powers), 2) if powers else 0.0
    max_temp_c = round(max(temps), 1) if temps else 0.0
    throttle_reasons = _decode_throttle_reasons(masks) if masks else "unknown"

    return {
        "device_name": device_name,
        "cuda_fp32_tflops": float(cuda_fp32_tflops),
        "h2d_gbps": float(h2d_gbps),
        "kernel_launch_us": float(kernel_launch_us),
        "avg_clock_mhz": float(avg_clock_mhz),
        "max_power_w": float(max_power_w),
        "max_temp_c": float(max_temp_c),
        "throttle_reasons": throttle_reasons,
    }


def measure_disk_bench(scratch_dir: Path) -> dict:
    """Unbuffered Win32 CreateFileW + VirtualAlloc disk benchmark."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    CREATE_ALWAYS = 2
    OPEN_EXISTING = 3
    FILE_FLAG_NO_BUFFERING = 0x20000000
    FILE_FLAG_WRITE_THROUGH = 0x80000000
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    FILE_FLAG_RANDOM_ACCESS = 0x10000000
    FILE_BEGIN = 0
    MEM_COMMIT = 0x1000
    MEM_RESERVE = 0x2000
    MEM_RELEASE = 0x8000
    PAGE_READWRITE = 0x04

    invalid_handle = ctypes.c_void_p(-1).value

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p

    kernel32.WriteFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL

    kernel32.ReadFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL

    kernel32.SetFilePointerEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int64,
        ctypes.POINTER(ctypes.c_int64),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.VirtualAlloc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualAlloc.restype = ctypes.c_void_p

    kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
    kernel32.VirtualFree.restype = wintypes.BOOL

    block_size = 1024 * 1024  # 1 MiB
    num_blocks = 128          # 128 MiB total
    total_bytes = block_size * num_blocks

    tmp_file = scratch_dir / f"_tmp_disk_bench_{os.getpid()}.bin"
    buf = kernel32.VirtualAlloc(None, block_size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not buf:
        raise ctypes.WinError(ctypes.get_last_error())

    # Fill buffer with deterministic non-zero pattern
    ctypes.memset(buf, 0xA5, block_size)

    try:
        # 1. Sequential Unbuffered Write
        handle = kernel32.CreateFileW(
            str(tmp_file),
            GENERIC_WRITE,
            0,
            None,
            CREATE_ALWAYS,
            FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH,
            None,
        )
        if handle == invalid_handle or not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            bytes_written = wintypes.DWORD(0)
            t0 = time.perf_counter()
            for _ in range(num_blocks):
                ok = kernel32.WriteFile(handle, buf, block_size, ctypes.byref(bytes_written), None)
                if not ok or bytes_written.value != block_size:
                    raise ctypes.WinError(ctypes.get_last_error())
            t1 = time.perf_counter()
            seq_write_mbps = round((total_bytes / (1024.0 * 1024.0)) / (t1 - t0), 2)
        finally:
            kernel32.CloseHandle(handle)

        # 2. Sequential Unbuffered Read
        handle = kernel32.CreateFileW(
            str(tmp_file),
            GENERIC_READ,
            0,
            None,
            OPEN_EXISTING,
            FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN,
            None,
        )
        if handle == invalid_handle or not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            bytes_read = wintypes.DWORD(0)
            t0 = time.perf_counter()
            for _ in range(num_blocks):
                ok = kernel32.ReadFile(handle, buf, block_size, ctypes.byref(bytes_read), None)
                if not ok or bytes_read.value != block_size:
                    raise ctypes.WinError(ctypes.get_last_error())
            t1 = time.perf_counter()
            seq_read_mbps = round((total_bytes / (1024.0 * 1024.0)) / (t1 - t0), 2)
        finally:
            kernel32.CloseHandle(handle)

        # 3. Random 4K Unbuffered Read IOPS
        handle = kernel32.CreateFileW(
            str(tmp_file),
            GENERIC_READ,
            0,
            None,
            OPEN_EXISTING,
            FILE_FLAG_NO_BUFFERING | FILE_FLAG_RANDOM_ACCESS,
            None,
        )
        if handle == invalid_handle or not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            page_4k = 4096
            max_page_idx = (total_bytes // page_4k) - 1
            rng = random.Random(42)
            offsets = [rng.randint(0, max_page_idx) * page_4k for _ in range(2000)]
            new_pos = ctypes.c_int64(0)
            bytes_read = wintypes.DWORD(0)

            t0 = time.perf_counter()
            for off in offsets:
                if not kernel32.SetFilePointerEx(handle, off, ctypes.byref(new_pos), FILE_BEGIN):
                    raise ctypes.WinError(ctypes.get_last_error())
                ok = kernel32.ReadFile(handle, buf, page_4k, ctypes.byref(bytes_read), None)
                if not ok or bytes_read.value != page_4k:
                    raise ctypes.WinError(ctypes.get_last_error())
            t1 = time.perf_counter()
            rand4k_read_iops = round(len(offsets) / (t1 - t0), 1)
        finally:
            kernel32.CloseHandle(handle)

        return {
            "seq_read_mbps": float(seq_read_mbps),
            "seq_write_mbps": float(seq_write_mbps),
            "rand4k_read_iops": float(rand4k_read_iops),
        }
    finally:
        kernel32.VirtualFree(buf, 0, MEM_RELEASE)
        try:
            if tmp_file.exists():
                tmp_file.unlink()
        except OSError:
            pass


def _read_reg_dword(root, subkey: str, value_name: str):
    try:
        with winreg.OpenKey(root, subkey) as k:
            val, _ = winreg.QueryValueEx(k, value_name)
            return int(val)
    except OSError:
        return None


def measure_os_state() -> dict:
    scheme_res = subprocess.run(
        ["powercfg", "/getactivescheme"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    active_guid = None
    active_plan = "unknown"
    m = re.search(
        r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\s*\((.+)\)\s*$",
        scheme_res.stdout,
    )
    if m:
        active_guid = m.group(1).lower()
        active_plan = f"{active_guid} ({m.group(2).strip()})"

    usb_suspend_ac = None
    if active_guid:
        q_res = subprocess.run(
            [
                "powercfg",
                "/q",
                active_guid,
                "2a737441-1930-4402-8d77-b2bebba308a3",
                "48e6b7a6-50f5-4782-a5d4-53bb8f07e226",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if q_res.returncode == 0:
            hex_matches = re.findall(r"0x([0-9a-fA-F]+)", q_res.stdout)
            if hex_matches:
                usb_suspend_ac = int(hex_matches[0], 16) != 0

    gamedvr_val = _read_reg_dword(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
    trans_val = _read_reg_dword(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        "EnableTransparency",
    )

    hiberfil_exists = os.path.exists(r"C:\hiberfil.sys")

    vbs_status = None
    try:
        cim_res = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard -ClassName Win32_DeviceGuard).VirtualizationBasedSecurityStatus",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if cim_res.returncode == 0 and cim_res.stdout.strip():
            vbs_status = int(cim_res.stdout.strip().splitlines()[0])
    except Exception:
        vbs_status = None

    return {
        "active_power_plan": active_plan,
        "usb_suspend_ac": usb_suspend_ac,
        "gamedvr_enabled": (gamedvr_val == 1) if gamedvr_val is not None else None,
        "transparency_enabled": (trans_val == 1) if trans_val is not None else None,
        "hiberfil_exists": bool(hiberfil_exists),
        "vbs_status": vbs_status,
    }


def _count_events_xpath(xpath: str) -> int:
    res = subprocess.run(
        ["wevtutil", "qe", "System", f"/q:{xpath}", "/f:xml", "/e:Events"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    if res.returncode != 0:
        raise RuntimeError(f"wevtutil failed ({res.returncode}): {res.stderr.strip()}")
    xml_text = res.stdout.strip()
    if not xml_text:
        return 0
    root = ET.fromstring(xml_text)
    return sum(1 for elem in root if elem.tag.endswith("Event"))


def measure_event_errors(window_ms: int = 604800000) -> dict:
    nv_q = f"*[System[Provider[@Name='nvlddmkm'] and (Level=1 or Level=2 or Level=3) and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    kp_q = f"*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41 and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    scm_q = f"*[System[Provider[@Name='Service Control Manager'] and (Level=1 or Level=2) and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"

    return {
        "nvlddmkm_count": _count_events_xpath(nv_q),
        "kernel_power_count": _count_events_xpath(kp_q),
        "scm_error_count": _count_events_xpath(scm_q),
        "window_hours": int(window_ms // 3600000),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reproducible system, CPU, GPU, disk, and stability benchmark.")
    parser.add_argument("--tag", required=True, choices=["baseline", "post"], help="Benchmark tag.")
    parser.add_argument("--out", default=None, help="Output JSON path (default: reports/<tag>_system.json).")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_path = Path(args.out) if args.out else (repo_root / "reports" / f"{args.tag}_system.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir = out_path.parent

    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": args.tag,
        "is_admin": is_admin,
        "system_overhead": measure_system_overhead(),
        "timer_jitter_ms": measure_timer_jitter_ms(),
        "cpu_bench": measure_cpu_bench(scratch_dir),
        "gpu_bench": measure_gpu_bench(),
        "disk_bench": measure_disk_bench(scratch_dir),
        "os_state": measure_os_state(),
        "event_errors": measure_event_errors(),
    }

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[OK] System benchmark ({args.tag}) saved -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
