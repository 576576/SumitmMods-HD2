#!/usr/bin/env python3
"""按依赖链重建每个地雷选项的独立 patch_0 三件套。

用法:
  python split_bundle.py inspect
  python split_bundle.py build
  python split_bundle.py verify
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATCH = ROOT / "tools" / "source" / "9ba626afa44a3aa3.patch_2"
SOURCE_GPU = ROOT / "tools" / "source" / "9ba626afa44a3aa3.patch_2.gpu_resources"
GROUPS_FILE = ROOT / "tools" / "groups.json"
OPTIONS_DIR = ROOT / "options"
BUNDLE = "9ba626afa44a3aa3"

TYPE_UNIT = 0xE0A48D0BE9A7453F

SOURCE_SHA = {
    "9ba626afa44a3aa3.patch_2":
        "62543F01EDACCFF1B51BAC15A4753A2DA9A20B99312B9FF4F813DA9BDE4F325E",
    "9ba626afa44a3aa3.patch_2.gpu_resources":
        "D34632FC77D97D7B65F0EEB715000CD350D5BC3AAADB7040417D46092CD196EB",
}

MAGIC = bytes.fromhex("110000f0")
HEADER_SIZE = 72
TYPE_ENTRY_SIZE = 32
RES_ENTRY_SIZE = 80
REC = struct.Struct("<7Q6I")


def check_source(patch: bytes, gpu: bytes):
    """构建源必须与原始 patch 逐字节一致；GPU 对齐间隙不能被重建。"""
    for data, (name, want) in zip((patch, gpu), SOURCE_SHA.items()):
        got = hashlib.sha256(data).hexdigest().upper()
        if got != want:
            raise ValueError("build source differs from the original %s: %s" % (name, got))


class Source:
    def __init__(self, patch_path=SOURCE_PATCH, gpu_path=SOURCE_GPU):
        self.patch = Path(patch_path).read_bytes()
        self.gpu = Path(gpu_path).read_bytes()
        check_source(self.patch, self.gpu)
        if self.patch[:4] != MAGIC:
            raise ValueError("not a Stingray bundle patch")
        self.ntypes, self.nres = struct.unpack_from("<II", self.patch, 4)
        self.types_base = HEADER_SIZE
        self.res_base = self.types_base + TYPE_ENTRY_SIZE * self.ntypes
        self.data_base = self.res_base + RES_ENTRY_SIZE * self.nres
        self.header = self.patch[:HEADER_SIZE]
        self.type_entries = [
            bytearray(self.patch[self.types_base + i * TYPE_ENTRY_SIZE:
                                 self.types_base + (i + 1) * TYPE_ENTRY_SIZE])
            for i in range(self.ntypes)
        ]
        self.rows = [
            list(REC.unpack_from(self.patch, self.res_base + i * RES_ENTRY_SIZE))
            for i in range(self.nres)
        ]
        for i, r in enumerate(self.rows):
            if r[2] + r[7] > len(self.patch):
                raise ValueError("resource %d points outside the patch" % i)
            if r[9] and r[4] + r[9] > len(self.gpu):
                raise ValueError("resource %d gpu offset outside the gpu file" % i)
        self.gpu_slices = [
            self.gpu[r[4]:r[4] + r[9]] if r[9] else b"" for r in self.rows
        ]

    def payload(self, i):
        r = self.rows[i]
        return self.patch[r[2]:r[2] + r[7]]

    def sha(self, i):
        return hashlib.sha256(self.payload(i)).hexdigest(), hashlib.sha256(self.gpu_slices[i]).hexdigest()


def load_config():
    data = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    return data["shared_indices"], data["groups"], [
        o["key"] for o in data.get("standalone_options", [])
    ]


def option_indices(shared, group):
    return sorted(shared + [group["unit_index"]])


def align_up(value, alignment):
    if alignment <= 1:
        return value
    return value + (-value) % alignment


def build_subset(src, indices):
    """资源表裁剪为 indices，GPU 文件保留源包完整副本以维持绝对引用。"""
    indices = list(indices)
    if indices != sorted(indices):
        raise ValueError("indices must follow the original table order")
    sel = [src.rows[i] for i in indices]

    kept_types = []
    for te in src.type_entries:
        type_id = struct.unpack_from("<Q", te, 8)[0]
        count = sum(1 for r in sel if r[1] == type_id)
        if count:
            out = bytearray(te)
            struct.pack_into("<I", out, 16, count)
            kept_types.append(bytes(out))

    header = bytearray(src.header)
    struct.pack_into("<I", header, 4, len(kept_types))
    struct.pack_into("<I", header, 8, len(sel))

    data_base = HEADER_SIZE + TYPE_ENTRY_SIZE * len(kept_types) + RES_ENTRY_SIZE * len(sel)
    main = bytearray()
    table = bytearray()
    for k, (orig_i, r) in enumerate(zip(indices, sel)):
        nr = list(r)
        main_align = nr[10] or 1
        at = data_base + len(main)
        main.extend(b"\0" * (align_up(at, main_align) - at))
        nr[2] = data_base + len(main)
        nr[3] = 0
        nr[12] = k + 1
        table.extend(REC.pack(*nr))
        main.extend(src.payload(orig_i))

    body = bytearray(header)
    body.extend(b"".join(kept_types))
    body.extend(table)
    body.extend(main)
    struct.pack_into("<QQ", body, 0x20, len(body), len(src.gpu))
    return bytes(body), bytes(src.gpu)


def parse_patch(data):
    if data[:4] != MAGIC:
        raise ValueError("not a Stingray bundle patch")
    nt, nr = struct.unpack_from("<II", data, 4)
    res_base = HEADER_SIZE + TYPE_ENTRY_SIZE * nt
    return [list(REC.unpack_from(data, res_base + i * RES_ENTRY_SIZE)) for i in range(nr)]


def cmd_inspect(_args):
    src = Source()
    shared, groups, standalone = load_config()
    print("patch          :", SOURCE_PATCH)
    print("size           :", len(src.patch), "gpu:", len(src.gpu))
    print("types/resources:", src.ntypes, src.nres)
    for g in groups:
        ids = option_indices(shared, g)
        print("option %-12s %2d records  unit=%s  unit_r5/r6=%s" % (
            g["key"], len(ids), g["unit"],
            (src.rows[g["unit_index"]][5], src.rows[g["unit_index"]][6])))
    return 0


def cmd_build(_args):
    src = Source()
    shared, groups, standalone = load_config()
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    keep = {g["key"] for g in groups} | set(standalone)
    for d in sorted(OPTIONS_DIR.iterdir()):
        if d.is_dir() and d.name not in keep:
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()
            if any(d.iterdir()):
                raise SystemExit("stale option dir contains subdirectories: %s" % d)
            d.rmdir()
            print("pruned stale option dir:", d.name)
    for g in groups:
        ids = option_indices(shared, g)
        out = OPTIONS_DIR / g["key"]
        out.mkdir(parents=True, exist_ok=True)
        patch, gpu = build_subset(src, ids)
        stem = BUNDLE + ".patch_0"
        (out / stem).write_bytes(patch)
        (out / (stem + ".gpu_resources")).write_bytes(gpu)
        (out / (stem + ".stream")).write_bytes(b"")
        print("%-12s patch=%8d gpu=%9d records=%2d" % (
            g["key"], len(patch), len(gpu), len(ids)))
    return 0


def cmd_verify(_args):
    src = Source()
    shared, groups, standalone = load_config()
    problems = []

    if sorted(shared + [g["unit_index"] for g in groups]) != list(range(src.nres)):
        problems.append("shared + unit indices do not cover every resource exactly once")

    for g in groups:
        ids = option_indices(shared, g)
        out = OPTIONS_DIR / g["key"]
        stem = BUNDLE + ".patch_0"
        patch = out / stem
        gpu = out / (stem + ".gpu_resources")
        stream = out / (stem + ".stream")
        for p in (patch, gpu, stream):
            if not p.is_file():
                problems.append("missing %s" % p)
        if problems:
            continue
        pdata = patch.read_bytes()
        gdata = gpu.read_bytes()
        approx_main, approx_gpu = struct.unpack_from("<QQ", pdata, 0x20)
        if approx_main != len(pdata):
            problems.append("%s: ApproxMainSize %d != %d" % (g["key"], approx_main, len(pdata)))
        if approx_gpu != len(gdata):
            problems.append("%s: ApproxGPUSize %d != %d" % (g["key"], approx_gpu, len(gdata)))
        if gdata != src.gpu:
            problems.append("%s: gpu file differs from the full source buffer" % g["key"])
        rows = parse_patch(pdata)
        if len(rows) != len(ids):
            problems.append("%s: record count %d != %d" % (g["key"], len(rows), len(ids)))
            continue
        for k, (i, r) in enumerate(zip(ids, rows)):
            want = src.rows[i]
            if r[0] != want[0] or r[1] != want[1]:
                problems.append("%s: resource identity changed at %d" % (g["key"], i))
            if r[2] + r[7] > len(pdata) or r[2] % (r[10] or 1):
                problems.append("%s: invalid main offset at %d" % (g["key"], i))
            if r[9] and (r[4] + r[9] > len(gdata) or r[4] % (r[11] or 1)):
                problems.append("%s: invalid gpu offset at %d" % (g["key"], i))
            if r[4] != want[4] or r[5] != want[5] or r[6] != want[6] or r[3] != 0:
                problems.append("%s: buffer offsets changed at %d" % (g["key"], i))
            if r[12] != k + 1:
                problems.append("%s: bad record index at %d" % (g["key"], i))
            payload = pdata[r[2]:r[2] + r[7]]
            source_payload = src.payload(i)
            if payload != source_payload:
                delta = sum(1 for x, y in zip(payload, source_payload) if x != y)
                if r[1] != TYPE_UNIT or delta > 64 or len(payload) != len(source_payload):
                    problems.append("%s: main payload differs at %d" % (g["key"], i))
            gslice = gdata[r[4]:r[4] + r[9]] if r[9] else b""
            if hashlib.sha256(gslice).hexdigest() != src.sha(i)[1]:
                problems.append("%s: gpu slice differs at %d" % (g["key"], i))

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    for opt in manifest["Options"]:
        for inc in opt["Include"]:
            d = ROOT / inc
            if not d.is_dir():
                problems.append("manifest Include missing: %s" % inc)
            elif not (d / (BUNDLE + ".patch_0")).is_file():
                problems.append("option folder has no patch_0: %s" % inc)

    units = [g["unit_index"] for g in groups]
    if len(units) != len(set(units)):
        problems.append("duplicate unit indices across options")

    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: %d options, full gpu buffer preserved, %d source resources, hashes and offsets valid" % (
        len(groups), src.nres))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inspect").set_defaults(func=cmd_inspect)
    sub.add_parser("build").set_defaults(func=cmd_build)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
