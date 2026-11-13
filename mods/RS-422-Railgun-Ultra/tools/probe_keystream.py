"""Measure the baked keystream, verify it against the applied file, and test
whether the same stream can seal/unseal a different dl_bin."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_seal import decode  # noqa: E402

LUA = Path(r"C:/Users/16695/AppData/Local/hd2arsenal/mods/"
           r"护甲多被动_静止隐匿-7.2.2-MDL3版-v1.7.1_AR142883/Addon/ba61629999bcbdb5.lua")
GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2")

PAT = re.compile(r'(keystream|epk|poly)="((?:[^"\\]|\\.)*)"')


def field(name):
    text = LUA.read_text(encoding="utf-8", errors="replace")
    for m in PAT.finditer(text):
        if m.group(1) == name:
            return decode(m.group(2))
    raise SystemExit("missing " + name)


ks = field("keystream")
epk = field("epk")
poly = field("poly")
print(f"keystream = {len(ks):,} bytes")
print(f"epk       = {epk.hex()}")
print(f"poly      = {poly.hex()}")
print(f"ks[:32]==poly -> {ks[:32] == poly}")

applied = (GAME / "ArmoryForge-Multi.applied.dl_bin").read_bytes()
print(f"\napplied   = {len(applied):,} bytes, epk match -> {applied[:32] == epk}")

if applied[:32] == epk:
    body = applied[48:]
    plain = bytes(a ^ b for a, b in zip(body, ks[32:32 + len(body)]))
    print(f"unsealed head = {plain[:24].hex(' ')}")
    print(f"               {plain[:24]!r}")
    print(f"total plaintext = {len(plain):,}")

# which dl_bin files would fit in this keystream?
print("\nfiles that fit (plaintext + 32 <= keystream):")
for p in sorted((GAME / "data/game").glob("generated_*.dl_bin")):
    need = p.stat().st_size - 48 + 32
    ok = "FIT " if need <= len(ks) else "over"
    print(f"  {ok} {p.name:52s} {p.stat().st_size:>7,}  needs {need:>7,}")

target = GAME / "data/game/generated_projectile_settings.dl_bin"
t = target.read_bytes()
print(f"\nprojectile_settings size={len(t):,} keystream covers {len(ks) / max(1, len(t) - 48):.1%} of its plaintext")
