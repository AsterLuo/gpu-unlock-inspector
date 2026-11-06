# -*- coding: utf-8 -*-
r"""弹窗：确认框、信息框、进度框。全部内容居中，跟随主题。

设计要点
--------
- **内容居中**：标题、正文、按钮都水平居中，视觉重心稳。
- **进度条分情况**：
    * 能拿到真实进度 → 显示百分比进度条（determinate）
    * 拿不到 → 显示不确定进度（来回扫动的滑块）
- 保留系统标题栏：overrideredirect 的无边框窗口在部分 Windows 环境下
  不进任务栏也不置顶，会被主窗口挡住，用户会以为「点了没反应」。
"""
from __future__ import annotations

import tkinter as tk

from core import theme as T
from ui.base import Button, rounded_rect


class BaseDialog(tk.Toplevel):
    """所有弹窗的底座：居中、置顶、可拖动、跟随主题。"""

    def __init__(self, master, ctx, title, width=460):
        super().__init__(master)
        self.ctx = ctx
        self.configure(bg=ctx.c("card"))
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self._dw = ctx.s(width)  # 注意：_w 是 tkinter 内部路径名，不能占用
        self.frame = tk.Frame(self, bg=ctx.c("card"))
        self.frame.pack(fill="both", expand=True)
        self._drag = None
        for w in (self, self.frame):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self.bind("<Escape>", lambda e: self.done(None))

    # ---------------------------------------------------------- 布局工具
    def add_title(self, text, sub=None):
        ctx = self.ctx
        pad = ctx.s(24)
        tk.Label(self.frame, text=text, bg=ctx.c("card"), fg=ctx.c("ink"),
                 font=ctx.font("h1", bold=True), anchor="center",
                 justify="center").pack(fill="x", padx=pad, pady=(pad, ctx.s(6)))
        if sub:
            tk.Label(self.frame, text=sub, bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                     font=ctx.font("small"), anchor="center").pack(
                fill="x", padx=pad, pady=(0, ctx.s(10)))

    def add_body(self, text):
        ctx = self.ctx
        pad = ctx.s(24)
        tk.Label(self.frame, text=text, bg=ctx.c("card"), fg=ctx.c("ink_soft"),
                 font=ctx.font("body"), anchor="center", justify="center",
                 wraplength=self._dw - pad * 2).pack(fill="x", padx=pad,
                                                    pady=(0, ctx.s(16)))

    def add_buttons(self, specs):
        """specs: [(label, value, kind)]，从右到左排，整体居中。"""
        ctx = self.ctx
        pad = ctx.s(24)
        row = tk.Frame(self.frame, bg=ctx.c("card"))
        row.pack(fill="x", padx=pad, pady=(0, pad))
        inner = tk.Frame(row, bg=ctx.c("card"))
        inner.pack(anchor="center")
        for label, value, kind in reversed(specs):
            b = Button(inner, ctx, label, kind=kind,
                       command=lambda v=value: self.done(v))
            b.pack(side="right", padx=(ctx.s(8), 0))

    # ---------------------------------------------------------- 位置
    def center_on(self, master):
        self.update_idletasks()
        w = max(self.winfo_width(), self.winfo_reqwidth())
        h = max(self.winfo_height(), self.winfo_reqheight())
        try:
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = master.winfo_width(), master.winfo_height()
        except tk.TclError:
            mx = my = 0
            mw, mh = 800, 600
        x = mx + (mw - w) // 2
        y = my + (mh - h) // 3
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = max(40, min(x, sw - w - 40))
            y = max(40, min(y, sh - h - 80))
        except tk.TclError:
            pass
        self.geometry("+%d+%d" % (x, y))

    def show(self, master):
        """居中 + 置顶 + 抢焦点，确保用户一定看得见。"""
        self.center_on(master)
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(220, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except tk.TclError:
            pass

    def done(self, value):
        self.result = value
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        if not self._drag:
            return
        self.geometry("+%d+%d" % (e.x_root - self._drag[0], e.y_root - self._drag[1]))


class Modal(BaseDialog):
    """确认 / 提示框。"""

    def __init__(self, master, ctx, title, body, buttons, width=460):
        super().__init__(master, ctx, title, width)
        self.add_title(title)
        self.add_body(body)
        self.add_buttons(buttons)
        self.bind("<Return>", lambda e: self.done(buttons[0][1] if buttons else None))
        self.show(master)

    @classmethod
    def ask(cls, master, ctx, title, body, ok_label, cancel_label, width=460):
        m = cls(master, ctx, title, body,
                [(ok_label, True, "solid"), (cancel_label, False, "ghost")],
                width=width)
        try:
            m.wait_window()
        except tk.TclError:
            pass
        return bool(m.result)

    @classmethod
    def tell(cls, master, ctx, title, body, ok_label, width=460):
        m = cls(master, ctx, title, body, [(ok_label, True, "solid")], width=width)
        return m


class ConfirmType(BaseDialog):
    """二次确认：要求手打盘符字母。"""

    def __init__(self, master, ctx, title, body, hint, expect, ok_label, cancel_label):
        super().__init__(master, ctx, title, width=460)
        self.expect = (expect or "").strip().upper()
        self.add_title(title)
        self.add_body(body)

        ctx = self.ctx
        pad = ctx.s(24)
        row = tk.Frame(self.frame, bg=ctx.c("card"))
        row.pack(fill="x", padx=pad, pady=(0, ctx.s(16)))
        inner = tk.Frame(row, bg=ctx.c("card"))
        inner.pack(anchor="center")
        tk.Label(inner, text=hint, bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                 font=ctx.font("small")).pack(side="left")
        self.entry = tk.Entry(inner, width=4, bg=ctx.c("chip_bg"), fg=ctx.c("ink"),
                              bd=0, font=ctx.font("h2", bold=True),
                              justify="center", insertbackground=ctx.c("ink"))
        self.entry.pack(side="left", padx=(ctx.s(8), 0), ipady=ctx.s(4))

        self.add_buttons([(ok_label, True, "solid"), (cancel_label, False, "ghost")])
        self.entry.focus_set()
        self.bind("<Return>", lambda e: self._ok())
        self.show(master)

    def _ok(self):
        if (self.entry.get() or "").strip().upper() == self.expect:
            self.done(True)
        else:
            self.entry.configure(bg=self.ctx.c("bad_soft"))
            self.after(600, lambda: self.entry.configure(bg=self.ctx.c("chip_bg")))


class ProgressDialog(BaseDialog):
    """进度弹窗。

    进度条分两种情况：
      - determinate（知道总量）：实心进度条 + 百分比
      - indeterminate（不知道总量）：来回扫动的滑块
    完成后若 ok=True，延迟 0.9 秒自动关闭。
    """

    def __init__(self, master, ctx, title, initial="", cancel_label="Cancel",
                 on_cancel=None, width=480):
        super().__init__(master, ctx, title, width)
        ctx = self.ctx
        pad = ctx.s(24)
        self.on_cancel = on_cancel
        self.closed = False
        self.cancelled = False
        self._mode = "indeterminate"
        self._frac = 0.0
        self._sweep = 0.0
        self._job = None

        self.add_title(title)

        self.stage = tk.Label(self.frame, text=initial, bg=ctx.c("card"),
                              fg=ctx.c("ink_soft"), font=ctx.font("body"),
                              anchor="center", justify="center",
                              wraplength=self._dw - pad * 2)
        self.stage.pack(fill="x", padx=pad, pady=(0, ctx.s(12)))

        self.bar = tk.Canvas(self.frame, bg=ctx.c("chip_bg"), bd=0,
                             highlightthickness=0, height=ctx.s(10),
                             width=self._dw - pad * 2)
        self.bar.pack(fill="x", padx=pad)

        self.pct = tk.Label(self.frame, text="", bg=ctx.c("card"),
                            fg=ctx.c("violet"), font=ctx.font("small", bold=True),
                            anchor="center")
        self.pct.pack(fill="x", padx=pad, pady=(ctx.s(6), 0))

        self.detail = tk.Label(self.frame, text="", bg=ctx.c("card"),
                               fg=ctx.c("ink_faint"), font=ctx.font("mono"),
                               anchor="center", justify="center",
                               wraplength=self._dw - pad * 2)
        self.detail.pack(fill="x", padx=pad, pady=(ctx.s(4), ctx.s(12)))

        self.cancel_btn = None
        if cancel_label:
            row = tk.Frame(self.frame, bg=ctx.c("card"))
            row.pack(fill="x", padx=pad, pady=(0, pad))
            inner = tk.Frame(row, bg=ctx.c("card"))
            inner.pack(anchor="center")
            self.cancel_btn = Button(inner, ctx, cancel_label, kind="ghost",
                                     command=self._cancel)
            self.cancel_btn.pack()

        self.show(master)
        self._tick()

    # ---------------------------------------------------------- 进度
    def set_progress(self, frac=None, stage=None, detail=None):
        if self.closed:
            return
        if frac is None:
            self._mode = "indeterminate"
        else:
            self._mode = "determinate"
            self._frac = max(0.0, min(1.0, frac))
        try:
            if stage is not None:
                self.stage.configure(text=stage)
            if detail is not None:
                self.detail.configure(text=detail[:180])
            self.pct.configure(text=("%d%%" % round(self._frac * 100))
                               if self._mode == "determinate" else "")
        except tk.TclError:
            pass

    def _tick(self):
        if self.closed:
            return
        try:
            self._paint()
            self._job = self.after(50, self._tick)
        except tk.TclError:
            pass

    def _paint(self):
        ctx = self.ctx
        w = max(10, self.bar.winfo_width())
        h = self.bar.winfo_height() or ctx.s(10)
        self.bar.delete("all")
        rounded_rect(self.bar, 0, 0, w, h, h / 2, fill=ctx.c("chip_bg"), outline="")
        if self._mode == "determinate":
            if self._frac <= 0:
                return
            fill_w = max(h, w * self._frac)
            rounded_rect(self.bar, 0, 0, fill_w, h, h / 2,
                         fill=ctx.c("violet"), outline="")
        else:
            # 不确定进度：一个来回扫动的滑块
            self._sweep = (self._sweep + 0.035) % 1.0
            seg = w * 0.28
            pos = self._sweep * (w + seg) - seg
            x1 = max(0, pos)
            x2 = min(w, pos + seg)
            if x2 - x1 > h:
                rounded_rect(self.bar, x1, 0, x2, h, h / 2,
                             fill=ctx.c("violet_lt"), outline="")

    # ---------------------------------------------------------- 结束
    def _cancel(self):
        self.cancelled = True
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass
        try:
            self.stage.configure(text="Cancelling...")
            if self.cancel_btn is not None:
                self.cancel_btn.set_enabled(False)
        except tk.TclError:
            pass

    def finish(self, ok=True, message=""):
        if self.closed:
            return
        try:
            if ok:
                self._mode = "determinate"
                self._frac = 1.0
                self.pct.configure(text="100%")
            self.stage.configure(text=message or self.stage.cget("text"),
                                 fg=self.ctx.c("ok" if ok else "bad"))
            if self.cancel_btn is not None:
                self.cancel_btn.set_text(self.ctx.tr("msg.close"))
                self.cancel_btn.command = self.close
                self.cancel_btn.set_enabled(True)
        except tk.TclError:
            pass
        if ok and not self.cancelled:
            self.after(900, self.close)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass

class OnboardDialog(BaseDialog):
    """首次启动引导：检测环境 → 安装（同窗进度）→ 提示开始验证。

    三态切换，全部在同一个窗口里完成，不叠弹窗：
      state=missing : 说明缺什么 + [安装] [稍后]
      state=install : 进度条（有真实进度用百分比，没有用扫动滑块）+ [取消]
      state=ready   : 安装完成 + [开始验证] [稍后]
    点「开始验证」返回 "verify"，由调用方接着跑算力实测。
    """

    def __init__(self, master, ctx, title, body, cancel_label="Cancel"):
        super().__init__(master, ctx, title, width=500)
        self.cancel_label = cancel_label
        self.progress = None
        self._built = None
        self.build_missing(title, body)

    # ---------------------------------------------------------- 清空
    def _clear(self):
        for w in self.frame.winfo_children():
            w.destroy()

    # ---------------------------------------------------------- 三态
    def build_missing(self, title, body):
        self._built = "missing"
        self._clear()
        self.add_title(title)
        self.add_body(body)
        ctx = self.ctx
        pad = ctx.s(24)
        row = tk.Frame(self.frame, bg=ctx.c("card"))
        row.pack(fill="x", padx=pad, pady=(0, pad))
        inner = tk.Frame(row, bg=ctx.c("card"))
        inner.pack(anchor="center")
        Button(inner, ctx, self.ctx.tr("onb.install"), kind="solid",
               command=lambda: self.done("install")).pack(side="left",
                                                          padx=(0, ctx.s(8)))
        Button(inner, ctx, self.cancel_label, kind="ghost",
               command=lambda: self.done(None)).pack(side="left")
        self.show(self.master)

    def build_install(self, title):
        """安装态：进度条 + 取消。"""
        self._built = "install"
        self._clear()
        ctx = self.ctx
        pad = ctx.s(24)
        self.add_title(title)

        self.stage = tk.Label(self.frame, text="", bg=ctx.c("card"),
                              fg=ctx.c("ink_soft"), font=ctx.font("body"),
                              anchor="center", justify="center",
                              wraplength=self._dw - pad * 2)
        self.stage.pack(fill="x", padx=pad, pady=(0, ctx.s(12)))

        self.bar = tk.Canvas(self.frame, bg=ctx.c("chip_bg"), bd=0,
                             highlightthickness=0, height=ctx.s(10),
                             width=self._dw - pad * 2)
        self.bar.pack(fill="x", padx=pad)

        self.pct = tk.Label(self.frame, text="", bg=ctx.c("card"),
                            fg=ctx.c("violet"), font=ctx.font("small", bold=True),
                            anchor="center")
        self.pct.pack(fill="x", padx=pad, pady=(ctx.s(6), 0))

        self.detail = tk.Label(self.frame, text="", bg=ctx.c("card"),
                               fg=ctx.c("ink_faint"), font=ctx.font("mono"),
                               anchor="center", justify="center",
                               wraplength=self._dw - pad * 2)
        self.detail.pack(fill="x", padx=pad, pady=(ctx.s(4), ctx.s(12)))

        row = tk.Frame(self.frame, bg=ctx.c("card"))
        row.pack(fill="x", padx=pad, pady=(0, pad))
        inner = tk.Frame(row, bg=ctx.c("card"))
        inner.pack(anchor="center")
        self.cancel_btn = Button(inner, ctx, self.cancel_label, kind="ghost",
                                 command=lambda: self.done("cancel"))
        self.cancel_btn.pack()

        self._frac = None
        self._sweep = 0.0
        self._animate()
        self.show(self.master)

    def build_ready(self, title, body):
        """完成态：提示可以开始验证。"""
        self._built = "ready"
        self._clear()
        ctx = self.ctx
        pad = ctx.s(24)
        self.add_title(title)
        self.add_body(body)
        row = tk.Frame(self.frame, bg=ctx.c("card"))
        row.pack(fill="x", padx=pad, pady=(0, pad))
        inner = tk.Frame(row, bg=ctx.c("card"))
        inner.pack(anchor="center")
        Button(inner, ctx, self.ctx.tr("onb.verify"), kind="solid",
               command=lambda: self.done("verify")).pack(side="left",
                                                         padx=(0, ctx.s(8)))
        Button(inner, ctx, self.ctx.tr("onb.later"), kind="ghost",
               command=lambda: self.done(None)).pack(side="left")
        self.center_on(self.master)
        self.show(self.master)

    # ---------------------------------------------------------- 进度
    def set_progress(self, frac=None, stage=None, detail=None):
        if self._built != "install":
            return
        self._frac = None if frac is None else max(0.0, min(1.0, frac))
        try:
            if stage is not None:
                self.stage.configure(text=stage)
            if detail is not None:
                self.detail.configure(text=str(detail)[:180])
            self.pct.configure(text=("" if self._frac is None
                                     else "%d%%" % round(self._frac * 100)))
        except tk.TclError:
            pass

    def _animate(self):
        if self._built != "install":
            return
        try:
            ctx = self.ctx
            w = max(10, self.bar.winfo_width())
            h = self.bar.winfo_height() or ctx.s(10)
            self.bar.delete("all")
            rounded_rect(self.bar, 0, 0, w, h, h / 2, fill=ctx.c("chip_bg"),
                         outline="")
            if self._frac is not None:
                if self._frac > 0:
                    rounded_rect(self.bar, 0, 0, max(h, w * self._frac), h,
                                 h / 2, fill=ctx.c("violet"), outline="")
            else:
                self._sweep = (self._sweep + 0.035) % 1.0
                seg = w * 0.28
                pos = self._sweep * (w + seg) - seg
                x1, x2 = max(0, pos), min(w, pos + seg)
                if x2 - x1 > h:
                    rounded_rect(self.bar, x1, 0, x2, h, h / 2,
                                 fill=ctx.c("violet_lt"), outline="")
            self.after(50, self._animate)
        except tk.TclError:
            pass


class BasisDialog(BaseDialog):
    """判定依据：只展示当前真正生效的三层判定链。

    为什么重做
    ----------
    旧版直接把 verdict.rules 原样列出来 —— 那是早期「驱动版本推断」时代的规则表
    （LHR 移除版本 / LHR 设备 ID / 显存频率 / 功耗）。自从判定改成「实测 TFLOPS 优先」，
    这几条一条都不参与最终结论，可它们仍被画在最显眼的位置，
    真实判据（实测 vs 型号库参考值 × 0.75）反倒不显示。

    现在的结构对应 unlock.verdict_now() 的实际优先级：
      ① 实测算力（judge_by_compute）—— 硬否决
      ② 本次开机 loader.log        —— 佐证
      ③ 驱动版本推断               —— 仅在前两者都缺失时兜底
    每一层都标出「生效 / 未生效 / 被否决」，用户一眼能看出谁决定了结论。
    """

    def __init__(self, master, ctx, tr, combined, tflops_summary,
                 unlocked_ref, entry, threshold=0.75, width=560):
        super().__init__(master, ctx, tr("unlock.basis"), width)
        self.tr = tr
        pad = ctx.s(22)
        self.add_title(tr("unlock.basis"), tr("basis.sub"))
        # 标题文字很短，Tk 会按文字宽度把窗口压窄。这里先钉住内容区宽度，
        # 后面所有行都在这个宽度里做左右两端对齐。
        tk.Frame(self.frame, bg=ctx.c("card"), height=ctx.s(1),
                 width=self._dw - pad * 2).pack(fill="x")

        body = tk.Frame(self.frame, bg=ctx.c("card"))
        body.pack(fill="both", expand=True, padx=pad)

        # ---- 结论条
        state = (combined or {}).get("state") or "unknown"
        fg, bg = T.state_color(ctx.theme, state)
        bar = tk.Canvas(body, bg=ctx.c("card"), bd=0, highlightthickness=0,
                        height=ctx.s(46))
        bar.pack(fill="x", pady=(0, ctx.s(12)))
        bar.bind("<Configure>",
                 lambda e, c=bar, s=state, f=fg, b=bg: self._paint_bar(
                     c, s, f, b, ctx, tr))

        # ---- 三层判定链
        self._chain(body, ctx, pad, combined, tflops_summary, unlocked_ref,
                    entry, threshold)

        # ---- 备注（仲裁说明）
        note_key = (combined or {}).get("note_key")
        if note_key:
            tk.Label(body, text=tr(note_key), bg=ctx.c("card"), fg=ctx.c("ink_soft"),
                     font=ctx.font("small"), anchor="w", justify="left",
                     wraplength=self._dw - pad * 2).pack(fill="x",
                                                         pady=(ctx.s(10), 0))

        self.add_buttons([(tr("msg.ok"), True, "solid")])

    def _paint_bar(self, canvas, state, fg, bg, ctx, tr):
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        canvas.delete("all")
        if w < 20 or h < 20:
            return
        rounded_rect(canvas, 0, 0, w, h, ctx.s(12), fill=bg, outline="")
        canvas.create_text(w / 2, h / 2, text=tr("state." + state), fill=fg,
                           font=ctx.font("h2", bold=True))

    def _section(self, parent, ctx, index, title, active, why):
        """一节 = 序号圆点 + 标题 + 生效状态。返回可往里塞明细的容器。"""
        row = tk.Frame(parent, bg=ctx.c("card"))
        row.pack(fill="x", pady=(ctx.s(10), ctx.s(2)))

        dot = tk.Canvas(row, bg=ctx.c("card"), width=ctx.s(20), height=ctx.s(20),
                        bd=0, highlightthickness=0)
        dot.pack(side="left")
        tone = "ok" if active == "on" else ("warn" if active == "veto" else "chip_bg")
        rounded_rect(dot, 0, 0, ctx.s(20), ctx.s(20), ctx.s(10),
                     fill=ctx.c(tone) if tone != "chip_bg" else ctx.c("chip_bg"),
                     outline="")
        dot.create_text(ctx.s(10), ctx.s(10), text=index,
                        fill=ctx.c("card") if tone != "chip_bg" else ctx.c("ink_faint"),
                        font=ctx.font("small", bold=True))

        tk.Label(row, text=title, bg=ctx.c("card"), fg=ctx.c("ink"),
                 font=ctx.font("body", bold=True), anchor="w").pack(
            side="left", padx=(ctx.s(8), 0))
        tag = {"on": "basis.tag.on", "veto": "basis.tag.veto",
               "off": "basis.tag.off"}.get(active, "basis.tag.off")
        tk.Label(row, text=self.tr(tag), bg=ctx.c("card"),
                 fg=(ctx.c("ok") if active == "on" else
                     ctx.c("warn") if active == "veto" else ctx.c("ink_faint")),
                 font=ctx.font("small", bold=True)).pack(side="left",
                                                          padx=(ctx.s(8), 0))
        if why:
            tk.Label(row, text=why, bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                     font=ctx.font("small")).pack(side="left",
                                                 padx=(ctx.s(8), 0))

        box = tk.Frame(parent, bg=ctx.c("card"))
        box.pack(fill="x", padx=(ctx.s(28), 0))
        return box

    def _line(self, parent, ctx, left, right, tone="ink"):
        """左标签 + 右数值，两端对齐。

        用 grid 而不是 pack(side=...)：pack 的左右两段会互相挤压，
        标签长一点就把右侧数值顶成换行或重叠（实测踩到过）。
        grid 的 column 1 有 weight，中间的空隙会把两者自然拉开。
        """
        r = tk.Frame(parent, bg=ctx.c("card"))
        r.pack(fill="x", pady=ctx.s(1))
        r.columnconfigure(1, weight=1)
        tk.Label(r, text=left, bg=ctx.c("card"), fg=ctx.c("ink_soft"),
                 font=ctx.font("small"), anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(r, text=right, bg=ctx.c("card"), fg=ctx.c(tone),
                 font=ctx.font("mono"), anchor="e").grid(row=0, column=2, sticky="e")

    def _chain(self, body, ctx, pad, combined, tflops_summary, unlocked_ref,
               entry, threshold):
        tr = self.tr
        cv = combined or {}
        tf_state = cv.get("tf")
        log_state = cv.get("log")
        final = cv.get("state")

        # ---------------- ① 实测算力
        rows = tflops_summary or []
        measured = [r for r in rows if r.get("tflops")]
        if not measured:
            box = self._section(body, ctx, "1", tr("basis.l1"), "off",
                                tr("basis.l1.none"))
            self._line(box, ctx, tr("basis.measured"), tr("basis.not_run"),
                       "ink_faint")
        else:
            active = "on" if tf_state in ("locked", "unlocked") else "off"
            why = tr("basis.l1.key") if tf_state == "locked" else ""
            box = self._section(body, ctx, "1", tr("basis.l1"), active, why)
            ref = unlocked_ref or {}
            worst_ratio = None
            for r in measured:
                key = r.get("key")
                val = float(r["tflops"])
                band = ref.get(key)
                lo = None
                if band:
                    lo = band[0] if isinstance(band, (list, tuple)) else band
                if lo:
                    ratio = val / float(lo)
                    if worst_ratio is None or ratio < worst_ratio:
                        worst_ratio = ratio
                    tone = "ok" if ratio >= threshold else "bad"
                    self._line(box, ctx,
                               ("%-20s %6.1f / %.0f") % (tr("case." + key),
                                                          val, lo),
                               "%3.0f%%" % (ratio * 100), tone)
                else:
                    self._line(box, ctx, ("%-20s %6.1f") % (tr("case." + key), val),
                               tr("basis.no_ref"), "ink_faint")
            if worst_ratio is not None:
                self._line(box, ctx,
                           tr("basis.threshold_fmt").replace(
                               "{p}", "%.0f%%" % (threshold * 100)),
                           "%.0f%% %s" % (worst_ratio * 100,
                                          tr("basis.pass") if worst_ratio >= threshold
                                          else tr("basis.fail")),
                           "ok" if worst_ratio >= threshold else "bad")

        # ---------------- ② 本次开机日志
        if log_state in ("unlocked", "locked"):
            box = self._section(body, ctx, "2", tr("basis.l2"),
                                "on" if final == log_state else "veto", "")
            self._line(box, ctx, tr("basis.log_state"),
                       tr("state." + log_state),
                       "ok" if log_state == "unlocked" else "warn")
            self._line(box, ctx, tr("basis.log_boot"), tr("basis.log_same_boot"))
        else:
            box = self._section(body, ctx, "2", tr("basis.l2"), "off",
                                tr("basis.l2.none"))
            self._line(box, ctx, tr("basis.log_state"), tr("basis.no_log"),
                       "ink_faint")

        # ---------------- ③ 驱动推断（兜底）
        driver = cv.get("driver")
        if driver in ("unlocked", "locked"):
            # 有驱动推断结果，但前两层已定论 → 它只是备而未用
            box = self._section(body, ctx, "3", tr("basis.l3"), "off",
                                tr("basis.l3.fallback"))
            self._line(box, ctx, tr("basis.driver_state"),
                       tr("state." + driver), "ink_soft")
        else:
            box = self._section(body, ctx, "3", tr("basis.l3"), "off",
                                tr("basis.l3.skipped"))
            # 这里是「这一层没有给出结论」，不是「结论是无法判定」——
            # 后者读起来像最终判定失败，会误导。
            self._line(box, ctx, tr("basis.driver_state"),
                       tr("basis.no_conclusion"), "ink_faint")
