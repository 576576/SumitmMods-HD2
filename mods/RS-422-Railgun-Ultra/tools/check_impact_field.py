"""Decode the explosion table and cross-check the projectile table's +76 column:
if every value there is a valid explosion record id, +76 is the impact-effect
reference RS-422 rewrites at runtime."""
import struct
from pathlib import Path

from nacl.bindings import crypto_box_seal_open

PK = bytes.fromhex("2dc9aa7c30e6eed5f7fd0e0e95c7287299b126f79520cc41de750982ee7ff50c")
SK = bytes.fromhex("a0296b81310b58f013d8a9b343a1c292008003dfd9ab15fcf351543048b49b64")
GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2/data/game")
OUT = Path(__file__).resolve().parent.parent / "unsealed"

PROJ = {"stride": 272, "type": 0xBD4042C2}
EXPL = {"stride": 152, "type": 0x2AEA2592}


def unseal(name):
    data = (GAME / name).read_bytes()
    return crypto_box_seal_open(data, PK, SK)


def table(plain, stride):
    size = struct.unpack_from("<I", plain, 16)[0]
    typ = struct.unpack_from("<I", plain, 12)[0]
    body = 4 + 24
    n = size // stride
    rows = []
    for i in range(n):
        base = body + i * stride
        rid = struct.unpack_from("<I", plain, base + 16)[0]
        rows.append((rid, base))
    return typ, n, rows


def main():
    OUT.mkdir(exist_ok=True)

    ex = unseal("generated_explosion_settings.dl_bin")
    (OUT / "generated_explosion_settings.dl_bin").write_bytes(ex)
    etyp, en, erows = table(ex, EXPL["stride"])
    eids = sorted(r[0] for r in erows)
    print(f"explosion: type={etyp:#x} rows={en} stride={EXPL['stride']} "
          f"ids {min(eids)}..{max(eids)} unique={len(set(eids))}")

    pr = unseal("generated_projectile_settings.dl_bin")
    ptyp, pn, prows = table(pr, PROJ["stride"])
    print(f"projectile: type={ptyp:#x} rows={pn} stride={PROJ['stride']}")

    eset = set(eids)
    vals76 = []
    for rid, base in prows:
        v = struct.unpack_from("<I", pr, base + 76)[0]
        vals76.append((rid, v))
    inside = sum(1 for _, v in vals76 if v in eset)
    print(f"\n+76 inside explosion id set: {inside}/{len(vals76)} "
          f"({inside / len(vals76):.1%})")
    outside = [(r, v) for r, v in vals76 if v not in eset]
    print(f"outside: {len(outside)} -> {outside[:10]}")

    for rid in (186,):
        v = dict(vals76)[rid]
        print(f"\nrecord {rid}: +76 = {v}  (is explosion id: {v in eset})")

    # what do the four RS-422 effects look like?
    for eid in (343, 82, 52, 188):
        base = next((b for r, b in erows if r == eid), None)
        if base is None:
            print(f"explosion {eid}: MISSING")
            continue
        head = ex[base:base + 64]
        print(f"explosion {eid}: {head.hex(' ')}")


if __name__ == "__main__":
    main()
