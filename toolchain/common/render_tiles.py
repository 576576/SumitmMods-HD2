#!/usr/bin/env python3
"""把游戏里的地雷 unit 渲染成倾斜 3D 图（透明底），供 make_icons 拼进选项图标。

一次性素材工具，不进构建流程：需要游戏目录与 toolchain/rawdump.exe。
产出 images/model_<key>.png，make_icons 见到就拿来用，没有就退回画太阳。

用法：python toolchain/common/render_tiles.py [--game-dir "E:/.../Helldivers 2"] [--keep]
"""

import argparse
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

ROOT = paths.mod("Mines-Highlight")
IMAGES = ROOT / "images"
GENERAL = paths.TOOLCHAIN
RAWPUMP = paths.RAWDUMP
TYPE_UNIT = "0xE0A48D0BE9A7453F"

import bug_patch as bp
import mine_patch as mp

# 图标里画的是游戏原版模型（不是因为替换后的发光外壳），这样形状最好认。
UNITS = {
    "contact": "0xee89cc10d80b6afb",
    "incendiary": "0xe49a5ae078aad407",
    "tank": "0x032489cf1d439bba",
    "gas": "0x693028644d27ffef",
    "thermite": "0xee4c107b941ab7f4",
    "decoy": "0xa20683199dfc19e8",
    "caltrops": "0xc0b4502e67c7c867",
    "bot": "0x946f4ee6c568bc37",
    "bug": "0xd31decfb096403cc",
}

SUPERSAMPLE = 1024
OUTPUT = 512
YAW = -32.0
PITCH = 34.0          # 俯视角；顶面朝观察者，不是从下往上看
FRAME = 0.86          # 模型占画布的比例，按投影后的外框算，各张大小一致
AMBIENT = 0.19
SHEEN = np.array([0.88, 0.95, 1.0], dtype=np.float32)
FRONT_ALPHA = 0.74
BACK_ALPHA = 0.52
LIGHT = np.array([-0.40, 0.58, 0.72])
FILL = np.array([0.62, 0.34, 0.10])


def position_offset(items):
    """只累加到位置属性为止：机器人地雷的布局项总长和 stride 对不上，不能要求整体相等。"""
    at = 0
    for type_id, fmt, layer in items:
        if type_id == bp.ITEM_POSITION and layer == 0:
            return at, fmt
        at += bp.FORMAT_SIZE[fmt]
    raise SystemExit("layout has no position attribute")


def primary_meshes(main):
    """每个 LOD 组最细那一级的 mesh；没有 LOD 表就是全部。

    这些 unit 的每一级 LOD 都是完整外壳，几级同时画会互相穿插（孢囊雷的触须因此糊成一团）。
    """
    groups = bp.lod_groups(main)
    if not groups:
        return None
    keep = set()
    for group in groups:
        if group[1]:
            keep.update(group[1][0][2])
    return keep


def unit_triangles(main, gpu):
    """可见本体的三角形（跳过阴影/碰撞/踩踏代理与粗 LOD），返回 (n, 3, 3) float32。"""
    thins, fulls = bp.material_map(main)
    keep = {t for t, f in zip(thins, fulls) if f not in mp.PROXY_FULLS}
    primary = primary_meshes(main)
    layouts = bp.layout_blocks(main)
    per_mesh = []
    for index, mesh in enumerate(bp.mesh_infos(main)):
        if primary is not None and index not in primary:
            continue
        if not (set(bp.mesh_materials(main, mesh)) & keep):
            continue
        layout = layouts[bp.mesh_layout(main, mesh)]
        items = bp.layout_items(main, layout)
        stride = bp.u32(main, layout + bp.LAYOUT_VERTEX_STRIDE)
        nverts = bp.u32(main, layout + bp.LAYOUT_NUM_VERTICES)
        vbase = bp.u32(main, layout + bp.LAYOUT_VERTEX_OFFSET)
        ioff = bp.u32(main, layout + bp.LAYOUT_INDEX_OFFSET)
        pos_at, fmt = position_offset(items)
        if fmt != 2:
            raise SystemExit("unsupported position format %d" % fmt)
        block = np.frombuffer(gpu, dtype="<f4", count=nverts * stride // 4, offset=vbase)
        pos = block.reshape(nverts, stride // 4)[:, pos_at // 4: pos_at // 4 + 3]
        chunks = []
        for group in bp.mesh_groups(main, mesh):
            count = group[4]
            if count < 3:
                continue
            idx = np.frombuffer(gpu, dtype="<u2", count=count, offset=ioff + group[3] * 2)
            idx = idx[:(count // 3) * 3].reshape(-1, 3).astype(np.int32)
            chunks.append(pos[idx])
        per_mesh.extend(chunks)
    if not per_mesh:
        raise SystemExit("unit has no visible triangles")

    # 机器人地雷自带一根 4.8 m 的光柱（单独一组三角形）：留着它整张图会被压到看不见雷本体。
    # AABB 在这批 unit 上多数是 0，只能按三角形实际高度判断。
    spans = [float(t[:, :, 2].max() - t[:, :, 2].min()) for t in per_mesh]
    median = sorted(spans)[(len(spans) - 1) // 2]
    kept = [t for t, span in zip(per_mesh, spans) if span <= max(median * 4.0, 1e-6)]
    return np.concatenate(kept or per_mesh, axis=0)


def transform(tris):
    """归一化 + 绕 Z 旋转（yaw）+ 俯视（pitch 取负号让顶面朝观察者），返回 (n, 3, 3)。"""
    flat = tris.reshape(-1, 3)
    lo, hi = flat.min(axis=0), flat.max(axis=0)
    center = (lo + hi) / 2.0
    extent = float((hi - lo).max()) or 1.0
    v = (flat - center) / extent * 2.0

    ya, pa = np.radians(YAW), -np.radians(PITCH)
    cz, sz = np.cos(ya), np.sin(ya)
    rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    cx, sx = np.cos(pa), np.sin(pa)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    v = v @ rz.T @ rx.T
    return v.reshape(-1, 3, 3)


def shade(normals, facing, base):
    """漫反射 + 硬高光 + 掠射反光，做成有光泽的塑料/玻璃感。"""
    base = np.asarray(base, dtype=np.float32)
    if base.ndim == 1:
        base = np.broadcast_to(base, (len(normals), 3))
    n = np.where(facing[:, None], normals, -normals)
    light = LIGHT / np.linalg.norm(LIGHT)
    view = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    half = light + view
    half /= np.linalg.norm(half)

    lambert = np.clip(n @ light.astype(np.float32), 0.0, 1.0)
    fill = np.clip(n @ FILL.astype(np.float32), 0.0, 1.0)
    spec = np.clip(n @ half.astype(np.float32), 0.0, 1.0)
    gloss = spec ** 26.0 * 1.05 + spec ** 5.0 * 0.20
    fresnel = (1.0 - np.clip(n @ view, 0.0, 1.0)) ** 2.0

    color = base * (AMBIENT + (1.0 - AMBIENT) * lambert + 0.10 * fill)[:, None]
    color += SHEEN[None, :] * (gloss + fresnel * 0.30)[:, None]
    color += base * (fresnel * 0.55)[:, None]
    return np.clip(color, 0.0, 1.0)


def rasterize(sx, sy, depth, color, alpha_value, size):
    """三角形光栅化进 (rgb, alpha)；返回本层的结果，不做合成。"""
    img = np.zeros((size, size, 3), np.float32)
    alpha = np.zeros((size, size), np.float32)
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
        alpha[py0:py1 + 1, px0:px1 + 1][win] = alpha_value
    return img, alpha


def clip_center(tris, ratio):
    """只保留沿水平长轴的中间一段，用来切掉可展开的侧翼。"""
    flat = tris.reshape(-1, 3)
    lo, hi = flat.min(axis=0), flat.max(axis=0)
    span = hi - lo
    axis = 0 if span[0] >= span[1] else 1
    center = (lo[axis] + hi[axis]) / 2.0
    reach = span[axis] * ratio
    centroids = tris[:, :, axis].mean(axis=1)
    keep = np.abs(centroids - center) <= reach
    return tris[keep] if keep.any() else tris


def render(main, gpu, tint, frame=FRAME, clip=None):
    tris = unit_triangles(main, gpu)
    if clip is not None:
        tris = clip_center(tris, clip)
    tris = transform(tris)
    base = np.array(tint, dtype=np.float32) / 255.0
    size = SUPERSAMPLE

    px = tris[:, :, 0]
    py = -tris[:, :, 2]
    # 刺球按外框缩会显得比别的雷小一圈，单独放大到接近满幅
    scale = size * frame / max(float(px.max() - px.min()), float(py.max() - py.min()), 1e-9)
    sx = (px - (px.min() + px.max()) / 2.0) * scale + size / 2.0
    sy = (py - (py.min() + py.max()) / 2.0) * scale + size / 2.0
    sz = tris[:, :, 1]

    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    facing = normal[:, 1] > 0.0
    color = shade(normal, facing, base)

    # 背面先画一层浅的：正面半透明时能透出内部结构，看起来像有厚度的玻璃。
    back_img, back_a = rasterize(sx[~facing], sy[~facing], sz[~facing],
                                 color[~facing] * 0.80, BACK_ALPHA, size)
    front_img, front_a = rasterize(sx[facing], sy[facing], sz[facing],
                                   color[facing], FRONT_ALPHA, size)

    alpha = front_a + back_a * (1.0 - front_a)
    safe = np.maximum(alpha, 1e-6)[:, :, None]
    img = front_img * front_a[:, :, None] + back_img * back_a[:, :, None] * (1.0 - front_a)[:, :, None]
    img = img / safe

    rgb = Image.fromarray((np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")
    a = Image.fromarray((np.clip(alpha, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "L")
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out.resize((OUTPUT, OUTPUT), Image.LANCZOS)


# 刺球按外框缩会显得比别的雷小一圈，单独放大到接近满幅
FRAME_OVERRIDE = {
    "bug": 0.97,
}


def dump_units(game_dir, work):
    work.mkdir(parents=True, exist_ok=True)
    cmd = [str(RAWPUMP), game_dir, str(work), TYPE_UNIT] + list(UNITS.values())
    print("dump:", " ".join(cmd[:2]), "...", len(UNITS), "units")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout[-800:])
        raise SystemExit("rawdump failed")


def unit_from_patch(zip_name, stem, unit_type=bp.TYPE_UNIT):
    """从 source_*.zip 里的 patch 取 unit 的 main/gpu：机器人地雷的 GPU 不在索引里，只能这么拿。"""
    import zipfile
    with zipfile.ZipFile(ROOT / "tools" / zip_name) as z:
        patch = z.read(stem)
        gpu = z.read(stem + ".gpu_resources")
    for row in bp.rows_of(patch):
        if row[1] == unit_type:
            return patch[row[2]:row[2] + row[7]], gpu[row[4]:row[4] + row[9]]
    raise SystemExit("no unit row in %s" % zip_name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default="E:/SteamLibrary/steamapps/common/Helldivers 2")
    ap.add_argument("--work", default=str(GENERAL / "out" / "_render_units"))
    args = ap.parse_args()

    work = Path(args.work)
    dump_units(args.game_dir, work)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import make_icons

    IMAGES.mkdir(parents=True, exist_ok=True)
    for key, rid in UNITS.items():
        tag = "0x" + rid[2:]
        main_path = work / (tag + ".main")
        gpu_path = work / (tag + ".gpu")
        if not main_path.is_file():
            raise SystemExit("missing %s" % main_path)
        if gpu_path.is_file():
            main, gpu = main_path.read_bytes(), gpu_path.read_bytes()
        else:
            main, gpu = unit_from_patch("source_bot.zip", "9ba626afa44a3aa3.patch_87")
        img = render(main, gpu, make_icons.COLORS[key],
                     FRAME_OVERRIDE.get(key, FRAME))
        out = IMAGES / ("model_%s.png" % key)
        img.save(out)
        print("render:", key, img.size, out.name)


if __name__ == "__main__":
    main()
