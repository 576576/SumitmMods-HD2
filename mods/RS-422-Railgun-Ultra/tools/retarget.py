"""Retarget the RS-422 projectile's two explosion slots and reseal the table.

RS-422 swaps `info+60` (direct-hit explosion) and `info+144` (main explosion) at
runtime. Offline the same two fields live at record+60 and record+144 of the
projectile settings table, so rewriting them retargets the round permanently.

Nothing is written to the game here; the sealed file lands in out/.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "ordnance"))

from nacl.bindings import crypto_box_seal, crypto_box_seal_open  # noqa: E402

import hd2edit  # noqa: E402

PK, SK = hd2edit.PK, hd2edit.SK
GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2/data/game")
NAME = "generated_projectile_settings.dl_bin"
OUTDIR = Path(__file__).resolve().parent.parent / "out"

RECORD_ID = 186          # RS-422's projectile, the id RS-422 asserts on
DIRECT_OFF = 60          # RS-422 rewrites info+60
MAIN_OFF = 144           # RS-422 rewrites info+144
SALT_OFF = 56            # unused 0.0f slot, absorbs the hash-correction nudge
STRIDE = 272
RECORDS = 44             # payload 16B preamble, then records

EFFECTS = {
    343: "Orbital Precision", 82: "Orbital Gas", 52: "Orbital Railcannon",
    188: "Orbital EMS", 273: "current RS-422 direct", 282: "current RS-422 main",
}


def record_base(plain, rid):
    count = struct.unpack_from("<I", plain, 36)[0]
    for i in range(count):
        base = RECORDS + i * STRIDE
        if struct.unpack_from("<I", plain, base)[0] == rid:
            return base
    raise SystemExit(f"record {rid} not found")


def u32p(p, o):
    return struct.unpack_from("<I", p, o)[0]


def build(effect_id, src=None, dry=False):
    sealed = (src or (GAME / NAME)).read_bytes()
    plain = bytearray(crypto_box_seal_open(sealed, PK, SK))
    target_hash = hd2edit.murmur64(bytes(plain))
    base = record_base(plain, RECORD_ID)

    print(f"target explosion {effect_id} ({EFFECTS.get(effect_id, '?')}) on record {RECORD_ID}")
    for off, label in ((DIRECT_OFF, "direct-hit"), (MAIN_OFF, "main")):
        old = u32p(plain, base + off)
        print(f"  {label:<10} +{off:<4} {old:<6} ({EFFECTS.get(old, '?')}) -> {effect_id}")
        struct.pack_into("<I", plain, base + off, effect_id)

    moved = hd2edit.murmur64(bytes(plain))
    print(f"  hash {target_hash:#018x} -> {moved:#018x}")
    salt_off = base + SALT_OFF
    salt = hd2edit.correct(plain, target_hash, [salt_off])
    after = hd2edit.murmur64(bytes(plain))
    print(f"  correction {salt}, hash now {after:#018x}, ok={after == target_hash}")
    print(f"  salt slot f32 {struct.unpack_from('<f', plain, salt_off)[0]:.9g}")

    resealed = crypto_box_seal(bytes(plain), PK)
    OUTDIR.mkdir(exist_ok=True)
    dst = OUTDIR / NAME
    dst.write_bytes(resealed)

    check = crypto_box_seal_open(dst.read_bytes(), PK, SK)
    print(f"  round trip: hash={hd2edit.murmur64(check) == target_hash} "
          f"direct={u32p(check, base + DIRECT_OFF)} main={u32p(check, base + MAIN_OFF)} "
          f"size={len(check) == len(plain)}")
    print(f"  written: {dst} ({len(resealed):,} bytes)")
    return dst


if __name__ == "__main__":
    build(int(sys.argv[1]) if len(sys.argv) > 1 else 52)
