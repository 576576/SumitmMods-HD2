#!/usr/bin/env python3
"""SumitmMods-HD2 发布脚本：查看版本、升版、按项目重建并校验产物。

用法：
    python toolchain/release.py list
    python toolchain/release.py bump rs422 patch
    python toolchain/release.py bump mines 1.0.2 --build
    python toolchain/release.py build sentry
    python toolchain/release.py build rs422 --full

版本号只写在各自 mod 的 version.txt，本脚本不碰清单里的显示名——那是 build.py 的事。
产物统一落在仓库根的 dist/。
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

PROJECTS = {
    "mines": {
        "dir": "mods/Mines-Highlight",
        "label": "Mines Highlight",
        "build": ["build.py"],
        "artifacts": ["Mines-Highlight.zip"],
    },
    "sentry": {
        "dir": "mods/Sentry-Ammo-Display",
        "label": "炮塔阵地弹药显示",
        "build": ["build.py"],
        "artifacts": ["Sentry-Ammo-Display.zip"],
    },
    "armor": {
        "dir": "mods/Armor-Multi-Passive",
        "label": "护甲多被动",
        "build": ["build.py"],
        "artifacts": ["Armor-Multi-Passive.zip"],
    },
    "rs422": {
        "dir": "mods/RS-422-Railgun-Ultra",
        "label": "RS-422 Railgun Ultra",
        # 换游戏版本后先重烘补丁表（约 3 分钟），再打包、端到端验证。
        "full": ["tools/bake_lua_patch.py"],
        "build": ["tools/build_lua_mod.py", "tools/test_packages.py"],
        "artifacts": ["RS-422-Railgun-Ultra.zip"],
    },
}


def project_dir(name):
    return ROOT / PROJECTS[name]["dir"]


def version_of(name):
    path = project_dir(name) / "version.txt"
    if not path.is_file():
        raise SystemExit("%s 缺少 version.txt" % project_dir(name))
    return path.read_text(encoding="utf-8").strip()


def artifacts_of(name):
    out = []
    for pattern in PROJECTS[name]["artifacts"]:
        if "*" in pattern:
            out.extend(sorted(DIST.glob(pattern)))
        else:
            out.append(DIST / pattern)
    return [p for p in out if p.is_file()]


def manifest_name(zip_path):
    try:
        with zipfile.ZipFile(zip_path) as z:
            return json.loads(z.read("manifest.json").decode("utf-8"))["Name"]
    except Exception:
        return None


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def run(name, steps):
    cwd = project_dir(name)
    for step in steps:
        print(">>", step)
        subprocess.run([sys.executable, str(cwd / step)], cwd=str(cwd), check=True)


def report(name):
    print("version:", version_of(name))
    for artifact in artifacts_of(name):
        with zipfile.ZipFile(artifact) as z:
            count = len(z.namelist())
        print("  %-34s %10s B  files=%-3d sha256=%s"
              % (artifact.name, format(artifact.stat().st_size, ","), count,
                 sha256(artifact)))
        print("  %-34s 显示名: %s" % ("", manifest_name(artifact)))


def cmd_list(args):
    for name, info in PROJECTS.items():
        version = version_of(name)
        files = artifacts_of(name)
        state = "未构建" if not files else ""
        if files:
            shown = manifest_name(files[0]) or ""
            if version not in shown:
                state = "显示名未同步"
        print("%-7s %-14s %-9s %s" % (name, info["label"], version, state))
        for artifact in files:
            print("        %-34s %8s B  %s"
                  % (artifact.name, format(artifact.stat().st_size, ","), sha256(artifact)))
        if not files:
            print("        未找到产物：", info["artifacts"])


def bump_version(current, target):
    if VERSION_RE.match(target):
        return target
    if target not in ("patch", "minor", "major"):
        raise SystemExit("目标版本要写成 x.y.z，或 patch / minor / major")
    major, minor, patch = (int(x) for x in current.split("."))
    if target == "major":
        return "%d.0.0" % (major + 1)
    if target == "minor":
        return "%d.%d.0" % (major, minor + 1)
    return "%d.%d.%d" % (major, minor, patch + 1)


def cmd_bump(args):
    current = version_of(args.project)
    new = bump_version(current, args.version)
    (project_dir(args.project) / "version.txt").write_text(
        new + "\n", encoding="utf-8", newline="")
    print("%s: %s -> %s" % (args.project, current, new))
    if args.build:
        cmd_build(args)


def cmd_build(args):
    name = args.project
    steps = list(PROJECTS[name].get("full", [])) if getattr(args, "full", False) else []
    steps += PROJECTS[name]["build"]
    run(name, steps)
    report(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出各项目的版本、产物与哈希").set_defaults(func=cmd_list)

    p_bump = sub.add_parser("bump", help="升版本号，可顺带重建")
    p_bump.add_argument("project", choices=sorted(PROJECTS))
    p_bump.add_argument("version", help="x.y.z，或 patch / minor / major")
    p_bump.add_argument("--build", action="store_true", help="改完直接重建")
    p_bump.set_defaults(func=cmd_bump)

    p_build = sub.add_parser("build", help="按项目重建并校验")
    p_build.add_argument("project", choices=sorted(PROJECTS))
    p_build.add_argument("--full", action="store_true",
                         help="连预处理一起跑（rs422 会重烘字节补丁）")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
