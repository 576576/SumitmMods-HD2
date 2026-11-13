"""Derive the per-file Poly1305 key for a sealed dl_bin and prove the 8-byte
ciphertext flip works.

PyNaCl exposes neither the xsalsa20 stream nor standalone Poly1305, so Salsa20 is
implemented here and cross-checked against crypto_secretbox output.
"""
import hashlib
import struct
import sys
from pathlib import Path

from nacl.bindings import crypto_box_beforenm, crypto_box_seal_open, crypto_secretbox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "ordnance"))
import hd2edit  # noqa: E402

PK, SK = hd2edit.PK, hd2edit.SK
SRC = Path(__file__).resolve().parent.parent / "stock" / "generated_projectile_settings.orig.dl_bin"
RECORD, STRIDE = 186, 272
FIELDS = (60, 144)
TARGET = 52


def record_base(plain, rid):
    count = struct.unpack_from("<I", plain, 36)[0]
    for i in range(count):
        base = 44 + i * STRIDE
        if struct.unpack_from("<I", plain, base)[0] == rid:
            return base
    raise SystemExit(f"record {rid} not found")

M32 = 0xFFFFFFFF


def rotl(v, c):
    return ((v << c) & M32) | (v >> (32 - c))


def salsa20_block(state):
    x = list(state)
    for _ in range(10):
        for a, b, c, d in ((0, 4, 8, 12), (5, 9, 13, 1), (10, 14, 2, 6), (15, 3, 7, 11),
                           (0, 1, 2, 3), (5, 6, 7, 4), (10, 11, 8, 9), (15, 12, 13, 14)):
            x[b] ^= rotl((x[a] + x[d]) & M32, 7)
            x[c] ^= rotl((x[b] + x[a]) & M32, 9)
            x[d] ^= rotl((x[c] + x[b]) & M32, 13)
            x[a] ^= rotl((x[d] + x[c]) & M32, 18)
    return [(x[i] + state[i]) & M32 for i in range(16)]


def hsalsa20(key, inp16):
    c = b"expand 32-byte k"
    k = struct.unpack("<8I", key)
    n = struct.unpack("<4I", inp16)
    st = [struct.unpack_from("<I", c, 0)[0], k[0], k[1], k[2], k[3],
          struct.unpack_from("<I", c, 4)[0], n[0], n[1], n[2], n[3],
          struct.unpack_from("<I", c, 8)[0], k[4], k[5], k[6], k[7],
          struct.unpack_from("<I", c, 12)[0]]
    x = list(st)
    for _ in range(10):
        for a, b, cc, d in ((0, 4, 8, 12), (5, 9, 13, 1), (10, 14, 2, 6), (15, 3, 7, 11),
                            (0, 1, 2, 3), (5, 6, 7, 4), (10, 11, 8, 9), (15, 12, 13, 14)):
            x[b] ^= rotl((x[a] + x[d]) & M32, 7)
            x[cc] ^= rotl((x[b] + x[a]) & M32, 9)
            x[d] ^= rotl((x[cc] + x[b]) & M32, 13)
            x[a] ^= rotl((x[d] + x[cc]) & M32, 18)
    return struct.pack("<8I", x[0], x[5], x[10], x[15], x[6], x[7], x[8], x[9])


def xsalsa20(key, nonce24, length):
    sub = hsalsa20(key, nonce24[:16])
    n8 = struct.unpack("<2I", nonce24[16:24])
    c = b"expand 32-byte k"
    k = struct.unpack("<8I", sub)
    out = bytearray()
    counter = 0
    while len(out) < length:
        st = [struct.unpack_from("<I", c, 0)[0], k[0], k[1], k[2], k[3],
              struct.unpack_from("<I", c, 4)[0], n8[0], n8[1], counter & M32, counter >> 32,
              struct.unpack_from("<I", c, 8)[0], k[4], k[5], k[6], k[7],
              struct.unpack_from("<I", c, 12)[0]]
        out += struct.pack("<16I", *salsa20_block(st))
        counter += 1
    return bytes(out[:length])


def poly1305(msg, key):
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    p = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        acc = ((acc + int.from_bytes(block + b"\x01", "little")) * r) % p
    return ((acc + s) % (1 << 128)).to_bytes(16, "little")


def main():
    data = SRC.read_bytes()
    epk, tag, cipher = data[:32], data[32:48], data[48:]
    k = crypto_box_beforenm(epk, SK)
    nonce = hashlib.blake2b(epk + PK, digest_size=24).digest()

    stream = xsalsa20(k, nonce, 64 + 64)
    # cross-check: secretbox of zeros exposes stream[32:]
    probe = crypto_secretbox(b"\x00" * 64, nonce, k)
    print(f"salsa20 stream matches secretbox: {stream[32:96] == probe[16:]}")
    polykey = stream[:32]
    print(f"poly key = {polykey.hex()}")
    print(f"poly1305(cipher) == file tag: {poly1305(cipher, polykey) == tag}")

    plain = crypto_box_seal_open(data, PK, SK)
    base = record_base(plain, RECORD)
    print(f"record {RECORD} at plain offset {base} (row {(base - 44) // STRIDE})")
    new_cipher = bytearray(cipher)
    for off in FIELDS:
        pos = base + off
        old = struct.unpack_from("<I", plain, pos)[0]
        newb = struct.pack("<I", TARGET)
        for i in range(4):
            new_cipher[pos + i] ^= plain[pos + i] ^ newb[i]
        print(f"  record+{off}: {old} -> {TARGET} (cipher byte {pos})")

    new_tag = poly1305(bytes(new_cipher), polykey)
    rebuilt = epk + new_tag + bytes(new_cipher)
    check = crypto_box_seal_open(rebuilt, PK, SK)
    got = tuple(struct.unpack_from("<I", check, base + o)[0] for o in FIELDS)
    print(f"\nrebuilt opens: fields={got}, hash preserved="
          f"{hd2edit.murmur64(check) == hd2edit.murmur64(plain)}")
    print(f"POLYKEY={polykey.hex()}")


if __name__ == "__main__":
    main()
