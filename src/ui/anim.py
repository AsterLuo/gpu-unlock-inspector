# -*- coding: utf-8 -*-
"""轻量动效引擎：缓动曲线、入场、数字滚动、状态过渡。

设计原则
--------
1. **只做有信息价值的动效**：出现、变化、聚焦。不做无意义的抖动/弹跳。
2. **全部走合成属性**：Tk 里没有 GPU 合成，但「只改颜色 / 只改坐标」等价于
   CSS 的 color / transform —— 不触发控件重排，代价最低。
3. **可量化**：时长取自 theme.MOTION，缓动取自 theme.EASE 的 cubic-bezier，
   所有参数都能在令牌表里查到，不写魔法数字。
4. **可降级**：尊重系统「减少动态效果」（theme.motion_scale() 返回 0 时全部即时完成）；
   程序内也可用 set_enabled(False) 全局关闭。

为什么不用固定 interval
----------------------
原来是 steps=10 / interval=14 平均分配，视觉上是「匀速走完」，机械且没有收尾感。
现在按 cubic-bezier 采样：前 30% 走完约 70% 的行程，尾部自然减速 —— 这就是
CSS 里 cubic-bezier(0.2, 0, 0, 1) 的手感。
"""
from __future__ import annotations

import tkinter as tk

from core import theme as T

# 全局开关：设置里关掉动效或系统要求减动效时置 False
_ENABLED = True
# 每帧间隔：~60fps
FRAME_MS = 16


def set_enabled(on):
    """全局开关（设置面板 / 系统减动效都会调它）。"""
    global _ENABLED
    _ENABLED = bool(on)


def enabled():
    return _ENABLED and T.motion_scale() > 0.0


def _mix(c1, c2, t):
    """在两个 #rrggbb 之间插值。"""
    try:
        a = tuple(int(c1.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        b = tuple(int(c2.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return c1
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def run(widget, ms, on_frame, ease_name="standard", on_done=None):
    """通用补间驱动。

    on_frame(progress) 会按缓动后的进度被调用（progress ∈ [0,1]，含 0 与 1）。
    ms=0 或动效被关闭时直接调一次 on_frame(1.0)，保证逻辑仍然成立。
    """
    if not enabled() or ms <= 0:
        try:
            on_frame(1.0)
            if on_done:
                on_done()
        except tk.TclError:
            pass
        return None

    frames = max(1, int(round(ms / float(FRAME_MS))))

    def step(i):
        try:
            if i > frames:
                on_frame(1.0)
                if on_done:
                    on_done()
                return
            raw = i / float(frames)
            on_frame(T.ease(ease_name, raw))
            widget.after(FRAME_MS, lambda: step(i + 1))
        except tk.TclError:
            pass

    step(0)
    return True


def fade_in(widget, ctx, ms=None, bg_from=None, bg_to=None, ease_name="standard"):
    """控件底色淡入（等价 CSS 的 background-color transition）。"""
    start = bg_from or ctx.c("bg")
    end = bg_to or ctx.c("card")
    run(widget, ms if ms is not None else T.duration("base"),
        lambda p: widget.configure(bg=_mix(start, end, p)), ease_name)


def reveal(frame, ctx, ms=None):
    """卡片入场：底色从页面色渐入到卡片色，稍慢一点更有层次。"""
    fade_in(frame, ctx, ms if ms is not None else T.duration("slow"))


class Counter:
    """数字滚动：从旧值平滑变到新值（等价 CSS 的 @property 数字动画）。"""

    def __init__(self, label, ctx, fmt="%.1f", ms=None, ease_name="standard"):
        self.label = label
        self.ctx = ctx
        self.fmt = fmt
        self.ms = ms if ms is not None else T.duration("base")
        self.ease_name = ease_name
        self._job = None

    def set(self, new, suffix=""):
        try:
            old = float(getattr(self.label, "_value", 0) or 0)
        except (TypeError, ValueError):
            old = 0.0
        try:
            new = float(new)
        except (TypeError, ValueError):
            try:
                self.label.configure(text=str(new))
            except tk.TclError:
                pass
            return
        self.label._value = new

        def frame(p):
            try:
                self.label.configure(text=(self.fmt % (old + (new - old) * p)) + suffix)
            except tk.TclError:
                raise

        try:
            run(self.label, self.ms, frame, self.ease_name)
        except tk.TclError:
            pass


def pulse(widget, ctx, color="violet", times=2, interval=180):
    """轻脉冲：用于「刚刚更新过」的强调。减动效时直接把颜色落定。"""
    if not times:
        try:
            widget.configure(fg=ctx.c(color))
        except tk.TclError:
            pass
        return
    if not enabled():
        return
    try:
        base_fg = widget.cget("fg")
    except tk.TclError:
        return

    def back():
        try:
            widget.configure(fg=base_fg)
        except tk.TclError:
            return
        widget.after(interval, lambda: pulse(widget, ctx, color, times - 1, interval))

    try:
        widget.configure(fg=ctx.c(color))
        widget.after(interval, back)
    except tk.TclError:
        pass


class Fade:
    """状态切换过渡：把一组控件的底色从旧主题淡到新主题（切主题/切语言用）。"""

    def __init__(self, ctx):
        self.ctx = ctx

    def swap(self, widget, old_color_name, new_color_name, ms=None):
        run(widget, ms if ms is not None else T.duration("fast"),
            lambda p: widget.configure(
                bg=_mix(self.ctx.c(old_color_name), self.ctx.c(new_color_name), p)))
