# -*- coding: utf-8 -*-
r"""可选依赖安装：一键装出带 torch+CUDA 的测试环境。

策略（按顺序）
--------------
  1. 找本机已有、且**真的能建 venv** 的 Python 3.10+；
  2. 找不到就下载一份便携版 CPython（~21 MB，见 python_boot），解压即用；
  3. 用该解释器建 tools\torch-venv 并装 torch（~2–3 GB）。

全部落在程序目录内，不写注册表、不污染系统。

约束：安装耗时数分钟、体积大，UI 必须显示实时进度并允许取消；
装不上就老实说装不上，绝不静默降级后假装数值可信。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

# CUDA 12.1 轮子兼容面最广；不锁死小版本
PIP_ARGS = ("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple",
            "torch", "--extra-index-url", "https://download.pytorch.org/whl/cu121")


def venv_dir():
    from .modeldb import app_dir
    return os.path.join(app_dir(), "tools", "torch-venv")


def venv_python():
    p = os.path.join(venv_dir(), "Scripts", "python.exe")
    return p if os.path.isfile(p) else None


def find_system_python():
    r"""找一个能真正建立 venv 的 Python 3.10+。

    坑：PATH 上第一个 python.exe 常常是 Microsoft Store 的转发器
    （WindowsApps\python.exe），它建 venv 会失败（Store 版不支持 venv）。
    所以要逐个验证 `-m venv --help` 可用，而不是看看文件存在就当能用。
    """
    from .envscan import safe_python
    cands = []
    for name in ("python.exe", "python3.exe"):
        import shutil
        got = shutil.which(name)
        if got:
            cands.append(got)
    for key in ("LOCALAPPDATA",):
        base = os.environ.get(key)
        if base:
            for ver in ("312", "311", "313", "310"):
                cands.append(os.path.join(base, "Programs", "Python", "Python" + ver,
                                          "python.exe"))
    for ver in ("312", "311", "313", "310"):
        cands.append("C:\\Python%s\\python.exe" % ver)
    # 复用环境扫描的第三方发现（ComfyUI 等自带 python 也能建 venv）
    try:
        from . import envscan
        cands.extend(envscan.candidate_pythons(deep=True))
    except Exception:
        pass

    seen = set()
    for exe in cands:
        if not exe or exe in seen:
            continue
        seen.add(exe)
        if not safe_python(exe):
            continue
        # Store 转发器：路径里带 WindowsApps，直接跳过（venv 必失败）
        if "windowsapps" in exe.lower():
            continue
        if _can_make_venv(exe):
            return exe
    return None


def _can_make_venv(exe, timeout=25):
    """验证这个解释器能建 venv（顺带确认版本 >= 3.10）。"""
    try:
        p = subprocess.run(
            [exe, "-c",
             "import sys,venv;print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, timeout=timeout,
            creationflags=(0x08000000 if sys.platform == "win32" else 0))
    except Exception:
        return False
    if p.returncode != 0:
        return False
    try:
        major, minor = (p.stdout or b"").decode().split()[:2]
        return (int(major), int(minor)) >= (3, 10)
    except Exception:
        return False


class TorchInstaller:
    """后台线程装 torch。UI 轮询 events。"""

    def __init__(self):
        self.events = []
        self.running = False
        self.ok = False
        self.error = None
        self.progress = 0.0          # 0..1
        self.stage = "init"          # python / venv / torch / verify
        self.finished = False
        self.cancelled = False
        self.torch_version = None
        self._proc = None
        self.proxy = os.environ.get("HTTPS_PROXY") or None
        self._thread = None

    def start(self):
        if self.running:
            return False
        self.running = True
        self._thread = threading.Thread(target=self._run, name="torch-install",
                                        daemon=True)
        self._thread.start()
        return True

    def _emit(self, text):
        self.events.append("%s" % text)

    def _pump(self, proc, phase, weight_from, weight_to):
        """流式读取子进程输出，解析进度并更新 self.progress。

        pip 的下载进度形如 "  45%|####      | 320.1M/715.2M"，把它映射到本阶段的
        权重区间；解析不到就用「已运行时长 / 估计时长」做不确定进度。
        """
        import re
        pct_re = re.compile(rb"(\d{1,3})%\|")
        buf = b""
        start = time.time()
        while True:
            chunk = proc.stdout.readline() if proc.stdout else b""
            if not chunk:
                break
            buf += chunk
            line = chunk.decode("utf-8", "replace").strip()
            if line:
                self._emit(line[:200])
            m = pct_re.findall(chunk)
            if m:
                try:
                    p = min(99, int(m[-1]))
                    self.progress = weight_from + (weight_to - weight_from) * p / 100.0
                except ValueError:
                    pass
            else:
                # 没有百分比时，用时间做一个缓慢爬升的不确定进度（不超过区间上限）
                frac = min(0.9, (time.time() - start) / 120.0)
                self.progress = max(self.progress,
                                    weight_from + (weight_to - weight_from) * frac)
        return buf

    def _run(self):
        try:
            self.progress = 0.02
            self.stage = "python"
            # 优先用系统里能建 venv 的 Python；没有就自举一份便携版。
            py = find_system_python()
            if py:
                self._emit("using system python: %s" % py)
                self.progress = 0.55
            else:
                self.stage = "download_python"
                self._emit("no usable system python; downloading a portable one ...")
                from . import python_boot

                def _prog(frac, got, total):
                    # 下载阶段占 5%..50%
                    self.progress = 0.05 + 0.45 * max(0.0, min(1.0, frac))

                py = python_boot.fetch_python(proxy=self.proxy, progress=_prog,
                                              log=self._emit)
                if not py:
                    self.error = "could not obtain a python interpreter"
                    self._emit(self.error)
                    return
                self.progress = 0.55

            vd = venv_dir()
            if not os.path.isfile(os.path.join(vd, "Scripts", "python.exe")):
                self.stage = "venv"
                os.makedirs(os.path.dirname(vd), exist_ok=True)
                self._emit("creating venv at %s" % vd)
                proc = subprocess.Popen(
                    [py, "-m", "venv", vd],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
                    creationflags=(0x08000000 if sys.platform == "win32" else 0))
                self._pump(proc, "venv", 0.55, 0.62)
                rc = proc.wait(timeout=600)
                if rc != 0:
                    self.error = "venv create failed (rc=%d)" % rc
                    self._emit(self.error)
                    return
            self.progress = 0.62

            vpy = os.path.join(vd, "Scripts", "python.exe")
            self.stage = "torch"
            self._emit("installing torch (this can take several minutes) ...")
            cmd = [vpy, "-m", "pip", "install", "--progress-bar", "on"] + list(PIP_ARGS)
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
                creationflags=(0x08000000 if sys.platform == "win32" else 0))
            self._proc = proc
            out = self._pump(proc, "torch", 0.62, 0.96)
            rc = proc.wait(timeout=7200)
            self._proc = None
            tail = out.decode("utf-8", "replace")
            if self.cancelled:
                self.error = "cancelled"
                self._emit(self.error)
                return
            if rc != 0:
                self.error = "pip install failed: %s" % tail[-400:]
                self._emit(self.error)
                return
            self.progress = 0.96

            self.stage = "verify"
            chk = subprocess.run(
                [vpy, "-c", "import torch;print(torch.__version__, torch.cuda.is_available())"],
                capture_output=True, timeout=180)
            verify = (chk.stdout or b"").decode("utf-8", "replace").strip()
            self._emit("verify: %s" % verify)
            self.torch_version = verify.split()[0] if verify else None
            self.ok = chk.returncode == 0 and "True" in verify
            if not self.ok:
                self.error = "torch installed but CUDA unavailable"
            else:
                self.progress = 1.0
        except subprocess.TimeoutExpired:
            self.error = "install timed out"
            self._emit(self.error)
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self._emit(self.error)
        finally:
            self.running = False
            self.finished = True

    def cancel(self):
        """用户点取消：终止 pip 子进程。"""
        self.cancelled = True
        proc = getattr(self, "_proc", None)
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
