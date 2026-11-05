# -*- coding: utf-8 -*-
"""面板层：首屏结论 + 五张功能卡片。

每条文案走 i18n，每个颜色走 theme，没有一个字面量。
数据一律可缺失（None → 显示「未知」），绝不因为缺字段而抛异常。
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from core import theme as T
from ui.base import (Badge, Button, Card, Metric, Segmented, Sparkline, rounded_rect)
from ui.busy import StatusLine


# ===================================================================== 格式化工具
def fmt_num(v, digits=0, unit="", unknown=""):
    if v is None or v == "":
        return unknown
    try:
        if digits == 0:
            return ("%d" % round(float(v))) + ((" " + unit) if unit else "")
        return ("%.*f" % (digits, float(v))) + ((" " + unit) if unit else "")
    except (TypeError, ValueError):
        return unknown


def fmt_bytes(nbytes, unknown=""):
    if not nbytes:
        return unknown
    gb = nbytes / (1024 ** 3)
    if gb >= 1:
        return "%.0f GB" % gb
    return "%.0f MB" % (nbytes / (1024 ** 2))


def fmt_pct(v, digits=1, unknown=""):
    if v is None:
        return unknown
    return "%.*f%%" % (digits, v)


class Section(tk.Frame):
    """卡片里的一个字段行：标签左、值右、来源小标。"""

    def __init__(self, master, ctx, label, value, source=None, strong=False,
                 value_color=None):
        self.ctx = ctx
        bg = ctx.c("card")
        super().__init__(master, bg=bg)
        self.columnconfigure(0, weight=1)
        tk.Label(self, text=label, bg=bg, fg=ctx.c("ink_faint"),
                 font=ctx.font("small"), anchor="w").grid(row=0, column=0, sticky="w")
        self.value_label = tk.Label(
            self, text=value, bg=bg,
            fg=ctx.c(value_color) if value_color else ctx.c("ink" if strong else "ink_soft"),
            font=ctx.font("body" if not strong else "h3", bold=strong), anchor="e")
        self.value_label.grid(row=0, column=1, sticky="e")
        if source:
            tk.Label(self, text=source, bg=bg, fg=ctx.c("ink_faint"),
                     font=ctx.font("small"), anchor="e").grid(row=0, column=2,
                                                              sticky="e", padx=(ctx.s(8), 0))


# ===================================================================== 首屏
class HeroPanel(tk.Frame):
    """打开就给结论：状态徽章 + 算力提升百分比 + 四个关键事实。"""

    def __init__(self, master, ctx, on_show_basis, on_pick_model=None):
        self.ctx = ctx
        self.on_show_basis = on_show_basis
        self.on_pick_model = on_pick_model
        super().__init__(master, bg=ctx.c("bg"))
        self._build()

    def _build(self):
        ctx = self.ctx
        self.canvas = tk.Canvas(self, bg=ctx.c("bg"), bd=0, highlightthickness=0,
                                height=ctx.s(200))
        self.canvas.pack(fill="x")
        self.inner = tk.Frame(self.canvas, bg=ctx.c("bg"))
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", self._on_resize)
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(height=e.height + ctx.s(2)))

        pad = ctx.s(T.PAD["page"])
        wrap = tk.Frame(self.inner, bg=ctx.c("bg"))
        wrap.pack(fill="x", padx=pad, pady=(ctx.s(4), ctx.s(10)))

        # ---- 左：结论
        left = tk.Frame(wrap, bg=ctx.c("bg"))
        left.pack(side="left", anchor="n")
        tk.Label(left, text=ctx.tr("hero.eyebrow"), bg=ctx.c("bg"),
                 fg=ctx.c("violet"), font=ctx.font("small", bold=True),
                 anchor="w").pack(anchor="w")

        row = tk.Frame(left, bg=ctx.c("bg"))
        row.pack(anchor="w", pady=(ctx.s(8), 0))
        self.badge = Badge(row, ctx, "unknown", big=True)
        self.badge.pack(side="left")

        # 指标行：标题（当前算力 / 较解锁前）+ 数值 + 说明。
        # 标题随模式变，数值永远带符号，别让「未解锁」配一个正的百分比。
        self.gain_row = tk.Frame(left, bg=ctx.c("bg"))
        self.gain_row.pack(anchor="w", pady=(ctx.s(12), 0))
        self.gain_caption = tk.Label(self.gain_row, text=ctx.tr("hero.metric.pending"),
                                     bg=ctx.c("bg"), fg=ctx.c("ink_faint"),
                                     font=ctx.font("small"))
        self.gain_caption.pack(side="left")
        self.gain_label = tk.Label(self.gain_row, text="—", bg=ctx.c("bg"),
                                   fg=ctx.c("violet"), font=ctx.font("h1", bold=True))
        self.gain_label.pack(side="left", padx=(ctx.s(8), 0))
        self.gain_unit = tk.Label(self.gain_row, text="", bg=ctx.c("bg"),
                                  fg=ctx.c("ink_faint"), font=ctx.font("small"))
        self.gain_unit.pack(side="left", padx=(ctx.s(4), 0), pady=(ctx.s(6), 0))
        self.gain_hint = tk.Label(left, text="", bg=ctx.c("bg"), fg=ctx.c("ink_faint"),
                                  font=ctx.font("small"), anchor="w", justify="left",
                                  wraplength=ctx.s(360))
        self.gain_hint.pack(anchor="w", pady=(ctx.s(4), 0))

        # ---- 右：四个关键事实
        right = tk.Frame(wrap, bg=ctx.c("bg"))
        right.pack(side="right", anchor="e")
        self.facts = {}
        for i, key in enumerate(("hero.model", "hero.arch", "hero.driver", "hero.vbios")):
            cell = tk.Frame(right, bg=ctx.c("bg"))
            cell.grid(row=i // 2, column=i % 2, sticky="w",
                      padx=(ctx.s(24), 0), pady=(0, ctx.s(6)))
            tk.Label(cell, text=ctx.tr(key), bg=ctx.c("bg"), fg=ctx.c("ink_faint"),
                     font=ctx.font("small"), anchor="w").pack(anchor="w")
            lab = tk.Label(cell, text=ctx.tr("u.unknown"), bg=ctx.c("bg"),
                           fg=ctx.c("ink"), font=ctx.font("body", bold=True), anchor="w")
            lab.pack(anchor="w")
            self.facts[key] = lab

        # ---- 判定依据 + 手动选型号（原「解锁状态」卡片移除后，这两个入口搬到首屏）
        actions = tk.Frame(left, bg=ctx.c("bg"))
        actions.pack(anchor="w", pady=(ctx.s(14), 0))
        self.basis_btn = Button(actions, ctx, ctx.tr("hero.basis"), kind="quiet",
                                command=self.on_show_basis, height=30)
        self.basis_btn.pack(side="left")
        self.pick_btn = Button(actions, ctx, ctx.tr("unlock.pick_model"), kind="ghost",
                               command=lambda: self.on_pick_model() if self.on_pick_model else None,
                               height=30)
        self.pick_btn.pack(side="left", padx=(ctx.s(8), 0))
        self.pick_btn.set_enabled(bool(self.on_pick_model))

    def _render_metric(self, state, current, base, gain_pct, measured, unit):
        """首屏那一个数字到底该显示什么。

        规则（按优先级）：
          1. 还没实测 → 「当前算力 / 尚未实测」，不编数字；
          2. 有解锁前存档 + 当前实测 → 「较解锁前 ±x.x%」，正负都照实显示；
          3. 只有当前实测、没有解锁前 → 「当前算力 xx TFLOPS」；
          4. 什么都没有 → 「当前算力 / —」。
        """
        tr = self.ctx.tr
        ink_faint = self.ctx.c("ink_faint")

        if current is None:
            self.gain_caption.configure(text=tr("hero.metric.pending"))
            self.gain_label.configure(text="—", fg=ink_faint)
            self.gain_unit.configure(text="")
            self.gain_hint.configure(text=tr("hero.metric.hint") if state == "pending" else "")
            return

        if base is not None and gain_pct is not None:
            self.gain_caption.configure(text=tr("hero.metric.delta"))
            txt = "%+.1f%%" % gain_pct
            color = (self.ctx.c("ok") if gain_pct > 0.05
                     else self.ctx.c("bad") if gain_pct < -0.05
                     else ink_faint)
            self.gain_label.configure(text=txt, fg=color)
            self.gain_unit.configure(text="")
            self.gain_hint.configure(
                text="%s %.1f → %.1f %s" % (tr("hero.metric.delta"),
                                            base, current, unit))
            return

        self.gain_caption.configure(text=tr("hero.metric.cur"))
        self.gain_label.configure(text="%.1f" % current, fg=self.ctx.c("violet"))
        self.gain_unit.configure(text=unit)
        self.gain_hint.configure(text=tr("hero.metric.nobase"))

    def _on_resize(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        ctx = self.ctx
        self.canvas.delete("art")
        r = ctx.s(T.RADIUS["card"])
        w = event.width
        h = self.canvas.winfo_height()
        if w < 40 or h < 40:
            return
        x1, y1 = ctx.s(T.PAD["page"]), 0
        x2, y2 = w - ctx.s(T.PAD["page"]), h - ctx.s(4)
        # 渐变软底 + 边框
        rounded_rect(self.canvas, x1, y1, x2, y2, r,
                     fill=T.mix(ctx.c("card"), ctx.c("violet"), 0.05),
                     outline=ctx.c("line"), width=1, tags="art")
        self.canvas.tag_lower("art")

    def update_data(self, verdict, entry, gpu, gain_pct, tr, state=None, measured=False,
                    current=None, base=None, unit="TFLOPS"):
        """刷新首屏。

        gain_pct 只表示「较解锁前的变化百分比」，带正负；None 表示没有可比基准。
        current  是本次实测的当前算力（可为 None）。
        base     是「解锁前」那份存档的算力（可为 None）。
        """
        self.ctx.tr = tr
        if state is None:
            state = verdict.state if verdict else "unknown"
        fg, _bg = T.state_color(self.ctx.theme, state)
        self.badge.state = state
        w = self.badge.w
        self.badge.delete("all")
        rounded_rect(self.badge, 0, 0, w, self.badge.h, self.badge.h / 2, fill=_bg, outline="")
        self.badge.create_text(w / 2, self.badge.h / 2, text=tr("state." + state), fill=fg,
                               font=self.ctx.font("h1", bold=True))

        self._render_metric(state, current, base, gain_pct, measured, unit)

        self.facts["hero.model"].configure(text=(entry or {}).get("name")
                                           or (gpu or {}).get("name") or tr("u.unknown"))
        self.facts["hero.arch"].configure(text=(entry or {}).get("arch") or tr("u.unknown"))
        self.facts["hero.driver"].configure(text=(gpu or {}).get("driver_version")
                                            or tr("u.unknown"))
        self.facts["hero.vbios"].configure(text=(gpu or {}).get("vbios_version")
                                           or tr("u.unknown"))
        self.basis_btn.set_text(tr("hero.basis"))


# ===================================================================== 概览
class OverviewCard(Card):
    def __init__(self, master, ctx):
        self.metrics = {}
        super().__init__(master, ctx, "card.overview", "card.overview.desc")
        self.metrics = {}
        self._build()

    def _build(self):
        ctx = self.ctx
        grid = tk.Frame(self.body, bg=ctx.c("card"))
        grid.pack(fill="x")
        for i in range(3):
            grid.columnconfigure(i, weight=1)
        spec = [
            ("f.power_draw", "u.watt", "metric_md"),
            ("f.temp", "u.celsius", "metric_md"),
            ("f.fan", "u.percent", "metric_md"),
            ("f.util_gpu", "u.percent", "metric_md"),
            ("f.util_mem", "u.percent", "metric_md"),
            ("f.power_limit", "u.watt", "metric_md"),
        ]
        for i, (label_key, unit_key, size) in enumerate(spec):
            m = Metric(grid, ctx, ctx.tr(label_key), ctx.tr("u.unknown"), "", size=size)
            m.grid(row=i // 3, column=i % 3, sticky="w",
                   padx=(0, ctx.s(18)), pady=(0, ctx.s(12)))
            self.metrics[label_key] = m
        self._unit_keys = {k: u for k, u, _ in spec}

        tk.Frame(self.body, bg=ctx.c("line"), height=1).pack(fill="x", pady=(ctx.s(4), ctx.s(12)))

        self.specs = tk.Frame(self.body, bg=ctx.c("card"))
        self.specs.pack(fill="x")
        self.spec_rows = {}

        # 实时数据来源徽标（HWiNFO / NVML / nvidia-smi）
        self.source_label = tk.Label(self.body, text="", bg=ctx.c("card"),
                                     fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                     anchor="w")
        self.source_label.pack(fill="x", pady=(ctx.s(10), 0))

    def push_live(self, live, tr):
        """只刷新实时字段。不重建任何控件 —— 这是让数字「跟得上」的关键。"""
        if not live:
            return
        ctx = self.ctx
        unk = tr("u.unknown")
        pairs = (
            ("f.power_draw", live.get("power_w"), "u.watt", 1),
            ("f.temp", live.get("temp_c"), "u.celsius", 0),
            ("f.fan", live.get("fan_pct"), "u.percent", 0),
            ("f.util_gpu", live.get("util_gpu_pct"), "u.percent", 0),
            ("f.util_mem", live.get("util_mem_pct"), "u.percent", 0),
            ("f.power_limit", live.get("power_limit_w"), "u.watt", 0),
        )
        for key, val, unit_key, digits in pairs:
            m = self.metrics.get(key)
            if m is None or val is None:
                continue
            m.update_value(fmt_num(val, digits), tr(unit_key))
        # 扩展读数（HWiNFO 才有）
        extra = []
        if live.get("hotspot_c") is not None:
            extra.append("Hotspot %s" % fmt_num(live["hotspot_c"], 0, tr("u.celsius")))
        if live.get("mem_junction_c") is not None:
            extra.append("Mem.Junction %s" % fmt_num(live["mem_junction_c"], 0, tr("u.celsius")))
        if live.get("fan_rpm") is not None:
            extra.append("%s %s RPM" % (tr("f.fan"), fmt_num(live["fan_rpm"], 0)))
        src_key = "src." + (live.get("source") or "nvml")
        text = "%s: %s" % (tr("live.source"), tr(src_key))
        if extra:
            text += "   ·   " + "   ·   ".join(extra)
        self.source_label.configure(text=text)

    def update_data(self, gpu, entry, tr):
        ctx = self.ctx
        gpu = gpu or {}
        entry = entry or {}
        unk = tr("u.unknown")
        mapping = {
            "f.power_draw": gpu.get("power_draw_w"),
            "f.temp": gpu.get("temp_c"),
            "f.fan": gpu.get("fan_pct"),
            "f.util_gpu": gpu.get("util_gpu_pct"),
            "f.util_mem": gpu.get("util_mem_pct"),
            "f.power_limit": gpu.get("power_limit_w"),
        }
        for key, val in mapping.items():
            m = self.metrics[key]
            unit = tr(self._unit_keys[key])
            if val is None:
                m.update_value(unk, "")
            else:
                digits = 1 if key == "f.power_draw" else 0
                m.update_value(fmt_num(val, digits), unit)
            m.label.configure(text=tr(key))

        for w in self.specs.winfo_children():
            w.destroy()

        mem = entry.get("mem") or {}
        clocks = entry.get("clocks") or {}
        rows = [
            ("f.cuda", fmt_num(entry.get("cuda_cores") or gpu.get("num_cores"), 0,
                               tr("u.core"), unk)),
            ("f.sm", fmt_num(entry.get("sm_count"), 0, "", unk)),
            ("f.node", (str(entry.get("process_nm")) + " nm") if entry.get("process_nm") else unk),
            ("f.tdp", fmt_num(entry.get("tdp_w"), 0, tr("u.watt"), unk)),
            ("f.mem_size", fmt_bytes(gpu.get("mem_total_bytes"), unk)),
            ("f.mem_type", mem.get("type") or unk),
            ("f.mem_bus", fmt_num(mem.get("bus") or gpu.get("mem_bus_width"), 0,
                                  tr("u.bit"), unk)),
            ("f.mem_bw", fmt_num(mem.get("bandwidth"), 0, tr("u.gbps"), unk)),
            ("f.core_base", fmt_num(clocks.get("base"), 0, tr("u.mhz"), unk)),
            ("f.core_boost", fmt_num(clocks.get("boost"), 0, tr("u.mhz"), unk)),
            ("f.core_cur", fmt_num(gpu.get("clock_sm_mhz"), 0, tr("u.mhz"), unk)),
            ("f.pcie", ("Gen%d x%d" % (gpu.get("pcie_gen_cur"), gpu.get("pcie_width_cur")))
             if gpu.get("pcie_gen_cur") is not None else unk),
        ]
        left = tk.Frame(self.specs, bg=ctx.c("card"))
        left.pack(side="left", fill="x", expand=True)
        right = tk.Frame(self.specs, bg=ctx.c("card"))
        right.pack(side="left", fill="x", expand=True)
        for i, (key, val) in enumerate(rows):
            host = left if i % 2 == 0 else right
            Section(host, ctx, tr(key), str(val)).pack(fill="x",
                                                       pady=(0, ctx.s(6)),
                                                       padx=(0, ctx.s(16)))


# ===================================================================== 详细参数
class DetailCard(Card):
    def __init__(self, master, ctx):
        super().__init__(master, ctx, "card.detail", "card.detail.desc")
        self.container = tk.Frame(self.body, bg=ctx.c("card"))
        self.container.pack(fill="x")

    def update_data(self, gpu, entry, verdict, tr, sources_used):
        ctx = self.ctx
        for w in self.container.winfo_children():
            w.destroy()
        gpu = gpu or {}
        entry = entry or {}
        unk = tr("u.unknown")
        src = gpu.get("source") or "—"
        src_label = tr("src." + src) if ("src." + src) else src

        mem = entry.get("mem") or {}
        clocks = entry.get("clocks") or {}
        throttle = gpu.get("throttle_active") or []

        rows = [
            ("f.model", gpu.get("name") or entry.get("name") or unk, src_label),
            ("f.codename", entry.get("codename") or unk, tr("src.db")),
            ("f.arch", entry.get("arch") or unk, tr("src.db")),
            ("f.vbios", gpu.get("vbios_version") or unk, src_label),
            ("f.driver", gpu.get("driver_version") or unk, tr("src.wmi")),
            ("f.mem_size", fmt_bytes(gpu.get("mem_total_bytes"), unk), src_label),
            ("f.mem_clock", fmt_num(gpu.get("mem_clock_mhz"), 0, "MT/s", unk), src_label),
            ("f.pci_bus", gpu.get("pci_bus_id") or unk, src_label),
            ("f.serial", gpu.get("serial") or unk, src_label),
            ("f.uuid", gpu.get("uuid") or unk, src_label),
            ("f.throttle", ", ".join(throttle) if throttle else tr("u.none"), src_label),
        ]
        grid = tk.Frame(self.container, bg=ctx.c("card"))
        grid.pack(fill="x")
        for i, (key, val, source) in enumerate(rows):
            Section(grid, ctx, tr(key), str(val), source=source).grid(
                row=i, column=0, sticky="ew", pady=(0, ctx.s(7)))
        grid.columnconfigure(0, weight=1)

        if sources_used:
            tk.Label(self.container,
                     text=tr("f.source") + ": " + " → ".join(
                         tr("src." + s) for s in sources_used),
                     bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                     font=ctx.font("small"), anchor="w",
                     wraplength=ctx.s(520), justify="left").pack(
                fill="x", pady=(ctx.s(10), 0))


# ===================================================================== 基准测试
class BenchCard(Card):
    def __init__(self, master, ctx, on_start, on_stop, on_save_before, on_save_after,
                 on_export, on_compare):
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_save_before = on_save_before
        self.on_save_after = on_save_after
        self.on_export = on_export
        self.on_compare = on_compare
        super().__init__(master, ctx, "card.bench", "card.bench.desc")
        self._build()

    def _build(self):
        ctx = self.ctx
        ctl = tk.Frame(self.body, bg=ctx.c("card"))
        ctl.pack(fill="x")

        # 时长
        box = tk.Frame(ctl, bg=ctx.c("card"))
        box.pack(side="left", padx=(0, ctx.s(20)))
        tk.Label(box, text=ctx.tr("bench.duration"), bg=ctx.c("card"),
                 fg=ctx.c("ink_faint"), font=ctx.font("small")).pack(anchor="w")
        row = tk.Frame(box, bg=ctx.c("card"))
        row.pack(anchor="w", pady=(ctx.s(4), 0))
        self.duration = tk.Spinbox(row, from_=5, to=1800, increment=5, width=6,
                                   bg=ctx.c("chip_bg"), fg=ctx.c("ink"), bd=0,
                                   font=ctx.font("body"), buttonbackground=ctx.c("chip_bg"),
                                   highlightthickness=0, justify="center")
        self.duration.delete(0, "end")
        self.duration.insert(0, "30")
        self.duration.pack(side="left")
        tk.Label(row, text=ctx.tr("bench.duration.sec"), bg=ctx.c("card"),
                 fg=ctx.c("ink_faint"), font=ctx.font("small")).pack(side="left",
                                                                     padx=(ctx.s(6), 0))

        # 算法
        box2 = tk.Frame(ctl, bg=ctx.c("card"))
        box2.pack(side="left", padx=(0, ctx.s(20)))
        tk.Label(box2, text=ctx.tr("bench.algo"), bg=ctx.c("card"),
                 fg=ctx.c("ink_faint"), font=ctx.c("x") if False else ctx.font("small")).pack(anchor="w")
        self.algo = Segmented(box2, ctx,
                              [("proxy_v1", ctx.tr("algo.proxy_v1"))],
                              "proxy_v1")
        self.algo.pack(anchor="w", pady=(ctx.s(4), 0))

        # 强度
        box3 = tk.Frame(ctl, bg=ctx.c("card"))
        box3.pack(side="left", padx=(0, ctx.s(20)))
        tk.Label(box3, text=ctx.tr("bench.intensity"), bg=ctx.c("card"),
                 fg=ctx.c("ink_faint"), font=ctx.font("small")).pack(anchor="w")
        self.intensity = Segmented(box3, ctx,
                                   [("low", ctx.tr("bench.intensity.low")),
                                    ("mid", ctx.tr("bench.intensity.mid")),
                                    ("high", ctx.tr("bench.intensity.high"))],
                                   "mid")
        self.intensity.pack(anchor="w", pady=(ctx.s(4), 0))

        # 按钮
        btns = tk.Frame(ctl, bg=ctx.c("card"))
        btns.pack(side="right")
        self.start_btn = Button(btns, ctx, ctx.tr("bench.start"), kind="solid",
                                command=self.on_start, height=36)
        self.start_btn.pack(side="left", padx=(0, ctx.s(8)))
        self.stop_btn = Button(btns, ctx, ctx.tr("bench.stop"), kind="ghost",
                               command=self.on_stop, height=36)
        self.stop_btn.pack(side="left")
        self.stop_btn.set_enabled(False)

        # 状态条
        self.status = tk.Label(self.body, text=ctx.tr("bench.idle"), bg=ctx.c("card"),
                               fg=ctx.c("ink_faint"), font=ctx.font("small"), anchor="w")
        self.status.pack(fill="x", pady=(ctx.s(12), ctx.s(8)))

        self.statusline = StatusLine(self.body, ctx)
        self.statusline.pack(fill="x", pady=(ctx.s(2), ctx.s(6)))

        # 实时指标
        met = tk.Frame(self.body, bg=ctx.c("card"))
        met.pack(fill="x")
        self.m_avg = Metric(met, ctx, ctx.tr("bench.avg"), "—", "MSol/s", tone="violet")
        self.m_avg.pack(side="left", padx=(0, ctx.s(24)))
        self.m_peak = Metric(met, ctx, ctx.tr("bench.peak"), "—", "MSol/s", tone="ok")
        self.m_peak.pack(side="left", padx=(0, ctx.s(24)))
        self.m_stab = Metric(met, ctx, ctx.tr("bench.stability"), "—", "%")
        self.m_stab.pack(side="left", padx=(0, ctx.s(24)))
        self.m_power = Metric(met, ctx, ctx.tr("bench.power"), "—", "W")
        self.m_power.pack(side="left", padx=(0, ctx.s(24)))
        self.m_temp = Metric(met, ctx, ctx.tr("bench.temp"), "—", "°C")
        self.m_temp.pack(side="left")

        # 曲线
        self.spark = Sparkline(self.body, ctx, height=150)
        self.spark.pack(fill="x", pady=(ctx.s(14), ctx.s(10)))

        # 底部动作：分两行，避免窄窗口下被裁切
        act = tk.Frame(self.body, bg=ctx.c("card"))
        act.pack(fill="x")
        act_a = tk.Frame(act, bg=ctx.c("card"))
        act_a.pack(fill="x", pady=(0, ctx.s(6)))
        self.save_before = Button(act_a, ctx, ctx.tr("bench.save_before"), kind="ghost",
                                  command=self.on_save_before, height=30)
        self.save_before.pack(side="left", padx=(0, ctx.s(8)))
        self.save_after = Button(act_a, ctx, ctx.tr("bench.save_after"), kind="ghost",
                                 command=self.on_save_after, height=30)
        self.save_after.pack(side="left", padx=(0, ctx.s(8)))
        self.cmp_btn = Button(act_a, ctx, ctx.tr("bench.compare"), kind="quiet",
                              command=self.on_compare, height=30)
        self.cmp_btn.pack(side="left")

        act_b = tk.Frame(act, bg=ctx.c("card"))
        act_b.pack(fill="x")
        self.export_btn = Button(act_b, ctx, ctx.tr("exp.csv"), kind="quiet",
                                 command=lambda: self.on_export("csv"), height=30)
        self.export_btn.pack(side="left", padx=(0, ctx.s(8)))
        self.export_json = Button(act_b, ctx, ctx.tr("exp.json"), kind="quiet",
                                  command=lambda: self.on_export("json"), height=30)
        self.export_json.pack(side="left", padx=(0, ctx.s(8)))
        self.export_png = Button(act_b, ctx, ctx.tr("exp.png"), kind="quiet",
                                 command=lambda: self.on_export("png"), height=30)
        self.export_png.pack(side="left")

        self.save_hint = tk.Label(self.body, text=ctx.tr("bench.save_hint"),
                                  bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                                  font=ctx.font("small"), anchor="w", justify="left",
                                  wraplength=ctx.s(540))
        self.save_hint.pack(fill="x", pady=(ctx.s(8), 0))

        self.engine_label = tk.Label(self.body, text="", bg=ctx.c("card"),
                                     fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                     anchor="w", justify="left", wraplength=ctx.s(560))
        self.engine_label.pack(fill="x", pady=(ctx.s(8), 0))


    # ---- 统一反馈入口（带 spinner / 进度）
    def set_busy(self, text):
        try:
            self.statusline.set_busy(text)
        except Exception:
            pass

    def set_progress(self, frac, text=None):
        try:
            self.statusline.set_progress(frac, text)
        except Exception:
            pass

    def set_done(self, text):
        try:
            self.statusline.set_done(text)
        except Exception:
            pass

    def set_error(self, text):
        try:
            self.statusline.set_error(text)
        except Exception:
            pass
    def set_running(self, running):
        self.start_btn.set_enabled(not running)
        self.stop_btn.set_enabled(running)

    def set_engine_note(self, text):
        self.engine_label.configure(text=text)

    def push_sample(self, sample, result):
        self.spark.set_data([s["rate"] for s in result.samples])
        if result.avg() is not None:
            self.m_avg.update_value("%.1f" % result.avg(), "MSol/s")
        if result.peak() is not None:
            self.m_peak.update_value("%.1f" % result.peak(), "MSol/s")
        st = result.stability()
        if st is not None:
            self.m_stab.update_value("%.1f" % st, "%")
        if result.power_avg() is not None:
            self.m_power.update_value("%.0f" % result.power_avg(), "W")
        if result.temp_peak() is not None:
            self.m_temp.update_value("%.0f" % result.temp_peak(), "°C")

    def set_status(self, text, tone="ink_faint"):
        self.status.configure(text=text, fg=self.ctx.c(tone))


# ===================================================================== 历史记录
class HistoryCard(Card):
    def __init__(self, master, ctx, on_delete, on_clear):
        self.on_delete = on_delete
        self.on_clear = on_clear
        super().__init__(master, ctx, "card.history", "card.history.desc")
        self.container = tk.Frame(self.body, bg=ctx.c("card"))
        self.container.pack(fill="x")

    def update_data(self, records, tr):
        ctx = self.ctx
        for w in self.container.winfo_children():
            w.destroy()
        if not records:
            tk.Label(self.container, text=tr("hist.empty"), bg=ctx.c("card"),
                     fg=ctx.c("ink_faint"), font=ctx.font("small"), anchor="w",
                     justify="left", wraplength=ctx.s(520)).pack(fill="x")
            return

        header = tk.Frame(self.container, bg=ctx.c("card"))
        header.pack(fill="x", pady=(0, ctx.s(4)))
        for key, width in (("hist.time", 20), ("hist.kind", 10), ("hist.avg", 14),
                           ("hist.peak", 14), ("hist.delta", 0)):
            tk.Label(header, text=tr(key), bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                     font=ctx.font("small"), width=width, anchor="w").pack(side="left")

        for rec in reversed(records[-20:]):
            row = tk.Frame(self.container, bg=ctx.c("card"))
            row.pack(fill="x", pady=(0, ctx.s(3)))
            kind_key = "hist.before" if rec.get("kind") == "before" else "hist.after"
            color = "warn" if rec.get("kind") == "before" else "ok"
            vals = (
                (rec.get("iso", "—"), 20, "ink_soft"),
                (tr(kind_key), 10, color),
                (fmt_num(rec.get("avg"), 1, "MSol/s", "—"), 14, "ink"),
                (fmt_num(rec.get("peak"), 1, "MSol/s", "—"), 14, "ink"),
            )
            for text, width, c in vals:
                tk.Label(row, text=text, bg=ctx.c("card"), fg=ctx.c(c),
                         font=ctx.font("small"), width=width, anchor="w").pack(side="left")
            Button(row, self.ctx, tr("hist.delete"), kind="quiet", height=22,
                   command=lambda i=rec.get("iso"): self.on_delete(i)).pack(side="right")

        Button(self.container, self.ctx, tr("hist.clear"), kind="quiet", height=26,
               command=self.on_clear).pack(anchor="e", pady=(ctx.s(8), 0))

# ===================================================================== 跨重启对比
class CrossBootCard(Card):
    """解锁前后对比 —— 必须跨重启。

    解锁是 U 盘引导在 UEFI 预驱动阶段做的，运行中的 Windows 不能热解锁，
    所以「解锁前」的结果要落盘到 ~/.cache，重启后再来配对。
    """

    def __init__(self, master, ctx, on_clear, on_open_folder,
                 on_import=None, on_snap=None):
        self.on_clear = on_clear
        self.on_open_folder = on_open_folder
        self.on_import = on_import
        self.on_snap = on_snap
        super().__init__(master, ctx, "xboot.title", "xboot.desc")
        self._build()

    def _build(self):
        ctx = self.ctx
        # 三步流程条
        self.steps = tk.Frame(self.body, bg=ctx.c("card"))
        self.steps.pack(fill="x")
        self.step_labels = {}
        for i, key in enumerate(("xboot.step1", "xboot.step2", "xboot.step3")):
            cell = tk.Frame(self.steps, bg=ctx.c("card"))
            cell.pack(side="left", padx=(0, ctx.s(10)))
            dot = tk.Canvas(cell, bg=ctx.c("card"), width=ctx.s(22), height=ctx.s(22),
                            bd=0, highlightthickness=0)
            dot.pack(side="left")
            rounded_rect(dot, 0, 0, ctx.s(22), ctx.s(22), ctx.s(11),
                         fill=ctx.c("chip_bg"), outline="")
            num = dot.create_text(ctx.s(11), ctx.s(11), text=str(i + 1),
                                  fill=ctx.c("ink_faint"), font=ctx.font("small", bold=True))
            lab = tk.Label(cell, text=ctx.tr(key), bg=ctx.c("card"),
                           fg=ctx.c("ink_soft"), font=ctx.font("small"))
            lab.pack(side="left", padx=(ctx.s(6), 0))
            self.step_labels[key] = (dot, num, lab)
            if i < 2:
                tk.Label(self.steps, text="→", bg=ctx.c("card"),
                         fg=ctx.c("ink_faint"), font=ctx.font("small")).pack(
                    side="left", padx=(0, ctx.s(10)))

        # 状态 + 结果
        self.status = tk.Label(self.body, text="", bg=ctx.c("card"), fg=ctx.c("ink_soft"),
                               font=ctx.font("body"), anchor="w", justify="left",
                               wraplength=ctx.s(560))
        self.status.pack(fill="x", pady=(ctx.s(14), ctx.s(6)))

        # 结果区：算力实测与基准测试各占一组，两组同时显示，不互相顶替
        res = tk.Frame(self.body, bg=ctx.c("card"))
        res.pack(fill="x", pady=(ctx.s(4), 0))

        self.tf_row = tk.Frame(res, bg=ctx.c("card"))
        self.tf_row.pack(fill="x")
        tk.Label(self.tf_row, text=ctx.tr("xboot.group.compute"), bg=ctx.c("card"),
                 fg=ctx.c("ink_soft"), font=ctx.font("small", bold=True),
                 anchor="w").pack(anchor="w", pady=(0, ctx.s(2)))
        self.tf_vals = tk.Frame(self.tf_row, bg=ctx.c("card"))
        self.tf_vals.pack(anchor="w")
        self.tf_base = Metric(self.tf_vals, ctx, ctx.tr("xboot.step1"), "—", "TFLOPS",
                              tone="warn")
        self.tf_base.pack(side="left", padx=(0, ctx.s(26)))
        self.tf_after = Metric(self.tf_vals, ctx, ctx.tr("xboot.step3"), "—", "TFLOPS",
                               tone="ok")
        self.tf_after.pack(side="left", padx=(0, ctx.s(26)))
        self.tf_gain = Metric(self.tf_vals, ctx, ctx.tr("xboot.result"), "—", "",
                              tone="violet")
        self.tf_gain.pack(side="left")

        self.bench_row = tk.Frame(res, bg=ctx.c("card"))
        self.bench_row.pack(fill="x", pady=(ctx.s(12), 0))
        tk.Label(self.bench_row, text=ctx.tr("xboot.group.bench"), bg=ctx.c("card"),
                 fg=ctx.c("ink_soft"), font=ctx.font("small", bold=True),
                 anchor="w").pack(anchor="w", pady=(0, ctx.s(2)))
        self.bench_vals = tk.Frame(self.bench_row, bg=ctx.c("card"))
        self.bench_vals.pack(anchor="w")
        self.bn_base = Metric(self.bench_vals, ctx, ctx.tr("xboot.step1"), "—", "MSol/s",
                              tone="warn")
        self.bn_base.pack(side="left", padx=(0, ctx.s(26)))
        self.bn_after = Metric(self.bench_vals, ctx, ctx.tr("xboot.step3"), "—", "MSol/s",
                               tone="ok")
        self.bn_after.pack(side="left", padx=(0, ctx.s(26)))
        self.bn_gain = Metric(self.bench_vals, ctx, ctx.tr("xboot.result"), "—", "",
                              tone="violet")
        self.bn_gain.pack(side="left")

        # 按钮：存档的「存为」入口统一在「算力测试」卡里，这里只留取用与操作
        act = tk.Frame(self.body, bg=ctx.c("card"))
        act.pack(fill="x", pady=(ctx.s(14), 0))
        self.b_imp_before = Button(act, ctx, ctx.tr("xboot.import_before"),
                                   kind="ghost",
                                   command=lambda: self._imp("before"), height=32)
        self.b_imp_before.pack(side="left", padx=(0, ctx.s(8)))
        self.b_imp_after = Button(act, ctx, ctx.tr("xboot.import_after"),
                                  kind="ghost",
                                  command=lambda: self._imp("after"), height=32)
        self.b_imp_after.pack(side="left", padx=(0, ctx.s(8)))
        self.b_snap = Button(act, ctx, ctx.tr("xboot.snap"), kind="quiet",
                             command=lambda: self._snap(), height=32)
        self.b_snap.pack(side="left", padx=(0, ctx.s(8)))
        self.b_clear = Button(act, ctx, ctx.tr("xboot.clear"), kind="quiet",
                              command=self.on_clear, height=32)
        self.b_clear.pack(side="left")
        Button(act, ctx, ctx.tr("xboot.open_folder"), kind="quiet",
               command=self.on_open_folder, height=32).pack(side="right")

        self.path_label = tk.Label(self.body, text="", bg=ctx.c("card"),
                                   fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                   anchor="w", justify="left", wraplength=ctx.s(560))
        self.path_label.pack(fill="x", pady=(ctx.s(10), 0))
        self.note = tk.Label(self.body, text=ctx.tr("xboot.note"), bg=ctx.c("card"),
                             fg=ctx.c("ink_faint"), font=ctx.font("small"),
                             anchor="w", justify="left", wraplength=ctx.s(560))
        self.note.pack(fill="x", pady=(ctx.s(6), 0))

    def _imp(self, kind):
        if self.on_import:
            self.on_import(kind)

    def _snap(self):
        if self.on_snap:
            self.on_snap()

    def snap_rect(self):
        """本卡片在屏幕上的矩形（用于「保存截图」）。"""
        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty()
        return x, y, self.winfo_width(), self.winfo_height()

    def _fill_row(self, pair, _tag, m_before, m_after, m_gain,
                  k_before, k_after, k_pct, unit):
        """给一组指标填数值。缺哪一边就把整组打成「—」，不编造。"""
        b = (pair or {}).get(k_before)
        a = (pair or {}).get(k_after)
        if b is None or a is None:
            m_before.update_value("—", unit)
            m_after.update_value("—", unit)
            m_gain.update_value("—", "")
            return
        m_before.update_value("%.1f" % b, unit)
        m_after.update_value("%.1f" % a, unit)
        pct = (pair or {}).get(k_pct)
        if pct is None:
            m_gain.update_value("—", "")
        else:
            m_gain.update_value("%+.1f%%" % pct, "", "ok" if pct > 0 else "warn")

    def update_data(self, archive, tr, has_result=None, pair=None, paths=None):
        ctx = self.ctx
        base_s = archive.latest_bundle("before") if archive else None
        after_s = archive.latest_bundle("after") if archive else None
        if pair is None and archive is not None:
            pair = archive.bundle_pair()

        # 步骤圆点着色
        def mark(key, done, tone):
            dot, num, lab = self.step_labels[key]
            dot.delete("all")
            rounded_rect(dot, 0, 0, ctx.s(22), ctx.s(22), ctx.s(11),
                         fill=ctx.c(tone + "_soft") if done else ctx.c("chip_bg"), outline="")
            dot.create_text(ctx.s(11), ctx.s(11), text=num and "✓" if done else
                            str(("xboot.step1", "xboot.step2", "xboot.step3").index(key) + 1),
                            fill=ctx.c(tone) if done else ctx.c("ink_faint"),
                            font=ctx.font("small", bold=True))
            lab.configure(text=tr(key), fg=ctx.c("ink") if done else ctx.c("ink_soft"))

        mark("xboot.step1", bool(base_s), "ok")
        mark("xboot.step2", bool(base_s), "warn")
        mark("xboot.step3", bool(after_s), "ok")

        if pair:
            self.status.configure(text=tr("xboot.baseline_ready") + "  " +
                                  (tr("xboot.cross") if pair["cross_boot"] else tr("xboot.same")),
                                  fg=ctx.c("ok"))
        else:
            if base_s and not after_s:
                self.status.configure(text=tr("xboot.baseline_ready") + "  " +
                                      tr("xboot.pending"), fg=ctx.c("warn"))
            elif not base_s:
                self.status.configure(text=tr("xboot.no_baseline") + "  " +
                                      tr("xboot.reboot_hint"), fg=ctx.c("ink_soft"))
            else:
                self.status.configure(text=tr("xboot.have_after"), fg=ctx.c("ink_soft"))

        # 两组各算各的：任一组缺数据就只把这一组打成「—」，不影响另一组
        self._fill_row(pair, "tf", self.tf_base, self.tf_after, self.tf_gain,
                       "tflops_before", "tflops_after", "tflops_pct", "TFLOPS")
        self._fill_row(pair, "bench", self.bn_base, self.bn_after, self.bn_gain,
                       "bench_before", "bench_after", "bench_pct", "MSol/s")
        # 两个子文件夹各自的最新一份，直接标在按钮下，省得用户去翻目录
        def stamp(item):
            if not item:
                return tr("xboot.none_yet")
            return "%s   %s" % (item.get("iso") or "-",
                                tr("state." + (item.get("unlock_state") or "unknown")))
        self.path_label.configure(
            text="%s %s      %s %s\n%s: %s" % (
                tr("xboot.before_at"), stamp(base_s),
                tr("xboot.after_at"), stamp(after_s),
                tr("xboot.archive"), archive.root if archive else "-"))
        self.b_clear.set_text(tr("xboot.clear"))
        self.b_imp_before.set_text(tr("xboot.import_before"))
        self.b_imp_after.set_text(tr("xboot.import_after"))
        self.b_snap.set_text(tr("xboot.snap"))
        self.note.configure(text=tr("xboot.note"))
# ===================================================================== Tensor 算力实测
class TflopsCard(Card):
    """复刻参考图：测试项 / 锁定态区间 / 本次实测 / 判定。"""

    def __init__(self, master, ctx, on_run, on_install=None, on_pick=None):
        self.on_run = on_run
        self.on_install = on_install
        self.on_pick = on_pick
        super().__init__(master, ctx, "tf.title", "tf.desc")
        self._build()

    def set_scanning(self, tr):
        """扫描中状态。"""
        try:
            self.env_label.configure(text=tr("tf.env.scanning"), fg=self.ctx.c("ink_faint"))
            self.env_detail.configure(text="")
        except Exception:
            pass

    def set_env(self, usable, items, tr):
        """探测结束：显示找到了什么，或给出安装入口。"""
        ctx = self.ctx
        self._env_usable = usable
        # 环境就绪时顺手把引擎标签与警告刷新掉，避免与下面的状态行打架
        try:
            eng = "cublas" if usable else "opencl"
            self.engine_label.configure(
                text="%s: %s" % (tr("tf.engine"), tr("tf.engine." + eng)))
            self.note.configure(text="" if usable else
                                "  ".join([tr("tf.engine.warn.opencl"),
                                           tr("tf.engine.warn.opencl2")]))
        except Exception:
            pass
        if usable:
            self.env_label.configure(
                text="%s: %s (%s)" % (tr("tf.env.ok"), tr("tf.engine.cublas"),
                                      usable.get("version") or ""),
                fg=ctx.c("ok"))
            self.env_detail.configure(text=str(usable.get("exe") or ""), fg=ctx.c("ink_faint"))
            self.install_btn.set_enabled(False)
        else:
            self.env_label.configure(text=tr("tf.env.missing"), fg=ctx.c("warn"))
            # 列出扫过哪些解释器（最多 3 个），让用户知道真的找过
            tried = [os.path.basename((i.get("exe") or "")) or "?" for i in items[:3]]
            more = "" if len(items) <= 3 else " +%d" % (len(items) - 3)
            self.env_detail.configure(
                text="%s：%s%s" % (tr("tf.env.tried"), ", ".join(tried), more) if items
                else tr("tf.env.none_found"),
                fg=ctx.c("ink_faint"))
            self.install_btn.set_enabled(True)

    def _build(self):
        ctx = self.ctx
        head = tk.Frame(self.body, bg=ctx.c("card"))
        head.pack(fill="x")
        self.engine_label = tk.Label(head, text="", bg=ctx.c("card"),
                                     fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                     anchor="w", justify="left", wraplength=ctx.s(520))
        self.engine_label.pack(side="left")
        self.run_btn = Button(head, ctx, ctx.tr("tf.run"), kind="solid",
                              command=self.on_run, height=32)
        self.run_btn.pack(side="right")
        self.install_btn = Button(head, ctx, ctx.tr("dep.install"), kind="ghost",
                                  command=lambda: self.on_install() if self.on_install else None,
                                  height=32)
        self.install_btn.pack(side="right", padx=(0, ctx.s(8)))
        self.install_btn.set_enabled(False)
        self.pick_btn = Button(head, ctx, ctx.tr("dep.pick"), kind="quiet",
                               command=lambda: self.on_pick() if self.on_pick else None,
                               height=32)
        self.pick_btn.pack(side="right", padx=(0, ctx.s(8)))
        # 状态行放在「引擎说明」和「按钮」之间 —— 就是点按钮时视线所在的位置
        self.status = StatusLine(head, ctx)
        self.status.pack(side="left", fill="x", expand=True, padx=(ctx.s(12), ctx.s(12)))
        self.engine_label.pack(side="left")

        self.env_label = tk.Label(self.body, text=ctx.tr("tf.env.scanning"), bg=ctx.c("card"),
                                  fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                  anchor="w", justify="left", wraplength=ctx.s(560))
        self.env_label.pack(fill="x", pady=(ctx.s(8), 0))
        self.env_detail = tk.Label(self.body, text="", bg=ctx.c("card"),
                                   fg=ctx.c("ink_faint"), font=ctx.font("mono"),
                                   anchor="w", justify="left", wraplength=ctx.s(560))
        self.env_detail.pack(fill="x", pady=(ctx.s(2), 0))

        self.note = tk.Label(self.body, text="", bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                             font=ctx.font("small"), anchor="w", justify="left",
                             wraplength=ctx.s(560))
        self.note.pack(fill="x", pady=(ctx.s(8), 0))

        # ---- 运行反馈：状态行放在按钮同一行（用户视线就在右上角）
        self.status = StatusLine(head, ctx)
        self.status.pack(side="left", fill="x", expand=True, padx=(ctx.s(12), ctx.s(12)))

        self.live_label = tk.Label(self.body, text="", bg=ctx.c("card"),
                                   fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                   anchor="w", justify="left",
                                   wraplength=ctx.s(560))
        self.live_label.pack(fill="x", pady=(ctx.s(2), 0))

        self.trace = Sparkline(self.body, ctx, height=90)
        self.trace.pack(fill="x", pady=(ctx.s(8), 0))
        self._progress = 0.0
        self._elapsed = 0.0

        self.table = tk.Frame(self.body, bg=ctx.c("card"))
        self.table.pack(fill="x", pady=(ctx.s(12), 0))

        ratio = tk.Frame(self.body, bg=ctx.c("card"))
        ratio.pack(fill="x", pady=(ctx.s(14), 0))
        self.m_ratio = Metric(ratio, ctx, ctx.tr("tf.ratio"), "—", "", tone="violet")
        self.m_ratio.pack(side="left", padx=(0, ctx.s(28)))
        self.ratio_hint = tk.Label(ratio, text=ctx.tr("tf.ratio.hint"), bg=ctx.c("card"),
                                   fg=ctx.c("ink_faint"), font=ctx.font("small"),
                                   anchor="w", justify="left", wraplength=ctx.s(400))
        self.ratio_hint.pack(side="left", fill="x", expand=True, pady=(ctx.s(8), 0))

    # ---- 运行中反馈（与基准测试卡片同等待遇）
    def set_running(self, on, tr):
        self.run_btn.set_text(tr("tf.running") if on else tr("tf.run"))
        self.run_btn.set_enabled(not on)
        try:
            self.run_btn.configure(cursor="watch" if on else "hand2")
        except Exception:
            pass
        if on:
            self._progress = 0.0
            self._elapsed = 0.0
            self.status.set_busy(tr("tf.running"))
            self.live_label.configure(text=tr("tf.live.waiting"), fg=self.ctx.c("ink_faint"))
            self.trace.set_data([])
        # 结束时不要清空状态行：那一行是「跑完了、结论是什么」的唯一证据，
        # 清掉就等于什么都没发生（用户正是这么感觉的）。
        else:
            pass

    def tick_elapsed(self, elapsed, done, total, tr):
        p = min(0.99, done / float(max(1, total)))
        self._progress = p
        try:
            self.status.set_progress(p, tr("tf.running"))
        except Exception:
            pass

    def push_case(self, idx, total, key, val, elapsed, curve, tr):
        """每完成一项就刷新：进度、耗时、最新值、曲线。"""
        p = min(0.99, idx / float(max(1, total)))
        self._progress = p
        try:
            self.status.set_progress(p, tr("tf.running"))
            if val is not None:
                name = tr("case." + key)
                if name.startswith("\u27e6"):
                    name = key
                self.live_label.configure(text="%s  \u2192  %.1f TFLOPS" % (name, val),
                                          fg=self.ctx.c("ink"))
            self.trace.set_data([c["tflops"] for c in curve])
        except Exception:
            pass

    def update_data(self, analysis, summary, entry, db, tr, running=False):
        ctx = self.ctx
        self.run_btn.set_text(tr("tf.running") if running else tr("tf.run"))
        self.run_btn.set_enabled(not running)

        # 引擎显示以「实际环境」为准，不要用 analysis 缺失时的 "opencl" 兜底去吓人：
        # 环境扫描已经找到 torch 时，绝不能还挂着「未找到 torch」的警告。
        env_ready = bool(getattr(self, "_env_usable", None))
        engine = (analysis or {}).get("engine")
        if not engine:
            engine = "cublas" if env_ready else "opencl"
        self.engine_label.configure(text="%s: %s" % (tr("tf.engine"), tr("tf.engine." + engine)))

        notes = []
        if engine == "opencl" and not env_ready:
            notes.extend([tr("tf.engine.warn.opencl"), tr("tf.engine.warn.opencl2")])
        self.note.configure(text="  ".join(notes))

        for widget in self.table.winfo_children():
            widget.destroy()

        header = tk.Frame(self.table, bg=ctx.c("card"))
        header.pack(fill="x", pady=(0, ctx.s(6)))
        for key, width in (("tf.case", 26), ("tf.locked_band", 15),
                           ("tf.measured", 13), ("tf.verdict", 12)):
            tk.Label(header, text=tr(key), bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                     font=ctx.font("small"), width=width, anchor="w").pack(side="left")

        if not summary:
            tk.Label(self.table, text=tr("tf.need_run"), bg=ctx.c("card"),
                     fg=ctx.c("ink_faint"), font=ctx.font("body")).pack(anchor="w",
                                                                        pady=ctx.s(6))
            self.m_ratio.update_value("—", "")
            return

        ref = ((db.data.get("tflops_reference") or {}).get("by_model") or {})
        model_ref = ref.get((entry or {}).get("id")) if entry else None
        verdict = (analysis or {}).get("verdict")

        for row in summary:
            key = row["key"]
            tflops = row.get("tflops")
            band = model_ref.get(key) if model_ref else None
            band_txt = "—"
            if isinstance(band, dict):
                look = band.get("locked")
                if look and len(look) == 2:
                    band_txt = ("%g-%g" % (look[0], look[1])) if look[0] != look[1] else ("%g" % look[0])

            if tflops is None:
                state, state_txt = "unknown", tr("tf.state.unknown")
            elif row["control"]:
                state, state_txt = "na", tr("tf.state.normal")
            elif row["sensitive"]:
                if verdict == "unlocked":
                    state, state_txt = "unlocked", tr("tf.state.unlocked")
                elif verdict == "locked":
                    state, state_txt = "locked", tr("tf.state.locked")
                else:
                    state, state_txt = "unknown", tr("tf.state.unknown")
            else:
                state, state_txt = "na", tr("tf.state.normal")

            fg, _bg = T.state_color(ctx.theme, state)
            row_bg = ctx.c("accent_soft") if row["control"] else ctx.c("card")
            line = tk.Frame(self.table, bg=row_bg)
            line.pack(fill="x", pady=(0, ctx.s(3)))
            tk.Label(line, text=tr(row["label_key"]), bg=row_bg, fg=ctx.c("ink"),
                     font=ctx.font("small"), width=26, anchor="w").pack(side="left")
            tk.Label(line, text=band_txt, bg=row_bg, fg=ctx.c("ink_faint"),
                     font=ctx.font("mono"), width=15, anchor="w").pack(side="left")
            tk.Label(line, text=("%.1f" % tflops) if tflops is not None else "—",
                     bg=row_bg, fg=ctx.c("ink"), font=ctx.font("small", bold=True),
                     width=13, anchor="w").pack(side="left")
            mark = "\u2713 " if state in ("unlocked", "na") else "\u2717 "
            tk.Label(line, text=mark + state_txt, bg=row_bg, fg=fg,
                     font=ctx.font("small", bold=True), width=12,
                     anchor="w").pack(side="left")
            if row["control"]:
                tk.Label(line, text=tr("tf.control"), bg=row_bg, fg=ctx.c("ink_faint"),
                         font=ctx.font("small")).pack(side="left", padx=(ctx.s(6), 0))

        ratio = (analysis or {}).get("ratio")
        if ratio is None:
            self.m_ratio.update_value("—", "")
        else:
            self.m_ratio.update_value("%.1f%%" % (ratio * 100.0), "",
                                      "ok" if ratio >= 0.85 else
                                      ("warn" if ratio <= 0.65 else "ink_soft"))
        note_key = (analysis or {}).get("note_key")
        if note_key:
            self.ratio_hint.configure(
                text=tr("tf.ratio.hint") + "  " + tr(note_key),
                fg=ctx.c("warn") if (ratio or 1) <= 0.65 else ctx.c("ink_faint"))

        # ---- 完成提示：让用户明确知道这次跑完了、结论是什么
        verdict = (analysis or {}).get("verdict")
        err = (analysis or {}).get("error")
        if running:
            return
        if not summary:
            self.status.clear()
        elif err:
            self.status.set_error("%s: %s" % (tr("tf.done.failed"), str(err)[:120]))
        elif verdict == "unlocked":
            self.status.set_done(tr("tf.done.unlocked").format(
                ratio="%.1f" % ((ratio or 0) * 100)))
        elif verdict == "locked":
            self.status.set_done(tr("tf.done.locked").format(
                ratio="%.1f" % ((ratio or 0) * 100)))
        else:
            self.status.set_done(tr("tf.done.unknown"))


# ===================================================================== 引导 U 盘
class UsbCard(Card):
    """自动识别解锁引导盘 + 读 loader.log。"""

    def __init__(self, master, ctx, on_rescan, on_make=None):
        self.on_rescan = on_rescan
        self.on_make = on_make
        super().__init__(master, ctx, "usb.title", "usb.desc")
        self._build()

    def set_make_handler(self, cb):
        """原「解锁状态」卡片底部的「制作 U 盘」入口搬到这里。"""
        self.on_make = cb
        try:
            self.make_btn.set_enabled(True)
        except Exception:
            pass

    def _build(self):
        ctx = self.ctx
        head = tk.Frame(self.body, bg=ctx.c("card"))
        head.pack(fill="x")
        self.badge = Badge(head, ctx, "unknown")
        self.badge.pack(side="left")
        self.rescan_btn = Button(head, ctx, ctx.tr("usb.rescan"), kind="ghost",
                                 command=self.on_rescan, height=28)
        self.rescan_btn.pack(side="right")
        self.make_btn = Button(head, ctx, ctx.tr("make.open"), kind="ghost",
                               command=lambda: self.on_make() if self.on_make else None,
                               height=28)
        self.make_btn.pack(side="right", padx=(0, ctx.s(8)))
        self.make_btn.set_enabled(bool(self.on_make))
        self.detail = tk.Frame(self.body, bg=ctx.c("card"))
        self.detail.pack(fill="x", pady=(ctx.s(10), 0))

    def _set_badge(self, state, text):
        ctx = self.ctx
        fg, bg = T.state_color(ctx.theme, state)
        self.badge.delete("all")
        rounded_rect(self.badge, 0, 0, self.badge.w, self.badge.h,
                     self.badge.h / 2, fill=bg, outline="")
        self.badge.create_text(self.badge.w / 2, self.badge.h / 2, text=text,
                               fill=fg, font=ctx.font("h3", bold=True))

    def update_data(self, usb_info, log_info, tr):
        ctx = self.ctx
        for widget in self.detail.winfo_children():
            widget.destroy()
        self.rescan_btn.set_text(tr("usb.rescan"))

        if not usb_info:
            self._set_badge("unknown", tr("tf.state.unknown"))
            tk.Label(self.detail, text=tr("usb.none"), bg=ctx.c("card"),
                     fg=ctx.c("ink_soft"), font=ctx.font("body"), anchor="w").pack(fill="x")
            tk.Label(self.detail, text=tr("usb.none.hint"), bg=ctx.c("card"),
                     fg=ctx.c("ink_faint"), font=ctx.font("small"), anchor="w",
                     justify="left", wraplength=ctx.s(560)).pack(fill="x", pady=(ctx.s(4), 0))
            return

        state = (log_info or {}).get("verdict") or "unknown"
        txt = {"unlocked": tr("state.unlocked"), "locked": tr("state.locked")}.get(
            state, tr("tf.state.unknown"))
        self._set_badge("unlocked" if state == "unlocked" else
                        ("locked" if state == "locked" else "unknown"), txt)

        rows = [
            (tr("usb.drive"), "%s:\\  %s" % (usb_info.get("letter", "?"),
                                              usb_info.get("label") or "")),
            (tr("usb.fs"), usb_info.get("fs") or "—"),
            (tr("usb.families"), ", ".join(usb_info.get("families") or []) or "—"),
            (tr("usb.assets"), str(len(usb_info.get("assets") or []))),
            (tr("usb.efi"), "OK" if usb_info.get("efi") else "—"),
            (tr("usb.tx"), "TX" if usb_info.get("tx_build") else "—"),
        ]
        grid = tk.Frame(self.detail, bg=ctx.c("card"))
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        for i, (k, v) in enumerate(rows):
            Section(grid, ctx, k, str(v)).grid(row=i, column=0, sticky="ew",
                                               pady=(0, ctx.s(5)))

        if not log_info:
            tk.Label(self.detail, text=tr("usb.log.missing"), bg=ctx.c("card"),
                     fg=ctx.c("warn"), font=ctx.font("small"), anchor="w").pack(
                fill="x", pady=(ctx.s(8), 0))
            return

        tk.Frame(self.detail, bg=ctx.c("line"), height=1).pack(fill="x",
                                                              pady=(ctx.s(8), ctx.s(8)))

        def hx(v, width=8):
            return ("0x%0*X" % (width, v)) if v is not None else "—"

        vrows = [
            (tr("usb.verdict"), txt),
            (tr("usb.source"), tr("usb.source." + ((log_info or {}).get("verdict_source") or "none"))),
            (tr("usb.gpu"), log_info.get("gpu_dev") or "—"),
            ("PLM flip", "%s \u2192 %s" % (hx(log_info.get("plm_from")), hx(log_info.get("plm_to")))),
            ("SS0 / SS1", "%s / %s" % (hx(log_info.get("ss0")), hx(log_info.get("ss1"), 1))),
            ("FEAT_READOUT_1", hx(log_info.get("readout1"))),
            (tr("usb.log"), "%s (%d B)" % (os.path.basename(log_info.get("path") or ""),
                                           usb_info.get("log_size") or 0)),
        ]
        g2 = tk.Frame(self.detail, bg=ctx.c("card"))
        g2.pack(fill="x")
        g2.columnconfigure(0, weight=1)
        for i, (k, v) in enumerate(vrows):
            Section(g2, ctx, k, str(v), strong=(i == 0)).grid(row=i, column=0,
                                                              sticky="ew",
                                                              pady=(0, ctx.s(5)))