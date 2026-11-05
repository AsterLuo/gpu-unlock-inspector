# -*- coding: utf-8 -*-
"""跨重启存档：解锁前后对比的唯一正确做法。

为什么必须跨重启
----------------
nv-uefi-unlocker 这类方案是在 **UEFI 预驱动阶段** 覆盖 GPU 的 PLM/SS 寄存器，
解锁由 U 盘引导完成；进入 Windows 之后**不存在热解锁**，显卡也不会在运行中
从「未解锁」变成「已解锁」。因此前后对比一定是这个形状：

    [第 1 次开机] 未解锁 → 跑基准 → 存「解锁前」
    [关机] U 盘引导解锁
    [第 2 次开机] 已解锁 → 跑基准 → 存「解锁后」→ 自动配对出提升幅度

所以「解锁前」的结果必须落盘到一个重启后仍然存在的位置。按你的要求放在
用户目录的 `~/.cache/GPUUnlockInspector/` 下：

    ~/.cache/GPUUnlockInspector/
        sessions.json          所有基准存档（含解锁前后标签）
        state.json             上次运行时的解锁状态快照，用来判断「这次是不是换状态了」

注意 Windows 上 `~` 就是 `%USERPROFILE%`；本程序不依赖 shell 展开，
直接用 os.path.expanduser("~")，这样在便携版里也能落到真实用户目录。

这个位置的好处：重启、关机、换 U 盘引导都不会丢；同时它属于用户缓存目录，
删掉即清除，不污染系统。
"""
from __future__ import annotations

import json
import os
import time

APP_DIRNAME = "GPUUnlockInspector"
SESSIONS = "sessions.json"
STATE = "state.json"

# 解锁前 / 解锁后各一个子文件夹；一「份」存档 = 一个 JSON 文件，
# 里面装着这个板块的两个测试（算力实测 TFLOPS + 基准测试 proxy_v1）的全部结果。
BEFORE = "before"
AFTER = "after"



def _gpu_block(gpu):
    return {
        "name": (gpu or {}).get("name"),
        "vbios": (gpu or {}).get("vbios_version"),
        "driver": (gpu or {}).get("driver_version"),
        "mem_total": (gpu or {}).get("mem_total_bytes"),
        "pci": (gpu or {}).get("pci_bus_id"),
    }


def _bundle_tflops(bundle):
    """从一份 bundle 里取主判据 TFLOPS（FP16 + FP32 累加 @4096³）。"""
    comp = (bundle or {}).get("compute") or {}
    summary = comp.get("summary") or []
    for row in summary:
        if isinstance(row, dict) and row.get("key") == "fp16_fp32acc_4096":
            return row.get("tflops")
    # 退回「全速对照」或任意第一个非空值
    for row in summary:
        if isinstance(row, dict) and row.get("tflops") is not None:
            return row.get("tflops")
    return None


def cache_root():
    """~/.cache/GPUUnlockInspector（不存在则逐级创建）。

    不假设任何一级已经存在：~/.cache 没有就先建 .cache，再建 GPUUnlockInspector；
    before/ 与 after/ 两个子目录由 bundle_dir() 按需创建。
    """
    base = os.path.join(os.path.expanduser("~"), ".cache")
    path = os.path.join(base, APP_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        # 极端情况下（只读用户目录）退回到程序目录，保证功能不丢
        from .modeldb import app_dir
        path = os.path.join(app_dir(), ".cache", APP_DIRNAME)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
    return path


def bundle_dir(kind, root=None):
    """before/ 或 after/ 子目录（不存在则创建）。"""
    root = root or cache_root()
    d = os.path.join(root, BEFORE if _norm_kind(kind) == "before" else AFTER)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _norm_kind(kind):
    """把历史命名统一到 before / after。"""
    k = (kind or "").strip().lower()
    if k in ("after", "post"):
        return "after"
    return "before"


def _read(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


class Archive:
    """跨重启的测量存档。"""

    def __init__(self, root=None):
        self.root = root or cache_root()
        self.sessions_path = os.path.join(self.root, SESSIONS)
        self.state_path = os.path.join(self.root, STATE)
        self.sessions = []
        self.state = {}
        # 启动就把两个子文件夹准备好：用户第一次看到「打开所在文件夹」时
        # 里面应该是 before/ 与 after/ 两个空目录，而不是一个空文件夹让人猜。
        bundle_dir(BEFORE, self.root)
        bundle_dir(AFTER, self.root)
        self.reload()

    def reload(self):
        data = _read(self.sessions_path, {"sessions": []})
        sess = data.get("sessions")
        self.sessions = sess if isinstance(sess, list) else []
        self.state = _read(self.state_path, {})
        return self

    # ------------------------------------------------------------ 会话
    def boot_id(self):
        """本次开机的标识：开机时间 + 系统启动秒数。

        用来区分「同一次开机内的多次测试」和「重启之后的新测试」。
        """
        try:
            import ctypes
            ms = ctypes.windll.kernel32.GetTickCount64()
            up = ms / 1000.0
        except Exception:
            up = 0
        return {
            "boot_time": time.time() - up,
            "uptime_s": round(up, 1),
        }

    def is_new_boot(self):
        """和上次记录的 boot_time 差得够远，就认为是新的一次开机。"""
        last = self.state.get("boot_time")
        if last is None:
            return True
        try:
            return abs(float(last) - self.boot_id()["boot_time"]) > 120
        except (TypeError, ValueError):
            return True

    def add_session(self, record, kind, verdict_state, gpu, note="", compute=None):
        """保存一次基准结果。kind ∈ {baseline, after, plain}。

        baseline = 解锁前（第 1 次开机）
        after    = 解锁后（重启并解锁之后的第 2 次开机）
        plain    = 只是测了一下，不参与配对
        """
        boot = self.boot_id()
        item = {
            "id": "%s-%s" % (kind, time.strftime("%Y%m%d-%H%M%S")),
            "kind": kind,
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "boot_time": boot["boot_time"],
            "uptime_s": boot["uptime_s"],
            "unlock_state": verdict_state,
            "gpu": {
                "name": (gpu or {}).get("name"),
                "vbios": (gpu or {}).get("vbios_version"),
                "driver": (gpu or {}).get("driver_version"),
                "mem_total": (gpu or {}).get("mem_total_bytes"),
            },
            "bench": record,
            # 算力实测（TFLOPS）：与基准测试并列存，跨重启对比两者都要
            "compute": compute or {},
            "note": note,
        }
        self.sessions.append(item)
        _write(self.sessions_path, {"sessions": self.sessions[-200:]})
        self.sessions = self.sessions[-200:]
        return item

    def update_state(self, verdict_state, gpu):
        """记录「这次开机看到的状态」，下次启动就能判断状态有没有变化。"""
        boot = self.boot_id()
        prev = self.state.get("last_state")
        changed = prev is not None and prev != verdict_state
        self.state = {
            "last_state": verdict_state,
            "last_state_at": time.time(),
            "last_state_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "boot_time": boot["boot_time"],
            "gpu_name": (gpu or {}).get("name"),
            "previous_state": prev,
            "state_changed_since_last_boot": bool(changed),
        }
        _write(self.state_path, self.state)
        return self.state

    # ------------------------------------------------------------ 双文件夹存档
    # 一份文件 = 这个板块的两个测试（算力实测 + 基准测试）的全部结果。
    # 解锁前写 before/，解锁后写 after/，各自独立，互不覆盖。

    def save_bundle(self, kind, compute, bench, unlock_state, gpu, note=""):
        """把「算力实测 + 基准测试」一起写成一份 JSON。返回 (item, path)。"""
        k = _norm_kind(kind)
        boot = self.boot_id()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        item = {
            "schema": 1,
            "kind": k,
            "id": "%s-%s" % (k, stamp),
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "ts": time.time(),
            "boot_time": boot["boot_time"],
            "uptime_s": boot["uptime_s"],
            "unlock_state": unlock_state,
            "gpu": _gpu_block(gpu),
            "compute": compute or {},
            "bench": bench or {},
            "note": note,
        }
        d = bundle_dir(k, self.root)
        path = os.path.join(d, "%s.json" % item["id"])
        # 同一秒内重复保存时别覆盖上一份
        n = 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(d, "%s-%d.json" % (item["id"], n))
        _write(path, item)
        return item, path

    def list_bundles(self, kind):
        """列出某一边的全部存档文件（新的在前）。"""
        d = bundle_dir(kind, self.root)
        out = []
        try:
            names = [n for n in os.listdir(d) if n.lower().endswith(".json")]
        except OSError:
            return out
        for name in names:
            data = _read(os.path.join(d, name), None)
            if isinstance(data, dict):
                data.setdefault("path", os.path.join(d, name))
                out.append(data)
        out.sort(key=lambda x: x.get("ts") or 0, reverse=True)
        return out

    def latest_bundle(self, kind):
        items = self.list_bundles(kind)
        return items[0] if items else None

    def read_bundle(self, path):
        """从任意路径读一份存档（导入用）。读不出或格式不对返回 None。"""
        if not path or not os.path.isfile(path):
            return None
        data = _read(path, None)
        if not isinstance(data, dict):
            return None
        # 只认本程序写的存档：必须有 compute 或 bench 之一
        if not (data.get("compute") or data.get("bench")):
            return None
        data.setdefault("kind", "before")
        data["kind"] = _norm_kind(data.get("kind"))
        data["path"] = path
        return data

    def import_bundle(self, path, kind=None):
        """导入一份存档副本到对应子文件夹。kind 为空则用文件内记录的 kind。"""
        data = self.read_bundle(path)
        if data is None:
            return None
        k = _norm_kind(kind or data.get("kind"))
        d = bundle_dir(k, self.root)
        base = os.path.splitext(os.path.basename(path))[0]
        dst = os.path.join(d, "%s.json" % base)
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = os.path.join(d, "%s-import%d.json" % (base, n))
        try:
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                import json as _json
                _json.dump(data, fh, ensure_ascii=False, indent=2)
        except OSError:
            return None
        data["path"] = dst
        data["kind"] = k
        return data

    def clear_bundles(self):
        """清空 before/ 与 after/ 里的 JSON（保留目录本身）。"""
        removed = 0
        for k in (BEFORE, AFTER):
            d = bundle_dir(k, self.root)
            try:
                for name in os.listdir(d):
                    if name.lower().endswith(".json"):
                        try:
                            os.remove(os.path.join(d, name))
                            removed += 1
                        except OSError:
                            pass
            except OSError:
                pass
        return removed

    def bundle_pair(self, before=None, after=None):
        """用两份 bundle 算前后对比。两个测试各算一次。"""
        b = before or self.latest_bundle(BEFORE)
        a = after or self.latest_bundle(AFTER)
        if not b or not a:
            return None
        b_avg = (b.get("bench") or {}).get("avg")
        a_avg = (a.get("bench") or {}).get("avg")
        tf_b = _bundle_tflops(b)
        tf_a = _bundle_tflops(a)
        out = {
            "before": b,
            "after": a,
            "cross_boot": abs((a.get("boot_time") or 0) - (b.get("boot_time") or 0)) > 120,
            "baseline_state": b.get("unlock_state"),
            "after_state": a.get("unlock_state"),
            "bench_before": b_avg,
            "bench_after": a_avg,
            "bench_delta": (a_avg - b_avg) if (a_avg is not None and b_avg is not None) else None,
            "bench_pct": (((a_avg - b_avg) / b_avg * 100.0)
                           if (a_avg and b_avg) else None),
            "tflops_before": tf_b,
            "tflops_after": tf_a,
            "tflops_delta": ((tf_a - tf_b)
                              if (tf_a is not None and tf_b is not None) else None),
            "tflops_pct": (((tf_a - tf_b) / tf_b * 100.0)
                            if (tf_a is not None and tf_b) else None),
        }
        return out
    # ------------------------------------------------------------ 配对
    def latest_baseline(self):
        """最近一条「解锁前」。优先取上一次开机留下的（真正的跨重启基线）。"""
        cur_boot = self.boot_id()["boot_time"]
        cross = [s for s in self.sessions
                 if s.get("kind") == "baseline" and abs(cur_boot - s.get("boot_time", 0)) > 120]
        if cross:
            return cross[-1]
        same = [s for s in self.sessions if s.get("kind") == "baseline"]
        return same[-1] if same else None

    def latest_after(self):
        same = [s for s in self.sessions if s.get("kind") == "after"]
        return same[-1] if same else None

    def pair(self):
        """返回可用的前后配对：优先「跨重启 baseline ↔ 本次 after」。"""
        base = self.latest_baseline()
        after = self.latest_after()
        if not base or not after:
            return None
        if after.get("ts", 0) < base.get("ts", 0):
            return None
        b_avg = (base.get("bench") or {}).get("avg")
        a_avg = (after.get("bench") or {}).get("avg")
        if not b_avg or not a_avg:
            return None
        delta = a_avg - b_avg
        # 算力对比（TFLOPS）：取每份存档里的 fp16_fp32acc_4096
        def tf_of(item):
            c = (item.get("compute") or {}).get("summary") or []
            for r in c:
                if r.get("key") == "fp16_fp32acc_4096":
                    return r.get("tflops")
            return None

        b_tf, a_tf = tf_of(base), tf_of(after)
        tf_delta = (a_tf - b_tf) if (a_tf is not None and b_tf is not None) else None
        tf_pct = (tf_delta / b_tf * 100.0) if (tf_delta is not None and b_tf) else None
        return {
            "baseline": base,
            "after": after,
            "baseline_avg": b_avg,
            "after_avg": a_avg,
            "delta": delta,
            "pct": delta / b_avg * 100.0 if b_avg else None,
            "cross_boot": abs(after.get("boot_time", 0) - base.get("boot_time", 0)) > 120,
            "baseline_state": base.get("unlock_state"),
            "after_state": after.get("unlock_state"),
            "baseline_tflops": b_tf,
            "after_tflops": a_tf,
            "tflops_delta": tf_delta,
            "tflops_pct": tf_pct,
        }

    def delete(self, session_id):
        self.sessions = [s for s in self.sessions if s.get("id") != session_id]
        _write(self.sessions_path, {"sessions": self.sessions})

    def clear(self):
        self.sessions = []
        _write(self.sessions_path, {"sessions": self.sessions})