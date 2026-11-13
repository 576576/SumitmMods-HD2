#!/usr/bin/env python3
"""重建归档 9ba626afa44a3aa3.patch_0：只留入口与核心两个 lua，删掉自带加载器、
自带 boot 两行，不再留任何占位。

删行会改变 TOC 行数与载荷偏移，所以要重排：类型表计数、行数、每行 offset、
文件总长度全部重算，其余字段与载荷按原字节搬运。
"""

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "source" / "9ba626afa44a3aa3.orig.patch_0"
TARGET = ROOT / "core" / "9ba626afa44a3aa3.patch_0"

ADAPTER_ID = "8e07dd1fd069812d"
LOADER_ID = "8f97174d125328ba"
CORE_ID = "cd0ac323d6682be0"
BOOT_ID = "fa95186993df76f4"
DROP = (LOADER_ID, BOOT_ID)

HEADER = 72
TYPE_ENTRY = 32
ROW_SIZE = 80
PAYLOAD_START = 1024
ALIGN = 16
# 核心这一行由我们自己定长度（原包 188,996 是原作者的排版，不是限制）。
GROW = {CORE_ID: 204_800}


def u32(buf, offset):
    return struct.unpack_from("<I", buf, offset)[0]


def align16(value):
    return (value + ALIGN - 1) // ALIGN * ALIGN


def parse(buf):
    types, count = u32(buf, 4), u32(buf, 8)
    type_base = HEADER
    row_base = HEADER + types * TYPE_ENTRY
    entries = []
    for i in range(types):
        o = type_base + i * TYPE_ENTRY
        entries.append({
            "hash": buf[o + 8:o + 16],
            "count": u32(buf, o + 16),
            "raw": bytearray(buf[o:o + TYPE_ENTRY]),
        })
    rows = []
    for i in range(count):
        r = row_base + i * ROW_SIZE
        offset = u32(buf, r + 16) | (u32(buf, r + 20) << 32)
        size = u32(buf, r + 56)
        rows.append({
            "id": buf[r:r + 8].hex(),
            "type": buf[r + 8:r + 16].hex(),
            "size": size,
            "raw": bytearray(buf[r:r + ROW_SIZE]),
            "payload": bytes(buf[offset:offset + size]),
        })
    return entries, rows


def packed(source, size):
    """8 字节前缀（长度、类型 2）+ 源码 + 补白，长度必须正好占满原槽位。"""
    data = source.encode("utf-8")
    if len(data) > size - 8:
        raise SystemExit("源码 %d 字节，槽位只有 %d" % (len(data), size - 8))
    return struct.pack("<II", len(data), 2) + data + b"\n" * (size - 8 - len(data))


def rebuild(entries, rows):
    kept = [row for row in rows if row["id"] not in DROP]
    dropped = [row["id"] for row in rows if row["id"] in DROP]
    if len(dropped) != len(DROP):
        raise SystemExit("要删的行不全：删掉 %s" % dropped)

    sources = {
        ADAPTER_ID: (ROOT / "core" / "2d8169d01fdd078e.lua").read_text(encoding="utf-8"),
        CORE_ID: (ROOT / "core" / "e02b68d623c30acd.lua").read_text(encoding="utf-8"),
    }
    for row in kept:
        if row["id"] in GROW:
            row["size"] = GROW[row["id"]]
        if row["id"] in sources:
            row["payload"] = packed(sources[row["id"]], row["size"])
            print("%-9s %d bytes / slot %d" % (row["id"][:8], len(row["payload"]) - 8 + 8,
                                               row["size"]))

    counts = {}
    for row in kept:
        counts[row["type"]] = counts.get(row["type"], 0) + 1
    for entry in entries:
        want = counts.get(entry["hash"].hex(), 0)
        entry["count"] = want
        struct.pack_into("<I", entry["raw"], 16, want)

    types = len(entries)
    body_start = PAYLOAD_START
    header = bytearray(SOURCE.read_bytes()[:HEADER])
    struct.pack_into("<I", header, 8, len(kept))

    row_bytes = bytearray()
    payloads = bytearray()
    cursor = body_start
    for row in kept:
        offset = align16(cursor)
        payloads.extend(b"\0" * (offset - cursor))
        struct.pack_into("<Q", row["raw"], 16, offset)
        struct.pack_into("<I", row["raw"], 56, len(row["payload"]))
        row_bytes.extend(row["raw"])
        payloads.extend(row["payload"])
        cursor = offset + len(row["payload"])
    struct.pack_into("<Q", header, 32, cursor)

    out = bytearray()
    out.extend(header)
    for entry in entries:
        out.extend(entry["raw"])
    out.extend(row_bytes)
    out.extend(b"\0" * (body_start - len(out)))
    out.extend(payloads)
    return bytes(out), kept, dropped


def verify(buf, kept, dropped):
    if u32(buf, 0) != 0xF0000011:
        raise SystemExit("归档头被破坏")
    entries, rows = parse(buf)
    if [row["id"] for row in rows] != [row["id"] for row in kept]:
        raise SystemExit("行顺序与预期不符")
    for rid in dropped:
        if rid in {row["id"] for row in rows}:
            raise SystemExit("要删的行还在：%s" % rid)

    seen = {}
    for row in rows:
        seen[row["type"]] = seen.get(row["type"], 0) + 1
    for entry in entries:
        if entry["count"] != seen.get(entry["hash"].hex(), 0):
            raise SystemExit("类型表计数没对上：%s" % entry["hash"].hex())

    cursor = PAYLOAD_START
    for i in range(len(rows)):
        raw = rows[i]["raw"]
        offset = struct.unpack_from("<Q", raw, 16)[0]
        if offset != align16(cursor):
            raise SystemExit("偏移没有对齐：%s" % rows[i]["id"])
        if offset + len(rows[i]["payload"]) > len(buf):
            raise SystemExit("载荷越界：%s" % rows[i]["id"])
        cursor = offset + len(rows[i]["payload"])
    if struct.unpack_from("<Q", buf, 32)[0] != cursor:
        raise SystemExit("文件总长度字段不对")
    if cursor != len(buf):
        raise SystemExit("实际长度 %d 与推算 %d 不符" % (len(buf), cursor))


def main():
    buf = bytearray(SOURCE.read_bytes())
    entries, rows = parse(buf)
    out, kept, dropped = rebuild(entries, rows)
    verify(out, kept, dropped)

    TARGET.write_bytes(out)
    print("dropped:", ", ".join(dropped))
    print("rows   : %d -> %d" % (len(rows), len(kept)))
    print("written:", TARGET, len(out), "bytes")


if __name__ == "__main__":
    main()
