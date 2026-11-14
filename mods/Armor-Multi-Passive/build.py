#!/usr/bin/env python3
"""生成「护甲多被动（MDL3 版）」：重建归档、校验、打包到 dist/。"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT.parents[1] / "dist"
sys.path.insert(0, str(ROOT / "tools"))

import build_patch  # noqa: E402

ZIP_NAME = "Armor-Multi-Passive.zip"
VERSION_FILE = ROOT / "version.txt"
NAME_TEMPLATE = "护甲多被动 MDL3版 %s"
SRC = build_patch.SRC
CORE = build_patch.CORE


def stamp_version():
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("version.txt 是空的")
    path = ROOT / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    name = NAME_TEMPLATE % version
    if m.get("Name") != name:
        m["Name"] = name
        path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return version


def compile_check(source, name):
    try:
        import lupa
    except ImportError:
        print("lua  : lupa 不可用，跳过编译检查")
        return
    rt = lupa.lua51.LuaRuntime()
    rt.globals()["SRC"] = source.decode("utf-8")
    ok, why = rt.execute("local f, e = loadstring(SRC, '@x') "
                         "if f then return true, '' end return false, tostring(e)")
    if not ok:
        raise SystemExit("Lua 编译失败：%s" % why)
    print("lua  :", name, "编译通过")


def check(version):
    m = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if not m.get("Guid") or not m.get("Name"):
        raise SystemExit("清单缺少 Guid 或 Name")
    if version not in m["Name"]:
        raise SystemExit("清单显示名没有同步版本号：%s" % m["Name"])

    source = build_patch.stamped(SRC.read_bytes())
    module = build_patch.module_name(source)
    patch = CORE / build_patch.PATCH_NAME
    if not patch.is_file():
        raise SystemExit("缺少 core/%s" % build_patch.PATCH_NAME)
    others = sorted(p.name for p in CORE.glob("*.patch_*")
                    if not p.name.startswith(build_patch.PATCH_NAME))
    if others:
        raise SystemExit("core/ 里有多余的归档：%s" % others)

    archive = patch.read_bytes()
    rebuilt, _ = build_patch.build(build_patch.template(), source)
    if archive != rebuilt:
        raise SystemExit("归档与 src 的 lua 不一致，先跑 tools/build_patch.py")
    if build_patch.payload_of(archive) != source:
        raise SystemExit("归档里的 lua 载荷与 src 不符")
    tag = archive[build_patch.ROW_AT:build_patch.ROW_AT + 8]
    if tag != build_patch.idstring64(module).to_bytes(8, "little"):
        raise SystemExit("行标签不是模块名的哈希：%s" % tag.hex())

    for suffix in (".gpu_resources", ".stream"):
        sidecar = CORE / (build_patch.PATCH_NAME + suffix)
        if not sidecar.is_file():
            raise SystemExit("缺少 sidecar：" + sidecar.name)

    compile_check(source, "src lua")
    print("check: %s，模块名 %s，载荷 %d 字节" % (build_patch.PATCH_NAME, module, len(source)))


def package():
    target = DIST / ZIP_NAME
    DIST.mkdir(exist_ok=True)
    files = ["manifest.json", "README.md", "thumbnail.png"]
    files += sorted("core/" + p.name for p in CORE.iterdir() if p.is_file())
    files += sorted("Recovery/" + p.name for p in (ROOT / "Recovery").rglob("*") if p.is_file())
    # 游戏进程独占 dl_bin，写盘只能在游戏外完成；装包时把应用器和双击入口一并带上。
    files += ["apply.bat", "tools/apply_pending.py"]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(ROOT / rel, rel)
    with zipfile.ZipFile(target) as z:
        listed = z.namelist()
        if "manifest.json" not in listed:
            raise SystemExit("ZIP 根目录没有 manifest.json")
        if z.testzip() is not None:
            raise SystemExit("ZIP 完整性校验失败")
    return target, listed


def main():
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_patch.py")], check=True)
    version = stamp_version()
    print("version:", version)
    check(version)
    target, listed = package()
    digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    print("zip  :", target)
    print("files:", len(listed), "bytes:", target.stat().st_size)
    print("sha256:", digest)


if __name__ == "__main__":
    main()
