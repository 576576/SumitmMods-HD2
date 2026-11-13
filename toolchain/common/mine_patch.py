#!/usr/bin/env python3
"""战备地雷选项（热熔 / 诱饵 / 铁蒺藜）：高亮外壳 + 光柱。

本体材质与本体几何一个字节都不动，改成新增一层「外壳」几何：整块复制本体顶点，绕本体
包围盒中心放大 SHELL_INFLATE 倍，挂一个纯自发光材质。游戏里看到的是发光的壳，模型本身
的贴图与材质保持原样。

为什么不直接换本体材质：本体材质换成 markers/m_emissive 那套纯自发光材质之后，军械库里
的 3D 预览会整个不显示——那套材质只在世界渲染里出东西。留一份带贴图的本体材质，预览就
有东西可画。

光柱是另一个独立 mesh，用自己的一份材质，颜色与本体分开调。
"""

import hashlib
import json
import math
import struct
import sys
from pathlib import Path

import bug_patch as bp

ROOT = bp.ROOT
TOOLS = bp.TOOLS
GROUPS_FILE = bp.GROUPS_FILE
OPTIONS_DIR = bp.OPTIONS_DIR
STEM = bp.STEM
REC = bp.REC
TYPE_ENTRY = bp.TYPE_ENTRY
TYPE_MATERIAL = bp.TYPE_MATERIAL
TYPE_UNIT = bp.TYPE_UNIT
MAGIC = bp.MAGIC
HEADER_SIZE = bp.HEADER_SIZE
TYPE_ENTRY_SIZE = bp.TYPE_ENTRY_SIZE
RES_ENTRY_SIZE = bp.RES_ENTRY_SIZE

HDR_MATERIAL_LIST = bp.HDR_MATERIAL_LIST
MESH_OFF_AABB = bp.MESH_OFF_AABB
MESH_NUM_MATERIALS = bp.MESH_NUM_MATERIALS
MESH_MATERIAL_OFFSET = bp.MESH_MATERIAL_OFFSET
MESH_NUM_GROUPS = bp.MESH_NUM_GROUPS
MESH_GROUP_OFFSET = bp.MESH_GROUP_OFFSET
LAYOUT_NUM_ITEMS = bp.LAYOUT_NUM_ITEMS
LAYOUT_ITEMS = bp.LAYOUT_ITEMS
LAYOUT_ITEM_SIZE = bp.LAYOUT_ITEM_SIZE
LAYOUT_MAX_ITEMS = (bp.LAYOUT_NUM_ITEMS - bp.LAYOUT_ITEMS) // bp.LAYOUT_ITEM_SIZE
LAYOUT_NUM_VERTICES = bp.LAYOUT_NUM_VERTICES
LAYOUT_VERTEX_STRIDE = bp.LAYOUT_VERTEX_STRIDE
LAYOUT_NUM_INDICES = bp.LAYOUT_NUM_INDICES
LAYOUT_VERTEX_OFFSET = bp.LAYOUT_VERTEX_OFFSET
LAYOUT_POSITIONS_SIZE = bp.LAYOUT_POSITIONS_SIZE
LAYOUT_INDEX_OFFSET = bp.LAYOUT_INDEX_OFFSET
LAYOUT_INDICES_SIZE = bp.LAYOUT_INDICES_SIZE

ITEM_POSITION = bp.ITEM_POSITION
ITEM_NORMAL = bp.ITEM_NORMAL
ITEM_UV = bp.ITEM_UV
FORMAT_SIZE = bp.FORMAT_SIZE
align_up = bp.align_up
u32 = bp.u32
put32 = bp.put32
idstring64 = bp.idstring64
mesh_infos = bp.mesh_infos
material_map = bp.material_map
mesh_materials = bp.mesh_materials
mesh_groups = bp.mesh_groups
layout_blocks = bp.layout_blocks
mesh_layout = bp.mesh_layout
layout_items = bp.layout_items
pack_normal = bp.pack_normal
build_bundle = bp.build_bundle
rows_of = bp.rows_of

# 阴影、角色阴影、碰撞、踩踏：都不是可见本体，高亮不碰它们。
PROXY_FULLS = (
    0x5990E5EFBCA8AE21,
    0x75F87AD2AE08E9C2,
    0x84B327056BAF1C6D,
    0x2CBE5D4D083AFB33,
)

SHELL_THIN = 0xC0FFABB3
BEAM_THIN = 0xC0FFABB2
BEAM_SEGMENTS = 32
BEAM_RADIUS = 0.042
BEAM_SOURCE_HEIGHT = 4.7868
BEAM_HEIGHT_RATIO = 0.8
BEAM_HEIGHT = BEAM_SOURCE_HEIGHT * BEAM_HEIGHT_RATIO
BEAM_VERTICES = BEAM_SEGMENTS * 2
BEAM_TRIANGLES = BEAM_SEGMENTS * 2
BEAM_INDICES = BEAM_TRIANGLES * 3
BEAM_HEIGHT_TOLERANCE = 0.05
BEAM_BASE_SINK = 0.02
BEAM_ITEMS = bp.BEAM_ITEMS
BEAM_STRIDE = bp.BEAM_STRIDE

# 外壳相对本体放大 2%：太小会和本体深度打架，太大能看出两层。
SHELL_INFLATE = 1.02
SHELL_INFLATE_TOLERANCE = 0.004

SHELL_MESH_NAME = idstring64("mine_shell_mesh") & 0xFFFFFFFF
BEAM_MESH_NAME = idstring64("mine_beam_mesh") & 0xFFFFFFFF

TRANSFORM = "mine_patch"


def items_of():
    cfg = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    return [item for item in cfg.get("standalone_options", [])
            if item.get("transform") == TRANSFORM]


def material_bytes(item, key):
    path = TOOLS / item[key]["file"]
    meta = json.loads((TOOLS / item[key]["meta"]).read_text(encoding="utf-8"))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest().upper() != meta["sha256"]:
        raise SystemExit("%s checksum mismatch" % path.name)
    if len(data) != 208:
        raise SystemExit("unexpected %s size: %d" % (path.name, len(data)))
    return data


def read_source(item):
    import zipfile
    with zipfile.ZipFile(ROOT / item["source"]) as z:
        parts = {}
        for path in item["units"]:
            stem = path.rsplit("/", 1)[1]
            main = [n for n in z.namelist() if stem in n and n.endswith(".main")][0]
            gpu = [n for n in z.namelist() if stem in n and n.endswith(".gpu")][0]
            parts[stem] = {"main": z.read(main), "gpu": z.read(gpu)}
    return parts


def body_slots(data):
    """可见本体的材质槽：非代理、非外壳、非光柱。"""
    thins, fulls = material_map(data)
    return [t for t, f in zip(thins, fulls)
            if f not in PROXY_FULLS and t not in (SHELL_THIN, BEAM_THIN)]


def body_meshes(data):
    """用本体材质、且不是光柱那个单组的 mesh。"""
    slots = set(body_slots(data))
    beam_groups = [[0, 0, BEAM_VERTICES, 0, BEAM_INDICES, 0]]
    return [m for m in mesh_infos(data)
            if slots & set(mesh_materials(data, m))
            and not (mesh_materials(data, m) == [SHELL_THIN] or mesh_materials(data, m) == [BEAM_THIN])
            and mesh_groups(data, m) != beam_groups]


def body_layout(data):
    meshes = body_meshes(data)
    if not meshes:
        raise SystemExit("unit has no mesh using a visible body material")
    ids = {mesh_layout(data, m) for m in meshes}
    if len(ids) != 1:
        raise SystemExit("body meshes use %d different layouts" % len(ids))
    layout_id = ids.pop()
    layouts = layout_blocks(data)
    if not 0 <= layout_id < len(layouts):
        raise SystemExit("body mesh layout index %d is out of range" % layout_id)
    return meshes, layouts[layout_id]


def attribute_offsets(items, stride):
    """按布局项的声明顺序累加各属性的字节偏移；总长必须等于 stride。"""
    offs = {}
    at = 0
    for type_id, fmt, layer in items:
        if fmt not in FORMAT_SIZE:
            raise SystemExit("unsupported vertex format %d" % fmt)
        offs.setdefault((type_id, layer), (at, fmt))
        at += FORMAT_SIZE[fmt]
    if at != stride:
        raise SystemExit("layout items take %d bytes, stride is %d" % (at, stride))
    return offs


def beam_vertex(position, normal, uv):
    """光柱顶点：位置 + packed 法线 + float16 UV，不带蒙皮。

    带蒙皮的话这几个 3.8 m 高的顶点会被算进骨骼包围盒，军备预览据此取景，本体就缩成一点。
    """
    buf = bytearray(BEAM_STRIDE)
    struct.pack_into("<3f", buf, 0, *position)
    struct.pack_into("<I", buf, 12, pack_normal(normal[0], normal[1], 0.0))
    struct.pack_into("<2e", buf, 16, uv[0], uv[1])
    return bytes(buf)


def beam_vertices(z_bottom, height=None):
    height = BEAM_HEIGHT if height is None else height
    verts = []
    for v, z in ((0.0, z_bottom), (1.0, z_bottom + height)):
        for i in range(BEAM_SEGMENTS):
            angle = 2 * math.pi * i / BEAM_SEGMENTS
            nx, ny = math.cos(angle), math.sin(angle)
            verts.append(beam_vertex(
                (nx * BEAM_RADIUS, ny * BEAM_RADIUS, z), (nx, ny), (i / BEAM_SEGMENTS, v)))
    return b"".join(verts)


def beam_indices():
    indices = []
    for i in range(BEAM_SEGMENTS):
        j = (i + 1) % BEAM_SEGMENTS
        indices.extend((i, j, BEAM_SEGMENTS + j, i, BEAM_SEGMENTS + j, BEAM_SEGMENTS + i))
    return indices


def body_bounds(data, meshes):
    mins = [min(struct.unpack_from("<f", data, m + MESH_OFF_AABB + 4 * k)[0] for m in meshes)
            for k in range(3)]
    maxs = [max(struct.unpack_from("<f", data, m + MESH_OFF_AABB + 12 + 4 * k)[0] for m in meshes)
            for k in range(3)]
    return mins, maxs


def inflate(mins, maxs):
    center = [(mins[k] + maxs[k]) / 2.0 for k in range(3)]
    lo = [center[k] + (mins[k] - center[k]) * SHELL_INFLATE for k in range(3)]
    hi = [center[k] + (maxs[k] - center[k]) * SHELL_INFLATE for k in range(3)]
    return center, lo, hi


def shell_vertex_block(gpu, vertex_base, positions_size, stride, pos_at, center):
    """整块复制本体顶点，位置绕本体包围盒中心放大 SHELL_INFLATE 倍。"""
    block = bytearray(gpu[vertex_base:vertex_base + positions_size])
    for at in range(pos_at, len(block), stride):
        x, y, z = struct.unpack_from("<3f", block, at)
        struct.pack_into("<3f", block, at,
                         center[0] + (x - center[0]) * SHELL_INFLATE,
                         center[1] + (y - center[1]) * SHELL_INFLATE,
                         center[2] + (z - center[2]) * SHELL_INFLATE)
    return bytes(block)


def new_block(data, template, name, layout_index, material, groups, aabb):
    """拼一个 mesh 块：128 字节头 + 材质表 + 组表，偏移都相对块起点。"""
    block = bytearray(data[template:template + bp.MESH_HEADER_SIZE])
    struct.pack_into("<6f", block, MESH_OFF_AABB, *(list(aabb[0]) + list(aabb[1])))
    put32(block, bp.MESH_OFF_MESH_NAME, name)
    put32(block, bp.MESH_OFF_LAYOUT, layout_index)
    put32(block, MESH_NUM_MATERIALS, 1)
    put32(block, MESH_MATERIAL_OFFSET, bp.MESH_HEADER_SIZE)
    put32(block, MESH_NUM_GROUPS, len(groups))
    put32(block, MESH_GROUP_OFFSET, bp.MESH_HEADER_SIZE + 4)
    block.extend(struct.pack("<I", material))
    for group in groups:
        block.extend(struct.pack("<6I", *group))
    return bytes(block)


def new_layout(data, layout, vertex_base, num_vertices, stride, index_offset, num_indices,
               items=None):
    block = bytearray(data[layout:layout + bp.MESH_LAYOUT_SIZE])
    if items is not None:
        put32(block, LAYOUT_NUM_ITEMS, len(items))
        for k in range(LAYOUT_MAX_ITEMS):
            if k < len(items):
                struct.pack_into("<III", block, LAYOUT_ITEMS + LAYOUT_ITEM_SIZE * k, *items[k])
            else:
                struct.pack_into("<III", block, LAYOUT_ITEMS + LAYOUT_ITEM_SIZE * k, 0, 0, 0)
    put32(block, LAYOUT_NUM_VERTICES, num_vertices)
    put32(block, LAYOUT_VERTEX_STRIDE, stride)
    put32(block, LAYOUT_NUM_INDICES, num_indices)
    put32(block, LAYOUT_VERTEX_OFFSET, vertex_base)
    put32(block, LAYOUT_POSITIONS_SIZE, num_vertices * stride)
    put32(block, LAYOUT_INDEX_OFFSET, index_offset)
    put32(block, LAYOUT_INDICES_SIZE, num_indices * 2)
    return bp.rebuild_layout_list(data, bytes(block))


def inject(main, gpu, shell_hash, beam_hash, ratio=1.0):
    """返回 (new_main, new_gpu, 统计)。本体材质与本体几何一个字节都不动。"""
    data = bytearray(main)
    meshes, layout = body_layout(data)
    items = layout_items(data, layout)
    stride = u32(data, layout + LAYOUT_VERTEX_STRIDE)
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

    offs = attribute_offsets(items, stride)
    pos_at, pos_fmt = offs[(ITEM_POSITION, 0)]
    if pos_fmt != 2:
        raise SystemExit("unsupported position format %d" % pos_fmt)

    mins, maxs = body_bounds(data, meshes)
    center, shell_mins, shell_maxs = inflate(mins, maxs)

    # ---- 高亮外壳：本体顶点整块复制、放大，挂自发光材质 ----
    new_gpu = bytearray(gpu)
    new_gpu.extend(b"\0" * (align_up(len(new_gpu), 16) - len(new_gpu)))
    shell_vertex_base = len(new_gpu)
    new_gpu.extend(shell_vertex_block(gpu, vertex_base, positions_size, stride, pos_at, center))
    shell_index_offset = align_up(len(new_gpu), 4)
    new_gpu.extend(b"\0" * (shell_index_offset - len(new_gpu)))
    new_gpu.extend(gpu[index_offset:index_offset + indices_size])

    shell_layout = new_layout(data, layout, shell_vertex_base, num_vertices, stride,
                              shell_index_offset, num_indices)
    # 外壳只用最细那一级 LOD 的组：五级 LOD 同时画会互相穿插。
    primary = max(meshes, key=lambda m: sum(g[2] for g in mesh_groups(data, m)))
    shell_groups = [(0, group[1], group[2], group[3], group[4])
                    for group in mesh_groups(data, primary)]
    shell_block = new_block(data, primary, SHELL_MESH_NAME, shell_layout, SHELL_THIN,
                            [(g[0], g[1], g[2], g[3], g[4], i) for i, g in enumerate(shell_groups)],
                            (shell_mins, shell_maxs))
    shell_mesh = bp.rebuild_mesh_list(data, shell_block)

    # ---- 光柱 ----
    bottom_z = mins[2] - BEAM_BASE_SINK
    top_z = bottom_z + BEAM_HEIGHT * ratio

    new_gpu.extend(b"\0" * (align_up(len(new_gpu), 16) - len(new_gpu)))
    beam_vertex_base = len(new_gpu)
    new_gpu.extend(beam_vertices(bottom_z, BEAM_HEIGHT * ratio))
    beam_index_offset = align_up(len(new_gpu), 4)
    new_gpu.extend(b"\0" * (beam_index_offset - len(new_gpu)))
    new_gpu.extend(struct.pack("<%dH" % BEAM_INDICES, *beam_indices()))

    beam_layout = new_layout(data, layout, beam_vertex_base, BEAM_VERTICES, BEAM_STRIDE,
                             beam_index_offset, BEAM_INDICES, items=BEAM_ITEMS)
    # 包围盒不跟着光柱长高：军备预览按 mesh 包围盒取景，3.8 m 的柱子会把镜头拉远到看不清本体。
    beam_block = new_block(data, meshes[0], BEAM_MESH_NAME, beam_layout, BEAM_THIN,
                           [(0, 0, BEAM_VERTICES, 0, BEAM_INDICES, 0)],
                           (shell_mins, shell_maxs))
    beam_mesh = bp.rebuild_mesh_list(data, beam_block)

    # ---- mesh data 计数 ----
    mesh_data_at = u32(data, bp.HDR_MESH_DATA)
    if not mesh_data_at:
        raise SystemExit("unit has no mesh data list")
    mesh_count = len(mesh_infos(main))
    if u32(data, mesh_data_at) != mesh_count:
        raise SystemExit("mesh data count %d does not match mesh info count %d"
                         % (u32(data, mesh_data_at), mesh_count))
    at = align_up(len(data), 16)
    data.extend(b"\0" * (at - len(data)))
    new_mesh_data = len(data)
    data.extend(struct.pack("<I", mesh_count + 2) + b"\0" * 12)
    put32(data, bp.HDR_MESH_DATA, new_mesh_data)

    # ---- LOD 登记：外壳与光柱都登记进渲染本体的每一级 LOD ----
    body_ids = [mesh_infos(main).index(m) for m in meshes]
    bp.rebuild_lod_list(data, shell_mesh, body_ids)
    bp.rebuild_lod_list(data, beam_mesh, body_ids)

    # ---- 材质表：只追加外壳与光柱两项 ----
    thins, fulls = material_map(data)
    if SHELL_THIN in thins or BEAM_THIN in thins:
        raise SystemExit("unit already carries the shell/beam material slots")
    table_at = align_up(len(data), 4)
    data.extend(b"\0" * (table_at - len(data)))
    table = list(zip(thins, fulls)) + [(SHELL_THIN, shell_hash), (BEAM_THIN, beam_hash)]
    data.extend(struct.pack("<I", len(table)))
    for thin, _ in table:
        data.extend(struct.pack("<I", thin))
    for _, full in table:
        data.extend(struct.pack("<Q", full))
    put32(data, HDR_MATERIAL_LIST, table_at)

    data.extend(b"\0" * (align_up(len(data), 16) - len(data)))
    stats = {
        "mode": "shell+beam", "shell_mesh": shell_mesh, "beam_mesh": beam_mesh,
        "shell_layout": shell_layout, "beam_layout": beam_layout,
        "vertices": num_vertices, "indices": num_indices,
        "beam_vertices": BEAM_VERTICES, "beam_indices": BEAM_INDICES,
        "groups": len(shell_groups), "stride": stride, "meshes": len(meshes),
        "main": len(main), "new_main": len(data),
        "gpu": len(gpu), "new_gpu": len(new_gpu),
        "beam_bottom": bottom_z, "beam_top": top_z, "ratio": ratio,
    }
    return bytes(data), bytes(new_gpu), stats


def build():
    items = items_of()
    if not items:
        raise SystemExit("groups.json has no mine_patch option")
    bundle = bp.bot_sources()
    header, type_entries = bundle[1], bundle[2]

    for item in items:
        shell = idstring64(item["shell"]["name"])
        beam = idstring64(item["beam"]["name"])
        parts = read_source(item)
        units = [name.rsplit("/", 1)[1] for name in item["units"]]
        hashes = {stem: idstring64(item["units"][i]) for i, stem in enumerate(units)}

        entries = [{"name": shell, "type": TYPE_MATERIAL,
                    "main": material_bytes(item, "shell"), "gpu": b""},
                   {"name": beam, "type": TYPE_MATERIAL,
                    "main": material_bytes(item, "beam"), "gpu": b""}]
        stats = []
        for stem in units:
            main, gpu, info = inject(parts[stem]["main"], parts[stem]["gpu"], shell, beam)
            entries.append({"name": hashes[stem], "type": TYPE_UNIT, "main": main, "gpu": gpu})
            info["unit"] = stem
            stats.append(info)

        patch, gpu = build_bundle(entries, header, type_entries)
        out = OPTIONS_DIR / item["key"]
        out.mkdir(parents=True, exist_ok=True)
        (out / STEM).write_bytes(patch)
        (out / (STEM + ".gpu_resources")).write_bytes(gpu)
        (out / (STEM + ".stream")).write_bytes(b"")
        for info in stats:
            print("  %-14s %-13s main %5d->%5d  gpu %7d->%8d  %d LOD meshes  "
                  "beam %.3f..%.3f" % (item["key"], info["unit"], info["main"], info["new_main"],
                                       info["gpu"], info["new_gpu"], info["meshes"],
                                       info["beam_bottom"], info["beam_top"]))
        print("%-12s patch=%7d gpu=%9d resources=%d" % (
            item["key"], len(patch), len(gpu), len(entries)))
    return 0


def verify_unit(unit_id, main, gpu, source_main, source_gpu, shell, beam, problems):
    tag = "%016x" % unit_id
    try:
        ref_thins, ref_fulls = material_map(source_main)
        thins, fulls = material_map(main)
        if len(set(thins)) != len(thins):
            problems.append("%s: duplicate material slots" % tag)
        mapping = dict(zip(thins, fulls))
        ref_map = dict(zip(ref_thins, ref_fulls))
        for thin, full in ref_map.items():
            if mapping.get(thin) != full:
                problems.append("%s: body slot %08x no longer maps to its own material" % (tag, thin))
        if mapping.get(SHELL_THIN) != shell:
            problems.append("%s: shell material slot missing" % tag)
        if mapping.get(BEAM_THIN) != beam:
            problems.append("%s: beam material slot missing" % tag)
        if len(thins) != len(ref_thins) + 2:
            problems.append("%s: material table has %d slots, expected %d" % (
                tag, len(thins), len(ref_thins) + 2))

        meshes, layout = body_layout(main)
        stride = u32(main, layout + LAYOUT_VERTEX_STRIDE)
        num_vertices = u32(main, layout + LAYOUT_NUM_VERTICES)
        num_indices = u32(main, layout + LAYOUT_NUM_INDICES)
        vertex_base = u32(main, layout + LAYOUT_VERTEX_OFFSET)
        positions_size = u32(main, layout + LAYOUT_POSITIONS_SIZE)
        index_offset = u32(main, layout + LAYOUT_INDEX_OFFSET)
        indices_size = u32(main, layout + LAYOUT_INDICES_SIZE)
        if positions_size != num_vertices * stride:
            problems.append("%s: positions size %d != %d*%d" % (
                tag, positions_size, num_vertices, stride))
        if indices_size != num_indices * 2:
            problems.append("%s: indices size %d != %d*2" % (tag, indices_size, num_indices))

        pos_at, _ = attribute_offsets(layout_items(main, layout), stride)[(ITEM_POSITION, 0)]

        # 本体几何必须还在原位、内容不变
        ref_layout = body_layout(source_main)[1]
        ref_vbase = u32(source_main, ref_layout + LAYOUT_VERTEX_OFFSET)
        ref_ioff = u32(source_main, ref_layout + LAYOUT_INDEX_OFFSET)
        ref_positions = u32(source_main, ref_layout + LAYOUT_POSITIONS_SIZE)
        ref_indices = u32(source_main, ref_layout + LAYOUT_INDICES_SIZE)
        if gpu[vertex_base:vertex_base + positions_size] != \
                source_gpu[ref_vbase:ref_vbase + ref_positions]:
            problems.append("%s: body vertex block changed" % tag)
        if gpu[index_offset:index_offset + indices_size] != \
                source_gpu[ref_ioff:ref_ioff + ref_indices]:
            problems.append("%s: body index block changed" % tag)

        shells = [m for m in mesh_infos(main) if mesh_materials(main, m) == [SHELL_THIN]]
        beams = [m for m in mesh_infos(main) if mesh_materials(main, m) == [BEAM_THIN]]
        if len(shells) != 1:
            problems.append("%s: %d shell meshes" % (tag, len(shells)))
        if len(beams) != 1:
            problems.append("%s: %d beam meshes" % (tag, len(beams)))
        if len(shells) != 1 or len(beams) != 1:
            return
        shell_mesh, beam_mesh = shells[0], beams[0]

        # 外壳：组表取最细那一级 LOD、顶点是本体副本、位置正好放大 SHELL_INFLATE 倍
        primary = max(meshes, key=lambda m: sum(g[2] for g in mesh_groups(main, m)))
        body_groups = [(0, g[1], g[2], g[3], g[4]) for g in mesh_groups(main, primary)]
        shell_groups = [(g[0], g[1], g[2], g[3], g[4]) for g in mesh_groups(main, shell_mesh)]
        if shell_groups != body_groups:
            problems.append("%s: shell groups differ from the finest body LOD (%d vs %d)" % (
                tag, len(shell_groups), len(body_groups)))
        shell_layout = layout_blocks(main)[mesh_layout(main, shell_mesh)]
        if u32(main, shell_layout + LAYOUT_NUM_VERTICES) != num_vertices:
            problems.append("%s: shell layout vertex count %d" % (
                tag, u32(main, shell_layout + LAYOUT_NUM_VERTICES)))
        if u32(main, shell_layout + LAYOUT_NUM_INDICES) != num_indices:
            problems.append("%s: shell layout index count %d" % (
                tag, u32(main, shell_layout + LAYOUT_NUM_INDICES)))
        shell_vbase = u32(main, shell_layout + LAYOUT_VERTEX_OFFSET)
        shell_ioff = u32(main, shell_layout + LAYOUT_INDEX_OFFSET)
        if shell_vbase + positions_size > len(gpu) or shell_ioff + indices_size > len(gpu):
            problems.append("%s: shell blocks outside the gpu buffer" % tag)
            return
        if gpu[shell_ioff:shell_ioff + indices_size] != gpu[index_offset:index_offset + indices_size]:
            problems.append("%s: shell index block differs from the body" % tag)
        mins, maxs = body_bounds(main, meshes)
        center = [(mins[k] + maxs[k]) / 2.0 for k in range(3)]
        worst = 0.0
        for v in (0, num_vertices // 3, 2 * num_vertices // 3, num_vertices - 1):
            src = struct.unpack_from("<3f", gpu, vertex_base + v * stride + pos_at)
            dst = struct.unpack_from("<3f", gpu, shell_vbase + v * stride + pos_at)
            for k in range(3):
                want = center[k] + (src[k] - center[k]) * SHELL_INFLATE
                worst = max(worst, abs(dst[k] - want))
        if worst > SHELL_INFLATE_TOLERANCE:
            problems.append("%s: shell inflation off by %.4f" % (tag, worst))

        # 光柱：几何、高度、半径、索引在 GPU 尾部
        beam_layout = layout_blocks(main)[mesh_layout(main, beam_mesh)]
        if u32(main, beam_layout + LAYOUT_NUM_VERTICES) != BEAM_VERTICES:
            problems.append("%s: beam vertices %d" % (tag, u32(main, beam_layout + LAYOUT_NUM_VERTICES)))
        if u32(main, beam_layout + LAYOUT_NUM_INDICES) != BEAM_INDICES:
            problems.append("%s: beam indices %d" % (tag, u32(main, beam_layout + LAYOUT_NUM_INDICES)))
        if u32(main, beam_layout + LAYOUT_VERTEX_STRIDE) != BEAM_STRIDE:
            problems.append("%s: beam stride %d" % (tag, u32(main, beam_layout + LAYOUT_VERTEX_STRIDE)))
        if layout_items(main, beam_layout) != list(BEAM_ITEMS):
            problems.append("%s: beam layout is not the unskinned one" % tag)
        beam_vbase = u32(main, beam_layout + LAYOUT_VERTEX_OFFSET)
        beam_ioff = u32(main, beam_layout + LAYOUT_INDEX_OFFSET)
        beam_isize = u32(main, beam_layout + LAYOUT_INDICES_SIZE)
        if (beam_vbase + BEAM_VERTICES * BEAM_STRIDE > len(gpu)
                or beam_ioff + beam_isize != len(gpu)):
            problems.append("%s: beam blocks are not the gpu tail" % tag)
            return
        pts = [struct.unpack_from("<3f", gpu, beam_vbase + v * BEAM_STRIDE)
               for v in range(BEAM_VERTICES)]
        height = max(p[2] for p in pts) - min(p[2] for p in pts)
        if abs(height - BEAM_HEIGHT) > BEAM_HEIGHT_TOLERANCE:
            problems.append("%s: beam height %.3f" % (tag, height))
        radius = max(max(abs(p[0]), abs(p[1])) for p in pts)
        if abs(radius - BEAM_RADIUS) > 0.002:
            problems.append("%s: beam radius %.3f" % (tag, radius))

        infos = mesh_infos(main)
        for name, mesh_id in (("shell", shell_mesh), ("beam", beam_mesh)):
            index = infos.index(mesh_id)
            refs = sum(1 for g in bp.lod_groups(main) for e in g[1] if index in e[2])
            if not refs:
                problems.append("%s: %s mesh is not referenced by any LOD entry" % (tag, name))
        mesh_data_at = u32(main, bp.HDR_MESH_DATA)
        if u32(main, mesh_data_at) != len(infos):
            problems.append("%s: mesh data count does not match mesh info count" % tag)
    except SystemExit as exc:
        problems.append("%s: %s" % (tag, exc))


def verify():
    items = items_of()
    problems = []
    for item in items:
        patch = OPTIONS_DIR / item["key"] / STEM
        gpu_path = Path(str(patch) + ".gpu_resources")
        stream_path = Path(str(patch) + ".stream")
        if not all(p.is_file() for p in (patch, gpu_path, stream_path)):
            problems.append("%s: option files missing" % item["key"])
            continue
        data = patch.read_bytes()
        gpu = gpu_path.read_bytes()
        if data[:4] != MAGIC:
            problems.append("%s: not a bundle patch" % item["key"])
        f20, f28 = struct.unpack_from("<QQ", data, 0x20)
        if f20 != len(data) or f28 != len(gpu):
            problems.append("%s: Approx fields %d/%d != %d/%d" % (
                item["key"], f20, f28, len(data), len(gpu)))
        if stream_path.read_bytes() != b"":
            problems.append("%s: stream file should be empty" % item["key"])

        rows = rows_of(data)
        shell = idstring64(item["shell"]["name"])
        beam = idstring64(item["beam"]["name"])
        mats = [r for r in rows if r[1] == TYPE_MATERIAL]
        if sorted(r[0] for r in mats) != sorted((shell, beam)):
            problems.append("%s: material rows %s" % (
                item["key"], ["%016x" % r[0] for r in mats]))
        for r in mats:
            key = "shell" if r[0] == shell else "beam"
            if data[r[2]:r[2] + r[7]] != material_bytes(item, key):
                problems.append("%s: %s material bytes differ" % (item["key"], key))

        units = [r for r in rows if r[1] == TYPE_UNIT]
        if len(units) != len(item["units"]):
            problems.append("%s: %d units, expected %d" % (
                item["key"], len(units), len(item["units"])))
            continue
        parts = read_source(item)
        by_hash = {idstring64(path): path.rsplit("/", 1)[1] for path in item["units"]}
        for r in units:
            if r[2] + r[7] > len(data) or r[4] + r[9] > len(gpu):
                problems.append("%s: unit %016x points outside the patch" % (item["key"], r[0]))
                continue
            stem = by_hash.get(r[0])
            if stem is None:
                problems.append("%s: unit %016x not in the source" % (item["key"], r[0]))
                continue
            verify_unit(r[0], data[r[2]:r[2] + r[7]], gpu[r[4]:r[4] + r[9]],
                        parts[stem]["main"], parts[stem]["gpu"], shell, beam, problems)
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: %d stratagem mine options (highlight shell + beam)" % len(items))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    sys.exit(verify() if cmd == "verify" else build())
