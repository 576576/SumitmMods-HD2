#!/usr/bin/env python3
"""从 src/ 的 lua 重建归档 patch_0。

上游包只含一个 lua 资源，头部 184 字节就是全部元数据。这里不重算头部，
而是以上游原文件为模板、只改三处：资源 id（模组名的 MurmurHash64A）、
载荷长度、文件总长。这样头部布局永远和一份进过游戏的文件一致。

用法：
    python tools/build_patch.py            # 重建
    python tools/build_patch.py --selftest # 用原载荷反推，验证模板搬运无误
"""

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "armor_multi_passive.lua"
SOURCE = ROOT / "tools" / "source" / "9ba626afa44a3aa3.orig.patch_0"
CORE = ROOT / "core"
VERSION_FILE = ROOT / "version.txt"
VERSION_LINE = re.compile(br'(?m)^local MOD_VERSION = "[^"]*"$')

MAGIC = 0xF0000011
PAYLOAD_AT = 192          # 184 字节头 + 行表，向上对齐到 16
TOTAL_AT = 32             # u64，文件总长
ROW_AT = 104              # 唯一的资源行
ROW_TAG = 0
ROW_SIZE = 56             # u32，载荷长度 + 8（长度/类型前缀）
PREFIX = 8                # u32 长度 + u32 类型（2 = lua 文本）
# 包内文件名沿用上游与 Sentry 的核心归档名；部署时 Arsenal 会重新编号成 patch_N，
# 资源身份由行标签（模组名的哈希）决定，与文件名无关。
PATCH_NAME = "9ba626afa44a3aa3.patch_0"

M64 = 0xC6A4A7935BD1E995
MASK = (1 << 64) - 1


def idstring64(text):
    """资源 id：MurmurHash64A(名字, seed=0)，小端 8 字节。"""
    data = text.encode("utf-8")
    length = len(data)
    h = (length * M64) & MASK
    whole = length - length % 8
    for i in range(0, whole, 8):
        v = int.from_bytes(data[i:i + 8], "little")
        v = (v * M64) & MASK
        v ^= v >> 47
        v = (v * M64) & MASK
        h ^= v
        h = (h * M64) & MASK
    if whole < length:
        h ^= int.from_bytes(data[whole:], "little")
        h = (h * M64) & MASK
    h ^= h >> 47
    h = (h * M64) & MASK
    h ^= h >> 47
    return h


def stamped(source: bytes) -> bytes:
    """lua 里的 MOD_VERSION 按 version.txt 覆盖，版本号只有这一个来源。"""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("version.txt 是空的")
    if len(VERSION_LINE.findall(source)) != 1:
        raise SystemExit("lua 里 MOD_VERSION 声明不是刚好一处")
    return VERSION_LINE.sub(b'local MOD_VERSION = "%s"' % version.encode("ascii"), source)


def module_name(source):
    """资源名就是 lua 第一行的 HD2-Addon 声明。"""
    first = source.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    prefix = "-- HD2-Addon: "
    if not first.startswith(prefix):
        raise SystemExit("lua 第一行不是 '-- HD2-Addon: <名字>'：" + first[:80])
    name = first[len(prefix):].strip()
    if not name.isascii() or "/" not in name:
        raise SystemExit("资源名必须是 ASCII 且带命名空间：%r" % name)
    return name


def payload_of(buf):
    length, kind = struct.unpack_from("<II", buf, PAYLOAD_AT)
    if kind != 2:
        raise SystemExit("载荷前缀不是 lua 文本（kind=%d）" % kind)
    return buf[PAYLOAD_AT + PREFIX:PAYLOAD_AT + PREFIX + length]


def template():
    buf = SOURCE.read_bytes()
    magic, types, rows = struct.unpack_from("<III", buf, 0)
    if magic != MAGIC:
        raise SystemExit("模板归档头不对：%08x" % magic)
    if (types, rows) != (1, 1):
        raise SystemExit("模板应只有一个类型、一条资源，实际 %d/%d" % (types, rows))
    if struct.unpack_from("<Q", buf, TOTAL_AT)[0] != len(buf):
        raise SystemExit("模板总长字段与实际不符")
    return buf


def build(buf, source):
    """返回新归档字节；除 id / 两处长度外与模板逐字节一致。"""
    module = module_name(source)
    out = bytearray(buf[:PAYLOAD_AT])
    struct.pack_into("<Q", out, TOTAL_AT, PAYLOAD_AT + PREFIX + len(source))
    out[ROW_AT + ROW_TAG:ROW_AT + ROW_TAG + 8] = idstring64(module).to_bytes(8, "little")
    struct.pack_into("<I", out, ROW_AT + ROW_SIZE, PREFIX + len(source))
    out += struct.pack("<II", len(source), 2) + source
    return bytes(out), module


def selftest(buf):
    """拿模板自带的载荷与 id 反推一遍，结果必须与模板完全相同。"""
    payload = payload_of(buf)
    rebuilt, module = build(buf, payload)
    if rebuilt != buf:
        diff = [i for i in range(min(len(rebuilt), len(buf))) if rebuilt[i] != buf[i]]
        raise SystemExit("自检失败：重建结果与模板有 %d 处不同，首个在 %d" % (len(diff), diff[0]))
    print("selftest: 用 %s 反推，与原文件逐字节相同" % module)


def main():
    buf = template()
    if "--selftest" in sys.argv:
        selftest(buf)
        return
    source = stamped(SRC.read_bytes())
    if b"\r" in source:
        raise SystemExit("src 里的 lua 含 CR，先统一成 LF")
    data, module = build(buf, source)
    name = idstring64(module).to_bytes(8, "little").hex()
    CORE.mkdir(exist_ok=True)
    for stale in CORE.glob("*.patch_*"):
        if stale.name.split(".")[0] != PATCH_NAME.split(".")[0]:
            stale.unlink()
            print("removed stale", stale.name)
    target = CORE / PATCH_NAME
    target.write_bytes(data)
    for suffix in (".gpu_resources", ".stream"):
        (CORE / (PATCH_NAME + suffix)).write_bytes(b"")
    print("module :", module)
    print("id     :", name)
    print("lua    :", len(source), "bytes ->", "core/" + PATCH_NAME, "(%d bytes)" % len(data))


if __name__ == "__main__":
    main()
