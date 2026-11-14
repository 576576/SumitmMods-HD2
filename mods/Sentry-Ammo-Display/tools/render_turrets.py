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
import glbskin  # noqa: E402
import render_tiles as rt  # noqa: E402

RAWPUMP = paths.RAWDUMP
FILEDIVER = paths.FILEDIVER
WORK = REPO / "build" / "_turrets"
STRAT_ROOT = REPO / "build" / "_strat"
GAME_DIR = Path("E:/SteamLibrary/steamapps/common/Helldivers 2")
STRAT_DIR = STRAT_ROOT / "content" / "ui" / "shared" / "stratagem"
# 没有纹理版的战备图标（E/GL-21、武装补给舱增强）由 make_icons.py 从 xaml 光栅化，
# 与纹理版同格式，先在这里找，找不到再回落到原版纹理。
ICONS_DIR = IMAGES / "icons"
STRAT_ARCHIVE = "6a3eecfddce28fe8"
STRAT_PATH = "content/ui/shared/stratagem/"
TYPE_UNIT = "0xE0A48D0BE9A7453F"
PREFIX = "content/fac_helldivers/hellpod/"

CLEAR = (0, 0, 0, 0)
# 封面与选项图共用同一套底色，和游戏里的暗蓝灰 UI 一致。
BG = (13, 19, 27)
BORDER = (42, 60, 82)

Spec = namedtuple("Spec", "path shadow light clip")

# 三辆 FRV 的整车与挂载件：补给型（补给架 + 自行炮塔）、普通型（重机枪）、重型型（喷火器）。
FRV_MG_UNIT = "content/fac_helldivers/vehicles/frv/armaments/frv_mg/frv_mg"
FRV_FLAME_UNIT = ("content/fac_helldivers/vehicles/frv_heavy/armaments/"
                  "frv_flamethrower/frv_flamethrower")
FRV_MG_CAR = "content/fac_helldivers/vehicles/frv/frv"
FRV_FLAME_CAR = "content/fac_helldivers/vehicles/frv_heavy/frv_flamer"

# 暗部 / 亮部两端：渲染出来是灰阶，按这两端做渐变映射上色。
# 3D 模型沿用按类型分色，型号一眼能分开；2D 战备图标的墨色另算（见 BAR_SPECS）。
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
                  (0.28, 0.22, 0.11), (0.90, 0.82, 0.56), None),
    "car": Spec("content/fac_helldivers/vehicles/frv_supply/frv_supply",
                (0.19, 0.23, 0.30), (0.84, 0.91, 1.00), None),
    "frvmg": Spec(FRV_MG_CAR, (0.19, 0.23, 0.30), (0.84, 0.91, 1.00), None),
    "frvflame": Spec(FRV_FLAME_CAR, (0.19, 0.23, 0.30), (0.84, 0.91, 1.00), None),
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
# 另外两辆 FRV 的挂载件：普通型的重机枪与重型型的喷火器，都装在同一个 gun_mount 上。
# 补给舱本体。落地形态不能从 gpu 拿（那份只有绑定姿态），
# 要按 unit 自己的 deploy 动画求蒙皮，见 rack_open()。
RACK_UNIT = "content/fac_helldivers/hellpod/ammo_rack/ammo_rack"
# 舱内的四只补给包，独立 unit，挂点是与舱体同名的骨骼 attach_01..04。
PACK_UNIT = "content/fac_helldivers/hellpod/ammo_rack/ammo_box"
PACK_BONES = ("attach_01", "attach_02", "attach_03", "attach_04")
# 取动画的哪一帧：外壳沉到位在 0.75，四组托盘 0.65 起才往外滑、0.9 到最外并转
# 20°。游戏里落地后是合拢的（盒子收在窗口内），所以取托盘未动、外壳已沉下的 0.65。
RACK_POSE = 0.65
# 落地线：deploy 末帧外套筒沉到地面以下，绑定姿态的最低点是 0.09。
# 舱体按这条线裁断，底下补一块封盖，底口半径取裁剪处外壳的实测值。
RACK_GROUND = 0.09
RACK_FLOOR_RADIUS = 0.545
# 补给型 FRV 车斗上的补给架：顶框前沿 (0, 0.043, 0.056) 对到车身的 gun_mount 骨骼。
RACK_UNIT_FRV = ("content/fac_helldivers/vehicles/frv_supply/armaments/"
                 "frv_supply_rack/frv_supply_rack")
RACK_ANCHOR = (0.0, 0.043, 0.056)
# 顶面上的炮塔安装盘圆心。盘上两圈同心圆半径 0.316 / 0.230，炮塔底座半径 0.30，
# 正落在盘内；盘面高 0.024，与炮塔底面的 0.026 贴平。
RACK_SEAT = (0.0, 0.2959, 0.024)
# frv_supply 的 gun_mount 换到 unit 坐标：GLB (0, 1.6464, 1.1988) 按 (x, -z, y)
# 换轴，再整体减掉 g_body 节点平移 (0, 0.0282, 0.5628)。
CAR_GUN_MOUNT = (0.0, -1.227, 1.083)
# 炮塔按安装盘圆心落位，不按 gun_mount：架子按 RACK_ANCHOR 摆好之后，盘心在车坐标
# 里的位置 = gun_mount + (RACK_SEAT - RACK_ANCHOR)。
CAR_TURRET_AT = tuple(m + s - a for m, s, a in
                      zip(CAR_GUN_MOUNT, RACK_SEAT, RACK_ANCHOR))
MOUNTS = {
    # wall: [(unit 路径, 挂点世界坐标, 对位点)]
    # 发射器挂在墙的 attach_gun 骨骼（世界平移 (0, -0.052, 1.253)，旋转为单位矩阵）。
    # 弹链盒挂在武器的 attach_mag 骨骼，枪内坐标 (-0.023, 0.292, 0.146)，
    # 叠上 attach_gun 的平移就是 (-0.023, 0.240, 1.399)。
    "wall": [
        (GL_UNIT, (0.0, -0.052, 1.253), (0.0, 0.0, 0.0)),
        (MAG_UNIT, (-0.023, 0.240, 1.399), (0.0, 0.0, 0.0)),
    ],
    # 三辆车的 gun_mount 在 GLB 里都是 (0, 1.646, 1.199)，但三辆车 unit 的原点
    # 各不相同（包围盒中心不一样），换回 unit 坐标要按各自车身的包围盒对齐：
    # GLB +y 是车高、+z 是车尾。
    #   frv_supply  z -1.056..1.056  → (0, -1.227, 1.083)
    #   frv         z -1.524..0.621  → (0, -1.275, 0.615)
    #   frv_heavy   z -1.084..1.084  → (0, -1.475, 1.067)
    "car": [
        (RACK_UNIT_FRV, CAR_GUN_MOUNT, RACK_ANCHOR),
        (TURRET_UNIT, CAR_TURRET_AT, (0.0, 0.0, 0.0)),
    ],
    # 重机枪与喷火器的 boss 骨骼就在 unit 原点、姿态与 gun_mount 完全一致，
    # 所以原点直接落到挂点上，不做任何偏移；枪身自然向前伸。
    "frvmg": [(FRV_MG_UNIT, (0.0, -1.275, 0.615), (0.0, 0.0, 0.0))],
    "frvflame": [(FRV_FLAME_UNIT, (0.0, -1.475, 1.067), (0.0, 0.0, 0.0))],
}

# 挂载件单独配色，键是 MOUNTS 里的序号（1 = 补给架/发射器，2 = 炮塔/弹链盒）。
# 弹链盒是弹药箱，比掩体暗且偏黄铜；和墙用同一套灰就会连轮廓一起糊在墙顶豁口里。
# 补给车是冷灰的车身，只有炮塔染成暖金。
SUPPLY_TINT = ((0.40, 0.19, 0.02), (1.00, 0.76, 0.30))
# 舱内的补给包是一摞橄榄色弹药箱，比卡其色的罐体暗一档，
# 免得开口里看着像一片空腔。
CARGO_TINT = ((0.14, 0.14, 0.09), (0.62, 0.60, 0.44))
# 移动阵地组两辆车用各自的炮口色区分：重机枪偏青、喷火器偏橙。
MG_TINT = ((0.05, 0.26, 0.22), (0.42, 0.94, 0.84))
FLAME_TINT = ((0.40, 0.12, 0.02), (1.00, 0.52, 0.16))
MOUNT_TINTS = {
    "wall": {2: ((0.22, 0.21, 0.16), (0.84, 0.79, 0.65))},
    "crate": {1: CARGO_TINT, 2: SUPPLY_TINT},
    "car": {2: SUPPLY_TINT},
    "frvmg": {1: MG_TINT},
    "frvflame": {1: FLAME_TINT},
}

# 载具与补给舱是大块薄壳，默认的半透明会把背面重叠面透出来，看着像缺件。
SOLID_DEPTH = {"car": 0.40, "crate": 0.16, "frvmg": 0.40, "frvflame": 0.40}

# 三辆 FRV 的挂载件都在车斗后段，正面取景看不到，统一转半圈让车尾朝前。
SPEC_YAW = {"car": 180.0, "frvmg": 180.0, "frvflame": 180.0}

# 载具整车太占地方，炮塔与补给仓全在车尾：沿车长方向切掉前段，只留驾驶室后半与车斗。
# 值是保留部分的 y 上限（unit 坐标，+y 是车头）。
FRONT_CUT = 0.55
# 需要切的车：三辆 FRV 的挂载件都在车尾。
CUT_SPECS = {"car", "frvmg", "frvflame"}

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
                   | {PACK_UNIT})
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


def visible_primary(main, primary, visible, bp):
    """primary_meshes 之外再按材质去重：同一套材质的多个 LOD 只会列进来一半，
    整车（frv）就会把两档 LOD 一起画出来，成了一堆互相穿插的三角面。

    材质相同的候选里只留三角形最多的那一条；材质不同的（比如炮塔本体 + 底座）
    照旧全部保留。
    """
    best = {}
    for index, mesh in enumerate(bp.mesh_infos(main)):
        if primary is not None and index not in primary:
            continue
        mats = frozenset(bp.mesh_materials(main, mesh))
        if not (mats & visible):
            continue
        tris = sum(g[4] // 3 for g in bp.mesh_groups(main, mesh))
        if mats not in best or tris > best[mats][1]:
            best[mats] = (index, tris)
    return {index for index, _ in best.values()}


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

    chosen = visible_primary(main, primary, visible, bp)

    parts = []
    for index, mesh in enumerate(bp.mesh_infos(main)):
        if index not in chosen:
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


def _to_unit(tris):
    """glb 是 y 向上，unit 是 z 向上：绕 x 转 90° 再换轴。"""
    return np.stack([tris[..., 0], -tris[..., 2], tris[..., 1]], axis=-1)


Z_TO_Y = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)


def _unit_matrix(matrix):
    """同一个换轴作用在矩阵上，让骨骼的位姿能直接摆 unit 坐标的几何。"""
    return Z_TO_Y @ matrix @ Z_TO_Y.T


def _floor_cap(radius, z, sides=48):
    """把裁开的底口封成一块平盖：游戏里这一段埋在地下，见不到断面。"""
    angle = np.linspace(0.0, 2.0 * math.pi, sides, endpoint=False)
    ring = np.stack([radius * np.cos(angle), radius * np.sin(angle),
                     np.full(sides, z)], axis=-1)
    hub = np.repeat(np.array([[[0.0, 0.0, z]]]), sides, axis=0)
    ahead = np.roll(ring, -1, axis=0)
    return np.concatenate([hub, ring[:, None], ahead[:, None]], axis=1)


def rack_open():
    """补给舱落地形态：外壳沉到地面以下，窗口板合拢，补给包收在窗口里。

    unit 的 gpu 只有绑定姿态（还在天上），要按 deploy 动画求蒙皮。
    取 RACK_POSE 那一帧：再往后托盘和支架会推出去并外转，那是落地瞬间的动作，
    游戏里不保留。
    返回 (三角形, 四只补给包的挂点矩阵, 炮塔挂点)，坐标系是 unit 的 z 向上。
    """
    folder = WORK / "_ammorack"
    glb = folder / "combined_unit.combined.glb"
    if not glb.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(FILEDIVER), "--gamedir", str(GAME_DIR), "-o", str(folder),
                        "-i", "*ammo_rack/ammo_rack*", "--unit-single-file",
                        "--model-format", "glb", "--model-enable-animations"], check=True)
    gltf, blob = glbskin.load(glb)
    anim = [i for i, a in enumerate(gltf["animations"]) if a["name"].endswith("/deploy")][0]
    t = glbskin.duration(gltf, blob, anim) * RACK_POSE
    pose = glbskin.Pose(gltf, blob, anim, t)
    tris = _to_unit(np.concatenate([v[tri] for v, tri, _ in
                                    glbskin.skinned(gltf, blob, anim, t)]))
    # 外套筒在 deploy 里沉到地面以下（游戏里埋进地里），只保留地面线上面的部分；
    # 底口封一块平盖，免得断面透出空腔。
    tris = tris[~(tris[..., 2] < RACK_GROUND).all(axis=1)]
    tris[..., 2] = np.maximum(tris[..., 2], RACK_GROUND)
    tris = np.concatenate([tris, _floor_cap(RACK_FLOOR_RADIUS, RACK_GROUND)])
    packs = [_unit_matrix(pose.bone(name)) for name in PACK_BONES]
    mount = _unit_matrix(pose.bone("attach_top"))[:3, 3]
    return tris, packs, mount


def rack_packs(packs):
    """四只补给包按 attach_01..04 落位，箱背贴挂点、不倾斜。

    骨骼空间是 y 向上、z 朝内，unit 几何是 z 向上、y 朝外，所以几何要先绕 x
    转 90° 才进骨骼坐标系。挂点就是盒子的安装点，照搬即可：盒子落在窗口里，
    外面那层支架板正好压在盒面上。
    """
    box = turret_tris(stem_of(PACK_UNIT))
    box = box - np.array([0.0, 0.0, box.reshape(-1, 3)[:, 2].min()])
    return np.concatenate([place(box, matrix @ Z_TO_Y) for matrix in packs])



def crate_assembly():
    """武装补给舱：舱体取 deploy 的落地帧，补给包挂 attach_01..04，炮塔落 attach_top。

    返回 (三角形, 组号)：0 舱体、1 补给包、2 炮塔。
    """
    body, packs, mount = rack_open()
    groups = [(body, 0), (rack_packs(packs), 1)]

    turret = np.eye(4)
    turret[:3, 3] = mount
    groups.append((place(turret_tris(stem_of(TURRET_UNIT)), turret), 2))

    tris = np.concatenate([t for t, _ in groups])
    tags = np.concatenate([np.full(len(t), g, np.uint8) for t, g in groups])
    return tris, tags


def cut_front(tris, tags, limit):
    """切掉 y 超过 limit 的三角形，只留车尾。

    按三角形整条判：三个顶点都在切面之外的才删，切面处的边界留在原网格线上，
    不会切出一圈悬空的半条三角形。
    """
    keep = ~(tris[..., 1] > limit).all(axis=1)
    return tris[keep], (None if tags is None else tags[keep])


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

        if key in CUT_SPECS:
            tris, tags = cut_front(tris, tags, FRONT_CUT)

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


def whole_span(art):
    """按整幅外框的边缘排间距：车和舱这种大块实体不能按底座算，
    车身比轮胎着地面宽，按底座排会让后一件压在车身上。"""
    return 0, art.width - 1


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


def icon_file(stem):
    """图标文件：先找 tools/make_icons.py 生成的，再找原版纹理。"""
    for base in (ICONS_DIR, STRAT_DIR):
        path = base / (stem + ".png")
        if path.is_file():
            return path
    raise SystemExit("缺少图标 %s" % stem)


def unit_mark(stem, colour, flame=(0.96, 0.98, 1.00), size=0):
    """战备图标文件里的两路墨迹：红通道是本体，绿通道是火焰。

    本体填 colour、火焰填 flame（截图里火焰是白的），其余透明。
    """
    src = Image.open(icon_file(stem)).convert("RGB")
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
             ink=(0.20, 0.85, 0.36), label=None, symbol=None):
    """游戏内的弹药显示：四角框只圈左侧单位图标，右侧大字与其下的分段横条。

    比例取自截图里火焰哨戒那一条（以数字高度为 1）：图标高 1.04、图标与数字
    间距 1.0、条长 1.18 且水平居中于数字、条高 0.14、数字与条间距 0.22、
    段间为段宽的 0.29。label 给定时用它代替数字（热量百分比、倒计时）。
    symbol 给定时用它填图标的符号层，本体仍用 ink。
    """
    font = hud_font(digit)
    label = str(rounds) if label is None else label
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), label, font=font, stroke_width=2)
    text_w, text_h = box[2] - box[0], box[3] - box[1]

    mark = unit_mark(icon, ink, symbol or (0.96, 0.98, 1.00), size=int(text_h * 1.04))
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


# 封面摆位：1024 画布上的左上角坐标与像素尺寸，顺序即绘制顺序。
# 环绕式：外圈一圈围住，内圈放带弹药条的火焰与反坦克。
# 上排由 top_row() 排，这里只列其余元素。
COVER_LAYOUT = [
    ("tesla", 846, 367),
    ("mortar", 77, 337),
    ("flame", 269, 386),
    ("antitank", 517, 425),
    ("hmg", 56, 714),
    ("wall", 832, 704),
    ("car", 434, 827),
    ("frvmg", 253, 827),
    ("frvflame", 627, 827),
]

# 上排：炮塔按底座像素宽统一，炮管越长按高度缩放的底座显得越小，
# 摆成一排会一大一小；间距也按底座边缘算，免得炮管长的把空档撑开。
# 补给舱是罐体，按罐体自身缩放，只跟随底座间距排位。
TOP_ROW = ("crate", "mg", "gatling", "rocket", "laser", "autocannon")
TOP_BASE = 96
TOP_GAP = 46
TOP_SINK = 36  # 两端各下沉一档，中间四台底边同高
TOP_LINE = 264  # 中间四台的底座底边


def top_row(parts, mid=512):
    """上排按底座边缘等距排开，中间那道缝压在 mid 上；中间四台底边同高，
    只有两端各下沉一档。"""
    arts = [parts[k] for k in TOP_ROW]
    placed, _ = layout_row(arts, TOP_GAP, span=base_span)
    # 外框比底座宽（炮管伸出），按外框居中会把整排推偏；左右两半的底座宽度
    # 也不等，所以以中间两台之间的那道缝为基准。
    spans = [(x + base_span(art)[0], x + base_span(art)[1]) for x, art in placed]
    half_n = len(spans) // 2
    seam = (spans[half_n - 1][1] + spans[half_n][0]) / 2
    left = int(round(mid - seam))
    last = len(TOP_ROW) - 1
    out = []
    for i, ((x, art), key) in enumerate(zip(placed, TOP_ROW)):
        sink = TOP_SINK if i in (0, last) else 0
        out.append((key, left + x, TOP_LINE + sink - art.height))
    return out


def cover_parts():
    """各元素按原尺寸画好，裁到可见外框。"""
    p = {}
    p["tesla"] = scaled(IMAGES / "model_tesla.png", 305)
    p["mortar"] = mortar_column(305)
    p["flame"] = with_bar(scaled(IMAGES / "model_flame.png", 240),
                          ammo_bar(rounds=760, capacity=900, digit=52))
    p["antitank"] = with_bar(scaled(IMAGES / "model_antitank.png", 250, by="max"),
                             ammo_bar(rounds=30, capacity=30, digit=52, icon=AT_ICON),
                             pad=30)
    p["crate"] = scaled(IMAGES / "model_crate.png", 206)
    for key in TOP_ROW:
        if key != "crate":
            p[key] = scaled_by_base(IMAGES / ("model_%s.png" % key), TOP_BASE)
    # 墙裁掉侧翼后只有 1.26 m 宽，按统一高度摆会明显小一圈，单独放高一点。
    for key in ("hmg", "wall"):
        p[key] = scaled(IMAGES / ("model_%s.png" % key), 188 if key == "wall" else 178)
    for key in ("car", "frvmg", "frvflame"):
        p[key] = scaled(IMAGES / ("model_%s.png" % key), 158)
    out = {}
    for key, img in p.items():
        box = img.getbbox()
        out[key] = img.crop(box) if box else img
    return out


def cover_items(parts=None):
    """封面的完整摆位 [(key, x, y)]，顺序即绘制顺序。上排那道中缝对齐整幅
    外框的水平中点——成品裁切后居中，中缝才跟着落在正中。上排炮管可能伸出
    外框，所以按带上排的外框迭代到稳定。"""
    if parts is None:
        parts = cover_parts()
    frame = Image.new("RGBA", (1024, 1024), CLEAR)
    for key, x, y in COVER_LAYOUT:
        frame.alpha_composite(parts[key], (x, y))

    mid, row = 512, top_row(parts, 512)
    for _ in range(12):
        probe = frame.copy()
        for key, x, y in row:
            probe.alpha_composite(parts[key], (x, y))
        box = probe.getbbox()
        mid_new = (box[0] + box[2]) / 2
        if abs(mid_new - mid) < 0.5:
            break
        mid, row = mid_new, top_row(parts, mid_new)
    return COVER_LAYOUT + row


def cover():
    parts = cover_parts()
    canvas = Image.new("RGBA", (1024, 1024), CLEAR)
    for key, x, y in cover_items(parts):
        canvas.alpha_composite(parts[key], (x, y))
    return canvas


def thumbnail(art, pad=80):
    """把封面收成方形缩略图：窗口以内容外框的中心为准，等比缩到留边。"""
    box = art.getbbox()
    if not box:
        return art
    side = max(box[2] - box[0], box[3] - box[1])
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    left, top = round(cx - side / 2), round(cy - side / 2)
    art = art.crop((left, top, left + side, top + side))
    size = max(1, 1024 - 2 * pad)
    art = art.resize((size, size), Image.LANCZOS)
    square = Image.new("RGBA", (1024, 1024), BG + (255,))
    square.alpha_composite(art, ((1024 - size) // 2, (1024 - size) // 2))
    d = ImageDraw.Draw(square)
    d.rounded_rectangle((24, 24, 1000, 1000), radius=72, outline=BORDER, width=8)
    return square


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
    "mounted": ("frvmg", "frvflame"),
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
    "mounted": 0.52,
}
GROUP_GLOW = {
    "ammo": (255, 122, 61),
    "mortar": (176, 190, 208),
    "laser": (255, 196, 64),
    "tesla": (86, 214, 255),
    "emplacement": (110, 226, 196),
    "supply": (255, 186, 96),
    "mounted": (128, 226, 255),
}


def rows_art(rows, gap, row_gap, span=base_span):
    """逐行按 span 给出的边缘等间距排开，行内居中，行底边对齐。

    和封面底排同一套对齐方式：按底座边缘排间距，炮管探到邻座上方也不会显得挤。
    每行先单独画一条，再按实际像素框居中：按 layout_row 的累计宽度居中会被
    长炮管带偏，一行两台的组会整体偏到一侧。
    """
    laid = [layout_row(arts, gap, span) for arts in rows]
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
                scaled(IMAGES / "model_crate.png", 420)]
        art = rows_art([arts], 50, 0, span=whole_span)
        canvas = art
        unit = max(a.height for a in arts)
    elif key == "mounted":
        # 两辆车并排，按整幅外框排间距：车身比轮胎着地面宽得多。
        arts = [scaled(IMAGES / "model_frvmg.png", 500),
                scaled(IMAGES / "model_frvflame.png", 500)]
        art = rows_art([arts], 40, 0, span=whole_span)
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
# 第三排是补给舱与三辆 FRV，车尾的炮位就是显示位置。
PREVIEW_MOUNTED = ("crate", "car", "frvmg", "frvflame")

# 每个单位头顶的显示，参数与游戏内一致：单位图标、余量、容量、分段数、墨色、替代文本。
# 炮塔与阵地一律绿色，载具一律蓝色；余量都取满弹，条长才是整条。
AMMO_INK = (0.20, 0.85, 0.36)
VEHICLE_INK = (0.28, 0.62, 1.00)
# 补给舱图标本体是白色，炮塔用同一支绿，和别的炮塔图标连成一体。
POD_INK = (0.96, 0.98, 1.00)
BAR_SYMBOL = {"crate": AMMO_INK}
BAR_SPECS = {
    "mg": ("icon_strat_hud_defensive_turrets_machinegun", 150, 150, 4, AMMO_INK, None),
    "gatling": ("icon_strat_hud_defensive_turrets_gatling", 1200, 1200, 6, AMMO_INK, None),
    "rocket": ("icon_strat_hud_defensive_turrets_rocket", 24, 24, 4, AMMO_INK, None),
    "flame": ("icon_strat_hud_flamethrower_turret", 400, 400, 4, AMMO_INK, None),
    # 激光显示的是热量：满冷却时温度 0，百分比读 0%，条只剩底槽。
    "laser": ("icon_strat_hud_laser_cannon_turret", 0, 100, 4, AMMO_INK, "0%"),
    "autocannon": ("icon_strat_hud_defensive_turrets_autocannon", 60, 60, 4, AMMO_INK, None),
    "mortar": ("icon_strat_hud_defensive_turrets_mortar", 12, 12, 4, AMMO_INK, None),
    "mortar_gas": ("icon_strat_hud_defensive_turrets_mortar_gas", 12, 12, 4, AMMO_INK, None),
    "mortar_ems": ("icon_strat_hud_defensive_turrets_mortar_staticfield", 12, 12, 4, AMMO_INK, None),
    "tesla": ("icon_strat_hud_defensive_emplacements_teslatower", 60, 60, 4, AMMO_INK, "1:00"),
    "hmg": ("icon_strat_hud_defensive_turrets_manned_machinegun", 150, 150, 5, AMMO_INK, None),
    "antitank": ("icon_strat_hud_anti_tank_emplacement", 12, 12, 4, AMMO_INK, None),
    # E/GL-21 没有纹理版图标，用 xaml 光栅化的那张；显示的是炮塔弹药，不是剩余补给。
    "wall": ("wall", 24, 24, 4, AMMO_INK, None),
    "crate": ("ammo_pod", 150, 150, 4, POD_INK, None),
    "car": ("icon_strat_hud_vehicle_frv_supply", 150, 150, 4, VEHICLE_INK, None),
    "frvmg": ("icon_strat_hud_vehicle_frv_hmg", 100, 100, 4, VEHICLE_INK, None),
    "frvflame": ("icon_strat_hud_vehicle_frv_flamer", 800, 800, 4, VEHICLE_INK, None),
}
# 迫击炮三种型号竖排叠放，从上到下。
MORTAR_BARS = ("mortar_gas", "mortar", "mortar_ems")


def preview_bar(key, digit=24):
    icon, rounds, capacity, segments, ink, label = BAR_SPECS[key]
    bar = ammo_bar(rounds=rounds, capacity=capacity, segments=segments,
                   digit=digit, icon=icon, ink=ink, label=label,
                   symbol=BAR_SYMBOL.get(key))
    # 裁掉四周的透明留白，贴到模型顶边时才是真正的贴边，不会凭空多一截空隙
    box = bar.getbbox()
    return bar.crop(box) if box else bar


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


def mortar_stack(row_h=186, pad=10):
    """三种迫击炮的弹药条上下叠放，模型在下面，全部按底座中心对齐。

    三型迫击共用同一个模型，靠弹药条上的单位图标区分（毒气 / 常规 / 电磁），
    所以不再往炮身上叠效果符号。
    """
    bars = [preview_bar(key) for key in MORTAR_BARS]
    art = scaled(IMAGES / "model_mortar.png", row_h)
    base_l, base_r = base_span(art)
    centre = (base_l + base_r) // 2
    left = min(centre - b.width // 2 for b in bars)
    right = max(centre + (b.width + 1) // 2 for b in bars)
    art_left = centre - (base_l + base_r) // 2
    left = min(left, art_left)
    right = max(right, art_left + art.width)

    height = sum(b.height for b in bars) + pad * (len(bars) - 1) + 12 + art.height
    group = Image.new("RGBA", (right - left, height), CLEAR)
    y = 0
    for bar in bars:
        group.alpha_composite(bar, (centre - bar.width // 2 - left, y))
        y += bar.height + pad
    group.alpha_composite(art, (centre - (base_l + base_r) // 2 - left,
                                height - art.height))
    return group


def preview():
    """三排一字排开，底座间隙相等；不写战备编号，只留分隔线。

    中排两端各是一列：三发弹在上、迫击炮在下，特斯拉塔与之等高；
    中间三座阵地矮一档，全部底边对齐。第三排是补给舱与三辆载具，
    补给舱只有 1.73 m 高，按车辆的比例缩到相应大小。
    每个单位头顶叠一条与游戏内同款的显示，位置按各自的底座中心算。
    """
    row_h, margin, sep = 190, 34, 46

    top = [scaled(IMAGES / ("model_%s.png" % key), row_h) for key in PREVIEW_TOP]

    column = mortar_stack()
    column_h = column.height

    tesla = scaled(IMAGES / "model_tesla.png", column_h)
    middle = ([column]
              + [scaled(IMAGES / ("model_%s.png" % key), row_h) for key in PREVIEW_MIDDLE]
              + [tesla])
    # 补给舱按车高缩到 224：deploy 展开后模型比闭合态高一截，
    # 同高摆会让露出的舱内部分缩得太小。
    mounted = [scaled(IMAGES / ("model_%s.png" % key), 224 if key == "crate" else 232)
               for key in PREVIEW_MOUNTED]

    bars = {key: preview_bar(key) for key in BAR_SPECS}
    chip_h = max(bar.height for bar in bars.values())

    # 上排按底座排（炮管可以探到邻座上方），下面两排按整幅轮廓排，保证任何高度都不叠。
    top_row, top_w = layout_row(top, 58)
    mid_row, mid_w = layout_row(middle, 54, span=full_span)
    mnt_row, mnt_w = layout_row(mounted, 40, span=whole_span)

    widest = max(top_w, mid_w, mnt_w)
    width = widest + margin * 2
    height = margin * 2 + chip_h + row_h + sep + chip_h + column_h + sep + chip_h + 208
    img = Image.new("RGB", (width, height), BG)

    top_base = margin + chip_h + row_h
    mid_base = top_base + sep + chip_h + column_h
    mnt_base = mid_base + sep + chip_h + 208

    placed = []
    for row, keys, baseline, total_w in ((top_row, PREVIEW_TOP, top_base, top_w),
                                         (mid_row, (None,) + PREVIEW_MIDDLE + ("tesla",),
                                          mid_base, mid_w),
                                         (mnt_row, PREVIEW_MOUNTED, mnt_base, mnt_w)):
        left = margin + (widest - total_w) // 2
        for (x, art), key in zip(row, keys):
            xy = (x + left, baseline - art.height)
            img.paste(art, xy, art)
            if key:
                placed.append((key, xy, art))

    for key, (px, py), art in placed:
        bar = bars[key]
        base_left, base_right = base_span(art)
        cx = px + (base_left + base_right) // 2
        img.paste(bar, (cx - bar.width // 2, py - bar.height - 5), bar)

    d = ImageDraw.Draw(img)
    for y in (top_base + sep // 2, mid_base + sep // 2):
        d.line((margin, y, width - margin, y), fill=BORDER, width=2)
    img.save(ROOT / "preview.png", optimize=True)
    print("preview:", ROOT / "preview.png")


def group_icons():
    for key in GROUP_GLOW:
        group_icon(key).convert("RGB").save(IMAGES / ("group_%s.png" % key))
        print("group icon", key)


def main():
    global GAME_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", default=str(GAME_DIR))
    args = parser.parse_args()
    GAME_DIR = Path(args.game_dir)

    dump(args.game_dir)
    dump_strat_icons(args.game_dir)
    render_all()

    art = cover()
    thumbnail(art).convert("RGB").save(ROOT / "thumbnail.png")

    group_icons()
    preview()
    print("thumbnail:", ROOT / "thumbnail.png")


if __name__ == "__main__":
    main()
