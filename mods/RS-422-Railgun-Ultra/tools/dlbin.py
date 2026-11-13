"""Unseal a generated_*.dl_bin with the game's own sealed-box keypair and walk its
row tables, so we can see record ids and pick field offsets."""
import struct
import sys
from pathlib import Path

from nacl.bindings import crypto_box_seal_open

PK = bytes.fromhex("2dc9aa7c30e6eed5f7fd0e0e95c7287299b126f79520cc41de750982ee7ff50c")
SK = bytes.fromhex("a0296b81310b58f013d8a9b343a1c292008003dfd9ab15fcf351543048b49b64")

GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2/data/game")
OUT = Path(__file__).resolve().parent.parent / "unsealed"


def unseal(data: bytes) -> bytes:
    return crypto_box_seal_open(data, PK, SK)


def seal(plain: bytes) -> bytes:
    from nacl.bindings import crypto_box_seal
    return crypto_box_seal(plain, PK)


def headers(p: bytes):
    """LDLD container: u32 count, then count * (24B header + payload)."""
    count = struct.unpack_from("<I", p, 0)[0]
    pos = 4
    out = []
    for _ in range(count):
        if p[pos:pos + 4] != b"LDLD":
            raise SystemExit(f"bad magic at {pos:#x}")
        typ, size = struct.unpack_from("<II", p, pos + 8)
        out.append((pos, count, typ, size))
        pos += 24 + size
    if pos != len(p):
        raise SystemExit(f"trailing {len(p) - pos} bytes")
    return out, count


def main(name, want_ids=None, dump_rows=0):
    src = GAME / name
    data = src.read_bytes()
    print(f"{name}: {len(data):,} sealed bytes, epk={data[:32].hex()[:16]}...")
    plain = unseal(data)
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_bytes(plain)
    print(f"  unsealed -> {len(plain):,} bytes, head={plain[:16].hex(' ')}")
    print(f"  magic = {plain[4:12]!r}")

    hs, count = headers(plain)
    print(f"  count={count}")
    for pos, _, typ, size in hs:
        print(f"   block@{pos:#08x} type={typ:#010x} payload={size:,} "
              f"stride={size // 272 if 272 and size % 272 == 0 else '-'}")

    if dump_rows:
        pos, _, typ, size = hs[0]
        body = pos + 24
        stride = dump_rows
        n = size // stride
        print(f"  first block: {n} rows x {stride} bytes")
        for i in range(min(n, 12)):
            row = plain[body + i * stride: body + i * stride + 64]
            print(f"    row{i:3d}: {row.hex(' ')}")

    return plain


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "generated_projectile_settings.dl_bin",
         dump_rows=int(sys.argv[2]) if len(sys.argv) > 2 else 272)
