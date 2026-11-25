# Reporte de Verificación y Optimización — Laptop Gamer (`LENOVO 82K2` IdeaPad Gaming 3)

- **Fecha de verificación (UTC):** `2026-11-25T13:16:30Z`
- **Equipo:** Lenovo IdeaPad Gaming 3 15ACH6 (`82K2`), AMD Ryzen 5 5600H, 24 GB DDR4-3200, Kingston 960 GB SATA SSD, AMD Radeon Graphics + NVIDIA GeForce GTX 1650 (75W), Realtek RTL8852AE Wi-Fi 6 + Realtek PCIe GbE, Windows 11 Home 25H2 (`10.0.26200`)
- **Carpeta de trabajo:** `c:\Users\Usuario\teamwork_projects\laptop_optimization`

---

## 1. Resumen de Optimizaciones Aplicadas (M1–M3)

### M1 — Respaldos, Benchmarks y Reversibilidad (`F1–F4`)
- **Respaldos completos antes de cambios (`backups/`):**
  - `backups/system/`: exportaciones `.reg` de `HKCU` y `HKLM` (incluyendo `hkcu_nvspcaps_before.reg` para `ShadowPlay\NVSPCAPS`), `absent_keys.json`, `services_baseline.json`, `tasks_baseline.json`, `startup_baseline.json`, `os_state_before.json` y `restore_point_info.json`.
  - `backups/power/`: exportación de los 4 planes de energía (`power_scheme_*.pow`) y estado activo previo (`power_active_before.txt`).
  - `backups/network/`: `nic_advanced_before.csv`, `dns_before.json`, `tcp_global_before.txt`, `wlan_profiles_before.txt` y ramas `.reg` de controladores Wi-Fi, Ethernet, Tcpip, MMCSS, Psched y Delivery Optimization.
  - **Integridad de línea base:** `reports/baseline_system.json` (`2026-11-21T16:55:50Z`) y `reports/baseline_network.json` (`2026-11-21T16:56:40Z`) permanecen intactos.
- **Reversibilidad y utilidades (`scripts/`):**
  - `scripts/rollback_all.ps1` restaura todas las ramas `.reg` (incluyendo `hkcu_*.reg`), planes `.pow`, hibernación, DNS, propiedades NIC, perfiles WLAN, TCP RSC, servicios y tareas programadas.
  - `scripts/gaming_wlan_scan_toggle.ps1` (`-Mode gaming|restore|status`) permite desactivar temporalmente el escaneo en segundo plano `autoconfig` durante sesiones competitivas.
  - `scripts/toggle_vbs_hypervisor.ps1` (`-Mode gaming|wsl_docker`) permite alternar VBS/HVCI/Hyper-V con o sin `-KeepHypervisor`.

### M2 — Sistema Operativo, Energía, GPU, Inicio, Servicios y VBS/HVCI (`F5–F10`)
- **Hooks de usuario (`F5`):** `GameDVR_Enabled=0`, `AppCaptureEnabled=0`, `GameDVR_FSEBehaviorMode=2`, `EnableTransparency=0`, `MinAnimate="0"`, `VisualFXSetting=2`, `FIFA22.exe` con `GpuPreference=2;` (Alto rendimiento).
- **Reducción de bloat de inicio y overlays (`F6`):** Deshabilitados en `StartupApproved\Run` (`EpicGamesLauncher`, `CurseForge`, `Steam`, `Docker Desktop`, `AMDNoiseSuppression`, `OneDriveSetup`, `nefarius_HidHide_Updater`, `MicrosoftEdgeAutoLaunch_*`, `Free Download Manager`, `utweb`). Overlays detenidos y persistentes fuera de sesión (`NVIDIA Overlay` deshabilitado en usuario vía `HKCU\...\ShadowPlay\NVSPCAPS` tras respaldo en `hkcu_nvspcaps_before.reg`; tras confirmar Steam cerrado/sin descargas y re-ejecutar `apply_os_gpu_user.ps1` + `apply_network_user.ps1` en la sesión limpia actual `2026-11-25T13:14:47Z`–`13:14:48Z`, se verificó tras 5 s `0/10` procesos activos objetivo [`steam`, `steamwebhelper`, `GameBar`, `GameBarFTServer`, `NVIDIA Overlay`, `nvsphelper64`, `fdm`, `utweb`, `qbittorrent`, `Transmission`], además de `0` procesos de `RadeonSoftware`/`AMDRSSrcExt`/`utorrent`, con tráfico `Wi-Fi` en reposo `RxKBps = 1.95 KB/s`).
- **Energía e hibernación (`F7`):** Hibernación deshabilitada (`powercfg /h off`, `C:\hiberfil.sys` eliminado), `HiberbootEnabled=0`, `PowerThrottlingOff=1`, `Win32PrioritySeparation=0x26`, suspensión selectiva USB en AC = `0`.
- **GPU y controladores estables (`F8`):** NVIDIA Global DRS ajustado sin overclocking/undervolting (`Prefer Maximum Performance`, `Low Latency Mode=On`, `Texture Filtering=High Performance`, `Threaded Optimization=On`, `Shader Cache=Unlimited`), `AllowGameDVR=0` en política HKLM y `EnableUlps=0` en AMD iGPU.
- **Servicios y tareas (`F9`):** Deshabilitados `PCManager Service Store`, `MapsBroker`, `RemoteRegistry`, `RetailDemo`, `WalletService`, `PhoneSvc`, `wisvc`, `NetworkDaemon`, `DiagTrack`, `dmwappushservice`, `SysMain`; establecidos en `Manual` `WSearch`, `TrkWks`, `Spooler`, `lfsvc`; deshabilitadas las 11 tareas programadas objetivo de telemetría/actualizadores (`AMDRyzenMasterSDKTask`, `StartCN`, `StartDVR`, `NvDriverUpdateCheckDaily_*`, `NVIDIA App SelfUpdate_*`, 3× `OneDrive*`, `MareBackup`, `Microsoft Compatibility Appraiser Exp`, `SoftLandingCreativeManagementTask`).
- **VBS / HVCI (`F10`):** `EnableVirtualizationBasedSecurity=0`, `HypervisorEnforcedCodeIntegrity\Enabled=0`, `SecurityServicesRunning=[0]` (HVCI y Credential Guard desactivados).

### M3 — Red, Wi-Fi, Ethernet, DNS y Ancho de Banda (`F11–F14`)
- **Wi-Fi (`F11–F12`):** `Realtek RTL8852AE` conectado en 5 GHz (`CLARO2_F3E3B9`, canal 36, `866.7 Mbps`) con `LpsEn=0` (`Low`), `PnPCapabilities=24`, `MSPower_DeviceEnable=False`, `RegROAMSensitiveLevel=127` (`Deshabilitar`), `PreferBand=2`, `Wireless Power Saving AC/DC=0`, `PCIe ASPM AC/DC=0` y red 2.4 GHz `CLARO1_F3E3B9` en conexión manual.
- **DNS y segundo plano (`F13`):** `DODownloadMode=0` (`DoSvc` detenido/manual) y `0` procesos P2P activos (`fdm`, `utweb`, `qbittorrent`, `utorrent`).
- **Ethernet y pila TCP (`F14`):** `EnableGreenEthernet=0`, `PowerSavingMode=0`, `GigaLite=0`, `*InterruptModeration=0`, `*FlowControl=0`, `PnPCapabilities=24`, checksum offloads en `3` (`Rx y Tx activado`), `TcpAckFrequency=1`, `TCPNoDelay=1`.

---

## 2. Salvaguardas del Usuario Preservadas

1. **Plan de energía anti-apagones (`Kernel-Power 41` / equipo sin batería):** Se mantuvo activo **`Gaming Estable (Sin Bateria)`** (`46476e86-f770-4378-857e-3a25bf0336b4`) con `PROCTHROTTLEMIN=5%`, `PROCTHROTTLEMAX=99%` (`0x63`), `PERFBOOSTMODE=0 (Disabled)` y `SYSCOOLPOL=1 (Active)` para evitar picos eléctricos/térmicos.
2. **Configuración de red previa (`optimize_network_gaming.ps1`):** Preservados `LpsEn=0`, RSC deshabilitado (global y Wi-Fi), RSS habilitado, Auto-Tuning `normal`, ECN/Timestamps/NonSackRttResiliency deshabilitados, Nagle desactivado (`TcpAckFrequency=1`, `TCPNoDelay=1`), MMCSS (`NetworkThrottlingIndex=0xFFFFFFFF`, `SystemResponsiveness=0`), política QoS `Fortnite_Gaming_QoS` (DSCP `46`) y DNS estático en Wi-Fi **`1.1.1.1 / 1.0.0.1`**.
3. **Hyper-V para WSL2 / Docker Desktop (`-KeepHypervisor`):** Se ejecutó `apply_all_admin.ps1 -KeepHypervisor`, manteniendo `HypervisorPresent = True` y WSL2 funcional.
4. **Zen Browser intacto:** `user.js` (`2026-11-21T17:11:02Z`) y `chrome/userContent.css` no fueron modificados por los scripts de optimización.

---

## 3. Verificación Post-Reinicio, Eventos Entre Sesiones y Notas Técnicas

- **Último arranque y estabilidad del visor de eventos:**
  - `LastBootUpTime`: `2026-11-25T12:31:38Z`.
  - **Eventos `Kernel-Power 41` entre sesiones (confirmados por el usuario en `2026-11-24T02:18:23Z`, `2026-11-24T19:03:26Z` y `2026-11-25T13:11:52Z`):**
    - Se registraron eventos `Microsoft-Windows-Kernel-Power` ID `41` (`BugcheckCode=0`, `PowerButtonTimestamp=0`) entre sesiones en **`2026-11-23T01:00:53Z`**, **`2026-11-24T01:40:20Z`**, **`2026-11-24T18:48:47Z`**, **`2026-11-25T04:39:32Z`** (~22:39 hora local) y al inicio de la sesión actual en **`2026-11-25T12:31:40Z`** (~06:31 hora local, 2 s después del arranque `12:31:38Z`), todos con **`0` errores de GPU (`nvlddmkm = 0`)**.
    - El usuario confirmó explícitamente (`ORIGINAL_REQUEST.md`, `2026-11-24T02:18:23Z`, `2026-11-24T19:03:26Z` y `2026-11-25T13:11:52Z`) que todos estos eventos (`2026-11-23T01:00:53Z`, `2026-11-24T01:40:20Z`, `2026-11-24T18:48:47Z`, `2026-11-25T04:39:32Z` y `2026-11-25T12:31:40Z`) correspondieron a **desconexiones normales del cable de corriente / regleta entre sesiones** en esta laptop operada sin batería (`NoSystemBattery`), sin apagones inesperados bajo carga (`0` load crashes), con **`0` errores `nvlddmkm`**, **`0` errores `WHEA`**, **`0` thermal throttling (`throttle_reasons = none (0x0)`, máx. `46.9 °C`)**, y el plan **`Gaming Estable (Sin Bateria)`** (`PROCTHROTTLEMAX=99%`, `PERFBOOSTMODE=0`) completamente intacto.
  - **Estabilidad dentro de la sesión de arranque actual (`2026-11-25T12:31:38Z`) y tras `post_system.json` (`2026-11-25T13:15:14Z`):**
    - Tras estabilizarse el arranque (`>30 s` después de `12:31:38Z`, una vez registrado el evento histórico `12:31:40Z` del corte AC previo al encendido) y durante toda la sesión actual incluyendo las mediciones `post_system.json` (`2026-11-25T13:15:14Z`) y `post_network.json` (`2026-11-25T13:16:08Z`): **`Kernel-Power 41 = 0`**, **`nvlddmkm = 0`** (el último ocurrió el `2026-11-21T16:25:31Z`), **`WHEA = 0`**, **`Service Control Manager = 0`**, y **`Application = 0`**.
  - *Nota sobre métrica 7 días (`event_errors` en `post_system.json`):* Los contadores `5` (`nvlddmkm_count`) / `16` (`kernel_power_count`) / `33` (`scm_error_count`) corresponden a la ventana histórica móvil de **168 horas (7 días)** que incluye eventos anteriores a las optimizaciones más las desconexiones normales del cable AC/regleta entre sesiones confirmadas por el usuario (`2026-11-23T01:00:53Z`, `2026-11-24T01:40:20Z`, `2026-11-24T18:48:47Z`, `2026-11-25T04:39:32Z` y `2026-11-25T12:31:40Z`).
- **Conectividad y red sin contención verificadas (`post_network.json` — `2026-11-25T13:16:08Z`):**
  - `Wi-Fi` `IPv4Connectivity = Internet`; `Resolve-DnsName` resolvió sin pérdida (`0.0%`) en todos los servidores probados y el enlace 5 GHz (`CLARO2_F3E3B9`, canal 36, `866.7 Mbps`, `-59 dBm`) se mantuvo estable.
  - En la medición limpia sin contención (`Steam` cerrado, tráfico previo `RxKBps = 1.95 KB/s`), el salto local hacia el gateway `192.168.1.1` mejoró a **`avg = 2.828 ms`** (`-7.73%` vs. `3.065 ms`), **`p50 = 2.232 ms`** (`-7.23%` vs. `2.406 ms`), **`p95 = 2.673 ms`** (`-53.25%` vs. `5.718 ms`), **`jitter = 1.012 ms`** (`-8.99%` vs. `1.112 ms`), `0.0%` pérdida (con un pico aislado en `packet1 = 30.829 ms`, `p99 = 18.851 ms`), y los destinos WAN mostraron `0.0%` pérdida y mejoras consistentes en todos los objetivos: `9.9.9.9` (`packet1 = 39.711 ms`, `avg = 40.329 ms` [`-4.64%`], `p50 = 39.252 ms` [`-5.47%`], `p95 = 42.587 ms` [`-11.64%`], `jitter = 2.728 ms`), `8.8.8.8` (`packet1 = 38.554 ms` [`-16.98%`], `avg = 38.751 ms` [`-17.76%`], `p50 = 38.344 ms` [`-17.80%`], `p95 = 40.001 ms` [`-18.96%`], `jitter = 1.354 ms` [`-20.02%`]), y `1.1.1.1` (`packet1 = 39.725 ms` [`-41.50%`], `avg = 40.593 ms` [`-39.02%`], `p50 = 40.166 ms` [`-39.53%`], `p95 = 44.633 ms` [`-34.61%`], `jitter = 1.499 ms` [`-5.25%`]).
  - La resolución DNS también mejoró sustancialmente en los servidores configurados (`1.1.1.1`: `avg = 42.846 ms` [`-38.33%`], `p95 = 47.472 ms` [`-33.42%`]; `1.0.0.1`: `avg = 41.442 ms` [`-40.34%`], `p95 = 42.711 ms` [`-40.75%`]) así como en `9.9.9.9` (`avg = 42.766 ms` [`-6.98%`], `p95 = 48.252 ms` [`-30.15%`]) y los resolvers locales del ISP (`216.230.147.90`: `-48.39%` avg; `216.230.139.8`: `-30.49%` avg), todos con `0.0%` pérdida.
- **Endurecimiento Phase 1 y Phase 2 (Tier-5) aplicado en scripts y pruebas:**
  - `scripts/rollback_all.ps1`: restauración por adaptador de `MSPower_DeviceEnable` (`Get-BaselineMsPowerEnableTarget`) para `'Wi-Fi'` y `'Ethernet'` sin filtrar valores de Wi-Fi hacia Ethernet, limpieza de `PnPCapabilities` en Ethernet si no existía en el respaldo (`eth_driver_reg.reg`), limpieza de `DelayedAutostart=1` obsoleto en transiciones de servicios (`sc.exe config` + `Set-ItemProperty`), y resiliencia `StrictMode` ante JSON parcial/ausente en `backups/system/`.
  - `scripts/benchmark_network.ps1`: validación de rangos en parámetros (`[ValidateRange]`), diagnóstico explícito si falta el adaptador `$nic`, y guardias de `StrictMode` con `Select-Object -First 1` / `.PSObject.Properties[...]` sobre `DhcpNameServer`, subclaves de clase NIC (`^\d{4}$`), `$ipCfg`, `$route`, `$dnsObj`, `$connProfile` y `MSPower_DeviceEnable`.
  - `scripts/generate_comparison_report.py`: formato seguro `fmt_wifi_meta(ad)` para renderizar `N/A` en adaptadores no-Wi-Fi o campos ausentes, rechazo estricto de `NaN`/`Infinity` en JSON, y soporte de `"<ssid> / <band> / ch <channel>"` cuando Wi-Fi está conectado.
  - Guardias adicionales de `StrictMode`, `ValidateSet`, `-WhatIf` sin mutación y liberación de recursos aplicados en `backup_baseline.ps1`, `apply_os_gpu_admin.ps1`, `apply_network_admin.ps1` y `toggle_vbs_hypervisor.ps1`, junto con pruebas adversariales Tier-5 en `tests/run_e2e_tests.py --self-test` y la suite E2E completa (`176/176 PASS`).
- **Nota técnica — `-KeepHypervisor` (`VBS = 2` con `HVCI = 0`):**
  - En Windows 11 Home 25H2 (`10.0.26200`) con `VirtualMachinePlatform` activo para Docker/WSL2 (`HypervisorPresent = True`), el gestor de arranque habilita automáticamente el soporte base de Virtual Secure Mode (`Win32_DeviceGuard.VirtualizationBasedSecurityStatus = 2`), razón por la cual `post_system.json` reporta `vbs_status = 2`. Sin embargo, la política de VBS (`EnableVirtualizationBasedSecurity = 0`), la integridad de código por hipervisor (`HypervisorEnforcedCodeIntegrity\Enabled = 0`) y `SecurityServicesRunning = [0]` confirman que **HVCI (Memory Integrity) y Credential Guard están desactivados**. Si alguna vez desea `VirtualizationBasedSecurityStatus = 0` absoluto (sin WSL2/Docker), puede ejecutar `scripts/toggle_vbs_hypervisor.ps1 -Mode gaming` sin `-KeepHypervisor` y reiniciar.
- **Nota técnica — `Ethernet` (`PnPCapabilities = 24` vs WMI `MSPower_DeviceEnable = True`):**
  - El adaptador cableado `Realtek PCIe GbE` (`DEV_8168`, actualmente desconectado) conserva de forma persistente `PnPCapabilities = 24` (`0x18`) y todas sus propiedades avanzadas de ahorro en `0`, aunque el controlador Realtek restablece el bit WMI `MSPower_DeviceEnable = True` al reiniciar cuando no hay enlace físico conectado. El adaptador activo `Wi-Fi` (`DEV_8852`) mantiene tanto `PnPCapabilities = 24` como `MSPower_DeviceEnable = False`.
- **Nota técnica — RAM disponible bajo sesión protegida (`T4-S06`):**
  - El escenario `T4-S06` (`tests/test_tier4_scenarios.py`) valida que la RAM disponible en vivo cumpla simultáneamente `avail_mb >= 8,000 MB` (alineado con `T3-PW-12`) y mejora estricta sobre la línea base (`avail_mb > 7,442.14 MB` de `reports/baseline_system.json`), en lugar de un umbral fijo de `10,000 MB`. Esto responde a que `Zen Browser` (~7 GB en pestañas activas) está explícitamente protegido por salvaguarda del usuario (`ORIGINAL_REQUEST.md`, `2026-11-21T17:21:07Z`) y la sesión del IDE Antigravity (~3 GB) aloja la verificación, mientras que la RAM disponible medida tras las optimizaciones (`12,567.35 MB` en `post_system.json`, `+5,125.21 MB` / `+68.87%` respecto a `7,442.14 MB`) supera ampliamente tanto `8,000 MB` como la línea base.
- **Nota de usuario — `NVIDIA Overlay` y modo competitivo Wi-Fi:**
  - `apply_os_gpu_user.ps1` deshabilitó `IsShadowPlayEnabled` e `IsShadowPlayEnabledUser` en `HKCU\Software\NVIDIA Corporation\Global\ShadowPlay\NVSPCAPS` (con respaldo previo en `backups/system/hkcu_nvspcaps_before.reg`) y detuvo el contenedor de usuario `SPUser`, eliminando el relanzamiento de `NVIDIA Overlay.exe`. Si en algún momento desea volver a activar o gestionar el overlay manualmente, puede hacerlo desde **NVIDIA App → Configuración → Características → Superposición de NVIDIA**.
  - Dado que `netsh wlan` mantiene `autoconfig=yes` en estado normal para uso diario, puede ejecutar `scripts/gaming_wlan_scan_toggle.ps1 -Mode gaming` antes de partidas competitivas (y `-Mode restore` al terminar) para evitar micro-picos ocasionales de escaneo Wi-Fi en segundo plano.

---

## 4. Tabla Comparativa Exacta (Antes vs. Después — Métricas Reales)

> Fuentes: `reports/baseline_system.json` (`2026-11-21T16:55:36Z`), `reports/post_system.json` (`2026-11-25T13:15:14Z`), `reports/baseline_network.json` (`2026-11-21T16:56:40Z`), `reports/post_network.json` (`2026-11-25T13:16:08Z`) y `reports/comparison_report.md` (`2026-11-25T13:16:09Z`).

### 4.1 Sistema, CPU, GPU y Almacenamiento

| Métrica | Antes (Baseline) | Después (Post) | Cambio / Observación |
|---|---:|---:|---|
| Plan de energía activo | `Ultimate Performance` | `Gaming Estable (Sin Bateria)` | Salvaguarda anti-`KP41` activa |
| Procesos en ejecución | `236` | `202` | `-34` (`-14.41%`) |
| RAM disponible | `7442.14 MB` | `12567.35 MB` | `+5125.21 MB` (`+68.87%`) |
| Archivo de paginación usado | `538.43 MB` | `495.42 MB` | `-43.01 MB` (`-7.99%`) |
| Cambios de contexto/s | `76340 /s` | `5574 /s` | `-70766 /s` (`-92.70%`) |
| Timer jitter (`mean / p95 / p99 / max`) | `0.5273 / 0.6705 / 1.102 / 7.8945 ms` | `0.5848 / 0.893 / 1.0867 / 1.3865 ms` | `+0.058 ms` (`+10.90%`) media, `+0.223 ms` (`+33.18%`) p95, `-0.015 ms` (`-1.39%`) p99, `-6.508 ms` (`-82.44%`) max |
| CPU Single-Thread (`gcc -O3`) | `113.979 ms` | `123.704 ms` | `+9.725 ms` (`+8.53%`, sin Turbo Boost `PERFBOOSTMODE=0`, `MAX=99%`) |
| CPU Multi-Thread GEMM | `267.84 GFLOPS` | `393.94 GFLOPS` | `+126.10 GFLOPS` (`+47.08%`) |
| GPU CUDA FP32 GEMM (`GTX 1650`) | `1.354 TFLOPS` | `1.856 TFLOPS` | `+0.502 TFLOPS` (`+37.08%`) |
| GPU Clock promedio / Power max | `1785.0 MHz` / `65.05 W` | `1785.0 MHz` / `63.81 W` | `0 MHz` (`+0.00%`), `-1.24 W` (`-1.91%`), `throttle = none (0x0)` |
| GPU Temperatura máxima | `68.7 °C` | `46.9 °C` | `-21.8 °C` (`-31.73%`) |
| GPU H2D Pinned / Launch latency | `6.329 GB/s` / `15.738 us` | `6.639 GB/s` / `17.748 us` | `+0.310 GB/s` (`+4.90%`) / `+2.010 us` (`+12.77%`) |
| SSD Lectura secuencial | `87.43 MB/s` | `271.44 MB/s` | `+184.01 MB/s` (`+210.47%`) |
| SSD Escritura secuencial | `287.09 MB/s` | `227.94 MB/s` | `-59.15 MB/s` (`-20.60%`) |
| SSD 4K Lectura aleatoria | `2537.9 IOPS` | `6223.8 IOPS` | `+3685.9 IOPS` (`+145.23%`) |
| `GameDVR` / `Transparency` / `hiberfil.sys` | `True / True / True` | `False / False / False` | Todos deshabilitados / eliminado |
| `VBS Status` (`Win32_DeviceGuard`) | `2` | `2` | `-KeepHypervisor`: HVCI=`0`, `Running=[0]` |

### 4.2 Red, Latencia (Ping) y DNS (`Wi-Fi` 5 GHz `CLARO2_F3E3B9`)

| Métrica | Antes (Baseline) | Después (Post) | Cambio / Observación |
|---|---:|---:|---|
| Conectividad (`connectivity_ok`) | `True` (`866.7 Mbps`, `-67 dBm`) | `True` (`866.7 Mbps`, `-59 dBm`) | `+8 dBm` señal (`-11.94%`); enlace 5 GHz ch 36 estable |
| `LpsEn` / `RegROAMSensitiveLevel` | `2` / `80` | `0` / `127` | Ahorro Wi-Fi bajo, roaming deshabilitado |
| `MSPower_DeviceEnable` / `PnPCapabilities` | `True` / `16` | `False` / `24` | Ahorro energía NIC deshabilitado |
| DC Wireless PS / DC PCIe ASPM | `2` / `2` | `0` / `0` | Máximo rendimiento incluso en perfil DC |
| DNS configurado (`Wi-Fi`) | `1.1.1.1, 1.0.0.1` | `1.1.1.1, 1.0.0.1` | Preservado según preferencia del usuario |
| Gateway `192.168.1.1` (p1 / avg / p50 / p95 / jitter / loss) | `14.956 / 3.065 / 2.406 / 5.718 / 1.112 ms` / `0.0%` | `30.829 / 2.828 / 2.232 / 2.673 / 1.012 ms` / `0.0%` | Packet 1 `+15.873 ms` (`+106.13%`), Avg `-0.237 ms` (`-7.73%`), P50 `-0.174 ms` (`-7.23%`), P95 `-3.045 ms` (`-53.25%`), Jitter `-0.100 ms` (`-8.99%`) |
| `9.9.9.9` (p1 / avg / p50 / p95 / jitter / loss) | `40.467 / 42.292 / 41.525 / 48.195 / 2.724 ms` / `0.0%` | `39.711 / 40.329 / 39.252 / 42.587 / 2.728 ms` / `0.0%` | Packet 1 `-0.756 ms` (`-1.87%`), Avg `-1.963 ms` (`-4.64%`), P50 `-2.273 ms` (`-5.47%`), P95 `-5.608 ms` (`-11.64%`), Jitter `+0.004 ms` (`+0.15%`) |
| `8.8.8.8` (p1 / avg / p50 / p95 / jitter / loss) | `46.438 / 47.122 / 46.646 / 49.358 / 1.693 ms` / `0.0%` | `38.554 / 38.751 / 38.344 / 40.001 / 1.354 ms` / `0.0%` | Packet 1 `-7.884 ms` (`-16.98%`), Avg `-8.371 ms` (`-17.76%`), P50 `-8.302 ms` (`-17.80%`), P95 `-9.357 ms` (`-18.96%`), Jitter `-0.339 ms` (`-20.02%`) |
| `1.1.1.1` (p1 / avg / p50 / p95 / jitter / loss) | `67.909 / 66.568 / 66.424 / 68.258 / 1.582 ms` / `0.0%` | `39.725 / 40.593 / 40.166 / 44.633 / 1.499 ms` / `0.0%` | Packet 1 `-28.184 ms` (`-41.50%`), Avg `-25.975 ms` (`-39.02%`), P50 `-26.258 ms` (`-39.53%`), P95 `-23.625 ms` (`-34.61%`), Jitter `-0.083 ms` (`-5.25%`) |
| DNS `9.9.9.9` (avg / p95 / loss) | `45.975 / 69.084 ms` / `0.0%` | `42.766 / 48.252 ms` / `0.0%` | `-3.209 ms` avg (`-6.98%`), `-20.832 ms` p95 (`-30.15%`), `0.0%` pérdida |
| DNS `8.8.8.8` (avg / p95 / loss) | `51.757 / 57.271 ms` / `0.0%` | `53.863 / 64.115 ms` / `0.0%` | `+2.106 ms` avg (`+4.07%`), `+6.844 ms` p95 (`+11.95%`), `0.0%` pérdida |
| DNS `1.1.1.1` (avg / p95 / loss) | `69.471 / 71.296 ms` / `0.0%` | `42.846 / 47.472 ms` / `0.0%` | `-26.625 ms` avg (`-38.33%`), `-23.824 ms` p95 (`-33.42%`), `0.0%` pérdida |
| DNS `1.0.0.1` (avg / p95 / loss) | `69.459 / 72.081 ms` / `0.0%` | `41.442 / 42.711 ms` / `0.0%` | `-28.017 ms` avg (`-40.34%`), `-29.370 ms` p95 (`-40.75%`), `0.0%` pérdida |
| DNS `216.230.147.90` (avg / p95 / loss) | `38.362 / 113.612 ms` / `0.0%` | `19.798 / 43.771 ms` / `0.0%` | `-18.564 ms` avg (`-48.39%`), `-69.841 ms` p95 (`-61.47%`), `0.0%` pérdida |
| DNS `216.230.139.8` (avg / p95 / loss) | `18.594 / 53.181 ms` / `0.0%` | `12.924 / 27.385 ms` / `0.0%` | `-5.670 ms` avg (`-30.49%`), `-25.796 ms` p95 (`-48.51%`), `0.0%` pérdida |
