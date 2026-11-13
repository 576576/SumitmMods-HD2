"""Data-driven field hunt for generated_projectile_settings.dl_bin.

For every 4-byte slot in the 272-byte record, report how the values are spread
across all rows. Reference-id fields show up as a narrow 1..N range with many
distinct values; f32 physics fields show up as smooth floats.
"""
import struct
from collections import Counter
from pathlib import Path

UNSEALED = Path(__file__).resolve().parent.parent / "unsealed" / "generated_projectile_settings.dl_bin"
STRIDE = 272
BODY = 4 + 24


def main():
    p = UNSEALED.read_bytes()
    size = struct.unpack_from("<I", p, 4 + 12)[0]
    n = size // STRIDE
    rows = [BODY + i * STRIDE for i in range(n)]

    for off in range(0, STRIDE, 4):
        vals = [struct.unpack_from("<I", p, b + off)[0] for b in rows]
        fl = [struct.unpack_from("<f", p, b + off)[0] for b in rows]
        distinct = len(set(vals))
        lo, hi = min(vals), max(vals)
        # a plausible thin-hash / id column: many distinct, all small ints
        looks_id = hi <= 4096 and distinct > n * 0.6 and lo >= 1
        # a plausible float column: most values are finite and human scale
        finite = [v for v in fl if v == v and abs(v) < 1e6]
        looks_f32 = len(finite) > n * 0.9 and distinct > n * 0.5 and not looks_id
        tag = ""
        if looks_id:
            tag = " <== ID-LIKE"
        elif looks_f32:
            tag = " <== f32-LIKE"
        if tag:
            sample = Counter(vals).most_common(5)
            print(f"+{off:<4} distinct={distinct:<4} min={lo:<12} max={hi:<12} "
                  f"top={sample}{tag}")


if __name__ == "__main__":
    main()
