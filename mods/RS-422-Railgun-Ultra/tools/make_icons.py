#!/usr/bin/env python3
"""生成 RS-422 Railgun Ultra 的封面与四个选项图。仅构建时使用。

选项图用战备图标原稿：`assets/glyphs/<key>.png` 是白字 alpha 剪影，压到深色卡片上，
按选项配色描边并在底部写中文名。封面是单行标题「RS-422 +」+ 四个无框剪影 + 武器正视图
（`images/model_railgun.png`，由 render_model.py 生成；没有就只画上面几行）。
"""

from PIL import Image, ImageDraw, ImageFont

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLYPHS = ROOT / "assets" / "glyphs"
IMAGES = ROOT / "images"
FONT = "C:/Windows/Fonts/msyhbd.ttc"

BG = (15, 26, 23)
FG = (240, 247, 244)
BORDER = (48, 78, 68)

OPTIONS = [
    ("precision", "轨道精准", (255, 210, 74)),
    ("gas", "轨道毒气", (95, 208, 138)),
    ("railcannon", "轨道炮", (208, 238, 255)),
    ("ems", "轨道电磁", (77, 163, 255)),
]


def font(size):
    return ImageFont.truetype(FONT, size, index=0)


def centered(draw, box, text, f, fill):
    x0, y0, x1, y1 = box
    bb = draw.textbbox((0, 0), text, font=f)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    draw.text((x0 + (x1 - x0 - w) / 2 - bb[0], y0 + (y1 - y0 - h) / 2 - bb[1]),
              text, font=f, fill=fill)


def glyph(key, height):
    """白字剪影裁到内容框，等比缩到指定高度。"""
    src = Image.open(GLYPHS / (key + ".png")).convert("RGBA")
    box = src.getchannel("A").getbbox()
    if box:
        src = src.crop(box)
    scale = height / src.size[1]
    return src.resize((max(1, round(src.size[0] * scale)), height), Image.LANCZOS)


def tinted(key, height, color):
    """剪影按 alpha 上成本体色。"""
    art = glyph(key, height)
    solid = Image.new("RGBA", art.size, color + (255,))
    solid.putalpha(art.getchannel("A"))
    return solid


def glow(img, cx, cy, radius, color):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    steps = 14
    for i in range(steps, 0, -1):
        r = radius * i / steps
        alpha = int(5 + 28 * (1 - i / steps) ** 1.3)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (alpha,))
    img.alpha_composite(layer)


def card(key, size, color, label=None, pad=6, radius=20, frame_width=3):
    img = Image.new("RGBA", (size, size), BG + (255,))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((pad, pad, size - pad, size - pad), radius=radius,
                        outline=color, width=frame_width)
    cx = size // 2
    cy = int(size * (0.42 if label else 0.5))
    art = glyph(key, int(size * (0.50 if label else 0.58)))
    glow(img, cx, cy, int(size * 0.40), color)
    img.alpha_composite(art, (cx - art.size[0] // 2, cy - art.size[1] // 2))
    if label:
        d = ImageDraw.Draw(img)
        centered(d, (0, int(size * 0.72), size, int(size * 0.90)),
                 label, font(int(size * 0.12)), color)
    return img


def option(key, label, color, size=256):
    return card(key, size, color, label=label).convert("RGB")


def thumbnail(size=512):
    img = Image.new("RGBA", (size, size), BG + (255,))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((12, 12, size - 12, size - 12), radius=36, outline=BORDER, width=4)

    # 标题单行：按可用宽度自动选字号，不用手工试
    title = "RS-422 +"
    room = size - 96
    pts = 88
    while pts > 26 and d.textlength(title, font=font(pts)) > room:
        pts -= 2
    centered(d, (0, 38, size, 132), title, font(pts), FG)

    # 四个战备图标：去掉卡片外框，只留剪影，按选项配色上色
    row_h = 66
    step = 84.0
    left = (size - step * (len(OPTIONS) - 1)) / 2
    for i, (key, _, color) in enumerate(OPTIONS):
        art = tinted(key, row_h, color)
        cx = left + i * step
        img.alpha_composite(art, (round(cx - art.size[0] / 2), 146))

    # 武器渲染图：没有渲过就只留上面几行，构建不依赖游戏目录
    model = IMAGES / "model_railgun.png"
    if model.is_file():
        art = Image.open(model).convert("RGBA")
        width = size - 108
        scale = width / art.size[0]
        art = art.resize((max(1, round(art.size[0] * scale)), max(1, round(art.size[1] * scale))),
                         Image.LANCZOS)
        cx, cy = size // 2, 350
        glow(img, cx, cy, int(size * 0.36), (110, 145, 190))
        img.alpha_composite(art, (cx - art.size[0] // 2, cy - art.size[1] // 2))
    return img.convert("RGB")


def main():
    missing = [k for k, _, _ in OPTIONS if not (GLYPHS / (k + ".png")).is_file()]
    if missing:
        raise SystemExit("缺少图标原稿：%s" % ", ".join(missing))
    IMAGES.mkdir(parents=True, exist_ok=True)
    thumbnail().save(ROOT / "thumbnail.png", optimize=True)
    for key, label, color in OPTIONS:
        option(key, label, color).save(IMAGES / (key + ".png"), optimize=True)
    print("icons:", ROOT / "thumbnail.png",
          *[str(IMAGES / (k + ".png")) for k, _, _ in OPTIONS])


if __name__ == "__main__":
    main()
