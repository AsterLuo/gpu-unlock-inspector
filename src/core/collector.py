# -*- coding: utf-8 -*-
"""三层数据源编排：NVML → nvidia-smi → WMI。

任何一层失败都不抛异常，记录下来继续往下走；字段缺失一律 None（上层显示 Unknown）。
这是「无 GPU / 无驱动 / 无权限」三种异常场景不崩溃的根本保证。
"""
from __future__ import annotations

import sys
import traceback

from . import hwinfo as hwinfo_mod
from . import nvml as nvml_mod
from . import smi as smi_mod
from . import wmi as wmi_mod


class GpuSnapshot:
    """一台机器一次的采集结果。"""

    def __init__(self):
        self.gpus = []          # list[dict]，每项是合并后的字段字典
        self.sources_used = []  # ["nvml", "nvidia-smi", "wmi"]
        self.warnings = []      # [(i18n_key, detail)]
        self.nvml = None        # NVML 实例，供实时轮询复用
        self.nvml_error = None
        self.smi_available = False
        self.wmi_devices = []
        self.host = {}
        self.hwinfo_available = False

    @property
    def primary(self):
        return self.gpus[0] if self.gpus else None

    @property
    def has_gpu(self):
        return bool(self.gpus)


# 可以由「低优先级源」补齐、但绝不能覆盖 NVML 值的字段
_SMI_FILL = (
    "name", "vbios_version", "driver_version", "serial", "uuid", "mem_total_bytes", "mem_used_bytes",
    "mem_free_bytes", "mem_clock_mhz", "clock_graphics_mhz", "clock_sm_mhz",
    "clock_graphics_max_mhz", "clock_sm_max_mhz", "mem_clock_max_mhz", "temp_c",
    "temp_shutdown_c", "temp_slowdown_c", "fan_pct", "power_draw_w", "power_limit_w",
    "power_default_w", "power_enforced_w", "util_gpu_pct", "util_mem_pct",
    "pcie_gen_cur", "pcie_gen_max", "pcie_width_cur", "pcie_width_max",
    "throttle_active", "pci_bus_id",
)


def _merge(base, extra, fields):
    for f in fields:
        if base.get(f) in (None, "", []) and extra.get(f) not in (None, "", []):
            base[f] = extra[f]


def collect():
    """完整采集一次。永远返回 GpuSnapshot，不抛异常。"""
    snap = GpuSnapshot()

    # ---- 第 1 层：NVML
    try:
        n = nvml_mod.Nvml()
        if n.init() and n.count() > 0:
            snap.nvml = n
            snap.sources_used.append("nvml")
            for i in range(n.count()):
                d = n.read_device(i)
                if d:
                    d["index"] = i
                    snap.gpus.append(d)
        else:
            snap.nvml_error = n.error or "no_device"
            if n.error:
                snap.warnings.append(("msg.nvml_fail", str(n.error)))
    except Exception as exc:  # pragma: no cover - 兜底
        snap.nvml_error = f"{type(exc).__name__}: {exc}"
        snap.warnings.append(("msg.nvml_fail", snap.nvml_error))
        traceback.print_exc(file=sys.stderr)

    # ---- 第 2 层：nvidia-smi（补 NVML 缺的字段，或完全替代）
    try:
        devs, err = smi_mod.read_devices()
        snap.smi_available = not err and bool(devs)
        if err:
            if snap.gpus:
                snap.warnings.append(("msg.smi_fail", err))
        else:
            snap.sources_used.append("nvidia-smi")
            if not snap.gpus:
                snap.gpus = devs
            else:
                for i, d in enumerate(devs):
                    if i < len(snap.gpus):
                        _merge(snap.gpus[i], d, _SMI_FILL)
    except Exception as exc:  # pragma: no cover
        snap.warnings.append(("msg.smi_fail", f"{type(exc).__name__}: {exc}"))

    # ---- 第 3 层：WMI（一定有型号名与驱动版本）
    try:
        wmis, werr = wmi_mod.read_devices()
        if werr:
            if not snap.gpus:
                snap.warnings.append(("msg.wmi_fail", werr))
        else:
            snap.wmi_devices = wmis
            if not snap.gpus and wmis:
                # 前三层都拿不到实时数据时，WMI 至少能给出型号名与驱动版本。
                # 这时要「降级展示」而不是直接判定为没有显卡 —— 否则用户会以为程序坏了。
                nv_wmi = [w for w in wmis if wmi_mod.is_nvidia_name(w.get("name", ""))]
                if nv_wmi:
                    degraded = dict(nv_wmi[0])
                    degraded["source"] = "wmi"
                    degraded["degraded"] = True
                    snap.gpus.append(degraded)
                    snap.sources_used.append("wmi")
                    snap.warnings.append(("msg.wmi_degraded", ""))
            elif snap.gpus:
                snap.sources_used.append("wmi")
                # 驱动版本 NVML 不提供，只能从 WMI 拿
                nv_name = (snap.gpus[0].get("name") or "").lower()
                for w in wmis:
                    if not wmi_mod.is_nvidia_name(w.get("name", "")):
                        continue
                    key = (w.get("name") or "").lower().replace("nvidia", "").replace("geforce", "").strip()
                    if not (key and key in nv_name.replace("nvidia", "").replace("geforce", "")):
                        continue
                    # WMI 的 DriverVersion 是内部版本号（32.0.16.1714），
                    # 只在 NVML / nvidia-smi 都没给出公开发布号时才兜底
                    for f in ("driver_internal_version", "driver_date", "pnp_id"):
                        if not snap.gpus[0].get(f) and w.get(f):
                            snap.gpus[0][f] = w[f]
                    break
    except Exception as exc:  # pragma: no cover
        snap.warnings.append(("msg.wmi_fail", f"{type(exc).__name__}: {exc}"))

    # ---- 平台/主机信息
    try:
        snap.host = {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
            "admin": _is_admin(),
        }
    except Exception:
        pass

    # ---- 探测 HWiNFO（只探测一次，结果缓存到 snapshot 上）
    try:
        hw = hwinfo_mod.probe()
        snap.hwinfo_available = hw is not None
        if hw is not None:
            hw.close()
            snap.sources_used.append("hwinfo")
    except Exception:
        snap.hwinfo_available = False

    if not snap.gpus:
        snap.warnings.append(("msg.no_gpu.body", ""))

    return snap


def _is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def live_sampler(snap, index=0):
    """返回可调用的轮询函数，按「HWiNFO → NVML → nvidia-smi」取实时值。

    为什么 HWiNFO 排第一：NVML 的功耗/温度刷新被驱动压在 ~1 Hz，
    界面上会明显「跟不上」显卡真实状态；HWiNFO 按自身周期（默认 500 ms）
    持续采样，读数更新更快，还能提供热点温度 / 显存结温 / 风扇 RPM。

    每次调用返回 dict，绝不抛异常。返回的函数带 .source 属性，
    UI 用它显示当前实时数据的来源。
    """
    reader = None
    try:
        reader = hwinfo_mod.probe()
    except Exception:
        reader = None

    nvml_live = None
    if snap is not None and snap.nvml is not None and index < snap.nvml.count():
        nvml_live = nvml_mod.NvmlLive(snap.nvml, index)

    def _smi_sample():
        try:
            rows, err = smi_mod.read_state_fast()
            if err or not rows:
                return {}
            return rows[index] if index < len(rows) else rows[0]
        except Exception:
            return {}

    def sample():
        out = {}
        # 1) HWiNFO：最快、字段最多
        if reader is not None:
            try:
                if reader.refresh() > 0:
                    out.update(reader.snapshot())
            except Exception:
                pass
        # 2) NVML：补齐 HWiNFO 没给或版本不匹配时的字段
        if nvml_live is not None:
            try:
                for k, v in (nvml_live.sample() or {}).items():
                    if out.get(k) is None and v is not None:
                        out[k] = v
            except Exception:
                pass
        # 3) nvidia-smi：最后兜底
        if not out.get("power_w") or out.get("temp_c") is None:
            try:
                for k, v in (_smi_sample() or {}).items():
                    if out.get(k) is None and v is not None:
                        out[k] = v
            except Exception:
                pass
        if reader is not None:
            out["source"] = "hwinfo"
        elif nvml_live is not None:
            out["source"] = "nvml"
        else:
            out["source"] = "nvidia-smi"
        return out

    sample.source = ("hwinfo" if reader is not None
                     else ("nvml" if nvml_live is not None else "nvidia-smi"))
    sample.close = lambda: (reader.close() if reader is not None else None)
    return sample