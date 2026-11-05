# -*- coding: utf-8 -*-
r"""单实例守卫 + 递归启动防御。

为什么需要这个
--------------
曾经出现过「双击 exe 后无限弹出新窗口」。根因是环境探测把 sys.executable
（打包后就是本 exe 自己）当成 Python 解释器去执行 -c "import torch"，
于是每探测一次就再启动一个自己，新进程又去探测，形成递归。

两道防线：
  1. 探测层已把自身从候选里排除（envscan.safe_python）；
  2. 这里再加进程级防御：同名进程数超过阈值就直接退出并写日志。
"""
from __future__ import annotations

import os
import sys

# PyInstaller 单文件模式下 1 个应用 = 2 个进程（bootloader 父 + 应用子），
# 所以阈值按进程数给到 6（约 3 个应用），既能拦住失控递归，又不误伤正常多开。
MAX_INSTANCES = 6


def count_siblings():
    """数一数同名进程有几个（含自己）。失败返回 0。"""
    try:
        name = os.path.basename(sys.executable or "").lower()
        if not name.endswith(".exe"):
            return 0
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x00000002

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD),
                        ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == -1:
            return 0
        entry = PE32()
        entry.dwSize = ctypes.sizeof(PE32)
        n = 0
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if (entry.szExeFile or "").lower() == name:
                n += 1
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        k32.CloseHandle(snap)
        return n
    except Exception:
        return 0


def guard():
    """启动前调用。返回 (ok, reason)。"""
    n = count_siblings()
    if n > MAX_INSTANCES:
        return False, "too many instances: %d > %d" % (n, MAX_INSTANCES)
    return True, "ok"
