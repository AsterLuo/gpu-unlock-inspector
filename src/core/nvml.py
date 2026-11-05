# -*- coding: utf-8 -*-
r"""NVML 绑定（纯 ctypes，零第三方依赖）。

数据源优先级第 1 层：直接调用 Windows 自带的 nvml.dll (C:\Windows\System32\nvml.dll)。
优点：毫秒级、字段最全、无需子进程；缺点：老驱动缺某些符号，因此每个调用都做
存在性检查 + 返回码检查，缺失一律降级为 None 而不是抛异常。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import (POINTER, Structure, byref, c_char_p, c_int, c_uint,
                    c_ulonglong, c_void_p, create_string_buffer)

# ---------------------------------------------------------------- 常量
NVML_SUCCESS = 0
NVML_ERROR_DRIVER_NOT_LOADED = 9
NVML_ERROR_NO_PERMISSION = 4
NVML_ERROR_NOT_SUPPORTED = 3

NVML_CLOCK_GRAPHICS = 0
NVML_CLOCK_SM = 1
NVML_CLOCK_MEM = 2
NVML_CLOCK_VIDEO = 3

NVML_TEMPERATURE_GPU = 0

# 时钟降频原因位（NVML 文档）
THROTTLE_REASONS = [
    (0x0000000000000001, "gpu_idle"),
    (0x0000000000000002, "applications_clocks_setting"),
    (0x0000000000000004, "sw_power_cap"),
    (0x0000000000000008, "hw_slowdown"),
    (0x0000000000000010, "sync_boost"),
    (0x0000000000000020, "sw_thermal_slowdown"),
    (0x0000000000000040, "hw_thermal_slowdown"),
    (0x0000000000000080, "hw_power_brake_slowdown"),
    (0x0000000000000100, "display_clock_setting"),
    (0x0000000000000200, "sw_power_cap"),
    (0x0000000000000400, "hw_slowdown"),
    (0x0000000000000800, "hw_thermal"),
    (0x0000000000001000, "sw_thermal"),
    (0x0000000000002000, "hw_power_brake"),
    (0x4000000000000000, "sw_power_cap_always"),
]

NVML_STRUCT_VERSION = lambda data, ver: (ver << 24) | ctypes.sizeof(data)


class NvmlMemoryV2(Structure):
    _fields_ = [
        ("version", c_ulonglong),
        ("total", c_ulonglong),
        ("reserved", c_ulonglong),
        ("free", c_ulonglong),
        ("used", c_ulonglong),
    ]


class NvmlMemoryV1(Structure):
    _fields_ = [("total", c_ulonglong), ("free", c_ulonglong), ("used", c_ulonglong)]


class NvmlPciInfo(Structure):
    _fields_ = [
        ("busIdLegacy", ctypes.c_char * 16),
        ("domain", c_uint),
        ("bus", c_uint),
        ("device", c_uint),
        ("pciDeviceId", c_uint),
        ("pciSubSystemId", c_uint),
        ("busId", ctypes.c_char * 32),
    ]


class NvmlUtilization(Structure):
    _fields_ = [("gpu", c_uint), ("memory", c_uint)]


class NvmlProcessInfo(Structure):
    _fields_ = [("pid", c_uint), ("usedGpuMemory", c_ulonglong), ("gpuInstanceId", c_uint),
                ("computeInstanceId", c_uint)]


def _load_lib():
    """按 Windows 的 DLL 搜索路径加载 nvml.dll；失败返回 None。"""
    candidates = []
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    candidates.append(os.path.join(sys32, "nvml.dll"))
    candidates.append("nvml.dll")
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(os.path.join(prog, "NVIDIA Corporation", "NVSMI", "nvml.dll"))
    for c in candidates:
        try:
            return ctypes.WinDLL(c)
        except OSError:
            continue
    return None


class Nvml:
    """NVML 的薄封装。所有读取接口失败返回 None，绝不抛出到 UI 层。"""

    def __init__(self):
        self.lib = None
        self.available = False
        self.error = None
        self.driver_version = None
        self.nvml_version = None
        self.handles = []
        self._initialized = False

    # ---------------------------------------------------------- 生命周期
    def init(self):
        lib = _load_lib()
        if lib is None:
            self.error = "nvml_dll_missing"
            return False
        self.lib = lib
        try:
            rc = lib.nvmlInit_v2()
        except AttributeError:
            try:
                rc = lib.nvmlInit()
            except Exception as exc:  # pragma: no cover - 极老驱动
                self.error = f"nvmlInit missing: {exc}"
                return False
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
            return False
        if rc != NVML_SUCCESS:
            self.error = self._rc_text(rc)
            return False

        self._initialized = True
        self.available = True
        self.driver_version = self._str_call("nvmlSystemGetDriverVersion", 80)
        self.nvml_version = self._str_call("nvmlSystemGetNVMLVersion", 80)
        self._enumerate()
        return True

    def shutdown(self):
        if self._initialized and self.lib:
            try:
                self.lib.nvmlShutdown()
            except Exception:
                pass
        self._initialized = False

    def _enumerate(self):
        count = c_uint(0)
        fn = getattr(self.lib, "nvmlDeviceGetCount_v2", None) or getattr(self.lib, "nvmlDeviceGetCount", None)
        if fn is None:
            return
        if fn(byref(count)) != NVML_SUCCESS:
            return
        for i in range(count.value):
            h = c_void_p()
            fn2 = getattr(self.lib, "nvmlDeviceGetHandleByIndex_v2", None) or getattr(
                self.lib, "nvmlDeviceGetHandleByIndex", None)
            if fn2 is None:
                break
            if fn2(i, byref(h)) == NVML_SUCCESS:
                self.handles.append(h)

    # ---------------------------------------------------------- 内部工具
    def _rc_text(self, rc):
        try:
            name = getattr(self.lib, "nvmlErrorString", None)
            if name:
                name.restype = c_char_p
                return (name(rc) or b"").decode("utf-8", "replace")
        except Exception:
            pass
        return f"NVML error code {rc}"

    def _str_call(self, fname, size):
        fn = getattr(self.lib, fname, None)
        if fn is None:
            return None
        try:
            buf = create_string_buffer(size)
            if fn(buf, size) != NVML_SUCCESS:
                return None
            return buf.value.decode("utf-8", "replace").strip() or None
        except Exception:
            return None

    def _call(self, fname, *args, ctype=c_uint):
        """调用返回单个标量的 NVML 函数。"""
        fn = getattr(self.lib, fname, None)
        if fn is None:
            return None
        holder = ctype(0)
        try:
            if fn(*args, byref(holder)) != NVML_SUCCESS:
                return None
            return holder.value
        except Exception:
            return None

    def _str_dev(self, fname, dev, size):
        fn = getattr(self.lib, fname, None)
        if fn is None:
            return None
        try:
            buf = create_string_buffer(size)
            if fn(dev, buf, size) != NVML_SUCCESS:
                return None
            val = buf.value.decode("utf-8", "replace").strip()
            if not val or val in ("N/A", "n/a", "[N/A]", "[Not Supported]", "Not Supported"):
                return None
            return val
        except Exception:
            return None

    # ---------------------------------------------------------- 单卡读取
    def read_device(self, index):
        """返回该卡的原始字段字典；不可用字段为 None。"""
        if not self.available or index >= len(self.handles):
            return None
        dev = self.handles[index]
        d = {}

        d["name"] = self._str_dev("nvmlDeviceGetName", dev, 96)
        d["vbios_version"] = self._str_dev("nvmlDeviceGetVbiosVersion", dev, 64)
        d["serial"] = self._str_dev("nvmlDeviceGetSerial", dev, 64)
        d["uuid"] = self._str_dev("nvmlDeviceGetUUID", dev, 96)

        # 显存（优先 v2，带显存类型无关；v1 兜底）
        mem_v2 = NvmlMemoryV2()
        mem_v2.version = NVML_STRUCT_VERSION(NvmlMemoryV2, 2)
        got = False
        fn = getattr(self.lib, "nvmlDeviceGetMemoryInfo_v2", None)
        if fn is not None:
            try:
                if fn(dev, byref(mem_v2)) == NVML_SUCCESS:
                    d["mem_total_bytes"] = mem_v2.total
                    d["mem_free_bytes"] = mem_v2.free
                    d["mem_used_bytes"] = mem_v2.used
                    got = True
            except Exception:
                pass
        if not got:
            mem_v1 = NvmlMemoryV1()
            fn = getattr(self.lib, "nvmlDeviceGetMemoryInfo", None)
            if fn is not None:
                try:
                    if fn(dev, byref(mem_v1)) == NVML_SUCCESS:
                        d["mem_total_bytes"] = mem_v1.total
                        d["mem_free_bytes"] = mem_v1.free
                        d["mem_used_bytes"] = mem_v1.used
                except Exception:
                    pass

        d["mem_bus_width"] = self._call("nvmlDeviceGetMemoryBusWidth", dev)
        d["mem_clock_mhz"] = self._call("nvmlDeviceGetClockInfo", dev, NVML_CLOCK_MEM)
        d["mem_clock_max_mhz"] = self._call("nvmlDeviceGetMaxClockInfo", dev, NVML_CLOCK_MEM)

        # 频率
        d["clock_graphics_mhz"] = self._call("nvmlDeviceGetClockInfo", dev, NVML_CLOCK_GRAPHICS)
        d["clock_sm_mhz"] = self._call("nvmlDeviceGetClockInfo", dev, NVML_CLOCK_SM)
        d["clock_graphics_max_mhz"] = self._call("nvmlDeviceGetMaxClockInfo", dev, NVML_CLOCK_GRAPHICS)
        d["clock_sm_max_mhz"] = self._call("nvmlDeviceGetMaxClockInfo", dev, NVML_CLOCK_SM)
        # 默认应用时钟（判断是否被超频 / 被钳制的关键）
        d["clock_graphics_default_mhz"] = self._call("nvmlDeviceGetDefaultApplicationsClock", dev, NVML_CLOCK_GRAPHICS)
        d["clock_sm_default_mhz"] = self._call("nvmlDeviceGetDefaultApplicationsClock", dev, NVML_CLOCK_SM)
        d["clock_mem_default_mhz"] = self._call("nvmlDeviceGetDefaultApplicationsClock", dev, NVML_CLOCK_MEM)

        # 功耗
        pw = self._call("nvmlDeviceGetPowerUsage", dev)
        d["power_draw_w"] = round(pw / 1000.0, 2) if pw is not None else None
        lim = self._call("nvmlDeviceGetPowerManagementLimit", dev)
        d["power_limit_w"] = round(lim / 1000.0, 2) if lim is not None else None
        enf = self._call("nvmlDeviceGetEnforcedPowerLimit", dev)
        d["power_enforced_w"] = round(enf / 1000.0, 2) if enf is not None else None
        dflt = self._call("nvmlDeviceGetPowerManagementDefaultLimit", dev)
        d["power_default_w"] = round(dflt / 1000.0, 2) if dflt is not None else None

        # 温度 / 风扇
        d["temp_c"] = self._call("nvmlDeviceGetTemperature", dev, NVML_TEMPERATURE_GPU, ctype=c_uint)
        d["fan_pct"] = self._call("nvmlDeviceGetFanSpeed", dev)
        rpms = []
        nf = self._call("nvmlDeviceGetNumFans", dev)
        if nf:
            for i in range(nf):
                v = self._call("nvmlDeviceGetFanSpeedRPM", dev, i) if self._has(
                    "nvmlDeviceGetFanSpeedRPM_v1") else self._call("nvmlDeviceGetFanSpeedRPM_v1", dev, i)
                if v is not None:
                    rpms.append(v)
        d["fan_rpms"] = rpms or None

        # 利用率
        util = NvmlUtilization()
        fn = getattr(self.lib, "nvmlDeviceGetUtilizationRates", None)
        if fn is not None:
            try:
                if fn(dev, byref(util)) == NVML_SUCCESS:
                    d["util_gpu_pct"] = util.gpu
                    d["util_mem_pct"] = util.memory
            except Exception:
                pass

        # PCIe
        d["pcie_gen_cur"] = self._call("nvmlDeviceGetCurrPcieLinkGeneration", dev)
        d["pcie_gen_max"] = self._call("nvmlDeviceGetMaxPcieLinkGeneration", dev)
        d["pcie_width_cur"] = self._call("nvmlDeviceGetCurrPcieLinkWidth", dev)
        d["pcie_width_max"] = self._call("nvmlDeviceGetMaxPcieLinkWidth", dev)

        pci = NvmlPciInfo()
        fn = getattr(self.lib, "nvmlDeviceGetPciInfo", None)
        if fn is not None:
            try:
                if fn(dev, byref(pci)) == NVML_SUCCESS:
                    # busIdLegacy 在本驱动上未填充，用 domain/bus/device 自己拼
                    d["pci_bus_id"] = "%04X:%02X:%02X.0" % (pci.domain, pci.bus, pci.device)
                    # pciDeviceId 低 16 位是 vendor，高 16 位才是 device
                    vend = pci.pciDeviceId & 0xFFFF
                    devid = (pci.pciDeviceId >> 16) & 0xFFFF
                    d["pci_device_id"] = "%04X:%04X" % (vend, devid)
                    ssub = (pci.pciSubSystemId >> 16) & 0xFFFF
                    d["pci_subsystem_id"] = "%04X:%04X" % (vend, ssub)
            except Exception:
                pass

        d["brand"] = self._call("nvmlDeviceGetBrand", dev)
        d["num_cores"] = self._call("nvmlDeviceGetNumGpuCores", dev)  # 仅 Hopper/H100+ 可靠

        # 降频原因
        cr = self._call("nvmlDeviceGetCurrentClocksThrottleReasons", dev, ctype=c_ulonglong)
        d["throttle_mask"] = cr
        active = []
        if cr:
            for bit, name in THROTTLE_REASONS:
                if cr & bit and name not in active:
                    active.append(name)
        d["throttle_active"] = active
        sup = self._call("nvmlDeviceGetSupportedClocksThrottleReasons", dev, ctype=c_ulonglong)
        d["throttle_supported"] = sup

        # 温度阈值
        d["temp_shutdown_c"] = self._call("nvmlDeviceGetTemperatureThreshold", dev, 0)
        d["temp_slowdown_c"] = self._call("nvmlDeviceGetTemperatureThreshold", dev, 1)
        d["temp_max_op_c"] = self._call("nvmlDeviceGetTemperatureThreshold", dev, 2)

        d["serial_locked"] = d.get("serial") is None
        d["source"] = "nvml"
        return d

    def _has(self, fname):
        return self.lib is not None and hasattr(self.lib, fname)

    def count(self):
        return len(self.handles)


class NvmlLive:
    """轻量轮询器：只取基准测试每秒需要的几个字段，避免反复走全量读取。"""

    def __init__(self, nvml: Nvml, index=0):
        self.nvml = nvml
        self.index = index
        self.dev = nvml.handles[index] if nvml.available and index < len(nvml.handles) else None

    def sample(self):
        if self.dev is None:
            return None
        n = self.nvml
        out = {}
        pw = n._call("nvmlDeviceGetPowerUsage", self.dev)
        out["power_w"] = round(pw / 1000.0, 2) if pw is not None else None
        out["clock_sm_mhz"] = n._call("nvmlDeviceGetClockInfo", self.dev, NVML_CLOCK_SM)
        out["clock_graphics_mhz"] = n._call("nvmlDeviceGetClockInfo", self.dev, NVML_CLOCK_GRAPHICS)
        out["clock_mem_mhz"] = n._call("nvmlDeviceGetClockInfo", self.dev, NVML_CLOCK_MEM)
        out["temp_c"] = n._call("nvmlDeviceGetTemperature", self.dev, NVML_TEMPERATURE_GPU)
        out["fan_pct"] = n._call("nvmlDeviceGetFanSpeed", self.dev)
        util = NvmlUtilization()
        fn = getattr(n.lib, "nvmlDeviceGetUtilizationRates", None)
        if fn is not None:
            try:
                if fn(self.dev, byref(util)) == NVML_SUCCESS:
                    out["util_gpu_pct"] = util.gpu
                    out["util_mem_pct"] = util.memory
            except Exception:
                pass
        cr = n._call("nvmlDeviceGetCurrentClocksThrottleReasons", self.dev, ctype=c_ulonglong)
        out["throttle_mask"] = cr
        return out