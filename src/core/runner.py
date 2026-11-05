# -*- coding: utf-8 -*-
"""基准测试运行器：后台线程 + 队列，UI 线程只读队列，绝不阻塞。

设计：
- 用 threading.Thread + queue.Queue，不依赖 asyncio，tkinter 友好。
- 采样与「计算点」解耦：算力点每个 slice 记一次，功耗/温度每 0.5s 抓一次。
- 停止用 threading.Event，最多等一个 slice（约 0.1~0.3s）就退出。
- 所有异常都被捕获后通过 error 事件送回 UI，程序不会因为跑分挂掉。
"""
from __future__ import annotations

import queue
import threading
import time
import traceback


class BenchResult:
    def __init__(self):
        self.samples = []       # [{"t": 秒, "rate": MSol/s, "power": W, "clock": MHz, "temp": C}]
        self.started_at = None
        self.finished_at = None
        self.algo = "proxy_v1"
        self.engine = "opencl"
        self.intensity = "mid"
        self.duration = 0
        self.device = None
        self.error = None
        self.aborted = False

    # ---------------------------------------------------------- 统计
    def rates(self):
        return [s["rate"] for s in self.samples if s.get("rate")]

    def avg(self):
        r = self.rates()
        return sum(r) / len(r) if r else None

    def peak(self):
        r = self.rates()
        return max(r) if r else None

    def low(self):
        r = self.rates()
        return min(r) if r else None

    def stability(self):
        """稳定性 = 1 - 变异系数，映射到 0..100%。

        变异系数 = 标准差 / 平均值。压测时温度上来后降频会让它掉，
        所以这个值本身也是一条有用的证据（配合降频原因看）。
        """
        r = self.rates()
        if len(r) < 2:
            return None
        mean = sum(r) / len(r)
        if mean <= 0:
            return None
        var = sum((x - mean) ** 2 for x in r) / len(r)
        cv = (var ** 0.5) / mean
        return max(0.0, min(1.0, 1.0 - cv)) * 100.0

    def power_avg(self):
        p = [s["power"] for s in self.samples if s.get("power")]
        return sum(p) / len(p) if p else None

    def power_peak(self):
        p = [s["power"] for s in self.samples if s.get("power")]
        return max(p) if p else None

    def temp_peak(self):
        t = [s["temp"] for s in self.samples if s.get("temp")]
        return max(t) if t else None

    def clock_avg(self):
        c = [s["clock"] for s in self.samples if s.get("clock")]
        return sum(c) / len(c) if c else None

    def elapsed(self):
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, end - self.started_at)

    def to_dict(self):
        return {
            "algo": self.algo,
            "engine": self.engine,
            "intensity": self.intensity,
            "device": self.device,
            "duration_s": round(self.elapsed(), 2),
            "samples": len(self.samples),
            "avg": self.avg(),
            "peak": self.peak(),
            "min": self.low(),
            "stability": self.stability(),
            "power_avg": self.power_avg(),
            "power_peak": self.power_peak(),
            "temp_peak": self.temp_peak(),
            "clock_avg": self.clock_avg(),
            "curve": [{"t": round(s["t"], 3), "rate": s.get("rate"),
                       "power": s.get("power"), "clock": s.get("clock"),
                       "temp": s.get("temp")} for s in self.samples],
            "error": self.error,
            "aborted": self.aborted,
        }


class BenchRunner:
    """启动一次测试；UI 侧轮询 drain() 拿事件。

    事件类型：
        ("start", result)
        ("sample", {"t","rate","power","clock","temp","progress"})
        ("done", result)
        ("error", message)
    """

    def __init__(self, engine, sampler, duration=30, intensity="mid", algo="proxy_v1"):
        self.engine = engine          # core.bench_ocl.OpenCLBench / CpuBench 实例
        self.sampler = sampler        # 无参可调用，返回实时状态 dict
        self.duration = max(3, int(duration))
        self.intensity = intensity
        self.algo = algo
        self.result = BenchResult()
        self.result.algo = algo
        self.result.intensity = intensity
        self.result.engine = "opencl" if engine.__class__.__name__ == "OpenCLBench" else "cpu"
        self.result.device = getattr(engine, "device_name", None)
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

    # ---------------------------------------------------------- 控制
    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._thread = threading.Thread(target=self._run, name="bench", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def drain(self):
        """取出目前所有事件（非阻塞）。"""
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out

    # ---------------------------------------------------------- 主体
    def _run(self):
        res = self.result
        res.started_at = time.time()
        self._q.put(("start", res))

        try:
            if not self.engine.ok and not self.engine.open():
                raise RuntimeError(self.engine.error or "engine open failed")

            self.engine.warmup(self.intensity, rounds=2 if res.engine == "opencl" else 1)

            seed = int(time.time()) & 0xFFFF
            power_acc = []
            while not self._stop.is_set():
                elapsed = time.time() - res.started_at
                if elapsed >= self.duration:
                    break

                dt, ops = self.engine.slice_once(self.intensity, seed=seed)
                seed = (seed + 7919) & 0xFFFF
                rate = ops / dt / 1e6 if dt > 0 else 0.0

                live = {}
                try:
                    live = self.sampler() or {}
                except Exception:
                    live = {}

                sample = {
                    "t": time.time() - res.started_at,
                    "rate": round(rate, 2),
                    "power": live.get("power_w"),
                    "clock": live.get("clock_sm_mhz") or live.get("clock_graphics_mhz"),
                    "temp": live.get("temp_c"),
                    "fan": live.get("fan_pct"),
                }
                res.samples.append(sample)
                sample["progress"] = min(1.0, sample["t"] / max(1, self.duration))
                self._q.put(("sample", sample))

            res.aborted = self._stop.is_set()
        except Exception as exc:
            res.error = "%s: %s" % (type(exc).__name__, exc)
            self._q.put(("error", res.error))
            traceback.print_exc()
        finally:
            res.finished_at = time.time()
            self._q.put(("done", res))
