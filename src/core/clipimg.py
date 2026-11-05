# -*- coding: utf-8 -*-
"""把界面上的一个矩形区域抓成位图，直接送进 Windows 剪贴板。

为什么不用 Pillow
-----------------
1. Pillow 的 ImageGrab 只负责「抓」，写剪贴板还得靠 pywin32 / 自己调 Win32；
2. 单文件 exe 里多一个 5 MB 的依赖只为这件事不划算；
3. 纯 ctypes 的 BitBlt + SetClipboardData 在 Win7 以上完全够用，且不引入新依赖。

剪贴板放的是 **DIB**（CF_DIB = 8）：BITMAPINFOHEADER + 自底向上的 24 位 BGR 像素。
这是从 Win95 起就没变过的格式，画图 / QQ / 微信 / Word 都能直接粘贴。

失败一律返回 (False, 原因)，不抛异常 —— 抓图失败不该让整个程序崩掉。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import zlib

CF_DIB = 8
GMEM_MOVEABLE = 0x0002
SRCCOPY = 0x00CC0020


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


def available():
    return sys.platform == "win32"


# --------------------------------------------------------------------- 抓屏
def grab_bgra(x, y, w, h):
    """抓屏幕矩形，返回 (bgra_bytes, w, h)。像素自上而下、每像素 4 字节 BGRA。"""
    if not available():
        raise OSError("windows only")
    w = int(w)
    h = int(h)
    if w <= 0 or h <= 0:
        raise ValueError("empty rect: %dx%d" % (w, h))

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    src = user32.GetDC(0)
    if not src:
        raise OSError("GetDC failed")
    mem = gdi32.CreateCompatibleDC(src)
    if not mem:
        user32.ReleaseDC(0, src)
        raise OSError("CreateCompatibleDC failed")
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    if not bmp:
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, src)
        raise OSError("CreateCompatibleBitmap failed")
    try:
        gdi32.SelectObject(mem, bmp)
        if not gdi32.BitBlt(mem, 0, 0, w, h, src, int(x), int(y), SRCCOPY):
            raise OSError("BitBlt failed")

        bi = _BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h            # 负数 = 自上而下，方便后面直接按行处理
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        bi.biSizeImage = w * h * 4
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if got == 0:
            raise OSError("GetDIBits failed")
        return buf.raw, w, h
    finally:
        try:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(0, src)
        except Exception:
            pass


# --------------------------------------------------------------------- 编码
def to_dib(bgra, w, h):
    """转成剪贴板要的 DIB：24 位、自底向上、行按 4 字节对齐。"""
    stride_src = w * 4
    stride_dst = ((w * 3) + 3) & ~3
    rows = []
    for y in range(h - 1, -1, -1):          # 自底向上
        line = bgra[y * stride_src:(y + 1) * stride_src]
        out = bytearray(stride_dst)
        for x in range(w):
            i = x * 4
            o = x * 3
            out[o] = line[i]        # B
            out[o + 1] = line[i + 1]  # G
            out[o + 2] = line[i + 2]  # R
        rows.append(bytes(out))
    pixels = b"".join(rows)

    header = struct.pack("<IiiHHIIiiII",
                         ctypes.sizeof(_BITMAPINFOHEADER),
                         w, h, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    return header + pixels


def to_png(bgra, w, h):
    """顺手存一份 PNG（无第三方依赖），方便留档。"""
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        line = bgra[y * stride:(y + 1) * stride]
        row = bytearray(w * 3)
        for x in range(w):
            i = x * 4
            row[x * 3] = line[i + 2]
            row[x * 3 + 1] = line[i + 1]
            row[x * 3 + 2] = line[i]
        raw += row

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    return png


# --------------------------------------------------------------------- 剪贴板
def set_clipboard_dib(dib_bytes):
    """把 DIB 放到剪贴板。返回 (ok, reason)。"""
    if not available():
        return False, "windows only"
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HANDLE
    kernel32.GlobalLock.argtypes = [wt.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wt.HANDLE]
    kernel32.GlobalFree.argtypes = [wt.HANDLE]

    if not user32.OpenClipboard(None):
        return False, "OpenClipboard failed"
    try:
        user32.EmptyClipboard()
        size = len(dib_bytes)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False, "GlobalAlloc failed"
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False, "GlobalLock failed"
        try:
            ctypes.memmove(ptr, dib_bytes, size)
        finally:
            kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(handle)
            return False, "SetClipboardData failed"
        # 成功后所有权归系统，不能再 GlobalFree
        return True, "ok"
    finally:
        user32.CloseClipboard()


def copy_rect_to_clipboard(x, y, w, h, png_path=None):
    """抓屏幕矩形 → 剪贴板。可选同时写一份 PNG。返回 (ok, info)。"""
    try:
        bgra, gw, gh = grab_bgra(x, y, w, h)
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    try:
        dib = to_dib(bgra, gw, gh)
    except Exception as exc:
        return False, "encode DIB failed: %s" % exc
    ok, why = set_clipboard_dib(dib)
    if not ok:
        return False, why
    saved = None
    if png_path:
        try:
            with open(png_path, "wb") as fh:
                fh.write(to_png(bgra, gw, gh))
            saved = png_path
        except OSError:
            saved = None
    return True, {"size": (gw, gh), "png": saved, "bytes": len(dib)}
