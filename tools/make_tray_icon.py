#!/usr/bin/env python3
"""make_tray_icon.py - 由 docs/logo.svg 的几何参数生成图标

不依赖任何 SVG 渲染库（Pillow 无法直接渲染 SVG），这里按 logo.svg 的坐标
用 Pillow 重绘，输出多尺寸 .ico 与 .png 到仓库 assets/ 目录：
  assets/trae2api-cn.ico / .png        运行中（绿色）
  assets/trae2api-cn-off.ico / .png    已停止/异常（红色）

用法：
  .venv\\Scripts\\python.exe tools\\make_tray_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

F = 16                 # 相对 64 单位 viewBox 的超采样倍数（抗锯齿）
BASE = 64 * F          # 1024 x 1024 绘制，再缩放
TARGET = 256           # 输出 PNG 尺寸

BG = (11, 16, 13, 255)         # #0b100d
GREEN = (34, 197, 94, 255)     # #22c55e
DOT_GREEN = (74, 222, 128, 255)  # #4ade80
RED = (210, 75, 75, 255)
DOT_RED = (248, 113, 113, 255)


def _s(v: float) -> float:
    return v * F


def draw_logo(stroke, dot) -> Image.Image:
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # <rect x=3 y=3 width=58 height=58 rx=14 stroke-width=4>
    d.rounded_rectangle(
        [_s(3), _s(3), _s(61), _s(61)],
        radius=_s(14),
        fill=BG,
        outline=stroke,
        width=_s(4),
    )

    lw = _s(6)      # stroke-width 6
    r = lw // 2     # 圆头端点

    def line(p1, p2):
        d.line(
            [(_s(p1[0]), _s(p1[1])), (_s(p2[0]), _s(p2[1]))],
            fill=stroke, width=lw, joint="curve",
        )
        for x, y in (p1, p2):
            d.ellipse([_s(x) - r, _s(y) - r, _s(x) + r, _s(y) + r], fill=stroke)

    # M17 22 h27
    line((17, 22), (44, 22))
    # M28 22 v24
    line((28, 22), (28, 46))

    # M39 14 l9 8 -9 8  折线
    d.line(
        [(_s(39), _s(14)), (_s(48), _s(22)), (_s(39), _s(30))],
        fill=stroke, width=lw, joint="curve",
    )
    for x, y in ((39, 14), (48, 22), (39, 30)):
        d.ellipse([_s(x) - r, _s(y) - r, _s(x) + r, _s(y) + r], fill=stroke)

    # <circle cx=28 cy=47 r=4.5>
    cr = _s(4.5)
    d.ellipse([_s(28) - cr, _s(47) - cr, _s(28) + cr, _s(47) + cr], fill=dot)

    return img.resize((TARGET, TARGET), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    variants = (
        ("trae2api-cn", GREEN, DOT_GREEN),
        ("trae2api-cn-off", RED, DOT_RED),
    )
    for name, stroke, dot in variants:
        base = draw_logo(stroke, dot)
        png = OUT / f"{name}.png"
        ico = OUT / f"{name}.ico"
        base.save(png)
        base.save(
            ico,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
        print("wrote", png)
        print("wrote", ico)


if __name__ == "__main__":
    main()
