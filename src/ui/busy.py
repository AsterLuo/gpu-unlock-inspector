# -*- coding: utf-8 -*-
"""统一的「忙」反馈：spinner、进度条、完成提示。

为什么单独抽出来
----------------
此前多处点击后没有任何反馈（U 盘扫描、环境扫描、制盘、导出…），
用户不知道程序是不是没响应。这类问题不该逐个 patch，
应该有一个统一的入口：调用时挂在状态栏 + 卡片上，结束后给明确结论。
"""
from __future__ import annotations

import tkinter as tk

from core import theme as T
from ui.base import rounded_rect


class Spinner(tk.Canvas):
    """旋转加载指示器。纯 Canvas 绘制，不依赖图片。"""

    FRAMES = "|/-\\"

    def __init__(self, master, ctx, size=16, color="violet"):
        self.ctx = ctx
        self.color = color
        s = ctx.s(size)
        super().__init__(master, bg=ctx.c("card"), width=s, height=s,
                         bd=0, highlightthickness=0)
        self._size = s
        self._i = 0
        self._job = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self):
        self._running = False
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        try:
            self.delete("all")
        except tk.TclError:
            pass

    def _tick(self):
        if not self._running:
            return
        try:
            self.delete("all")
            ch = self.FRAMES[self._i % len(self.FRAMES)]
            self.create_text(self._size / 2, self._size / 2, text=ch,
                             fill=self.ctx.c(self.color),
                             font=self.ctx.font("h3", bold=True))
            self._i += 1
            self._job = self.after(120, self._tick)
        except tk.TclError:
            self._running = False


class StatusLine(tk.Frame):
    """状态行：spinner + 文案 + 可选百分比。

    set_busy("正在扫描 U 盘…")   → 显示 spinner 并开始转
    set_progress(0.42, "…")      → 显示进度条
    set_done("完成")             → 收起，显示绿色结论
    set_error("失败")            → 收起，显示红色结论
    """

    def __init__(self, master, ctx, bg_name="card"):
        self.ctx = ctx
        bg = ctx.c(bg_name)
        super().__init__(master, bg=bg)
        self.spinner = Spinner(self, ctx)
        self.label = tk.Label(self, text="", bg=bg, fg=ctx.c("ink_faint"),
                              font=ctx.font("small"), anchor="w", justify="left")
        self.label.pack(side="left", fill="x", expand=True, padx=(ctx.s(6), 0))

    def _ensure_spinner(self, show):
        if show:
            if not self.spinner.winfo_ismapped():
                self.spinner.pack(side="left", before=self.label)
            self.spinner.start()
        else:
            self.spinner.stop()
            if self.spinner.winfo_ismapped():
                self.spinner.pack_forget()

    def set_busy(self, text):
        self._ensure_spinner(True)
        self.label.configure(text=text, fg=self.ctx.c("ink_soft"))

    def set_progress(self, frac, text=None):
        self._ensure_spinner(False)
        pct = max(0, min(100, round((frac or 0) * 100)))
        self.label.configure(text="%s  %d%%" % (text or "", pct) if text else "%d%%" % pct,
                             fg=self.ctx.c("violet"))

    def set_done(self, text):
        self._ensure_spinner(False)
        self.label.configure(text="\u2713  " + text, fg=self.ctx.c("ok"))
        self._cue("success")

    def set_error(self, text):
        self._ensure_spinner(False)
        self.label.configure(text="\u2715  " + text, fg=self.ctx.c("bad"))
        self._cue("error")

    def _cue(self, name):
        """操作结论音：成功两声上行、失败单声下行。静音时静默跳过。"""
        try:
            from ui import sound
            sound.play(name)
        except Exception:
            pass

    def clear(self):
        self._ensure_spinner(False)
        self.label.configure(text="", fg=self.ctx.c("ink_faint"))
