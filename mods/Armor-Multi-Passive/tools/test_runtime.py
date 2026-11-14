#!/usr/bin/env python3
"""在临时目录里跑一遍本 mod 的写盘逻辑，不需要启动游戏。

覆盖四条路径：
  1. 启动时把 pending 补写进 dl_bin（退出时那次写失败的情况靠这条兜底）
  2. 没有 pending 时不碰文件
  3. 改名被拒（游戏占用文件）时退化成原地覆盖
  4. 改名和覆盖都被拒时报错并保留 pending

用法：
    python tools/test_runtime.py [--game-dir <Helldivers 2>]
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import lupa

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "armor_multi_passive.lua"
TARGET = "data/game/generated_customization_passive_bonuses.dl_bin"
PENDING = "ArmoryForge-Multi.pending"
LOG = "ArmoryForge-Multi.log"
STATE = "ArmoryForge-Multi.state"
BACKUP = "ArmoryForge-Multi.original.dl_bin"
APPLIED = "ArmoryForge-Multi.applied.dl_bin"

FAKE_HOST = """
stingray = { Application = { build = function() return 'release' end } }
"""

BLOCK_RENAME = """
local real_rename = os.rename
os.rename = function(a, b)
    if a:match('generated_customization_passive_bonuses%.dl_bin$') then
        return nil, 'Permission denied'
    end
    return real_rename(a, b)
end
"""

BLOCK_WRITE = """
local real_open = io.open
io.open = function(path, mode)
    if mode == 'wb' and path:match('generated_customization_passive_bonuses%.dl_bin$') then
        return nil, 'Permission denied'
    end
    return real_open(path, mode)
end
"""


def runtime(source, prelude):
    rt = lupa.lua51.LuaRuntime(unpack_returned_tuples=True)
    rt.globals()["SRC"] = source
    rt.execute(FAKE_HOST + prelude)
    return rt


def sandbox(stock, pending=None):
    folder = tempfile.mkdtemp(prefix="armor-")
    root = Path(folder)
    (root / "data" / "game").mkdir(parents=True)
    (root / TARGET).write_bytes(stock)
    if pending is not None:
        # 必须按字节写：write_text 在 Windows 上会把 \n 翻成 \r\n，模组只认 LF。
        (root / PENDING).write_bytes(pending.encode("utf-8"))
    return root


def run(source, stock, pending=None, prelude=""):
    root = sandbox(stock, pending)
    cwd = os.getcwd()
    os.chdir(root)
    try:
        rt = runtime(source, prelude)
        ok, err = rt.execute("local f, e = loadstring(SRC, '@armor') "
                             "if not f then return false, tostring(e) end "
                             "local ok, why = pcall(f) return ok, tostring(why)")
        if not ok:
            raise SystemExit("执行失败：%s" % err)
        # 引擎退出时会调 _G.shutdown，这里照样调一次，把退出那条写盘路径也跑到。
        rt.execute("local s = rawget(_G, 'shutdown') if type(s) == 'function' then pcall(s) end")
        log = (root / LOG).read_text(encoding="utf-8", errors="replace") if (root / LOG).exists() else ""
        result = {
            "root": root,
            "log": log,
            "target": (root / TARGET).read_bytes(),
            "pending": (root / PENDING).read_bytes() if (root / PENDING).exists() else None,
            "state": (root / STATE).read_text(encoding="utf-8") if (root / STATE).exists() else None,
            "backup": (root / BACKUP).read_bytes() if (root / BACKUP).exists() else None,
            "applied": (root / APPLIED).read_bytes() if (root / APPLIED).exists() else None,
        }
    finally:
        os.chdir(cwd)
        shutil.rmtree(root, ignore_errors=True)
    return result


def check(condition, message):
    if not condition:
        raise SystemExit("FAIL " + message)
    print("  ok  " + message)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=os.environ.get("HD2_GAME_DIR",
                    r"E:/SteamLibrary/steamapps/common/Helldivers 2"))
    args = ap.parse_args()

    # 用模组认领的持久备份当夹具：游戏目录里的 target 可能已经被应用过，
    # 拿它当「原版」会让「写出来的文件比原版大」这条断言随状态漂移。
    stock_path = Path(args.game_dir) / BACKUP
    if not stock_path.is_file():
        stock_path = Path(args.game_dir) / TARGET
    if not stock_path.is_file():
        raise SystemExit("找不到游戏生成的原版文件：%s（用 --game-dir 指定游戏目录）" % stock_path)
    stock = stock_path.read_bytes()
    source = SRC.read_text(encoding="utf-8")
    if "\x00" in source:
        raise SystemExit("lua 源码里有裸 NUL，无法按文本传入")
    print("原版 %d 字节，lua %d 字符" % (len(stock), len(source)))

    print("1) 启动补写 pending")
    r = run(source, stock, "AF2\n1,2,3\n")
    check("ADOPTED new stock" in r["log"], "认领了当前原版")
    check("APPLIED AT BOOT ids=1,2,3" in r["log"], "启动时写入了 pending")
    check(len(r["target"]) > len(stock), "目标文件已被改写（%d 字节）" % len(r["target"]))
    check(r["pending"] is None, "pending 已清掉")
    check(r["state"] == "AF2\n1,2,3\n", "状态文件与选择一致")
    check(r["backup"] == stock, "备份就是游戏原版")
    check(r["applied"] == r["target"], "applied 副本与目标一致")
    applied = r["target"]

    print("2) 没有 pending 时不动文件")
    r = run(source, applied)
    check("EXIT no pending selection" in r["log"], "退出时没有待写内容")
    check("APPLIED" not in r["log"], "没有重复写入")
    check(r["target"] == applied, "目标文件未变")

    print("3) 改名被拒时原地覆盖")
    r = run(source, applied, "AF2\n5,6\n", BLOCK_RENAME)
    check("COMMIT in-place" in r["log"], "走了原地覆盖")
    check("APPLIED AT BOOT ids=5,6" in r["log"], "新选择已写入")
    check(r["target"] != applied, "目标文件已更新（%d 字节）" % len(r["target"]))
    check(r["pending"] is None, "pending 已清掉")
    replaced = r["target"]

    print("4) 改名与覆盖都被拒")
    r = run(source, replaced, "AF2\n7\n", BLOCK_RENAME + BLOCK_WRITE)
    check("BOOT APPLY ERROR" in r["log"], "报出了失败原因")
    check("in-place write failed" in r["log"], "指出了原地写也失败")
    check(r["target"] == replaced, "目标文件没有被破坏")
    check(r["pending"] is not None, "pending 留着等下次")

    print("\n全部通过")


if __name__ == "__main__":
    main()
