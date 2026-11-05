# -*- coding: utf-8 -*-
"""GPUUnlockInspector 入口。任何异常都在这里兜底，保证不闪退。"""
from __future__ import annotations

import os
import sys
import traceback


def _bootstrap():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


T0 = __import__("time").time()


def _mark(msg):
    """启动阶段打点：写 stderr，配合 --console 构建定位冷启动瓶颈。"""
    import time as _t
    stream = sys.stderr
    if stream is None or not os.environ.get("GPUINV_DEBUG"):
        return
    try:
        stream.write("[%6.3fs] %s\n" % (_t.time() - T0, msg))
        stream.flush()
    except Exception:
        pass


def main():
    import time as _t
    os.environ.setdefault("GPUINV_PROC_START", "%.6f" % _t.time())
    _mark("main() entered")
    _bootstrap()
    try:
        from core import log as _log
        _log.init()
    except Exception:
        pass
    # 递归启动防御：同名进程过多说明有人把 exe 自己当解释器拉起来了
    try:
        from core import single_instance
        ok, why = single_instance.guard()
        if not ok:
            _mark("refusing to start: %s" % why)
            try:
                from core.modeldb import app_dir
                with open(os.path.join(app_dir(), "gpu_inspector_crash.log"), "a",
                          encoding="utf-8", newline="\n") as fh:
                    fh.write("\n===== %s =====\nrefused: %s\n"
                             % (__import__("time").strftime("%Y-%m-%d %H:%M:%S"), why))
            except Exception:
                pass
            return 2
    except Exception:
        pass
    _mark("bootstrap done")
    try:
        _mark("importing ui.app")
        from ui.app import App
        _mark("ui.app imported")
        app = App()
        _mark("App constructed")
        _mark("entering mainloop")
        app.mainloop()
        return 0
    except Exception:
        traceback.print_exc()
        # 最后一道防线：即便 UI 起不来，也要留一份可读的日志，而不是静默退出
        try:
            from core.modeldb import app_dir
            log = os.path.join(app_dir(), "gpu_inspector_crash.log")
            with open(log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n===== %s =====\n" % __import__("time").strftime(
                    "%Y-%m-%d %H:%M:%S"))
                traceback.print_exc(file=fh)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())