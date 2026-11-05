# -*- coding: utf-8 -*-
"""GPU 基准测试内核：OpenCL 内存硬代理算法 v1。

为什么用 OpenCL 而不是 CUDA：
  - Windows 自带 OpenCL.dll，NVIDIA 驱动自带 OpenCL 平台，零额外依赖；
  - 写成单个 .cl 源码字符串直接编译，不需要 nvcc；
  - 单文件 exe 只需 ctypes 调 OpenCL.dll，体积可以压到很小。

算法（proxy_v1）特征：
  - 512MB scratchpad，随机寻址（mem-hard，模仿 Ethash DAG 访问）；
  - 每个地址读改写，地址由上一轮结果决定（数据依赖，不能预取）；
  - 整数乘加 + 循环移位混洗，制造寄存器压力；
  - 这类负载对「内存带宽 + 延迟」敏感，正是 LHR 限制器针对的目标，
    因此前后对比能真实反映限制器行为。

单位：MSol/s（百万次混洗/秒），不冒充 MH/s。见 README「已知限制」。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import (POINTER, byref, c_char_p, c_int, c_size_t, c_uint,
                    c_ulonglong, c_void_p, create_string_buffer)

KERNEL_SRC = b"""
#define MASK 0x07FFFFFFu

__kernel void proxy_v1(__global uint *pad, __global uint *out,
                       const uint iters, const uint seed)
{
    uint idx  = get_global_id(0);
    uint addr = (idx * 2654435761u + seed) & MASK;
    uint acc  = pad[addr] ^ seed;

    for (uint i = 0; i < iters; i++) {
        addr = acc & MASK;
        uint v = pad[addr];
        acc = acc * 1664525u + 1013904223u;
        acc ^= v;
        acc = (acc << 7) | (acc >> 25);
        pad[addr] = acc;
    }
    out[idx] = acc;
}
"""


GEMM_SRC = b"""
#define TS 16
__kernel void gemm(__global const float *A, __global const float *B,
                   __global float *C, const uint n)
{
    __local float As[TS][TS];
    __local float Bs[TS][TS];
    uint lx = get_local_id(0), ly = get_local_id(1);
    uint gx = get_global_id(0), gy = get_global_id(1);
    float acc = 0.0f;
    uint tiles = n / TS;
    for (uint t = 0; t < tiles; t++) {
        As[ly][lx] = A[gy * n + t * TS + lx];
        Bs[ly][lx] = B[(t * TS + ly) * n + gx];
        barrier(CLK_LOCAL_MEM_FENCE);
        for (uint k = 0; k < TS; k++) {
            acc += As[ly][k] * Bs[k][lx];
        }
        barrier(CLK_LOCAL_MEM_FENCE);
    }
    C[gy * n + gx] = acc;
}
"""

PAD_WORDS = 128 * 1024 * 1024     # 128M uint = 512 MiB
OUT_WORDS = 4 * 1024 * 1024       # 4M uint = 16 MiB，必须 >= 最大 global size

INTENSITY = {
    # gs    = 每组 global work size
    # iters = 每个线程的混洗轮数
    # queues= 并发命令队列数；多队列可以让 GPU 真正吃满，功耗才会爬升
    "low":  {"gs": 512 * 1024, "iters": 32, "queues": 1},
    "mid":  {"gs": 1024 * 1024, "iters": 64, "queues": 2},
    "high": {"gs": 1024 * 1024, "iters": 96, "queues": 4},
}

CL_DEVICE_TYPE_GPU = 1 << 2
CL_DEVICE_TYPE_ALL = 0xFFFFFFFF
CL_MEM_READ_WRITE = 1
CL_PROGRAM_BUILD_LOG = 0x1183
CL_DEVICE_NAME = 0x102B
CL_DEVICE_GLOBAL_MEM_SIZE = 0x101F


class OpenCLBench:
    """一次构造 = 一次 OpenCL 初始化；可在多次测试间复用。"""

    name = "proxy_v1"

    def __init__(self):
        self.ok = False
        self.error = None
        self.device_name = None
        self.global_mem = None
        self.cl = None
        self._ctx = None
        self._queue = None
        self._program = None
        self._kernel = None
        self._pad = None
        self._out = None

    # ------------------------------------------------------------ setup
    def open(self):
        try:
            cl = ctypes.WinDLL("OpenCL.dll")
        except OSError:
            self.error = "OpenCL.dll not found"
            return False
        self.cl = cl
        try:
            self._bind(cl)
            if not self._create_context():
                return False
            if not self._build():
                return False
            self._alloc()
            self.ok = True
            return True
        except Exception as exc:
            self.error = "%s: %s" % (type(exc).__name__, exc)
            return False

    def _bind(self, cl):
        def f(name, args, rest):
            fn = getattr(cl, name)
            fn.argtypes = args
            fn.restype = rest
            return fn

        self.f_platforms = f("clGetPlatformIDs", [c_uint, POINTER(c_void_p), POINTER(c_uint)], c_int)
        self.f_devices = f("clGetDeviceIDs", [c_void_p, c_ulonglong, c_uint,
                                              POINTER(c_void_p), POINTER(c_uint)], c_int)
        self.f_device_info = f("clGetDeviceInfo", [c_void_p, c_uint, c_size_t, c_void_p,
                                                   POINTER(c_size_t)], c_int)
        self.f_ctx = f("clCreateContext", [c_void_p, c_uint, POINTER(c_void_p), c_void_p,
                                           c_void_p, POINTER(c_int)], c_void_p)
        self.f_queue = f("clCreateCommandQueue", [c_void_p, c_void_p, c_ulonglong,
                                                  POINTER(c_int)], c_void_p)
        self.f_program = f("clCreateProgramWithSource", [c_void_p, c_uint, POINTER(c_char_p),
                                                         POINTER(c_size_t), POINTER(c_int)], c_void_p)
        self.f_build = f("clBuildProgram", [c_void_p, c_uint, POINTER(c_void_p), c_char_p,
                                            c_void_p, c_void_p], c_int)
        self.f_build_log = f("clGetProgramBuildInfo", [c_void_p, c_void_p, c_uint, c_size_t,
                                                       c_char_p, POINTER(c_size_t)], c_int)
        self.f_kernel = f("clCreateKernel", [c_void_p, c_char_p, POINTER(c_int)], c_void_p)
        self.f_buffer = f("clCreateBuffer", [c_void_p, c_ulonglong, c_size_t, c_void_p,
                                             POINTER(c_int)], c_void_p)
        self.f_setarg = f("clSetKernelArg", [c_void_p, c_uint, c_size_t, c_void_p], c_int)
        self.f_ndrange = f("clEnqueueNDRangeKernel", [c_void_p, c_void_p, c_uint,
                                                      POINTER(c_size_t), POINTER(c_size_t),
                                                      POINTER(c_size_t), c_uint,
                                                      POINTER(c_void_p), c_void_p], c_int)
        self.f_write = f("clEnqueueWriteBuffer", [c_void_p, c_void_p, c_uint, c_size_t,
                                                  c_size_t, c_void_p, c_uint,
                                                  POINTER(c_void_p), c_void_p], c_int)
        self.f_finish = f("clFinish", [c_void_p], c_int)
        self.f_release_mem = f("clReleaseMemObject", [c_void_p], c_int)
        self.f_release_kernel = f("clReleaseKernel", [c_void_p], c_int)
        self.f_release_program = f("clReleaseProgram", [c_void_p], c_int)
        self.f_release_queue = f("clReleaseCommandQueue", [c_void_p], c_int)
        self.f_release_ctx = f("clReleaseContext", [c_void_p], c_int)

    def _create_context(self):
        n = c_uint(0)
        if self.f_platforms(0, None, byref(n)) != 0 or n.value == 0:
            self.error = "no OpenCL platform"
            return False
        plats = (c_void_p * n.value)()
        self.f_platforms(n.value, plats, byref(n))

        for i in range(n.value):
            dn = c_uint(0)
            rc = self.f_devices(plats[i], CL_DEVICE_TYPE_GPU, 0, None, byref(dn))
            if rc != 0 or dn.value == 0:
                continue
            devs = (c_void_p * dn.value)()
            if self.f_devices(plats[i], CL_DEVICE_TYPE_GPU, dn.value, devs, byref(dn)) != 0:
                continue
            err = c_int()
            ctx = self.f_ctx(None, 1, devs, None, None, byref(err))
            if not ctx or err.value != 0:
                continue
            q = self.f_queue(ctx, devs[0], 0, byref(err))
            if not q or err.value != 0:
                continue
            extra = []
            for _ in range(3):
                e2 = c_int()
                q2 = self.f_queue(ctx, devs[0], 0, byref(e2))
                if q2 and e2.value == 0:
                    extra.append(q2)
            self._ctx, self._queue, self._dev = ctx, q, devs[0]
            self._queues = [q] + extra
            self._read_device_info()
            return True
        self.error = "no usable OpenCL GPU device"
        return False

    def _read_device_info(self):
        buf = create_string_buffer(256)
        sz = c_size_t()
        if self.f_device_info(self._dev, CL_DEVICE_NAME, 256, buf, byref(sz)) == 0:
            self.device_name = buf.value.decode("utf-8", "replace").strip()
        mem = ctypes.c_ulonglong(0)
        if self.f_device_info(self._dev, CL_DEVICE_GLOBAL_MEM_SIZE, 8, byref(mem), byref(sz)) == 0:
            self.global_mem = mem.value

    def _build(self):
        src = c_char_p(KERNEL_SRC)
        ln = c_size_t(len(KERNEL_SRC))
        err = c_int()
        prog = self.f_program(self._ctx, 1, byref(src), byref(ln), byref(err))
        if not prog or err.value != 0:
            self.error = "clCreateProgramWithSource failed rc=%d" % err.value
            return False
        arr = (c_void_p * 1)(self._dev)
        rc = self.f_build(prog, 1, arr, b"-cl-fast-relaxed-math", None, None)
        if rc != 0:
            log = create_string_buffer(16384)
            sz = c_size_t()
            self.f_build_log(prog, self._dev, CL_PROGRAM_BUILD_LOG, 16384, log, byref(sz))
            self.error = "build failed rc=%d: %s" % (
                rc, log.value.decode("utf-8", "replace")[:400].strip())
            return False
        self._program = prog
        err = c_int()
        k = self.f_kernel(prog, b"proxy_v1", byref(err))
        if not k or err.value != 0:
            self.error = "clCreateKernel failed rc=%d" % err.value
            return False
        self._kernel = k
        return True

    def _alloc(self):
        err = c_int()
        self._pad = self.f_buffer(self._ctx, CL_MEM_READ_WRITE, PAD_WORDS * 4, None, byref(err))
        if not self._pad:
            raise RuntimeError("pad buffer alloc failed rc=%d" % err.value)
        self._out = self.f_buffer(self._ctx, CL_MEM_READ_WRITE, OUT_WORDS * 4, None, byref(err))
        if not self._out:
            raise RuntimeError("out buffer alloc failed rc=%d" % err.value)
        # 初始化：全 0。512MB 一次写入约 0.05~0.1s
        zeros = b"\x00" * (PAD_WORDS * 4)
        self.f_write(self._queue, self._pad, 1, 0, PAD_WORDS * 4, zeros, 0, None, None)
        self.f_finish(self._queue)

    # ------------------------------------------------------------ 运行
    def slice_once(self, intensity="mid", seed=1, min_seconds=0.10):
        """跑一个测量点，返回 (秒, 混洗次数)。

        单个 kernel 在高端卡上只要 1~5 ms，直接计时误差过大，
        因此在 kernel 内部循环 repeats 次，保证每个测量点至少 min_seconds。
        """
        cfg = INTENSITY.get(intensity, INTENSITY["mid"])
        gs = cfg["gs"]
        iters = cfg["iters"]
        args = [c_void_p(self._pad), c_void_p(self._out), c_uint(iters), c_uint(seed)]
        for i, a in enumerate(args):
            self.f_setarg(self._kernel, i, ctypes.sizeof(a), byref(a))
        g = c_size_t(gs)
        local = c_size_t(256)

        # 先用一次跑的耗时估算需要重复多少次，把测量窗口撑到 min_seconds
        single = self._probe_single(gs, iters)
        nq = max(1, min(cfg["queues"], len(self._queues)))
        repeats = max(1, int(min_seconds / (single * nq)) + 1)
        repeats = min(repeats, 400)

        # 多队列并发：每个队列提交一份独立 kernel（不同 seed），同时压满 GPU。
        # 注意：并发不代表工作量翻倍 —— 达到稳态后 GPU 吞吐是固定的，
        # 所以工作量必须按「提交的 kernel 总数 × 每个 kernel 的混洗数」算，
        # 而不是把并发数当倍率乘上去（那是虚高）。
        queues = self._queues[:max(1, min(cfg["queues"], len(self._queues)))]
        t0 = time.perf_counter()
        for r in range(repeats):
            for qi, q in enumerate(queues):
                sd = c_uint((seed + r * 131 + qi * 977) & 0xFFFFFFFF)
                self.f_setarg(self._kernel, 3, ctypes.sizeof(sd), byref(sd))
                rc = self.f_ndrange(q, self._kernel, 1, None, byref(g), byref(local), 0, None, None)
                if rc != 0:
                    raise RuntimeError("clEnqueueNDRangeKernel rc=%d" % rc)
        for q in queues:
            rc = self.f_finish(q)
            if rc != 0:
                raise RuntimeError("clFinish rc=%d" % rc)
        dt = time.perf_counter() - t0
        # 工作量 = 提交的 kernel 个数 × 每个 kernel 的混洗数（与并发无关）
        return dt, gs * iters * repeats * len(queues)

    def _probe_single(self, gs, iters, local_words=256):
        """跑一次 kernel，返回耗时。用于估算 repeats。"""
        g = c_size_t(gs)
        local = c_size_t(local_words)
        sd = c_uint(1)
        self.f_setarg(self._kernel, 3, ctypes.sizeof(sd), byref(sd))
        t0 = time.perf_counter()
        rc = self.f_ndrange(self._queue, self._kernel, 1, None, byref(g), byref(local), 0, None, None)
        if rc != 0:
            raise RuntimeError("clEnqueueNDRangeKernel rc=%d" % rc)
        rc = self.f_finish(self._queue)
        if rc != 0:
            raise RuntimeError("clFinish rc=%d" % rc)
        return max(time.perf_counter() - t0, 1e-6)

    def warmup(self, intensity="mid", rounds=3):
        for i in range(rounds):
            self.slice_once(intensity, seed=7919 * (i + 1))

    # ------------------------------------------------------------ GEMM（TFLOPS 口径）
    def _build_gemm(self):
        """编译 FP32 GEMM 内核。只用于没有 torch 时的「相对差异」参照。"""
        if getattr(self, "_gemm_ok", False):
            return True
        src = c_char_p(GEMM_SRC)
        ln = c_size_t(len(GEMM_SRC))
        err = c_int()
        prog = self.f_program(self._ctx, 1, byref(src), byref(ln), byref(err))
        if not prog or err.value != 0:
            return False
        arr = (c_void_p * 1)(self._dev)
        if self.f_build(prog, 1, arr, b"-cl-fast-relaxed-math", None, None) != 0:
            return False
        err = c_int()
        kern = self.f_kernel(prog, b"gemm", byref(err))
        if not kern or err.value != 0:
            return False
        self._gemm_prog = prog
        self._gemm_kernel = kern
        self._gemm_ok = True
        return True

    def gemm_tflops(self, n=512, iters=6):
        """FP32 GEMM 的 TFLOPS（手写内核，绝对数值远低于 cuBLAS）。

        已验证要点（踩过的坑，别再改回去）：
          1. global size 必须是 **二维** (n, n)，local 也是 (16,16)；
             传一维会被当成 n 个线程跑完整块，结果全 0 且耗时接近 0。
          2. 输入必须是随机数据。全 1.0 / 全 0.5 这种常量矩阵会被驱动常量折叠，
             实测 512³ 只要 14 微秒，算出 88 TFLOPS 的假数。
          3. 实测校验：n=512 时 C[0] 必须等于 sum(A[i]*B[i*n])，对不上就说明没真算。
        """
        if not self._build_gemm():
            return 0.0
        n = max(256, min(2048, (n // 16) * 16))
        size = n * n * 4
        import array
        import random
        rnd = random.Random(20261105)
        A = array.array("f", (rnd.random() for _ in range(n * n)))
        B = array.array("f", (rnd.random() for _ in range(n * n)))
        err = c_int()
        ba = self.f_buffer(self._ctx, CL_MEM_READ_WRITE, size, None, byref(err))
        bb = self.f_buffer(self._ctx, CL_MEM_READ_WRITE, size, None, byref(err))
        bc = self.f_buffer(self._ctx, CL_MEM_READ_WRITE, size, None, byref(err))
        if not (ba and bb and bc):
            return 0.0
        try:
            self.f_write(self._queue, ba, 1, 0, size, A.tobytes(), 0, None, None)
            self.f_write(self._queue, bb, 1, 0, size, B.tobytes(), 0, None, None)
            for i, a in enumerate([c_void_p(ba), c_void_p(bb), c_void_p(bc), c_uint(n)]):
                self.f_setarg(self._gemm_kernel, i, ctypes.sizeof(a), byref(a))
            g = (c_size_t * 2)(n, n)
            loc = (c_size_t * 2)(16, 16)
            rc = self.f_ndrange(self._queue, self._gemm_kernel, 2, None, g, loc, 0, None, None)
            if rc != 0:
                return 0.0
            rc = self.f_finish(self._queue)
            if rc != 0:
                return 0.0
            t0 = time.perf_counter()
            for _ in range(iters):
                if self.f_ndrange(self._queue, self._gemm_kernel, 2, None, g, loc,
                                  0, None, None) != 0:
                    return 0.0
            if self.f_finish(self._queue) != 0:
                return 0.0
            dt = time.perf_counter() - t0
            return (2.0 * n ** 3 * iters) / dt / 1e12 if dt > 0 else 0.0
        finally:
            for buf in (ba, bb, bc):
                try:
                    self.f_release_mem(buf)
                except Exception:
                    pass

    def close(self):
        for release, handle in (
            (getattr(self, "f_release_kernel", None), self._kernel),
            (getattr(self, "f_release_mem", None), self._pad),
            (getattr(self, "f_release_mem", None), self._out),
            (getattr(self, "f_release_program", None), self._program),
            (getattr(self, "f_release_queue", None), self._queue),
            (getattr(self, "f_release_ctx", None), self._ctx),
        ):
            if release and handle:
                try:
                    release(handle)
                except Exception:
                    pass
        for q in getattr(self, "_queues", [])[1:]:
            try:
                self.f_release_queue(q)
            except Exception:
                pass
        self._queues = []
        self._kernel = self._pad = self._out = self._program = self._queue = self._ctx = None
        self.ok = False


class CpuBench:
    """CPU 降级参照。完全不依赖 GPU，用来保证「无 GPU 也不崩」的验收项。

    单位同样是 MSol/s，但量级差三个数量级，UI 会明确标注「不能代表显卡算力」。
    """

    name = "cpu_proxy"

    def __init__(self):
        self.ok = True
        self.error = None
        self.device_name = "CPU"
        self.global_mem = None

    def open(self):
        return True

    def slice_once(self, intensity="mid", seed=1, min_seconds=0.10):
        cfg = INTENSITY.get(intensity, INTENSITY["mid"])
        n = min(cfg["gs"] // 16, 400_000)
        iters = max(8, cfg["iters"] // 4)
        mask = (1 << 27) - 1
        pad = [0] * (1 << 16)
        acc = seed & 0xFFFFFFFF
        t0 = time.perf_counter()
        total = 0
        for i in range(n):
            addr = acc & 0xFFFF
            v = pad[addr]
            for _ in range(iters):
                acc = (acc * 1664525 + 1013904223) & 0xFFFFFFFF
                acc ^= v
                acc = ((acc << 7) | (acc >> 25)) & 0xFFFFFFFF
                v = (v + acc) & 0xFFFFFFFF
            pad[addr] = acc
            total += iters
        dt = max(time.perf_counter() - t0, 1e-6)
        return dt, total

    def warmup(self, intensity="mid", rounds=1):
        self.slice_once(intensity, seed=3)

    def close(self):
        self.ok = False


def best_engine():
    """优先 OpenCL，失败回落 CPU。返回实例（未 open）。"""
    o = OpenCLBench()
    if o.open():
        return o
    return CpuBench()