#!/usr/bin/env python3
"""把战备图标合成一张 4x4 图集，供 core 里的 icons 表按 UV 取用。

图标资源（content/ui/shared/stratagem/icon_strat_hud_*）是 256x256 RGB：
红通道是炮塔本体、绿通道是符号层，两者互斥。合成后本体染成绿色、符号层染成
浅灰，alpha 取两层并集，缩到 128 一格。

输入目录默认 build/icons（用 filediver 导出），只在本机生成素材时用；
产物 images/icons_atlas.png 入库，构建时由 patch_atlas.py 写回归档。
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT.parents[1] / "build" / "icons" / "content" / "ui" / "shared" / "stratagem"
# 全量导出目录，含图标名表里没有的新债券图标（E/AT-12 等）。
EXTRA = ROOT.parents[1] / "build" / "_strat" / "content" / "ui" / "shared" / "stratagem"
# 没有纹理版的图标（武装补给舱）由 make_icons.py 光栅化，格式相同。
LOCAL = ROOT / "images" / "icons"
TARGET = ROOT / "images" / "icons_atlas.png"

CELL = 128
GRID = 4
BODY = (102, 148, 80)
SYMBOL = (237, 237, 237)
# 补给舱图标两色：舱体白、炮塔绿，和预览里的那一条弹药显示一致。
PALETTE = {"ammo_pod": ((237, 237, 237), (60, 214, 92))}

# 材质 icons_depth 采样的是这张图集，除了 12 个图标格，还必须有这两格：
#   (0,3) 四角括号 —— 图标外框； (3,3) 纯白 —— 弹药条的取样点。
# 缺了它们 draw 仍然提交，但条和框都会变成全透明。
FRAME_CELL = (0, 3)
WHITE_CELL = (3, 3)
CORNER_INSET = 6      # 括号描边中心线到格边的距离
CORNER_ARM = 19       # 括号两条边的长度（含拐角）
CORNER_RADIUS = 7     # 拐角圆弧半径
CORNER_WIDTH = 2
SS = 4                # 绘制时超采样倍数，缩小必须用 BOX，LANCZOS 会在硬边外留一圈振铃

# (列, 行, 图标名)；与 core/e02b68d623c30acd.lua 的 visual_profile.icons 一一对应。
CELLS = (
    (0, 0, "defensive_turrets_manned_machinegun"),
    (1, 0, "defensive_turrets_manned_grenade"),
    (2, 0, "defensive_turrets_machinegun"),
    (3, 0, "defensive_turrets_gatling"),
    (0, 1, "defensive_turrets_mortar"),
    (1, 1, "defensive_turrets_mortar_staticfield"),
    (2, 1, "defensive_turrets_mortar_gas"),
    (3, 1, "defensive_turrets_rocket"),
    (0, 2, "defensive_turrets_autocannon"),
    (1, 2, "flamethrower_turret"),
    (2, 2, "laser_cannon_turret"),
    (3, 2, "defensive_emplacements_teslatower"),
    (1, 3, "anti_tank_emplacement"),
    # 这个在游戏里没有纹理版，靠 xaml / svg 光栅化。
    (2, 3, "ammo_pod"),
)


def composed(name):
    """图标 → RGBA，本体绿、符号浅灰、alpha 为两层并集。"""
    path = LOCAL / ("%s.png" % name)
    if not path.is_file():
        path = SOURCE / ("icon_strat_hud_%s.png" % name)
    if not path.is_file():
        path = EXTRA / ("icon_strat_hud_%s.png" % name)
    if not path.is_file():
        raise SystemExit("缺少图标 %s（先用 filediver 导出到 build/icons 或 build/_strat）" % path)
    src = Image.open(path).convert("RGB")
    r, g, _ = src.split()
    body_ink, mark_ink = PALETTE.get(name, (BODY, SYMBOL))
    body = Image.new("RGBA", src.size, body_ink + (0,))
    body.putalpha(r)
    mark = Image.new("RGBA", src.size, mark_ink + (0,))
    mark.putalpha(g)
    out = Image.alpha_composite(body, mark)
    # 预乘后再缩放，边缘不会发黑
    return out.convert("RGBa").resize((CELL, CELL), Image.LANCZOS).convert("RGBA")


def frame_cell():
    """四角开口括号：一段直边 + 拐角圆弧 + 一段直边。"""
    size = CELL * SS
    big = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(big)
    i, arm, rad = CORNER_INSET * SS, CORNER_ARM * SS, CORNER_RADIUS * SS
    width = CORNER_WIDTH * SS
    ink = (255, 255, 255, 255)
    lo, hi = i, size - 1 - i
    corners = (
        (lo, lo, 1, 1, 180, 270),
        (hi, lo, -1, 1, 270, 360),
        (hi, hi, -1, -1, 0, 90),
        (lo, hi, 1, -1, 90, 180),
    )
    for x, y, sx, sy, start, end in corners:
        cx, cy = x + sx * rad, y + sy * rad
        draw.line([(cx, y), (x + sx * arm, y)], fill=ink, width=width)
        draw.line([(x, cy), (x, y + sy * arm)], fill=ink, width=width)
        draw.arc([cx - rad, cy - rad, cx + rad, cy + rad], start, end, fill=ink, width=width)
    return big.resize((CELL, CELL), Image.BOX)


def white_cell():
    return Image.new("RGBA", (CELL, CELL), (255, 255, 255, 255))


def main():
    atlas = Image.new("RGBA", (CELL * GRID, CELL * GRID), (0, 0, 0, 0))
    for col, row, name in CELLS:
        atlas.paste(composed(name), (col * CELL, row * CELL))
    for col, row in (FRAME_CELL, WHITE_CELL):
        if any(c == col and r == row for c, r, _ in CELLS):
            raise SystemExit("装饰格 (%d,%d) 与图标格重叠" % (col, row))
    atlas.paste(frame_cell(), (FRAME_CELL[0] * CELL, FRAME_CELL[1] * CELL))
    atlas.paste(white_cell(), (WHITE_CELL[0] * CELL, WHITE_CELL[1] * CELL))
    for col, row, name in CELLS:
        box = atlas.crop((col * CELL, row * CELL, col * CELL + CELL, row * CELL + CELL)).getbbox()
        if not box or box[0] < 2 or box[1] < 2 or box[2] > CELL - 2 or box[3] > CELL - 2:
            raise SystemExit("图标 %s 在格内被裁切：%s" % (name, box))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(TARGET)
    print("atlas:", TARGET, atlas.size)


if __name__ == "__main__":
    sys.exit(main())
