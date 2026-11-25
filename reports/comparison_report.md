# Laptop Optimization — Before / After Comparison Report

- **Generated (UTC):** 2026-11-25T13:16:09Z
- **Mode:** Full Before / After Comparison

## 1. Stability & Connectivity Summary

| Check | Baseline | Post | Status |
|-------|----------|------|--------|
| nvlddmkm Errors (7d) | 5 | 5 | 0 (+0.00%) [Neutral] |
| Kernel-Power 41 (7d) | 10 | 16 | +6 (+60.00%) [Regressed] |
| SCM Errors (7d) | 24 | 33 | +9 (+37.50%) [Regressed] |
| Connectivity OK | True | True | Unchanged |

## 2. System, CPU, GPU & Disk Performance

- **Baseline Timestamp:** `2026-11-21T16:55:36Z` (Admin: `False`)
- **Post Timestamp:** `2026-11-25T13:15:14Z` (Admin: `False`)

| Metric | Baseline | Post | Delta / Status |
|--------|----------|------|----------------|
| Process Count | 236 | 202 | -34 (-14.41%) [Improved] |
| Available RAM | 7442.14 MB | 12567.35 MB | +5125.21 MB (+68.87%) [Improved] |
| Pagefile Used | 538.43 MB | 495.42 MB | -43.01 MB (-7.99%) [Improved] |
| Context Switches/s | 76340/s | 5574/s | -70766/s (-92.70%) [Improved] |
| Timer Jitter (mean) | 0.527 ms | 0.585 ms | +0.057 ms (+10.90%) [Regressed] |
| Timer Jitter (p95) | 0.67 ms | 0.893 ms | +0.223 ms (+33.18%) [Regressed] |
| Timer Jitter (p99) | 1.102 ms | 1.087 ms | -0.015 ms (-1.39%) [Improved] |
| Timer Jitter (max) | 7.894 ms | 1.387 ms | -6.508 ms (-82.44%) [Improved] |
| CPU Single-Thread (gcc -O3) | 113.979 ms | 123.704 ms | +9.725 ms (+8.53%) [Regressed] |
| CPU Multi-Thread GEMM | 267.84 GFLOPS | 393.94 GFLOPS | +126.1 GFLOPS (+47.08%) [Improved] |
| GPU Device | NVIDIA GeForce GTX 1650 | NVIDIA GeForce GTX 1650 | Unchanged |
| CUDA FP32 GEMM | 1.354 TFLOPS | 1.856 TFLOPS | +0.502 TFLOPS (+37.08%) [Improved] |
| CUDA Pinned H2D Bandwidth | 6.329 GB/s | 6.639 GB/s | +0.31 GB/s (+4.90%) [Improved] |
| CUDA Kernel Launch Latency | 15.738 us | 17.748 us | +2.01 us (+12.77%) [Regressed] |
| GPU Avg Graphics Clock | 1785 MHz | 1785 MHz | 0 MHz (+0.00%) [Neutral] |
| GPU Max Power Draw | 65.05 W | 63.81 W | -1.24 W (-1.91%) [Neutral] |
| GPU Max Temp | 68.7 C | 46.9 C | -21.8 C (-31.73%) [Improved] |
| GPU Throttle Reasons | none (0x0) | none (0x0) | Unchanged |
| SSD Unbuffered Seq Read | 87.43 MB/s | 271.44 MB/s | +184.01 MB/s (+210.47%) [Improved] |
| SSD Unbuffered Seq Write | 287.09 MB/s | 227.94 MB/s | -59.15 MB/s (-20.60%) [Regressed] |
| SSD Unbuffered 4K Rand Read | 2537.9 IOPS | 6223.8 IOPS | +3685.9 IOPS (+145.23%) [Improved] |

### OS State Flags

| Setting | Baseline | Post | Status |
|---------|----------|------|--------|
| Active Power Plan | 8ac65e32-075f-48d5-828b-02e93ad3c1f8 (Ultimate Performance) | 46476e86-f770-4378-857e-3a25bf0336b4 (Gaming Estable (Sin Bateria)) | Changed |
| USB Selective Suspend (AC) | False | False | Unchanged |
| GameDVR Enabled | True | False | Changed (True -> False) |
| Transparency Enabled | True | False | Changed (True -> False) |
| hiberfil.sys Exists | True | False | Changed (True -> False) |
| VBS Status (2=Running, 0=Off) | 2 | 2 | 0 (+0.00%) [Neutral] |

## 3. Network, Latency (Ping) & DNS Performance

- **Baseline Timestamp:** `2026-11-21T16:56:40Z`
- **Post Timestamp:** `2026-11-25T13:16:08Z`

### Adapter & NIC Power State

| Property | Baseline | Post | Status |
|----------|----------|------|--------|
| Adapter Name | Wi-Fi | Wi-Fi | Unchanged |
| Status | Up | Up | Unchanged |
| Link Speed | 866.7 Mbps | 866.7 Mbps | Unchanged |
| SSID / Band / Channel | CLARO2_F3E3B9 / 5 GHz / ch 36 | CLARO2_F3E3B9 / 5 GHz / ch 36 | Unchanged |
| RSSI | -67 dBm | -59 dBm | +8 dBm (-11.94%) [Improved] |
| DNS Servers | 1.1.1.1, 1.0.0.1 | 1.1.1.1, 1.0.0.1 | Unchanged |
| NIC LpsEn | 2 | 0 | -2 (-100.00%) [Neutral] |
| NIC RegROAMSensitiveLevel | 80 | 127 | +47 (+58.75%) [Neutral] |
| MSPower_DeviceEnable | True | False | Changed (True -> False) |
| PnPCapabilities | 16 | 24 | +8 (+50.00%) [Neutral] |
| DC Wireless Power Saving | 2 | 0 | -2 (-100.00%) [Neutral] |
| DC PCIe ASPM | 2 | 0 | -2 (-100.00%) [Neutral] |

### ICMP Latency & Jitter

| Target & Metric | Baseline | Post | Delta / Status |
|-----------------|----------|------|----------------|
| 192.168.1.1 — Packet 1 (Wake) | 14.956 ms | 30.829 ms | +15.873 ms (+106.13%) [Regressed] |
| 192.168.1.1 — Avg RTT | 3.065 ms | 2.828 ms | -0.237 ms (-7.73%) [Improved] |
| 192.168.1.1 — P50 RTT | 2.406 ms | 2.232 ms | -0.174 ms (-7.23%) [Improved] |
| 192.168.1.1 — P95 RTT | 5.718 ms | 2.673 ms | -3.045 ms (-53.25%) [Improved] |
| 192.168.1.1 — P99 RTT | 12.83 ms | 18.851 ms | +6.021 ms (+46.93%) [Regressed] |
| 192.168.1.1 — Max RTT | 14.956 ms | 30.829 ms | +15.873 ms (+106.13%) [Regressed] |
| 192.168.1.1 — Jitter | 1.112 ms | 1.012 ms | -0.1 ms (-8.99%) [Improved] |
| 192.168.1.1 — Packet Loss | 0% | 0% | 0% [Neutral] |
| 9.9.9.9 — Packet 1 (Wake) | 40.467 ms | 39.711 ms | -0.756 ms (-1.87%) [Improved] |
| 9.9.9.9 — Avg RTT | 42.292 ms | 40.329 ms | -1.963 ms (-4.64%) [Improved] |
| 9.9.9.9 — P50 RTT | 41.525 ms | 39.252 ms | -2.273 ms (-5.47%) [Improved] |
| 9.9.9.9 — P95 RTT | 48.195 ms | 42.587 ms | -5.608 ms (-11.64%) [Improved] |
| 9.9.9.9 — P99 RTT | 53.951 ms | 62.714 ms | +8.763 ms (+16.24%) [Regressed] |
| 9.9.9.9 — Max RTT | 57.851 ms | 80.553 ms | +22.702 ms (+39.24%) [Regressed] |
| 9.9.9.9 — Jitter | 2.724 ms | 2.728 ms | +0.004 ms (+0.15%) [Regressed] |
| 9.9.9.9 — Packet Loss | 0% | 0% | 0% [Neutral] |
| 8.8.8.8 — Packet 1 (Wake) | 46.438 ms | 38.554 ms | -7.884 ms (-16.98%) [Improved] |
| 8.8.8.8 — Avg RTT | 47.122 ms | 38.751 ms | -8.371 ms (-17.76%) [Improved] |
| 8.8.8.8 — P50 RTT | 46.646 ms | 38.344 ms | -8.302 ms (-17.80%) [Improved] |
| 8.8.8.8 — P95 RTT | 49.358 ms | 40.001 ms | -9.357 ms (-18.96%) [Improved] |
| 8.8.8.8 — P99 RTT | 56.246 ms | 45.398 ms | -10.848 ms (-19.29%) [Improved] |
| 8.8.8.8 — Max RTT | 59.11 ms | 47.606 ms | -11.504 ms (-19.46%) [Improved] |
| 8.8.8.8 — Jitter | 1.693 ms | 1.354 ms | -0.339 ms (-20.02%) [Improved] |
| 8.8.8.8 — Packet Loss | 0% | 0% | 0% [Neutral] |
| 1.1.1.1 — Packet 1 (Wake) | 67.909 ms | 39.725 ms | -28.184 ms (-41.50%) [Improved] |
| 1.1.1.1 — Avg RTT | 66.568 ms | 40.593 ms | -25.975 ms (-39.02%) [Improved] |
| 1.1.1.1 — P50 RTT | 66.424 ms | 40.166 ms | -26.258 ms (-39.53%) [Improved] |
| 1.1.1.1 — P95 RTT | 68.258 ms | 44.633 ms | -23.625 ms (-34.61%) [Improved] |
| 1.1.1.1 — P99 RTT | 72.133 ms | 47.49 ms | -24.643 ms (-34.16%) [Improved] |
| 1.1.1.1 — Max RTT | 73.292 ms | 48.243 ms | -25.049 ms (-34.18%) [Improved] |
| 1.1.1.1 — Jitter | 1.582 ms | 1.499 ms | -0.083 ms (-5.25%) [Improved] |
| 1.1.1.1 — Packet Loss | 0% | 0% | 0% [Neutral] |

### DNS Resolution Benchmark

| DNS Server & Metric | Baseline | Post | Delta / Status |
|---------------------|----------|------|----------------|
| 9.9.9.9 — Avg | 45.975 ms | 42.766 ms | -3.209 ms (-6.98%) [Improved] |
| 9.9.9.9 — P95 | 69.084 ms | 48.252 ms | -20.832 ms (-30.15%) [Improved] |
| 9.9.9.9 — Loss | 0% | 0% | 0% [Neutral] |
| 8.8.8.8 — Avg | 51.757 ms | 53.863 ms | +2.106 ms (+4.07%) [Regressed] |
| 8.8.8.8 — P95 | 57.271 ms | 64.115 ms | +6.844 ms (+11.95%) [Regressed] |
| 8.8.8.8 — Loss | 0% | 0% | 0% [Neutral] |
| 1.1.1.1 — Avg | 69.471 ms | 42.846 ms | -26.625 ms (-38.33%) [Improved] |
| 1.1.1.1 — P95 | 71.296 ms | 47.472 ms | -23.824 ms (-33.42%) [Improved] |
| 1.1.1.1 — Loss | 0% | 0% | 0% [Neutral] |
| 1.0.0.1 — Avg | 69.459 ms | 41.442 ms | -28.017 ms (-40.34%) [Improved] |
| 1.0.0.1 — P95 | 72.081 ms | 42.711 ms | -29.37 ms (-40.75%) [Improved] |
| 1.0.0.1 — Loss | 0% | 0% | 0% [Neutral] |
| 216.230.147.90 — Avg | 38.362 ms | 19.798 ms | -18.564 ms (-48.39%) [Improved] |
| 216.230.147.90 — P95 | 113.612 ms | 43.771 ms | -69.841 ms (-61.47%) [Improved] |
| 216.230.147.90 — Loss | 0% | 0% | 0% [Neutral] |
| 216.230.139.8 — Avg | 18.594 ms | 12.924 ms | -5.67 ms (-30.49%) [Improved] |
| 216.230.139.8 — P95 | 53.181 ms | 27.385 ms | -25.796 ms (-48.51%) [Improved] |
| 216.230.139.8 — Loss | 0% | 0% | 0% [Neutral] |
