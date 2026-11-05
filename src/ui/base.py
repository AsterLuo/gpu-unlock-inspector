# -*- coding: utf-8 -*-
"""UI 基础层：主题上下文、字体、圆角卡片、徽章、按钮、图表画布。

所有颜色都从 core.theme 取，不在这里写字面量颜色。
所有文字都从 i18n 取，不在这里写字面量文案。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from core import theme as T


class Ctx:
    """一次绘制所需的全部上下文：主题表 + 翻译函数 + 字体 + DPI 缩放。"""

    def __init__(self, theme_name, tr, scale=1.0):
        self.theme_name = theme_name
        self.theme = T.THEMES.get(theme_name, T.THEMES["light"])
        self.tr = tr
        self.scale = scale
        self._fonts = {}

    def s(self, px):
        """按 DPI 缩放像素值。"""
        return int(round(px * self.scale))

    def font(self, kind="body", bold=False):
        key = (kind, bold)
        if key in self._fonts:
            return self._fonts[key]
        size = T.FONTS.get(kind, 11)
        family = "Microsoft YaHei UI" if self.tr.lang == "zh" else "Segoe UI"
        if kind == "mono":
            family = "Consolas"
        f = tkfont.Font(family=family, size=-self.s(size) if self.scale != 1.0 else size,
                        weight="bold" if bold else "normal")
        self._fonts[key] = f
        return f

    def c(self, name):
        return self.theme.get(name, "#000000")


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形（用 smooth polygon 近似，抗锯齿效果好）。"""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


def soft_shadow(canvas, x1, y1, x2, y2, r, color, layers=3, spread=3):
    """用多层低对比圆角矩形模拟 CSS 的负扩散柔阴影。"""
    for i in range(layers, 0, -1):
        d = spread * i
        rounded_rect(canvas, x1 - d, y1 - d + 2 * i, x2 + d, y2 + d + 2 * i, r + d,
                     fill=color, outline="")


class Card(tk.Frame):
    """卡片式分组：白底 + 大圆角 + 柔阴影 + 标题层级。"""

    def __init__(self, master, ctx, title_key=None, desc_key=None, **kw):
        bg = ctx.c("bg")
        super().__init__(master, bg=bg, bd=0, highlightthickness=0, **kw)
        self.ctx = ctx
        self._title_key = title_key
        self._desc_key = desc_key
        self.body = None
        self._make_shell()

    def _make_shell(self):
        ctx = self.ctx
        self.canvas = tk.Canvas(self, bg=ctx.c("bg"), bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=ctx.c("card"))
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", self._on_resize)
        self.inner.bind("<Configure>", self._on_inner)

        pad = ctx.s(T.PAD["card"])
        head = tk.Frame(self.inner, bg=ctx.c("card"))
        head.pack(fill="x", padx=pad, pady=(pad, 0))
        if self._title_key:
            tk.Label(head, text=ctx.tr(self._title_key), bg=ctx.c("card"),
                     fg=ctx.c("ink"), font=ctx.font("h2", bold=True),
                     anchor="w").pack(side="left")
        if self._desc_key:
            tk.Label(head, text=ctx.tr(self._desc_key), bg=ctx.c("card"),
                     fg=ctx.c("ink_faint"), font=ctx.font("small"),
                     anchor="w").pack(side="left", padx=(ctx.s(10), 0))
        self.head = head

        self.body = tk.Frame(self.inner, bg=ctx.c("card"))
        self.body.pack(fill="both", expand=True, padx=pad, pady=(ctx.s(10), pad))

    def _on_resize(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        self._redraw_bg(event.width, self.canvas.winfo_height())

    def _on_inner(self, event):
        # 关键：内容多高，Canvas 就多高 —— 少了这句卡片高度会塌成 0
        try:
            cur = int(float(self.canvas.cget("height") or 0))
        except (TypeError, ValueError):
            cur = -1
        if abs(cur - event.height) > 1:
            self.canvas.configure(height=event.height)
        self.canvas.itemconfigure(self._win, width=self.canvas.winfo_width() or event.width)
        self._redraw_bg(self.canvas.winfo_width(), event.height)

    def _redraw_bg(self, w, h):
        """卡片底：柔和多层阴影 + 1px 边线。

        等价 CSS：
          box-shadow: 0 1px 2px rgba(0,0,0,.04),
                      0 4px 12px rgba(0,0,0,.05),
                      0 12px 28px rgba(0,0,0,.04);
        用三层不同扩散的低对比圆角矩形近似 —— 单层阴影会显得「贴纸感」，
        三层叠起来才有卡片浮在页面上的层次。
        """
        if w < 10 or h < 10:
            return
        self.canvas.delete("bgart")
        ctx = self.ctx
        r = ctx.s(T.RADIUS["card"])
        self.canvas.create_rectangle(0, 0, w, h, fill=ctx.c("bg"), outline="",
                                     tags="bgart")
        # 阴影层：由外向内逐渐加深
        for i, (spread, color) in enumerate(((ctx.s(12), ctx.c("shadow_l")),
                                             (ctx.s(5), ctx.c("shadow")),
                                             (ctx.s(2), ctx.c("shadow_s")))):
            rounded_rect(self.canvas, 2 + spread * 0.35, 2 + spread,
                         w - 2 - spread * 0.35, h - 2 + spread * 0.6,
                         r + spread * 0.4, fill=color, outline="", tags="bgart")
        rounded_rect(self.canvas, 1, 1, w - 1, h - 1, r,
                     fill=ctx.c("card"), outline=ctx.c("line"), width=1, tags="bgart")
        self.canvas.tag_lower("bgart")


class Badge(tk.Canvas):
    """状态徽章：绿=已解锁 灰=不适用 黄=未解锁 红=无法判定。"""

    def __init__(self, master, ctx, state="unknown", text=None, big=False):
        self.ctx = ctx
        self.state = state
        self.big = big
        fg, bg = T.state_color(ctx.theme, state)
        h = ctx.s(40 if big else 26)
        w = ctx.s(150 if big else 96)
        if big:
            w = max(w, len(ctx.tr("state." + state)) * ctx.s(22))
        super().__init__(master, bg=ctx.c("card"), bd=0, highlightthickness=0,
                         height=h, width=w)
        self.w, self.h = w, h
        rounded_rect(self, 0, 0, w, h, h / 2, fill=bg, outline="")
        label = text if text is not None else ctx.tr("state." + state)
        self.create_text(w / 2, h / 2, text=label, fill=fg,
                         font=ctx.font("h1" if big else "h3", bold=True))


class Button(tk.Canvas):
    """四态按钮：normal / hover / active / disabled + focus 环。

    kind: solid（渐变实心，主操作）/ ghost（描边，次操作）/ quiet（无边框，三级）

    状态来源
    --------
    鼠标：<Enter>/<Leave> 驱动 hover，<ButtonPress>/<ButtonRelease> 驱动 active
    键盘：takefocus=1，<space>/<Return> 等价点击，<FocusIn>/<FocusOut> 画 2px 焦点环
    禁用：cursor 变 arrow、文字用 disabled_fg、不再响应任何事件

    为什么按钮要自己画而不直接用 tk.Button
    ------------------------------------
    tk.Button 在 Windows 上强制使用原生主题（无法圆角、无法渐变、无法自定义焦点环），
    与本项目的视觉体系冲突。Canvas 绘制代价与 Label 同级，且能精确控制三态。
    """

    def __init__(self, master, ctx, text, command=None, kind="ghost", width=None, height=None):
        self.ctx = ctx
        self.text = text
        self.command = command
        self.kind = kind
        self.enabled = True
        self._hover = False
        self._active = False
        self._focus = False
        self._anim_job = None
        self._hover_t = 0.0
        f = ctx.font("body", bold=(kind == "solid"))
        tw = f.measure(text) if hasattr(f, "measure") else len(text) * 8
        h = ctx.s(height or 34)
        # 44px 是最小可点面积（WCAG 2.5.5 建议 24px，桌面端 32 起也够）
        w = ctx.s(width or 0) or (tw + ctx.s(32))
        super().__init__(master, bg=ctx.c("card"), bd=0, highlightthickness=0,
                         height=h, width=w, takefocus=1)
        self.w, self.h = w, h
        self._draw()
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Key-space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.configure(cursor="hand2")

    # ---- 状态迁移 ------------------------------------------------------
    def _on_enter(self, _e):
        if not self.enabled:
            return
        self._hover = True
        self._tween_hover(1.0)

    def _on_leave(self, _e):
        self._hover = False
        self._active = False
        self._tween_hover(0.0)

    def _tween_hover(self, target):
        """悬停色渐变：140ms / cubic-bezier(0.2,0,0,1)，只重绘本控件。"""
        from ui import anim
        start = self._hover_t
        delta = target - start
        anim.run(self, T.duration("fast"),
                 lambda p: self._apply_hover(start + delta * p))
        if not anim.enabled():
            self._apply_hover(target)

    def _apply_hover(self, t):
        self._hover_t = max(0.0, min(1.0, t))
        self._draw()

    def _on_focus_in(self, _e):
        self._focus = True
        self._draw()

    def _on_focus_out(self, _e):
        self._focus = False
        self._draw()

    def _on_press(self, _e):
        if not self.enabled:
            return
        self._active = True
        self.focus_set()
        self._draw()

    def _on_release(self, _e):
        was = self._active
        self._active = False
        self._draw()
        if was and self.enabled and self.command:
            self._fire()

    def _on_key(self, _e):
        if self.enabled and self.command:
            self._fire()
        return "break"

    def _fire(self):
        """所有按钮激活的唯一出口：音效 → 命令。

        音效放在这里而不是每个 command 里，是为了保证「每个可点按钮都有反馈」
        且不会有人漏加，也不会重复加。
        """
        try:
            from ui import sound
            sound.play("click")
        except Exception:
            pass
        if self.command:
            self.command()

    # ---- 绘制 ----------------------------------------------------------
    def _draw(self):
        ctx = self.ctx
        self.delete("all")
        w, h = self.w, self.h
        r = ctx.s(T.RADIUS["sm"])
        hv = self._hover_t
        press = 1.0 if self._active else 0.0

        if not self.enabled:
            # 禁用态：底色压平、文字用 disabled_fg —— 一眼能看出不可点
            if self.kind == "solid":
                rounded_rect(self, 0, 0, w, h, r,
                             fill=ctx.c("violet_disabled"), outline="")
            elif self.kind == "ghost":
                rounded_rect(self, 0, 0, w, h, r, fill=ctx.c("disabled_bg"),
                             outline=ctx.c("line"), width=1)
            fg = ctx.c("disabled_fg")
        elif self.kind == "solid":
            c1 = T.mix(ctx.c("grad_from"), ctx.c("violet_hover"), hv)
            c2 = T.mix(ctx.c("grad_to"), ctx.c("violet_hover"), hv)
            # 按下：整体再压暗一点（等价 CSS :active 的 filter: brightness(.94)）
            if press:
                c1 = T.mix(c1, "#000000", 0.10)
                c2 = T.mix(c2, "#000000", 0.10)
            steps = 24
            for i in range(steps):
                x1 = w * i / steps
                x2 = w * (i + 1) / steps + 1
                self.create_rectangle(x1, 0, x2, h,
                                      fill=T.mix(c1, c2, i / max(1, steps - 1)), outline="")
            rounded_rect(self, 0, 0, w, h, r, fill="", outline=ctx.c("bg"),
                         width=ctx.s(3))
            fg = "#ffffff"
        elif self.kind == "quiet":
            fg = T.mix(ctx.c("ink_soft"), ctx.c("ink"), hv)
            if hv > 0.01:
                rounded_rect(self, 0, 0, w, h, r,
                             fill=T.mix(ctx.c("card"), ctx.c("chip_bg"), hv), outline="")
        else:  # ghost
            fill = T.mix(ctx.c("card"), ctx.c("accent_soft"), hv)
            edge = T.mix(ctx.c("line"), ctx.c("violet"), hv * 0.55)
            rounded_rect(self, 0, 0, w, h, r, fill=fill, outline=edge, width=1)
            fg = ctx.c("ink")

        self.create_text(w / 2, h / 2, text=self.text, fill=fg,
                         font=ctx.font("body", bold=(self.kind == "solid")))

        # 焦点环：键盘用户唯一的定位依据，必须在所有状态上都能看见
        if self._focus and self.enabled:
            rounded_rect(self, 1, 1, w - 1, h - 1, r,
                         fill="", outline=ctx.c("focus_ring"), width=ctx.s(2))

    def set_enabled(self, on):
        self.enabled = bool(on)
        if not self.enabled:
            self._hover = False
            self._active = False
            self._hover_t = 0.0
        self.configure(cursor="hand2" if self.enabled else "arrow",
                       takefocus=1 if self.enabled else 0)
        self._draw()

    def set_text(self, text):
        self.text = text
        self._draw()

    def _on_click(self, _e):
        if self.enabled and self.command:
            self.command()


class Segmented(tk.Frame):
    """分段控件（语言 / 主题 / 强度 / 算法都用它）。"""

    def __init__(self, master, ctx, options, value, command=None, bg_name="card"):
        self.ctx = ctx
        self.options = options      # [(value, label)]
        self.value = value
        self.command = command
        super().__init__(master, bg=ctx.c(bg_name), bd=0, highlightthickness=0)
        self._buttons = {}
        self._rebuild()

    def _rebuild(self):
        for child in self.winfo_children():
            child.destroy()
        self._buttons = {}
        for val, label in self.options:
            b = tk.Label(self, text=label, padx=self.ctx.s(10), pady=self.ctx.s(4),
                         font=self.ctx.font("small", bold=True), cursor="hand2")
            b.pack(side="left", padx=(0, self.ctx.s(4)))
            b.bind("<Button-1>", lambda e, v=val: self._select(v))
            self._buttons[val] = b
        self._paint()

    def _paint(self):
        ctx = self.ctx
        for val, b in self._buttons.items():
            on = (val == self.value)
            b.configure(bg=ctx.c("accent_soft") if on else ctx.c("chip_bg"),
                        fg=ctx.c("violet") if on else ctx.c("ink_soft"))

    def _select(self, val):
        self.value = val
        self._paint()
        if self.command:
            self.command(val)

    def set_value(self, val, notify=False):
        self.value = val
        self._paint()
        if notify and self.command:
            self.command(val)

    def set_options(self, options, value=None):
        self.options = options
        if value is not None:
            self.value = value
        self._rebuild()


class Sparkline(tk.Canvas):
    """算力曲线：平滑折线 + 渐变填充 + 网格 + 悬停读数。"""

    def __init__(self, master, ctx, height=150):
        self.ctx = ctx
        super().__init__(master, bg=ctx.c("card"), bd=0, highlightthickness=0,
                         height=ctx.s(height))
        self.points = []
        self.power_points = []
        self._hover = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))

    def set_data(self, points, power=None):
        self.points = list(points or [])
        self.power_points = list(power or [])
        self.redraw()

    def _on_motion(self, e):
        if not self.points:
            return
        w = self.winfo_width()
        pad = self.ctx.s(10)
        span = max(1, w - pad * 2)
        idx = int(round((e.x - pad) / span * (len(self.points) - 1)))
        idx = max(0, min(len(self.points) - 1, idx))
        self._set_hover(idx)

    def _set_hover(self, idx):
        self._hover = idx
        self.redraw()

    def redraw(self):
        ctx = self.ctx
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 20 or h < 20:
            return
        pad = ctx.s(10)
        pad_b = ctx.s(18)

        # 网格
        for i in range(1, 4):
            y = pad + (h - pad - pad_b) * i / 4
            self.create_line(pad, y, w - pad, y, fill=ctx.c("grid"), width=1)

        if len(self.points) < 2:
            self.create_text(w / 2, h / 2, text=ctx.tr("bench.idle"),
                             fill=ctx.c("ink_faint"), font=ctx.font("small"))
            return

        vals = [p for p in self.points if p is not None]
        if not vals:
            return
        vmin, vmax = min(vals), max(vals)
        if vmax - vmin < 1e-6:
            vmax = vmin + 1
        span = vmax - vmin
        vmin -= span * 0.12
        vmax += span * 0.12
        n = len(self.points)

        def xy(i, v):
            x = pad + (w - pad * 2) * i / (n - 1)
            y = h - pad_b - (h - pad_b - pad) * (v - vmin) / (vmax - vmin)
            return x, y

        # 渐变填充
        poly = [(pad, h - pad_b)]
        for i, v in enumerate(self.points):
            if v is None:
                continue
            poly.append(xy(i, v))
        poly.append((w - pad, h - pad_b))
        fill_color = T.mix(ctx.c("card"), ctx.c("violet"), 0.14)
        if len(poly) > 2:
            flat = [c for pt in poly for c in pt]
            self.create_polygon(flat, fill=fill_color, outline="")

        # 折线
        line_pts = []
        for i, v in enumerate(self.points):
            if v is None:
                continue
            line_pts.extend(xy(i, v))
        if len(line_pts) >= 4:
            self.create_line(line_pts, fill=ctx.c("violet"), width=ctx.s(2),
                             smooth=True, splinesteps=12)

        # 悬停
        if self._hover is not None and self._hover < n:
            hx, hy = xy(self._hover, self.points[self._hover] or vmin)
            self.create_line(hx, pad, hx, h - pad_b, fill=ctx.c("line"), width=1)
            self.create_oval(hx - ctx.s(4), hy - ctx.s(4), hx + ctx.s(4), hy + ctx.s(4),
                             fill=ctx.c("card"), outline=ctx.c("violet"), width=ctx.s(2))
            label = "%.1f" % (self.points[self._hover] or 0)
            tw = ctx.s(52)
            rounded_rect(self, hx - tw / 2, max(pad, hy - ctx.s(26)),
                         hx + tw / 2, max(pad, hy - ctx.s(26)) + ctx.s(18),
                         ctx.s(6), fill=ctx.c("ink"), outline="")
            self.create_text(hx, max(pad, hy - ctx.s(26)) + ctx.s(9), text=label,
                             fill=ctx.c("card"), font=ctx.font("mono"))

        # 轴标签
        self.create_text(pad, h - pad_b + ctx.s(9), text="0s", anchor="w",
                         fill=ctx.c("ink_faint"), font=ctx.font("small"))
        self.create_text(w - pad, h - pad_b + ctx.s(9),
                         text="%.0fs" % max(p.get("t", 0) if isinstance(p, dict) else n
                                            for p in [self.points]),
                         anchor="e", fill=ctx.c("ink_faint"), font=ctx.font("small"))


class Metric(tk.Frame):
    """大字号关键数值 + 小标签。"""

    def __init__(self, master, ctx, label, value, unit="", tone="ink", size="metric"):
        super().__init__(master, bg=ctx.c("card"))
        self.ctx = ctx
        tk.Label(self, text=label, bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                 font=ctx.font("small"), anchor="w").pack(anchor="w")
        row = tk.Frame(self, bg=ctx.c("card"))
        row.pack(anchor="w")
        self.val = tk.Label(row, text=value, bg=ctx.c("card"), fg=ctx.c(tone),
                            font=ctx.font(size, bold=True))
        self.val.pack(side="left")
        self.unit = tk.Label(row, text=unit, bg=ctx.c("card"), fg=ctx.c("ink_faint"),
                             font=ctx.font("small"))
        self.unit.pack(side="left", padx=(ctx.s(3), 0), pady=(ctx.s(6), 0))
        self.label = self.winfo_children()[0]

    def update_value(self, value, unit=None, tone=None):
        self.val.configure(text=value)
        if unit is not None:
            self.unit.configure(text=unit)
        if tone is not None:
            self.val.configure(fg=self.ctx.c(tone))