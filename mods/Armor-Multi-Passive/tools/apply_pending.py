#!/usr/bin/env python3
"""把排队的护甲被动写进 generated_customization_passive_bonuses.dl_bin。

游戏进程存活期间独占地持有这个 dl_bin，rename 和原地写都会拿到
Permission denied，因此模组自己在游戏内无法完成写盘。本脚本在游戏
关闭时执行同一套合成与加密逻辑（直接复用 src 里的 lua，不重写实现）。

用法：
    python tools/apply_pending.py                # 应用排队的选择
    python tools/apply_pending.py --dry-run      # 只报告将要写什么
    python tools/apply_pending.py --restore      # 还原成原版
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LUA_SRC = ROOT / "src" / "armor_multi_passive.lua"
CORE = ROOT / "core"

TARGET_REL = Path("data/game/generated_customization_passive_bonuses.dl_bin")
PENDING = "ArmoryForge-Multi.pending"
STATE = "ArmoryForge-Multi.state"
KEEP = "ArmoryForge-Multi.keep_original"
ORIGINAL = "ArmoryForge-Multi.original.dl_bin"
APPLIED = "ArmoryForge-Multi.applied.dl_bin"

DEFAULT_GAME_DIR = Path("E:/SteamLibrary/steamapps/common/Helldivers 2")


def game_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("HD2_GAME_DIR")
    if env:
        return Path(env)
    return DEFAULT_GAME_DIR


def game_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq helldivers2.exe", "/NH"],
            capture_output=True, timeout=20,
        ).stdout
    except Exception:
        return False
    return b"helldivers2.exe" in out.lower()


def lua_source() -> bytes:
    """取 lua 本体：源码树里读 src，安装包里读 core 归档的载荷。

    归档里的 lua 就是构建时写进去的那一份，所以在包内运行时不需要额外带 src/。
    """
    if LUA_SRC.is_file():
        return LUA_SRC.read_bytes()
    import struct

    for patch in sorted(CORE.glob("*.patch_0")):
        data = patch.read_bytes()
        types, rows = struct.unpack_from("<II", data, 4)
        for i in range(rows):
            row = 72 + 32 * types + 80 * i
            offset = struct.unpack_from("<Q", data, row + 16)[0]
            length = struct.unpack_from("<I", data, offset)[0]
            payload = data[offset + 8:offset + 8 + length]
            if payload.lstrip().startswith(b"--"):
                return payload
    raise SystemExit("找不到 lua 本体：src/armor_multi_passive.lua 与 core/*.patch_0 都没有")


def load_offline():
    """在无宿主环境下加载 lua，取出 build/seal 与内嵌常量。"""
    import lupa

    rt = lupa.lua51.LuaRuntime(encoding=None)
    rt.execute(b"ARMORY_FORGE_OFFLINE = true")
    loader = rt.eval(
        b"function(s) local f,e = loadstring(s,'@armor') if not f then error(e) end return f end"
    )
    chunk = loader(lua_source())
    table = chunk()
    key = lambda name: name.encode()
    return {
        "rt": rt,
        "plaintext": table[key("original_plaintext")],
        "build": table[key("build")],
        "seal": table[key("seal")],
    }


def parse_pending(raw: bytes) -> list[int] | None:
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("AF2\n"):
        return None
    line = text.split("\n", 2)[1]
    return sorted({int(x) for x in line.split(",") if x.strip()})


def read_or_none(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="在游戏外应用排队中的护甲被动")
    ap.add_argument("--game-dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true", help="清空选择并还原原版")
    ap.add_argument("--force", action="store_true", help="游戏在运行时也继续")
    args = ap.parse_args()

    root = game_dir(args.game_dir)
    target = root / TARGET_REL
    if not target.is_file():
        print("找不到游戏内的目标文件：%s" % target)
        return 2
    if game_running() and not args.force:
        print("游戏正在运行，文件被独占，写不进去。请先完全退出游戏。")
        return 3

    envelope = load_offline()
    rt = envelope["rt"]

    keep = True
    raw_keep = read_or_none(root / KEEP)
    if raw_keep:
        keep = raw_keep[:1] != b"0"

    live = target.read_bytes()
    original_path = root / ORIGINAL
    applied_path = root / APPLIED
    original = read_or_none(original_path)
    applied = read_or_none(applied_path)

    pending_path = root / PENDING
    if args.restore:
        if original is None:
            print("没有 %s，无法还原。" % ORIGINAL)
            return 2
        ids, source = None, "命令行 --restore"
    else:
        raw = read_or_none(pending_path)
        if raw is None:
            ids, source = parse_pending(read_or_none(root / STATE) or b""), "已生效状态（无排队）"
            if ids is None:
                print("既没有排队文件也没有生效状态，无从下手。")
                return 2
        else:
            ids = parse_pending(raw)
            if ids is None:
                print("%s 格式无法识别（需要 AF2 头 + 逗号分隔的编号）。" % PENDING)
                return 2
            source = "排队文件"

    known = {x for x in (original, applied) if x is not None}
    if live not in known and not args.force:
        # 游戏更新后重生成过：把当前 live 认作新原版，内置明文可能已过期。
        print("目标文件既不是已知原版也不是上次应用的结果——游戏可能重新生成过它。")
        print("  当前 %d 字节；已知原版 %s；上次应用 %s"
              % (len(live),
                 "%d 字节" % len(original) if original else "缺失",
                 "%d 字节" % len(applied) if applied else "缺失"))
        print("继续会用模组内置的明文重建，可能丢掉这次更新新增的被动数据。")
        print("确认要继续请加 --force（会先把当前文件备份成 %s）。" % ORIGINAL)
        return 4

    if ids is None:
        # --restore：直接写回原版密文，不走 merger——清空选择在 keep_original=0 时
        # 是「去掉护甲自带被动」，与「还原原版」不是一回事。
        desired = original
        how = "还原为备份原版 %s（%d 字节）" % (ORIGINAL, len(original))
    elif not ids and keep:
        desired = live
        how = "空选择 + 保留原被动，还原为当前原版"
    else:
        lua_list = rt.eval(b"function(...) local t={...} table.sort(t) return t end")(*ids)
        try:
            plain = envelope["build"](envelope["plaintext"], lua_list, keep)
            desired = envelope["seal"](plain)
        except Exception as e:
            print("合成失败：%s" % e)
            return 5
        how = "明文 %d 字节 -> 密文 %d 字节" % (len(plain), len(desired))

    print("游戏目录  : %s" % root)
    print("选择来源  : %s（%d 项）" % (source, len(ids or [])))
    print("保留原被动: %s" % ("是" if keep else "否"))
    print("生成结果  : %s" % how)

    if desired == live:
        print("目标文件已经是这个内容，无需写入。")
        if pending_path.is_file() and not args.dry_run:
            pending_path.unlink()
            print("已清除排队文件。")
        return 0

    if args.dry_run:
        print("--dry-run：未写入。")
        return 0

    # 只有「既不是原版也不是上次应用结果」才说明游戏重新生成过它，此时才更新备份；
    # 否则还原时会把已应用的内容当成新原版写进备份，真原版就丢了。
    if live != (original or b"") and live != (applied or b""):
        original_path.write_bytes(live)
        print("已把当前文件备份为 %s（%d 字节）" % (ORIGINAL, len(live)))

    target.write_bytes(desired)
    if target.read_bytes() != desired:
        print("写入校验失败：回读内容不一致。原版仍在 %s。" % ORIGINAL)
        return 6

    applied_path.write_bytes(desired)
    body = "AF2\n" + ",".join(str(i) for i in (ids or [])) + "\n"
    (root / STATE).write_bytes(body.encode("utf-8"))
    if pending_path.is_file():
        pending_path.unlink()

    print("已写入 %s（%d 字节）" % (TARGET_REL, len(desired)))
    print("下一步：直接启动游戏即可，游戏内 F6 面板的列表应与本次选择一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
