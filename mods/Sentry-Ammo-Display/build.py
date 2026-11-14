#!/usr/bin/env python3
"""生成「炮塔头顶弹药显示（仅 MDL3）」：改造核心、改写归档里的 lua、校验后打包。"""

import hashlib
import json
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT.parents[1] / "dist"
sys.path.insert(0, str(ROOT.parents[1] / "toolchain"))
from hd2tool import build_lua_bundle, read_lua_bundle  # noqa: E402

ZIP_NAME = "Sentry-Ammo-Display.zip"
# 版本号只写在 version.txt，清单里的显示名由构建时拼装，不手改。
VERSION_FILE = ROOT / "version.txt"
NAME_TEMPLATE = "炮塔阵地弹药显示 MDL3版 %s"
CORE = ROOT / "core"
PATCH = CORE / "9ba626afa44a3aa3.patch_0"

CORE_LUA = "e02b68d623c30acd.lua"
ADAPTER_LUA = "2d8169d01fdd078e.lua"
CORE_MODULE = "mods/sumitm/sentry_ammo_display/core"
ADAPTER_MODULE = "mods/sumitm/sentry_ammo_display/mdl_adapter"
LOADER_ID = "8f97174d125328ba"
BOOT_ID = "fa95186993df76f4"
LUA_TYPE = "e217d12cfa8d4ea1"
ALWAYS = ("manifest.json", "README.md", "thumbnail.png", "preview.png", "core/" + CORE_LUA,
          "core/" + ADAPTER_LUA, "core/9ba626afa44a3aa3.patch_0",
          "core/9ba626afa44a3aa3.patch_0.gpu_resources",
          "core/9ba626afa44a3aa3.patch_0.stream",
          "core/ee6b1ba7e22d71ed.patch_0",
          "core/ee6b1ba7e22d71ed.patch_0.gpu_resources",
          "core/ee6b1ba7e22d71ed.patch_0.stream")


GROUP_MODULE_PREFIX = "mods/sumitm/sentry_ammo_display/group_"

GROUPS = (
    ("ammo", "实弹哨戒", "机枪 / 加特林 / 自动 / 火箭 / 火焰",
     "8f3a1c62-2b4d-4e71-9a05-6c1d7e8f9a10"),
    ("mortar", "迫击炮", "迫击 / 电磁迫击 / 毒气迫击",
     "b1d7e405-6a29-4c83-8f17-2e9b0d4a5c23"),
    ("laser", "激光哨戒", "激光哨戒",
     "c4e2a918-3f05-4b6d-a271-58d09e6b4f34"),
    ("tesla", "特斯拉塔", "特斯拉塔",
     "d6f0b273-9c14-4a58-b309-71e2c5a8d045"),
    ("emplacement", "阵地", "重机枪 / 反坦克 / 榴弹兵墙",
     "e8c5d194-7b30-4f62-9a83-0d17b4e6c956"),
)

M64 = 0xC6A4A7935BD1E995
MASK = (1 << 64) - 1


def idstring64(text):
    data = text.encode("utf-8")
    length = len(data)
    h = (length * M64) & MASK
    whole = length - length % 8
    for i in range(0, whole, 8):
        v = int.from_bytes(data[i:i + 8], "little")
        v = (v * M64) & MASK
        v ^= v >> 47
        v = (v * M64) & MASK
        h ^= v
        h = (h * M64) & MASK
    if whole < length:
        h ^= int.from_bytes(data[whole:], "little")
        h = (h * M64) & MASK
    h ^= h >> 47
    h = (h * M64) & MASK
    h ^= h >> 47
    return h.to_bytes(8, "little")


# 归档里的行标签就是 addon 名的 IDString64，改名等于换行标签，两者必须同源。
CORE_ID = idstring64(CORE_MODULE).hex()
ADAPTER_ID = idstring64(ADAPTER_MODULE).hex()


def build_archive(source: bytes, module: str) -> bytes:
    return build_lua_bundle(module, source)


def group_marks():
    """每个组一个极小 patch：里面只有一条把组名写进全局表的 lua。"""
    out = []
    for key, label, detail, guid in GROUPS:
        module = GROUP_MODULE_PREFIX + key
        source = ("""-- HD2-Addon: %s
local t=rawget(_G,'SENTRY_AMMO_GROUPS')
if type(t)~='table' then t={};rawset(_G,'SENTRY_AMMO_GROUPS',t) end
t.%s=true
""" % (module, key)).encode("utf-8")
        folder = ROOT / "groups" / key
        folder.mkdir(parents=True, exist_ok=True)
        name = idstring64(module).hex() + ".patch_0"
        archive = build_archive(source, module)
        if read_lua_bundle(archive)[1] != source:
            raise SystemExit("分组 %s 的归档读回来不是预期 lua" % key)
        for stale in folder.iterdir():
            if stale.name != name and stale.suffix != ".png":
                stale.unlink()
        (folder / name).write_bytes(archive)
        for suffix in (".gpu_resources", ".stream"):
            (folder / (name + suffix)).write_bytes(b"")
        out.append((key, label, detail, guid, "groups/%s/%s" % (key, name)))
    return out


def stamp_version():
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit("version.txt 是空的")
    path = ROOT / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    name = NAME_TEMPLATE % version
    if m.get("Name") != name:
        m["Name"] = name
        path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="")
    return version


def build_payloads():
    for script in ("patch_core.py", "patch_archive.py"):
        subprocess.run([sys.executable, str(ROOT / "tools" / script)], check=True)


def lua_runtime():
    try:
        import lupa
    except ImportError:
        return None
    rt = lupa.lua51.LuaRuntime()
    return rt


def compile_check(rt, source, name):
    loader = rt.eval("function(s,n) local f,e=loadstring(s,'@'..n) "
                     "if not f then return nil,tostring(e) end return f end")
    fn = loader(source, name)
    if not hasattr(fn, "__call__"):
        raise SystemExit("Lua 编译失败：%s" % name)


def archive_lua(buf):
    """返回 {id: (源码, 偏移, 长度)}，只取 lua 行。"""
    types, count = struct.unpack_from("<II", buf, 4)
    base = 72 + types * 32
    out = {}
    for i in range(count):
        r = base + i * 80
        if buf[r + 8:r + 16].hex() != LUA_TYPE:
            continue
        off = struct.unpack_from("<I", buf, r + 16)[0]
        length, kind = struct.unpack_from("<II", buf, off)
        if kind != 2:
            raise SystemExit("lua 行的类型前缀不是 2")
        out[buf[r:r + 8].hex()] = buf[off + 8:off + 8 + length].decode("utf-8")
    return out


def check(rt):
    m = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if not m.get("Guid") or not m.get("Name"):
        raise SystemExit("清单缺少 Guid 或 Name")

    buf = PATCH.read_bytes()
    if struct.unpack_from("<I", buf, 0)[0] != 0xF0000011:
        raise SystemExit("归档头不对")
    lua = archive_lua(buf)

    if BOOT_ID in lua or LOADER_ID in lua:
        raise SystemExit("归档里还留着自带加载器或 boot 行")
    if set(lua) != {ADAPTER_ID, CORE_ID}:
        raise SystemExit("lua 行不是只剩入口与核心：%s" % sorted(lua))
    if struct.unpack_from("<I", buf, 8)[0] != 8:
        raise SystemExit("归档行数应为 8（4 个 lua 删到 2）")
    if struct.unpack_from("<Q", buf, 32)[0] != len(buf):
        raise SystemExit("归档总长度字段与实际不符")

    core = (CORE / CORE_LUA).read_bytes().decode("utf-8")
    adapter = (CORE / ADAPTER_LUA).read_bytes().decode("utf-8")
    if lua.get(CORE_ID) != core:
        raise SystemExit("归档里的核心与 core/%s 不一致" % CORE_LUA)
    if lua.get(ADAPTER_ID) != adapter:
        raise SystemExit("归档里的入口与 core/%s 不一致" % ADAPTER_LUA)
    if (CORE / CORE_LUA).read_bytes().count(13) > 0:
        raise SystemExit("Core lua 被写成了 CRLF，长度对不上槽位")
    if "local ot=..." in core:
        raise SystemExit("核心仍在等外部注入 ot")
    if "sentry_ammo_loader" in adapter:
        raise SystemExit("入口仍引用自带加载器")
    if not adapter.startswith("-- HD2-Addon:"):
        raise SystemExit("入口缺少 HD2-Addon 声明")
    if "mods/codex/" in adapter or "mods/codex/" in core:
        raise SystemExit("lua 里还引用着上游命名空间")
    if CORE_MODULE not in adapter or ADAPTER_MODULE not in adapter:
        raise SystemExit("入口声明的 addon 名与行标签不同源")

    if rt is not None:
        for source, name in ((core, CORE_LUA), (adapter, ADAPTER_LUA)):
            compile_check(rt, source, name)
        for rid, source in lua.items():
            compile_check(rt, source, rid)
        print("lua  : 核心/入口/归档内两行全部编译通过")
    else:
        print("lua  : lupa 不可用，跳过编译检查")
    print("check: 归档 %d 个 lua 资源，boot 槽位已让出" % len(lua))

    for key, *_ in GROUPS:
        module = GROUP_MODULE_PREFIX + key
        path = ROOT / "groups" / key / (idstring64(module).hex() + ".patch_0")
        if not path.is_file():
            raise SystemExit("分组 %s 缺少归档 %s" % (key, path.name))
        rid, payload = read_lua_bundle(path.read_bytes())
        if rid != int.from_bytes(idstring64(module), "little"):
            raise SystemExit("分组 %s 的资源 id 对不上" % key)
        if not payload.startswith(b"-- HD2-Addon: ") or ("t.%s=true" % key).encode() not in payload:
            raise SystemExit("分组 %s 的 lua 不是预期内容" % key)
    print("check: %d 个分组补丁的类型表与 lua 行均可用" % len(GROUPS))


def apply_options(marks):
    """清单里六个勾选：核心必选，其余五组各自带一个标记 patch。"""
    path = ROOT / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    options = [{
        "Name": "核心（必选）",
        "Description": "弹药读取与头顶显示本体。不勾这一项，下面几组装了也不会有任何显示。",
        "Image": "thumbnail.png",
        "Include": ["core"],
    }]
    for key, label, detail, guid, rel in marks:
        options.append({
            "Name": label if detail == label else "%s（%s）" % (label, detail),
            "Description": "显示%s的头顶弹药。可与其他组同时勾选。" % detail,
            "Image": "images/group_%s.png" % key,
            "Include": ["groups/" + key],
        })
    m["IconPath"] = "thumbnail.png"
    m["Options"] = options
    path.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8", newline="")


def make_zip(marks):
    target = DIST / ZIP_NAME
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel in ALWAYS:
            z.write(ROOT / rel, rel)
        for icon in sorted((ROOT / "images").glob("group_*.png")):
            z.write(icon, "images/" + icon.name)
        for key, label, detail, guid, rel in marks:
            folder = (ROOT / rel).parent
            for item in sorted(folder.iterdir()):
                z.write(item, item.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as z:
        listed = z.namelist()
        if "manifest.json" not in listed:
            raise SystemExit("ZIP 根目录没有 manifest.json")
        if z.testzip() is not None:
            raise SystemExit("ZIP 完整性校验失败")
    return target, listed


def main():
    build_payloads()
    print("version:", stamp_version())
    marks = group_marks()
    apply_options(marks)
    print("groups:", len(marks))
    rt = lua_runtime()
    check(rt)
    target, listed = make_zip(marks)
    digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    print("zip  :", target)
    print("files:", len(listed), "bytes:", target.stat().st_size)
    print("sha256:", digest)


if __name__ == "__main__":
    main()
