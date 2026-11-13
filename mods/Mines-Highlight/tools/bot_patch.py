#!/usr/bin/env python3
"""机器人地雷选项：加入本体高亮材质，并去掉光柱底部的机器人徽标几何。"""

import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATERIAL_FILE = ROOT / "tools" / "materials" / "m_cy_mine_highlight.material"
MATERIAL_META = ROOT / "tools" / "materials" / "m_cy_mine_highlight.json"

MAGIC = bytes.fromhex("110000f0")
HEADER_SIZE = 72
TYPE_ENTRY_SIZE = 32
RES_ENTRY_SIZE = 80
REC = struct.Struct("<7Q6I")
TYPE_ENTRY = struct.Struct("<IIQIIII")

TYPE_MATERIAL = 0xEAC0B497876ADEDF
TYPE_UNIT = 0xE0A48D0BE9A7453F

MATERIAL_NAME = "content/env_cyborg/gameplay_structures/materials/m_cy_mine_highlight.material"
BODY_KEY = 0xDFDDF784
BEAM_KEY = 0xC0FFABB2
OLD_BODY_MATERIAL = 0xCCCDE975F2ED7A81

UNIT_MAIN_SIZE = 1416
UNIT_GPU_SIZE = 262242
INDEX_OFFSET = 238656
BODY_INDICES = 10563
BEAM_INDICES = 1230
INDEX_STRIDE = 2
VERTEX_STRIDE = 28
BEAM_TRIANGLES = BEAM_INDICES // 3
EMBLEM_X_LIMIT = 0.1

# 光柱高度按原始实测值（4.7868）压低 20%，与终结族光柱取同一高度。
BEAM_SOURCE_HEIGHT = 4.7868
BEAM_HEIGHT_RATIO = 0.8
BEAM_TARGET_HEIGHT = BEAM_SOURCE_HEIGHT * BEAM_HEIGHT_RATIO
BEAM_HEIGHT_TOLERANCE = 0.02

MASK64 = (1 << 64) - 1
MIX = 0xC6A4A7935BD1E995
SHIFT = 47


def idstring64(value):
    data = value.encode("utf-8") if isinstance(value, str) else value
    h = (len(data) * MIX) & MASK64
    for i in range(len(data) // 8):
        k = int.from_bytes(data[i * 8:i * 8 + 8], "little")
        k = (k * MIX) & MASK64
        k ^= k >> SHIFT
        k = (k * MIX) & MASK64
        h ^= k
        h = (h * MIX) & MASK64
    tail = data[(len(data) // 8) * 8:]
    if tail:
        h ^= int.from_bytes(tail, "little")
        h = (h * MIX) & MASK64
    h ^= h >> SHIFT
    h = (h * MIX) & MASK64
    h ^= h >> SHIFT
    return h & MASK64


MATERIAL_HASH = idstring64(MATERIAL_NAME)


def align_up(value, alignment):
    if alignment <= 1:
        return value
    return (value + alignment - 1) // alignment * alignment


def material_bytes():
    data = MATERIAL_FILE.read_bytes()
    meta = json.loads(MATERIAL_META.read_text(encoding="utf-8"))
    if hashlib.sha256(data).hexdigest().upper() != meta["sha256"]:
        raise SystemExit("highlight material checksum mismatch")
    if len(data) != 208:
        raise SystemExit("unexpected highlight material size: %d" % len(data))
    return data


def rows_of(data):
    nt, nr = struct.unpack_from("<II", data, 4)
    base = HEADER_SIZE + TYPE_ENTRY_SIZE * nt
    return [list(REC.unpack_from(data, base + RES_ENTRY_SIZE * i)) for i in range(nr)]


def material_map(up):
    if len(up) != UNIT_MAIN_SIZE:
        raise SystemExit("unexpected unit main size: %d" % len(up))
    count = struct.unpack_from("<I", up, len(up) - 36)[0]
    keys = [struct.unpack_from("<I", up, len(up) - 32 + 4 * i)[0] for i in range(count)]
    vals = [struct.unpack_from("<Q", up, len(up) - 24 + 8 * i)[0] for i in range(count)]
    return keys, vals


def check_unit_main(up):
    keys, vals = material_map(up)
    if keys != [BODY_KEY, BEAM_KEY] or vals != [OLD_BODY_MATERIAL, 0xF60F3F933A3E8832]:
        raise SystemExit("unexpected unit material map: %s %s" % (keys, vals))


def remove_emblem(gpu):
    if len(gpu) != UNIT_GPU_SIZE:
        raise SystemExit("unexpected unit gpu size: %d" % len(gpu))
    start = INDEX_OFFSET + BODY_INDICES * INDEX_STRIDE
    if start + BEAM_INDICES * INDEX_STRIDE != len(gpu):
        raise SystemExit("unit index layout mismatch")
    idx = list(struct.unpack_from("<%dH" % BEAM_INDICES, gpu, start))

    def position(v):
        base = v * VERTEX_STRIDE
        return struct.unpack_from("<3f", gpu, base)

    hidden = 0
    for i in range(0, BEAM_INDICES, 3):
        pts = [position(idx[i + k]) for k in range(3)]
        span_z = max(p[2] for p in pts) - min(p[2] for p in pts)
        if max(abs(p[0]) for p in pts) > EMBLEM_X_LIMIT or span_z < 3.0:
            idx[i] = idx[i + 1] = idx[i + 2] = 0
            hidden += 1
    struct.pack_into("<%dH" % BEAM_INDICES, gpu, start, *idx)
    return hidden


def shrink_beam(gpu):
    """以柱体底部为基准压 z，徽标三角形已退化，不需要跟着动。"""
    start = INDEX_OFFSET + BODY_INDICES * INDEX_STRIDE
    idx = struct.unpack_from("<%dH" % BEAM_INDICES, gpu, start)
    verts = {v for v in idx if v}
    zs = [struct.unpack_from("<f", gpu, v * VERTEX_STRIDE + 8)[0] for v in verts]
    z0 = min(zs)
    for v in verts:
        base = v * VERTEX_STRIDE
        z = struct.unpack_from("<f", gpu, base + 8)[0]
        struct.pack_into("<f", gpu, base + 8, z0 + (z - z0) * BEAM_HEIGHT_RATIO)
    return (max(zs) - z0) * BEAM_HEIGHT_RATIO


def build(parts):
    data = bytearray(parts["patch"])
    gpu = bytearray(parts["gpu"])
    if data[:4] != MAGIC:
        raise SystemExit("not a Stingray bundle patch")
    nt, nr = struct.unpack_from("<II", data, 4)
    rows = rows_of(data)
    units = [i for i, r in enumerate(rows) if r[1] == TYPE_UNIT]
    if len(units) != 1:
        raise SystemExit("expected one unit")
    ui = units[0]

    up_off, up_len = rows[ui][2], rows[ui][7]
    up = bytearray(data[up_off:up_off + up_len])
    check_unit_main(up)
    struct.pack_into("<Q", up, len(up) - 24, MATERIAL_HASH)
    data[up_off:up_off + up_len] = up

    ugpu_off, ugpu_len = rows[ui][4], rows[ui][9]
    if ugpu_off + ugpu_len > len(gpu):
        raise SystemExit("unit gpu out of range")
    unit_gpu = bytearray(gpu[ugpu_off:ugpu_off + ugpu_len])
    hidden = remove_emblem(unit_gpu)
    if hidden != 346:
        raise SystemExit("expected 346 emblem triangles, hid %d" % hidden)
    height = shrink_beam(unit_gpu)
    if abs(height - BEAM_TARGET_HEIGHT) > BEAM_HEIGHT_TOLERANCE:
        raise SystemExit("beam height %.3f, expected %.3f" % (height, BEAM_TARGET_HEIGHT))
    gpu[ugpu_off:ugpu_off + ugpu_len] = unit_gpu

    type_base = HEADER_SIZE
    mat_entry = None
    for i in range(nt):
        entry = TYPE_ENTRY.unpack_from(data, type_base + TYPE_ENTRY_SIZE * i)
        if entry[2] == TYPE_MATERIAL:
            mat_entry = (i, entry)
    if mat_entry is None:
        raise SystemExit("material type entry missing")
    mat_index, mat = mat_entry
    if mat[3] != 1:
        raise SystemExit("unexpected material count %d" % mat[3])
    struct.pack_into("<I", data, type_base + TYPE_ENTRY_SIZE * mat_index + 16, 2)

    for r in rows:
        r[2] += RES_ENTRY_SIZE
    new_row = [MATERIAL_HASH, TYPE_MATERIAL, 0, 0, 0, 0, 0, len(material_bytes()), 0, 0, mat[5], mat[6], nr + 1]
    payload = bytes(data[HEADER_SIZE + TYPE_ENTRY_SIZE * nt + RES_ENTRY_SIZE * nr:])
    table = b"".join(REC.pack(*r) for r in rows + [new_row])
    material = material_bytes()
    data_start = HEADER_SIZE + TYPE_ENTRY_SIZE * nt + RES_ENTRY_SIZE * (nr + 1)
    new_off = align_up(data_start + len(payload), mat[5])
    new_row[2] = new_off
    table = b"".join(REC.pack(*r) for r in rows + [new_row])
    body = bytearray(data[:HEADER_SIZE + TYPE_ENTRY_SIZE * nt])
    struct.pack_into("<I", body, 8, nr + 1)
    body.extend(table)
    body.extend(payload)
    body.extend(b"\0" * (new_off - len(body)))
    body.extend(material)
    struct.pack_into("<QQ", body, 0x20, len(body), len(gpu))
    return bytes(body), bytes(gpu)


def verify(parts, data, gpu):
    problems = []
    src_rows = rows_of(parts["patch"])
    rows = rows_of(data)
    if len(rows) != len(src_rows) + 1:
        problems.append("resource count %d, expected %d" % (len(rows), len(src_rows) + 1))
    if struct.unpack_from("<I", data, 8)[0] != len(rows):
        problems.append("header resource count mismatch")
    f20, f28 = struct.unpack_from("<QQ", data, 0x20)
    if f20 != len(data) or f28 != len(gpu):
        problems.append("Approx fields %d/%d != %d/%d" % (f20, f28, len(data), len(gpu)))

    nt = struct.unpack_from("<I", data, 4)[0]
    counts = {}
    for i in range(nt):
        entry = TYPE_ENTRY.unpack_from(data, HEADER_SIZE + TYPE_ENTRY_SIZE * i)
        counts[entry[2]] = entry[3]
    actual = {}
    for r in rows:
        actual[r[1]] = actual.get(r[1], 0) + 1
    if counts != actual:
        problems.append("type counts %s != resources %s" % (counts, actual))

    mats = [i for i, r in enumerate(rows) if r[1] == TYPE_MATERIAL]
    new_mats = [i for i in mats if rows[i][0] == MATERIAL_HASH]
    if len(new_mats) != 1:
        problems.append("highlight material missing")
    else:
        mr = rows[new_mats[0]]
        if data[mr[2]:mr[2] + mr[7]] != material_bytes():
            problems.append("highlight material data mismatch")
        if mr[2] % (mr[10] or 1):
            problems.append("highlight material offset not aligned")

    units = [i for i, r in enumerate(rows) if r[1] == TYPE_UNIT]
    if len(units) != 1:
        problems.append("expected one unit")
        return problems
    ui = units[0]
    up = data[rows[ui][2]:rows[ui][2] + rows[ui][7]]
    keys, vals = material_map(up)
    if keys != [BODY_KEY, BEAM_KEY] or vals != [MATERIAL_HASH, 0xF60F3F933A3E8832]:
        problems.append("unit material map not updated: %s %s" % (keys, vals))
    if rows[ui][4] + rows[ui][9] > len(gpu):
        problems.append("unit gpu out of range")

    start = rows[ui][4] + INDEX_OFFSET + BODY_INDICES * INDEX_STRIDE
    idx = list(struct.unpack_from("<%dH" % BEAM_INDICES, gpu, start))

    gpu_base = rows[ui][4]

    def position(v):
        return struct.unpack_from("<3f", gpu, gpu_base + v * VERTEX_STRIDE)

    degenerate = sum(1 for i in range(0, BEAM_INDICES, 3) if idx[i] == idx[i + 1] == idx[i + 2])
    visible = BEAM_TRIANGLES - degenerate
    kept = []
    for i in range(0, BEAM_INDICES, 3):
        if idx[i] == idx[i + 1] == idx[i + 2]:
            continue
        kept.extend(position(idx[i + k]) for k in range(3))
    if kept:
        max_x = max(abs(p[0]) for p in kept)
        min_z = min(p[2] for p in kept)
        max_z = max(p[2] for p in kept)
        radius = max(max(abs(p[0]), abs(p[1])) for p in kept)
    else:
        max_x, min_z, max_z, radius = 0.0, 0.0, 0.0, 0.0
    if (degenerate != 346 or visible != 64 or max_x > 0.05
            or abs((max_z - min_z) - BEAM_TARGET_HEIGHT) > BEAM_HEIGHT_TOLERANCE
            or abs(radius - 0.042) > 0.002):
        problems.append(
            "beam geometry degenerate=%d visible=%d max_x=%.3f r=%.3f z=%.2f..%.2f (h=%.3f)"
            % (degenerate, visible, max_x, radius, min_z, max_z, max_z - min_z))

    for i, r in enumerate(src_rows):
        if r[1] == TYPE_UNIT:
            continue
        same = rows[i][0] == r[0] and rows[i][1] == r[1]
        same = same and data[rows[i][2]:rows[i][2] + rows[i][7]] == parts["patch"][r[2]:r[2] + r[7]]
        if not same:
            problems.append("existing resource %d changed" % i)
    return problems
