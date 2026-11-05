# -*- coding: utf-8 -*-
r"""统一日志：写文件 + 控制台，便于事后排查。

GUI 程序没有控制台，出问题时用户只能描述「点了没反应」。
日志把每一步都落到文件里，看最后几行就知道卡在哪。

- 文件：<exe目录>/gpu_inspector.log
- 轮转：超过 2 MB 改名为 .1（只留一份）
- 级别：DEBUG/INFO/WARN/ERROR，默认 INFO，GPUINV_LOG=DEBUG 打开
- 线程安全；写日志失败绝不影响主流程
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

NL = chr(10)

_lock = threading.Lock()
_path = None
_level = None
_fh = None

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _default_path():
    try:
        from core.modeldb import app_dir
        return os.path.join(app_dir(), "gpu_inspector.log")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "gpu_inspector.log")


def _open():
    global _fh, _path
    if _fh is not None:
        return _fh
    _path = _path or _default_path()
    try:
        d = os.path.dirname(_path)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.isfile(_path) and os.path.getsize(_path) > 2 * 1024 * 1024:
            try:
                old = _path + ".1"
                if os.path.exists(old):
                    os.remove(old)
                os.replace(_path, old)
            except OSError:
                pass
        _fh = open(_path, "a", encoding="utf-8", newline="")
    except OSError:
        _fh = None
    return _fh


def init(path=None, level=None):
    global _path, _level
    if path:
        _path = path
    _level = (level or os.environ.get("GPUINV_LOG") or "INFO").upper()
    if _level not in LEVELS:
        _level = "INFO"
    log("INFO", "log", "===== session start =====")
    log("INFO", "log", "file=%s level=%s" % (_path or _default_path(), _level))
    return _path or _default_path()


def enabled(level):
    lv = _level or (os.environ.get("GPUINV_LOG") or "INFO").upper()
    return LEVELS.get(level, 20) >= LEVELS.get(lv, 20)


def log(level, tag, message):
    if not enabled(level):
        return
    try:
        ts = time.strftime("%H:%M:%S", time.localtime())
        ms = int((time.time() % 1) * 1000)
        line = "%s.%03d [%-5s] %-14s %s" % (ts, ms, level, "[" + tag + "]", message)
        with _lock:
            fh = _open()
            if fh:
                fh.write(line + NL)
                fh.flush()
            stream = sys.stderr
            if stream is not None and (level in ("WARN", "ERROR") or enabled("DEBUG")):
                try:
                    stream.write(line + NL)
                    stream.flush()
                except Exception:
                    pass
    except Exception:
        pass


def debug(tag, msg):
    log("DEBUG", tag, msg)


def info(tag, msg):
    log("INFO", tag, msg)


def warn(tag, msg):
    log("WARN", tag, msg)


def error(tag, msg, exc=None):
    log("ERROR", tag, msg)
    if exc is not None:
        try:
            tb = "".join(traceback.format_exception(type(exc), exc,
                                                   exc.__traceback__))
            for ln in tb.rstrip().splitlines():
                log("ERROR", tag, "  " + ln)
        except Exception:
            pass


def tail(path=None, n=50):
    """读最后 n 行，供 UI 显示或在崩溃时上报。"""
    p = path or _path or _default_path()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-n:])
    except OSError:
        return ""


def close():
    global _fh
    try:
        with _lock:
            if _fh:
                _fh.write("%s [INFO ] [log] ===== session end =====%s"
                          % (time.strftime("%H:%M:%S", time.localtime()), NL))
                _fh.close()
    except Exception:
        pass
    _fh = None
