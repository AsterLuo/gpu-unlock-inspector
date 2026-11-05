# -*- coding: utf-8 -*-
r"""按本机显卡家族，从参考项目拉取并生成引导 U 盘资产。

为什么不在 exe 里内置资产
------------------------
一组家族资产约 81 MB（主体是 84 MB 的 fwimage，GSP-RM 固件）。
内置就把 exe 从 19 MB 撑到 88 MB，而且只覆盖一个家族、把固件再分发出去。
参考项目自己也**不分发固件**：它只发布一个 maker，由 maker 现场从 NVIDIA 公开包
下载并 SHA-256 校验后生成。我们照它的做法来，体积和许可都干净。

流程
----
  1. 从 GitHub Release 取最新 tag（或用户指定 tag）；
  2. 下载 `+"`nv-uefi-unlocker-<tag>.zip`"+` 并解压到 `+"`<exe目录>\tools\unlocker\<tag>`"+`；
  3. 调 `+"`make-usbroot.ps1 -Family <family> -Out <缓存目录>`"+`；
  4. 校验产物齐全（每个家族 8 个必需文件）；
  5. 缓存起来，下次同家族直接复用。

全部联网操作都可取消；失败时把 maker 的原始输出交给 UI，不吞错。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time

REPO = "physicsdolphin/nv-uefi-unlocker"
RELEASE_API = "https://api.github.com/repos/%s/releases/latest" % REPO
ZIP_URL = ("https://github.com/%s/releases/download/v%s/nv-uefi-unlocker-v%s.zip"
           % (REPO, "%s", "%s"))

# 每个家族必需的产物（相对 usbroot 根）
FAMILY_FILES = {
    "ga102": ("booter_load_ga102.bin", "gsp_boot_ga102.bin", "gsp_boot_desc_ga102.bin",
              "fwimage_ga102.bin", "fwsignature_ga102.bin", "scrubber_ad10x.bin",
              "v67-ga102-four-plm.bin", "EFI/BOOT/BOOTX64.EFI"),
    "ad102": ("booter_load_ad102.bin", "gsp_boot_ad102.bin", "gsp_boot_desc_ad102.bin",
              "fwimage_ad102.bin", "fwsignature_ad102.bin", "scrubber_ad10x.bin",
              "v67-ad102-four-plm.bin", "EFI/BOOT/BOOTX64.EFI"),
    "tu10x": ("booter_load_tu10x.bin", "gsp_boot_tu10x.bin", "gsp_boot_desc_tu10x.bin",
              "fwimage_tu10x.bin", "fwsignature_tu10x.bin", "sec2_bl_tu10x.bin",
              "v67-tu10x-four-plm.bin", "EFI/BOOT/BOOTX64.EFI"),
}


def tools_root():
    from .modeldb import app_dir
    return os.path.join(app_dir(), "tools", "unlocker")


def cache_dir(family):
    return os.path.join(tools_root(), "usbroot-" + family)


def _no_window():
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw["startupinfo"] = si
    return kw


def cached_family_ok(family):
    """缓存里是不是已经有一份完整产物。"""
    d = cache_dir(family)
    need = FAMILY_FILES.get(family)
    if not need or not os.path.isdir(d):
        return False
    for rel in need:
        p = os.path.join(d, rel.replace("/", os.sep))
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            return False
    return True


def cached_listing(family):
    """返回 [(rel, bytes)]，供 UI 显示将写什么。"""
    d = cache_dir(family)
    out = []
    if not os.path.isdir(d):
        return out
    for root, _dirs, files in os.walk(d):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, d).replace(os.sep, "/")
            if rel.lower().endswith(".log"):
                continue
            out.append((rel, os.path.getsize(src)))
    return sorted(out)


class AssetFetcher:
    """后台拉资产。UI 轮询 events + running。"""

    def __init__(self, family, tag=None, proxy=None):
        self.family = family
        self.tag = tag or "2.1.10"
        self.proxy = proxy or os.environ.get("HTTPS_PROXY") or ""
        self.events = []
        self.running = False
        self.ok = False
        self.error = None
        self.cancelled = False
        # 供 UI 画进度条：progress 为 0..1 的确定值，None = 拿不到总量（用扫动滑块）
        self.progress = None
        self.stage = ""
        self.stage_key = ""       # i18n 键，UI 用它取本地化文案
        # 字节级计数，UI 可显示「12.3 / 84.1 MB」
        self.bytes_done = 0
        self.bytes_total = 0
        self._thread = None

    def _emit(self, text):
        self.events.append(str(text))

    def _set_stage(self, key, progress=None, extra=""):
        """更新阶段。key 是 i18n 键，UI 取本地化文案；extra 拼在末尾（如 tag 号）。

        阶段切换时把进度重置成 None，避免残留上一个阶段的百分比。
        """
        self.stage_key = key
        self.stage = ("%s %s" % (key, extra)).strip()
        self.progress = progress
        self._emit(self.stage)

    def cancel(self):
        """标记取消：下载分块之间会检查，已启动的子进程只能等它自己结束。"""
        self.cancelled = True
        self._emit("cancel requested")

    def start(self):
        if self.running:
            return False
        self.running = True
        self._thread = threading.Thread(target=self._run, name="asset-fetch",
                                        daemon=True)
        self._thread.start()
        return True

    # ---------------------------------------------------------- 内部
    def _run(self):
        try:
            if cached_family_ok(self.family):
                self._emit("cache hit: %s" % cache_dir(self.family))
                self._set_stage("make.stage.cached", 1.0)
                self.ok = True
                return
            self._set_stage("make.stage.prepare", 0.0)
            src = self._ensure_maker()
            if not src or self.cancelled:
                return
            self._set_stage("make.stage.generate", None)
            self._generate(src)
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self._emit(self.error)
        finally:
            self.stage = self.stage or ""
            self.running = False

    def _ensure_maker(self):
        """确保本地有 maker 脚本；返回其目录。"""
        base = os.path.join(tools_root(), self.tag)
        script = os.path.join(base, "make-usbroot.ps1")
        if os.path.isfile(script):
            self._emit("maker already present: %s" % base)
            return base

        os.makedirs(base, exist_ok=True)
        zip_path = os.path.join(base, "pkg.zip")
        url = ZIP_URL % (self.tag, self.tag)
        self._set_stage("make.stage.maker_download", 0.0)
        self._emit("downloading %s" % url)
        if not self._download(url, zip_path):
            return None
        if self.cancelled:
            return None
        self._set_stage("make.stage.maker_unzip", None)
        self._emit("extracting ...")
        try:
            shutil.unpack_archive(zip_path, base)
        except Exception as exc:
            self.error = "unzip failed: %s" % exc
            self._emit(self.error)
            return None
        if not os.path.isfile(script):
            # 压缩包有时多一层目录
            for root, _d, files in os.walk(base):
                if "make-usbroot.ps1" in files:
                    script = os.path.join(root, "make-usbroot.ps1")
                    base = root
                    break
        return base if os.path.isfile(script) else None

    def _download(self, url, dst):
        """优先 urllib（能分块报进度），退回 curl（能吃掉自签证书环境）。

        顺序与旧版相反：进度反馈是这个弹窗存在的意义，能拿到真实百分比就先拿。
        urllib 失败才退 curl —— 那种情况多半是证书链问题，curl 也不一定救得回来，
        但至少行为不比以前差。
        """
        if self._download_urllib(url, dst):
            return True
        if self.cancelled:
            return False
        self._emit("urllib failed, falling back to curl")
        return self._download_curl(url, dst)

    def _download_urllib(self, url, dst):
        part = dst + ".part"
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "gpuinv"})
            if self.proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"https": self.proxy,
                                                 "http": self.proxy}))
            else:
                opener = urllib.request.build_opener()
            with opener.open(req, timeout=60) as resp, open(part, "wb") as fh:
                total = int(resp.headers.get("Content-Length") or 0)
                self.bytes_total = total
                self.bytes_done = 0
                while True:
                    if self.cancelled:
                        raise RuntimeError("cancelled")
                    block = resp.read(65536)
                    if not block:
                        break
                    fh.write(block)
                    self.bytes_done += len(block)
                    if total > 0:
                        self.progress = min(0.99, self.bytes_done / float(total))
            if os.path.getsize(part) > 1024:
                os.replace(part, dst)
                self.progress = 1.0
                self._emit("downloaded %d bytes" % os.path.getsize(dst))
                return True
            self._emit("urllib got only %d bytes" % (os.path.getsize(part) if
                                                     os.path.isfile(part) else 0))
            return False
        except Exception as exc:
            if self.cancelled:
                return False
            self._emit("urllib failed: %s: %s" % (type(exc).__name__, exc))
            return False
        finally:
            try:
                if os.path.isfile(part) and not os.path.isfile(dst):
                    os.remove(part)
            except OSError:
                pass

    def _download_curl(self, url, dst):
        # curl 在 Win10 1803+ 自带
        curl = shutil.which("curl") or shutil.which("curl.exe")
        if not curl:
            self.error = "download failed: neither urllib nor curl worked"
            self._emit(self.error)
            return False
        self.progress = None
        cmd = [curl, "-sSL", "--max-time", "300", "-o", dst, url]
        if self.proxy:
            cmd[1:1] = ["-x", self.proxy]
        p = subprocess.run(cmd, capture_output=True, timeout=360, **_no_window())
        if p.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 1024:
            self.progress = 1.0
            self._emit("downloaded %d bytes" % os.path.getsize(dst))
            return True
        self.error = "curl failed rc=%s" % p.returncode
        self._emit(self.error)
        return False

    def _generate(self, maker_dir):
        script = os.path.join(maker_dir, "make-usbroot.ps1")
        out = cache_dir(self.family)
        os.makedirs(out, exist_ok=True)
        work = os.path.join(maker_dir, "inputs")
        os.makedirs(work, exist_ok=True)
        ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        cmd = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", script, "-Family", self.family, "-Out", out, "-Work", work,
               "-SkipGspCheck"]
        self._set_stage("make.stage.maker_run", None)
        self._emit("running make-usbroot (%s) ..." % self.family)
        env = dict(os.environ)
        if self.proxy:
            env["HTTPS_PROXY"] = self.proxy
            env["HTTP_PROXY"] = self.proxy

        # 真正流式读 maker 的输出：它要跑几分钟，blocking run() 期间事件列表是空的，
        # UI 就只能挂一个不会动的扫动条。改成逐行读，每行都进 events、都能显示。
        lines = []
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=env,
                                    cwd=maker_dir, **_no_window())
        except Exception as exc:
            self.error = "spawn maker failed: %s" % exc
            self._emit(self.error)
            return
        try:
            for raw in iter(proc.stdout.readline, b""):
                if self.cancelled:
                    proc.kill()
                    break
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                lines.append(line)
                self._emit(line)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
        rc = proc.wait(timeout=60)
        if self.cancelled:
            self.error = "cancelled"
            self._emit(self.error)
            return
        for line in lines[-25:]:
            pass
        if rc != 0:
            self.error = "maker exit %d" % rc
            self._emit(self.error)
            return
        if cached_family_ok(self.family):
            self.ok = True
            self._set_stage("make.stage.ready", 1.0)
            self._emit("assets ready: %s (%d files)" % (out, len(cached_listing(self.family))))
        else:
            self.error = "maker finished but assets incomplete"
            self._emit(self.error)
