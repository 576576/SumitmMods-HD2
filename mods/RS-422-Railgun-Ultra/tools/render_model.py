#!/usr/bin/env python3
"""把 RS-422 的武器 unit 渲成透明底产品图，供 make_icons 拼进封面。

一次性素材工具，不进构建流程：需要游戏目录与 toolchain/rawdump.exe。
着色与光栅化直接复用 toolchain/common 的 render_tiles，光泽和地雷图标一致。
产出 images/model_railgun.png（透明底、已裁到内容框）。

用法：python tools/render_model.py [--game-dir "E:/.../Helldivers 2"] [--yaw 270] [--pitch 0]
"""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parents[1]

sys.path.insert(0, str(REPO / "toolchain" / "common"))
import paths  # noqa: E402

RAWPUMP = paths.RAWDUMP
DUMP = REPO / "build" / "_render_railgun"
IMAGES = ROOT / "images"
TYPE_UNIT = "0xE0A48D0BE9A7453F"
STEM = "fac_helldivers_equipment_support_weapons_railgun_railgun"
UNIT = "content/fac_helldivers/equipment/support_weapons/railgun/railgun"

# 最细一级 LOD 是 mesh 9（本体）与 mesh 3（同外壳的第二组材质），更粗的几级只做备份。
MESHES = {9, 3}
YAW = 270.0
PITCH = 0.0
TINT = (150, 160, 175)
SUPERSAMPLE = 1024
FRAME = 0.97


def render_tiles():
    """按路径加载 toolchain/common 里的渲染器，借它的着色与光栅化。"""
    path = paths.COMMON / "render_tiles.py"
    if not path.is_file():
        raise SystemExit("找不到 %s" % path)
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("mines_render_tiles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dump(game_dir):
    if (DUMP / (STEM + ".main")).is_file() and (DUMP / (STEM + ".gpu")).is_file():
        return
    DUMP.mkdir(parents=True, exist_ok=True)
    cmd = [str(RAWPUMP), game_dir, str(DUMP), TYPE_UNIT, UNIT]
    print("dump:", " ".join(cmd[2:]))
    if subprocess.run(cmd, capture_output=True, text=True).returncode != 0:
        raise SystemExit("rawdump 失败")


def triangles(bp, rt, meshes):
    main = (DUMP / (STEM + ".main")).read_bytes()
    gpu = (DUMP / (STEM + ".gpu")).read_bytes()
    layouts = bp.layout_blocks(main)
    chunks = []
    for index, mesh in enumerate(bp.mesh_infos(main)):
        if index not in meshes:
            continue
        layout = layouts[bp.mesh_layout(main, mesh)]
        items = bp.layout_items(main, layout)
        stride = bp.u32(main, layout + bp.LAYOUT_VERTEX_STRIDE)
        nverts = bp.u32(main, layout + bp.LAYOUT_NUM_VERTICES)
        vbase = bp.u32(main, layout + bp.LAYOUT_VERTEX_OFFSET)
        ioff = bp.u32(main, layout + bp.LAYOUT_INDEX_OFFSET)
        pos_at, fmt = rt.position_offset(items)
        if fmt != 2:
            raise SystemExit("unsupported position format %d" % fmt)
        block = np.frombuffer(gpu, dtype="<f4", count=nverts * stride // 4, offset=vbase)
        pos = block.reshape(nverts, stride // 4)[:, pos_at // 4: pos_at // 4 + 3]
        for group in bp.mesh_groups(main, mesh):
            count = group[4]
            if count < 3:
                continue
            idx = np.frombuffer(gpu, dtype="<u2", count=count, offset=ioff + group[3] * 2)
            idx = idx[:(count // 3) * 3].reshape(-1, 3).astype(np.int32)
            chunks.append(pos[idx])
    if not chunks:
        raise SystemExit("unit has no triangles for meshes %s" % sorted(meshes))
    return np.concatenate(chunks, axis=0)


def render(rt, tris, yaw, pitch, tint):
    rt.YAW, rt.PITCH = yaw, pitch
    tris = rt.transform(tris)
    base = np.array(tint, dtype=np.float32) / 255.0
    size = SUPERSAMPLE

    px, py = tris[:, :, 0], -tris[:, :, 2]
    scale = size * FRAME / max(float(px.max() - px.min()), float(py.max() - py.min()), 1e-9)
    sx = (px - (px.min() + px.max()) / 2.0) * scale + size / 2.0
    sy = (py - (py.min() + py.max()) / 2.0) * scale + size / 2.0
    sz = tris[:, :, 1]

    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-9)
    facing = normal[:, 1] > 0.0
    color = rt.shade(normal, facing, base)

    back_img, back_a = rt.rasterize(sx[~facing], sy[~facing], sz[~facing],
                                    color[~facing] * 0.80, rt.BACK_ALPHA, size)
    front_img, front_a = rt.rasterize(sx[facing], sy[facing], sz[facing],
                                      color[facing], rt.FRONT_ALPHA, size)

    alpha = front_a + back_a * (1.0 - front_a)
    safe = np.maximum(alpha, 1e-6)[:, :, None]
    img = (front_img * front_a[:, :, None]
           + back_img * back_a[:, :, None] * (1.0 - front_a)[:, :, None]) / safe

    out = Image.fromarray((np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(Image.fromarray((np.clip(alpha, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "L"))
    box = out.getchannel("A").getbbox()
    return out.crop(box) if box else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default="E:/SteamLibrary/steamapps/common/Helldivers 2")
    ap.add_argument("--yaw", type=float, default=YAW)
    ap.add_argument("--pitch", type=float, default=PITCH)
    ap.add_argument("--tint", default="%d,%d,%d" % TINT)
    args = ap.parse_args()

    dump(args.game_dir)
    rt = render_tiles()
    import bug_patch as bp

    tint = tuple(int(v) for v in args.tint.split(","))
    tris = triangles(bp, rt, MESHES)
    img = render(rt, tris, args.yaw, args.pitch, tint)
    IMAGES.mkdir(parents=True, exist_ok=True)
    out = IMAGES / "model_railgun.png"
    img.save(out)
    print("render: %d triangles -> %s %s" % (len(tris), img.size, out.name))


if __name__ == "__main__":
    main()
