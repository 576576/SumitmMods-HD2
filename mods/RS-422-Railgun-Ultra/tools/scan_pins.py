"""Locate RS-422's hardcoded RVA byte signatures inside current game modules.

Reads the pins out of the installed 1.0.3 lua, then searches each signature in
runtime.dll / helldivers2.exe and reports file offset + mapped RVA.
"""
import re
import struct
import sys
from pathlib import Path

GAME = Path(r"E:/SteamLibrary/steamapps/common/Helldivers 2")
LUA = Path(
    r"C:/Users/16695/AppData/Local/hd2arsenal/mods/RS-422Pro-Max-v1.0.3_AR926657"
    r"/Addon/7840a79220c20a47.lua"
)

ESC = {
    "a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11,
    "\\": 92, '"': 34, "'": 39, "\n": 10,
}


def decode_lua_string(body: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            i += 1
            nxt = body[i]
            if nxt.isdigit():
                d = ""
                while i < len(body) and len(d) < 3 and body[i].isdigit():
                    d += body[i]
                    i += 1
                out.append(int(d) & 0xFF)
                continue
            out.append(ESC.get(nxt, ord(nxt)))
            i += 1
        else:
            out.append(ord(ch))
            i += 1
    return bytes(out)


def parse_pins(text: str):
    pins = []
    for m in re.finditer(r'\{rva=(0x[0-9a-fA-F]+|\d+),bytes="((?:[^"\\]|\\.)*)"\}', text):
        pins.append((int(m.group(1), 0), decode_lua_string(m.group(2))))
    return pins


def sections(path: Path):
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    base = pe + 24 + optsz
    out = []
    for i in range(nsec):
        off = base + i * 40
        name = data[off:off + 8].rstrip(b"\0").decode("latin1")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, off + 8)
        out.append((name, vaddr, vsize, rawptr, rawsize))
    return data, out


def off_to_rva(secs, off):
    for name, vaddr, vsize, rawptr, rawsize in secs:
        if rawptr <= off < rawptr + rawsize:
            return vaddr + (off - rawptr), name
    return None, None


def search(data: bytes, needle: bytes):
    hits = []
    start = 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            return hits
        hits.append(i)
        start = i + 1


def main():
    text = LUA.read_text(encoding="utf-8", errors="replace")
    pins = parse_pins(text)
    print(f"parsed {len(pins)} pins from {LUA.name}\n")

    targets = {}
    for rel in ("data/game/game.dll", "bin/runtime.dll", "bin/helldivers2.exe"):
        p = GAME / rel
        if p.exists():
            name = Path(rel).name
            data, secs = sections(p)
            targets[name] = (data, secs)
            print(f"{name}: {len(data):,} bytes, sections: " +
                  ", ".join(f"{s[0]}@{s[1]:#x}+{s[2]:#x}" for s in secs))
    print()

    for idx, (rva, needle) in enumerate(pins, 1):
        best = []
        for name, (data, secs) in targets.items():
            for off in search(data, needle):
                new_rva, sec = off_to_rva(secs, off)
                best.append((name, sec, off, new_rva))
        status = "UNIQUE" if len(best) == 1 else (f"{len(best)} hits" if best else "NOT FOUND")
        print(f"pin#{idx:02d} old_rva={rva:#x} len={len(needle)} -> {status}")
        for name, sec, off, new_rva in best[:6]:
            print(f"    {name} [{sec}] file_off={off:#x} rva={new_rva if new_rva is None else hex(new_rva)}")
        if best and len(best) == 1:
            print(f"    DELTA = {best[0][3] - rva:+#x}" if best[0][3] is not None else "")
        print()


if __name__ == "__main__":
    main()
