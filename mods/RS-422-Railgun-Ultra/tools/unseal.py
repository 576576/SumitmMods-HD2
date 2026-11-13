"""Unseal the dl_bin files that fit inside the baked keystream, then walk the LDLD
structure so we can see which tables hold projectile / effect references."""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_seal import decode  # noqa: E402

GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2/data/game")
LUA = Path(r"C:/Users/16695/AppData/Local/hd2arsenal/mods/"
           r"护甲多被动_静止隐匿-7.2.2-MDL3版-v1.7.1_AR142883/Addon/ba61629999bcbdb5.lua")
OUT = Path(__file__).resolve().parent.parent / "unsealed"


def field(name):
    text = LUA.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'(keystream|epk|poly)="((?:[^"\\]|\\.)*)"', text):
        if m.group(1) == name:
            return decode(m.group(2))
    raise SystemExit("missing " + name)


KS = field("keystream")


def unseal(path: Path):
    d = path.read_bytes()
    body = d[48:]
    need = len(body) + 32
    if need > len(KS):
        return None
    return bytes(a ^ b for a, b in zip(body, KS[32:32 + len(body)]))


def walk_header(p: bytes):
    """LDLD container: u32 count, then count * (24 byte header + payload)."""
    count = struct.unpack_from("<I", p, 0)[0]
    pos = 4
    blocks = []
    for _ in range(count):
        if p[pos:pos + 4] != b"LDLD":
            return count, blocks, f"bad magic at {pos:#x}"
        size = struct.unpack_from("<I", p, pos + 12)[0]
        prio, flags = struct.unpack_from("<II", p, pos + 4)[0], struct.unpack_from("<H", p, pos + 16)[0]
        kind = struct.unpack_from("<I", p, pos + 8)[0]
        blocks.append({"pos": pos, "size": size, "kind": kind, "byte17": p[pos + 16],
                       "byte18": p[pos + 17], "byte19": p[pos + 18], "byte20": p[pos + 19]})
        pos += 24 + size
    return count, blocks, ("ok" if pos == len(p) else f"trailing {len(p) - pos}")


def main(names):
    OUT.mkdir(exist_ok=True)
    for name in names:
        p = GAME / name
        plain = unseal(p)
        if plain is None:
            print(f"{name}: too big for keystream")
            continue
        (OUT / name).write_bytes(plain)
        count, blocks, status = walk_header(plain)
        sizes = [b["size"] for b in blocks]
        print(f"{name}")
        print(f"  unsealed {len(plain):,} bytes, file={p.stat().st_size:,}, status={status}")
        print(f"  blocks={count} payload sizes: min={min(sizes) if sizes else 0} "
              f"max={max(sizes) if sizes else 0} total={sum(sizes):,}")
        if blocks:
            b = blocks[0]
            head = plain[b["pos"] + 24:b["pos"] + 24 + 48]
            print(f"  first block head: {head.hex(' ')}")
            print(f"  byte17..20 = {b['byte17']} {b['byte18']} {b['byte19']} {b['byte20']}")
            from collections import Counter
            print(f"  distinct sizes: {Counter(sizes).most_common(6)}")


if __name__ == "__main__":
    main(sys.argv[1:] or [
        "generated_damage_settings.dl_bin",
        "generated_unit_effect_settings.dl_bin",
        "generated_weapon_customization_settings.dl_bin",
        "generated_status_effect_settings.dl_bin",
    ])
