"""Precompute the sealed bytes each RS-422 variant needs, plus the state signatures
the in-game Lua uses to recognise what is currently on disk.

Output (`build/lua_patches.json`):
  - `union`:  ciphertext offsets where any known state differs from stock
  - `states`: signature of every known state (stock + each variant) at those offsets
  - `variants`: per-variant target signature over the same offsets

Because the tag is baked too, the Lua only writes bytes — no Salsa20, no Poly1305,
no hash maths at runtime. And since every variant's signature is listed as a known
state, switching from one option to another works in place instead of being refused.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

from nacl.bindings import crypto_box_beforenm, crypto_box_seal_open

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "ordnance"))

import hd2edit  # noqa: E402
from verify_flip import poly1305, xsalsa20  # noqa: E402

PK, SK = hd2edit.PK, hd2edit.SK
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "stock" / "generated_projectile_settings.orig.dl_bin"
RECORD, STRIDE = 186, 272
DIRECT_OFF, MAIN_OFF, SALT_OFF = 60, 144, 56
VARIANTS = [(343, "轨道精准"), (82, "轨道毒气"), (52, "轨道炮"), (188, "轨道电磁")]


def record_base(plain, rid):
    count = struct.unpack_from("<I", plain, 36)[0]
    for i in range(count):
        base = 44 + i * STRIDE
        if struct.unpack_from("<I", plain, base)[0] == rid:
            return base
    raise SystemExit(f"record {rid} not found")


def polykey_for(epk):
    k = crypto_box_beforenm(epk, SK)
    nonce = hashlib.blake2b(epk + PK, digest_size=24).digest()
    return xsalsa20(k, nonce, 32)


def retarget(sealed, plain, polykey, effect_id, target_hash):
    """Rewrite the two explosion slots in the ciphertext and re-do the tag."""
    base = record_base(plain, RECORD)
    new_plain = bytearray(plain)
    struct.pack_into("<I", new_plain, base + DIRECT_OFF, effect_id)
    struct.pack_into("<I", new_plain, base + MAIN_OFF, effect_id)
    hd2edit.correct(new_plain, target_hash, [base + SALT_OFF])
    if hd2edit.murmur64(bytes(new_plain)) != target_hash:
        raise SystemExit(f"{effect_id}: hash repair failed")

    epk, cipher = sealed[:32], sealed[48:]
    out = bytearray(cipher)
    for i, (old, new) in enumerate(zip(plain, new_plain)):
        if old != new:
            out[i] ^= old ^ new
    cipher = bytes(out)
    rebuilt = epk + poly1305(cipher, polykey) + cipher

    check = crypto_box_seal_open(rebuilt, PK, SK)
    got = tuple(struct.unpack_from("<I", check, base + o)[0] for o in (DIRECT_OFF, MAIN_OFF))
    if got != (effect_id, effect_id) or hd2edit.murmur64(check) != target_hash:
        raise SystemExit(f"{effect_id}: rebuilt seal does not verify")
    return rebuilt


def signature(sealed, offsets):
    return "".join("%02x" % sealed[o] for o in offsets)


def main():
    sealed = SRC.read_bytes()
    epk = sealed[:32]
    polykey = polykey_for(epk)
    if poly1305(sealed[48:], polykey) != sealed[32:48]:
        raise SystemExit("poly key does not reproduce the stock tag")

    plain = crypto_box_seal_open(sealed, PK, SK)
    target_hash = hd2edit.murmur64(plain)

    built = {"stock": sealed}
    for effect_id, label in VARIANTS:
        built[label] = retarget(sealed, plain, polykey, effect_id, target_hash)
        print(f"{label:<14} effect={effect_id:<4} sealed bytes differ from stock="
              f"{sum(a != b for a, b in zip(built[label], sealed))}")

    union = sorted({i for state in built.values()
                    for i, (a, b) in enumerate(zip(state, sealed)) if a != b})
    states = {name: signature(state, union) for name, state in built.items()}
    out = {
        "union": union,
        "states": states,
        "variants": {label: {"effect": effect_id, "sig": states[label]}
                     for effect_id, label in VARIANTS},
    }
    for label, info in out["variants"].items():
        print(f"{label:<14} target sig={info['sig']}")

    dst = ROOT / "build" / "lua_patches.json"
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"union offsets: {len(union)}  -> {dst}")


if __name__ == "__main__":
    main()
