"""仓库内各目录的唯一来源。

共享库和各个 mod 的脚本都从这里取路径，不要在别处再拼相对层级：
换目录结构时只改这一个文件。
"""

from pathlib import Path

TOOLCHAIN = Path(__file__).resolve().parent.parent
REPO = TOOLCHAIN.parent
COMMON = TOOLCHAIN / "common"
MODS = REPO / "mods"
DIST = REPO / "dist"
DOCS = REPO / "docs"

RAWDUMP = TOOLCHAIN / "rawdump.exe"
UNITCHECK = TOOLCHAIN / "unitcheck.exe"
FILEDIVER = TOOLCHAIN / "third_party" / "filediver-cli.exe"
HASHES = TOOLCHAIN / "third_party" / "filediver" / "hashes"


def mod(name):
    """某个 mod 的目录，name 就是 mods/ 下的目录名。"""
    return MODS / name
