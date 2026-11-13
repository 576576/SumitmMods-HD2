#!/usr/bin/env python3
"""用当前游戏数据修正 patch 里的 unit 版本与布局，消除 Arsenal 的旧 unit 警告。"""

import json
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
GROUPS_FILE = TOOLS / "groups.json"
REPORT = TOOLS / "repatch_report.json"
BUNDLE = "9ba626afa44a3aa3"
STEM = BUNDLE + ".patch_0"

sys.path.insert(0, str(TOOLS / "repatcher"))
REC = struct.Struct("<7Q6I")
TYPE_UNIT = 0xE0A48D0BE9A7453F
MAX_CHANGED = 64


def option_keys():
    cfg = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    return [g["key"] for g in cfg["groups"]] + [o["key"] for o in cfg["standalone_options"]]


def game_data_dir():
    cfg = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    path = os.environ.get("HD2_DATA") or cfg.get("game_data")
    if not path or not Path(path).is_dir():
        raise SystemExit("game data folder not found; set HD2_DATA or groups.json game_data")
    return Path(path)


def unit_ranges(data):
    nt, nr = struct.unpack_from("<II", data, 4)
    base = 72 + 32 * nt
    out = []
    for i in range(nr):
        r = REC.unpack_from(data, base + 80 * i)
        if r[1] == TYPE_UNIT:
            out.append((r[0], r[2], r[2] + r[7]))
    return out


def changed_regions(a, b):
    out = []
    i, n = 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def run():
    import update_unit_mods as u

    data_dir = game_data_dir()
    u.init_game_resources(str(data_dir))
    report = {"game_data": str(data_dir), "units_indexed": len(u.game_resource_mapping), "options": []}

    for key in option_keys():
        patch = ROOT / "options" / key / STEM
        if not patch.is_file():
            raise SystemExit("missing patch for %s" % key)
        gpu = Path(str(patch) + ".gpu_resources")
        gpu_before = gpu.read_bytes()
        before = patch.read_bytes()
        nt_before, nr_before = struct.unpack_from("<II", before, 4)
        units = unit_ranges(before)

        # 只覆盖材质的选项没有 unit 可行，repatcher 无从下手。
        if not units:
            report["options"].append({"key": key, "changed_bytes": 0,
                                      "unit_ranges": [], "units": []})
            print("%-12s changed= 0  (no unit in this patch, skipped)" % key)
            continue

        # 版本已经对齐的 unit 不必过 repatcher：它会按游戏原版重写整份 unit，
        # 结构改动（新增 mesh、光柱）会被抹掉。
        pending = []
        for unit_id, lo, _ in units:
            version = struct.unpack_from("<I", before, lo + 0x2C)[0]
            expected = struct.unpack("<I", u.get_data_from_original_file(unit_id)[0])[0]
            if version != expected:
                pending.append((unit_id, lo, expected))
        if units and not pending:
            report["options"].append({
                "key": key, "changed_bytes": 0,
                "unit_ranges": [[lo, hi] for _, lo, hi in units],
                "units": [{"unit": "%016x" % unit_id,
                           "version": "%#x" % struct.unpack_from("<I", before, lo + 0x2C)[0]}
                          for unit_id, lo, _ in units],
            })
            print("%-12s changed= 0  %s (already current, skipped)" % (
                key, ", ".join("%016x" % unit_id for unit_id, _, _ in units)))
            continue

        code, _ = u.update_patch_file(str(patch))
        after = patch.read_bytes()
        nt_after, nr_after = struct.unpack_from("<II", after, 4)
        if (nt_before, nr_before) != (nt_after, nr_after):
            raise SystemExit("%s: resource table changed during repatch" % key)
        regions = changed_regions(before, after)
        total = sum(j - i for i, j in regions)
        inside = all(any(lo <= i and j <= hi for _, lo, hi in units) for i, j in regions)
        if total > MAX_CHANGED or not inside:
            raise SystemExit("%s: repatch touched %d bytes outside the unit (%s)" % (key, total, regions[:4]))
        if gpu.read_bytes() != gpu_before:
            raise SystemExit("%s: gpu file changed during repatch" % key)

        if len(after) != struct.unpack_from("<Q", after, 0x20)[0]:
            fixed = bytearray(after)
            struct.pack_into("<QQ", fixed, 0x20, len(after), len(gpu_before))
            patch.write_bytes(bytes(fixed))

        entries = []
        for unit_id, lo, hi in units:
            version = struct.unpack_from("<I", after, lo + 0x2C)[0]
            expected = struct.unpack("<I", u.get_data_from_original_file(unit_id)[0])[0]
            if version != expected:
                raise SystemExit("%s: unit %016x version %#x, game expects %#x" % (key, unit_id, version, expected))
            entries.append({"unit": "%016x" % unit_id, "version": "%#x" % version})
        report["options"].append({
            "key": key, "changed_bytes": total, "unit_ranges": [[lo, hi] for _, lo, hi in units],
            "units": entries,
        })
        print("%-12s changed=%2d  %s" % (key, total, ", ".join("%s=%s" % (e["unit"], e["version"]) for e in entries)))

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def verify():
    if not REPORT.is_file():
        print("FAIL: repatch report missing")
        return 1
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    problems = []
    for item in report["options"]:
        patch = ROOT / "options" / item["key"] / STEM
        if not patch.is_file():
            problems.append("%s: missing patch" % item["key"])
            continue
        data = patch.read_bytes()
        if item["changed_bytes"] > MAX_CHANGED:
            problems.append("%s: %d bytes changed" % (item["key"], item["changed_bytes"]))
        for lo, hi in item["unit_ranges"]:
            if hi > len(data):
                problems.append("%s: unit range outside file" % item["key"])
        if not item["units"] and item["unit_ranges"]:
            problems.append("%s: no unit repatched" % item["key"])
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: %d options repatched against build data, unit versions current" % len(report["options"]))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    sys.exit(verify() if cmd == "verify" else run())
