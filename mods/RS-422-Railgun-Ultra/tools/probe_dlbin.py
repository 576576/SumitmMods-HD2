"""Report file-level entropy / compressibility of HD2 dl_bin data files."""
import collections
import math
import sys
import zlib
from pathlib import Path


def entropy(d: bytes) -> float:
    c = collections.Counter(d)
    n = len(d)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def runs(d: bytes, minimum: int = 8):
    out, cur = [], bytearray()
    for b in d:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= minimum:
                out.append(bytes(cur))
            cur = bytearray()
    if len(cur) >= minimum:
        out.append(bytes(cur))
    return out


for p in sys.argv[1:]:
    d = Path(p).read_bytes()
    printable = sum(1 for b in d if 32 <= b < 127) / len(d)
    print(f"{Path(p).name:52s} size={len(d):>8,} entropy={entropy(d):.3f} "
          f"distinct={len(set(d))} printable={printable:.2%}")
    r = runs(d)
    print(f"   printable runs>=8: {len(r)}  first={[x[:40] for x in r[:4]]}")
    for off in (0, 8, 16, 32):
        try:
            o = zlib.decompressobj()
            out = o.decompress(d[off:])
            print(f"   zlib@{off}: ok -> {len(out)}")
            break
        except Exception as e:
            print(f"   zlib@{off}: {type(e).__name__}")
