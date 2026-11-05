# -*- coding: utf-8 -*-
"""可滚动容器：整页纵向滚动 + 鼠标滚轮，滚动条自绘。"""
from __future__ import annotations

import sys
import tkinter as tk


class ScrollPage(tk.Frame):
    def __init__(self, master, ctx):
        super().__init__(master, bg=ctx.c("bg"), bd=0, highlightthickness=0)
        self.ctx = ctx
        self.canvas = tk.Canvas(self, bg=ctx.c("bg"), bd=0, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar = tk.Canvas(self, bg=ctx.c("bg"), bd=0, highlightthickness=0, width=ctx.s(10))
        self.vbar.pack(side="right", fill="y")

        self.content = tk.Frame(self.canvas, bg=ctx.c("bg"))
        self._win = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._on_content)
        self.canvas.bind("<Configure>", self._on_canvas)

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(seq, self._on_wheel, add="+")

        self._drag_from = None
        self.vbar.bind("<Button-1>", self._bar_press)
        self.vbar.bind("<B1-Motion>", self._bar_drag)

    def _on_content(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._draw_bar()

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        self._draw_bar()

    def _on_wheel(self, event):
        if not self.winfo_ismapped():
            return
        try:
            if event.num == 4:
                delta = -3
            elif event.num == 5:
                delta = 3
            else:
                delta = -1 * int(event.delta / 120) * 3
            self.canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass
        self._draw_bar()

    def _draw_bar(self):
        self.vbar.delete("all")
        ch = self.canvas.winfo_height()
        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        total = max(1, bbox[3] - bbox[1])
        if total <= ch:
            return
        top, bottom = self.canvas.yview()
        w = self.vbar.winfo_width()
        y1 = top * ch
        y2 = bottom * ch
        r = min(w / 2, (y2 - y1) / 2, 4)
        from ui.base import rounded_rect
        rounded_rect(self.vbar, 2, y1 + 2, w - 2, y2 - 2, r,
                     fill=self.ctx.c("line"), outline="")

    def _bar_press(self, event):
        ch = self.canvas.winfo_height()
        self.canvas.yview_moveto(max(0.0, min(1.0, event.y / max(1, ch))))
        self._draw_bar()

    def _bar_drag(self, event):
        self._bar_press(event)

    def scroll_to_top(self):
        self.canvas.yview_moveto(0)
        self._draw_bar()

    def clear(self):
        for child in self.content.winfo_children():
            child.destroy()