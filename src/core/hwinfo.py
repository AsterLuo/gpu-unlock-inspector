# -*- coding: utf-8 -*-
"""HWiNFO64 共享内存读取（可选增强数据源，只读）。

为什么用它
----------
NVML 的刷新率被驱动限制：功耗/温度大约 1 Hz、利用率约 1/6 s 一次，
所以界面上的实时数字「跟不上」显卡真实状态。
HWiNFO64 会以 100~2000 ms 的周期持续采样，把结果写进共享内存；
我们只是读它缓存好的最新值，因此刷新率更高，还能拿到 NVML 根本没有的字段
（热点温度、显存结温、VRM 温度、风扇 RPM 等）。

前置条件（缺一不可）
--------------------
1. 装有 HWiNFO64（免费版即可）；
2. 启动时必须带 `-r<共享内存名>`，或打开 Settings → Safety →
   "Shared Memory Support"；
3. 传感器窗口（Sensors）必须开着，否则它不写共享内存。

结构定义
--------
按公开的布局文档（位置见 README 引用）实现：

    头部 48 字节
      0x00 magic            uint32  0x53695748 ("HWiS")
      0x14 sensor_off       uint32
      0x18 sensor_size      uint32   >= 264
      0x1C sensor_count     uint32
      0x20 entry_off        uint32
      0x24 entry_size       uint32   >= 316
      0x28 entry_count      uint32
      0x2C poll_time        uint32   ms

    sensor 元素
      0x00 id uint32 | 0x04 instance uint32
      0x08 name_original char[128] | 0x88 name_user char[128]

    entry 元素
      0x000 type uint32 | 0x004 sensor_index uint32 | 0x008 id uint32
      0x00C name_original char[128] | 0x08C name_user char[128]
      0x10C unit char[16]
      0x11C value double | 0x124 value_min | 0x12C value_max | 0x134 value_avg

所有 header 字段都当不可信输入做边界检查，越界直接返回 None，绝不崩。
"""
from __future__ import annotations

import ctypes
import re
import struct
import sys

FILE_MAP_READ = 0x0004
SHM_NAME = r"Global\HWiNFO_SENS_SM2"
HEADER_SIZE = 48
HEADER_MAGIC = 0x53695748

MIN_SENSOR_SIZE = 264
MIN_ENTRY_SIZE = 316
MAX_TOTAL_SIZE = 64 * 1024 * 1024
MAX_SENSOR_COUNT = 4096
MAX_ENTRY_COUNT = 65536

SENSOR_TYPES = {0: "none", 1: "temperature", 2: "voltage", 3: "fan", 4: "current",
                5: "power", 6: "clock", 7: "usage", 8: "other"}

_CTRL = re.compile("[\x00-\x1f\x7f-\x9f]")


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_uint32),
        ("__align1", ctypes.c_uint32),
        ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_uint32),
        ("Protect", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
        ("__align2", ctypes.c_uint32),
    ]


def _decode(raw, offset, length):
    """HWiNFO 用系统 ANSI 代码页（cp1252）写字符串，"°C" 是单字节 0xB0。"""
    s = raw[offset:offset + length].split(bytes([0]))[0].decode("cp1252", errors="replace")
    return _CTRL.sub("", s)


def parse_buffer(buf):
    """纯函数：解析一份共享内存快照。返回读数列表或 None。

    抽成纯函数是为了能在没有 HWiNFO 的机器上直接喂合成数据做单测。
    """
    if len(buf) < HEADER_SIZE:
        return None
    try:
        if struct.unpack_from("<I", buf, 0x00)[0] != HEADER_MAGIC:
            return None
        sensor_off, sensor_size, sensor_count = struct.unpack_from("<III", buf, 0x14)
        entry_off, entry_size, entry_count = struct.unpack_from("<III", buf, 0x20)

        if sensor_size < MIN_SENSOR_SIZE or entry_size < MIN_ENTRY_SIZE:
            return None
        if sensor_count > MAX_SENSOR_COUNT or entry_count > MAX_ENTRY_COUNT:
            return None
        if sensor_off < HEADER_SIZE or entry_off < HEADER_SIZE:
            return None
        sensor_end = sensor_off + sensor_count * sensor_size
        entry_end = entry_off + entry_count * entry_size
        if sensor_end > len(buf) or entry_end > len(buf):
            return None
        # 两段数组不得重叠，否则说明 header 已损坏
        if sensor_off < entry_end and entry_off < sensor_end:
            return None

        names = {}
        for i in range(sensor_count):
            b = sensor_off + i * sensor_size
            user = _decode(buf, b + 136, 128)
            orig = _decode(buf, b + 8, 128)
            names[i] = user or orig

        out = []
        for i in range(entry_count):
            b = entry_off + i * entry_size
            etype, sidx, eid = struct.unpack_from("<III", buf, b)
            r_orig = _decode(buf, b + 12, 128)
            r_user = _decode(buf, b + 140, 128)
            unit = _decode(buf, b + 268, 16)
            value, vmin, vmax, vavg = struct.unpack_from("<dddd", buf, b + 284)
            out.append({
                "sensor": names.get(sidx, "sensor_%d" % sidx),
                "reading": r_user or r_orig,
                "type": SENSOR_TYPES.get(etype, "unknown"),
                "value": value, "min": vmin, "max": vmax, "avg": vavg,
                "unit": unit,
            })
        return out
    except (struct.error, ValueError, IndexError):
        return None


class HwinfoReader:
    """打开 → refresh() → snapshot() 三步。

    拿不到就 ok=False，snapshot() 返回空 dict，主流程照常跑。
    """

    def __init__(self, name=SHM_NAME):
        self.name = name
        self.ok = False
        self.error = None
        self.poll_time = None
        self.readings = []
        self._handle = None
        self._view = None
        self._k32 = None

    def open(self):
        if sys.platform != "win32":
            self.error = "not windows"
            return False
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenFileMappingW.restype = ctypes.c_void_p
            k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
            k32.MapViewOfFile.restype = ctypes.c_void_p
            k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                          ctypes.c_uint32, ctypes.c_size_t]
            k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.VirtualQuery.restype = ctypes.c_size_t
            k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]

            h = k32.OpenFileMappingW(FILE_MAP_READ, 0, self.name)
            if not h:
                self.error = ("shared memory not found. "
                              "需要 HWiNFO64 以 -r%s 启动（或开启 Shared Memory Support），"
                              "并保持传感器窗口打开。" % self.name)
                return False
            view = k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
            if not view:
                k32.CloseHandle(h)
                self.error = "MapViewOfFile failed (err=%d)" % ctypes.get_last_error()
                return False
            self._handle, self._view, self._k32 = h, view, k32
            self.ok = True
            self.error = None
            return True
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            return False

    def _region_size(self):
        mbi = _MBI()
        if self._k32.VirtualQuery(self._view, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            return 0
        return int(mbi.RegionSize)

    def refresh(self):
        """重新解析一次共享内存，返回读数条数。"""
        if not self.ok:
            return 0
        try:
            size = self._region_size()
            if size < HEADER_SIZE:
                self.error = "mapped region too small (%d)" % size
                return 0
            read_size = min(size, MAX_TOTAL_SIZE)
            buf = bytes((ctypes.c_char * read_size).from_address(self._view))
            self.poll_time = struct.unpack_from("<I", buf, 0x2C)[0]
            parsed = parse_buffer(buf)
            if parsed is None:
                self.error = "unrecognised shared memory layout (HWiNFO version mismatch?)"
                self.readings = []
                return 0
            self.readings = parsed
            self.error = None
            return len(parsed)
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            return 0

    # ------------------------------------------------------------ 查询
    def find(self, *needles, stype=None):
        low = [n.lower() for n in needles]
        for r in self.readings:
            if stype and r["type"] != stype:
                continue
            if any(n in r["reading"].lower() for n in low):
                return r
        return None

    def snapshot(self):
        """整理成与 NVML 同名的字段，便于合并。取不到就不出现这个 key。"""
        out = {}
        if not self.ok or not self.readings:
            return out

        def take(key, needles, stype, convert=None):
            for n in needles:
                r = self.find(n, stype=stype)
                if r is not None:
                    v = r["value"]
                    try:
                        out[key] = convert(v, r["unit"]) if convert else v
                    except (TypeError, ValueError):
                        pass
                    return

        # 温度（°C，HWiNFO 已经是十进制度）
        take("temp_c", ("GPU Temperature", "GPU Temp", "GPU D3D Temperature"), "temperature")
        take("hotspot_c", ("GPU Hot Spot Temperature", "Hot Spot"), "temperature")
        take("mem_junction_c", ("GPU Memory Junction Temperature", "Memory Junction"), "temperature")
        take("vrm_c", ("GPU VRM Temperature", "VRM"), "temperature")
        # 功耗
        take("power_w", ("GPU Power", "GPU Package Power"), "power")
        take("power_limit_w", ("GPU Power Limit", "Power Limit"), "power")
        # 频率 / 利用率
        take("clock_sm_mhz", ("GPU Core Clock", "GPU Clock"), "clock")
        take("clock_mem_mhz", ("GPU Memory Clock", "Memory Clock"), "clock")
        take("util_gpu_pct", ("GPU Core Load", "GPU Utilization"), "usage")
        take("util_mem_pct", ("GPU Memory Usage", "GPU Memory Allocation"), "usage")
        # 风扇：百分比与 RPM 分开取，避免把 RPM 当百分比
        fan_pct = self.find("GPU Fan", stype="fan")
        if fan_pct is not None and "%" in (fan_pct.get("unit") or ""):
            out["fan_pct"] = fan_pct["value"]
        fan_rpm = self.find("GPU Fan", stype="fan")
        if fan_rpm is not None and "rpm" in (fan_rpm.get("unit") or "").lower():
            out["fan_rpm"] = fan_rpm["value"]
        return out

    def close(self):
        try:
            if self._view:
                self._k32.UnmapViewOfFile(self._view)
            if self._handle:
                self._k32.CloseHandle(self._handle)
        except Exception:
            pass
        self._view = self._handle = None
        self.ok = False


def probe():
    """一次性探测：能用就返回已 refresh 的 reader，否则 None。"""
    try:
        r = HwinfoReader()
        if r.open() and r.refresh() > 0:
            return r
        r.close()
    except Exception:
        pass
    return None
