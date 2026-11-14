#!/usr/bin/env python3
"""把 images/icons_atlas.png 写回归档里的 icons_atlas 纹理。

这张纹理是 512x512 RGBA8、10 级 mip、未压缩，尺寸在资源表里有记录。只替换
`.gpu_resources` 里对应切片的字节、长度不变，所以行偏移、总长度都不用重算。
"""

import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parents[1] / "toolchain"))
from hd2tool import idstring64  # noqa: E402

PATCH = ROOT / "core" / "9ba626afa44a3aa3.patch_0"
GPU = PATCH.with_name(PATCH.name + ".gpu_resources")
ATLAS = ROOT / "images" / "icons_atlas.png"
MODULE = "mods/sumitm/sentry_ammo_display/icons_atlas"

HEADER = 72
TYPE_ENTRY = 32
ROW_SIZE = 80
SIZE = 512
MIPS = 10


def find_row(buf):
    types, count = struct.unpack_from("<II", buf, 4)
    base = HEADER + types * TYPE_ENTRY
    want = idstring64(MODULE).to_bytes(8, "little")
    for i in range(count):
        r = base + i * ROW_SIZE
        if buf[r:r + 8] != want:
            continue
        vals = struct.unpack_from("<7Q6I", buf, r)
        return {"main_off": vals[2], "main_size": vals[7], "gpu_off": vals[4], "gpu_size": vals[9]}
    raise SystemExit("归档里找不到 icons_atlas 纹理行")


def mip_chain(image):
    """逐级减半，先预乘再缩放，避免透明边缘发黑。"""
    levels = [image]
    while len(levels) < MIPS:
        prev = levels[-1]
        size = max(1, prev.width // 2)
        levels.append(prev.convert("RGBa").resize((size, size), Image.BOX).convert("RGBA"))
    return levels


def encode(image):
    if image.size != (SIZE, SIZE):
        raise SystemExit("图集必须是 %dx%d，实际 %s" % (SIZE, SIZE, image.size))
    out = bytearray()
    for level in mip_chain(image.convert("RGBA")):
        out.extend(level.tobytes())
    return bytes(out)


def verify_header(main):
    if main[0xC0:0xC4] != b"DDS ":
        raise SystemExit("纹理主数据不是 DDS")
    height, width = struct.unpack_from("<II", main, 0xC0 + 12)
    mips = struct.unpack_from("<I", main, 0xC0 + 28)[0]
    fmt = struct.unpack_from("<I", main, 0xC0 + 128)[0]
    if (width, height, mips, fmt) != (SIZE, SIZE, MIPS, 28):
        raise SystemExit("纹理格式与预期不符：%dx%d mips=%d dxgi=%d" % (width, height, mips, fmt))


def main():
    patch = PATCH.read_bytes()
    row = find_row(patch)
    verify_header(patch[row["main_off"]:row["main_off"] + row["main_size"]])

    data = encode(Image.open(ATLAS))
    if len(data) != row["gpu_size"]:
        raise SystemExit("编码后 %d 字节，槽位 %d" % (len(data), row["gpu_size"]))

    gpu = bytearray(GPU.read_bytes())
    start = row["gpu_off"]
    if start + len(data) > len(gpu):
        raise SystemExit("切片超出 .gpu_resources")
    if gpu[start:start + len(data)] == data:
        print("atlas: 已是最新")
        return
    gpu[start:start + len(data)] = data
    GPU.write_bytes(bytes(gpu))
    print("atlas: 写入 %d 字节 @ %d" % (len(data), start))


if __name__ == "__main__":
    main()
