#!/usr/bin/env python3
"""生成 Mines-Highlight 的 Arsenal 图标与选项预览图。仅构建时使用。

选项图优先用 `images/model_<key>.png`（tools/render_tiles.py 渲的倾斜 3D 图），
没有就退回画太阳，保证冷启动也能构建。
"""

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
FONT = "C:/Windows/Fonts/msyhbd.ttc"
FACTION = ROOT / "tools" / "faction"
BG = (15, 26, 23)
FG = (238, 246, 241)
MUTED = (150, 176, 165)
MAIN = (208, 238, 255)
CORE = (246, 253, 255)

COLORS = {
    "contact": (77, 163, 255),
    "incendiary": (255, 122, 61),
    "tank": (255, 210, 74),
    "gas": (95, 208, 138),
    "bot": (232, 64, 64),
    "bug": (255, 186, 20),
    "thermite": (255, 122, 40),
    "decoy": (255, 200, 70),
    "caltrops": (190, 145, 255),
}
# 图上只留战备编号：管理器本来就会显示选项名，重复写一遍中文名太挤。
# 机器人雷和孢囊雷没有编号，改画阵营标志。
CODES = {
    "contact": "MD-6",
    "incendiary": "MD-I4",
    "tank": "MD-17",
    "gas": "MD-8",
    "bot": None,      # 没有战备编号，用阵营标志代替文字
    "bug": None,
    "thermite": "G/40-K",
    "decoy": "TM-1",
    "caltrops": "G-11",
}

BORDER = (48, 78, 68)
MODEL_SCALE = 0.74
MODEL_CY = 0.415

# 封面：中间机械雷 + 光柱，两侧各一列 4 颗，只中间有光柱
THUMB_LEFT = ("contact", "incendiary", "tank", "gas")
THUMB_RIGHT = ("thermite", "decoy", "caltrops", "bug")
THUMB_X = (84, 428)
THUMB_ROWS = (88, 200, 312, 424)
THUMB_SIDE = 108
THUMB_CENTER = 216
SEPARATOR = 40

LOGO_FILES = {
    "bot": FACTION / "faction_logo_bot.png",
    "bug": FACTION / "faction_logo_bug.png",
}

# 标志用阵营代表色：机器人红、终结族黄，与本体色无关
LOGO_COLORS = {
    "bot": (232, 64, 64),
    "bug": (255, 186, 20),
}


def font(size):
    return ImageFont.truetype(FONT, size, index=0)


def centered(draw, box, text, f, fill):
    x0, y0, x1, y1 = box
    bb = draw.textbbox((0, 0), text, font=f)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    draw.text((x0 + (x1 - x0 - w) / 2 - bb[0], y0 + (y1 - y0 - h) / 2 - bb[1]),
              text, font=f, fill=fill)


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def beam_column(draw, x, y_top, y_base, width, color):
    """竖直光柱：由外向内一层层加亮，最里面一条近白的芯。"""
    for i in range(10):
        ratio = 7.0 - i * 0.72
        strength = 0.015 + i * 0.026
        draw.line((x, y_base, x, y_top), fill=mix(BG, color, strength),
                  width=max(2, int(width * ratio)))
    draw.line((x, y_base, x, y_top + width * 0.6), fill=color, width=max(3, width // 2))
    draw.line((x, y_base, x, y_top + width), fill=mix(color, CORE, 0.8), width=max(2, width // 4))


def beam(img, x, y_top, y_base, radius, color):
    """半透明圆柱光柱：横向按圆柱面的朝向给明暗，顶上留一个椭圆口，外面一层柔光。

    柱体是一次成像的，所以底下的雷要后贴，柱脚自然被挡住。
    """
    w, h = img.size
    xs = np.arange(w, dtype=np.float32)
    t = (xs - x) / float(radius)
    prof = np.sqrt(np.clip(1.0 - t * t, 0.0, 1.0))          # 正对观察者处最亮
    col = np.array(color, dtype=np.float32) / 255.0
    core = prof ** 4

    def strip(shade_alpha, shade_rgb, y0, y1):
        arr = np.zeros((h, w, 4), np.float32)
        arr[y0:y1, :, :3] = shade_rgb[None, :, :]
        arr[y0:y1, :, 3] = shade_alpha[None, :]
        return Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "RGBA")

    glow = np.exp(-np.clip(t / 2.4, -3.0, 3.0) ** 2) * 0.16
    img.alpha_composite(strip(glow, np.broadcast_to(col, (w, 3)), y_top, y_base))

    body_alpha = np.where(np.abs(t) <= 1.0, 0.14 + 0.26 * prof, 0.0)
    body_rgb = col[None, :] * (0.86 + 0.14 * prof[:, None]) + (1.0 - col)[None, :] * (0.7 * core)[:, None]
    img.alpha_composite(strip(body_alpha, body_rgb, y_top, y_base))

    # 顶口：俯视看圆柱，口是个椭圆
    ry = radius * 0.26
    ys = np.arange(int(y_top - ry), int(y_top + ry) + 1)
    cap_rgb = col * 0.6 + (1.0 - col) * 0.55
    for y in ys:
        k = (y - y_top) / ry
        if abs(k) > 1.0:
            continue
        half = radius * math.sqrt(max(0.0, 1.0 - k * k))
        if half < 0.6:
            continue
        a = 0.22 + 0.26 * (1.0 - abs(k))
        d = ImageDraw.Draw(img)
        d.line((x - half, y, x + half, y), fill=tuple(int(c * 255) for c in cap_rgb) + (int(a * 255),))


def logo(key, size, color):
    """阵营标志：白色剪影按 alpha 上成本体色，裁到内容框后等比缩到 size。"""
    src = Image.open(LOGO_FILES[key]).convert("RGBA")
    box = src.getchannel("A").getbbox()
    if box:
        src = src.crop(box)
    art = Image.new("RGBA", src.size, color + (255,))
    art.putalpha(src.getchannel("A"))
    scale = size / max(art.size)
    return art.resize((max(1, round(art.size[0] * scale)),
                       max(1, round(art.size[1] * scale))), Image.LANCZOS)


def paste_model(img, key, cx, cy, side):
    """把渲染好的倾斜 3D 图贴到画布上；缺图返回 False。"""
    path = IMAGES / ("model_%s.png" % key)
    if not path.is_file():
        return False
    art = Image.open(path).convert("RGBA").resize((side, side), Image.LANCZOS)
    img.alpha_composite(art, (cx - side // 2, cy - side // 2))
    return True


def sun(draw, cx, cy, radius, color, beam=None):
    """上半边太阳：七道光芒 + 地平线 + 半圆。

    beam=(顶端 y, 宽度) 时中间那道光芒改画成光柱：先画几层由底色渐亮的柔光，再本色，最后
    一条近白的芯；光柱画在半圆之前，下段被半圆盖住，看起来是从穹顶后面升起来的。
    """
    dark = tuple(int(c * 0.45) for c in color)
    width = max(3, int(radius * 0.11))
    for k in range(7):
        if beam and k == 3:
            continue
        a = math.pi + k * math.pi / 6
        draw.line((cx + math.cos(a) * radius * 1.22, cy + math.sin(a) * radius * 1.22,
                   cx + math.cos(a) * radius * 1.70, cy + math.sin(a) * radius * 1.70),
                  fill=dark, width=width)
    draw.line((cx - radius * 1.90, cy, cx + radius * 1.90, cy), fill=dark, width=width)

    if beam:
        top, beam_width = beam
        beam_column(draw, cx, top, cy, beam_width, color)

    draw.pieslice((cx - radius, cy - radius, cx + radius, cy + radius), 180, 360, fill=dark)
    r2 = radius * 0.94
    draw.pieslice((cx - r2, cy - r2, cx + r2, cy + r2), 180, 360, fill=color)


def glow(img, cx, cy, radius, color):
    """模型背后的柔光，让 3D 图不至于浮在纯色底上。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    steps = 14
    for i in range(steps, 0, -1):
        r = radius * i / steps
        alpha = int(4 + 26 * (1 - i / steps) ** 1.3)
        d.ellipse((cx - r, cy - r * 0.92, cx + r, cy + r * 0.92), fill=color + (alpha,))
    img.alpha_composite(layer)


def thumbnail():
    img = Image.new("RGBA", (512, 512), BG + (255,))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((12, 12, 500, 500), radius=36, outline=BORDER, width=4)

    # 中间那根是机械雷的光柱，画在模型之前，下段被雷本体挡住
    if not (IMAGES / "model_bot.png").is_file():
        sun(d, 256, 330, 78, MAIN, beam=(44, 8))
    else:
        beam(img, 256, 40, 320, 12, COLORS["bot"])
        paste_model(img, "bot", 256, 320, THUMB_CENTER)

    for x, keys in ((THUMB_X[0], THUMB_LEFT), (THUMB_X[1], THUMB_RIGHT)):
        for cy, key in zip(THUMB_ROWS, keys):
            paste_model(img, key, x, cy, THUMB_SIDE)

    img.convert("RGB").save(ROOT / "thumbnail.png")


def option(key, size=256, frame=True):
    img = Image.new("RGBA", (size, size), BG + (255,))
    d = ImageDraw.Draw(img)
    if frame:
        d.rounded_rectangle((6, 6, size - 6, size - 6), radius=20, outline=BORDER, width=3)

    model = IMAGES / ("model_%s.png" % key)
    cy = int(size * MODEL_CY)
    if model.is_file():
        glow(img, size // 2, cy, int(size * 0.44), COLORS[key])
        art = Image.open(model).convert("RGBA")
        side = int(size * MODEL_SCALE)
        art = art.resize((side, side), Image.LANCZOS)
        img.alpha_composite(art, (size // 2 - side // 2, cy - side // 2))
        d = ImageDraw.Draw(img)
    else:
        sun(d, size // 2, cy, int(size * 0.19), COLORS[key])

    band = (10, int(size * 0.765), size - 10, int(size * 0.905))
    code = CODES[key]
    if code:
        centered(d, band, code, font(28), COLORS[key])
    else:
        mark = logo(key, int(size * 0.19), LOGO_COLORS[key])
        img.alpha_composite(mark, (size // 2 - mark.size[0] // 2,
                                   band[1] + (band[3] - band[1] - mark.size[1]) // 2))
    return img.convert("RGB")


def preview():
    """双行 5+4：上行四颗战备雷 + 两枚敌方雷，下行是四个 MD。只留一条横线分隔。"""
    top = ("thermite", "decoy", "caltrops", "bot", "bug")
    rows = (top, THUMB_LEFT)
    tile, gap, margin = 256, 0, 24
    width = tile * len(top) + margin * 2
    height = tile * 2 + margin * 2 + SEPARATOR
    img = Image.new("RGB", (width, height), BG)
    for r, keys in enumerate(rows):
        left = margin + (tile * len(top) - tile * len(keys)) // 2
        y = margin + r * (tile + SEPARATOR)
        for i, key in enumerate(keys):
            img.paste(option(key, tile, frame=False), (left + i * tile, y))
    d = ImageDraw.Draw(img)
    line_y = margin + tile + SEPARATOR // 2
    d.line((margin, line_y, width - margin, line_y), fill=BORDER, width=2)
    img.save(ROOT / "preview.png", optimize=True)


def main():
    IMAGES.mkdir(parents=True, exist_ok=True)
    for p in IMAGES.glob("*.png"):
        if p.stem not in COLORS and not p.stem.startswith("model_"):
            p.unlink()
            print("removed stale icon:", p.name)
    thumbnail()
    for key in COLORS:
        option(key).save(IMAGES / (key + ".png"))
    preview()
    print("icons written:", ROOT / "thumbnail.png", *(str(IMAGES / (k + ".png")) for k in COLORS))


if __name__ == "__main__":
    main()
