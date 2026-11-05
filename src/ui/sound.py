# -*- coding: utf-8 -*-
"""交互音效：极短、极轻、可关。

为什么是「生成」而不是「加载 wav」
-----------------------------------
1. 单文件 exe 里每多一个资源文件都要进 PyInstaller 的 datas，容易漏；
2. 4 个音效加起来不到 4 KB 的波形，现场算比解压还快；
3. 不引入任何音频库 —— 用 Win32 的 PlaySound(内存 WAV) 一行搞定。

音效设计参数（与 theme.SOUND 对应）
-----------------------------------
  click    40ms / -15 dB  按钮按下，方波+快速衰减，像指甲敲桌面
  success 120ms / -13 dB  操作成功，两声上行小三度（C5→E5）
  error   160ms / -14 dB  操作失败，单声下行（A4→F4），不刺耳

为什么音量定在 0.18~0.22
------------------------
系统音量 50% 时，0.2 的峰值大约 65 dB SPL，属于「能听见但不打断」的区间。
超过 0.35 在安静环境里会明显打扰，低于 0.08 则笔记本扬声器基本听不到。

降级策略
--------
- 设置里关掉 → 直接 return，一次系统调用都不发；
- Windows 上 PlaySound 失败（无声卡 / 服务被禁）→ 静默失败，绝不弹错；
- 音效永远不阻塞操作：异步标志 SND_ASYNC，调用立即返回。
"""
from __future__ import annotations

import math
import struct
import sys
import wave
import io

from core import theme as T

_available = None
_enabled = None      # None = 还没读过设置


def _winmm():
    try:
        import ctypes
        return ctypes.windll.winmm
    except Exception:
        return None


def available():
    """当前平台能不能发声。"""
    global _available
    if _available is None:
        _available = sys.platform == "win32" and _winmm() is not None
    return _available


def set_enabled(on):
    global _enabled
    _enabled = bool(on)


def enabled():
    if _enabled is None:
        return False        # 默认静音：程序启动时由设置决定，避免一开机就出声
    return bool(_enabled) and available()


def _tone(notes, ms, gain, decay=True):
    """合成一段 WAV 字节流。notes 是 [(频率, 占比)]，占比之和为 1。"""
    rate = 22050
    total = max(1, int(rate * ms / 1000.0))
    buf = bytearray()
    pos = 0
    for freq, share in notes:
        n = max(1, int(total * share))
        for i in range(n):
            # 正弦基频 + 少量二次谐波，听起来更接近「木质」而非电子音
            t = i / float(rate)
            env = 1.0
            if decay:
                env = math.exp(-5.0 * i / float(n))
            s = (math.sin(2 * math.pi * freq * t) * 0.8 +
                 math.sin(4 * math.pi * freq * t) * 0.2) * env * gain
            v = int(max(-1.0, min(1.0, s)) * 32767)
            buf += struct.pack("<h", v)
        pos += n
    # 不足的部分用静音补齐（多音符时按占比分配一般不会不足）
    while pos < total:
        buf += struct.pack("<h", 0)
        pos += 1

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return out.getvalue()


_NOTES = {
    # 名称: (音符表, 缓存)
    "click": ([(1180, 1.0)], None),
    "success": ([(523, 0.45), (659, 0.55)], None),
    "error": ([(440, 0.5), (349, 0.5)], None),
}


def _wav(name):
    entry = _NOTES.get(name)
    if not entry:
        return None
    notes, cached = entry
    if cached is None:
        cfg = T.SOUND.get(name, {"ms": 100, "gain": 0.2})
        cached = _tone(notes, cfg["ms"], cfg["gain"])
        _NOTES[name] = (notes, cached)
    return cached


def play(name):
    """播放一个交互音效。失败静默返回 False。"""
    if not enabled():
        return False
    data = _wav(name)
    if not data:
        return False
    m = _winmm()
    if m is None:
        return False
    try:
        import ctypes
        # SND_MEMORY=0x4 | SND_ASYNC=0x1 | SND_NODEFAULT=0x2
        flags = 0x0004 | 0x0001 | 0x0002
        ok = m.PlaySound(ctypes.create_string_buffer(data, len(data)),
                         0, flags)
        return bool(ok)
    except Exception:
        return False


def stop():
    m = _winmm()
    if m is not None:
        try:
            m.PlaySound(None, 0, 0)
        except Exception:
            pass
