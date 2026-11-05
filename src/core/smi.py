# -*- coding: utf-8 -*-
"""数据源第 2 层：nvidia-smi 子进程（XML 模式）。

NVML 直接调用失败（老驱动缺符号 / DLL 被占用）时用它兜底。
用 --format=xml 而不是 csv，因为 XML 里的字段名稳定、不会被本地化。
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

CREATE_NO_WINDOW = 0x08000000


def _no_window_kwargs():
    """打包成 --noconsole 后，子进程仍然不能弹黑框。"""
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return kw


def _smi_path():
    cand = []
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    cand.append(os.path.join(sys32, "nvidia-smi.exe"))
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    cand.append(os.path.join(prog, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"))
    for c in cand:
        if os.path.isfile(c):
            return c
    return None


def smi_available():
    return _smi_path() is not None


def _run(args, timeout=12):
    exe = _smi_path()
    if not exe:
        return None, "nvidia-smi not found"
    try:
        p = subprocess.run([exe] + args, capture_output=True, timeout=timeout,
                           **_no_window_kwargs())
    except subprocess.TimeoutExpired:
        return None, "nvidia-smi timeout"
    except OSError as exc:
        return None, f"nvidia-smi spawn failed: {exc}"
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        return None, err or f"nvidia-smi exit {p.returncode}"
    return p.stdout.decode("utf-8", "replace"), None


def _txt(node, path, default=None):
    if node is None:
        return default
    el = node.find(path)
    if el is None or el.text is None:
        return default
    return el.text.strip()


def _num(node, path, conv=float):
    v = _txt(node, path)
    if v is None:
        return None
    try:
        return conv(v)
    except (TypeError, ValueError):
        return None


def read_devices():
    """返回 (devices, error)。devices 为字段字典列表。"""
    out, err = _run(["-q", "-x"])
    if out is None:
        return [], err
    try:
        root = ET.fromstring(out)
    except ET.ParseError as exc:
        return [], f"nvidia-smi xml parse error: {exc}"

    root_driver = _txt(root, "driver_version")
    devs = []
    for gpu in root.findall("gpu"):
        d = {"source": "nvidia-smi"}
        d["name"] = _txt(gpu, "product_name")
        d["driver_version"] = root_driver
        d["vbios_version"] = _txt(gpu, "vbios_version")
        d["serial"] = _txt(gpu, "serial")
        d["uuid"] = _txt(gpu, "uuid")
        d["pci_bus_id"] = _txt(gpu, "pci/pci_bus_id")

        total = _num(gpu, "fb_memory_usage/total", int)
        used = _num(gpu, "fb_memory_usage/used", int)
        free = _num(gpu, "fb_memory_usage/free", int)
        d["mem_total_bytes"] = total * 1024 * 1024 if total else None
        d["mem_used_bytes"] = used * 1024 * 1024 if used else None
        d["mem_free_bytes"] = free * 1024 * 1024 if free else None
        d["mem_clock_mhz"] = _num(gpu, "clocks/mem", int)
        d["clock_graphics_mhz"] = _num(gpu, "clocks/graphics", int)
        d["clock_sm_mhz"] = _num(gpu, "clocks/sm", int)
        d["clock_graphics_max_mhz"] = _num(gpu, "max_clocks/graphics", int)
        d["clock_sm_max_mhz"] = _num(gpu, "max_clocks/sm", int)
        d["mem_clock_max_mhz"] = _num(gpu, "max_clocks/mem", int)

        d["temp_c"] = _num(gpu, "temperature/gpu_temp", int)
        d["temp_shutdown_c"] = _num(gpu, "temperature/gpu_temp_max_threshold", int)
        d["temp_slowdown_c"] = _num(gpu, "temperature/gpu_temp_slow_threshold", int)

        d["fan_pct"] = _num(gpu, "fan_speed", int)

        d["power_draw_w"] = _num(gpu, "gpu_power_readings/power_draw")
        d["power_limit_w"] = _num(gpu, "gpu_power_readings/current_power_limit")
        d["power_default_w"] = _num(gpu, "gpu_power_readings/default_power_limit")
        d["power_enforced_w"] = _num(gpu, "gpu_power_readings/enforced_power_limit")

        d["util_gpu_pct"] = _num(gpu, "utilization/gpu_util", int)
        d["util_mem_pct"] = _num(gpu, "utilization/memory_util", int)

        d["pcie_gen_cur"] = _num(gpu, "pci/pci_gpu_link_info/pcie_gen/current_link_gen", int)
        d["pcie_gen_max"] = _num(gpu, "pci/pci_gpu_link_info/pcie_gen/max_link_gen", int)
        d["pcie_width_cur"] = _num(gpu, "pci/pci_gpu_link_info/link_width/current_link_width", int)
        d["pcie_width_max"] = _num(gpu, "pci/pci_gpu_link_info/link_width/max_link_width", int)

        reasons = []
        for el in gpu.findall("clocks_throttle_reasons/active_clocks_throttle_reasons"):
            # 只有值为 0x0000000000000001 的子项才算“激活”
            for child in list(el):
                if child.text and child.text.strip().startswith("0x000000000000000"):
                    pass
        for tag in ("gpu_idle", "applications_clocks_setting", "sw_power_cap",
                    "hw_slowdown", "sync_boost", "sw_thermal_slowdown",
                    "hw_thermal_slowdown", "hw_power_brake_slowdown",
                    "display_clock_setting"):
            v = _txt(gpu, f"clocks_throttle_reasons/{tag}")
            if v and v.lower().startswith("active"):
                reasons.append(tag)
        d["throttle_active"] = reasons
        d["throttle_mask"] = None

        devs.append(d)
    return devs, None


def read_state_fast():
    """只取实时字段（毫秒级），供基准测试轮询使用。"""
    out, err = _run([
        "--query-gpu=power.draw,clocks.sm,clocks.mem,temperature.gpu,fan.speed,utilization.gpu,utilization.memory",
        "--format=csv,noheader,nounits",
    ], timeout=6)
    if out is None:
        return None, err
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue

        def f(x):
            try:
                return float(x)
            except ValueError:
                return None

        rows.append({
            "power_w": f(parts[0]),
            "clock_sm_mhz": f(parts[1]),
            "clock_mem_mhz": f(parts[2]),
            "temp_c": f(parts[3]),
            "fan_pct": f(parts[4]),
            "util_gpu_pct": f(parts[5]),
            "util_mem_pct": f(parts[6]),
        })
    return rows, None