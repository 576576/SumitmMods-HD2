"""Pull the ORIGINAL_PLAINTEXT / ORIGINAL_SEALED constants out of the ArmoryForge lua.

Both are Lua escape-sequence string literals. XORing the sealed body against the
plaintext recovers the keystream used by the sealed-box wrapper.
"""
import re
import sys
from pathlib import Path

LUA = Path(r"C:/Users/16695/AppData/Local/hd2arsenal/mods/"
           r"护甲多被动_静止隐匿-7.2.2-MDL3版-v1.7.1_AR142883/Addon/ba61629999bcbdb5.lua")

ESC = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11,
       "\\": 92, '"': 34, "'": 39, "\n": 10, "0": 0}


def decode(body: str) -> bytes:
    out = bytearray()
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\":
            out.append(ord(ch))
            i += 1
            continue
        i += 1
        if i >= n:
            break
        if body[i].isdigit():
            d = ""
            while i < n and len(d) < 3 and body[i].isdigit():
                d += body[i]
                i += 1
            out.append(int(d) & 0xFF)
            continue
        out.append(ESC.get(body[i], ord(body[i])))
        i += 1
    return bytes(out)


def grab(text: str, name: str):
    m = re.search(r'^local %s = "((?:[^"\\]|\\.)*)"' % re.escape(name), text, re.M)
    if not m:
        raise SystemExit("not found: " + name)
    return decode(m.group(1))


def main():
    text = LUA.read_text(encoding="utf-8", errors="replace")
    plain = grab(text, "ORIGINAL_PLAINTEXT")
    sealed = grab(text, "ORIGINAL_SEALED")
    print(f"ORIGINAL_PLAINTEXT {len(plain):,} bytes  head={plain[:16].hex(' ')}")
    print(f"ORIGINAL_SEALED    {len(sealed):,} bytes  head={sealed[:16].hex(' ')}")

    prefix = len(sealed) - len(plain)
    print(f"prefix (epk+mac) = {prefix}")
    if prefix <= 0:
        raise SystemExit("sealed shorter than plaintext")

    body = sealed[prefix:]
    ks = bytes(a ^ b for a, b in zip(body, plain))
    print(f"keystream head = {ks[:32].hex(' ')}")

    # Does the keystream repeat / look like a fixed table?
    print(f"keystream distinct bytes = {len(set(ks))}, len={len(ks):,}")

    out = Path(__file__).resolve().parent.parent / "stock"
    (out / "keystream.bin").write_bytes(ks)
    print("written:", out / "keystream.bin")

    # Verify against the live file: XOR keystream over its head and look for LDLD.
    target = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2/data/game/"
                  r"generated_projectile_settings.dl_bin").read_bytes()
    trial = bytes(a ^ b for a, b in zip(target[:4096], ks[:4096]))
    print("projectile_settings decrypted head:", trial[:32].hex(" "))
    print("  as text:", trial[:32])


if __name__ == "__main__":
    main()
