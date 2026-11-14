#!/usr/bin/env python3
"""把炮塔与阵地渲染成透明底贴图，拼成 mod 封面与五个分组图标。

一次性素材工具，不进构建流程。依赖：
  - 游戏目录、toolchain/rawdump.exe（导出 unit 的 main/gpu）
  - toolchain/third_party/filediver-cli.exe（导出原版战备图标纹理）
  - toolchain/common/render_tiles.py 的光栅器（同一套渲染管线）

用法：python tools/render_turrets.py [--game-dir ...]
"""

import argparse
import math
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parents[1]
IMAGES = ROOT / "images"

sys.path.insert(0, str(REPO / "toolchain" / "common"))
import paths  # noqa: E402
import bug_patch as bp  # noqa: E402
import mine_patch as mp  # noqa: E402
import render_tiles as rt  # noqa: E402

RAWPUMP = paths.RAWDUMP
FILEDIVER = paths.FILEDIVER
WORK = REPO / "build" / "_turrets"
STRAT_ROOT = REPO / "build" / "_strat"
STRAT_DIR = STRAT_ROOT / "content" / "ui" / "shared" / "stratagem"
STRAT_ARCHIVE = "6a3eecfddce28fe8"
STRAT_PATH = "content/ui/shared/stratagem/"
TYPE_UNIT = "0xE0A48D0BE9A7453F"
PREFIX = "content/fac_helldivers/hellpod/"

CLEAR = (0, 0, 0, 0)
# 封面与选项图共用同一套底色，和游戏里的暗蓝灰 UI 一致。
BG = (13, 19, 27)
BORDER = (42, 60, 82)

Spec = namedtuple("Spec", "path shadow light clip")

# 暗部 / 亮部两端：渲染出来是灰阶，按这两端做渐变映射上色。
# 同一分组用相邻色相，组间拉开，保证一眼能分开。
SPECS = {
    "mg": Spec(PREFIX + "turret_machinegun_gpmg/turret_machinegun_gpmg",
               (0.05, 0.22, 0.12), (0.46, 0.96, 0.66), None),
    "gatling": Spec(PREFIX + "turret/turret",
                    (0.36, 0.15, 0.03), (1.00, 0.64, 0.26), None),
    "autocannon": Spec(PREFIX + "aa_turret/aa_turret",
                       (0.36, 0.30, 0.05), (1.00, 0.97, 0.62), None),
    "rocket": Spec(PREFIX + "rocket_turret/rocket_turret",
                   (0.33, 0.11, 0.04), (1.00, 0.50, 0.30), None),
    "flame": Spec(PREFIX + "flamethrower_turret/flamethrower_turret",
                  (0.40, 0.07, 0.02), (1.00, 0.36, 0.08), None),
    "mortar": Spec(PREFIX + "mortar_turret/mortar_turret",
                   (0.24, 0.27, 0.31), (0.90, 0.93, 0.97), None),
    "laser": Spec(PREFIX + "laser_cannon_turret/laser_cannon_turret",
                  (0.34, 0.22, 0.02), (1.00, 0.78, 0.14), None),
    "tesla": Spec(PREFIX + "tesla_turret/tesla_turret",
                  (0.06, 0.30, 0.44), (0.50, 0.94, 1.00), None),
    "hmg": Spec(PREFIX + "manned_turret/manned_turret",
                (0.18, 0.27, 0.05), (0.70, 0.94, 0.34), None),
    "antitank": Spec(PREFIX + "antitank_emplacement/antitank_emplacement",
                     (0.04, 0.27, 0.25), (0.40, 0.94, 0.86), None),
    # 可展开阵地的侧翼撑开后主体只剩一小块，只保留中间那一段。
    "wall": Spec(PREFIX + "expandable_cover/expandable_cover",
                 (0.22, 0.28, 0.36), (0.80, 0.88, 0.98), 0.12),
    # 补给类不是「炮塔」：本体是补给舱与补给车，炮塔挂上去另上色。
    "crate": Spec("content/fac_helldivers/hellpod/ammo_rack/ammo_rack",
                  (0.20, 0.24, 0.31), (0.86, 0.92, 1.00), None),
    "car": Spec("content/fac_helldivers/vehicles/frv_supply/frv_supply",
                (0.19, 0.23, 0.30), (0.84, 0.91, 1.00), None),
}

# 榴弹兵墙的发射器是独立 unit：主 unit 里只有墙体和 attach_gun 座。
# 墙的专属包（packages/generated/loadout/expandable_cover_grenade_launcher）里只装了
# 墙、support_weapons/grenade_launcher、它的弹链盒、40mm 抛壳和红点瞄具，
# 没有第二套"墙专用发射器"模型 —— 墙上用的就是玩家那把 GL-21。
GL_UNIT = ("content/fac_helldivers/equipment/support_weapons/"
           "grenade_launcher/grenade_launcher")
# 弹链盒（方箱 + 拉直的一排 40mm）只有这个包有，玩家武器包不带。
MAG_UNIT = ("content/fac_helldivers/equipment/attachment/magazine/"
            "grenade_launcher_ammo_box")
# 武装重新补给舱与补给型 FRV 共用这挺 AR-23 炮塔，只是挂点不同。
TURRET_UNIT = ("content/fac_helldivers/hellpod/ammo_rack_mounted_turret/"
               "ammo_rack_mounted_turret")
# 补给舱本体与舱内的弹药箱。舱体在游戏里是 deploy 动画控制的开合结构，
# 只导出静态 unit 得到的是闭合态，侧面要自己开出口子。
RACK_UNIT = "content/fac_helldivers/hellpod/ammo_rack/ammo_rack"
CARGO_UNIT = "content/fac_helldivers/hellpod/ammo_rack/ammo_box"
# 四个补给包与炮塔的挂点，取自 unit 骨骼 attach_01..04 / attach_top 的世界变换
# （GLB 的节点矩阵累乘后换回 unit 坐标，高度减 0.957 对齐到罐体中心）。
# 包是沿着 z 轴挂的，上下两排；attach_02/04 相对 01/03 绕竖轴转了 180°。
CARGO_MOUNTS = (
    ((0.0, 0.500, 0.301), 1.0),
    ((0.0, -0.500, 0.301), -1.0),
    ((0.0, 0.500, -0.277), 1.0),
    ((0.0, -0.500, -0.238), -1.0),
)
TURRET_MOUNT = (0.0, 0.0, 0.862)
# 侧面开口的范围：前后两侧各一个，覆盖上下两排补给包，只挖外壁。
CARGO_WINDOWS = (90.0, 270.0)
CARGO_HALF_ANGLE = 32.0
CARGO_Z_RANGE = (-0.34, 0.62)
CARGO_RADIUS = 0.42
MOUNTS = {
    # wall: [(unit 路径, 挂点世界坐标, 对位点)]
    # 发射器挂在墙的 attach_gun 骨骼（世界平移 (0, -0.052, 1.253)，旋转为单位矩阵）。
    # 弹链盒挂在武器的 attach_mag 骨骼，枪内坐标 (-0.023, 0.292, 0.146)，
    # 叠上 attach_gun 的平移就是 (-0.023, 0.240, 1.399)。
    "wall": [
        (GL_UNIT, (0.0, -0.052, 1.253), (0.0, 0.0, 0.0)),
        (MAG_UNIT, (-0.023, 0.240, 1.399), (0.0, 0.0, 0.0)),
    ],
    # 补给车货斗后段的 gun_mount 骨骼：世界 (x, y, z) = (0, -1.199, 1.083)，
    # 就是炮塔的安装原点；货斗底板在 z=0.18，中间那截是炮塔自己的底座。
    "car": [(TURRET_UNIT, (0.0, -1.199, 1.083), (0.0, 0.0, 0.0))],
}

# 挂载件单独配色，键是 MOUNTS 里的序号（1 = 发射器，2 = 弹链盒）。
# 弹链盒是弹药箱，比掩体暗且偏黄铜；和墙用同一套灰就会连轮廓一起糊在墙顶豁口里。
# 补给舱与补给车是冷灰的箱体/车身，炮塔染成暖金，一眼能看出显示的是哪一件。
SUPPLY_TINT = ((0.40, 0.19, 0.02), (1.00, 0.76, 0.30))
# 舱内的弹药箱比罐体暖、比炮塔灰，夹在两者之间才分得清三层。
CARGO_TINT = ((0.16, 0.20, 0.13), (0.60, 0.70, 0.46))
MOUNT_TINTS = {
    "wall": {2: ((0.22, 0.21, 0.16), (0.84, 0.79, 0.65))},
    "crate": {1: CARGO_TINT, 2: SUPPLY_TINT},
    "car": {1: SUPPLY_TINT},
}

# 载具与补给舱是大块薄壳，默认的半透明会把背面重叠面透出来，看着像缺件。
SOLID_DEPTH = {"car": 0.40, "crate": 0.16}

# 补给车原朝向就能看清整车与炮塔，不需要再转。
SPEC_YAW = {}

# 三种迫击共用同一个模型，靠游戏原版的战备图标区分。
# 弹药显示左侧的单位图标取原版战备 HUD 图标；manned_flame 那个带人形，
# 是"人操"标记，不是哨戒本身的图标。
AMMO_ICON = "icon_strat_hud_flamethrower_turret"
# E/AT-12 是人操反坦克炮台，图标挂在 stratagem 目录根下，没有 defensive_emplacements
# 前缀（那个族只有地雷、掩体墙、能量盾、特斯拉塔）。这个名字也还没进 hash 表，
# 所以只能按名字算 hash 去取文件。
AT_ICON = "icon_strat_hud_anti_tank_emplacement"

MORTARS = (
    ("mortar_gas", "icon_strat_hud_defensive_turrets_mortar_gas", (0.39, 0.88, 0.42)),
    ("mortar", "icon_strat_hud_defensive_turrets_mortar", (0.64, 0.42, 1.00)),
    ("mortar_ems", "icon_strat_hud_defensive_turrets_mortar_staticfield", (0.30, 0.78, 1.00)),
)


def stem_of(path):
    """rawdump 导出时会去掉 content/ 前缀。"""
    return path.removeprefix("content/").replace("/", "_")


def dump(game_dir):
    WORK.mkdir(parents=True, exist_ok=True)
    paths = sorted({spec.path for spec in SPECS.values()}
                   | {part[0] for parts in MOUNTS.values() for part in parts}
                   | {RACK_UNIT, CARGO_UNIT})
    for _ in range(2):
        if all((WORK / (stem_of(p) + ".main")).exists() for p in paths):
            break
        cmd = [str(RAWPUMP), str(game_dir), str(WORK), TYPE_UNIT] + paths
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    for path in paths:
        if not (WORK / (stem_of(path) + ".main")).exists():
            print("missing:", path)


def idstring64(text):
    """资源名 -> 8 字节小端 MurmurHash64A，与游戏归档里的名字哈希一致。"""
    mask = (1 << 64) - 1
    mul = 0xC6A4A7935BD1E995
    data = text.encode("utf-8")
    length = len(data)
    h = (length * mul) & mask
    whole = length - length % 8
    for i in range(0, whole, 8):
        v = int.from_bytes(data[i:i + 8], "little")
        v = (v * mul) & mask
        v ^= v >> 47
        v = (v * mul) & mask
        h ^= v
        h = (h * mul) & mask
    if whole < length:
        h ^= int.from_bytes(data[whole:], "little")
        h = (h * mul) & mask
    h ^= h >> 47
    h = (h * mul) & mask
    h ^= h >> 47
    return h.to_bytes(8, "little")


def dump_strat_icons(game_dir):
    """原版战备图标（256×256 方形）：红通道是本体，绿通道是效果符号。

    按名字哈希取文件而不是按名字匹配：部分战备（如 E/AT-12）的资源名还没被
    名字表收录，按名字搜是空的，但哈希本身照样能命中。已知名字的图标会被写成
    原名，未知名字的落成 0x<hash>.png，两种都要认。
    """
    wanted = [stem for _, stem, _ in MORTARS] + [AMMO_ICON, AT_ICON]
    missing = [stem for stem in wanted if not (STRAT_DIR / (stem + ".png")).exists()]
    if not missing:
        return
    STRAT_ROOT.mkdir(parents=True, exist_ok=True)
    STRAT_DIR.mkdir(parents=True, exist_ok=True)
    # 整个归档只有 688 张图：一次全导再按哈希挑，不必为每张图重读一遍元数据。
    subprocess.run([str(FILEDIVER), "-g", str(game_dir), "--archives", STRAT_ARCHIVE,
                    "-o", str(STRAT_ROOT), "--texture-format", "png"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for stem in missing:
        if (STRAT_DIR / (stem + ".png")).exists():
            continue
        key = int.from_bytes(idstring64(STRAT_PATH + stem), "little")
        dumped = STRAT_ROOT / ("0x%016x.png" % key)
        if not dumped.exists():
            raise SystemExit("missing stratagem icon: " + stem)
        dumped.replace(STRAT_DIR / (stem + ".png"))


def crisp(image):
    return image.filter(ImageFilter.UnsharpMask(radius=2.2, percent=85, threshold=2))


def tint_model(image, shadow, light):
    """灰阶渲染图按亮度重映射到两端色，并压低透明度的损失。"""
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    rgb, alpha = arr[..., :3], arr[..., 3]
    luma = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    mask = alpha > 0.2
    if mask.any():
        lo, hi = np.percentile(luma[mask], (4, 96))
    else:
        lo, hi = float(luma.min()), float(luma.max())
    flat = np.clip((luma - lo) / max(float(hi - lo), 1e-3), 0.0, 1.0)
    t = flat ** 0.95

    low = np.array(shadow, np.float32)
    high = np.array(light, np.float32)
    out = low + (high - low) * t[..., None]

    # 金属反光：灰度图里 rgb.max 恒等于 luma，原来那版「提回高光」实际上一律为 0。
    # 只对最亮的一小段叠一点白，多余的光泽会盖掉半透明感。
    glint = np.clip((flat - 0.90) / 0.10, 0.0, 1.0) ** 1.5
    out = np.clip(out + glint[..., None] * 0.34, 0.0, 1.0)

    a = np.clip(alpha ** 0.90, 0.0, 1.0)
    result = Image.fromarray((out * 255.0 + 0.5).astype(np.uint8), "RGB").convert("RGBA")
    result.putalpha(Image.fromarray((a * 255.0 + 0.5).astype(np.uint8), "L"))
    return result


def index_width(main, layout):
    """索引位宽：顶点数超过 65535 的 mesh 用 u32，字节数除以索引条数即得。"""
    count = bp.u32(main, layout + bp.LAYOUT_NUM_INDICES)
    size = bp.u32(main, layout + bp.LAYOUT_INDICES_SIZE)
    if count and size // count == 4:
        return 4
    return 2


def unit_parts(stem, clip=None):
    """unit 里全部可见材质组，按 (mesh, group) 切开返回三角形列表。

    不做高度过滤：炮塔的「底座 + 支架」一组高 1.78、武器一组只有 0.42，
    按高度阈值滤光柱会把底座那组一起删掉，画出来只剩半截枪。

    载具（FRV）的整车 mesh 顶点数过 65535，索引是 u32；一律按 u16 读会把
    索引拆成两半，画出来是一团从中心放射的碎片。
    """
    main = (WORK / (stem + ".main")).read_bytes()
    gpu = (WORK / (stem + ".gpu")).read_bytes()
    thins, fulls = bp.material_map(main)
    visible = {t for t, f in zip(thins, fulls) if f not in mp.PROXY_FULLS}
    primary = rt.primary_meshes(main)
    layouts = bp.layout_blocks(main)

    parts = []
    for index, mesh in enumerate(bp.mesh_infos(main)):
        if primary is not None and index not in primary:
            continue
        if not (set(bp.mesh_materials(main, mesh)) & visible):
            continue
        layout = layouts[bp.mesh_layout(main, mesh)]
        items = bp.layout_items(main, layout)
        stride = bp.u32(main, layout + bp.LAYOUT_VERTEX_STRIDE)
        count_v = bp.u32(main, layout + bp.LAYOUT_NUM_VERTICES)
        vbase = bp.u32(main, layout + bp.LAYOUT_VERTEX_OFFSET)
        ibase = bp.u32(main, layout + bp.LAYOUT_INDEX_OFFSET)
        pos_at, fmt = rt.position_offset(items)
        if fmt != 2:
            raise SystemExit("unsupported position format %d" % fmt)
        block = np.frombuffer(gpu, dtype="<f4", count=count_v * stride // 4, offset=vbase)
        pos = block.reshape(count_v, stride // 4)[:, pos_at // 4: pos_at // 4 + 3]
        width = index_width(main, layout)
        dtype = "<u4" if width == 4 else "<u2"
        for order, group in enumerate(bp.mesh_groups(main, mesh)):
            if group[4] < 3:
                continue
            idx = np.frombuffer(gpu, dtype=dtype, count=group[4],
                                offset=ibase + group[3] * width)
            idx = idx[:(group[4] // 3) * 3].reshape(-1, 3).astype(np.int32)
            parts.append((index, order, pos[idx]))
    if not parts:
        raise SystemExit("unit has no visible triangles: %s" % stem)
    if clip is not None:
        parts = [(m, g, rt.clip_center(t, clip)) for m, g, t in parts]
    return parts


def turret_tris(stem, clip=None):
    """unit 的全部可见三角形，按 (mesh, group) 合并成一个大数组。"""
    return np.concatenate([t for _, _, t in unit_parts(stem, clip)], axis=0)


def mount(tris, target, yaw=0.0, scale=1.0, anchor=None):
    """把挂载件搬到 target（世界坐标），可选绕竖直轴旋转。

    主 unit 只带了安装座，部件本体在另一个 unit 里，两者都是米制坐标，
    按世界坐标拼即可，比例天然正确。anchor 为模型自身坐标里的对位点，
    省略时用包围盒中心；挂点按武器原点定义时传 (0, 0, 0)。
    """
    flat = tris.reshape(-1, 3)
    if anchor is None:
        lo, hi = flat.min(axis=0), flat.max(axis=0)
        anchor = (lo + hi) / 2.0
    v = (flat - np.asarray(anchor, np.float32)) * scale
    if yaw:
        a = np.radians(yaw)
        c, s = np.cos(a), np.sin(a)
        rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], np.float32)
        v = v @ rot.T
    return (v + np.asarray(target, np.float32)).reshape(tris.shape)


def coil_belt(tris, centre=(0.060, -0.210), radius=(0.032, 0.072)):
    """把拉直的弹链绕竖直轴卷成一圈螺旋，塞进弹匣箱体里。

    弹链盒 unit 的 1、2 组在模型里是沿 +X 拉直的 0.5 m 一排（链带 + 14 发 40mm），
    直接画会像一条带子横在墙顶外面。按弧长把 X 卷成圆周、把原来的 z 偏量当作
    径向厚度，弹体截面就不会被压扁。
    """
    flat = tris.reshape(-1, 3)
    x0, x1 = float(flat[:, 0].min()), float(flat[:, 0].max())
    length = max(x1 - x0, 1e-6)
    r0, r1 = radius
    u = (flat[:, 0] - x0) / length
    # 半径线性增长时，角度按 ∫du·L/R(u) 累积，螺距才均匀。
    pitch = max(r1 - r0, 1e-6)
    theta = length / pitch * np.log((r0 + pitch * u) / r0)
    radial = r0 + pitch * u + flat[:, 2]
    out = flat.copy()
    out[:, 0] = centre[0] + radial * np.cos(theta)
    out[:, 2] = centre[1] + radial * np.sin(theta)
    return out.reshape(tris.shape)


def _components(mask):
    """4 连通分块，返回 [(最左列, 区域掩膜)]。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, bool)
    out = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or seen[y, x]:
                continue
            stack = [(y, x)]
            seen[y, x] = True
            pts = []
            while stack:
                cy, cx = stack.pop()
                pts.append((cy, cx))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            region = np.zeros_like(mask, bool)
            ys = [p[0] for p in pts]
            xs = [p[1] for p in pts]
            region[ys, xs] = True
            out.append((min(xs), region))
    return out


def bolt_mask(stem, size=48):
    """战备图标的绿通道里有两坨：右边的炮弹本体、左边的效果符号。只取左边那坨。"""
    src = Image.open(STRAT_DIR / (stem + ".png")).convert("RGB")
    mask = np.asarray(src, np.float32)[..., 1] / 255.0 > 0.35
    parts = _components(mask)
    if not parts:
        raise SystemExit("icon has no mark: %s" % stem)
    _, region = min(parts, key=lambda item: item[0])
    art = Image.fromarray((region * 255).astype(np.uint8), "L")
    art = art.crop(art.getbbox())
    scale = size / max(art.size)
    return art.resize((max(1, int(art.width * scale)), max(1, int(art.height * scale))),
                      Image.LANCZOS)


def bolt_overlay(image, stem, alpha=175):
    """把 2D 闪电贴到塔尖右上方，作为灰度并进上色管线。

    直接贴带色的小图会得到一层不透明的死色块；先垫成白色蒙版再统一 tint，
    颜色与透明度才会和塔体一致。
    """
    art = bolt_mask(stem)
    # 灰度取 0.78：白色会落到调色板最亮端，比塔体亮出一截；压到中高调才像同一材质。
    solid = Image.new("RGBA", art.size, (199, 199, 199, 0))
    solid.putalpha(art.point(lambda v: int(v * alpha / 255)))

    ink = np.array(image)[:, :, 3]
    ys, xs = np.nonzero(ink > 40)
    if not len(ys):
        return image
    top = int(ys.min())
    band = ys < top + max(4, int((ys.max() - top) * 0.02))
    tip_x = int(np.mean(xs[band]))

    left = max(2, min(tip_x + 8, image.width - solid.width - 2))
    upper = max(2, top + 12 - solid.height)
    out = image.copy()
    out.alpha_composite(solid, (left, upper))
    return out


def shade_metal(normals, facing):
    """炮塔用的柔光：亮暗之间留足层次，但暗部不压死。

    共享渲染器的光照（AMBIENT 0.19 + 大范围 lambert + 到处铺的 sheen）在这批
    大模型上会把亮度堆到 0.9 附近，调色板只能吃到亮端，看起来一片糊；
    而只留环境光 + 紧高光又会反过来变成实心金属块。这里取中间：环境光抬到 0.22、
    lambert 收在 1.1、高光柔化，再加一圈较宽的掠射边缘光做出通透感。
    """
    n = np.where(facing[:, None], normals, -normals)
    light = rt.LIGHT / np.linalg.norm(rt.LIGHT)
    fill = rt.FILL / np.linalg.norm(rt.FILL)
    view = np.array([0.0, 1.0, 0.0], np.float32)
    half = light + view
    half /= np.linalg.norm(half)

    lambert = np.clip(n @ light.astype(np.float32), 0.0, 1.0) ** 1.10
    bounce = np.clip(n @ fill.astype(np.float32), 0.0, 1.0) ** 1.8
    spec = np.clip(n @ half.astype(np.float32), 0.0, 1.0)
    rim = (1.0 - np.clip(n @ view, 0.0, 1.0)) ** 2.2

    base = 0.22 + 0.56 * lambert + 0.08 * bounce
    gloss = spec ** 30.0 * 0.40 + spec ** 7.0 * 0.10
    color = np.repeat(base[:, None], 3, axis=1)
    color += gloss[:, None]
    color += rim[:, None] * 0.16
    return np.clip(color, 0.0, 1.0)


def rasterize_var(sx, sy, depth, color, alpha, size, tags=None):
    """rt.rasterize 的逐三角形 alpha 版本，可顺带记录每个像素来自哪一组。"""
    img = np.zeros((size, size, 3), np.float32)
    out_a = np.zeros((size, size), np.float32)
    out_tag = np.zeros((size, size), np.uint8)
    zbuf = np.full((size, size), -1e9, np.float32)

    grid = np.arange(size)
    for i in range(len(sx)):
        px0 = max(int(np.floor(sx[i].min())), 0)
        px1 = min(int(np.ceil(sx[i].max())), size - 1)
        py0 = max(int(np.floor(sy[i].min())), 0)
        py1 = min(int(np.ceil(sy[i].max())), size - 1)
        if px1 < px0 or py1 < py0:
            continue
        gx, gy = np.meshgrid(grid[px0:px1 + 1], grid[py0:py1 + 1])
        ax, ay = sx[i, 0], sy[i, 0]
        bx, by = sx[i, 1], sy[i, 1]
        cx_, cy_ = sx[i, 2], sy[i, 2]
        area = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
        if abs(area) < 1e-9:
            continue
        w0 = ((by - cy_) * (gx - cx_) + (cx_ - bx) * (gy - cy_)) / area
        w1 = ((cy_ - ay) * (gx - cx_) + (ax - cx_) * (gy - cy_)) / area
        w2 = 1.0 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        d = w0 * depth[i, 0] + w1 * depth[i, 1] + w2 * depth[i, 2]
        buf = zbuf[py0:py1 + 1, px0:px1 + 1]
        win = inside & (d > buf)
        if not win.any():
            continue
        buf[win] = d[win]
        img[py0:py1 + 1, px0:px1 + 1][win] = color[i]
        out_a[py0:py1 + 1, px0:px1 + 1][win] = alpha[i]
        if tags is not None:
            out_tag[py0:py1 + 1, px0:px1 + 1][win] = tags[i]
    return img, out_a, out_tag


def render_tris(tris, frame=rt.FRAME, tags=None, solid=0.0):
    """rt.render 的后半段，换成直接吃三角形数组。

    tags 给每个三角形标组号；给了就多返回灰度图和组号图，供挂载件单独上色。
    solid 抬高不透明度：载具这种大块薄壳按默认半透明渲染会透出背面的重叠面，
    看着像外壳缺了好几块。
    """
    tris = rt.transform(tris)
    size = rt.SUPERSAMPLE
    px, py = tris[:, :, 0], -tris[:, :, 2]
    span = max(float(px.max() - px.min()), float(py.max() - py.min()), 1e-9)
    scale = size * frame / span
    sx = (px - (px.min() + px.max()) / 2.0) * scale + size / 2.0
    sy = (py - (py.min() + py.max()) / 2.0) * scale + size / 2.0
    sz = tris[:, :, 1]

    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    facing = normal[:, 1] > 0.0
    color = shade_metal(normal, facing)

    # 半透明：alpha 跟着亮度走，暗部透光、亮部才结实处。正面与背面合成后，
    # 背面层能透过正面暗部显出来，轮廓内部才有一层层次，不是一块实心色。
    # 背面颜色只压到 0.90：压太狠会在轮廓边缘透出黑块。
    lum = np.clip(color[:, 0], 0.0, 1.0)
    front_tag = None if tags is None else tags[facing]
    back_tag = None if tags is None else tags[~facing]
    front_alpha = np.clip(0.56 + 0.36 * lum[facing] + solid, 0.0, 1.0)
    back_alpha = np.full(int((~facing).sum()), min(0.50 + solid * 0.8, 1.0))
    back_img, back_a, back_tag_map = rasterize_var(
        sx[~facing], sy[~facing], sz[~facing],
        color[~facing] * 0.90, back_alpha, size, back_tag)
    front_img, front_a, front_tag_map = rasterize_var(
        sx[facing], sy[facing], sz[facing],
        color[facing], front_alpha, size, front_tag)
    alpha = front_a + back_a * (1.0 - front_a)
    safe = np.maximum(alpha, 1e-6)[:, :, None]
    img = front_img * front_a[:, :, None] + back_img * back_a[:, :, None] * (1.0 - front_a)[:, :, None]
    img = img / safe

    rgb = Image.fromarray((np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")
    a = Image.fromarray((np.clip(alpha, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "L")
    out = rgb.convert("RGBA")
    out.putalpha(a)
    out = out.resize((rt.OUTPUT, rt.OUTPUT), Image.LANCZOS)
    if tags is None:
        return out, None, None

    tag_map = np.where(front_a > 0.0, front_tag_map, back_tag_map)
    tags_img = Image.fromarray(tag_map, "L").resize((rt.OUTPUT, rt.OUTPUT), Image.NEAREST)
    return out, rgb.resize((rt.OUTPUT, rt.OUTPUT), Image.LANCZOS), tags_img


def retint(image, gray, tag_map, tag, shadow, light, alpha_gain=1.05):
    """把某一组部件的颜色换成另一套映射。

    挂载件和主模型同一套灰度渲染，直接整体上色会和本体糊成一片，
    盒子尤其明显 —— 轮廓被墙顶豁口吃掉，只剩弹链露在外面。
    """
    luma = np.asarray(gray, np.float32) / 255.0 @ np.array([0.299, 0.587, 0.114], np.float32)
    mask = np.asarray(tag_map) == tag
    if not mask.any():
        return image
    lo, hi = np.percentile(luma[mask], (4, 96))
    t = np.clip((luma - lo) / max(float(hi - lo), 1e-3), 0.0, 1.0) ** 0.95
    low, high = np.array(shadow, np.float32), np.array(light, np.float32)
    color = (low + (high - low) * t[..., None]) * 255.0

    out = np.asarray(image, np.float32).copy()
    out[mask, :3] = color[mask]
    out[mask, 3] = np.clip(out[mask, 3] * alpha_gain, 0.0, 255.0)
    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGBA")


def mag_part():
    """弹链盒：0 组是箱体原样保留，1、2 组的弹链先卷进箱体。"""
    parts = unit_parts(stem_of(MAG_UNIT))
    box = [t for _, group, t in parts if group == 0]
    belt = [t for _, group, t in parts if group != 0]
    return np.concatenate([np.concatenate(box), coil_belt(np.concatenate(belt))], axis=0)


def place(tris, matrix):
    """按 4×4 矩阵摆放：挂点自带朝向，包和炮塔都要按骨骼的姿态落位。"""
    flat = tris.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    return flat.reshape(tris.shape)


def cargo_window(tris):
    """侧面开口：只删三个顶点全落在窗口里的三角形，边缘留在原网格线上。

    按三角形重心判会切出锯齿，按顶点判则保留模型自身的边框轮廓。
    """
    radius = np.hypot(tris[..., 0], tris[..., 1])
    angle = np.arctan2(tris[..., 1], tris[..., 0])
    z0, z1 = CARGO_Z_RANGE
    base = (radius > CARGO_RADIUS) & (tris[..., 2] > z0) & (tris[..., 2] < z1)
    sel = np.zeros(tris.shape[:2], bool)
    for centre in CARGO_WINDOWS:
        delta = np.abs((angle - math.radians(centre) + math.pi) % (2 * math.pi) - math.pi)
        sel |= base & (delta < math.radians(CARGO_HALF_ANGLE))
    return sel.all(axis=1)


def crate_assembly():
    """装好补给的武装补给舱：侧面开两口，四个补给包挂在 attach_01..04，炮塔在 attach_top。

    返回 (三角形, 组号)：0 罐体、1 补给包、2 炮塔。
    """
    body = turret_tris(stem_of(RACK_UNIT))
    groups = [(body[~cargo_window(body)], 0)]

    box = turret_tris(stem_of(CARGO_UNIT))
    for (x, y, z), flip in CARGO_MOUNTS:
        matrix = np.eye(4)
        matrix[:3, :3] = np.diag([flip, flip, 1.0])
        matrix[:3, 3] = (x, y, z)
        groups.append((place(box, matrix), 1))

    turret = np.eye(4)
    turret[:3, 3] = TURRET_MOUNT
    groups.append((place(turret_tris(stem_of(TURRET_UNIT)), turret), 2))

    tris = np.concatenate([t for t, _ in groups])
    tags = np.concatenate([np.full(len(t), g, np.uint8) for t, g in groups])
    return tris, tags


def yaw_model(tris, degrees):
    """绕竖直轴转整台单位（挂载件已经并进同一份三角形，会一起转）。"""
    v = tris.reshape(-1, 3).copy()
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    x, y = v[:, 0].copy(), v[:, 1].copy()
    v[:, 0] = c * x - s * y
    v[:, 1] = s * x + c * y
    return v.reshape(tris.shape)


def render_all():
    IMAGES.mkdir(parents=True, exist_ok=True)
    for key, spec in SPECS.items():
        if key == "crate":
            tris, tags = crate_assembly()
        else:
            tris = turret_tris(stem_of(spec.path), spec.clip)
            tags = None
        if key in MOUNTS:
            parts = [tris]
            tags = [np.zeros(len(tris), np.uint8)]
            for index, (unit, target, anchor) in enumerate(MOUNTS[key], start=1):
                raw = mag_part() if unit == MAG_UNIT else turret_tris(stem_of(unit))
                part = mount(raw, target, anchor=anchor)
                parts.append(part)
                tags.append(np.full(len(part), index, np.uint8))
            tris, tags = np.concatenate(parts, axis=0), np.concatenate(tags)

        if key in SPEC_YAW:
            tris = yaw_model(tris, SPEC_YAW[key])

        image, gray, tag_map = render_tris(tris, tags=tags,
                                           solid=SOLID_DEPTH.get(key, 0.0))
        if key == "tesla":
            # 特斯拉没有弹匣：塔尖右侧挂电磁迫击那颗 2D 闪电，按放电而非弹药计量。
            image = bolt_overlay(image, MORTARS[2][1])
        image = tint_model(image, spec.shadow, spec.light)
        for index, (shadow, light) in MOUNT_TINTS.get(key, {}).items():
            image = retint(image, gray, tag_map, index, shadow, light)
        crisp(image).save(IMAGES / ("model_" + key + ".png"))
        print("render", key)


def scaled(path, size, fill=0.97, by="height"):
    """裁到内容框后等比缩放。

    默认按高度统一：按最长边缩会让宽扁的模型（机枪带支架）被横向压小，
    摆成一排时高度对不齐。by="max" 按最长边；by="area" 按面积开方，
    用于同一张图里混排宽高比差得多的模型 —— 按高度统一会让宽扁的看起来偏小。
    """
    image = Image.open(path).convert("RGBA")
    box = image.getbbox()
    if box:
        image = image.crop(box)
    w, h = image.size
    if by == "height":
        base = h
    elif by == "max":
        base = max(w, h)
    else:
        base = math.sqrt(w * h)
    scale = size * fill / max(base, 1)
    return image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def base_span(art, band=0.18):
    """模型底部条带的水平范围。炮管伸出的宽度不算，只按底座边缘排间距。"""
    alpha = np.asarray(art.getchannel("A"))
    rows = alpha[int(alpha.shape[0] * (1.0 - band)):, :]
    cols = np.nonzero((rows > 12).any(axis=0))[0]
    if not len(cols):
        return 0, art.width - 1
    return int(cols[0]), int(cols[-1])


def scaled_by_base(path, base_px):
    """按底座宽度缩放。

    实弹组五台共用同一个底座，渲染时按各自外框归一化，炮管越长底座显得越小；
    统一按底座像素宽度缩放，摆在一起才是同一个基座。
    """
    image = Image.open(path).convert("RGBA")
    box = image.getbbox()
    if box:
        image = image.crop(box)
    left, right = base_span(image)
    k = base_px / max(right - left + 1, 1)
    return image.resize((max(1, round(image.width * k)), max(1, round(image.height * k))),
                        Image.LANCZOS)


def full_span(art):
    """整幅轮廓的水平范围。整行都密排时，只有按它排才保证任何高度上都不相碰。"""
    alpha = np.asarray(art.getchannel("A"))
    cols = np.nonzero((alpha > 12).any(axis=0))[0]
    if not len(cols):
        return 0, art.width - 1
    return int(cols[0]), int(cols[-1])


def paste_center(canvas, image, cx, cy):
    canvas.alpha_composite(image, (cx - image.width // 2, cy - image.height // 2))


FONT_DIRS = (Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
             Path("C:/Windows/Fonts"))


def _font(size, heavy=True):
    """heavy 给标题类粗体；False 走 Arial Bold，游戏 HUD 的数字就是这一档。"""
    for name in (("ariblk.ttf", "arialbd.ttf", "arial.ttf") if heavy
                 else ("arialbd.ttf", "arial.ttf")):
        for root in FONT_DIRS:
            path = root / name
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    pass
    return ImageFont.load_default()


def hud_font(size):
    """HUD 数字用等宽字形；本机装了 JetBrains Mono 就用它，否则退回 Arial Bold。"""
    for name in ("JetBrainsMono-SemiBold.ttf", "JetBrainsMono-Bold.ttf",
                 "JetBrainsMono-Medium.ttf", "JetBrainsMono-Regular.ttf"):
        for root in FONT_DIRS:
            path = root / name
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    pass
    return _font(size, heavy=False)


def _fill_holes(mask):
    """把闭合轮廓的内部填实：火焰在图标里画的是描边，直接取只剩一圈线。

    从补上一圈边后的左上角做 4 连通漫水，走不到的地方就是轮廓内部。
    """
    free = ~np.pad(mask, 1, constant_values=False)
    seen = np.zeros_like(free)
    h, w = free.shape
    seen[0, 0] = True
    stack = [(0, 0)]
    while stack:
        y, x = stack.pop()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and free[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return ~seen[1:-1, 1:-1]


def unit_mark(stem, colour, flame=(0.96, 0.98, 1.00), size=0):
    """战备图标文件里的两路墨迹：红通道是本体，绿通道是火焰。

    本体填 colour、火焰填 flame（截图里火焰是白的），其余透明。
    """
    src = Image.open(STRAT_DIR / (stem + ".png")).convert("RGB")
    arr = np.asarray(src, np.float32) / 255.0
    body = arr[..., 0] > 0.25
    glow = _fill_holes(arr[..., 1] > 0.25)
    mask = body | glow

    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    box = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
    mask, body, glow = mask[box], body[box], glow[box]

    rgba = np.zeros(mask.shape + (4,), np.float32)
    rgba[..., :3] = np.array(colour, np.float32)
    rgba[glow, :3] = np.array(flame, np.float32)
    rgba[..., 3] = mask.astype(np.float32)
    art = Image.fromarray((rgba * 255.0 + 0.5).astype(np.uint8), "RGBA")
    scale = size / art.height
    return art.resize((max(1, round(art.width * scale)), size), Image.LANCZOS)


def ammo_bar(rounds=760, capacity=900, segments=4, digit=76,
             icon=AMMO_ICON,
             ink=(0.20, 0.85, 0.36)):
    """游戏内的弹药显示：四角框只圈左侧单位图标，右侧大字与其下的分段横条。

    比例取自截图里火焰哨戒那一条（以数字高度为 1）：图标高 1.04、图标与数字
    间距 1.0、条长 1.18 且水平居中于数字、条高 0.14、数字与条间距 0.22、
    段间为段宽的 0.29。
    """
    font = hud_font(digit)
    label = str(rounds)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), label, font=font, stroke_width=2)
    text_w, text_h = box[2] - box[0], box[3] - box[1]

    mark = unit_mark(icon, ink, size=int(text_h * 1.04))
    icon_gap = int(text_h * 0.95)
    # 框固定为正方形，边长只由字高决定：两条弹药显示的框因此完全等大，
    # 也不会因为图标本身宽扁就被拉成矩形。
    side = int(text_h * 2.05)
    frame_w = frame_h = side
    limit = int(side * 0.66)
    if max(mark.width, mark.height) > limit:
        k = limit / max(mark.width, mark.height)
        mark = mark.resize((max(1, round(mark.width * k)), max(1, round(mark.height * k))),
                           Image.LANCZOS)

    thickness = max(6, int(text_h * 0.14))
    # 条长与数字位数无关：30 和 760 的条一样长，宽度按字高定
    bar_w = int(text_h * 2.7)
    seg_w = bar_w / (segments + 0.29 * (segments - 1))
    seg_gap = int(seg_w * 0.29)
    bar_top_gap = int(text_h * 0.22)
    content_h = text_h + bar_top_gap + thickness
    pad = 6

    left = pad + frame_w + icon_gap
    height = max(content_h, frame_h) + pad * 2
    bar_x = left + (text_w - bar_w) // 2
    width = max(left + text_w, bar_x + bar_w) + pad

    img = Image.new("RGBA", (width, height), CLEAR)
    d = ImageDraw.Draw(img)

    top = (height - content_h) // 2
    d.text((left - box[0], top - box[1]), label, font=font,
           fill=(250, 252, 255, 244), stroke_width=2, stroke_fill=(10, 13, 18, 200))

    fraction = max(0.0, min(1.0, rounds / float(capacity)))

    bar_y = top + text_h + bar_top_gap
    for i in range(segments):
        x = bar_x + i * (seg_w + seg_gap)
        d.rectangle((x, bar_y, x + seg_w, bar_y + thickness),
                    fill=(94, 100, 112, 120))
        part = max(0.0, min(1.0, fraction * segments - i))
        if part > 0.02:
            d.rectangle((x, bar_y, x + max(2.0, seg_w * part), bar_y + thickness),
                        fill=(242, 247, 255, 238))

    fx = pad
    fy = (height - frame_h) // 2
    img.alpha_composite(mark, (fx + (frame_w - mark.width) // 2,
                               fy + (frame_h - mark.height) // 2))
    # 四角是斜切折角：竖臂上来先折 45° 段，再横臂
    arm = int(frame_h * 0.26)
    cut = int(frame_h * 0.15)
    line = max(3, round(frame_h * 0.024))
    l, r = fx, fx + frame_w - 1
    t, b = fy, fy + frame_h - 1
    inkc = (240, 245, 252, 215)
    for sx, sy in ((l, t), (r, t), (l, b), (r, b)):
        dx = 1 if sx == l else -1
        dy = 1 if sy == t else -1
        d.line([(sx, sy + dy * arm), (sx, sy + dy * cut),
                (sx + dx * cut, sy), (sx + dx * arm, sy)],
               fill=inkc, width=line, joint="curve")
    return img


def strat_mark(stem, accent, size=62):
    """只取原版战备图标里的效果符号（绿通道），透明底。"""
    src = Image.open(STRAT_DIR / (stem + ".png")).convert("RGB")
    arr = np.asarray(src, dtype=np.float32) / 255.0
    mark = arr[..., 1] > 0.35

    rgba = np.zeros(mark.shape + (4,), np.float32)
    rgba[..., :3] = np.array(accent, np.float32)
    rgba[..., 3] = mark.astype(np.float32)
    art = Image.fromarray((rgba * 255.0 + 0.5).astype(np.uint8), "RGBA")
    box = art.getbbox()
    if box:
        art = art.crop(box)
    scale = size / max(art.size)
    return art.resize((max(1, int(art.width * scale)), max(1, int(art.height * scale))),
                      Image.LANCZOS)


def mortar_column(target=None, size=250, chip=76):
    """并排三个原版符号在上、一个 3D 迫击炮在下，不加框。

    target 给定时整列高度等于它（与特斯拉塔的上下界对齐），差值由两者间距吸收。
    """
    marks = [strat_mark(stem, colour, chip) for _, stem, colour in MORTARS]
    gap = 0
    row = sum(m.width for m in marks) + gap * (len(marks) - 1)
    width = max(row, 200)
    art = scaled(IMAGES / "model_mortar.png", size)
    space = 18
    content = chip + space + art.height
    height = max(int(target or 0), content)
    group = Image.new("RGBA", (width, height), CLEAR)
    top = height - content
    centre = width // 2
    x = centre - row // 2
    for mark in marks:
        group.alpha_composite(mark, (x, top))
        x += mark.width + gap
    # 炮身左边炮管伸出得比右边多，按底座中心对位，弹列才落在正上方
    base_l, base_r = base_span(art)
    group.alpha_composite(art, (centre - (base_l + base_r) // 2, height - art.height))
    return group


def with_bar(turret, bar, pad=12):
    """把一条弹药显示摞在炮塔正上方，作为同组一起摆放。"""
    width = max(turret.width, bar.width)
    group = Image.new("RGBA", (width, bar.height + pad + turret.height), CLEAR)
    group.alpha_composite(bar, ((width - bar.width) // 2, 0))
    group.alpha_composite(turret, ((width - turret.width) // 2, bar.height + pad))
    return group


def cover():
    """两列上下齐平：左列火箭 + 迫击炮组，右列激光 + 特斯拉塔。"""
    canvas = Image.new("RGBA", (1024, 1024), CLEAR)

    tesla = scaled(IMAGES / "model_tesla.png", 350)
    tesla_top = 400
    canvas.alpha_composite(tesla, (914 - tesla.width // 2, tesla_top))

    # 迫击炮列的上下界与特斯拉塔（含塔尖闪电）完全对齐
    column = mortar_column(tesla.height)
    canvas.alpha_composite(column, (110 - column.width // 2, tesla_top))

    # 左右两列的顶上一台：底边与特斯拉塔留出同样的间距
    top_bottom = tesla_top - 84
    for key, x in (("rocket", 110), ("laser", 914)):
        art = scaled(IMAGES / ("model_" + key + ".png"), 220)
        canvas.alpha_composite(art, (x - art.width // 2, top_bottom - art.height))

    # 中排：火焰哨戒连同它的弹药显示整体上移；反坦克炮台留在原位，
    # 只在它上方摞一条，两条因此天然错开。
    flame = scaled(IMAGES / "model_flame.png", 330)
    flame_group = with_bar(flame, ammo_bar(rounds=760, capacity=900, digit=56))
    canvas.alpha_composite(flame_group, (366 - flame_group.width // 2, 236))

    antitank = scaled(IMAGES / "model_antitank.png", 312, by="max")
    at_bar = ammo_bar(rounds=30, capacity=30, digit=56, icon=AT_ICON)
    at_group = with_bar(antitank, at_bar, pad=34)
    at_top = 512 - antitank.height // 2 - 34 - at_bar.height
    canvas.alpha_composite(at_group, (646 - at_group.width // 2, at_top))

    bottom = ["mg", "gatling", "autocannon", "hmg", "wall"]
    tile, gap = 190, 12
    total = tile * len(bottom) + gap * (len(bottom) - 1)
    x = (1024 - total) // 2
    for key in bottom:
        # 墙裁掉侧翼后只有 1.26 m 宽，按统一高度摆进来会明显小一圈，单独放高一点。
        size = 230 if key == "wall" else 220
        paste_center(canvas, scaled(IMAGES / ("model_" + key + ".png"), size), x + tile // 2, 870)
        x += tile + gap
    return canvas


def glow(img, cx, cy, radius, color):
    """模型背后的柔光，免得 3D 图浮在纯色底上。"""
    layer = Image.new("RGBA", img.size, CLEAR)
    d = ImageDraw.Draw(layer)
    steps = 14
    for i in range(steps, 0, -1):
        r = radius * i / steps
        alpha = int(4 + 26 * (1 - i / steps) ** 1.3)
        d.ellipse((cx - r, cy - r * 0.92, cx + r, cy + r * 0.92), fill=tuple(color) + (alpha,))
    img.alpha_composite(layer)


GROUP_ART = {
    "laser": ("laser",),
    "tesla": ("tesla",),
}
# 各组"一台炮塔"的目标高度（占图标边长的比例）。按整组外框缩放会让只有一台的组
# 撑满、三台一排的组被压扁，按单台高度统一才有一致的视觉重量。
GROUP_MAIN = {
    "ammo": 0.36,
    "mortar": 0.46,
    "laser": 0.50,
    "tesla": 0.56,
    "emplacement": 0.44,
    "supply": 0.56,
}
GROUP_GLOW = {
    "ammo": (255, 122, 61),
    "mortar": (176, 190, 208),
    "laser": (255, 196, 64),
    "tesla": (86, 214, 255),
    "emplacement": (110, 226, 196),
    "supply": (255, 186, 96),
}


def rows_art(rows, gap, row_gap):
    """逐行按底座边缘等间距排开，行内居中，行底边对齐。

    和封面底排同一套对齐方式：按底座边缘排间距，炮管探到邻座上方也不会显得挤。
    每行先单独画一条，再按实际像素框居中：按 layout_row 的累计宽度居中会被
    长炮管带偏，一行两台的组会整体偏到一侧。
    """
    laid = [layout_row(arts, gap) for arts in rows]
    strips = []
    for arts, w in laid:
        h = max(a.height for _, a in arts)
        strip = Image.new("RGBA", (max(w, 1), h), CLEAR)
        for x, art in arts:
            strip.alpha_composite(art, (x, h - art.height))
        strips.append(strip.crop(strip.getbbox()) if strip.getbbox() else strip)

    width = max(s.width for s in strips)
    height = sum(s.height for s in strips) + row_gap * (len(strips) - 1)
    canvas = Image.new("RGBA", (width, height), CLEAR)
    y = 0
    for strip in strips:
        canvas.alpha_composite(strip, ((width - strip.width) // 2, y))
        y += strip.height + row_gap
    return canvas


def group_art(key):
    """把本组元素摆到一张临时画布上，返回裁到内容框的整组图与单台高度。

    每组元素个数从 1 台到 5 台不等，逐个按固定尺寸摆会让单台组撑满、
    多台组缩成一小团；这里只定元素间的相对大小，最后整组统一缩放居中。
    """
    width = 2000
    canvas = Image.new("RGBA", (width, 1600), CLEAR)

    def row(arts, y, gap=0):
        x = (width - (sum(a.width for a in arts) + gap * (len(arts) - 1))) // 2
        for art in arts:
            canvas.alpha_composite(art, (x, y))
            x += art.width + gap

    if key == "mortar":
        # 三种迫击共用一个模型，靠原版战备图标的效果符号区分；符号排在炮身上方。
        marks = [strat_mark(stem, colour, 200) for _, stem, colour in MORTARS]
        x = (width - (sum(m.width for m in marks) + 24 * 2)) // 2
        for mark in marks:
            canvas.alpha_composite(mark, (x, 0))
            x += mark.width + 24
        art = scaled(IMAGES / "model_mortar.png", 560, by="area")
        unit = art.height
        canvas.alpha_composite(art, ((width - art.width) // 2, marks[0].height + 50))
    elif key == "ammo":
        base = 300
        arts = {k: scaled_by_base(IMAGES / ("model_%s.png" % k), base)
                for k in ("mg", "gatling", "autocannon", "rocket", "flame")}
        art = rows_art([[arts["flame"], arts["rocket"]],
                        [arts["mg"], arts["gatling"], arts["autocannon"]]],
                       int(base * 0.50), int(base * 0.30))
        canvas = art
        unit = max(a.height for a in arts.values())
    elif key == "emplacement":
        arts = {k: scaled(IMAGES / ("model_%s.png" % k), 620, by="area")
                for k in ("hmg", "antitank", "wall")}
        art = rows_art([[arts["antitank"]], [arts["hmg"], arts["wall"]]], 120, 70)
        canvas = art
        unit = max(a.height for a in arts.values())
    elif key == "supply":
        # 左车右仓，按真实尺寸拉开差距：车长 4.68 m，补给舱直径 1.20 m、高 1.73 m。
        # 两者按同一高度摆，罐子会被画得比车还大。
        arts = [scaled(IMAGES / "model_car.png", 520),
                scaled(IMAGES / "model_crate.png", 340)]
        art = rows_art([arts], 60, 0)
        canvas = art
        unit = max(a.height for a in arts)
    else:
        arts = [scaled(IMAGES / ("model_%s.png" % k), 620, by="area") for k in GROUP_ART[key]]
        row(arts, 0, 20)
        unit = max(a.height for a in arts)

    return canvas.crop(canvas.getbbox()), unit


def group_icon(key, size=512, frame=True):
    """选项图标：底色 + 圆角边框 + 柔光，摆上本组的全部炮塔。"""
    icon = Image.new("RGBA", (size, size), BG + (255,))
    if frame:
        d = ImageDraw.Draw(icon)
        inset, radius, width = round(size * 0.023), round(size * 0.070), max(2, round(size * 0.008))
        d.rounded_rectangle((inset, inset, size - inset, size - inset),
                            radius=radius, outline=BORDER, width=width)

    art, unit = group_art(key)
    max_w, max_h = int(size * 0.84), int(size * 0.76)
    k = min(max_w / art.width, max_h / art.height, size * GROUP_MAIN[key] / max(unit, 1))
    art = art.resize((max(1, round(art.width * k)), max(1, round(art.height * k))), Image.LANCZOS)
    cx, cy = (size - art.width) // 2, (size - art.height) // 2
    glow(icon, cx + art.width // 2, cy + art.height // 2, int(art.width * 0.52), GROUP_GLOW[key])
    icon.alpha_composite(art, (cx, cy))
    return icon


# 预览图上排：五台实弹哨戒 + 激光，自动哨戒炮管横向最长，放最右边不挡别人。
PREVIEW_TOP = ("mg", "gatling", "rocket", "flame", "laser", "autocannon")
PREVIEW_MIDDLE = ("hmg", "antitank", "wall")


def layout_row(arts, gap, span=base_span):
    """沿 span 给出的边缘依次排开，相邻两列的间隙相等，返回 [(x, art)] 与总宽。"""
    placed, right = [], None
    for art in arts:
        left, edge = span(art)
        x = -left if right is None else right + gap - left
        placed.append([x, art])
        right = x + edge
    shift = min(x for x, _ in placed) if placed else 0
    for item in placed:
        item[0] -= shift
    width = max(x + art.width for x, art in placed) if placed else 0
    return [(x, art) for x, art in placed], width


def preview():
    """两排一字排开，底座间隙相等；不写战备编号，只留一条分隔线。

    下排两端各是一列：三发弹在上、迫击炮在下，特斯拉塔与之等高；
    中间三座阵地矮一档，全部底边对齐。
    """
    row_h, margin, sep = 190, 26, 46

    top = [scaled(IMAGES / ("model_%s.png" % key), row_h) for key in PREVIEW_TOP]

    marks = [strat_mark(stem, colour, 58) for _, stem, colour in MORTARS]
    mortar = scaled(IMAGES / "model_mortar.png", 186)
    column_w = max(mortar.width, sum(m.width for m in marks))
    column_h = marks[0].height + 14 + mortar.height
    column = Image.new("RGBA", (column_w, column_h), CLEAR)
    x = (column_w - sum(m.width for m in marks)) // 2
    for mark in marks:
        column.alpha_composite(mark, (x, 0))
        x += mark.width
    # 炮身不一定左右对称，按底座中心对位，弹列才会真正落在正上方
    base_l, base_r = base_span(mortar)
    column.alpha_composite(mortar, (column_w // 2 - (base_l + base_r) // 2,
                                    column_h - mortar.height))

    tesla = scaled(IMAGES / "model_tesla.png", column_h)
    bottom = ([column]
              + [scaled(IMAGES / ("model_%s.png" % key), row_h) for key in PREVIEW_MIDDLE]
              + [tesla])

    # 上排按底座排（炮管可以探到邻座上方），下排按整幅轮廓排，保证任何高度都不叠。
    top_row, top_w = layout_row(top, 24)
    bottom_row, bottom_w = layout_row(bottom, 18, span=full_span)
    widest = max(top_w, bottom_w)
    width = widest + margin * 2
    height = margin * 2 + row_h + sep + column_h
    img = Image.new("RGB", (width, height), BG)

    top_left = margin + (widest - top_w) // 2
    for x, art in top_row:
        img.paste(art, (x + top_left, margin + row_h - art.height), art)

    baseline = margin + row_h + sep + column_h
    bottom_left = margin + (widest - bottom_w) // 2
    for x, art in bottom_row:
        img.paste(art, (x + bottom_left, baseline - art.height), art)

    d = ImageDraw.Draw(img)
    line_y = margin + row_h + sep // 2
    d.line((margin, line_y, width - margin, line_y), fill=BORDER, width=2)
    img.save(ROOT / "preview.png", optimize=True)
    print("preview:", ROOT / "preview.png")


def group_icons():
    for key in GROUP_GLOW:
        group_icon(key).convert("RGB").save(IMAGES / ("group_%s.png" % key))
        print("group icon", key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", default="E:/SteamLibrary/steamapps/common/Helldivers 2")
    args = parser.parse_args()

    dump(args.game_dir)
    dump_strat_icons(args.game_dir)
    render_all()

    art = cover()
    box = art.getbbox()
    if box:
        art = art.crop(box)
    # 留出边框的位置
    avail = 1024 - 2 * 80
    scale = avail / max(art.size)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))),
                     Image.LANCZOS)
    square = Image.new("RGBA", (1024, 1024), BG + (255,))
    square.alpha_composite(art, ((1024 - art.width) // 2, (1024 - art.height) // 2))
    d = ImageDraw.Draw(square)
    d.rounded_rectangle((24, 24, 1000, 1000), radius=72, outline=BORDER, width=8)
    square.convert("RGB").save(ROOT / "thumbnail.png")

    group_icons()
    preview()
    print("thumbnail:", ROOT / "thumbnail.png")


if __name__ == "__main__":
    main()
