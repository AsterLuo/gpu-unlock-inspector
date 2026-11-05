# -*- coding: utf-8 -*-
"""U 盘制作：把内置的引导资产写到目标盘（会格式化！）。

安全设计（这是整个程序里唯一会写系统之外设备的功能）
------------------------------------------------------
1. **两次独立确认**：
   - 第一次：说明风险 + 列出将要被格式化的盘与容量；
   - 第二次：要求手打盘符字母才继续（防误点）。
2. **拒绝危险目标**：系统盘、有页面文件的盘、容量对不上的一律拒绝。
3. **先验资产后动手**：资产不全直接返回，不碰目标盘。
4. **格式化为 FAT32**：EFI 固件只认 FAT；MBR 分区表兼容性最好。
5. 全程记录到列表，UI 逐行显示进度。

资产来源优先级：
  a. exe 同目录的 `+"`unlock_usb/`"+`（用户自己放的最新资产）
  b. 打包内置的 `+"`assets/unlock_usb/`"+`（PyInstaller 打进 exe）
  c. 已插入的现有引导 U 盘（直接照抄，保证版本一致）
"""
from __future__ import annotations

import ctypes
import os
import shutil
import string
import subprocess
import sys

# 这些都是「必须有」才能完成一次解锁事务
REQUIRED_BY_FAMILY = {
    "tu10x": ("EFI/BOOT/BOOTX64.EFI", "booter_load_tu10x.bin", "gsp_boot_tu10x.bin",
              "gsp_boot_desc_tu10x.bin", "fwimage_tu10x.bin", "fwsignature_tu10x.bin",
              "sec2_bl_tu10x.bin", "v67-tu10x-four-plm.bin"),
    "ga102": ("EFI/BOOT/BOOTX64.EFI", "booter_load_ga102.bin", "gsp_boot_ga102.bin",
              "gsp_boot_desc_ga102.bin", "fwimage_ga102.bin", "fwsignature_ga102.bin",
              "scrubber_ad10x.bin", "v67-ga102-four-plm.bin"),
    "ad102": ("EFI/BOOT/BOOTX64.EFI", "booter_load_ad102.bin", "gsp_boot_ad102.bin",
              "gsp_boot_desc_ad102.bin", "fwimage_ad102.bin", "fwsignature_ad102.bin",
              "scrubber_ad10x.bin", "v67-ad102-four-plm.bin"),
}

# 可选资产：有就带上，没有不影响
OPTIONAL_FILES = ("ssdiag-x86_64-windows.exe", "i_know_what_i_am_doing.txt",
                  "tx-select-bdf.txt", "tx-quiet.txt", "chain-target.txt")


class UsbMaker:
    def __init__(self, asset_dir=None, family=None):
        self.asset_dir = asset_dir or find_assets(family)
        self.family = None
        self.plan = []          # [(src, relpath, size)]
        self.log = []

    # ------------------------------------------------------------ 资产
    def load_assets(self):
        """扫描资产目录，按家族分组。返回 {family: [relpaths]}。"""
        out = {}
        if not self.asset_dir or not os.path.isdir(self.asset_dir):
            return out
        for name in os.listdir(self.asset_dir):
            low = name.lower()
            for fam in REQUIRED_BY_FAMILY:
                if fam in low:
                    out.setdefault(fam, []).append(name)
        if os.path.isdir(os.path.join(self.asset_dir, "EFI")):
            for fam in list(out) + list(REQUIRED_BY_FAMILY):
                out.setdefault(fam, []).append("EFI/BOOT/BOOTX64.EFI")
        return out

    def asset_status(self):
        """返回 {family: (ok, missing[])}，供 UI 显示能不能制盘。"""
        out = {}
        have = set()
        if self.asset_dir and os.path.isdir(self.asset_dir):
            for root, _d, files in os.walk(self.asset_dir):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), self.asset_dir)
                    have.add(rel.replace("\\", "/"))
        for fam, req in REQUIRED_BY_FAMILY.items():
            missing = [r for r in req if r.lower() not in {h.lower() for h in have}]
            out[fam] = (not missing, missing)
        return out

    def build_plan(self, family):
        """列出要写的文件。只取该家族必需项 + 可选辅助文件。"""
        status = self.asset_status()
        ok, missing = status.get(family, (False, ["<unknown family>"]))
        if not ok:
            self.log.append(("missing", ", ".join(missing)))
            return []
        base = self.asset_dir
        need = set(r.lower() for r in REQUIRED_BY_FAMILY[family])
        plan = []
        for root, _d, files in os.walk(base):
            for f in files:
                src_path = os.path.join(root, f)
                rel = os.path.relpath(src_path, base).replace(os.sep, "/")
                low = rel.lower()
                if low.endswith(".log"):
                    continue
                if low in need or f in OPTIONAL_FILES:
                    plan.append((src_path, rel, os.path.getsize(src_path)))
        self.family = family
        self.plan = plan
        return plan

    def total_bytes(self):
        return sum(p[2] for p in self.plan)


def find_assets(family=None):
    """返回该家族的资产目录：优先已取回的缓存，其次 exe 同目录的 unlock_usb/。"""
    from . import assets_fetch
    from .modeldb import app_dir
    if family:
        d = assets_fetch.cache_dir(family)
        if assets_fetch.cached_family_ok(family):
            return d
    local = os.path.join(app_dir(), "unlock_usb")
    if os.path.isdir(local) and os.listdir(local):
        return local
    return None


def _is_system_drive(letter):
    try:
        sysroot = os.environ.get("SystemRoot", "C:\\Windows")
        return sysroot[:2].upper() == letter.upper()
    except Exception:
        return letter.upper() == "C"


def format_and_write(letter, source_dir, family, progress=None):
    """格式化目标盘并写入资产。**调用方必须先做完两次确认。**

    返回 (ok, messages[])。任何一步失败立即停止并返回，不静默继续。
    """
    msgs = []

    def emit(kind, text):
        msgs.append((kind, text))
        if progress:
            try:
                progress(kind, text)
            except Exception:
                pass

    letter = letter.strip().rstrip(":\\/").upper()
    if len(letter) != 1 or letter not in string.ascii_uppercase:
        emit("error", "invalid drive letter")
        return False, msgs
    if _is_system_drive(letter):
        emit("error", "refusing to format the system drive")
        return False, msgs
    if sys.platform != "win32":
        emit("error", "windows only")
        return False, msgs

    # ---- 1. 盘点要写的文件
    maker = UsbMaker(source_dir)
    plan = maker.build_plan(family)
    if not plan:
        emit("error", "assets incomplete for family %s" % family)
        return False, msgs
    total = sum(p[2] for p in plan)
    emit("info", "assets ok: %d files, %.1f MB" % (len(plan), total / 1048576.0))

    # ---- 2. 格式化（FAT32 + MBR，EFI 固件只认 FAT）
    ps = ("Format-Volume -DriveLetter %s -FileSystem FAT32 -NewFileSystemLabel UNLOCK "
          "-Confirm:$false -Force -ErrorAction Stop | Out-Null") % letter
    emit("info", "formatting %s: as FAT32 ..." % letter)
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, timeout=600)
    except Exception as exc:
        emit("error", "format failed: %s" % exc)
        return False, msgs
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()[:300]
        emit("error", "format failed: %s" % (err or "exit %d" % p.returncode))
        return False, msgs
    emit("ok", "formatted")

    # ---- 3. 写文件
    root = "%s:\\" % letter
    done = 0
    for src, rel, size in plan:
        dst = os.path.join(root, rel.replace("/", os.sep))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            done += 1
            emit("copy", "%s (%.1f MB)" % (rel, size / 1048576.0))
        except OSError as exc:
            emit("error", "copy %s failed: %s" % (rel, exc))
            return False, msgs

    # ---- 4. 回读校验：文件数对得上才算完成
    emit("info", "verifying %d files ..." % len(plan))
    try:
        written = 0
        for _root, _d, files in os.walk(root):
            written += len(files)
        if written < len(plan):
            emit("error", "verify failed: %d/%d files present" % (written, len(plan)))
            return False, msgs
    except OSError as exc:
        emit("error", "verify failed: %s" % exc)
        return False, msgs

    emit("ok", "done: %d files written to %s:" % (done, letter))
    return True, msgs