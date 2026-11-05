# -*- coding: utf-8 -*-
r"""扫描本机有没有能跑 TFLOPS 的 Python 环境（torch + CUDA）。

为什么要扫这么多地方
--------------------
用户机器上带 torch 的 Python 往往藏在第三方工具的私有目录里
（ComfyUI 的 python_embeded、sd-webui、conda env 等），
不在 PATH 上、也不在注册表里。只试 sys.executable 一定找不到。

扫描顺序（前面的优先）：
  1. 环境变量 GPUINV_TORCH_PY 指定的解释器
  2. 本程序自己装出来的 tools\torch-venv
  3. exe 旁边的 tools\python
  4. CONDA_PREFIX / VIRTUAL_ENV
  5. PATH 上的 python / python3
  6. 官方安装器默认位置 + 注册表 PythonCore
  7. 常见第三方发行版目录（ComfyUI / sd-webui 等）
  8. 当前进程的解释器

全部只读：只执行 python -c 打印版本与 cuda 可用性，不改任何东西。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

# 第三方 AI 工具常见的私有解释器位置（盘符 × 目录名 × 子路径）
THIRD_PARTY = (
    ("ComfyUI", "python_embeded"),
    ("ComfyUI", "python"),
    ("ComfyUI_windows_portable", "python_embeded"),
    ("ComfyUI_windows_portable", "python"),
    ("stable-diffusion-webui", "python"),
    ("sd-webui", "python"),
)


def _drive_roots():
    roots = []
    for letter in "CDEFGH":
        r = letter + ":\\"
        if os.path.isdir(r):
            roots.append(r)
    return roots


def _registry_pythons():
    out = []
    try:
        import winreg
        sub = r"SOFTWARE\Python\PythonCore"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    key = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                try:
                    count = winreg.QueryInfoKey(key)[0]
                    for i in range(count):
                        try:
                            tag = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, tag + r"\InstallPath") as ik:
                                path = winreg.QueryValue(ik, "")
                            if path:
                                out.append(os.path.join(path, "python.exe"))
                        except OSError:
                            continue
                finally:
                    key.Close()
    except Exception:
        pass
    return out



def _iter_program_dirs():
    """常见的软件安装根目录（第三方 AI 工具基本都落在这里）。"""
    roots = []
    for letter in "CDEFGH":
        drive = letter + ":\\"
        if not os.path.isdir(drive):
            continue
        roots.append(drive)
        for sub in ("Program Files", "Program Files (x86)", "Programs", "Apps",
                    "Tools", "AI", "opt"):
            p = os.path.join(drive, sub)
            if os.path.isdir(p):
                roots.append(p)
    for key in ("LOCALAPPDATA", "APPDATA", "ProgramData"):
        v = os.environ.get(key)
        if v and os.path.isdir(v):
            roots.append(v)
    return roots


# 目录名里出现这些词，就值得进去找 python
AI_NAME_HINTS = ("comfy", "stable-diffusion", "sd-webui", "webui", "invoke",
                 "fooocus", "kohya", "text-generation", "oobabooga", "llama",
                 "whisper", "rvc", "diffusion", "torch", "conda", "miniconda",
                 "anaconda", "venv", "python")


def _walk_for_python(root, max_depth=3, budget_s=6.0, found=None):
    """在 root 下有限深度地找 python.exe。命中 AI 相关目录名才深入。"""
    import time
    found = found if found is not None else []
    start = time.time()
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\").count(os.sep)

    for dirpath, dirnames, filenames in os.walk(root):
        if time.time() - start > budget_s:
            break
        depth = dirpath.rstrip("\\").count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        else:
            keep = []
            for d in dirnames:
                low = d.lower()
                if any(h in low for h in AI_NAME_HINTS):
                    keep.append(d)
                elif depth < 1:
                    keep.append(d)          # 第一层都进去看看
            dirnames[:] = keep
        for f in filenames:
            if f.lower() in ("python.exe", "python3.exe"):
                found.append(os.path.join(dirpath, f))
    return found


def _discover_third_party(deep=True, budget_s=12.0):
    """扫描常见安装根目录，找出第三方 AI 工具自带的 python。"""
    import time
    out = []
    seen = set()
    start = time.time()
    for root in _iter_program_dirs():
        if time.time() - start > budget_s:
            break
        try:
            entries = os.listdir(root)
        except (OSError, PermissionError):
            continue
        for name in entries:
            if time.time() - start > budget_s:
                break
            low = name.lower()
            full = os.path.join(root, name)
            if not os.path.isdir(full) or full in seen:
                continue
            # 名字像 AI 工具、或本身叫 python*，就进去找；其余跳过（避免全盘遍历）
            if not any(h in low for h in AI_NAME_HINTS) and not low.startswith("python"):
                continue
            seen.add(full)
            # 先试最常见的两种布局，命中就不必遍历
            for sub in ("python\\python.exe", "python_embeded\\python.exe",
                        "venv\\Scripts\\python.exe", ".venv\\Scripts\\python.exe",
                        "env\\Scripts\\python.exe", "python3\\python.exe"):
                cand = os.path.join(full, sub)
                if os.path.isfile(cand):
                    out.append(cand)
            # 再有限深度扫一层（应对 some-runtime_v1.0\python 这种带版本号的目录）
            if not out or all(not c.startswith(full) for c in out):
                out.extend(_walk_for_python(full, max_depth=3, budget_s=3.0))
    return out

def candidate_pythons(extra=(), deep=False):
    """列出候选解释器（仅存在性过滤，不执行）。"""
    cands = list(extra)

    env = os.environ.get("GPUINV_TORCH_PY")
    if env:
        cands.insert(0, env)

    from .modeldb import app_dir
    cands.append(os.path.join(app_dir(), "tools", "torch-venv", "Scripts", "python.exe"))
    cands.append(os.path.join(app_dir(), "tools", "python", "python.exe"))

    for key in ("CONDA_PREFIX", "VIRTUAL_ENV"):
        root = os.environ.get(key)
        if root:
            cands.append(os.path.join(root, "python.exe"))

    for name in ("python.exe", "python3.exe"):
        got = shutil.which(name)
        if got:
            cands.append(got)

    local = os.environ.get("LOCALAPPDATA")
    if local:
        for ver in ("313", "312", "311", "310"):
            cands.append(os.path.join(local, "Programs", "Python",
                                      "Python" + ver, "python.exe"))
    for ver in ("313", "312", "311", "310"):
        cands.append("C:\\Python" + ver + "\\python.exe")

    cands.extend(_registry_pythons())

    if deep:
        cands.extend(_discover_third_party(deep=True))

    cands.append(sys.executable)

    seen = set()
    out = []
    for c in cands:
        if not c:
            continue
        try:
            c = os.path.abspath(os.path.expandvars(c))
        except Exception:
            continue
        if c in seen:
            continue
        # 关键：打包后 sys.executable 是本 exe，绝不能当解释器去执行，
        # 否则会递归启动自己（无限弹窗）。
        if not safe_python(c):
            continue
        seen.add(c)
        out.append(c)
    return out


def probe(exe, timeout=25):
    """探测一个解释器。返回 {exe, has_torch, version, cuda, usable, note}。"""
    item = {"exe": exe, "has_torch": False, "version": None, "cuda": False,
            "usable": False, "note": None}
    try:
        p = subprocess.run(
            [exe, "-c",
             "import torch;print(torch.__version__);print(torch.cuda.is_available())"],
            capture_output=True, timeout=timeout,
            creationflags=(0x08000000 if sys.platform == "win32" else 0))
    except Exception as exc:
        item["note"] = str(exc)[:160]
        return item
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    err = (p.stderr or b"").decode("utf-8", "replace").strip()
    if p.returncode != 0 or not out:
        item["note"] = (err.splitlines()[-1] if err else "no torch")[:160]
        return item
    lines = out.splitlines()
    item["has_torch"] = True
    item["version"] = lines[0].strip() if lines else None
    item["cuda"] = len(lines) > 1 and lines[1].strip().lower() == "true"
    item["usable"] = item["cuda"]
    if not item["cuda"]:
        item["note"] = "torch present but no CUDA device"
    return item


def scan(extra=(), deep=False, progress=None, budget=90):
    """扫描并返回清单。有可用的立即停；总耗时受 budget 秒限制。"""
    import time as _t
    start = _t.time()
    found = []
    for exe in candidate_pythons(extra, deep=deep):
        if _t.time() - start > budget:
            found.append({"exe": "<budget exhausted>", "has_torch": False,
                          "version": None, "cuda": False, "usable": False,
                          "note": "scan budget %.0fs exceeded" % budget})
            break
        item = probe(exe, timeout=12)
        found.append(item)
        if progress:
            try:
                progress(item)
            except Exception:
                pass
        if item["usable"]:
            break
    return found


def quick(extra=()):
    """快速判断：只看路径特征 + 一次轻量探测，用于首屏。"""
    for exe in candidate_pythons(extra, deep=False):
        item = probe(exe, timeout=6)
        if item["usable"]:
            return item
    return None

def is_frozen():
    """当前进程是不是被打包的 exe（PyInstaller / cx_Freeze 等）。"""
    return bool(getattr(sys, "frozen", False))


def is_self_executable(path):
    """path 是不是本程序自己的可执行文件。

    打包后 sys.executable 指向 GPUUnlockInspector.exe 本身。
    如果把它当成 Python 解释器去 `-c "import torch"`，
    exe 会被重新启动 —— 表现为「无限弹出新窗口」，必须挡住。
    """
    if not path:
        return False
    try:
        a = os.path.normcase(os.path.abspath(path))
        b = os.path.normcase(os.path.abspath(sys.executable or ""))
    except Exception:
        return False
    if not b:
        return False
    if a == b:
        # 只有「自身不是 python.exe」时才真的算自执行（打包后的 exe）。
        # 源码运行时 sys.executable 就是解释器本身，用它跑 -c 是正常的。
        return not os.path.basename(b).lower().startswith("python")
    return False
    # *_MEIPASS 下的 bootloader 副本也一并挡掉
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            if os.path.normcase(os.path.abspath(os.path.dirname(a))) == \
                    os.path.normcase(os.path.abspath(meipass)):
                return True
        except Exception:
            pass
    return False


def safe_python(path):
    """这个 path 能否安全地当作 Python 解释器去执行。"""
    if not path or not os.path.isfile(path):
        return False
    if is_self_executable(path):
        return False
    name = os.path.basename(path).lower()
    if not name.startswith("python"):
        return False
    # WindowsApps 下的是 Store 转发器/受限副本：拿它跑 -c 会直接失败，
    # 建 venv 也不支持，所以一律不当解释器用。
    if "\\windowsapps\\" in os.path.abspath(path).lower():
        return False
    return True
