"""Walk the projectile settings rows: id at +16, then the candidate fields."""
import struct
import sys
from pathlib import Path

UNSEALED = Path(__file__).resolve().parent.parent / "unsealed" / "generated_projectile_settings.dl_bin"
STRIDE = 272
BODY = 4 + 24


def main():
    p = UNSEALED.read_bytes()
    size = struct.unpack_from("<I", p, 4 + 12)[0]
    n = size // STRIDE
    print(f"{n} rows, stride {STRIDE}, body at {BODY:#x}")

    rows = []
    for i in range(n):
        base = BODY + i * STRIDE
        rid = struct.unpack_from("<I", p, base + 16)[0]
        rows.append((rid, i, base))

    ids = [r[0] for r in rows]
    print(f"id range {min(ids)}..{max(ids)}, unique {len(set(ids))}")
    print("first 20 ids:", ids[:20])

    for want in (186, 64, 343, 52, 82, 188):
        hit = [r for r in rows if r[0] == want]
        print(f"\n=== record id {want} -> {[(i, hex(b)) for _, i, b in hit]} ===")
        for rid, i, base in hit:
            row = p[base:base + STRIDE]
            print(f"  row {i}")
            print(f"  hex   {row[:80].hex(' ')}")
            for off in range(0, STRIDE, 4):
                u = struct.unpack_from("<I", p, base + off)[0]
                f = struct.unpack_from("<f", p, base + off)[0]
                if off in (16, 20, 24, 40, 44, 48, 52, 56, 60, 64, 68, 72, 76, 80,
                           144, 148, 152, 156, 160):
                    print(f"    +{off:<4} u32={u:<12} f32={f:<14.6g} ({u:#x})")


if __name__ == "__main__":
    main()
