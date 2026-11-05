# -*- coding: utf-8 -*-
"""Tensor 算力实测（TFLOPS）—— 对齐 nv-uefi-unlocker 的 mma_bench 口径。

为什么不用 proxy_v1
--------------------
proxy_v1 测的是内存硬混洗，单位 MSol/s，和「解锁提升多少」没有量纲关系。
要证明的是 Tensor 路径的半速钳制被解开，所以必须直接测矩阵乘：
  - 钳制精确到「张量路径 × 累加模式」：FP16/BF16 + FP32 累加 出厂半速，
    FP16 + FP16 累加 与 FP32 CUDA 核 从未被锁（用作对照组）。
  - 必须能分别跑 FP32 累加 与 FP16 累加，否则测不出差异。

口径
----
  FLOP = 2 * M * N * K（一次乘加计 2 FLOP，dense，不含稀疏）。
  warmup 后取中位数，避免首轮 JIT / 提频干扰。

两条引擎（UI 必须标明本次数据来自哪条，绝不混用冒充）
------------------------------------------------------
  1. cublas：检测到带 torch+CUDA 的 Python 就调用，数值与参考项目同量级。
  2. opencl：内置手写 GEMM，零依赖但打不满 Tensor Core，只做相对差异参照。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

CASES = (
    ("fp16_fp32acc_4096", "case.fp16_fp32acc_4096", 4096, "fp16", "fp32"),
    ("bf16_fp32acc_4096", "case.bf16_fp32acc_4096", 4096, "bf16", "fp32"),
    ("fp16_fp32acc_8192", "case.fp16_fp32acc_8192", 8192, "fp16", "fp32"),
    ("bf16_fp32acc_8192", "case.bf16_fp32acc_8192", 8192, "bf16", "fp32"),
    ("fp32_ref_4096", "case.fp32_ref_4096", 4096, "fp32", "fp32"),
    ("fp16_fp16acc_4096", "case.fp16_fp16acc_4096", 4096, "fp16", "fp16"),
)

CASE_KEYS = tuple(c[0] for c in CASES)
LOCK_SENSITIVE = ("fp16_fp32acc_4096", "bf16_fp32acc_4096",
                  "fp16_fp32acc_8192", "bf16_fp32acc_8192")
CONTROL_CASES = ("fp32_ref_4096", "fp16_fp16acc_4096")

_BRIDGE = r"""
import json, sys, time
import torch

def bench(n, dtype, fp16_acc, warm, iters):
    dt = {"fp16": torch.float16, "bf16": torch.bfloat16, "fp32": torch.float32}[dtype]
    a = torch.randn(n, n, dtype=dt, device="cuda")
    b = torch.randn(n, n, dtype=dt, device="cuda")
    try:
        torch.backends.cuda.matmul.allow_fp16_accumulation = bool(fp16_acc)
        torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = bool(fp16_acc)
    except Exception:
        pass
    for _ in range(warm):
        torch.mm(a, b)
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        torch.mm(a, b)
        torch.cuda.synchronize()
        ts.append(time.perf_counter() - t0)
    ts.sort()
    t = ts[len(ts) // 2]
    return 2.0 * n * n * n / t / 1e12, t

def main():
    spec = json.loads(sys.argv[1])
    out = {"ok": True, "device": torch.cuda.get_device_name(0),
           "torch": torch.__version__, "results": {}}
    for item in spec:
        key, n, dtype, acc = item["key"], item["n"], item["dtype"], item["acc"]
        warm = 8 if n <= 4096 else 5
        iters = 25 if n <= 4096 else 12
        try:
            tflops, med = bench(n, dtype, acc == "fp16", warm, iters)
            out["results"][key] = {"tflops": round(tflops, 2), "sec": med}
        except Exception as exc:
            out["results"][key] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(out))

if __name__ == "__main__":
    main()
"""


def find_torch_python(extra=()):
    """找带 torch+CUDA 的 Python。委托给 envscan（扫描面更广）。"""
    from . import envscan
    for item in envscan.scan(extra, deep=True):
        if item.get("usable"):
            return item["exe"], "torch %s" % item.get("version")
    return None, "no torch+cuda python found"


def scan_environments(extra=(), progress=None):
    """给 UI 用的环境清单。"""
    from . import envscan
    return envscan.scan(extra, deep=True, progress=progress)


class TflopsRunner:
    """一次 TFLOPS 实测。run() 阻塞，但每完成一项回调一次 progress。"""

    def __init__(self, torch_python=None, cases=None, extra_pythons=()):
        self.torch_python = torch_python
        self.cases = list(cases or CASE_KEYS)
        self.extra_pythons = extra_pythons
        self.results = {}
        self.engine = None
        self.device = None
        self.torch_version = None
        self.error = None
        self.started = None
        self.finished = None
        self.total_cases = 0
        self.current_index = 0
        self.current_key = None
        self.on_case = None

    def ensure_engine(self):
        if self.engine:
            return self.engine
        py = self.torch_python
        if py is None:
            py, why = find_torch_python(self.extra_pythons)
            if py is None:
                self.error = why
        if py:
            self.torch_python = py
            self.engine = "cublas"
        else:
            self.engine = "opencl"
        return self.engine

    def _spec(self):
        return [{"key": k, "n": n, "dtype": d, "acc": a}
                for k, _l, n, d, a in CASES if k in self.cases]

    def run(self, progress=None, on_case=None):
        """跑一遍全部测试项。

        progress(key, result)  每完成一项回调一次（旧的用法）
        on_case(idx, total, key, phase)  phase ∈ {"start","done"}，给 UI 做进度用
        """
        self.started = time.time()
        self.results = {}
        self.total_cases = len([c for c in CASES if c[0] in self.cases])
        self.current_index = 0
        self.current_key = None
        self.on_case = on_case
        engine = self.ensure_engine()
        try:
            if engine == "cublas":
                self._run_cublas(progress)
            else:
                self._run_opencl(progress)
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            if os.environ.get("GPUINV_DEBUG"):
                import traceback
                traceback.print_exc()
        self.finished = time.time()
        return self.results

    def _run_cublas(self, progress):
        spec = self._spec()
        with tempfile.TemporaryDirectory(prefix="gpuinv_tf_") as td:
            script = os.path.join(td, "bridge.py")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(_BRIDGE)
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            p = subprocess.run([self.torch_python, script, json.dumps(spec)],
                               capture_output=True, timeout=1800, env=env)
            raw = (p.stdout or b"").decode("utf-8", "replace").strip()
            if p.returncode != 0 or not raw:
                err = (p.stderr or b"").decode("utf-8", "replace")[-400:]
                raise RuntimeError("cublas bridge failed: %s" % (err or "no output"))
            data = json.loads(raw.splitlines()[-1])
        self.device = data.get("device")
        self.torch_version = data.get("torch")
        for key, item in (data.get("results") or {}).items():
            if "tflops" in item:
                self.results[key] = {"tflops": item["tflops"], "sec": item.get("sec")}
            else:
                self.results[key] = {"error": item.get("error")}
            self.current_index += 1
            self.current_key = key
            if self.on_case:
                try:
                    self.on_case(self.current_index, self.total_cases, key, "done")
                except Exception:
                    pass
            if progress:
                progress(key, self.results[key])

    def _run_opencl(self, progress):
        from . import bench_ocl
        eng = bench_ocl.OpenCLBench()
        if not eng.open():
            raise RuntimeError(eng.error or "OpenCL unavailable")
        self.device = eng.device_name
        try:
            for n in sorted({c[2] for c in CASES if c[0] in self.cases}):
                key = "gemm_%d" % n
                tflops = eng.gemm_tflops(n if n <= 2048 else 2048)
                self.results[key] = {"tflops": round(tflops, 2)}
                if progress:
                    progress(key, self.results[key])
        finally:
            eng.close()

    def summary(self):
        rows = []
        for key, label, n, dtype, acc in CASES:
            if key not in self.cases:
                continue
            item = self.results.get(key) or {}
            rows.append({"key": key, "label_key": label, "n": n, "dtype": dtype,
                         "acc": acc, "tflops": item.get("tflops"),
                         "error": item.get("error"),
                         "sensitive": key in LOCK_SENSITIVE,
                         "control": key in CONTROL_CASES})
        return rows
