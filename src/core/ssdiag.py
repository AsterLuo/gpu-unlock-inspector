# -*- coding: utf-8 -*-
"""可选深度诊断：调用外部 ssdiag（nv-uefi-unlocker 项目）读取 FEAT 寄存器。

⚠️ 重要边界（写进 README，也在 UI 上明说）：
  1. ssdiag 需要**管理员权限**：它要在探针期间临时加载一个 Intel 签名的内核驱动
     pmxdrv.sys，SCM 会记录事件 7045，退出时停止并删除该服务。
  2. 因此它**违反**本工具的默认约束（非管理员 + 只读 + 不装驱动）。
  3. 所以我们把它做成「显式可选」：默认绝不调用；只有用户点「深度诊断」并且
     同意风险时才跑，跑完立刻恢复原状。
  4. 找不到 exe、没有权限、输出格式不认识 —— 一律返回 unknown，绝不编造。

判定规则来自该项目文档：
    FEAT SS0 == 0x88888888 且 SS1 == 0x8  → UNLOCKED（Tensor/PLM 维度）
注意这个 UNLOCKED 与 LHR 完全不同：它解锁的是张量计算性能，不是挖矿算力。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000

# ssdiag 的常见落点（用户从 nv-uefi-unlocker 的 USB 包里拿到的就是这个文件）
CANDIDATE_NAMES = ("ssdiag-x86_64-windows.exe", "ssdiag.exe")
CANDIDATE_DIRS = (
    ".",
    "gpu-unlock-kit/pkg",
    "gpu-unlock-kit/USB-STICK",
    "usbroot",
    os.path.join("..", "gpu-unlock-kit", "pkg"),
)


def find_ssdiag(app_dir, resource_root):
    """在几个已知位置找 ssdiag；找不到返回 None。"""
    for root in (app_dir, resource_root, os.path.join(app_dir, "tools")):
        for d in CANDIDATE_DIRS:
            for name in CANDIDATE_NAMES:
                p = os.path.abspath(os.path.join(root, d, name))
                if os.path.isfile(p):
                    return p
    return None


def _no_window():
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = CREATE_NO_WINDOW
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return kw


class DeepProbe:
    """一次深度诊断的结果。"""

    def __init__(self):
        self.available = False      # 找到 exe 了吗
        self.ran = False            # 真的跑了吗
        self.admin = False
        self.state = "unknown"      # unlocked / locked / unknown
        self.ss0 = None
        self.ss1 = None
        self.raw = ""
        self.error = None
        self.platform = {}          # 平台门禁信息（Secure Boot / HVCI / 黑名单）

    def to_dict(self):
        return {
            "available": self.available, "ran": self.ran, "admin": self.admin,
            "state": self.state, "ss0": self.ss0, "ss1": self.ss1,
            "platform": self.platform, "error": self.error,
        }


def parse_output(text):
    """从 ssdiag 输出里抽 SS0 / SS1 与判定。格式不认识就返回 unknown。"""
    res = {"ss0": None, "ss1": None, "state": "unknown", "platform": {}}

    m = re.search(r"SS0\s*[=:]\s*(0x[0-9a-fA-F]+|\d+)", text)
    if m:
        try:
            res["ss0"] = int(m.group(1), 0)
        except ValueError:
            pass
    m = re.search(r"SS1\s*[=:]\s*(0x[0-9a-fA-F]+|\d+)", text)
    if m:
        try:
            res["ss1"] = int(m.group(1), 0)
        except ValueError:
            pass

    low = text.lower()
    if "unlocked" in low and "not_unlocked" not in low.replace("not_unlocked", ""):
        pass
    # 明确的三态判定词优先
    if re.search(r"\bnot[_\s-]?unlocked\b|\blocked\b|\bstill locked\b", low):
        res["state"] = "locked"
    elif re.search(r"\bunlocked\b", low):
        res["state"] = "unlocked"
    elif res["ss0"] is not None and res["ss1"] is not None:
        # 文档规则：SS0==0x88888888 && SS1==0x8 → UNLOCKED
        res["state"] = "unlocked" if (res["ss0"] == 0x88888888 and res["ss1"] == 0x8) else "locked"

    for key, pat in (("secure_boot", r"Secure Boot\s*=\s*(\w+)"),
                     ("hvci", r"HVCI\)\s*=\s*([\w()\- ]+)"),
                     ("blocklist", r"blocklist\s*=\s*(\w+)")):
        mm = re.search(pat, text, re.I)
        if mm:
            res["platform"][key] = mm.group(1).strip()
    return res


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run(app_dir, resource_root, timeout=90):
    """执行一次深度诊断。任何失败都以 DeepProbe.error 返回，不抛异常。"""
    p = DeepProbe()
    exe = find_ssdiag(app_dir, resource_root)
    p.admin = is_admin()
    if not exe:
        p.error = "ssdiag_not_found"
        return p
    p.available = True

    # 先跑 --dry-run 拿平台门禁信息（不需要管理员）
    try:
        r = subprocess.run([exe, "--dry-run"], capture_output=True, timeout=30,
                           **_no_window())
        p.raw = (r.stdout or b"").decode("utf-8", "replace")
        parsed = parse_output(p.raw)
        p.platform = parsed["platform"]
    except Exception as exc:
        p.error = "dryrun_failed: %s" % exc
        return p

    if not p.admin:
        p.error = "admin_required"
        return p

    # 真正扫描（会临时装驱动；ssdiag 自己保证退出时卸载）
    try:
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        r = subprocess.run([exe], capture_output=True, timeout=timeout, env=env,
                           **_no_window())
        out = (r.stdout or b"").decode("utf-8", "replace")
        p.raw = out
        parsed = parse_output(out)
        p.ss0, p.ss1 = parsed["ss0"], parsed["ss1"]
        p.state = parsed["state"]
        p.ran = True
        if r.returncode != 0 and not out.strip():
            p.error = "ssdiag_exit_%d" % r.returncode
    except subprocess.TimeoutExpired:
        p.error = "ssdiag_timeout"
    except Exception as exc:
        p.error = "%s: %s" % (type(exc).__name__, exc)
    return p
