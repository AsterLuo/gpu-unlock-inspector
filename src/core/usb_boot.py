# -*- coding: utf-8 -*-
"""U 盘识别 + loader.log 解析。

一、找 U 盘（只读，不执行 U 盘上任何东西）
最快的判据顺序，越靠前越便宜：
  a. 卷文件系统 == FAT32/FAT16  —— EFI 固件只认 FAT，这是硬约束
  b. 根目录存在 \\EFI\\BOOT\\BOOTX64.EFI —— 这就是「检测 efi」那一步
  c. 根目录存在 nv-uefi-unlocker 的家族资产（booter_load_*.bin / v67-*.bin 等）

二、解析 loader.log
该项目每次引导后把结果写回 U 盘根目录的 loader.log，其中有一行机器可读的最终结论：
  HANDOFF-OUT: ss0=0x88888888 ss1=0x8 readout1=0x00000000 feat_plm=0xffffffff ...
               ss0_unlock=true ss1_unlock=true plm_open=true
这行是「解锁是否成功」的权威答案，优先级高于任何推断。

三、判定事实（来自项目文档与真实日志）
- 锁在 FEAT_OVR_PLM (0x00823804) 门 + SS0/SS1 (0x0082381c / 0x823820) 覆盖寄存器上。
- PLM 从 0xffffff8f（仅 L3 可写）开到 0xffffffff 才允许写 selector。
- 写入 SS0=0x88888888, SS1=0x8 后九路速率选择器回到全速。
- 锁定态只读签名：RTX 30 系 FEAT_READOUT_1 = 0x00400080；解锁后归零 0x00000000。
- 值落在 always-on 岛：FLR 后仍存活，断电即失 —— 所以每次开机都要重新引导解锁。
"""
from __future__ import annotations

import os
import re
import string
import sys

FAMILY_ASSET_PATTERNS = (
    re.compile(r"^booter_load_(tu10x|ga102|ad102)\.bin$", re.I),
    re.compile(r"^fwimage_(tu10x|ga10x|ga102|ad10x)\.bin$", re.I),
    re.compile(r"^fwsignature_(tu10x|ga10x|ga102|ad10x)\.bin$", re.I),
    re.compile(r"^gsp_boot_(tu10x|ga10x|ga102|ad10x)\.bin$", re.I),
    re.compile(r"^scrubber_ad10x\.bin$", re.I),
    re.compile(r"^v67-.*\.bin$", re.I),
    re.compile(r"^sec2_bl_tu10x\.bin$", re.I),
)

FAT_TYPES = ("FAT32", "FAT16", "FAT", "FAT12")

PLM_OPEN_VALUE = 0xFFFFFFFF
PLM_LOCKED_VALUE = 0xFFFFFF8F
SS0_UNLOCK_VALUE = 0x88888888
SS1_UNLOCK_VALUE = 0x8
READOUT1_LOCKED = 0x00400080
READOUT1_UNLOCKED = 0x00000000


def _volumes():
    """返回 [{letter, root, fs, label, removable}]。失败返回空列表。"""
    out = []
    if sys.platform != "win32":
        return out
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        DRIVE_REMOVABLE, DRIVE_FIXED = 2, 3
        k32.GetLogicalDrives.restype = wintypes.DWORD
        mask = k32.GetLogicalDrives()
        k32.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k32.GetDriveTypeW.restype = wintypes.UINT
        for i, letter in enumerate(string.ascii_uppercase):
            if not (mask & (1 << i)):
                continue
            root = "%s:\\" % letter
            dtype = k32.GetDriveTypeW(root)
            if dtype not in (DRIVE_REMOVABLE, DRIVE_FIXED):
                continue
            fs_buf = ctypes.create_unicode_buffer(64)
            label_buf = ctypes.create_unicode_buffer(261)
            serial = wintypes.DWORD()
            maxlen = wintypes.DWORD()
            flags = wintypes.DWORD()
            ok = k32.GetVolumeInformationW(root, label_buf, 261, ctypes.byref(serial),
                                           ctypes.byref(maxlen), ctypes.byref(flags),
                                           fs_buf, 64)
            out.append({"letter": letter, "root": root,
                        "fs": fs_buf.value if ok else "",
                        "label": label_buf.value if ok else "",
                        "removable": dtype == DRIVE_REMOVABLE})
    except Exception:
        for letter in string.ascii_uppercase:
            root = "%s:\\" % letter
            if os.path.isdir(root):
                out.append({"letter": letter, "root": root, "fs": "",
                            "label": "", "removable": False})
    return out


def _listdir(path):
    try:
        return os.listdir(path)
    except (OSError, PermissionError):
        return []


def inspect_root(root):
    """检查一个卷根目录是不是 nv-uefi-unlocker 的引导盘。只读。"""
    info = {"efi": False, "efi_path": None, "families": [], "assets": [],
            "tx_build": False, "log": None, "log_size": None, "markers": []}
    for rel in ("EFI\\BOOT\\BOOTX64.EFI", "EFI/boot/bootx64.efi", "efi/BOOT/BOOTX64.EFI"):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            info["efi"] = True
            info["efi_path"] = p
            break
    names = _listdir(root)
    fams = set()
    for name in names:
        low = name.lower()
        for pat in FAMILY_ASSET_PATTERNS:
            m = pat.match(name)
            if m:
                info["assets"].append(name)
                if m.groups():
                    fams.add(m.group(1).lower())
                break
        if low in ("i_know_what_i_am_doing.txt", "tx-select-bdf.txt",
                   "chain-target.txt", "tx-quiet.txt", "tx-require-d0.txt"):
            info["markers"].append(name)
        if low.startswith("v67-"):
            info["tx_build"] = True
    info["families"] = sorted(fams)
    for cand in ("loader.log", "LOADER.LOG"):
        p = os.path.join(root, cand)
        if os.path.isfile(p):
            info["log"] = p
            try:
                info["log_size"] = os.path.getsize(p)
            except OSError:
                pass
            break
    return info


def scan(removable_only=False):
    """扫描全机卷，按「像不像解锁引导盘」排序。"""
    results = []
    for vol in _volumes():
        info = inspect_root(vol["root"])
        score = 0
        if info["efi"]:
            score += 4
        if info["families"] or info["assets"]:
            score += 2
        if info["log"]:
            score += 1
        if vol["removable"]:
            score += 1
        if vol["fs"].upper() in FAT_TYPES:
            score += 1
        is_unlocker = bool(info["efi"] and (info["assets"] or info["log"]))
        entry = dict(vol)
        entry.update(info)
        entry["score"] = score
        entry["is_unlocker"] = is_unlocker
        results.append(entry)
    results.sort(key=lambda v: (-int(v["is_unlocker"]), -v["score"], v["letter"]))
    if removable_only:
        results = [v for v in results if v["removable"] or v["is_unlocker"]]
    return results


_HANDOFF = re.compile(r"HANDOFF-OUT:\s*(.+)$", re.I)
_KV = re.compile(r"(\w+)=(\S+)")
_PLM_FLIP = re.compile(r"PLM flip\s+(0x[0-9a-f]+)\s*->\s*(0x[0-9a-f]+)", re.I)
_SELECTOR = re.compile(r"SELECTOR RESULT:\s*SS0=(0x[0-9a-f]+)\s+SS1=(0x[0-9a-f]+)", re.I)
_READOUT1 = re.compile(r"FEAT_READOUT_1\s*=\s*(0x[0-9a-f]+)", re.I)
_VERSION = re.compile(r"nv-uefi-unlock\w*\s+v([\d.]+)", re.I)
_BUILD = re.compile(r"built\s+(.+?)\s*\+", re.I)
# 只认「真的没解锁」的中止：banner 里那句 "ABORT; unlocks NOTHING" 是说明文字，不是事件
_ABORT = re.compile(
    r"(CONFIRM:\s*30s timeout[^\n]*|"
    r"keywait[^\n]*ABORT[^\n]*NOTHING was unlocked[^\n]*|"
    r"nothing was unlocked this boot[^\n]*)", re.I)
_PHASE = re.compile(r"PHASE:\s*(\w+)")
_TXMODE = re.compile(r"TX:\s*(V67 present[^\n]*|BEGIN[^\n]*)", re.I)
_FINAL = re.compile(r"FINAL:\s*([^\n]+)", re.I)
_GPU_DEV = re.compile(r"SAMPLE:\s*GPU\s+([0-9a-f]{4}:[0-9a-f]{4})\s*@\s*([0-9a-f:.]+)", re.I)


def _hex(v):
    try:
        return int(v, 16)
    except (TypeError, ValueError):
        return None


def parse_log(path, max_bytes=4 * 1024 * 1024):
    """解析 loader.log，返回结构化证据。只读，绝不执行 U 盘上的任何东西。"""
    out = {
        "path": path, "ok": False, "error": None,
        "version": None, "build": None,
        "tx_mode": False, "phases": [], "final": None, "abort": None,
        "plm_open": None, "plm_from": None, "plm_to": None,
        "ss0": None, "ss1": None, "ss0_unlock": None, "ss1_unlock": None,
        "readout1": None, "readout1_locked": None,
        "gpu_dev": None, "gpu_bdf": None, "handoff": {},
        "verdict": "unknown", "verdict_source": "none", "lines": 0,
    }
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            raw = fh.read()
        text = raw.decode("utf-8", "replace")
    except (OSError, PermissionError) as exc:
        out["error"] = str(exc)
        return out

    out["lines"] = text.count("\n") + 1

    m = _VERSION.search(text)
    if m:
        out["version"] = m.group(1)
    m = _BUILD.search(text)
    if m:
        out["build"] = m.group(1).strip()
    m = _TXMODE.search(text)
    if m:
        out["tx_mode"] = bool(re.search(r"V67 present|unlock mode", m.group(1), re.I))
    out["phases"] = [p.upper() for p in _PHASE.findall(text)]
    m = _FINAL.search(text)
    if m:
        out["final"] = m.group(1).strip()
    m = _ABORT.search(text)
    if m:
        out["abort"] = m.group(1).strip()[:200]
    m = _PLM_FLIP.search(text)
    if m:
        out["plm_from"] = _hex(m.group(1))
        out["plm_to"] = _hex(m.group(2))
    m = _SELECTOR.search(text)
    if m:
        out["ss0"] = _hex(m.group(1))
        out["ss1"] = _hex(m.group(2))
    m = _READOUT1.search(text)
    if m:
        out["readout1"] = _hex(m.group(1))
    m = _GPU_DEV.search(text)
    if m:
        out["gpu_dev"] = m.group(1).lower()
        out["gpu_bdf"] = m.group(2)

    # 优先取 tx_booter 的 "SELECTOR RESULT: SS0=.. SS1=.."（那是真正写入后的读回），
    # 而不是别处出现的 ss0=/ss1=（例如 "pre-FLR selectors: ss0=.. ss1=.." 同样是有效读回，
    # 但 TU10x 的 0xbadf5040 出现在 handoff 的 tu10x 段，不能被当成 RTX 的 selector 值）。
    _SEL_TX = re.compile(r"SELECTOR RESULT:\s*SS0=(0x[0-9a-f]+)\s+SS1=(0x[0-9a-f]+)", re.I)
    sel = _SEL_TX.findall(text)
    if sel:
        s0, s1 = sel[-1]
        out["ss0"], out["ss1"] = _hex(s0), _hex(s1)
        out["selector_from_tx"] = True

    # ---- 权威结论行
    # 注意：HANDOFF-OUT 用 " | " 分段，后面还跟着 tu10x 的 ss0/ss1（0xbadf5040）。
    # 必须先按 | 切段、只看主卡段，否则会被 tu10x 的值覆盖。
    handoff = None
    for line in text.splitlines():
        mm = _HANDOFF.search(line)
        if mm:
            segs = [s.strip() for s in mm.group(1).split("|")]
            kv = {}
            for seg in segs:
                # "tu10x: plm=.. ss0=.." 这一段是 Turing 的独立读数，混进来会覆盖主卡
                if seg.lower().startswith("tu10x:"):
                    continue
                for k, v in _KV.findall(seg):
                    kv.setdefault(k, v)
            handoff = kv
    if handoff:
        out["handoff"] = handoff
        # handoff 里的 ss0/ss1 是主卡（非 tu10x 段）的值，优先于其它来源
        if handoff.get("ss0", "").startswith("0x"):
            out["ss0"] = _hex(handoff["ss0"])
        if handoff.get("ss1", "").startswith("0x"):
            out["ss1"] = _hex(handoff["ss1"])
        if handoff.get("readout1", "").startswith("0x"):
            out["readout1"] = _hex(handoff["readout1"])
        if handoff.get("feat_plm", "").startswith("0x"):
            out["plm_to"] = _hex(handoff["feat_plm"])
        for k in ("ss0_unlock", "ss1_unlock", "plm_open"):
            if k in handoff:
                out[k] = handoff[k].lower() == "true"

    # ---- 结论推导：按证据强度排序，够不上就留 unknown
    flags = [out["plm_open"], out["ss0_unlock"], out["ss1_unlock"]]
    known = [f for f in flags if f is not None]
    if known and all(known):
        out["verdict"], out["verdict_source"] = "unlocked", "handoff"
    elif known and not any(known):
        out["verdict"], out["verdict_source"] = "locked", "handoff"

    if out["verdict"] == "unknown" and out["ss0"] is not None:
        if out["ss0"] == SS0_UNLOCK_VALUE and out["ss1"] == SS1_UNLOCK_VALUE:
            out["verdict"], out["verdict_source"] = "unlocked", "selector"

    if out["verdict"] == "unknown" and out["plm_to"] is not None:
        if out["plm_to"] == PLM_OPEN_VALUE:
            out["verdict"], out["verdict_source"] = "unlocked", "plm"
        elif out["plm_to"] == PLM_LOCKED_VALUE:
            out["verdict"], out["verdict_source"] = "locked", "plm"

    if out["readout1"] is not None:
        out["readout1_locked"] = out["readout1"] == READOUT1_LOCKED
        if out["verdict"] == "unknown":
            if out["readout1"] == READOUT1_UNLOCKED:
                out["verdict"], out["verdict_source"] = "unlocked", "readout"
            elif out["readout1"] == READOUT1_LOCKED:
                out["verdict"], out["verdict_source"] = "locked", "readout"

    if out["abort"] and "NOTHING was unlocked" in out["abort"]:
        out["verdict"], out["verdict_source"] = "locked", "abort"

    out["ok"] = bool(out["version"] or out["handoff"])
    return out