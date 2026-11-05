# -*- coding: utf-8 -*-
r"""自举一个可用的 Python 环境：不依赖用户机器上装没装 Python。

为什么需要
----------
之前依赖本机已有 Python 3.10+ 来建 venv。实测踩到两个坑：
  1. PATH 上第一个 python.exe 是 Microsoft Store 转发器，建 venv 直接失败；
  2. 有些机器根本没有任何可用的 Python。

解法：直接下载一份**便携版 CPython**（约 21 MB），解压即用、自带 pip、
能建 venv，不写注册表、不装系统。来源：python-build-standalone
（astral-sh 维护，GitHub 公开发布，被 uv 等项目使用）。

拿到解释器后，再用它建 tools\torch-venv 并装 torch。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

REPO = "astral-sh/python-build-standalone"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
# 与 torch cu121 轮子兼容性最好的版本
PREFERRED = ("3.12", "3.11")
ASSET_RE = (r"^cpython-(?P<ver>\d+\.\d+)\.\d+\+\d+-x86_64-pc-windows-msvc"
            r"-install_only_stripped\.tar\.gz$")


def bootstrap_dir():
    from .modeldb import app_dir
    return os.path.join(app_dir(), "tools", "python")


def existing_python():
    """已经自举好就直接用。"""
    p = os.path.join(bootstrap_dir(), "python.exe")
    if os.path.isfile(p):
        return p
    p2 = os.path.join(bootstrap_dir(), "python", "python.exe")
    return p2 if os.path.isfile(p2) else None


def _no_window():
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return kw


def _http_get(url, dst, proxy=None, progress=None, timeout=900):
    """带进度的下载。支持代理。"""
    req = urllib.request.Request(url, headers={"User-Agent": "gpuinv"})
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers) if handlers else \
        urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with open(dst, "wb") as fh:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                if progress and total:
                    try:
                        progress(got / float(total), got, total)
                    except Exception:
                        pass
    return os.path.getsize(dst)


def _pick_asset(release):
    import re
    pat = re.compile(ASSET_RE)
    best = None
    for a in release.get("assets") or []:
        m = pat.match(a.get("name") or "")
        if not m:
            continue
        ver = m.group("ver")
        rank = PREFERRED.index(ver) if ver in PREFERRED else len(PREFERRED)
        if best is None or rank < best[0]:
            best = (rank, a)
    return best[1] if best else None


def fetch_python(proxy=None, progress=None, log=None):
    """下载并解压便携 Python。返回解释器路径或 None。"""
    def emit(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    have = existing_python()
    if have:
        emit("portable python already present: %s" % have)
        return have

    try:
        emit("querying %s ..." % REPO)
        with urllib.request.urlopen(
                urllib.request.Request(API, headers={"User-Agent": "gpuinv"}),
                timeout=60) as resp:
            release = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        emit("release query failed: %s" % exc)
        return None

    asset = _pick_asset(release)
    if not asset:
        emit("no windows x86_64 asset in release %s" % release.get("tag_name"))
        return None
    emit("asset: %s (%.1f MB)" % (asset["name"], asset["size"] / 1048576.0))

    root = bootstrap_dir()
    os.makedirs(root, exist_ok=True)
    tar_path = os.path.join(root, "python.tar.gz")
    try:
        done = _http_get(asset["browser_download_url"], tar_path, proxy=proxy,
                         progress=progress)
        emit("downloaded %.1f MB" % (done / 1048576.0))
    except Exception as exc:
        emit("download failed: %s" % exc)
        return None

    try:
        emit("extracting ...")
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(root)
        os.remove(tar_path)
    except Exception as exc:
        emit("extract failed: %s" % exc)
        return None

    exe = existing_python()
    if not exe:
        emit("python.exe not found after extract")
        return None
    try:
        p = subprocess.run([exe, "-c", "import sys,venv;print(sys.version.split()[0])"],
                           capture_output=True, timeout=120, **_no_window())
        ver = (p.stdout or b"").decode().strip()
        emit("portable python ready: %s" % ver)
    except Exception as exc:
        emit("verify failed: %s" % exc)
    return exe
