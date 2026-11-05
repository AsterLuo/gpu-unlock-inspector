# -*- coding: utf-8 -*-
"""数据源第 3 层：WMI（Win32_VideoController / Win32_PnPEntity）降级。

只提供「一定有」的字段：型号名、驱动版本、驱动日期、显存报告值。
其余一律返回 None，由上层显示为 Unknown —— 绝不编造。
用 PowerShell 的 CIM 而不是 wmic：Win11 24H2 起 wmic 默认不存在。
"""
from __future__ import annotations

import json
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000


def _no_window_kwargs():
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return kw


PS_QUERY = (
    "Get-CimInstance Win32_VideoController | "
    "Select-Object Name,DriverVersion,DriverDate,AdapterRAM,VideoProcessor,PNPDeviceID,"
    "CurrentHorizontalResolution,CurrentVerticalResolution | ConvertTo-Json -Compress"
)


def read_devices():
    """返回 (devices, error)。"""
    exe = "powershell.exe"
    try:
        p = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", PS_QUERY],
                           capture_output=True, timeout=20, **_no_window_kwargs())
    except Exception as exc:
        return [], f"WMI query failed: {exc}"
    if p.returncode != 0:
        return [], (p.stderr or b"").decode("utf-8", "replace").strip()[:300]
    raw = (p.stdout or b"").decode("utf-8", "replace").strip()
    if not raw:
        return [], "WMI returned empty"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], f"WMI json error: {exc}"
    if isinstance(data, dict):
        data = [data]

    out = []
    for item in data:
        name = (item.get("Name") or "").strip()
        if not name:
            continue
        ram = item.get("AdapterRAM")
        d = {
            "source": "wmi",
            "name": name,
            "driver_version": (item.get("DriverVersion") or "").strip() or None,
            "driver_date": (item.get("DriverDate") or "").strip() or None,
            "pnp_id": (item.get("PNPDeviceID") or "").strip() or None,
            "mem_total_bytes": int(ram) if isinstance(ram, (int, float)) and ram else None,
        }
        out.append(d)
    return out, None


def is_nvidia_name(name: str) -> bool:
    n = (name or "").lower()
    return "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n or "gtx" in n
