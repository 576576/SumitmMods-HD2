#!/usr/bin/env python3
"""生成 thumbnail.png（封面）：近黑底 + 警戒黄标题，供 Arsenal 列表显示。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "thumbnail.png"
SIZE = 512

BG = (12, 14, 18)
BG2 = (22, 26, 33)
GOLD = (255, 213, 0)
BONE = (238, 234, 224)
MUTED = (150, 154, 160)
DIM = (70, 76, 86)

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"


def font(path, size):
    return ImageFont.truetype(path, size)


def main():
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)

    # 斜向底纹：每 24px 一条更亮的细带，做出工业面板感
    for x in range(-SIZE, SIZE * 2, 24):
        d.line([(x, SIZE), (x + SIZE, 0)], fill=BG2, width=6)

    # 左上角标签
    d.rectangle([36, 40, 44, 92], fill=GOLD)
    d.text((60, 44), "HELLDIVERS 2", font=font(FONT_BOLD, 22), fill=MUTED)

    # 中央盾形
    cx, cy, r = SIZE // 2, 190, 62
    shield = [
        (cx - r, cy - r + 8), (cx, cy - r), (cx + r, cy - r + 8),
        (cx + r, cy + 18), (cx, cy + r + 6), (cx - r, cy + 18),
    ]
    d.polygon(shield, outline=GOLD, width=6)
    d.line([(cx - 26, cy + 2), (cx - 6, cy + 22), (cx + 28, cy - 22)], fill=GOLD, width=10)

    # 标题：字重与字号都放大，缩略图上才看得清
    title = "护甲多被动"
    f_title = font(FONT_BOLD, 86)
    w = d.textlength(title, font=f_title)
    d.text(((SIZE - w) / 2, 300), title, font=f_title, fill=GOLD)

    sub = "可自选 32 项额外被动"
    f_sub = font(FONT_REG, 30)
    w = d.textlength(sub, font=f_sub)
    d.text(((SIZE - w) / 2, 402), sub, font=f_sub, fill=BONE)

    # 底部版本条
    d.rectangle([36, SIZE - 62, SIZE - 36, SIZE - 58], fill=DIM)
    d.text((36, SIZE - 50), "MDL3 版", font=font(FONT_BOLD, 24), fill=MUTED)

    img.save(OUT, "PNG", optimize=True)
    print("wrote", OUT, img.size, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
