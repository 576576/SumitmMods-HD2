"""HD2 资源容器工具库：IDString64、LZ4 block、DSAR、Stingray bundle / patch、资源名反查。

结构定义来自本机 Steam build 26251122 的 data 目录实测，并与 Filediver 的实现交叉核对。
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GAME_DIR = Path(os.environ.get(
    "HD2_GAME_DIR", "E:/SteamLibrary/steamapps/common/Helldivers 2"))
DEFAULT_NAMES_DIR = ROOT / "third_party" / "filediver" / "hashes"
OUT_DIR = ROOT / "out"

BUNDLE_MAGIC = 0xF0000011
DSAR_MAGIC = b"DSAR"
BUNDLE_HEADER = struct.Struct("<III20sQQ24s")
BUNDLE_TYPE = struct.Struct("<IIQIIII")
BUNDLE_FILE = struct.Struct("<7Q6I")
DSAR_HEADER = struct.Struct("<4s4sIIQ8s")
DSAR_CHUNK = struct.Struct("<QQIIBB6s")
HEADER_SIZE = BUNDLE_HEADER.size
TYPE_ENTRY_SIZE = BUNDLE_TYPE.size
FILE_ENTRY_SIZE = BUNDLE_FILE.size

TYPE_TEXTURE = "cd4238c6a0c69e32"
TYPE_MATERIAL = "eac0b497876adedf"
TYPE_BONES = "18dead01056b72e9"
TYPE_UNIT = "e0a48d0be9a7453f"

MASK64 = (1 << 64) - 1
MIX = 0xC6A4A7935BD1E995
SHIFT = 47


def idstring64(value: str | bytes) -> int:
    """Stingray IDString64：MurmurHash64A，长度参与 seed。"""
    data = value.encode("utf-8") if isinstance(value, str) else value
    h = (len(data) * MIX) & MASK64
    for i in range(len(data) // 8):
        k = int.from_bytes(data[i * 8:i * 8 + 8], "little")
        k = (k * MIX) & MASK64
        k ^= k >> SHIFT
        k = (k * MIX) & MASK64
        h ^= k
        h = (h * MIX) & MASK64
    tail = data[(len(data) // 8) * 8:]
    if tail:
        h ^= int.from_bytes(tail, "little")
        h = (h * MIX) & MASK64
    h ^= h >> SHIFT
    h = (h * MIX) & MASK64
    h ^= h >> SHIFT
    return h & MASK64


def thin_hash(value: int) -> int:
    return (value >> 32) & 0xFFFFFFFF


def hex64(value: int) -> str:
    return "%016x" % value


def lz4_block(src: bytes, expected: int) -> bytes:
    """解 LZ4 raw block。DSAR 的 0x03 块不带 frame 头，不能用 LZ4 frame 解码器。"""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out.extend(src[i:i + lit])
        i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(out):
            raise ValueError("invalid LZ4 offset %d at byte %d" % (offset, i))
        match = token & 0x0F
        if match == 15:
            while True:
                b = src[i]
                i += 1
                match += b
                if b != 255:
                    break
        match += 4
        start = len(out) - offset
        for k in range(match):
            out.append(out[start + k])
    if len(out) < expected:
        raise ValueError("LZ4 output %d bytes, expected %d" % (len(out), expected))
    return bytes(out[:expected])


@dataclass
class Chunk:
    out_offset: int
    file_offset: int
    usize: int
    csize: int
    compression: int
    resource_flags: int


class DSAR:
    """DirectStorage 风格的分块容器。0x20 起每 32 字节一条 chunk 表。"""

    def __init__(self, path: Path, data: bytes | None = None):
        self.path = Path(path)
        self.raw = data if data is not None else self.path.read_bytes()
        if self.raw[:4] != DSAR_MAGIC:
            raise ValueError("not a DSAR container: %s" % self.path)
        magic, unk, self.chunk_count, self.header_size, self.uncompressed_size, padding = (
            DSAR_HEADER.unpack_from(self.raw, 0))
        self.chunks = []
        for i in range(self.chunk_count):
            o = DSAR_HEADER.size + i * DSAR_CHUNK.size
            uo, fo, us, cs, comp, flags, _pad = DSAR_CHUNK.unpack_from(self.raw, o)
            self.chunks.append(Chunk(uo, fo, us, cs, comp, flags))
        if self.header_size != DSAR_HEADER.size + self.chunk_count * DSAR_CHUNK.size:
            raise ValueError("DSAR header size mismatch in %s" % self.path)

    def chunk_bytes(self, index: int) -> bytes:
        c = self.chunks[index]
        raw = self.raw[c.file_offset:c.file_offset + c.csize]
        if len(raw) != c.csize:
            raise ValueError("truncated chunk %d in %s" % (index, self.path))
        if c.compression == 0:
            return raw
        if c.compression == 3:
            return lz4_block(raw, c.usize)
        raise ValueError("unknown DSAR compression %d" % c.compression)

    def decompress(self) -> bytes:
        out = bytearray()
        for i in range(self.chunk_count):
            out.extend(self.chunk_bytes(i))
        if len(out) != self.uncompressed_size:
            raise ValueError("DSAR decompressed %d bytes, header says %d (%s)" % (
                len(out), self.uncompressed_size, self.path))
        return bytes(out)


@dataclass
class Resource:
    name: int
    type: int
    offset: int
    gpu_offset: int
    size: int
    gpu_size: int
    index: int
    raw_row: tuple


class Bundle:
    """0xF0000011 容器。游戏基包与 mod patch_0 使用同一个格式。"""

    def __init__(self, main: bytes, gpu: bytes = b"", stream: bytes = b"", path: Path | None = None):
        self.main = main
        self.gpu = gpu
        self.stream = stream
        self.path = path
        if len(main) < HEADER_SIZE or struct.unpack_from("<I", main, 0)[0] != BUNDLE_MAGIC:
            raise ValueError("not a Stingray bundle")
        self.num_types, self.num_files = struct.unpack_from("<II", main, 4)
        self.approx_main, self.approx_gpu = struct.unpack_from("<QQ", main, 0x20)
        self.types_base = HEADER_SIZE
        self.files_base = self.types_base + TYPE_ENTRY_SIZE * self.num_types
        self.data_base = self.files_base + FILE_ENTRY_SIZE * self.num_files
        self.type_entries = [
            bytearray(main[self.types_base + i * TYPE_ENTRY_SIZE:
                           self.types_base + (i + 1) * TYPE_ENTRY_SIZE])
            for i in range(self.num_types)
        ]
        self.resources = []
        for i in range(self.num_files):
            row = BUNDLE_FILE.unpack_from(main, self.files_base + i * FILE_ENTRY_SIZE)
            self.resources.append(Resource(row[0], row[1], row[2], row[4], row[7], row[9], row[12], row))
        self.gpu_slices = []
        data_end = 0
        for r in self.resources:
            self.gpu_slices.append(
                self.gpu[r.gpu_offset:r.gpu_offset + r.gpu_size] if r.gpu_size else b"")
            if r.gpu_size:
                data_end = max(data_end, r.gpu_offset + r.gpu_size)
        self.gpu_tail = self.gpu[data_end:]
        if any(len(s) != r.gpu_size for s, r in zip(self.gpu_slices, self.resources)):
            raise ValueError("gpu_resources shorter than the resource table requires")

    @classmethod
    def load(cls, path: Path) -> "Bundle":
        p = Path(path)
        gpu = p.with_name(p.name + ".gpu_resources")
        stream = p.with_name(p.name + ".stream")
        return cls(p.read_bytes(),
                   gpu.read_bytes() if gpu.is_file() else b"",
                   stream.read_bytes() if stream.is_file() else b"", p)

    def main_bytes(self, i: int) -> bytes:
        r = self.resources[i]
        return self.main[r.offset:r.offset + r.size]

    def ids(self) -> set[int]:
        return {r.name for r in self.resources}


def align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return value + (-value) % alignment


def rebuild(source: Bundle, indices: list[int], full_gpu: bool = False) -> tuple[bytes, bytes]:
    """按原表顺序取子集重建 bundle；full_gpu 保留源包完整 GPU 缓冲区与绝对引用。"""
    if indices != sorted(indices):
        raise ValueError("indices must be ascending in the original table order")
    selected = [source.resources[i] for i in indices]

    kept_types = []
    for te in source.type_entries:
        type_id = struct.unpack_from("<Q", te, 8)[0]
        count = sum(1 for r in selected if r.type == type_id)
        if count:
            out = bytearray(te)
            struct.pack_into("<I", out, 16, count)
            kept_types.append(bytes(out))

    header = bytearray(source.main[:HEADER_SIZE])
    struct.pack_into("<I", header, 4, len(kept_types))
    struct.pack_into("<I", header, 8, len(selected))

    data_base = (HEADER_SIZE + TYPE_ENTRY_SIZE * len(kept_types)
                 + FILE_ENTRY_SIZE * len(selected))
    main_body = bytearray()
    gpu = bytearray(source.gpu) if full_gpu else bytearray()
    table = bytearray()
    for k, (orig_i, r) in enumerate(zip(indices, selected)):
        row = list(r.raw_row)
        main_align = row[10] or 1
        gpu_align = row[11] or 1

        main_at = data_base + len(main_body)
        main_body.extend(b"\0" * (align_up(main_at, main_align) - main_at))
        row[2] = data_base + len(main_body)

        if not full_gpu:
            if row[9]:
                gpu.extend(b"\0" * (align_up(len(gpu), gpu_align) - len(gpu)))
                row[4] = len(gpu)
                gpu.extend(source.gpu_slices[orig_i])
            else:
                row[4] = 0
            row[5] = 0
            row[6] = 0
        row[3] = 0
        row[12] = k + 1

        table.extend(BUNDLE_FILE.pack(*row))
        main_body.extend(source.main_bytes(orig_i))

    if not full_gpu and indices and indices[-1] == source.num_files - 1:
        gpu.extend(source.gpu_tail)

    main = bytearray(header)
    main.extend(b"".join(kept_types))
    main.extend(table)
    main.extend(main_body)
    struct.pack_into("<QQ", main, 0x20, len(main), len(gpu))
    return bytes(main), bytes(gpu)


class NameDB:
    """资源名反查表。hash 相同的名字会全部保留。"""

    FILES = ("hashes.txt", "cracked.txt", "dl_type_names.txt",
             "thinhashes.txt", "wwise_streams.txt", "material_textures.txt")

    def __init__(self):
        self.by_hash: dict[int, list[str]] = {}
        self.names: set[str] = set()

    @classmethod
    def load(cls, directory: Path = DEFAULT_NAMES_DIR) -> "NameDB":
        db = cls()
        for name in cls.FILES:
            p = Path(directory) / name
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("//") or s.startswith("#"):
                    continue
                db.add(s)
        return db

    def add(self, name: str) -> None:
        if name in self.names:
            return
        self.names.add(name)
        self.by_hash.setdefault(idstring64(name), []).append(name)

    def resolve(self, value: int) -> list[str]:
        return self.by_hash.get(value, [])

    def __len__(self) -> int:
        return len(self.names)


def resource_type_name(type_id: int, db: NameDB | None = None) -> str:
    known = {
        int(TYPE_TEXTURE, 16): "texture",
        int(TYPE_MATERIAL, 16): "material",
        int(TYPE_BONES, 16): "bones",
        int(TYPE_UNIT, 16): "unit",
    }
    if type_id in known:
        return known[type_id]
    if db:
        names = db.resolve(type_id)
        if names:
            return names[0]
    return "unknown"


def write_json(path: Path, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
