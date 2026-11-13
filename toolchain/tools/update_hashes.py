"""合并外部 hash 源，更新 filediver 的名字表。

filediver 自带的 hashes.txt 是几个社区表合并的产物（见
third_party/filediver/hashes/generate/combine/main.go）。游戏更新后新资源往往
还没进表，表现为 filediver 只能给出 hash。这里按同样的来源重新合并一遍，
只更新名字表、不动 filediver 二进制。

用法：
    python tools/update_hashes.py            # 更新 third_party/filediver/hashes/hashes.txt
    python tools/update_hashes.py --dry-run  # 只统计，不写文件
"""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASHES = ROOT / "third_party" / "filediver" / "hashes"
TARGET = HASHES / "hashes.txt"

# 与 combine/main.go 同一组来源
SOURCES = (
    ("https://raw.githubusercontent.com/Xaymar/Hellextractor/root/files.txt", None),
    ("https://raw.githubusercontent.com/Xaymar/Hellextractor/root/types.txt", None),
    # 这份带很多字符串碎片，但能把覆盖率再提一点
    ("https://raw.githubusercontent.com/HW12Dev/Helldivers2-Hashlist/main/hashlist.txt", "before_dot"),
    ("https://raw.githubusercontent.com/dtzxporter/hd2-name-db/main/assets.txt", "after_comma"),
)

LOCAL = ("cracked.txt", "wwise_streams.txt")

PROLOG = """// Copied from Hellextractor by Xaymar (https://github.com/Xaymar/Hellextractor),
// hd2-name-db by DTZxPorter (https://github.com/dtzxporter/hd2-name-db) and
// Helldivers 2 Hashlist by HW12Dev (https://github.com/HW12Dev/Helldivers2-Hashlist).
// This is a list of all known file type and file path strings.
// Their respective hashes will be generated automatically.
"""


def download(url, path):
    """走 curl：本机 python 的 urllib 不读代理变量，直连 github 经常半路断。"""
    for extra in ((), ("--noproxy", "*")):
        cmd = ["curl", "-sS", "-L", "-m", "180", "-o", str(path)] + list(extra) + [url]
        if subprocess.run(cmd, check=False).returncode == 0 and path.exists()                 and path.stat().st_size > 1000:
            return True
    return False


def fetch(url, transform, cache):
    """下载并解析；失败返回 None（外部表缺一两份不影响整体）。"""
    name = url.rsplit("/", 1)[-1]
    path = cache / name
    if not path.exists() and not download(url, path):
        print("skip", name, "download failed")
        return None
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if transform == "before_dot":
            line = line.split(".", 1)[0]
        elif transform == "after_comma" and "," in line:
            line = line.split(",", 1)[1]
        if line:
            out.add(line)
    print("  %-20s %7d" % (name, len(out)))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cache", default=str(ROOT / "out" / "_hashcache"))
    args = parser.parse_args()

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    names = set()
    for url, transform in SOURCES:
        got = fetch(url, transform, cache)
        if got:
            names |= got

    for name in LOCAL:
        path = HASHES / name
        if not path.exists():
            continue
        added = {line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                 if line.strip() and not line.startswith(("//", "#"))}
        print("  %-20s %7d" % (name, len(added)))
        names |= added

    print("merged:", len(names))
    if args.dry_run:
        return

    # 合并结果的规模必须和现有表同量级，否则说明有源没拉到，宁可不写
    current = 0
    if TARGET.exists():
        current = sum(1 for line in TARGET.read_text(encoding="utf-8", errors="ignore").splitlines()
                      if line.strip() and not line.startswith(("//", "#")))
    if len(names) < current * 0.9:
        raise SystemExit("refusing to shrink hashes: %d -> %d (a source failed)"
                         % (current, len(names)))

    if TARGET.exists():
        shutil.copy2(TARGET, TARGET.with_suffix(".txt.bak"))
    with TARGET.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(PROLOG)
        for name in sorted(names):
            fh.write(name + "\n")
    print("wrote", TARGET, TARGET.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
