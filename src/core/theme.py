# -*- coding: utf-8 -*-
"""设计令牌：直接提炼自 网页ui/qqa-ambient-template/assets/css/01-tokens.css。

原 CSS 是一套「柔光」视觉系统：冷白底、大圆角、极柔阴影、紫蓝渐变。
这里把 CSS 变量逐条翻译成 Python 常量，键名保持一一对应，方便以后改基线。
"""
from __future__ import annotations

LIGHT = {
    "bg": "#f5f5fa",
    "bg_2": "#eeeef6",
    "card": "#ffffff",
    "surface": "#ffffff",
    "glass": "#ffffff",

    "ink": "#14141c",
    "ink_soft": "#55556b",
    "ink_faint": "#64647a",   # 4.99（原 #9a9ab0 仅 2.75，弱化文字实际不可读）

    "violet": "#5b4bd6",      # 主色加深：card 上 5.32、accent_soft 上 5.32（原 #6c5ce7 仅 4.21）
    "violet_lt": "#8b7cf6",
    "blue": "#2563eb",        # 渐变尾端：白字 5.17（原 #4a9eff 仅 2.75，白字几乎看不清）
    "mint": "#00c2a8",
    "coral": "#ff7a59",
    "amber": "#f0a020",

    "grad_from": "#5b4bd6",   # 白字 6.14
    "grad_to": "#2563eb",     # 白字 5.17

    "line": "#ececf3",
    "line_2": "#f4f4f8",

    "shadow_s": "#f0f0f6",
    "shadow": "#e4e4ef",
    "shadow_l": "#dcdce9",

    "accent_soft": "#f0eeff",
    "chip_bg": "#f4f4f9",
    "grid": "#ececf4",

    # 状态色（规范：绿=已解锁 灰=不适用 黄=未解锁 红=无法判定）
    # 全部按 WCAG AA 重算：前景在 card / bg / chip_bg / 自身 soft 底上均 >= 4.5:1
    "ok": "#107a58",          # 4.61（原 #12b886 仅 2.55）
    "ok_soft": "#e6f8f1",
    "na": "#6b6b80",          # 4.50（原 #9a9ab0 仅 2.75）
    "na_soft": "#f0f0f4",
    "warn": "#9c5a00",        # 4.69（原 #e8a317 仅 2.17）
    "warn_soft": "#fdf4e2",
    "bad": "#c62828",         # 4.87（原 #e5484d 仅 3.91，大字才勉强）
    "bad_soft": "#fdeded",

    # ---- 交互态补充令牌（补齐 hover / active / focus / disabled 四态）
    "violet_hover": "#5b4bd6",
    "violet_active": "#4f3fc4",
    "violet_disabled": "#b7b0ef",
    "focus_ring": "#6c5ce7",
    "disabled_fg": "#9a9ab0",
    "disabled_bg": "#f7f7fb",
    "line_strong": "#d9d9e6",
}

DARK = {
    "bg": "#0a0b11",
    "bg_2": "#12141c",
    "card": "#161925",
    "surface": "#171a26",
    "glass": "#161925",

    "ink": "#e8eaf4",
    "ink_soft": "#b6bacd",
    "ink_faint": "#82879e",

    "violet": "#8b7cff",
    "violet_lt": "#a99cff",
    "blue": "#4aa3ff",
    "mint": "#2ee6c8",
    "coral": "#ff8f6b",
    "amber": "#f5b545",

    # 实心按钮两端：白字必须 >= 4.5（原 #8b7cff 3.27 / #4aa3ff 2.63 都不达标）
    "grad_from": "#6a58d8",   # 白字 5.25
    "grad_to": "#2f6fdd",     # 白字 4.73

    "line": "#252a3a",
    "line_2": "#1e2230",

    "shadow_s": "#0e1017",
    "shadow": "#0b0d13",
    "shadow_l": "#080a0f",

    "accent_soft": "#221f3d",
    "chip_bg": "#1d2130",
    "grid": "#252a3a",

    "ok": "#2ee6a8",
    "ok_soft": "#12312a",
    "na": "#82879e",
    "na_soft": "#1c2030",
    "warn": "#f5b545",
    "warn_soft": "#2e2612",
    "bad": "#ff6b6f",
    "bad_soft": "#331a1c",

    # ---- 交互态补充令牌（与浅色同构，深色下饱和度略降、亮度抬高）
    "violet_hover": "#a99cff",
    "violet_active": "#c0b6ff",
    "violet_disabled": "#4a4478",
    "focus_ring": "#8b7cff",
    "disabled_fg": "#5f6479",
    "disabled_bg": "#1a1d2a",
    "line_strong": "#333a4e",
}

THEMES = {"light": LIGHT, "dark": DARK}

# 字体层级（对应 CSS 的 h0/h1/h2/lead/body/mono）
FONTS = {
    "display": 30,
    "h1": 22,
    "h2": 16,
    "h3": 13,
    "body": 11,
    "small": 10,
    "mono": 10,
    "metric": 34,
    "metric_md": 22,
}

# 间距节奏：4pt 基准，8 的倍数为主（= CSS 的 0.25rem 阶梯）
PAD = {"page": 24, "card": 20, "gap": 16, "tight": 8, "xs": 4, "sm": 8, "md": 12, "lg": 20}
RADIUS = {"sm": 10, "card": 18, "pill": 999, "xs": 6, "lg": 24}

# 行高系数：中文字形占满 em，1.6 才不挤；英文 1.5 即可
LINE_HEIGHT = {"tight": 1.25, "normal": 1.6, "loose": 1.75}

# ---- 动效令牌 ----------------------------------------------------------
# 时长对齐 Material 3 的 duration scale；缓动统一用缓出曲线。
# Tk 没有 CSS 的 cubic-bezier，这里给出等价采样函数（见 ease_out_cubic）。
MOTION = {
    "instant": 80,      # 按下反馈
    "fast": 140,        # 悬停 / 焦点
    "base": 200,        # 展开、淡入
    "slow": 320,        # 卡片入场
    "slower": 480,      # 页面切换
    "spin": 900,        # 旋转一圈
}
EASE = {
    # 名称 -> (x1, y1, x2, y2)，与 CSS cubic-bezier 同参
    "standard": (0.2, 0.0, 0.0, 1.0),      # 入场：快出慢停
    "decelerate": (0.0, 0.0, 0.2, 1.0),
    "accelerate": (0.4, 0.0, 1.0, 1.0),
    "overshoot": (0.34, 1.56, 0.64, 1.0),   # 轻微回弹
    "linear": (0.0, 0.0, 1.0, 1.0),
}

# 音效令牌：时长(ms) / 音量(0..1) / 是否默认开启
SOUND = {
    "click": {"ms": 40, "gain": 0.18, "default_on": True},
    "success": {"ms": 120, "gain": 0.22, "default_on": True},
    "error": {"ms": 160, "gain": 0.20, "default_on": True},
}

# 窗口断点：与 CSS media query 等价，窗口宽度决定列数
BREAKPOINTS = {"sm": 720, "md": 1080, "lg": 1320, "xl": 1560}


def state_color(theme, state):
    """状态 → (前景, 背景)。"""
    return {
        "unlocked": (theme["ok"], theme["ok_soft"]),
        "locked": (theme["warn"], theme["warn_soft"]),
        "na": (theme["na"], theme["na_soft"]),
        # 待检测：还没有任何实测结论，用中性灰而不是「无法判定」的红
        "pending": (theme["ink_soft"], theme["chip_bg"]),
        "unknown": (theme["bad"], theme["bad_soft"]),
    }.get(state, (theme["ink_soft"], theme["chip_bg"]))


def mix(c1, c2, t):
    """线性混色，t=0 取 c1，t=1 取 c2。用于模拟渐变与柔和阴影。"""
    t = max(0.0, min(1.0, t))
    a = _hex(c1)
    b = _hex(c2)
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def _hex(c):
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def gradient_stops(c1, c2, n):
    return [mix(c1, c2, i / max(1, n - 1)) for i in range(n)]


# ---------------------------------------------------------------- 缓动曲线
def cubic_bezier(x1, y1, x2, y2, t, steps=16):
    """求 cubic-bezier(x1,y1,x2,y2) 在时间 t∈[0,1] 的输出值。

    CSS 里 t 对应 x，要的是 y，所以先二分把 x 解出来（Tk 没有原生贝塞尔）。
    steps 控制迭代次数：16 次二分后误差 < 1e-5，肉眼完全看不出。
    """
    t = max(0.0, min(1.0, t))

    def bez(a, b, u):
        # 端点固定的三次贝塞尔，控制点 a/b
        return 3 * (1 - u) ** 2 * u * a + 3 * (1 - u) * u ** 2 * b + u ** 3

    lo, hi = 0.0, 1.0
    for _ in range(steps):
        mid = (lo + hi) / 2.0
        if bez(x1, x2, mid) < t:
            lo = mid
        else:
            hi = mid
    return bez(y1, y2, (lo + hi) / 2.0)


def ease(name, t):
    """按名称取缓动值。未知名称退回线性。"""
    x1, y1, x2, y2 = EASE.get(name, EASE["linear"])
    return cubic_bezier(x1, y1, x2, y2, t)


def duration(name):
    """按名称取动效时长（毫秒）。"""
    return MOTION.get(name, MOTION["base"])


def motion_scale():
    """尊重系统的「减少动态效果」设置（= prefers-reduced-motion）。

    Windows: 设置 → 辅助功能 → 视觉效果 → 动画效果 → 关闭
    关闭时返回 0.0（所有时长压到 0），程序退化为即时切换。
    """
    try:
        import ctypes
        # SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(enabled), 0)
        if ok and not enabled.value:
            return 0.0
    except Exception:
        pass
    return 1.0
