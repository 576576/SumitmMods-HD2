#!/usr/bin/env python3
"""生成两种没有纹理版的战备图标：榴弹兵墙与武装重新补给舱增强。

原版只有一部分战备带 icon_strat_hud_* 纹理，较新的条目（E/GL-21、武装补给舱
增强）的图标只存在于 xaml 的矢量 Path Data 里，游戏 UI 也是直接画这些路径。
这里把 xaml / svg 导出来，用 svgicon 光栅化成与纹理同格式的 256 方图：
红通道本体、绿通道符号、蓝通道为 0。

产物 images/icons/ 入库；preview 与图集都从这里取，游戏内显示才能用同一份素材。
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parents[1]
OUT = ROOT / "images" / "icons"
BUILD = REPO / "build"

sys.path.insert(0, str(REPO / "toolchain" / "common"))
sys.path.insert(0, str(ROOT / "tools"))
import paths  # noqa: E402
import svgicon  # noqa: E402

XAML_DIR = BUILD / "_icons_xaml" / "content" / "ui" / "shared" / "resources" / "generated_icons"
SVG_DIR = BUILD / "_icons_svg" / "content" / "ui" / "shared" / "resources" / "generated_icons"

# 输出名 → 来源。xaml 里的键是 DataTemplate.Icon.<键>。
# layers 给定时是 (本体下标, 符号下标)：补给舱的 svg 第一层是六边形底板，
# 不要；舱体（舱盖、两侧弹药箱、主体）进本体，右侧那门带枪管的炮塔进符号层。
JOBS = (
    ("wall", "xaml", "StratagemType.CoverGrenadeLauncher", None),
    ("ammo_pod", "svg",
     "booster_icons.svgs/DataTemplate.Icon.Booster.DefensiveAmmoPod.svg",
     ((1, 2, 3, 4), (5,))),
)


def extract(game_dir):
    if not (XAML_DIR / "stratagem_icons.xaml").is_file():
        subprocess.run([str(paths.FILEDIVER), "--gamedir", str(game_dir),
                        "-o", str(BUILD / "_icons_xaml"), "-i", "*stratagem_icons*",
                        "--xaml-format", "xaml"], check=True)
    if not (SVG_DIR / "booster_icons.svgs").is_dir():
        subprocess.run([str(paths.FILEDIVER), "--gamedir", str(game_dir),
                        "-o", str(BUILD / "_icons_svg"), "-i", "*booster_icons*"],
                       check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", default="E:/SteamLibrary/steamapps/common/Helldivers 2")
    args = parser.parse_args()

    extract(args.game_dir)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, kind, key, layers in JOBS:
        if kind == "xaml":
            text = (XAML_DIR / "stratagem_icons.xaml").read_text(encoding="utf-8")
            paths_ = svgicon.paths_from_xaml(text, key)
        else:
            text = (SVG_DIR / key).read_text(encoding="utf-8")
            paths_ = svgicon.paths_from_svg(text)
        if layers:
            icon = svgicon.to_icon_split(paths_, layers[0], layers[1])
        else:
            icon = svgicon.to_icon(paths_)
        icon.save(OUT / (name + ".png"))
        print("icon:", OUT / (name + ".png"), len(paths_), "paths")


if __name__ == "__main__":
    sys.exit(main())
