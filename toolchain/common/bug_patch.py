#!/usr/bin/env python3
"""终结族地雷选项：本体高亮材质 + 光柱。

虫雷本体是 content/env_bugs/assets/bug_web/bug_cluster_01/02：黄顶、四周放射长针的孢囊。

光柱不放进本体 mesh，而是单独建一个 mesh，理由是顶点布局：

- 本体布局只有 position + normal + uv 三项、stride 20，没有光柱材质要的 UV 层；
- 机器人地雷的光柱材质跑在 stride 28、三套 UV 的布局上。

同一个 mesh 只能有一个布局，所以光柱必须自成 mesh，用一份与机器人地雷同构的布局
（position + 2×int8 normal + 三套 packed UV），几何也按这套布局生成。之后把新 mesh
登记进 LOD 组，否则不会被渲染。

unit 的 main 是变长结构，内部偏移都相对各自表头，所以新数据一律追加在末尾：布局列表、
mesh 列表、LOD 组列表三张表各自重建一份搬到尾部，旧副本留下不用。
"""

import hashlib
import json
import math
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

# 补丁数据（options/、tools/materials/、source*.zip）属于 Mines-Highlight，
# 本模块是共享库，路径仍指向那个项目。
ROOT = paths.mod("Mines-Highlight")
TOOLS = ROOT / "tools"
GROUPS_FILE = TOOLS / "groups.json"
OPTIONS_DIR = ROOT / "options"

BUNDLE = "9ba626afa44a3aa3"
STEM = BUNDLE + ".patch_0"

MAGIC = bytes.fromhex("110000f0")
HEADER_SIZE = 72
TYPE_ENTRY_SIZE = 32
RES_ENTRY_SIZE = 80
REC = struct.Struct("<7Q6I")
TYPE_ENTRY = struct.Struct("<IIQIIII")
TYPE_TEXTURE = 0xCD4238C6A0C69E32
TYPE_MATERIAL = 0xEAC0B497876ADEDF
TYPE_UNIT = 0xE0A48D0BE9A7453F

HDR_LOD_LIST = 0x30
HDR_LAYOUT_LIST = 92
HDR_MESH_DATA = 96
HDR_MESH_INFO_LIST = 100
HDR_MATERIAL_LIST = 112

MESH_HEADER_SIZE = 128
MESH_OFF_AABB = 8
MESH_OFF_MESH_NAME = 40
MESH_OFF_LAYOUT = 60
MESH_NUM_MATERIALS = 104
MESH_MATERIAL_OFFSET = 108
MESH_NUM_GROUPS = 120
MESH_GROUP_OFFSET = 124
MESH_GROUP_SIZE = 24

MESH_LAYOUT_SIZE = 448
LAYOUT_NUM_ITEMS = 328
LAYOUT_ITEMS = 8
LAYOUT_ITEM_SIZE = 20
LAYOUT_NUM_VERTICES = 352
LAYOUT_VERTEX_STRIDE = 356
LAYOUT_NUM_INDICES = 392
LAYOUT_VERTEX_OFFSET = 416
LAYOUT_POSITIONS_SIZE = 420
LAYOUT_INDEX_OFFSET = 424
LAYOUT_INDICES_SIZE = 428

LOD_FOOTER_SIZE = 44

# 代理材质：分别给阴影与碰撞网格用，高亮只改可见本体那一槽。
SHADOW_FULL = 0x5990E5EFBCA8AE21
COLLISION_FULL = 0x84B327056BAF1C6D
PROXY_FULLS = (SHADOW_FULL, COLLISION_FULL)
BEAM_THIN = 0xC0FFABB2
BEAM_FULL = 0xF60F3F933A3E8832

ITEM_POSITION = 0
ITEM_NORMAL = 1
ITEM_UV = 4
ITEM_COLOR = 5

FORMAT_SIZE = {
    0: 4, 1: 8, 2: 12, 3: 16, 4: 4,
    21: 4, 22: 8, 23: 12, 24: 16,
    25: 1, 26: 2, 27: 3, 28: 4,
    29: 4, 30: 4,
    32: 2, 33: 4, 34: 6, 35: 8,
}

# 光柱 mesh 的顶点布局与本体一致（位置 + packed 法线 + float16 UV），
# 对应的高亮材质就是在本体这套布局上验证过能渲染的写法。
BEAM_ITEMS = ((ITEM_POSITION, 2, 0), (ITEM_NORMAL, 30, 0), (ITEM_UV, 33, 0))
BEAM_STRIDE = 20

BEAM_SEGMENTS = 32
BEAM_RADIUS = 0.042
# 与机器人光柱取同一高度：原始实测 4.7868，压低 20%。
BEAM_SOURCE_HEIGHT = 4.7868
BEAM_HEIGHT_RATIO = 0.8
BEAM_HEIGHT = BEAM_SOURCE_HEIGHT * BEAM_HEIGHT_RATIO
BEAM_VERTICES = BEAM_SEGMENTS * 2
BEAM_TRIANGLES = BEAM_SEGMENTS * 2
BEAM_INDICES = BEAM_TRIANGLES * 3
BEAM_HEIGHT_TOLERANCE = 0.05

HIGHLIGHT_NAME = "content/env_bugs/assets/bug_hive/materials/m_bug_mine_highlight.material"
HIGHLIGHT_FILE = TOOLS / "materials" / "m_bug_mine_highlight.material"
HIGHLIGHT_META = TOOLS / "materials" / "m_bug_mine_highlight.json"

BEAM_HIGHLIGHT_NAME = "content/env_bugs/assets/bug_web/materials/m_bug_beam_highlight.material"
BEAM_HIGHLIGHT_FILE = TOOLS / "materials" / "m_bug_beam_highlight.material"
BEAM_HIGHLIGHT_META = TOOLS / "materials" / "m_bug_beam_highlight.json"

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


HIGHLIGHT_HASH = idstring64(HIGHLIGHT_NAME)
BEAM_HIGHLIGHT_HASH = idstring64(BEAM_HIGHLIGHT_NAME)
# 光柱 mesh 用一个独立的名字，避免和本体 mesh 重名被引擎合并。
BEAM_MESH_NAME = idstring64("bug_beam_mesh") & 0xFFFFFFFF


def align_up(value, alignment):
    if alignment <= 1:
        return value
    return value + (-value) % alignment


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def put32(data, offset, value):
    struct.pack_into("<I", data, offset, value)


def highlight_bytes():
    data = HIGHLIGHT_FILE.read_bytes()
    meta = json.loads(HIGHLIGHT_META.read_text(encoding="utf-8"))
    if hashlib.sha256(data).hexdigest().upper() != meta["sha256"]:
        raise SystemExit("bug highlight material checksum mismatch")
    if len(data) != 208:
        raise SystemExit("unexpected bug highlight material size: %d" % len(data))
    return data


def beam_material_bytes():
    data = BEAM_HIGHLIGHT_FILE.read_bytes()
    meta = json.loads(BEAM_HIGHLIGHT_META.read_text(encoding="utf-8"))
    if hashlib.sha256(data).hexdigest().upper() != meta["sha256"]:
        raise SystemExit("beam highlight material checksum mismatch")
    if len(data) != 208:
        raise SystemExit("unexpected beam highlight material size: %d" % len(data))
    return data


def config():
    cfg = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    for item in cfg.get("standalone_options", []):
        if item["key"] == "bug":
            return item
    raise SystemExit("groups.json has no bug standalone option")


def read_source(item):
    with zipfile.ZipFile(ROOT / item["source"]) as z:
        parts = {}
        for name in item["units"]:
            stem = name.rsplit("/", 1)[1]
            parts[stem] = {"main": z.read(stem + ".main"), "gpu": z.read(stem + ".gpu")}
    for stem, sums in item["sha256"].items():
        for ext, want in sums.items():
            got = hashlib.sha256(parts[stem][ext]).hexdigest().upper()
            if got != want:
                raise SystemExit("source checksum mismatch %s%s: %s" % (stem, ext, got))
    return parts


def bot_sources():
    """光柱材质与三张贴图来自机器人地雷源包，原样复用。"""
    with zipfile.ZipFile(ROOT / "tools" / "source_bot.zip") as z:
        patch = z.read("9ba626afa44a3aa3.patch_87")
        gpu = z.read("9ba626afa44a3aa3.patch_87.gpu_resources")
    nt, nr = struct.unpack_from("<II", patch, 4)
    base = HEADER_SIZE + TYPE_ENTRY_SIZE * nt
    out = []
    for i in range(nr):
        r = REC.unpack_from(patch, base + RES_ENTRY_SIZE * i)
        if r[1] == TYPE_UNIT:
            continue
        out.append({
            "name": r[0], "type": r[1],
            "main": patch[r[2]:r[2] + r[7]],
            "gpu": gpu[r[4]:r[4] + r[9]] if r[9] else b"",
        })
    return out, patch[:HEADER_SIZE], [
        patch[HEADER_SIZE + TYPE_ENTRY_SIZE * i:HEADER_SIZE + TYPE_ENTRY_SIZE * (i + 1)]
        for i in range(nt)
    ]


def mesh_infos(data):
    base = u32(data, HDR_MESH_INFO_LIST)
    if not base:
        return []
    count = u32(data, base)
    return [base + u32(data, base + 4 + 4 * i) for i in range(count)]


def material_map(data):
    base = u32(data, HDR_MATERIAL_LIST)
    count = u32(data, base)
    thins = [u32(data, base + 4 + 4 * i) for i in range(count)]
    fulls = [struct.unpack_from("<Q", data, base + 4 + 4 * count + 8 * i)[0] for i in range(count)]
    return thins, fulls


def mesh_materials(data, mesh):
    count = u32(data, mesh + MESH_NUM_MATERIALS)
    offset = u32(data, mesh + MESH_MATERIAL_OFFSET)
    return [u32(data, mesh + offset + 4 * i) for i in range(count)]


def mesh_groups(data, mesh):
    count = u32(data, mesh + MESH_NUM_GROUPS)
    offset = u32(data, mesh + MESH_GROUP_OFFSET)
    return [list(struct.unpack_from("<6I", data, mesh + offset + MESH_GROUP_SIZE * i))
            for i in range(count)]


def layout_blocks(data):
    base = u32(data, HDR_LAYOUT_LIST)
    if not base:
        raise SystemExit("unit has no mesh layout list")
    count = u32(data, base)
    return [base + u32(data, base + 4 + 4 * i) for i in range(count)]


def mesh_layout(data, mesh):
    return struct.unpack_from("<i", data, mesh + MESH_OFF_LAYOUT)[0]


def layout_items(data, layout):
    count = u32(data, layout + LAYOUT_NUM_ITEMS)
    return [struct.unpack_from("<III", data, layout + LAYOUT_ITEMS + LAYOUT_ITEM_SIZE * k)
            for k in range(count)]


def pack_normal(nx, ny, nz):
    def channel(v):
        return max(0, min(1023, int(round((v * 0.5 + 0.5) * 1023))))
    return channel(nx) | (channel(ny) << 10) | (channel(nz) << 20)


def pack_uv(uv):
    def channel(v):
        return max(0, min(1023, int(round(v * 1023))))
    return channel(uv[0]) | (channel(uv[1]) << 10) | (1023 << 20)


def beam_vertex(position, normal, uv):
    """按光柱布局写一条顶点：3f 位置、packed32u 法线、float16 UV。"""
    buf = bytearray(BEAM_STRIDE)
    struct.pack_into("<3f", buf, 0, *position)
    struct.pack_into("<I", buf, 12, pack_normal(normal[0], normal[1], 0.0))
    struct.pack_into("<2e", buf, 16, uv[0], uv[1])
    return bytes(buf)


def beam_geometry(z_bottom, height=BEAM_HEIGHT):
    """32 边圆柱：每边两个三角形，法线朝外，u 绕一周、v 沿高度。"""
    verts = []
    for v, z in ((0.0, z_bottom), (1.0, z_bottom + height)):
        for i in range(BEAM_SEGMENTS):
            angle = 2 * math.pi * i / BEAM_SEGMENTS
            nx, ny = math.cos(angle), math.sin(angle)
            verts.append(beam_vertex(
                (nx * BEAM_RADIUS, ny * BEAM_RADIUS, z), (nx, ny), (i / BEAM_SEGMENTS, v)))
    indices = []
    for i in range(BEAM_SEGMENTS):
        j = (i + 1) % BEAM_SEGMENTS
        indices.extend((i, j, BEAM_SEGMENTS + j, i, BEAM_SEGMENTS + j, BEAM_SEGMENTS + i))
    return b"".join(verts), indices


def body_slots(data):
    """可见本体用的材质槽：既不是阴影/碰撞代理，也不是光柱。"""
    thins, fulls = material_map(data)
    return [t for t, f in zip(thins, fulls)
            if f not in PROXY_FULLS and t != BEAM_THIN and f != BEAM_HIGHLIGHT_HASH]


def body_meshes(data):
    slots = set(body_slots(data))
    return [m for m in mesh_infos(data) if slots & set(mesh_materials(data, m))]


def body_layout(data):
    meshes = body_meshes(data)
    if not meshes:
        raise SystemExit("unit has no mesh using a visible body material")
    layout_ids = {mesh_layout(data, m) for m in meshes}
    if len(layout_ids) != 1:
        raise SystemExit("body meshes use %d different layouts" % len(layout_ids))
    layout_id = layout_ids.pop()
    layouts = layout_blocks(data)
    if not 0 <= layout_id < len(layouts):
        raise SystemExit("body mesh layout index %d is out of range" % layout_id)
    return meshes, layouts[layout_id]


def rebuild_layout_list(data, extra_block):
    """把布局列表连同全部块复制到末尾，追加一个新块，返回新块索引。"""
    base = u32(data, HDR_LAYOUT_LIST)
    count = u32(data, base)
    blocks = []
    for i in range(count):
        at = base + u32(data, base + 4 + 4 * i)
        blocks.append(bytes(data[at:at + MESH_LAYOUT_SIZE]))
    blocks.append(extra_block)

    at = align_up(len(data), 16)
    data.extend(b"\0" * (at - len(data)))
    new_base = len(data)
    data.extend(b"\0" * (4 + 4 * len(blocks)))
    put32(data, new_base, len(blocks))
    for i, block in enumerate(blocks):
        at = align_up(len(data), 16)
        data.extend(b"\0" * (at - len(data)))
        put32(data, new_base + 4 + 4 * i, at - new_base)
        data.extend(block)
    put32(data, HDR_LAYOUT_LIST, new_base)
    return len(blocks) - 1


def mesh_block_size(data, mesh):
    """mesh 块的真实长度：头部之后还跟着材质表和组表，偏移都是相对块起点的。"""
    size = MESH_HEADER_SIZE
    materials = u32(data, mesh + MESH_NUM_MATERIALS)
    material_at = u32(data, mesh + MESH_MATERIAL_OFFSET)
    groups = u32(data, mesh + MESH_NUM_GROUPS)
    group_at = u32(data, mesh + MESH_GROUP_OFFSET)
    return max(size, material_at + 4 * materials, group_at + MESH_GROUP_SIZE * groups)


def rebuild_mesh_list(data, extra_block):
    base = u32(data, HDR_MESH_INFO_LIST)
    count = u32(data, base)
    blocks = []
    for i in range(count):
        at = base + u32(data, base + 4 + 4 * i)
        blocks.append(bytes(data[at:at + mesh_block_size(data, at)]))
    blocks.append(extra_block)

    at = align_up(len(data), 16)
    data.extend(b"\0" * (at - len(data)))
    new_base = len(data)
    data.extend(b"\0" * (4 + 4 * len(blocks)))
    put32(data, new_base, len(blocks))
    for i, block in enumerate(blocks):
        at = align_up(len(data), 16)
        data.extend(b"\0" * (at - len(data)))
        put32(data, new_base + 4 + 4 * i, at - new_base)
        data.extend(block)
    put32(data, HDR_MESH_INFO_LIST, new_base)
    return len(blocks) - 1


def lod_groups(data):
    base = u32(data, HDR_LOD_LIST)
    if not base:
        return []
    count = u32(data, base)
    groups = []
    for i in range(count):
        at = base + u32(data, base + 4 + 4 * i)
        header = bytes(data[at:at + 12])
        footers_n = u32(data, at + 12)
        entries_n = u32(data, at + 16)
        entry_offs = [u32(data, at + 20 + 4 * j) for j in range(entries_n)]
        foot_offs = [u32(data, at + 20 + 4 * entries_n + 4 * j) for j in range(footers_n)]
        entries = []
        for off in entry_offs:
            eb = at + off
            near, far = struct.unpack_from("<2f", data, eb)
            n = u32(data, eb + 8)
            meshes = [u32(data, eb + 12 + 4 * k) for k in range(n)]
            entries.append([near, far, meshes])
        footers = [bytes(data[at + off:at + off + LOD_FOOTER_SIZE]) for off in foot_offs]
        groups.append([header, entries, footers])
    return groups


def rebuild_lod_list(data, extra_mesh, body_mesh_indices):
    """重建 LOD 组列表，把光柱 mesh 登记进渲染本体 mesh 的那一组。"""
    base = u32(data, HDR_LOD_LIST)
    if not base:
        raise SystemExit("unit has no LOD group list")
    groups = lod_groups(data)
    target = set(body_mesh_indices)
    registered = 0
    for _, entries, _ in groups:
        if any(target & set(entry[2]) for entry in entries):
            for entry in entries:
                if extra_mesh not in entry[2]:
                    entry[2].append(extra_mesh)
                    registered += 1
    if not registered:
        raise SystemExit("no LOD group references the body meshes")

    at = align_up(len(data), 16)
    data.extend(b"\0" * (at - len(data)))
    new_base = len(data)
    data.extend(b"\0" * (4 + 4 * len(groups)))
    put32(data, new_base, len(groups))
    for gi, (header, entries, footers) in enumerate(groups):
        at = align_up(len(data), 4)
        data.extend(b"\0" * (at - len(data)))
        group_at = len(data)
        put32(data, new_base + 4 + 4 * gi, group_at - new_base)
        data.extend(header)
        data.extend(struct.pack("<II", len(footers), len(entries)))
        entry_blobs = [struct.pack("<2fI", near, far, len(meshes))
                       + struct.pack("<%dI" % len(meshes), *meshes)
                       for near, far, meshes in entries]
        head = 12 + 8 + 4 * len(entries) + 4 * len(footers)
        cursor = group_at + head
        entry_offs = []
        for blob in entry_blobs:
            entry_offs.append(cursor - group_at)
            cursor += len(blob)
        footer_offs = []
        for footer in footers:
            footer_offs.append(cursor - group_at)
            cursor += len(footer)
        data.extend(struct.pack("<%dI" % len(entry_offs), *entry_offs))
        data.extend(struct.pack("<%dI" % len(footer_offs), *footer_offs))
        for blob in entry_blobs:
            data.extend(blob)
        for footer in footers:
            data.extend(footer)
    put32(data, HDR_LOD_LIST, new_base)
    return registered


def inject(main, gpu, highlight_hash, beam_hash, ratio=1.0):
    """返回 (new_main, new_gpu, 统计)。

    本体材质槽改指高亮材质；另建一个只含光柱的 mesh（机器人地雷同构布局），登记进 LOD。
    本体顶点与索引一个字节都不动，光柱的顶点/索引追加在 GPU 缓冲末尾。
    """
    data = bytearray(main)
    meshes, layout = body_layout(data)

    thins, fulls = material_map(data)
    if not body_slots(data):
        raise SystemExit("no visible body material in the unit material map")
    new_thins = list(thins) + [BEAM_THIN]
    new_fulls = [highlight_hash if f not in PROXY_FULLS else f for f in fulls] + [beam_hash]

    aabb_min_z = min(struct.unpack_from("<f", data, m + MESH_OFF_AABB + 8)[0] for m in meshes)
    beam_verts, beam_local = beam_geometry(aabb_min_z - 0.02, BEAM_HEIGHT * ratio)

    new_gpu = bytearray(gpu)
    new_gpu.extend(b"\0" * (align_up(len(new_gpu), 16) - len(new_gpu)))
    vertex_base = len(new_gpu)
    new_gpu.extend(beam_verts)
    index_offset = align_up(len(new_gpu), 4)
    new_gpu.extend(b"\0" * (index_offset - len(new_gpu)))
    new_gpu.extend(struct.pack("<%dH" % BEAM_INDICES, *beam_local))

    # 新布局：复制本体布局块，换成光柱的顶点格式与计数。
    template = bytes(data[layout:layout + MESH_LAYOUT_SIZE])
    block = bytearray(template)
    put32(block, LAYOUT_NUM_ITEMS, len(BEAM_ITEMS))
    for k, item in enumerate(BEAM_ITEMS):
        struct.pack_into("<III", block, LAYOUT_ITEMS + LAYOUT_ITEM_SIZE * k, *item)
    put32(block, LAYOUT_NUM_VERTICES, BEAM_VERTICES)
    put32(block, LAYOUT_VERTEX_STRIDE, BEAM_STRIDE)
    put32(block, LAYOUT_NUM_INDICES, BEAM_INDICES)
    put32(block, LAYOUT_VERTEX_OFFSET, vertex_base)
    put32(block, LAYOUT_POSITIONS_SIZE, BEAM_VERTICES * BEAM_STRIDE)
    put32(block, LAYOUT_INDEX_OFFSET, index_offset)
    put32(block, LAYOUT_INDICES_SIZE, BEAM_INDICES * 2)
    beam_layout_index = rebuild_layout_list(data, bytes(block))

    # 新 mesh：以本体第一个 mesh 为模板，只留一个材质槽和一个组。
    mesh = bytearray(data[meshes[0]:meshes[0] + MESH_HEADER_SIZE])
    mins = [min(struct.unpack_from("<f", data, m + MESH_OFF_AABB + 4 * k)[0] for m in meshes)
            for k in range(3)]
    maxs = [max(struct.unpack_from("<f", data, m + MESH_OFF_AABB + 12 + 4 * k)[0] for m in meshes)
            for k in range(3)]
    # 包围盒只覆盖本体：军备预览按 mesh 包围盒取景，把 3.8 m 的柱子算进去会把本体缩成一小点。
    struct.pack_into("<6f", mesh, MESH_OFF_AABB, *(mins + maxs))
    put32(mesh, MESH_OFF_MESH_NAME, BEAM_MESH_NAME)
    put32(mesh, MESH_OFF_LAYOUT, beam_layout_index)
    put32(mesh, MESH_NUM_MATERIALS, 1)
    put32(mesh, MESH_MATERIAL_OFFSET, MESH_HEADER_SIZE)
    put32(mesh, MESH_NUM_GROUPS, 1)
    put32(mesh, MESH_GROUP_OFFSET, MESH_HEADER_SIZE + 4)
    mesh.extend(struct.pack("<I", BEAM_THIN))
    mesh.extend(struct.pack("<6I", 0, 0, BEAM_VERTICES, 0, BEAM_INDICES, 0))
    beam_mesh_index = rebuild_mesh_list(data, bytes(mesh))

    # MeshData 表只有一条计数，必须与 mesh 数一致：另建一份加一的表。
    mesh_data_at = u32(data, HDR_MESH_DATA)
    if not mesh_data_at:
        raise SystemExit("unit has no mesh data list")
    mesh_data_count = u32(data, mesh_data_at)
    if mesh_data_count != len(mesh_infos(main)):
        raise SystemExit("mesh data count %d does not match mesh info count %d"
                         % (mesh_data_count, len(mesh_infos(main))))
    at = align_up(len(data), 16)
    data.extend(b"\0" * (at - len(data)))
    new_mesh_data = len(data)
    data.extend(struct.pack("<I", mesh_data_count + 1) + b"\0" * 12)
    put32(data, HDR_MESH_DATA, new_mesh_data)

    rebuild_lod_list(data, beam_mesh_index, [mesh_infos(main).index(m) for m in meshes])

    at = align_up(len(data), 4)
    data.extend(b"\0" * (at - len(data)))
    table_at = len(data)
    data.extend(struct.pack("<I", len(new_thins)))
    for value in new_thins:
        data.extend(struct.pack("<I", value))
    for value in new_fulls:
        data.extend(struct.pack("<Q", value))
    put32(data, HDR_MATERIAL_LIST, table_at)

    data.extend(b"\0" * (align_up(len(data), 16) - len(data)))
    stats = {
        "mode": "mesh", "beam_mesh": beam_mesh_index, "beam_layout": beam_layout_index,
        "vertices": BEAM_VERTICES, "indices": BEAM_INDICES,
        "main": len(main), "new_main": len(data),
        "gpu": len(gpu), "new_gpu": len(new_gpu),
    }
    return bytes(data), bytes(new_gpu), stats


def inject_group(main, gpu, highlight_hash, beam_hash, ratio=1.0):
    """把光柱并进本体 mesh：每个本体 mesh 追加一个材质槽和一个组。

    与机器人地雷的结构一致（一个 mesh、两个组），不新建 layout / mesh / LOD 条目。
    本体顶点与索引整块搬到 GPU 末尾，光柱接在后面；已有组的偏移相对布局基址，不用改。
    """
    data = bytearray(main)
    meshes, layout = body_layout(data)

    stride = u32(data, layout + LAYOUT_VERTEX_STRIDE)
    if stride != BEAM_STRIDE:
        raise SystemExit("body stride %d does not match the beam layout" % stride)
    num_vertices = u32(data, layout + LAYOUT_NUM_VERTICES)
    num_indices = u32(data, layout + LAYOUT_NUM_INDICES)
    vertex_base = u32(data, layout + LAYOUT_VERTEX_OFFSET)
    positions_size = u32(data, layout + LAYOUT_POSITIONS_SIZE)
    index_offset = u32(data, layout + LAYOUT_INDEX_OFFSET)
    indices_size = u32(data, layout + LAYOUT_INDICES_SIZE)
    if positions_size != num_vertices * stride:
        raise SystemExit("body vertex block size mismatch")
    if indices_size != num_indices * 2:
        raise SystemExit("body index block size mismatch")
    if vertex_base + positions_size > len(gpu) or index_offset + indices_size > len(gpu):
        raise SystemExit("body blocks point outside the gpu buffer")
    if num_vertices + BEAM_VERTICES > 0xFFFF:
        raise SystemExit("uint16 indices would overflow")

    thins, fulls = material_map(data)
    if not body_slots(data):
        raise SystemExit("no visible body material in the unit material map")
    new_thins = list(thins) + [BEAM_THIN]
    new_fulls = [highlight_hash if f not in PROXY_FULLS else f for f in fulls] + [beam_hash]

    aabb_min_z = min(struct.unpack_from("<f", data, m + MESH_OFF_AABB + 8)[0] for m in meshes)
    beam_verts, beam_local = beam_geometry(aabb_min_z - 0.02, BEAM_HEIGHT * ratio)

    new_gpu = bytearray(gpu)
    new_gpu.extend(b"\0" * (align_up(len(new_gpu), 16) - len(new_gpu)))
    new_vertex_base = len(new_gpu)
    new_gpu.extend(gpu[vertex_base:vertex_base + positions_size])
    new_gpu.extend(beam_verts)
    new_index_offset = align_up(len(new_gpu), 4)
    new_gpu.extend(b"\0" * (new_index_offset - len(new_gpu)))
    new_gpu.extend(gpu[index_offset:index_offset + indices_size])
    new_gpu.extend(struct.pack("<%dH" % BEAM_INDICES,
                               *[num_vertices + v for v in beam_local]))

    put32(data, layout + LAYOUT_VERTEX_OFFSET, new_vertex_base)
    put32(data, layout + LAYOUT_NUM_VERTICES, num_vertices + BEAM_VERTICES)
    put32(data, layout + LAYOUT_POSITIONS_SIZE, (num_vertices + BEAM_VERTICES) * stride)
    put32(data, layout + LAYOUT_INDEX_OFFSET, new_index_offset)
    put32(data, layout + LAYOUT_INDICES_SIZE, (num_indices + BEAM_INDICES) * 2)
    put32(data, layout + LAYOUT_NUM_INDICES, num_indices + BEAM_INDICES)

    for mesh in meshes:
        old_mats = mesh_materials(data, mesh)
        at = align_up(len(data), 4)
        data.extend(b"\0" * (at - len(data)))
        materials_at = len(data)
        for value in old_mats + [BEAM_THIN]:
            data.extend(struct.pack("<I", value))
        put32(data, mesh + MESH_NUM_MATERIALS, len(old_mats) + 1)
        put32(data, mesh + MESH_MATERIAL_OFFSET, materials_at - mesh)

        groups = mesh_groups(data, mesh)
        at = align_up(len(data), 4)
        data.extend(b"\0" * (at - len(data)))
        groups_at = len(data)
        for group in groups:
            data.extend(struct.pack("<6I", *group))
        data.extend(struct.pack("<6I", len(old_mats), num_vertices, BEAM_VERTICES,
                                num_indices, BEAM_INDICES, len(groups)))
        put32(data, mesh + MESH_NUM_GROUPS, len(groups) + 1)
        put32(data, mesh + MESH_GROUP_OFFSET, groups_at - mesh)

    at = align_up(len(data), 4)
    data.extend(b"\0" * (at - len(data)))
    table_at = len(data)
    data.extend(struct.pack("<I", len(new_thins)))
    for value in new_thins:
        data.extend(struct.pack("<I", value))
    for value in new_fulls:
        data.extend(struct.pack("<Q", value))
    put32(data, HDR_MATERIAL_LIST, table_at)

    data.extend(b"\0" * (align_up(len(data), 16) - len(data)))
    stats = {
        "mode": "group", "vertices": BEAM_VERTICES, "indices": BEAM_INDICES,
        "main": len(main), "new_main": len(data),
        "gpu": len(gpu), "new_gpu": len(new_gpu),
    }
    return bytes(data), bytes(new_gpu), stats


def build_bundle(entries, header, type_entries):
    """按资源表重建 patch；GPU 偏移按类型条目的对齐要求重排。"""
    order = [struct.unpack_from("<Q", entry, 8)[0] for entry in type_entries]
    by_type = {}
    for item in entries:
        by_type.setdefault(item["type"], []).append(item)

    kept = []
    for type_id in order:
        items = by_type.pop(type_id, [])
        if items:
            kept.append((type_id, items))
    if by_type:
        raise SystemExit("resources use a type the template does not cover: %s"
                         % ", ".join("%016x" % t for t in by_type))

    extra = {}
    for entry in type_entries:
        _, _, type_id, _, _, main_align, gpu_align = TYPE_ENTRY.unpack(entry)
        extra[type_id] = (main_align, gpu_align)

    type_blob = bytearray()
    for type_id, items in kept:
        entry = bytearray(type_entries[order.index(type_id)])
        struct.pack_into("<I", entry, 16, len(items))
        type_blob.extend(entry)
    data_base = HEADER_SIZE + TYPE_ENTRY_SIZE * len(kept) + RES_ENTRY_SIZE * sum(
        len(items) for _, items in kept)

    rows = []
    main_body = bytearray()
    gpu_body = bytearray()
    for type_id, items in kept:
        main_align, gpu_align = extra[type_id]
        for item in items:
            at = align_up(data_base + len(main_body), main_align)
            main_body.extend(b"\0" * (at - data_base - len(main_body)))
            main_at = data_base + len(main_body)
            if item["gpu"]:
                at = align_up(len(gpu_body), gpu_align)
                gpu_body.extend(b"\0" * (at - len(gpu_body)))
                gpu_at = len(gpu_body)
                gpu_body.extend(item["gpu"])
            else:
                gpu_at = 0
            rows.append([item["name"], type_id, main_at, 0, gpu_at, 0, 0,
                         len(item["main"]), 0, len(item["gpu"]),
                         main_align, gpu_align, len(rows) + 1])
            main_body.extend(item["main"])

    out = bytearray(header)
    struct.pack_into("<II", out, 4, len(kept), len(rows))
    out.extend(type_blob)
    for row in rows:
        out.extend(REC.pack(*row))
    out.extend(main_body)
    struct.pack_into("<QQ", out, 0x20, len(out), len(gpu_body))
    return bytes(out), bytes(gpu_body)


def rows_of(data):
    nt, nr = struct.unpack_from("<II", data, 4)
    base = HEADER_SIZE + TYPE_ENTRY_SIZE * nt
    return [REC.unpack_from(data, base + RES_ENTRY_SIZE * i) for i in range(nr)]


def build():
    item = config()
    parts = read_source(item)
    shared, header, type_entries = bot_sources()
    highlight = highlight_bytes()

    entries = [dict(asset) for asset in shared]
    entries.append({"name": HIGHLIGHT_HASH, "type": TYPE_MATERIAL,
                    "main": highlight, "gpu": b""})
    entries.append({"name": BEAM_HIGHLIGHT_HASH, "type": TYPE_MATERIAL,
                    "main": beam_material_bytes(), "gpu": b""})

    ratios = item.get("beam_ratios", {})
    modes = item.get("beam_modes", {})
    stats = []
    for name in item["units"]:
        stem = name.rsplit("/", 1)[1]
        ratio = ratios.get(stem, 1.0)
        mode = modes.get(stem, "mesh")
        build_one = inject_group if mode == "group" else inject
        main, gpu, info = build_one(parts[stem]["main"], parts[stem]["gpu"],
                                    HIGHLIGHT_HASH, BEAM_HIGHLIGHT_HASH, ratio)
        entries.append({"name": idstring64(name), "type": TYPE_UNIT, "main": main, "gpu": gpu})
        info["unit"] = stem
        info["ratio"] = ratio
        stats.append(info)

    patch, gpu = build_bundle(entries, header, type_entries)
    out = OPTIONS_DIR / item["key"]
    out.mkdir(parents=True, exist_ok=True)
    (out / STEM).write_bytes(patch)
    (out / (STEM + ".gpu_resources")).write_bytes(gpu)
    (out / (STEM + ".stream")).write_bytes(b"")
    for info in stats:
        if info["mode"] == "mesh":
            where = "beam mesh #%d" % info["beam_mesh"]
        else:
            where = "beam in body group"
        print("  %-24s main %5d->%5d  gpu %7d->%8d  %-18s h=%.3f" % (
            info["unit"], info["main"], info["new_main"], info["gpu"], info["new_gpu"],
            where, BEAM_HEIGHT * info["ratio"]))
    print("%-12s patch=%7d gpu=%9d resources=%d" % (item["key"], len(patch), len(gpu), len(entries)))
    return 0


def verify_unit(unit_id, main, gpu, problems, label, ratio, mode):
    tag = "%s %016x" % (label, unit_id)
    expected_height = BEAM_HEIGHT * ratio
    try:
        meshes, layout = body_layout(main)
        stride = u32(main, layout + LAYOUT_VERTEX_STRIDE)
        num_vertices = u32(main, layout + LAYOUT_NUM_VERTICES)
        num_indices = u32(main, layout + LAYOUT_NUM_INDICES)
        vertex_base = u32(main, layout + LAYOUT_VERTEX_OFFSET)
        positions_size = u32(main, layout + LAYOUT_POSITIONS_SIZE)
        index_offset = u32(main, layout + LAYOUT_INDEX_OFFSET)
        indices_size = u32(main, layout + LAYOUT_INDICES_SIZE)
        if positions_size != num_vertices * stride:
            problems.append("%s: body positions size %d != %d*%d" % (
                tag, positions_size, num_vertices, stride))
        if index_offset + indices_size > len(gpu):
            problems.append("%s: body index block past the gpu buffer" % tag)

        thins, fulls = material_map(main)
        if len(set(thins)) != len(thins):
            problems.append("%s: duplicate material slots" % tag)
        mapping = dict(zip(thins, fulls))
        if mapping.get(BEAM_THIN) != BEAM_HIGHLIGHT_HASH:
            problems.append("%s: beam material mapping missing" % tag)
        slots = body_slots(main)
        if not slots:
            problems.append("%s: no body material slot" % tag)
        for slot in slots:
            if mapping[slot] != HIGHLIGHT_HASH:
                problems.append("%s: body slot %08x not highlighted" % (tag, slot))

        if mode == "group":
            for mesh in meshes:
                mats = mesh_materials(main, mesh)
                if mats != list(slots) + [BEAM_THIN]:
                    problems.append("%s: body mesh materials %s" % (tag, [hex(m) for m in mats]))
                    continue
                groups = mesh_groups(main, mesh)
                if len(groups) != 2:
                    problems.append("%s: body mesh has %d groups" % (tag, len(groups)))
                    continue
                grp = groups[-1]
                if (grp[0] != len(mats) - 1 or grp[1] != num_vertices - BEAM_VERTICES
                        or grp[2] != BEAM_VERTICES or grp[4] != BEAM_INDICES):
                    problems.append("%s: beam group %s" % (tag, grp))
                    continue
                if grp[1] + grp[2] != num_vertices or grp[3] + grp[4] != num_indices:
                    problems.append("%s: beam group is not the tail block" % tag)
                flat = set(struct.unpack_from(
                    "<%dH" % BEAM_INDICES, gpu, index_offset + grp[3] * 2))
                if max(flat) != num_vertices - 1 or min(flat) != grp[1]:
                    problems.append("%s: beam indices out of range" % tag)
                    continue
                pts = [struct.unpack_from("<3f", gpu, vertex_base + v * stride) for v in flat]
                height = max(p[2] for p in pts) - min(p[2] for p in pts)
                if abs(height - expected_height) > BEAM_HEIGHT_TOLERANCE:
                    problems.append("%s: beam height %.3f != %.3f" % (tag, height, expected_height))
                radius = max(max(abs(p[0]) for p in pts), max(abs(p[1]) for p in pts))
                if abs(radius - BEAM_RADIUS) > 0.002:
                    problems.append("%s: beam radius %.3f" % (tag, radius))
            return

        for mesh in meshes:
            if mesh_materials(main, mesh) != list(slots):
                problems.append("%s: body mesh materials changed %s" % (
                    tag, [hex(m) for m in mesh_materials(main, mesh)]))
            if len(mesh_groups(main, mesh)) != 1:
                problems.append("%s: body mesh gained extra groups" % tag)

        mesh_count = len(mesh_infos(main))
        mesh_data_at = u32(main, HDR_MESH_DATA)
        if not mesh_data_at or u32(main, mesh_data_at) != mesh_count:
            problems.append("%s: mesh data count does not match mesh info count" % tag)

        beams = [m for m in mesh_infos(main) if mesh_materials(main, m) == [BEAM_THIN]]
        if len(beams) != 1:
            problems.append("%s: %d beam meshes" % (tag, len(beams)))
            return
        beam = beams[0]
        beam_layout = layout_blocks(main)[mesh_layout(main, beam)]
        items = layout_items(main, beam_layout)
        if items != list(BEAM_ITEMS):
            problems.append("%s: beam layout items %s" % (tag, items))
        bstride = u32(main, beam_layout + LAYOUT_VERTEX_STRIDE)
        bverts = u32(main, beam_layout + LAYOUT_NUM_VERTICES)
        bindices = u32(main, beam_layout + LAYOUT_NUM_INDICES)
        bvbase = u32(main, beam_layout + LAYOUT_VERTEX_OFFSET)
        bvsize = u32(main, beam_layout + LAYOUT_POSITIONS_SIZE)
        bioff = u32(main, beam_layout + LAYOUT_INDEX_OFFSET)
        bisize = u32(main, beam_layout + LAYOUT_INDICES_SIZE)
        if (bstride, bverts, bindices) != (BEAM_STRIDE, BEAM_VERTICES, BEAM_INDICES):
            problems.append("%s: beam layout stride/verts/indices %d/%d/%d" % (
                tag, bstride, bverts, bindices))
        if bvsize != bverts * BEAM_STRIDE or bisize != bindices * 2:
            problems.append("%s: beam block sizes %d/%d" % (tag, bvsize, bisize))
        if bioff + bisize != len(gpu):
            problems.append("%s: beam index block does not end at the gpu tail" % tag)
        groups = mesh_groups(main, beam)
        if groups != [[0, 0, BEAM_VERTICES, 0, BEAM_INDICES, 0]]:
            problems.append("%s: beam groups %s" % (tag, groups))
        flat = list(struct.unpack_from("<%dH" % BEAM_INDICES, gpu, bioff))
        if max(flat) != BEAM_VERTICES - 1 or min(flat) != 0:
            problems.append("%s: beam indices out of range" % tag)
        pts = [struct.unpack_from("<3f", gpu, bvbase + v * BEAM_STRIDE)
               for v in range(bverts)]
        height = max(p[2] for p in pts) - min(p[2] for p in pts)
        if abs(height - expected_height) > BEAM_HEIGHT_TOLERANCE:
            problems.append("%s: beam height %.3f != %.3f" % (tag, height, expected_height))
        radius = max(max(abs(p[0]) for p in pts), max(abs(p[1]) for p in pts))
        if abs(radius - BEAM_RADIUS) > 0.002:
            problems.append("%s: beam radius %.3f" % (tag, radius))
        beam_index = mesh_infos(main).index(beam)
        refs = sum(1 for g in lod_groups(main) for e in g[1] if beam_index in e[2])
        if not refs:
            problems.append("%s: beam mesh is not referenced by any LOD entry" % tag)
    except SystemExit as exc:
        problems.append("%s: %s" % (tag, exc))


def verify():
    item = config()
    problems = []
    patch = OPTIONS_DIR / item["key"] / STEM
    gpu_path = Path(str(patch) + ".gpu_resources")
    stream_path = Path(str(patch) + ".stream")
    for p in (patch, gpu_path, stream_path):
        if not p.is_file():
            problems.append("missing %s" % p)
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1

    data = patch.read_bytes()
    gpu = gpu_path.read_bytes()
    highlight = highlight_bytes()
    if data[:4] != MAGIC:
        problems.append("not a bundle patch")
    f20, f28 = struct.unpack_from("<QQ", data, 0x20)
    if f20 != len(data) or f28 != len(gpu):
        problems.append("Approx fields %d/%d != %d/%d" % (f20, f28, len(data), len(gpu)))
    if stream_path.read_bytes() != b"":
        problems.append("stream file should be empty")

    rows = rows_of(data)
    ids = [(r[0], r[1]) for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate resources")
    if sorted(r[12] for r in rows) != list(range(1, len(rows) + 1)):
        problems.append("resource indices are not sequential")

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

    units = [r for r in rows if r[1] == TYPE_UNIT]
    if len(units) != len(item["units"]):
        problems.append("%d units, expected %d" % (len(units), len(item["units"])))
    for index, r in enumerate(units):
        if r[2] + r[7] > len(data) or r[4] + r[9] > len(gpu):
            problems.append("unit %016x points outside the patch" % r[0])
            continue
        stem = item["units"][index].rsplit("/", 1)[1] if index < len(item["units"]) else ""
        verify_unit(r[0], data[r[2]:r[2] + r[7]], gpu[r[4]:r[4] + r[9]], problems, "unit",
                    item.get("beam_ratios", {}).get(stem, 1.0),
                    item.get("beam_modes", {}).get(stem, "mesh"))

    mats = [r for r in rows if r[1] == TYPE_MATERIAL]
    hl = [r for r in mats if r[0] == HIGHLIGHT_HASH]
    if len(hl) != 1:
        problems.append("highlight material missing")
    elif data[hl[0][2]:hl[0][2] + hl[0][7]] != highlight:
        problems.append("highlight material bytes differ")
    beam_material = beam_material_bytes()
    bl = [r for r in mats if r[0] == BEAM_HIGHLIGHT_HASH]
    if len(bl) != 1:
        problems.append("beam highlight material missing")
    elif data[bl[0][2]:bl[0][2] + bl[0][7]] != beam_material:
        problems.append("beam highlight material bytes differ")

    shared, _, _ = bot_sources()
    by_name = {r[0]: r for r in rows}
    for asset in shared:
        r = by_name.get(asset["name"])
        if r is None:
            problems.append("shared resource %016x missing" % asset["name"])
            continue
        if data[r[2]:r[2] + r[7]] != asset["main"]:
            problems.append("shared resource %016x main differs" % asset["name"])
        if r[9] and gpu[r[4]:r[4] + r[9]] != asset["gpu"]:
            problems.append("shared resource %016x gpu differs" % asset["name"])

    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: bug option %d resources, %d units, highlight + standalone beam mesh" % (
        len(rows), len(units)))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    sys.exit(verify() if cmd == "verify" else build())
