"""End-to-end check of the single RS-422 package.

For every option: unpack its archive, run the Lua for real under Lua 5.1 against a
sandbox copy of the sealed table, re-open the result with the sealed-box keypair and
verify fields + hash. Then prove the two behaviours the option layout depends on:
switching between options in place, and refusing a table it does not recognise.
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

from lupa import LuaRuntime
from nacl.bindings import crypto_box_seal_open

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "ordnance"))

import hd2edit  # noqa: E402

PK, SK = hd2edit.PK, hd2edit.SK
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "stock" / "generated_projectile_settings.orig.dl_bin"
BUNDLE = "9ba626afa44a3aa3"
RECORD, STRIDE = 186, 272
FIELDS = (60, 144)
EXPECT = {"轨道精准": 343, "轨道毒气": 82, "轨道炮": 52, "轨道电磁": 188}
TARGET_REL = Path("data") / "game" / "generated_projectile_settings.dl_bin"


def archive_payload(archive: bytes, offset=192) -> bytes:
    size = struct.unpack_from("<I", archive, offset)[0]
    typ = struct.unpack_from("<I", archive, offset + 4)[0]
    assert typ == 2, f"unexpected payload type {typ}"
    return archive[offset + 8:offset + 8 + size]


def record_base(plain, rid):
    count = struct.unpack_from("<I", plain, 36)[0]
    for i in range(count):
        base = 44 + i * STRIDE
        if struct.unpack_from("<I", plain, base)[0] == rid:
            return base
    raise SystemExit("record missing")


def sandbox():
    work = Path(tempfile.mkdtemp(prefix="rs422-"))
    target = work / TARGET_REL
    target.parent.mkdir(parents=True)
    shutil.copy2(SRC, target)
    return work, target


def run_lua(lua, work):
    cwd = os.getcwd()
    os.chdir(work)
    try:
        LuaRuntime(unpack_returned_tuples=True).execute(lua)
    finally:
        os.chdir(cwd)


def fields_of(sealed, base, want_hash):
    plain = crypto_box_seal_open(sealed, PK, SK)
    got = tuple(struct.unpack_from("<I", plain, base + o)[0] for o in FIELDS)
    return got, hd2edit.murmur64(plain) == want_hash


def main():
    # 产物集中在仓库 dist/，只取本 mod 的包，别被其它 mod 的 zip 干扰
    zips = sorted((ROOT.parents[1] / "dist").glob("RS-422-Railgun-Ultra*.zip"))
    if len(zips) != 1:
        raise SystemExit("dist 里应当只有一个 RS-422 Railgun Ultra 包，实际：%s" % [p.name for p in zips])
    zip_path = zips[0]

    sealed = SRC.read_bytes()
    base = record_base(crypto_box_seal_open(sealed, PK, SK), RECORD)
    want_hash = hd2edit.murmur64(crypto_box_seal_open(sealed, PK, SK))

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        lua = {}
        for opt in manifest["Options"]:
            inc = opt["Include"][0]
            key = inc.split("/", 1)[1]
            archive = z.read("%s/%s.patch_0" % (inc, BUNDLE))
            lua[key] = archive_payload(archive).decode("utf-8")

    ok_all = True

    struct_ok = (len(manifest["Options"]) == len(EXPECT)
                 and manifest.get("Guid")
                 and manifest["IconPath"] in names
                 and all(o["Image"] in names and any(n.startswith(o["Include"][0] + "/") for n in names)
                         for o in manifest["Options"]))
    ok_all &= struct_ok
    print(f"manifest  name={manifest['Name']!r} options={len(manifest['Options'])} "
          f"guid={manifest['Guid'][:8]} files={len(names)} -> {'OK' if struct_ok else 'FAIL'}")

    for opt in manifest["Options"]:
        key = opt["Include"][0].split("/", 1)[1]
        label = opt["Name"].split(" / ")[0]
        work, target = sandbox()
        run_lua(lua[key], work)
        first = target.read_bytes()
        run_lua(lua[key], work)
        second = target.read_bytes()
        got, hash_ok = fields_of(first, base, want_hash)
        good = got == (EXPECT[label], EXPECT[label]) and hash_ok and first == second
        ok_all &= good
        print(f"{label:<8} fields={got} hash={hash_ok} idempotent={first == second} "
              f"-> {'OK' if good else 'FAIL'}")
        shutil.rmtree(work, ignore_errors=True)

    # switch in place: apply all four in sequence on one table, no restore in between
    work, target = sandbox()
    chain_ok = True
    for opt in manifest["Options"]:
        key = opt["Include"][0].split("/", 1)[1]
        label = opt["Name"].split(" / ")[0]
        run_lua(lua[key], work)
        got, hash_ok = fields_of(target.read_bytes(), base, want_hash)
        good = got == (EXPECT[label], EXPECT[label]) and hash_ok
        chain_ok &= good
        print(f"switch -> {label:<8} fields={got} hash={hash_ok} -> {'OK' if good else 'FAIL'}")
    ok_all &= chain_ok

    # unknown state must be left alone
    work, target = sandbox()
    data = bytearray(target.read_bytes())
    data[32] ^= 0xFF
    target.write_bytes(bytes(data))
    run_lua(lua["precision"], work)
    untouched = target.read_bytes() == bytes(data)
    ok_all &= untouched
    print(f"unknown state refused -> {'OK' if untouched else 'FAIL'}")
    shutil.rmtree(work, ignore_errors=True)

    print("\nALL OK" if ok_all else "\nFAILURES PRESENT")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
