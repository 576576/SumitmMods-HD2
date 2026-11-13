#!/usr/bin/env python3
"""把旧管理器的独立资源 patch 转成 Arsenal 选项包，只更新格式头。"""

import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

import bot_patch
from repatch_units import changed_regions, unit_ranges

ROOT = Path(__file__).resolve().parent.parent
GROUPS_FILE = ROOT / "tools" / "groups.json"
OPTIONS_DIR = ROOT / "options"
BUNDLE = "9ba626afa44a3aa3"
STEM = BUNDLE + ".patch_0"

MAGIC = bytes.fromhex("110000f0")
HEADER_SIZE = 72
TYPE_ENTRY_SIZE = 32
RES_ENTRY_SIZE = 80
REC = struct.Struct("<7Q6I")
TYPE_TEXTURE = 0xCD4238C6A0C69E32
TYPE_MATERIAL = 0xEAC0B497876ADEDF
TYPE_UNIT = 0xE0A48D0BE9A7453F


HANDLED_ELSEWHERE = ("bug_patch", "mine_patch")


def load_items():
    """虫雷与战备地雷（transform=bug_patch / mine_patch）各有独立模块，这里不接管。"""
    items = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))["standalone_options"]
    return [item for item in items if item.get("transform") not in HANDLED_ELSEWHERE]


def read_source(item):
    with zipfile.ZipFile(ROOT / item["source"]) as z:
        stem = item["patch"]
        parts = {
            "patch": z.read(stem),
            "gpu": z.read(stem + ".gpu_resources"),
            "stream": z.read(stem + ".stream"),
        }
    for key, want in item["sha256"].items():
        got = hashlib.sha256(parts[key]).hexdigest().upper()
        if got != want:
            raise SystemExit("source checksum mismatch %s/%s: %s" % (item["key"], key, got))
    return parts


def convert(parts):
    """旧管理器 patch 的 Approx 字段是过时近似值；其余字节必须原样保留。"""
    data = bytearray(parts["patch"])
    if data[:4] != MAGIC:
        raise SystemExit("not a Stingray bundle patch")
    struct.pack_into("<QQ", data, 0x20, len(data), len(parts["gpu"]))
    return bytes(data)


def rows_of(data):
    nt, nr = struct.unpack_from("<II", data, 4)
    base = HEADER_SIZE + TYPE_ENTRY_SIZE * nt
    return [REC.unpack_from(data, base + RES_ENTRY_SIZE * i) for i in range(nr)]


def main_source_ids():
    """主源的 (name, type)；同时勾选的选项不得引入同键资源。"""
    with zipfile.ZipFile(ROOT / "tools" / "source.zip") as z:
        data = z.read("9ba626afa44a3aa3.patch_2")
    return {(r[0], r[1]) for r in rows_of(data)}


def build():
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    for item in load_items():
        parts = read_source(item)
        out = OPTIONS_DIR / item["key"]
        out.mkdir(parents=True, exist_ok=True)
        if item.get("transform") == "bot_patch":
            patch, gpu = bot_patch.build(parts)
        else:
            patch, gpu = convert(parts), parts["gpu"]
        (out / STEM).write_bytes(patch)
        (out / (STEM + ".gpu_resources")).write_bytes(gpu)
        (out / (STEM + ".stream")).write_bytes(parts["stream"])
        print("%-12s patch=%7d gpu=%9d resources=%d" % (
            item["key"], len(patch), len(gpu), len(rows_of(patch))))
    return 0


def verify():
    problems = []
    existing = main_source_ids()
    for item in load_items():
        parts = read_source(item)
        out = OPTIONS_DIR / item["key"]
        patch = out / STEM
        gpu = out / (STEM + ".gpu_resources")
        stream = out / (STEM + ".stream")
        for p in (patch, gpu, stream):
            if not p.is_file():
                problems.append("%s: missing %s" % (item["key"], p))
        if problems:
            continue
        data = patch.read_bytes()
        gdata = gpu.read_bytes()
        src = bytearray(parts["patch"])
        struct.pack_into("<QQ", src, 0x20, len(src), len(parts["gpu"]))
        if data != bytes(src) and item.get("transform") != "bot_patch":
            regions = changed_regions(bytes(src), data)
            total = sum(j - i for i, j in regions)
            units = unit_ranges(data)
            inside = bool(regions) and all(
                any(lo <= i and j <= hi for _, lo, hi in units) for i, j in regions)
            if total > 64 or not inside:
                problems.append("%s: repatch touched %d bytes outside the unit" % (item["key"], total))
        if stream.read_bytes() != parts["stream"]:
            problems.append("%s: stream differs from source" % item["key"])
        if item.get("transform") == "bot_patch":
            findings = bot_patch.verify(parts, data, gdata)
            problems.extend("%s: %s" % (item["key"], f) for f in findings)
        elif gdata != parts["gpu"]:
            problems.append("%s: gpu differs from source" % item["key"])
        f20, f28 = struct.unpack_from("<QQ", data, 0x20)
        if f20 != len(data) or f28 != len(gdata):
            problems.append("%s: Approx fields %d/%d != %d/%d" % (
                item["key"], f20, f28, len(data), len(gdata)))
        rows = rows_of(data)
        ids = {(r[0], r[1]) for r in rows}
        if ids & existing:
            problems.append("%s: %d resources collide with the main source" % (
                item["key"], len(ids & existing)))
        for i, r in enumerate(rows):
            if r[2] + r[7] > len(data):
                problems.append("%s: main out of range at %d" % (item["key"], i))
            if r[9] and r[4] + r[9] > len(gdata):
                problems.append("%s: gpu out of range at %d" % (item["key"], i))
            if r[5] >= len(gdata) or r[6] >= len(gdata):
                problems.append("%s: buffer offset out of range at %d" % (item["key"], i))
            if r[12] != i + 1:
                problems.append("%s: bad resource index at %d" % (item["key"], i))
        tex = {r[0] for r in rows if r[1] == TYPE_TEXTURE}
        mat = {r[0] for r in rows if r[1] == TYPE_MATERIAL}
        units = [r for r in rows if r[1] == TYPE_UNIT]
        if len(units) != 1:
            problems.append("%s: expected exactly one unit" % item["key"])
            continue
        up = data[units[0][2]:units[0][2] + units[0][7]]
        refs = [m for m in mat if m.to_bytes(8, "little") in up]
        if item.get("transform") == "bot_patch":
            if len(refs) != 2:
                problems.append("%s: unit references %d materials, expected 2" % (item["key"], len(refs)))
            continue
        if len(refs) != 1:
            problems.append("%s: unit references %d materials" % (item["key"], len(refs)))
            continue
        mr = next(r for r in rows if r[0] == refs[0] and r[1] == TYPE_MATERIAL)
        mp = data[mr[2]:mr[2] + mr[7]]
        ntex = sum(1 for t in tex if t.to_bytes(8, "little") in mp)
        if ntex != 3:
            problems.append("%s: material references %d textures" % (item["key"], ntex))
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: %d standalone options, source bytes preserved, no resource collisions" % len(load_items()))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    sys.exit(verify() if cmd == "verify" else build())
