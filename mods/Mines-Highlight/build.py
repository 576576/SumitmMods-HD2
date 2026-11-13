#!/usr/bin/env python3
"""重建 Mines-Highlight 的选项包，离线校验，并产出 Arsenal 可导入的 ZIP。"""

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / "toolchain" / "common"))
sys.path.insert(0, str(ROOT / "tools"))
import split_bundle as sb

DIST = REPO / "dist"
# 文件名不带空格，管理器里显示的名字（BASE_NAME）才带空格，两者互不影响。
ZIP_NAME = "Mines-Highlight.zip"
VERSION_FILE = ROOT / "version.txt"
BASE_NAME = "Mines Highlight"
BUNDLE = sb.BUNDLE
OPTION_KEYS = ("contact", "incendiary", "tank", "gas",
               "thermite", "decoy", "caltrops", "bot", "bug")
ALL_KEYS = OPTION_KEYS


def ensure_icons():
    needed = [ROOT / "thumbnail.png"] + [ROOT / "images" / (k + ".png") for k in OPTION_KEYS]
    try:
        import make_icons
        make_icons.main()
    except Exception as exc:
        if not all(p.is_file() for p in needed):
            raise SystemExit("icons missing and generation failed: %s" % exc)
        print("icons: reused", len(needed), "PNG files (Pillow unavailable)")
        return
    missing = [str(p) for p in needed if not p.is_file()]
    if missing:
        raise SystemExit("icons still missing: %s" % ", ".join(missing))


def stamp_version():
    """名称里的版本号以 version.txt 为准，构建时写回清单。"""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("version.txt is empty")
    path = ROOT / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    name = "%s %s" % (BASE_NAME, version)
    if m.get("Name") != name:
        m["Name"] = name
        path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return version


def check_manifest():
    m = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if not m.get("Guid") or not m.get("Name"):
        raise SystemExit("manifest needs Guid and Name")
    if not (ROOT / m["IconPath"]).is_file():
        raise SystemExit("missing IconPath: %s" % m["IconPath"])
    for opt in m["Options"]:
        if opt.get("Image") and not (ROOT / opt["Image"]).is_file():
            raise SystemExit("missing option image: %s" % opt["Image"])
        incs = opt["Include"]
        if len(incs) != len(set(incs)):
            raise SystemExit("duplicate Include paths within one option: %s" % opt["Name"])
        for inc in incs:
            d = ROOT / inc
            for suffix in ("", ".gpu_resources", ".stream"):
                if not (d / (BUNDLE + ".patch_0" + suffix)).is_file():
                    raise SystemExit("missing %s" % (d / (BUNDLE + ".patch_0" + suffix)))
    return m


PACKAGE_FILES = ("manifest.json", "version.txt", "thumbnail.png", "README.md", "preview.png")


def ships(rel):
    """只打 Arsenal 要读的东西：清单、封面、选项图、说明和图，源码一律不进包。"""
    if rel in PACKAGE_FILES:
        return True
    if rel.startswith("options/"):
        return True
    return (rel.startswith("images/") and rel.count("/") == 1
            and rel.endswith(".png") and not rel.startswith("images/model_"))


def make_zip():
    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / ZIP_NAME
    entries = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if ships(rel):
            entries.append((rel, p))
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel, p in entries:
            z.write(p, rel)
    with zipfile.ZipFile(target) as z:
        names = z.namelist()
        if "manifest.json" not in names:
            raise SystemExit("ZIP has no manifest.json at root")
        patches = [n for n in names if n.startswith("options/") and n.endswith(".patch_0")]
        if sorted(n.split("/")[1] for n in patches) != sorted(ALL_KEYS):
            raise SystemExit("ZIP option patches mismatch: %s" % patches)
        if z.testzip() is not None:
            raise SystemExit("ZIP integrity check failed")
    return target, entries


def main():
    import restore_source
    import build_highlight
    import bug_patch
    import mine_patch
    import repatch_units
    restore_source.main()
    ensure_icons()
    print("build: rebuilding option packages")
    sb.cmd_build(argparse.Namespace())
    build_highlight.build()
    print("build: injecting the terminid highlight and light column")
    bug_patch.build()
    print("build: adding the thermite and lure mines")
    mine_patch.build()
    print("repatch: aligning unit versions with the current build")
    repatch_units.run()
    print("verify: checking against the original patches")
    if sb.cmd_verify(argparse.Namespace()) != 0:
        raise SystemExit("offline verification failed; ZIP not written")
    if build_highlight.verify() != 0:
        raise SystemExit("standalone option verification failed; ZIP not written")
    if bug_patch.verify() != 0:
        raise SystemExit("terminid option verification failed; ZIP not written")
    if mine_patch.verify() != 0:
        raise SystemExit("stratagem mine option verification failed; ZIP not written")
    if repatch_units.verify() != 0:
        raise SystemExit("unit repatch verification failed; ZIP not written")
    print("version:", stamp_version())
    check_manifest()
    target, entries = make_zip()
    digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    print("zip  :", target)
    print("files:", len(entries), "bytes:", target.stat().st_size)
    print("sha256:", digest)
    print("done : import this ZIP through Arsenal; game-side QA is still required")


if __name__ == "__main__":
    main()
