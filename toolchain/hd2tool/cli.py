"""hd2tool 命令行入口。用法：python -m hd2tool.cli <子命令> --help"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

from .core import (Bundle, DSAR, NameDB, DEFAULT_GAME_DIR, DEFAULT_NAMES_DIR,
                   OUT_DIR, ROOT, hex64, idstring64, rebuild, resource_type_name,
                   write_json)

DEFAULT_OLD_PATCH = Path(
    "C:/Users/16695/Documents/Workbuddy Projects/hd2mods/Mines-Highlight/tools/source/9ba626afa44a3aa3.patch_2")
MINE_LIST = OUT_DIR / "filediver_mine_list.txt"

HASH_VECTORS = (
    ("mods/rexsybimatw/apw1_pene_v3", "d3631a1b5a122db3"),
    ("mods/etxp/gl52_self_exclude", "1dec977ef3d38e7c"),
)


def game_data_dir(args) -> Path:
    return Path(args.game_dir) / "data"


def iter_index_dsar(data_dir: Path):
    for p in sorted(data_dir.iterdir()):
        if p.is_file() and p.suffix == "":
            yield p


def iter_nxa(data_dir: Path):
    for p in sorted(data_dir.glob("*.nxa")):
        yield p


def cmd_selftest(args):
    ok = True
    for path, expected in HASH_VECTORS:
        got = hex64(idstring64(path))
        want = "%016x" % int.from_bytes(bytes.fromhex(expected), "little")
        ok &= got == want
        print("  %s idstring64 %-22s %s" % ("ok" if got == want else "FAIL", path, got))

    names = NameDB.load(DEFAULT_NAMES_DIR)
    print("  name db: %d names" % len(names))
    for s in ("content/fac_helldivers/equipment/throwables/contact_mine/contact_mine",
              "content/fac_helldivers/equipment/throwables/tank_mine/tank_mine"):
        hit = names.resolve(idstring64(s))
        ok &= s in hit
        print("  %s resolve %s" % ("ok" if s in hit else "FAIL", s))

    data = game_data_dir(args)
    small = data / "0cf14e223de06a26"
    if small.is_file():
        d = DSAR(small)
        out = d.decompress()
        ok &= len(out) == d.uncompressed_size
        print("  %s DSAR small: %d chunks -> %d bytes" % (
            "ok" if len(out) == d.uncompressed_size else "FAIL", d.chunk_count, len(out)))
    big = data / "02582f3da1f8daf5"
    if big.is_file():
        d = DSAR(big)
        out = d.decompress()
        b = Bundle(out)
        resolved = sum(1 for r in b.resources if names.resolve(r.name))
        ok &= out[:4] == b"\x11\x00\x00\xf0" and resolved > 0
        print("  %s DSAR big: %d chunks -> %d bytes, %d resources, %d named" % (
            "ok" if resolved > 0 else "FAIL", d.chunk_count, len(out), b.num_files, resolved))

    if DEFAULT_OLD_PATCH.is_file():
        p = Bundle.load(DEFAULT_OLD_PATCH)
        main, gpu = rebuild(p, list(range(p.num_files)))
        rebuilt = Bundle(main, gpu)
        same = (len(rebuilt.resources) == p.num_files and all(
            rebuilt.main_bytes(i) == p.main_bytes(i) and
            rebuilt.gpu_slices[i] == p.gpu_slices[i]
            for i in range(p.num_files)))
        ok &= same
        print("  %s rebuild round-trip: %d resources" % ("ok" if same else "FAIL", p.num_files))

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_index(args):
    data = game_data_dir(args)
    if not data.is_dir():
        raise SystemExit("game data dir not found: %s" % data)
    report = {"game_dir": str(Path(args.game_dir)), "data_dir": str(data),
              "index_dsar": [], "nxa": [], "patch_files": []}
    total_ids = set()
    for p in iter_index_dsar(data):
        try:
            d = DSAR(p)
            entry = {"file": p.name, "size": p.stat().st_size,
                     "chunks": d.chunk_count, "uncompressed": d.uncompressed_size}
            raw = d.decompress()
            if raw[:4] == b"\x11\x00\x00\xf0":
                b = Bundle(raw)
                entry["num_types"] = b.num_types
                entry["num_resources"] = b.num_files
                total_ids |= b.ids()
            report["index_dsar"].append(entry)
        except Exception as exc:
            report["index_dsar"].append({"file": p.name, "error": str(exc)})
    for p in iter_nxa(data):
        with p.open("rb") as fh:
            head = fh.read(32)
        if head[:4] == b"DSAR":
            _, _, chunks, hdrsize, usize, _ = struct.unpack("<4s4sIIQ8s", head)
            report["nxa"].append({"file": p.name, "size": p.stat().st_size,
                                  "chunks": chunks, "header_size": hdrsize,
                                  "uncompressed": usize})
    for p in sorted(data.glob("*.patch_*")):
        if p.suffix.startswith(".patch_") or ".patch_" in p.name:
            if not any(p.name.endswith(x) for x in (".stream", ".gpu_resources")):
                report["patch_files"].append({"file": p.name, "size": p.stat().st_size})
    report["total_distinct_resource_ids_in_index_bundles"] = len(total_ids)
    out = OUT_DIR / "game_index.json"
    write_json(out, report)
    print("index:", len(report["index_dsar"]), "index DSAR,", len(report["nxa"]), "NXA,",
          len(report["patch_files"]), "deployed patches")
    print("distinct resource ids in index bundles:", len(total_ids))
    print("written:", out)
    return 0


def cmd_inspect(args):
    names = NameDB.load(DEFAULT_NAMES_DIR)
    b = Bundle.load(Path(args.patch))
    print("file:", b.path, "bytes:", len(b.main), "gpu:", len(b.gpu))
    print("types:", b.num_types, "resources:", b.num_files)
    print("approx_main:", b.approx_main, "approx_gpu:", b.approx_gpu)
    for i, r in enumerate(b.resources):
        label = names.resolve(r.name)
        print("  %2d name=%s type=%-9s main=%-8d gpu=%-9d %s" % (
            i, hex64(r.name), resource_type_name(r.type, names), r.size, r.gpu_size,
            label[0] if label else ""))
    return 0


def cmd_resolve(args):
    names = NameDB.load(DEFAULT_NAMES_DIR)
    targets = []
    if args.patch:
        b = Bundle.load(Path(args.patch))
        targets = sorted(b.ids())
    targets += [int(x, 16) for x in args.hashes]
    for h in targets:
        hit = names.resolve(h)
        print("%s %s" % (hex64(h), " | ".join(hit) if hit else "(unknown)"))
    return 0


def cmd_diagnose(args):
    names = NameDB.load(DEFAULT_NAMES_DIR)
    b = Bundle.load(Path(args.patch))
    data = game_data_dir(args)
    current_ids = set()
    for p in iter_index_dsar(data):
        try:
            raw = DSAR(p).decompress()
            if raw[:4] == b"\x11\x00\x00\xf0":
                current_ids |= Bundle(raw).ids()
        except Exception:
            continue
    mine_units = {}
    if MINE_LIST.is_file():
        for line in MINE_LIST.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"0x([0-9a-f]{16})\.0x([0-9a-f]{16})", line)
            if m and "throwables" in line and ".unit" in line:
                mine_units[int(m.group(1), 16)] = line.split(",")[0]
    rows = []
    for i, r in enumerate(b.resources):
        rows.append({
            "index": i, "name": hex64(r.name), "type": hex64(r.type),
            "type_name": resource_type_name(r.type, names),
            "name_known": names.resolve(r.name), "main_size": r.size,
            "gpu_size": r.gpu_size,
            "in_index_bundles": r.name in current_ids,
            "in_current_mine_units": r.name in mine_units,
            "current_unit_path": mine_units.get(r.name),
        })
    summary = {
        "patch": str(b.path), "resources": b.num_files,
        "distinct_ids": len(b.ids()),
        "ids_in_index_bundles": sum(1 for x in rows if x["in_index_bundles"]),
        "known_names": sum(1 for x in rows if x["name_known"]),
        "current_build_mine_units": len(mine_units),
        "mine_unit_ids_still_present": sorted(
            hex64(h) for h in mine_units if h in b.ids()),
    }
    write_json(OUT_DIR / "diagnose.json", {"summary": summary, "resources": rows})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("details:", OUT_DIR / "diagnose.json")
    return 0


def cmd_extract(args):
    out = Path(args.out)
    if args.dsar:
        d = DSAR(Path(args.dsar))
        raw = d.decompress()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        print("DSAR ->", out, len(raw), "bytes,", d.chunk_count, "chunks")
        return 0
    if args.bundle:
        b = Bundle.load(Path(args.bundle))
        want = int(args.hash, 16)
        hits = [i for i, r in enumerate(b.resources) if r.name == want]
        if not hits:
            raise SystemExit("hash not found in bundle")
        out.mkdir(parents=True, exist_ok=True)
        for i in hits:
            r = b.resources[i]
            stem = "%s_%s" % (hex64(r.name), hex64(r.type))
            (out / (stem + ".bin")).write_bytes(b.main_bytes(i))
            if r.gpu_size:
                (out / (stem + ".gpu_resources")).write_bytes(b.gpu_slices[i])
            print("resource", i, "->", stem, r.size, "+", r.gpu_size, "gpu bytes")
        return 0
    raise SystemExit("specify --dsar or --bundle")


def cmd_fixheader(args):
    src = Path(args.patch)
    b = Bundle.load(src)
    before = (b.approx_main, b.approx_gpu)
    main = bytearray(b.main)
    struct.pack_into("<QQ", main, 0x20, len(b.main), len(b.gpu))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        for suffix, data in (("", bytes(main)),
                             (".gpu_resources", b.gpu),
                             (".stream", b.stream)):
            if suffix and not data:
                continue
            (Path(str(out) + suffix)).write_bytes(data)
        dest = out
    else:
        src.write_bytes(bytes(main))
        dest = src
    print("patch:", src)
    print("approx_main/gpu: %d/%d -> %d/%d" % (before[0], before[1], len(b.main), len(b.gpu)))
    print("actual main/gpu: %d/%d" % (len(b.main), len(b.gpu)))
    print("written:", dest)
    return 0


def cmd_rebuild(args):
    source = Bundle.load(Path(args.patch))
    cfg = json.loads(Path(args.groups).read_text(encoding="utf-8"))
    groups = cfg["groups"]
    shared = cfg.get("shared_indices", [])
    out = Path(args.out)
    seen = []
    for g in groups:
        ids = g.get("resource_indices")
        if ids is None:
            ids = sorted(shared + [g["unit_index"]])
        seen.extend(ids)
        main, gpu = rebuild(source, ids, full_gpu=args.full_gpu)
        d = out / g["key"]
        d.mkdir(parents=True, exist_ok=True)
        stem = "9ba626afa44a3aa3.patch_0"
        (d / stem).write_bytes(main)
        (d / (stem + ".gpu_resources")).write_bytes(gpu)
        (d / (stem + ".stream")).write_bytes(b"")
        print("%-12s %2d resources  patch=%7d  gpu=%9d" % (
            g["key"], len(ids), len(main), len(gpu)))
    if sorted(set(seen)) != list(range(source.num_files)):
        raise SystemExit("groups must cover every resource at least once")
    print("rebuild OK ->", out)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game-dir", default=str(DEFAULT_GAME_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    sub.add_parser("index").set_defaults(func=cmd_index)
    p = sub.add_parser("inspect")
    p.add_argument("patch")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("resolve")
    p.add_argument("--patch")
    p.add_argument("hashes", nargs="*", default=[])
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("diagnose")
    p.add_argument("patch", nargs="?", default=str(DEFAULT_OLD_PATCH))
    p.set_defaults(func=cmd_diagnose)
    p = sub.add_parser("extract")
    p.add_argument("--dsar")
    p.add_argument("--bundle")
    p.add_argument("--hash")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("fixheader")
    p.add_argument("patch")
    p.add_argument("--out")
    p.set_defaults(func=cmd_fixheader)
    p = sub.add_parser("rebuild")
    p.add_argument("--patch", required=True)
    p.add_argument("--groups", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--full-gpu", action="store_true")
    p.set_defaults(func=cmd_rebuild)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
