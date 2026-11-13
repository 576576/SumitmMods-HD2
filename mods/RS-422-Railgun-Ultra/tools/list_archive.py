"""Dump the resource rows of an HD2 Arsenal patch archive."""
import struct
import sys
from pathlib import Path


def u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def rows(buf):
    types, count = u32(buf, 4), u32(buf, 8)
    base = 72 + types * 32
    out = []
    for i in range(count):
        r = base + i * 80
        out.append({
            "row": r,
            "id": buf[r:r + 8].hex(),
            "type": buf[r + 8:r + 16].hex(),
            "offset": u32(buf, r + 16) | (u32(buf, r + 20) << 32),
            "size": u32(buf, r + 56),
        })
    return out


def type_names(buf):
    types = u32(buf, 4)
    names = []
    for i in range(types):
        o = 72 + i * 32
        names.append(buf[o:o + 8].hex())
    return names


def main(path):
    buf = Path(path).read_bytes()
    print(f"{path}  {len(buf):,} bytes  magic={u32(buf, 0):#x}  types={u32(buf, 4)} rows={u32(buf, 8)}")
    for r in rows(buf):
        head = buf[r["offset"]:r["offset"] + 8]
        plen, ptype = struct.unpack_from("<IH", head)
        kind = "LUA" if r["type"] == "e217d12cfa8d4ea1" else ""
        preview = ""
        body = buf[r["offset"] + 8:r["offset"] + 8 + min(120, max(0, plen))]
        if r["type"] == "e217d12cfa8d4ea1":
            preview = body.decode("utf-8", "replace").split("\n")[0][:100]
        else:
            preview = body[:16].hex()
        print(f"  row@{r['row']:#06x} id={r['id']} type={r['type']} "
              f"off={r['offset']:#x} size={r['size']:>7,} plen={plen:>7,} ptype={ptype} {kind} {preview}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
        print()
