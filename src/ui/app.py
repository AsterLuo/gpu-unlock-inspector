# -*- coding: utf-8 -*-
"""主窗口：装配所有面板、处理事件、串起采集 / 判定 / 基准 / 导出。

界面结构（自上而下）：
- 首屏结论 HeroPanel：状态徽章 + 算力达成率 + 型号事实 + 判定依据 / 手动选型号；
- 分组一 硬件概览：OverviewCard + DetailCard 并排；
- 分组二 算力测试：TflopsCard + BenchCard 并排（原「解锁状态」卡片已移除，
  判定结论由首屏徽章与实测表的「判定」列表达）；
- 分组三 引导 U 盘：UsbCard（含制作入口）；
- 分组四 跨重启对比 / 分组五 测量记录。

线程模型：
- Tk 主线程负责全部 UI；
- 基准测试在后台线程跑，通过 queue 送回样本，主线程用 after(120) 轮询；
- NVML / nvidia-smi 采集在主线程做（耗时 < 1s），不阻塞首屏。
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog

from core import archive as archive_mod
from core import bench_ocl, clipimg, collector, modeldb, ssdiag, storage, tflops, unlock
from core import assets_fetch, deps, envscan, gpu_family, log, usb_boot, usb_maker


def _diag(msg, dt=None):
    """诊断打点。

    注意：--noconsole 打包后 sys.stderr 是 None，直接 .write 会让程序启动即崩。
    这正是打包版「双击没反应」的经典成因，所以这里必须防御性写。
    """
    stream = sys.stderr
    if stream is None or not os.environ.get("GPUINV_DEBUG"):
        return
    try:
        stream.write("[app] %s: %.3f\n" % (msg, dt if dt is not None else 0.0))
        stream.flush()
    except Exception:
        pass
from core import theme as T
from i18n.strings import Translator
from ui.base import Button, Ctx, Segmented, rounded_rect
from ui.dialogs import (BasisDialog, ConfirmType, Modal, OnboardDialog,
                        ProgressDialog)
from ui.panels import (BenchCard, CrossBootCard, DetailCard, HeroPanel,
                     HistoryCard, OverviewCard, TflopsCard, UsbCard, fmt_bytes,
                     fmt_num)
from ui.scroll import ScrollPage


class App(tk.Tk):
    def __init__(self):
        import time as _t
        try:
            self._proc_start = float(os.environ.get("GPUINV_PROC_START") or 0) or None
        except (TypeError, ValueError):
            self._proc_start = None
        _t0 = _t.time()
        super().__init__()
        _diag("Tk super init", _t.time() - _t0)
        self.store = storage.Store()
        self.db = modeldb.GpuDatabase()
        self.tr = Translator(self.store.get("lang", "zh"))
        self.theme_name = self.store.get("theme", "light")
        self.ctx = Ctx(self.theme_name, self.tr, self._dpi_scale(),
                       weight_boost=bool(self.store.get("font_bold", T.BOLD_BY_DEFAULT)),
                       size_boost=T.SIZE_BOOSTS.get(
                           self.store.get("font_scale", "large"), 1.15))
        # 音效 / 动效：从设置恢复；动效还要看系统「减少动态效果」
        from ui import anim as _anim, sound as _sound
        _sound.set_enabled(bool(self.store.get("sound_on", False)))
        _anim.set_enabled(bool(self.store.get("motion_on", True)))

        self.snapshot = None
        self.verdict = None
        self.entry = None
        self.deep = None
        self.archive = archive_mod.Archive()
        self._live = None
        self._live_id = None
        self._live_interval = int(self.store.get("live_interval_ms", 500) or 500)
        self.usb_info = None
        self.usb_log = None
        self.tf_analysis = None
        self.tf_summary = []
        self.tf_runner = None
        # 这两个只在跑过算力实测后才有值。必须给默认值：
        # 没跑过实测时点「存为解锁前」，_save_bundle 会直接读它们，
        # 而 tkinter 会吞掉回调里的 AttributeError —— 表现就是「点了完全没反应、也没有文件」。
        self.compute_verdict = None
        self.combined_verdict = None
        self.dep_installer = None
        self.fetcher = None
        self._onboard_running = False
        self._auto_verified = False
        _saved = self.store.get("torch_python")
        if _saved and os.path.isfile(_saved):
            os.environ["GPUINV_TORCH_PY"] = _saved
        self.torch_env = None        # 探测到的可用解释器
        self.torch_envs = []         # 全部候选
        self._env_scanning = False
        self.last_result = None
        self.runner = None
        self.engine = None
        self._poll_id = None
        self._manual_id = self.store.get("manual_gpu_id")

        _diag("stores ready", _t.time() - _t0)
        self._setup_window()
        _diag("window setup", _t.time() - _t0)
        # 关键：先把窗口画出来（用户能在 1 秒内看到界面），再去采集。
        # 采集无论多慢都不会拖住首屏 —— 这是冷启动体验的分水岭。
        self._build_ui()
        _diag("_build_ui", _t.time() - _t0)
        self.update_idletasks()
        _diag("idletasks", _t.time() - _t0)
        self._loading = True
        self.after(30, self._first_paint)
        self.after(60, self.refresh_data)
        self._install_timing_probe()

    def _install_timing_probe(self):
        """诊断用：设置 GPUINV_TIMING=<文件路径> 时，把首帧可见时刻写出来。

        这条探针只在环境变量存在时生效，正常运行零开销、零副作用。
        """
        target = os.environ.get("GPUINV_TIMING")
        if not target:
            return
        import time as _t
        t_start = self._proc_start or _t.time()

        def _on_map(_evt=None):
            try:
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("%.3f\n" % (_t.time() - t_start))
            except Exception:
                pass
        self.bind("<Map>", _on_map, add="+")

    def _first_paint(self):
        """首帧：所有卡片先以「采集中」状态呈现，避免空白闪烁。"""
        try:
            self._render_all()
            self._loading = False
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ 窗口
    def _dpi_scale(self):
        try:
            return max(1.0, self.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            return 1.0

    def _window_title(self):
        """英文下 title 与 subtitle 相同，别拼出「X · X」。"""
        t = self.tr("app.title")
        s = self.tr("app.subtitle")
        return t if not s or s == t else "%s · %s" % (t, s)

    def _setup_window(self):
        self.title(self._window_title())
        w = self.store.get("window", {}).get("w", 1320)
        h = self.store.get("window", {}).get("h", 880)
        self.geometry("%dx%d" % (w, h))
        # 最小尺寸必须低于 md 断点（1080），否则单栏布局永远不可达
        self.minsize(880, 640)
        self.configure(bg=self.ctx.c("bg"))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.tk.call("tk", "scaling", self.ctx.scale)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 骨架
    def _build_ui(self):
        ctx = self.ctx
        self.topbar = tk.Frame(self, bg=ctx.c("bg"))
        self.topbar.pack(fill="x", side="top")
        self._build_topbar()

        self.page = ScrollPage(self, ctx)
        self.page.pack(fill="both", expand=True)

        body = self.page.content
        pad = ctx.s(T.PAD["page"])
        gap = ctx.s(T.PAD["gap"])

        # ══════════════════ 第一屏：结论 ══════════════════
        self.hero = HeroPanel(body, ctx, self.show_basis, self.pick_model)
        self.hero.pack(fill="x")

        # ══════════════════ 分组一：硬件概览 ══════════════════
        self._group(body, "grp.hardware")
        row1 = tk.Frame(body, bg=ctx.c("bg"))
        row1.pack(fill="x", padx=pad, pady=(0, gap))
        row1.columnconfigure(0, weight=1, uniform="c")
        row1.columnconfigure(1, weight=1, uniform="c")
        self.overview = OverviewCard(row1, ctx)
        self.overview.grid(row=0, column=0, sticky="nsew", padx=(0, gap))
        self.detail = DetailCard(row1, ctx)
        self.detail.grid(row=0, column=1, sticky="nsew")

        # ══════════════════ 分组二：算力测试（两栏并排）══════════════════
        # 原「解锁状态」卡片已移除：判定结论改由首屏徽章 + 算力实测表的「判定」列表达，
        # 腾出的位置正好给算力实测与基准测试这两张卡。
        self._group(body, "grp.bench")
        row2 = tk.Frame(body, bg=ctx.c("bg"))
        row2.pack(fill="x", padx=pad, pady=(0, gap))
        row2.columnconfigure(0, weight=1, uniform="t")
        row2.columnconfigure(1, weight=1, uniform="t")

        self.tf_card = TflopsCard(row2, ctx, self.run_tflops,
                                  on_install=self.install_torch,
                                  on_pick=self.pick_torch_python)
        self.tf_card.grid(row=0, column=0, sticky="nsew", padx=(0, gap))

        self.bench = BenchCard(row2, ctx, self.start_bench, self.stop_bench,
                               self.save_baseline, self.save_after,
                               self.export, self.show_compare)
        self.bench.grid(row=0, column=1, sticky="nsew")
        # 记下需要跟着窗口宽度重排的双栏行
        self._grid_rows = [(row1, self.overview, self.detail),
                           (row2, self.tf_card, self.bench)]
        self._narrow = None
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.after(60, lambda: self._apply_breakpoint(force=True))

        # ══════════════════ 分组三：引导 U 盘 ══════════════════
        # 原来嵌在「解锁状态」卡片底部，卡片移除后独立成一张卡，识别与制作入口都保留。
        self._group(body, "grp.usb")
        self.usb_card = UsbCard(body, ctx, lambda: self.rescan_usb())
        self.usb_card.set_make_handler(self.make_usb)
        self.usb_card.pack(fill="x", padx=pad, pady=(0, gap))

        # ══════════════════ 分组四：跨重启对比 ══════════════════
        self._group(body, "grp.crossboot")
        self.crossboot = CrossBootCard(body, ctx,
                                       self.clear_archive, self.open_archive_folder,
                                       on_import=self.import_bundle,
                                       on_snap=self.snapshot_crossboot)
        self.crossboot.pack(fill="x", padx=pad, pady=(0, gap))

        # ══════════════════ 分组五：记录 ══════════════════
        self._group(body, "grp.history")
        self.history = HistoryCard(body, ctx, self.delete_record, self.clear_records)
        self.history.pack(fill="x", padx=pad, pady=(0, gap))

        self.footer = tk.Label(body, text=self.tr("app.footer"), bg=ctx.c("bg"),
                               fg=ctx.c("ink_faint"), font=ctx.font("small"))
        self.footer.pack(pady=(0, pad))

    # ------------------------------------------------------------ 响应式断点
    def _on_window_configure(self, event):
        """窗口尺寸变化 → 按断点决定双栏还是单栏（等价 CSS media query）。"""
        if event.widget is not self:
            return
        self._apply_breakpoint(event.width)

    def _apply_breakpoint(self, width=None, force=False):
        """md 以上两栏并排；md 以下纵向堆叠，避免文字被压到不可读。

        断点取自 theme.BREAKPOINTS：sm 720 / md 1080 / lg 1320 / xl 1560。
        这里只用 md 这一档 —— 桌面程序窗口最小 1080，再细分没有实际收益。
        """
        if not hasattr(self, "_grid_rows"):
            return
        try:
            w = width if width is not None else self.winfo_width()
        except tk.TclError:
            return
        if w <= 1:
            return
        narrow = w < T.BREAKPOINTS["md"]
        if not force and narrow == self._narrow:
            return
        self._narrow = narrow
        gap = self.ctx.s(T.PAD["gap"])
        for idx, (row, left, right) in enumerate(self._grid_rows):
            uni = "c" if idx == 0 else "t"   # 第一行两卡等宽，第二行两卡也等宽
            try:
                if narrow:
                    # 单栏：右卡挪到第二行，左右间距归零、上下留 gap
                    row.columnconfigure(0, weight=1, uniform=uni)
                    row.columnconfigure(1, weight=0, uniform="")
                    left.grid_configure(row=0, column=0, sticky="nsew", padx=0,
                                        pady=(0, gap))
                    right.grid_configure(row=1, column=0, sticky="nsew", padx=0, pady=0)
                else:
                    row.columnconfigure(0, weight=1, uniform=uni)
                    row.columnconfigure(1, weight=1, uniform=uni)
                    left.grid_configure(row=0, column=0, sticky="nsew", padx=(0, gap),
                                        pady=0)
                    right.grid_configure(row=0, column=1, sticky="nsew", padx=0, pady=0)
            except tk.TclError:
                pass

    def _group(self, parent, key):
        """分组标题：一条细线 + 小字，把长页面切成 5 段，降低阅读负担。"""
        ctx = self.ctx
        pad = ctx.s(T.PAD["page"])
        wrap = tk.Frame(parent, bg=ctx.c("bg"))
        wrap.pack(fill="x", padx=pad, pady=(ctx.s(6), ctx.s(10)))
        tk.Frame(wrap, bg=ctx.c("line"), height=1).pack(side="left", fill="x",
                                                       expand=True, pady=ctx.s(8))
        tk.Label(wrap, text=" " + ctx.tr(key) + " ", bg=ctx.c("bg"),
                 fg=ctx.c("violet"), font=ctx.font("small", bold=True)).pack(side="left")
        tk.Frame(wrap, bg=ctx.c("line"), height=1).pack(side="left", fill="x",
                                                       expand=True, pady=ctx.s(8))

    def _build_topbar(self):
        """顶栏：品牌在左，设置组在右。

        字号放大后一行放不下 7 个控件（实测 1360px 宽时会互相挤压），
        所以设置组内部再分两行：上排是展示类（字号/加粗/语言），
        下排是行为类（动效/音效/主题/刷新）。品牌区垂直居中。
        """
        ctx = self.ctx
        for w in self.topbar.winfo_children():
            w.destroy()
        pad = ctx.s(T.PAD["page"])
        bar = tk.Frame(self.topbar, bg=ctx.c("bg"))
        bar.pack(fill="x", padx=pad, pady=(ctx.s(14), ctx.s(6)))

        brand = tk.Frame(bar, bg=ctx.c("bg"))
        brand.pack(side="left", anchor="n")
        dot = tk.Canvas(brand, bg=ctx.c("bg"), width=ctx.s(28), height=ctx.s(28),
                        bd=0, highlightthickness=0)
        dot.pack(side="left")
        rounded_rect(dot, 0, 0, ctx.s(28), ctx.s(28), ctx.s(9),
                     fill=ctx.c("violet"), outline="")
        dot.create_text(ctx.s(14), ctx.s(14), text="G", fill="#ffffff",
                        font=ctx.font("h3", bold=True))
        tk.Label(brand, text=self.tr("app.title"), bg=ctx.c("bg"), fg=ctx.c("ink"),
                 font=ctx.font("h1", bold=True)).pack(side="left", padx=(ctx.s(10), 0))
        if self.tr("app.subtitle") and self.tr("app.subtitle") != self.tr("app.title"):
            tk.Label(brand, text="· " + self.tr("app.subtitle"), bg=ctx.c("bg"),
                     fg=ctx.c("ink_faint"), font=ctx.font("small")).pack(side="left")

        right = tk.Frame(bar, bg=ctx.c("bg"))
        right.pack(side="right", anchor="n")

        # 上排：字号 / 加粗 / 语言 —— 都是「看得见」的显示设置
        row1 = tk.Frame(right, bg=ctx.c("bg"))
        row1.pack(anchor="e")
        self.font_seg = Segmented(
            row1, ctx,
            [("normal", self.tr("top.font.sm")), ("large", self.tr("top.font.md")),
             ("xlarge", self.tr("top.font.lg"))],
            self.store.get("font_scale", "large"),
            command=self.set_font, bg_name="bg")
        self.font_seg.pack(side="left")
        self.bold_seg = Segmented(
            row1, ctx,
            [("off", self.tr("top.bold.off")), ("on", self.tr("top.bold.on"))],
            "on" if self.store.get("font_bold", T.BOLD_BY_DEFAULT) else "off",
            command=self.set_bold, bg_name="bg")
        self.bold_seg.pack(side="left", padx=(ctx.s(10), 0))
        Segmented(row1, ctx,
                  [("zh", self.tr("top.lang.zh")), ("en", self.tr("top.lang.en"))],
                  self.tr.lang, command=self.set_lang,
                  bg_name="bg").pack(side="left", padx=(ctx.s(10), 0))

        # 下排：动效 / 音效 / 主题 / 刷新 —— 都是「动手」的行为设置
        row2 = tk.Frame(right, bg=ctx.c("bg"))
        row2.pack(anchor="e", pady=(ctx.s(6), 0))
        self.motion_seg = Segmented(
            row2, ctx,
            [("off", self.tr("top.motion.off")), ("on", self.tr("top.motion.on"))],
            "on" if self.store.get("motion_on", True) else "off",
            command=self.set_motion, bg_name="bg")
        self.motion_seg.pack(side="left")
        self.sound_seg = Segmented(
            row2, ctx,
            [("off", self.tr("top.sound.off")), ("on", self.tr("top.sound.on"))],
            "on" if self.store.get("sound_on", False) else "off",
            command=self.set_sound, bg_name="bg")
        self.sound_seg.pack(side="left", padx=(ctx.s(10), 0))
        Segmented(row2, ctx, [("light", self.tr("top.theme.light")),
                              ("dark", self.tr("top.theme.dark"))],
                  self.theme_name, command=self.set_theme,
                  bg_name="bg").pack(side="left", padx=(ctx.s(10), 0))
        Button(row2, ctx, self.tr("top.refresh"), kind="ghost",
               command=self.refresh_data, height=30).pack(side="left",
                                                          padx=(ctx.s(10), 0))

    # ------------------------------------------------------------ 采集 / 判定
    def refresh_data(self):
        try:
            self.footer.configure(text=self.tr("app.loading"))
            self.busy_start(self.tr("app.loading"))
        except Exception:
            pass
        self.update_idletasks()
        try:
            self.snapshot = collector.collect()
        except Exception as exc:
            traceback.print_exc()
            self._show_error(exc)
            self.snapshot = collector.GpuSnapshot()

        try:
            if self.snapshot.primary:
                auto_entry, _how = self.db.find(
                    self.snapshot.primary.get("name"),
                    self.snapshot.primary.get("mem_total_bytes"))
                if self._manual_id and not self.db.by_id(self._manual_id):
                    self._manual_id = None
            self.verdict = unlock.evaluate(self.snapshot, self.db, self._manual_id,
                                           self._bench_summary(), self.deep)
            self.entry = self.verdict.entry
        except Exception as exc:
            traceback.print_exc()
            self._show_error(exc)
            self.verdict = None
            self.entry = None

        # 走 _recompute_unlock 而不是裸 _render_all：
        # 启动时还没有实测算力，必须把结论压成「待检测」，
        # 否则首屏会直接显示驱动推断出来的「已解锁」，用户以为程序已经验过了。
        self._recompute_unlock()
        self._surface_warnings()
        self._first_reveal()
        self._start_live()
        self._sync_archive_state()
        # U 盘扫描要枚举所有卷，放到首帧之后再跑，别拖慢打开速度
        self.after(120, lambda: self.rescan_usb(quiet=True))
        self.after(260, self.scan_torch_env)
        self.after(1400, self.ensure_environment)


    # ------------------------------------------------------------ 统一反馈
    def busy_start(self, text, determinate=False):
        """开始一个耗时操作。

        原则：能拿到真实进度的用百分比（determinate=True），
        拿不到的才用 spinner 转圈 —— 不假装有进度。
        """
        try:
            if determinate:
                self.bench.set_status(text, "violet")
            else:
                self.bench.set_busy(text)
        except Exception:
            pass

    def busy_progress(self, frac, text=None):
        try:
            self.bench.set_progress(frac, text)
        except Exception:
            pass

    def busy_done(self, text):
        try:
            self.bench.set_done(text)
        except Exception:
            traceback.print_exc()

    def busy_error(self, text):
        try:
            self.bench.set_error(text)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ 实时刷新
    def _first_reveal(self):
        """首次渲染时给主卡片一个轻渐入，之后不再重复（避免整页闪动）。"""
        if getattr(self, "_revealed", False):
            return
        self._revealed = True
        try:
            from ui import anim
            if not anim.enabled():
                return
            # 逐张错峰入场（雨滴式）：每张延后 40ms，总时长仍控制在 320ms 内
            for i, card in enumerate((self.overview, self.detail,
                                      self.tf_card, self.bench)):
                self.after(i * 40, lambda c=card: anim.reveal(c, self.ctx))
        except Exception:
            pass

    def _start_live(self):
        """启动高频实时刷新。

        之前实时数字「跟不上」的原因是数据只在采集时取一次。
        现在用独立定时器轮询，只更新热字段，不重建控件。
        """
        self._stop_live()
        try:
            self._live = collector.live_sampler(self.snapshot, 0) if self.snapshot else None
        except Exception:
            traceback.print_exc()
            self._live = None
        if self._live is None:
            return
        self._tick_live()

    def _stop_live(self):
        if self._live_id is not None:
            try:
                self.after_cancel(self._live_id)
            except Exception:
                pass
            self._live_id = None
        if self._live is not None:
            closer = getattr(self._live, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
            self._live = None

    def _tick_live(self):
        if self._live is None:
            return
        try:
            live = self._live() or {}
            self.overview.push_live(live, self.tr)
        except Exception:
            traceback.print_exc()
        interval = max(200, min(5000, self._live_interval))
        self._live_id = self.after(interval, self._tick_live)

    def set_live_interval(self, ms):
        self._live_interval = int(ms)
        self.store.set("live_interval_ms", self._live_interval)
        if self._live is not None:
            self._stop_live()
            self._start_live()

    def _sync_archive_state(self):
        """把本次开机的解锁状态写进存档，用于判断「跨重启后状态变了吗」。"""
        try:
            cv = getattr(self, "combined_verdict", None)
            state = (cv or {}).get("state")
            # 只有实测过的结论才值得写进「状态快照」；pending 不参与跨重启判断
            if (state in ("unlocked", "locked")
                    and self.snapshot and self.snapshot.primary):
                self.archive.update_state(state, self.snapshot.primary)
        except Exception:
            traceback.print_exc()

    def _bench_summary(self):
        if not self.last_result:
            return None
        d = self.last_result.to_dict()
        d["reference_sol"] = self.store.get("bench_reference_sol")
        d["reference_power"] = self.store.get("bench_reference_power")
        return d

    def _render_all(self):
        tr = self.tr
        gpu = self.snapshot.primary if self.snapshot else None
        # 首屏数字：优先用「实测 vs 该型号解锁后参考值」的达成率，
        # 拿不到实测才退回型号库的参考提升（并明确标注是参考值）。
        # 当前算力（本次实测，按主判据 FP16+FP32acc）
        current, unit = None, "TFLOPS"
        if self.tf_summary:
            for row in self.tf_summary:
                if row.get("key") == "fp16_fp32acc_4096" and row.get("tflops"):
                    current = float(row["tflops"])
                    break
            else:
                vals = [r.get("tflops") for r in self.tf_summary
                        if not r.get("control") and r.get("tflops")]
                if vals:
                    current = float(vals[0])
        if current is None and self.last_result:
            # 没有 TFLOPS 时退回基准测试的平均值
            current = float(self.last_result.avg())
            unit = "MSol/s"

        # 解锁前基线：优先取 bundle 存档（两个测试都存），没有就退回旧 sessions
        base = None
        try:
            b = self.archive.latest_bundle("before")
            if b:
                base = archive_mod._bundle_tflops(b) if unit == "TFLOPS" else None
                if base is None and unit == "MSol/s":
                    base = (b.get("bench") or {}).get("avg")
        except Exception:
            traceback.print_exc()

        # 提升幅度只跟「解锁前实测」比，绝不拿型号库参考值冒充实测提升
        gain = None
        if current is not None and base:
            gain = (current - base) / float(base) * 100.0

        # 首屏与徽章只认合并结论 —— 实测不达标时绝不显示「已解锁」
        cv = getattr(self, "combined_verdict", None)
        display_state = (cv or {}).get("state") or (self.verdict.state if self.verdict else "unknown")
        self.hero.update_data(self.verdict, self.entry, gpu, gain, tr, display_state,
                             measured=bool(getattr(self, "compute_verdict", None)),
                             current=current, base=base, unit=unit)
        self.overview.update_data(gpu, self.entry, tr)
        self.detail.update_data(gpu, self.entry, self.verdict, tr,
                               self.snapshot.sources_used if self.snapshot else [])
        self.usb_card.update_data(self.usb_info, self.usb_log, tr)
        self.tf_card.update_data(self.tf_analysis, self.tf_summary, self.entry,
                                 self.db, tr)
        self.history.update_data(self.store.records, tr)
        try:
            self.crossboot.update_data(self.archive, tr,
                                       self.last_result is not None,
                                       pair=self.archive.bundle_pair())
        except Exception:
            traceback.print_exc()
        self.title(self._window_title())
        self.footer.configure(text=tr("app.footer"))
        self.update_idletasks()
        try:
            self.busy_done(tr("app.ready"))
        except Exception:
            pass

    def _surface_warnings(self):
        if not self.snapshot:
            return
        parts = []
        for key, detail in self.snapshot.warnings:
            if key == "msg.no_gpu.body":
                continue
            text = self.tr(key)
            if detail:
                text += "  (" + str(detail)[:120] + ")"
            parts.append(text)
        if not self.snapshot.has_gpu:
            parts.insert(0, self.tr("msg.no_gpu.title"))
        if self.store.readonly:
            parts.append(self.tr("msg.admin.hint"))
        self.bench.set_engine_note("  ·  ".join(parts))

    # ------------------------------------------------------------ 语言 / 主题
    def set_lang(self, lang):
        self.tr.set_lang(lang)
        self.store.set("lang", lang)
        self.ctx = self._make_ctx()
        self._rebuild_everything()

    def _make_ctx(self):
        """用当前设置重建绘制上下文：主题 / 语言 / 字体三组设置都走这里。

        字体两项（加粗、放大档）集中在此处读取，避免各处 Ctx() 调用漏掉设置。
        """
        return Ctx(self.theme_name, self.tr, self.ctx.scale,
                   weight_boost=bool(self.store.get("font_bold", T.BOLD_BY_DEFAULT)),
                   size_boost=T.SIZE_BOOSTS.get(
                       self.store.get("font_scale", "large"), 1.15))

    def set_font(self, value):
        """字体档位：normal / large / xlarge。"""
        self.store.set("font_scale", value)
        self.ctx = self._make_ctx()
        self._rebuild_everything()

    def set_bold(self, value):
        """字体加粗开关。"""
        self.store.set("font_bold", value == "on")
        self.ctx = self._make_ctx()
        self._rebuild_everything()

    def set_sound(self, value):
        """音效开关。开启时立刻播一声 click 作为试听。"""
        from ui import sound
        on = (value == "on")
        self.store.set("sound_on", on)
        sound.set_enabled(on)
        if on:
            sound.play("click")

    def set_motion(self, value):
        """动效开关。关掉后所有过渡走 anim 的即时分支。"""
        from ui import anim
        on = (value == "on")
        self.store.set("motion_on", on)
        anim.set_enabled(on)

    def set_theme(self, name):
        """切主题：整窗底色先做一次 140ms 淡变，再重建（避免硬切闪白）。

        Tk 控件无法逐控件补间（重建会销毁对象），所以只在窗口级做过渡：
        底色过渡到新主题的 bg，再重建 —— 观感上就是「整页柔和换肤」。
        """
        if name == self.theme_name:
            return
        from ui import anim
        prev_ctx = self.ctx
        self.theme_name = name
        self.store.set("theme", name)
        new_bg = T.THEMES.get(name, T.THEMES["light"])["bg"]
        old_bg = prev_ctx.c("bg")
        try:
            anim.run(self, T.duration("fast"),
                     lambda p: self.configure(bg=T.mix(old_bg, new_bg, p)))
        except Exception:
            pass
        self.ctx = self._make_ctx()
        self.configure(bg=self.ctx.c("bg"))
        self._rebuild_everything()

    def _rebuild_everything(self):
        for w in self.winfo_children():
            w.destroy()
        self._build_ui()
        self._render_all()
        self._surface_warnings()
        if self.last_result:
            self.bench.spark.set_data([s["rate"] for s in self.last_result.samples])
            self.bench.push_sample(None, self.last_result)

    # ------------------------------------------------------------ 型号选择
    def pick_model(self):
        choices = self.db.choices()
        dlg = tk.Toplevel(self)
        dlg.title(self.tr("unlock.pick_model"))
        dlg.configure(bg=self.ctx.c("bg"))
        dlg.transient(self)
        dlg.resizable(False, False)
        ctx = self.ctx
        pad = ctx.s(20)
        frame = tk.Frame(dlg, bg=ctx.c("card"))
        frame.pack(fill="both", expand=True, padx=ctx.s(1), pady=ctx.s(1))
        tk.Label(frame, text=self.tr("unlock.pick_model"), bg=ctx.c("card"),
                 fg=ctx.c("ink"), font=ctx.font("h1", bold=True)).pack(
            anchor="w", padx=pad, pady=(pad, ctx.s(10)))

        listbox = tk.Listbox(frame, height=16, width=44, bg=ctx.c("chip_bg"),
                             fg=ctx.c("ink"), bd=0, highlightthickness=0,
                             font=ctx.font("body"), selectbackground=ctx.c("violet"),
                             selectforeground="#ffffff", activestyle="none")
        listbox.pack(padx=pad, fill="both")
        labels = [self.tr("unlock.pick_auto")] + [c[0] for c in choices]
        ids = [None] + [c[1] for c in choices]
        for label in labels:
            listbox.insert("end", label)
        cur = self._manual_id
        if cur in ids:
            listbox.selection_set(ids.index(cur))

        row = tk.Frame(frame, bg=ctx.c("card"))
        row.pack(fill="x", padx=pad, pady=pad)

        def ok():
            sel = listbox.curselection()
            self._manual_id = ids[sel[0]] if sel else None
            self.store.set("manual_gpu_id", self._manual_id)
            dlg.destroy()
            self.refresh_data()

        Button(row, ctx, self.tr("bench.confirm.ok"), kind="solid", command=ok).pack(side="right")
        Button(row, ctx, self.tr("bench.confirm.cancel"), kind="ghost",
               command=dlg.destroy).pack(side="right", padx=(0, ctx.s(8)))
        dlg.update_idletasks()
        dlg.geometry("+%d+%d" % (self.winfo_rootx() + 120, self.winfo_rooty() + 100))

    # ------------------------------------------------------------ 判定依据
    def show_basis(self):
        """判定依据：展示当前真正生效的三层判定链。

        旧实现列的是驱动推断时代的规则表（evaluate() 产出的那批 Rule），
        自从判定改成「实测 TFLOPS 优先」后那几条一条都不参与结论，
        继续显示会让人以为程序还在用驱动版本判解锁。现在只画实际生效的链路。
        """
        combined = getattr(self, "combined_verdict", None) or {}
        ref = {}
        try:
            if self.entry:
                ref = unlock.unlocked_reference(self.entry, self.db) or {}
        except Exception:
            traceback.print_exc()
        dlg = BasisDialog(self, self.ctx, self.tr, combined, self.tf_summary,
                          ref, self.entry, threshold=0.75)
        dlg.show(self)
    # ------------------------------------------------------------ 深度诊断
    def run_deep_probe(self):
        exe = ssdiag.find_ssdiag(modeldb.app_dir(), modeldb.resource_root())
        if not exe:
            Modal.tell(self, self.ctx, self.tr("deep.title"), self.tr("deep.not_found"),
                       self.tr("msg.ok"))
            return
        if not ssdiag.is_admin():
            Modal.tell(self, self.ctx, self.tr("deep.title"), self.tr("deep.no_admin"),
                       self.tr("msg.ok"))
            return
        if not Modal.ask(self, self.ctx, self.tr("deep.title"), self.tr("deep.body"),
                         self.tr("deep.ok"), self.tr("deep.cancel")):
            return

        self.bench.set_status(self.tr("deep.running"), "violet")
        self.update_idletasks()
        try:
            self.deep = ssdiag.run(modeldb.app_dir(), modeldb.resource_root())
        except Exception as exc:
            traceback.print_exc()
            self.deep = ssdiag.DeepProbe()
            self.deep.error = "%s: %s" % (type(exc).__name__, exc)

        key = "deep.state." + (self.deep.state or "unknown")
        msg = "%s\n\n%s: %s\n%s: %s" % (
            self.tr(key), "SS0", ("0x%08X" % self.deep.ss0) if self.deep.ss0 is not None else "—",
            "SS1", ("0x%X" % self.deep.ss1) if self.deep.ss1 is not None else "—")
        if self.deep.platform:
            msg += "\n\n" + "  ".join("%s=%s" % (k, v) for k, v in self.deep.platform.items())
        if self.deep.error:
            msg += "\n\n" + self.deep.error
        Modal.tell(self, self.ctx, self.tr("deep.done"), msg, self.tr("msg.ok"))
        self.bench.set_status(self.tr("app.ready"), "ok")
        self.refresh_data()



# ------------------------------------------------------------ U 盘制作

    # ------------------------------------------------------------ 首次启动引导
    def ensure_environment(self):
        """启动后检查测试环境；缺失就弹引导窗，装完直接开始验证。

        流程（全程同一个弹窗，不叠窗）：
          缺环境 → [安装] → 进度条（Python→venv→torch）→ [开始验证] → 跑算力
        已有环境则静默跳过，不打扰用户。
        """
        if self._onboard_running:
            return
        log.info("onboard", "ensure_environment: torch_env=%s scanning=%s" % (
            bool(self.torch_env), self._env_scanning))
        # 扫描还没结束就等它结束（否则会误判「缺环境」而弹窗）
        if self._env_scanning or not self.torch_envs:
            log.info("onboard", "env scan not finished yet, retry in 500ms")
            self.after(500, self.ensure_environment)
            return
        if self.torch_env:
            # 环境就绪：直接按新逻辑跑一次算力判定（用户要求「启动即测」）
            self.after(400, self.auto_verify)
            return
        if not self._env_scanning and self.torch_envs == []:
            # 扫描还没开始或失败，等下一轮
            return

        self._onboard_running = True
        dlg = OnboardDialog(
            self, self.ctx, self.tr("onb.title"), self.tr("onb.body"),
            cancel_label=self.tr("onb.later"))

        def after_close():
            choice = dlg.result
            self._onboard_running = False
            if choice == "install":
                self._onboard_install()
            elif choice == "verify":
                self.auto_verify()

        try:
            self.wait_window(dlg)
        except tk.TclError:
            pass
        after_close()

    def _onboard_install(self):
        """安装流程：同一个弹窗里展示进度，装完切到「开始验证」。"""
        dlg = OnboardDialog(self, self.ctx, self.tr("onb.title"), "",
                            cancel_label=self.tr("make.cancel"))
        dlg.build_install(self.tr("onb.installing"))

        inst = deps.TorchInstaller()
        self.dep_installer = inst
        inst.start()

        def tick():
            if getattr(dlg, "closed", False) or not dlg.winfo_exists():
                return
            if inst.running:
                dlg.set_progress(inst.progress,
                                 self.tr(self._stage_key(inst.stage)),
                                 inst.events[-1] if inst.events else "")
                self.after(200, tick)
                return
            self.dep_installer = None
            if inst.ok:
                dlg.build_ready(self.tr("onb.ready.title"),
                                self.tr("onb.ready.body"))
                try:
                    self.wait_window(dlg)
                except tk.TclError:
                    pass
                if dlg.result == "verify":
                    self.scan_torch_env()
                    self.after(300, self.auto_verify)
            else:
                dlg.build_install(self.tr("dep.failed"))
                dlg.set_progress(0.0, self.tr("dep.failed"),
                                 (inst.error or "")[:160])

        self.after(300, tick)

    @staticmethod
    def _stage_key(stage):
        return {"python": "dep.stage.python", "venv": "dep.stage.venv",
                "torch": "dep.stage.torch", "verify": "dep.stage.verify",
                "download_python": "dep.stage.download_python",
                }.get(stage, "dep.stage.starting")

    # ------------------------------------------------------------ 启动即测
    def auto_verify(self):
        """按新判定逻辑跑一次算力：远低于该型号解锁后参考值 → 未解锁。"""
        if self.tf_runner is not None or self._auto_verified:
            return
        py = (self.torch_env or {}).get("exe")
        if not py:
            return
        self._auto_verified = True
        log.info("verify", "auto_verify: start with %s" % py)
        self.run_tflops(confirm=False)

    # ------------------------------------------------------------ 测试环境自动识别
    def scan_torch_env(self, deep=True):
        """后台扫一遍本机有没有能跑 TFLOPS 的 Python（torch+CUDA）。

        线程只做 subprocess 探测并把结果塞进队列；
        Tk 控件一律在主线程用 after() 轮询更新 —— Tk 不是线程安全的。
        """
        if self._env_scanning:
            return
        self._env_scanning = True
        log.info("envscan", "start (deep=%s)" % deep)
        self.tf_card.set_scanning(self.tr)
        self._env_queue = queue.Queue()

        def work():
            try:
                items = tflops.scan_environments()
                log.info("envscan", "done: %d candidates" % len(items))
                for it in items:
                    log.debug("envscan", "  %s usable=%s ver=%s" % (
                        it.get("exe"), it.get("usable"), it.get("version")))
                self._env_queue.put(("done", items))
            except Exception as exc:
                log.error("envscan", "failed", exc)
                self._env_queue.put(("error", "%s: %s" % (type(exc).__name__, exc)))

        threading.Thread(target=work, name="envscan", daemon=True).start()
        self._poll_env()

    def _poll_env(self):
        q = getattr(self, "_env_queue", None)
        if q is None:
            return
        try:
            kind, payload = q.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_env)
            return
        self._env_scanning = False
        if kind == "done":
            self.torch_envs = payload
            self.torch_env = next((i for i in payload if i.get("usable")), None)
            self.tf_card.set_env(self.torch_env, payload, self.tr)
        else:
            self.tf_card.set_env(None, [], self.tr)
            if os.environ.get("GPUINV_DEBUG"):
                sys.stderr.write("[envscan] %s\n" % payload)
    def install_torch(self):
        """用户主动点「安装」：弹进度窗，实时显示，装完自动关闭。"""
        if self.dep_installer is not None:
            return
        if not deps.find_system_python():
            Modal.tell(self, self.ctx, self.tr("dep.title"), self.tr("dep.no_python"),
                       self.tr("msg.ok"))
            return

        inst = deps.TorchInstaller()
        dlg = ProgressDialog(self, self.ctx, self.tr("dep.progress.title"),
                             initial=self.tr("dep.progress.starting"),
                             cancel_label=self.tr("make.cancel"),
                             on_cancel=inst.cancel)
        self.dep_installer = inst
        inst.start()

        def tick():
            if dlg.closed:
                return
            stage_key = {"python": "dep.stage.python", "venv": "dep.stage.venv",
                         "download_python": "dep.stage.download_python",
                         "torch": "dep.stage.torch", "verify": "dep.stage.verify",
                         "init": "dep.stage.starting"}.get(inst.stage, "dep.stage.starting")
            detail = inst.events[-1] if inst.events else ""
            dlg.set_progress(inst.progress, self.tr(stage_key), detail[:160])
            if not inst.running:
                self._finish_install(inst, dlg)
                return
            self.after(200, tick)

        self.after(200, tick)

    def _finish_install(self, inst, dlg):
        self.dep_installer = None
        if inst.ok:
            dlg.finish(True, self.tr("dep.ok"))
            self.scan_torch_env()
        elif inst.cancelled:
            dlg.finish(False, self.tr("dep.cancelled"))
        else:
            msg = "\n".join(inst.events[-6:]) or (inst.error or "")
            dlg.finish(False, "%s\n%s" % (self.tr("dep.failed"), msg[:300]))
    def pick_torch_python(self):
        """手动指定一个带 torch 的 python.exe。"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self, title=self.tr("dep.pick.title"),
            filetypes=[("python.exe", "python*.exe"), ("all", "*.*")])
        if not path:
            return
        item = envscan.probe(path)
        if not item.get("usable"):
            Modal.tell(self, self.ctx, self.tr("dep.title"),
                       "%s\n\n%s" % (self.tr("dep.pick.bad"), item.get("note") or ""),
                       self.tr("msg.ok"))
            return
        self.store.set("torch_python", path)
        os.environ["GPUINV_TORCH_PY"] = path
        self.scan_torch_env()

    def make_usb(self):
        """按本机显卡家族从参考项目取资产，然后两次确认写盘。"""
        family, why, dev_id = gpu_family.family_for_snapshot(self.snapshot)
        if family is None:
            key = {"ga100_datacenter": "make.family.unsupported.ga100_datacenter",
                   "blackwell_50": "make.family.unsupported.blackwell_50",
                   "pascal_or_older_skip": "make.family.unsupported.pascal",
                   }.get(why, "make.family.unknown")
            Modal.tell(self, self.ctx, self.tr("make.title"),
                       "%s\n\nPCI: %s" % (self.tr(key), dev_id or "-"),
                       self.tr("msg.ok"))
            return

        # ---- 1. 确保资产（可能联网下载 80MB 固件并现场生成）
        if not assets_fetch.cached_family_ok(family):
            if not Modal.ask(self, self.ctx, self.tr("make.fetch.title"),
                             self.tr("make.fetch.body").format(family=family),
                             self.tr("make.fetch.ok"), self.tr("make.cancel")):
                return
            self.fetch_assets(family)
            return
        self._do_make_usb(family)

    # ------------------------------------------------------------ 资产获取（带进度）
    def fetch_assets(self, family):
        """下载 + 生成资产，全程在一个进度弹窗里。

        以前这里是「确认弹窗关掉 → 光在后台跑 → 界面上什么都不显示」，
        用户完全看不出任务有没有在跑。现在：同一个弹窗接着展示阶段与进度，
        完成后再由调用方接上「确认格式化」。
        """
        fetcher = assets_fetch.AssetFetcher(family)
        self.fetcher = fetcher
        dlg = ProgressDialog(self, self.ctx, self.tr("make.fetch.title2"),
                             initial=self.tr("make.stage.prepare"),
                             cancel_label=self.tr("make.cancel"),
                             on_cancel=fetcher.cancel)
        self.bench.set_status(self.tr("make.fetch.running"), "violet")
        fetcher.start()

        def stage_text():
            return self.tr(fetcher.stage_key) if fetcher.stage_key else ""

        def tick():
            if dlg.closed:
                # 用户主动关窗 = 放弃这次获取
                fetcher.cancel()
                if self.fetcher is fetcher:
                    self.fetcher = None
                self.bench.set_status(self.tr("make.fetch.cancelled"), "warn")
                return
            if fetcher.running:
                # 有真实字节数就显示字节，别把百分比和字节和原始日志堆成一行
                if fetcher.bytes_total:
                    detail = "%s / %s" % (fmt_bytes(fetcher.bytes_done),
                                          fmt_bytes(fetcher.bytes_total))
                else:
                    detail = fetcher.events[-1] if fetcher.events else ""
                dlg.set_progress(fetcher.progress, stage_text(), detail)
                self.bench.set_status("%s %s" % (self.tr("make.fetch.running"),
                                                 stage_text()), "violet")
                self.after(150, tick)
                return

            if self.fetcher is fetcher:
                self.fetcher = None
            if fetcher.ok:
                dlg.set_progress(1.0, self.tr("make.stage.ready"),
                                 self.tr("make.stage.ready"))
                dlg.finish(True, self.tr("make.fetch.done"))
                self.bench.set_status(self.tr("make.fetch.done"), "ok")
                # 弹窗 900ms 后自动关；等它真的关掉再弹「确认格式化」，避免叠窗
                self.after(1200, lambda: self._continue_make(family))
            elif fetcher.cancelled:
                dlg.close()
                self.bench.set_status(self.tr("make.fetch.cancelled"), "warn")
            else:
                dlg.finish(False, "%s\n%s" % (self.tr("make.fetch.failed"),
                                              (fetcher.error or "")[:200]))
                self.bench.set_status(self.tr("make.fetch.failed"), "bad")
                self.after(1500, lambda: Modal.tell(
                    self, self.ctx, self.tr("make.fetch.failed"),
                    "\n".join(fetcher.events[-12:]) or (fetcher.error or ""),
                    self.tr("msg.ok")))

        self.after(150, tick)

    def _continue_make(self, family):
        """资产就绪后继续：确认 → 写盘。窗口可能已关，做个防御。"""
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._do_make_usb(family)

    def _do_make_usb(self, family):
        maker = usb_maker.UsbMaker(family=family)
        plan = maker.build_plan(family)
        if not plan:
            Modal.tell(self, self.ctx, self.tr("make.title"),
                       self.tr("make.assets.none") + "\n\n" +
                       self.tr("make.assets.none.hint"), self.tr("msg.ok"))
            return

        targets = [v for v in usb_boot.scan() if v.get("removable")]
        if not targets:
            Modal.tell(self, self.ctx, self.tr("make.title"), self.tr("make.no_target"),
                       self.tr("msg.ok"))
            return
        target = targets[0]
        letter = target.get("letter")
        mb = maker.total_bytes() / 1048576.0
        size_txt = "%s %s" % (target.get("fs") or "", target.get("label") or "")

        # ---- 第一次确认
        body1 = self.tr("make.confirm1.body").format(
            drive="%s:\\" % letter, size=size_txt.strip() or "?",
            family=family, count=len(plan), mb="%.1f" % mb)
        if not Modal.ask(self, self.ctx, self.tr("make.confirm1.title"), body1,
                         self.tr("make.confirm1.ok"), self.tr("make.cancel")):
            return

        # ---- 第二次确认：手打盘符
        dlg = ConfirmType(self, self.ctx, self.tr("make.confirm2.title"),
                          self.tr("make.confirm2.body").format(letter=letter),
                          self.tr("make.confirm2.hint"), letter,
                          self.tr("make.confirm2.ok"), self.tr("make.cancel"))
        self.wait_window(dlg)
        if not dlg.result:
            return

        # ---- 执行（两次确认都已通过）：后台线程写盘，主线程轮询进度
        self.write_async(letter, maker, family, len(plan))

    def write_async(self, letter, maker, family, count):
        """格式化 + 写盘放后台跑。

        这两步都很慢（格式化几秒、写 81 MB 十几秒），同步在主线程跑会让整个窗口假死，
        用户看到的就是「点了没反应」。改成后台线程 + 进度弹窗。
        """
        dlg = ProgressDialog(self, self.ctx, self.tr("make.write.title"),
                             initial=self.tr("make.write.format"),
                             cancel_label=None)
        self.busy_start(self.tr("make.running"))
        q = queue.Queue()

        def on_progress(kind, text):
            q.put((kind, text))

        def work():
            try:
                ok, msgs = usb_maker.format_and_write(letter, maker.asset_dir,
                                                      family, progress=on_progress)
                q.put(("__done__", (ok, msgs)))
            except Exception as exc:
                q.put(("__done__", (False, [("error", "%s: %s" % (type(exc).__name__, exc))])))

        threading.Thread(target=work, name="usb-write", daemon=True).start()
        copy_i = {"n": 0}
        total = max(1, count)

        def tick():
            if dlg.closed:
                return
            finished = None
            while True:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    break
                if kind == "__done__":
                    finished = payload
                    break
                if kind == "copy":
                    copy_i["n"] += 1
                    dlg.set_progress(min(0.99, copy_i["n"] / float(total)),
                                     self.tr("make.write.copy").format(
                                         rel=payload, index=copy_i["n"], total=total),
                                     payload)
                elif kind == "ok" and payload == "formatted":
                    dlg.set_progress(0.02, self.tr("make.write.verify"),
                                     payload)
                else:
                    dlg.set_progress(None, self.tr("make.write.format"), payload)
            if finished is None:
                self.after(120, tick)
                return

            ok, msgs = finished
            tail = "\n".join(m[1] for m in msgs[-8:])
            if ok:
                dlg.set_progress(1.0, self.tr("make.write.done").format(count=count), tail[-160:])
                dlg.finish(True, self.tr("make.write.done").format(count=count))
                self.busy_done(self.tr("make.done"))
                self.after(1200, lambda: self._after_write(True, tail))
            else:
                dlg.finish(False, self.tr("make.write.failed"))
                self.busy_error(self.tr("make.failed"))
                self.after(1500, lambda: self._after_write(False, tail))

        self.after(120, tick)

    def _after_write(self, ok, tail):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        Modal.tell(self, self.ctx,
                   self.tr("make.done") if ok else self.tr("make.failed"),
                   tail, self.tr("msg.ok"))
        if ok:
            self.rescan_usb()

    # ------------------------------------------------------------ torch 依赖
    def offer_torch(self):
        """没有 torch 时询问是否安装。装完自动重跑实测。"""
        if tflops.find_torch_python()[0] or self.dep_installer is not None:
            return
        if not deps.find_system_python():
            Modal.tell(self, self.ctx, self.tr("dep.title"), self.tr("dep.no_python"),
                       self.tr("msg.ok"))
            return
        if not Modal.ask(self, self.ctx, self.tr("dep.title"), self.tr("dep.body"),
                         self.tr("dep.install"), self.tr("dep.skip")):
            return
        self.dep_installer = deps.TorchInstaller()
        self.dep_installer.start()
        self.bench.set_status(self.tr("dep.running"), "violet")
        self._poll_dep()

    def _poll_dep(self):
        inst = self.dep_installer
        if inst is None:
            return
        if inst.running:
            self.after(700, self._poll_dep)
            return
        body = "\n".join(inst.events[-10:])
        if inst.ok:
            self.bench.set_status(self.tr("dep.ok"), "ok")
            self.tf_runner = None
            Modal.tell(self, self.ctx, self.tr("dep.ok"), body, self.tr("msg.ok"))
        else:
            self.bench.set_status(self.tr("dep.failed"), "bad")
            Modal.tell(self, self.ctx, self.tr("dep.failed"), body, self.tr("msg.ok"))
        self.dep_installer = None

    # ------------------------------------------------------------ U 盘扫描
    def rescan_usb(self, quiet=False):
        """扫一遍全机卷，找解锁引导盘并解析它的 loader.log。"""
        if not quiet:
            self.busy_start(self.tr("usb.scanning"))
        try:
            drives = usb_boot.scan(removable_only=True)
        except Exception:
            traceback.print_exc()
            drives = []
        best = next((d for d in drives if d.get("is_unlocker")), None)
        self.usb_info = best
        self.usb_log = None
        if best and best.get("log"):
            try:
                self.usb_log = usb_boot.parse_log(best["log"])
                # 记录日志时间戳：判定时必须确认「是不是本次开机后写的」
                try:
                    self.usb_log["mtime"] = os.path.getmtime(best["log"])
                except OSError:
                    self.usb_log["mtime"] = None
            except Exception:
                traceback.print_exc()
        try:
            if getattr(self, "usb_card", None) is not None:
                self.usb_card.update_data(self.usb_info, self.usb_log, self.tr)
        except Exception:
            traceback.print_exc()
        if not quiet:
            if self.usb_info:
                self.busy_done(self.tr("usb.found").format(
                    drive="%s:" % self.usb_info.get("letter")))
            else:
                self.busy_error(self.tr("usb.none"))
            self._render_all()
        self._recompute_unlock()

    # ------------------------------------------------------------ Tensor 算力实测
    def run_tflops(self, confirm=False):
        """开始实测。后台线程跑，主线程轮询进度。

        注意：不要在这里再调 find_torch_python() —— 那会重新完整扫描一遍环境（约 3 秒），
        而此刻还没给出任何反馈，用户只会觉得「点了没反应」。环境在启动时已扫好（self.torch_env）。
        """
        if self.tf_runner is not None:
            log.warn("tflops", "already running, ignored")
            return

        py = (self.torch_env or {}).get("exe")
        log.info("tflops", "run_tflops confirm=%s py=%s" % (confirm, py))
        if not py:
            # 还没扫完就再扫一次；扫不出可用环境则引导安装
            if self.dep_installer is None:
                self.busy_start(self.tr("tf.env.scanning"))
                self.update_idletasks()
                self.scan_torch_env()
            if not py and self.dep_installer is None:
                self.offer_torch()
                return
            return

        # 先确认（压测有代价），确认通过后再切「实测中」——
        # 否则弹窗还没答，卡片已经在转圈，会让人以为已经开始跑了。
        if confirm:
            if not Modal.ask(self, self.ctx, self.tr("bench.confirm.title"),
                             self.tr("bench.confirm.body"),
                             self.tr("bench.confirm.ok"),
                             self.tr("bench.confirm.cancel")):
                return
        self.tf_card.set_running(True, self.tr)
        self.update_idletasks()

        runner = tflops.TflopsRunner(torch_python=py)
        runner.ensure_engine()
        self.tf_runner = runner
        # TflopsRunner 没有 cases 属性概念：按 CASE_KEYS 过滤后才准
        self.tf_total = len(runner.cases) if hasattr(runner, "cases") else len(tflops.CASE_KEYS)
        self.tf_done = 0
        self.tf_curve = []
        self.tf_started = time.time()
        self._tf_queue = queue.Queue()

        def on_case(idx, total, key, phase):
            if phase == "done":
                item = runner.results.get(key) or {}
                self._tf_queue.put(("case", (idx, total, key, item.get("tflops"))))

        def work():
            try:
                runner.run(on_case=on_case)
                self._tf_queue.put(("done", None))
            except Exception as exc:
                self._tf_queue.put(("error", "%s: %s" % (type(exc).__name__, exc)))

        threading.Thread(target=work, name="tflops", daemon=True).start()
        self._poll_tflops()

    def _poll_tflops(self):
        q = getattr(self, "_tf_queue", None)
        runner = self.tf_runner
        if q is None or runner is None:
            return
        finished = False
        while True:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                break
            if kind == "case":
                idx, total, key, val = payload
                self.tf_done = idx
                if val is not None:
                    self.tf_curve.append({"t": time.time() - self.tf_started,
                                          "tflops": val, "key": key})
                self.tf_card.push_case(idx, total, key, val,
                                       time.time() - self.tf_started,
                                       self.tf_curve, self.tr)
            elif kind == "done":
                finished = True
            elif kind == "error":
                finished = True
                runner.error = payload
        if finished or not (runner.started and runner.finished is None
                            and getattr(runner, "_running_now", True)):
            if finished or not self._tf_thread_alive():
                self._finish_tflops(runner)
                return
        # 还在跑：更新耗时
        self.tf_card.tick_elapsed(time.time() - self.tf_started, self.tf_done,
                                  max(1, self.tf_total), self.tr)
        self.after(150, self._poll_tflops)

    def _tf_thread_alive(self):
        for t in threading.enumerate():
            if t.name == "tflops" and t.is_alive():
                return True
        return False

    def _finish_tflops(self, runner):
        self.tf_runner = None
        # 实测只有 5 秒左右，若立刻把按钮复原，用户会觉得「闪了一下什么都没发生」。
        # 先保持「实测中…」一小会儿，让结果落地再复原。
        self.after(1200, lambda: self.tf_card.set_running(False, self.tr))
        summary = runner.summary()
        # 新判定：算力 vs 该型号解锁后参考值（型号匹配），日志只在本次开机时佐证
        boot_ts = unlock.boot_time()
        log_info = None
        if self.usb_log:
            log_info = {"mtime": self.usb_log.get("mtime"),
                        "verdict": self.usb_log.get("verdict")}
        judged = unlock.verdict_now(summary, log_info, boot_ts, self.entry, self.db)
        self.compute_verdict = judged
        log.info("tflops", "verdict=%s source=%s detail=%s" % (
            judged["state"], judged["source"], judged["compute"].get("detail")))
        analysis = unlock.analyze_tflops(summary, self.entry, self.db)
        analysis["verdict"] = judged["state"]
        analysis["note_key"] = ("tf.note.self_" + judged["state"]
                                if judged["state"] in ("locked", "unlocked")
                                else "tf.note.self_ambiguous")
        analysis["engine"] = runner.engine
        analysis["device"] = runner.device
        analysis["error"] = runner.error
        self.tf_summary = summary
        self.tf_analysis = analysis
        self.tf_card.set_running(False, self.tr)
        self.tf_card.update_data(analysis, summary, self.entry, self.db, self.tr)
        self._recompute_unlock()

    # ------------------------------------------------------------ 统一重算结论
    def _recompute_unlock(self):
        """合成最终结论：算力实测 &gt; 本次开机日志 &gt; 驱动推断。

        用户要求：启动后立即测算力，远低于该型号解锁后参考值即判未解锁。
        所以 compute_verdict 存在时，它直接决定首屏状态。
        """
        try:
            cv = getattr(self, "compute_verdict", None)
            if cv and cv.get("state") in ("locked", "unlocked"):
                self.combined_verdict = {
                    "state": cv["state"],
                    "note_key": ("xf.note.relocked" if cv["state"] == "locked"
                                 and cv.get("log_state") == "unlocked"
                                 else "xf.note.measured_locked" if cv["state"] == "locked"
                                 else "xf.note.confirmed"),
                    "tf": cv["state"],
                    "log": cv.get("log_state"),
                    "driver": None,
                }
                log.info("verdict", "final=%s (from compute)" % cv["state"])
                self._render_all()
                return

            log_info = None
            if self.usb_log:
                log_info = {"mtime": self.usb_log.get("mtime"),
                            "verdict": self.usb_log.get("verdict")}
            boot_ts = unlock.boot_time()
            judged = unlock.verdict_now(None, log_info, boot_ts, self.entry, self.db)
            self.combined_verdict = {
                "state": judged["state"],
                "note_key": ("xf.note.log_only" if judged["source"] == "log_current_boot"
                             else "xf.note.no_evidence"),
                "tf": None,
                "log": judged.get("log_state"),
                "driver": self.verdict.state if self.verdict else None,
            }
            # 没有本次实测算力时，任何结论都是「推测」而不是「检测结果」。
            # 尤其驱动推断在 LHR 已移除的驱动上会给出「已解锁」，
            # 刚打开软件的那几秒就会闪一个用户根本没验过的结论 —— 一律先记「待检测」。
            self.combined_verdict["state"] = "pending"
            self.combined_verdict["note_key"] = (
                "xf.note.pending_log" if judged["source"] == "log_current_boot"
                else "xf.note.pending")
            log.info("verdict", "final=%s (source=%s)" % (
                self.combined_verdict["state"], judged["source"]))
        except Exception:
            traceback.print_exc()
        self._render_all()

    def _bench_record(self):
        if not self.last_result:
            return None
        d = self.last_result.to_dict()
        return {
            "avg": d["avg"], "peak": d["peak"], "min": d["min"],
            "stability": d["stability"], "power_avg": d["power_avg"],
            "temp_peak": d["temp_peak"], "engine": d["engine"], "algo": d["algo"],
            "intensity": d["intensity"], "device": d["device"],
            "samples": d["samples"], "duration_s": d["duration_s"],
        }

    def _compute_record(self):
        """当前算力实测（TFLOPS），供跨重启对比。"""
        if not self.tf_summary:
            return {}
        return {"summary": self.tf_summary,
                "verdict": (self.tf_analysis or {}).get("verdict"),
                "engine": (self.tf_analysis or {}).get("engine"),
                "device": (self.tf_analysis or {}).get("device")}

    def save_baseline(self):
        self._save_bundle("before")

    def save_after(self):
        self._save_bundle("after")

    def _save_bundle(self, kind):
        """把「算力实测 + 基准测试」一起存成一份文件。

        用户要求：两个测试的数据必须落在同一个文件里，解锁前/解锁后分开两个文件夹。
        任何一边没跑过都可以存 —— 存的是「当时手上有的全部数据」，
        缺的字段留空，比「因为少一项就不让存」更符合实际使用。
        """
        compute = self._compute_record()
        bench = self._bench_record()
        if not compute and not bench:
            Modal.tell(self, self.ctx, self.tr("xboot.title"), self.tr("xboot.need_run"),
                       self.tr("msg.ok"))
            return
        gpu = self.snapshot.primary if self.snapshot else None
        state = ((getattr(self, "compute_verdict", None) or {}).get("state")
                 or (self.verdict.state if self.verdict else "unknown"))
        item, path = self.archive.save_bundle(kind, compute, bench, state, gpu)
        # 同步进「测量记录」：存档是文件，历史卡是时间线，两处都要有
        if bench:
            self.store.add_record(dict(bench, kind=kind))
        # 「解锁后」的实测值同时作为同机参考，后续判定能用上算力/功耗规则
        if kind == "after" and bench:
            self.store.set("bench_reference_sol", bench.get("avg"))
            self.store.set("bench_reference_power", bench.get("power_avg"))
        self._render_all()
        title = (self.tr("xboot.side.after") if kind == "after"
                 else self.tr("xboot.side.before"))
        Modal.tell(self, self.ctx, title,
                   "%s\n\n%s: %s" % (self.tr("xboot.saved"),
                                     self.tr("xboot.saved_hint"), path),
                   self.tr("msg.ok"))

    def import_bundle(self, kind):
        """导入一份存档到 before/ 或 after/。"""
        path = filedialog.askopenfilename(
            parent=self, title=self.tr("xboot.import.pick"),
            filetypes=[("JSON", "*.json"), ("all", "*.*")])
        if not path:
            return
        item = self.archive.import_bundle(path, kind)
        if item is None:
            Modal.tell(self, self.ctx, self.tr("xboot.import.fail"),
                       self.tr("xboot.import.fail_body"), self.tr("msg.ok"))
            return
        self._render_all()
        Modal.tell(self, self.ctx, self.tr("xboot.import.ok"), item["path"],
                   self.tr("msg.ok"))

    def snapshot_crossboot(self):
        """把「跨重启对比」整张卡抓成位图放进剪贴板，可直接 Ctrl+V 发给别人。"""
        card = getattr(self, "crossboot", None)
        if card is None:
            return
        try:
            self.bench.set_status(self.tr("xboot.snap"), "violet")
        except Exception:
            pass
        self.update_idletasks()
        x, y, w, h = card.snap_rect()
        if w < 20 or h < 20:
            Modal.tell(self, self.ctx, self.tr("xboot.snap.fail"),
                       "widget not mapped", self.tr("msg.ok"))
            return
        # 抓图前把本窗口抬到最前，否则被别的窗口遮住会抓到别人
        try:
            self.lift()
            self.focus_force()
            self.update()
        except tk.TclError:
            pass
        stamp = time.strftime("%Y%m%d-%H%M%S")
        png = os.path.join(modeldb.app_dir(),
                           "gpu_unlock_crossboot_%s.png" % stamp)
        ok, info = clipimg.copy_rect_to_clipboard(x, y, w, h, png_path=png)
        if ok:
            try:
                self.bench.set_status(self.tr("xboot.snap.done"), "ok")
            except Exception:
                pass
        else:
            try:
                self.bench.set_status("%s: %s" % (self.tr("xboot.snap.fail"), info), "bad")
            except Exception:
                pass
            Modal.tell(self, self.ctx, self.tr("xboot.snap.fail"), str(info),
                       self.tr("msg.ok"))

    def clear_archive(self):
        if not Modal.ask(self, self.ctx, self.tr("xboot.clear"), self.tr("xboot.note"),
                         self.tr("bench.confirm.ok"), self.tr("bench.confirm.cancel")):
            return
        self.archive.clear_bundles()
        self.archive.clear()
        self._render_all()

    def open_archive_folder(self):
        self.busy_start(self.tr("xboot.open_folder"))
        try:
            os.startfile(self.archive.root)
            self.busy_done(self.tr("xboot.open_folder"))
        except Exception as exc:
            Modal.tell(self, self.ctx, self.tr("xboot.open_folder"),
                       "%s\n%s" % (self.archive.root, exc), self.tr("msg.ok"))

    # ------------------------------------------------------------ 基准测试
    def start_bench(self):
        if self.runner and self.runner.running():
            return
        try:
            duration = int(self.bench.duration.get())
        except (ValueError, tk.TclError):
            duration = 30
        if duration > 120:
            if not Modal.ask(self, self.ctx, self.tr("bench.confirm.title"),
                             self.tr("bench.confirm.body"),
                             self.tr("bench.confirm.ok"), self.tr("bench.confirm.cancel")):
                return

        intensity = self.bench.intensity.value
        algo = self.bench.algo.value
        sampler = collector.live_sampler(self.snapshot, 0) if self.snapshot else (lambda: {})

        if self.engine is None or not getattr(self.engine, "ok", False):
            try:
                self.engine = bench_ocl.best_engine()
            except Exception as exc:
                traceback.print_exc()
                self.engine = bench_ocl.CpuBench()

        if isinstance(self.engine, bench_ocl.CpuBench):
            self.bench.set_engine_note(self.tr("bench.warn.cpu"))
        else:
            self.bench.set_engine_note("%s: %s" % (self.tr("bench.engine"),
                                                   self.engine.device_name or "GPU"))

        self.runner = bench_ocl_run(self.engine, sampler, duration, intensity, algo)
        self.runner.start()
        self.bench.set_running(True)
        self.bench.set_status(self.tr("bench.running"), "violet")
        self.bench.spark.set_data([])
        self._poll_bench()

    def _poll_bench(self):
        # 可被重复调用（after 定时器 + 测试同步驱动），内部无状态副作用
        if not self.runner:
            return
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        events = self.runner.drain()
        done = False
        for kind, payload in events:
            if kind == "sample":
                self.bench.push_sample(payload, self.runner.result)
                pct = int(payload.get("progress", 0) * 100)
                self.bench.set_status("%s  %d%%   %.0fs" % (
                    self.tr("bench.running"), pct, payload.get("t", 0)), "violet")
            elif kind == "done":
                done = True
                self.last_result = payload
                self.bench.push_sample(None, payload)
                self.bench.set_running(False)
                if payload.error:
                    self.bench.set_status(self.tr("exp.fail") + ": " + payload.error, "bad")
                else:
                    avg = payload.avg() or 0
                    self.bench.set_status("%s  %.1f MSol/s  ·  %s %.1f%%" % (
                        self.tr("app.ready"), avg, self.tr("bench.stability"),
                        payload.stability() or 0), "ok")
                self._after_bench_done()
            elif kind == "error":
                self.bench.set_status(str(payload), "bad")
        if not done and self.runner and self.runner.running():
            self._poll_id = self.after(140, self._poll_bench)
        else:
            self.bench.set_running(False)

    def _after_bench_done(self):
        """跑完自动重算判定，因为算力规则现在有数据了。"""
        try:
            self.verdict = unlock.evaluate(self.snapshot, self.db, self._manual_id,
                                           self._bench_summary(), self.deep)
            self.entry = self.verdict.entry
            self._render_all()
        except Exception:
            traceback.print_exc()

    def stop_bench(self):
        if self.runner:
            self.runner.stop()
            self.bench.set_status(self.tr("bench.stop"), "warn")

    # ------------------------------------------------------------ 记录
    def save_measure(self, kind):
        """兼容旧入口：统一走 bundle 存档，别再有第二条落盘路径。"""
        self._save_bundle(kind)

    def delete_record(self, iso):
        self.store.delete_record(iso)
        self.history.update_data(self.store.records, self.tr)

    def clear_records(self):
        self.store.clear_records()
        self.history.update_data(self.store.records, self.tr)

    def show_compare(self):
        before, after = self.store.latest_pair()
        if not before or not after:
            Modal.tell(self, self.ctx, self.tr("bench.compare"), self.tr("bench.no_pair"),
                       self.tr("msg.ok"))
            return
        a, b = before.get("avg") or 0, after.get("avg") or 0
        delta = b - a
        pct = (delta / a * 100.0) if a else 0.0
        body = "%s: %.2f MSol/s\n%s: %.2f MSol/s\n\n%s: %+.2f MSol/s  (%+.2f%%)" % (
            self.tr("hist.before"), a, self.tr("hist.after"), b,
            self.tr("bench.delta"), delta, pct)
        Modal.tell(self, self.ctx, self.tr("bench.compare"), body, self.tr("msg.ok"))

    # ------------------------------------------------------------ 导出
    def export(self, kind):
        if not self.last_result:
            Modal.tell(self, self.ctx, self.tr("exp.fail"), self.tr("bench.no_pair"),
                       self.tr("msg.ok"))
            return
        d = self.last_result.to_dict()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = os.path.join(modeldb.app_dir(), "gpu_unlock_report_%s" % stamp)

        try:
            if kind == "csv":
                path = filedialog.asksaveasfilename(
                    parent=self, title=self.tr("exp.dialog.csv"),
                    defaultextension=".csv", initialfile=os.path.basename(base) + ".csv",
                    filetypes=[("CSV", "*.csv")])
                if not path:
                    return
                with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(["t_s", "rate_MSol_s", "power_W", "clock_MHz", "temp_C"])
                    for s in d["curve"]:
                        w.writerow([s["t"], s["rate"], s["power"], s["clock"], s["temp"]])
                    w.writerow([])
                    w.writerow(["avg", d["avg"]])
                    w.writerow(["peak", d["peak"]])
                    w.writerow(["min", d["min"]])
                    w.writerow(["stability_pct", d["stability"]])

            elif kind == "json":
                path = filedialog.asksaveasfilename(
                    parent=self, title=self.tr("exp.dialog.json"),
                    defaultextension=".json", initialfile=os.path.basename(base) + ".json",
                    filetypes=[("JSON", "*.json")])
                if not path:
                    return
                payload = {
                    "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "gpu": (self.snapshot.primary if self.snapshot else None),
                    "entry_id": (self.entry or {}).get("id"),
                    "verdict": {
                        "state": self.verdict.state if self.verdict else None,
                        "confidence": self.verdict.confidence if self.verdict else None,
                        "rules": [r.to_dict() for r in (self.verdict.rules if self.verdict else [])],
                    },
                    "bench": d,
                }
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)

            elif kind == "png":
                path = filedialog.asksaveasfilename(
                    parent=self, title=self.tr("exp.dialog.png"),
                    defaultextension=".png", initialfile=os.path.basename(base) + ".png",
                    filetypes=[("PNG", "*.png")])
                if not path:
                    return
                self._save_screenshot(path)
                return

            Modal.tell(self, self.ctx, self.tr("exp.done"), path, self.tr("msg.ok"))
        except Exception as exc:
            traceback.print_exc()
            Modal.tell(self, self.ctx, self.tr("exp.fail"),
                       "%s: %s" % (type(exc).__name__, exc), self.tr("msg.ok"))

    def _save_screenshot(self, path):
        """截图：优先用 tkinter 自身 postscript 转 PNG（无第三方依赖）。"""
        try:
            from PIL import ImageGrab  # 可选：Pillow 在环境里时用它
        except Exception:
            ImageGrab = None

        if ImageGrab is not None:
            x = self.winfo_rootx()
            y = self.winfo_rooty()
            w = self.winfo_width()
            h = self.winfo_height()
            self.lift()
            self.update()
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            img.save(path)
            Modal.tell(self, self.ctx, self.tr("exp.done"), path, self.tr("msg.ok"))
            return

        # 无 Pillow：退化为导出报告文本（明确告知，不假装成功）
        txt_path = os.path.splitext(path)[0] + ".txt"
        with open(txt_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self._report_text())
        Modal.tell(self, self.ctx, self.tr("exp.done"), txt_path, self.tr("msg.ok"))

    def _report_text(self):
        d = self.last_result.to_dict() if self.last_result else {}
        gpu = self.snapshot.primary if self.snapshot else {}
        lines = [
            self.tr("app.title"),
            "=" * 48,
            "%s: %s" % (self.tr("f.model"), (gpu or {}).get("name") or "-"),
            "%s: %s" % (self.tr("f.driver"), (gpu or {}).get("driver_version") or "-"),
            "%s: %s" % (self.tr("f.vbios"), (gpu or {}).get("vbios_version") or "-"),
            "%s: %s" % (self.tr("hero.status"),
                        self.tr("state." + (self.verdict.state if self.verdict else "unknown"))),
            "",
            "%s: %.2f MSol/s" % (self.tr("bench.avg"), d.get("avg") or 0),
            "%s: %.2f MSol/s" % (self.tr("bench.peak"), d.get("peak") or 0),
            "%s: %.1f%%" % (self.tr("bench.stability"), d.get("stability") or 0),
            "",
            self.tr("unlock.basis"),
        ]
        for r in (self.verdict.rules if self.verdict else []):
            lines.append("  %-28s %s = %s [%s]" % (self.tr(r.key), r.field or "-",
                                                   r.actual, r.threshold or "-"))
        return "\n".join(lines)

    # ------------------------------------------------------------ 错误
    def _show_error(self, exc):
        try:
            Modal.tell(self, self.ctx, self.tr("msg.err.title"),
                       "%s\n\n%s" % (self.tr("msg.err.body"), exc), self.tr("msg.ok"))
        except Exception:
            pass
        # 同时落一份日志，便于事后复核
        try:
            log = os.path.join(modeldb.app_dir(), "gpu_inspector_crash.log")
            with open(log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n===== %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                traceback.print_exc(file=fh)
        except Exception:
            pass

    # ------------------------------------------------------------ 关闭
    def _on_close(self):
        try:
            self._stop_live()
            if self.runner:
                self.runner.stop()
            if self.engine:
                self.engine.close()
            self.store.set("window", {"w": self.winfo_width(), "h": self.winfo_height()})
            if self.snapshot and self.snapshot.nvml:
                self.snapshot.nvml.shutdown()
        except Exception:
            pass
        self.destroy()


def bench_ocl_run(engine, sampler, duration, intensity, algo):
    from core.runner import BenchRunner
    return BenchRunner(engine, sampler, duration, intensity, algo)


def main():
    app = App()
    try:
        app.mainloop()
    except Exception:
        traceback.print_exc()
        try:
            app.destroy()
        except Exception:
            pass
        raise