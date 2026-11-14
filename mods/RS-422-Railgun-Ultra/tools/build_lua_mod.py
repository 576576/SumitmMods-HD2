"""Build the RS-422 Railgun Ultra package: one mod, four options.

Each option owns a folder (`options/<key>`) with a single Lua resource inside a
patch_0 archive. The Lua writes a precomputed set of ciphertext bytes — tag
included — so the game's sealed table still verifies after the rewrite.
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT.parents[1] / "dist"
sys.path.insert(0, str(ROOT.parents[1] / "toolchain"))
from hd2tool import build_lua_bundle, read_lua_bundle  # noqa: E402

ZIP_NAME = "RS-422-Railgun-Ultra.zip"
BUNDLE = "9ba626afa44a3aa3"
SRC = ROOT / "stock" / "generated_projectile_settings.orig.dl_bin"
VERSION = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
if not VERSION:
    raise SystemExit("version.txt 是空的")

BASE_NAME = "RS-422 Railgun Ultra"
# addon 名统一在本仓库的命名空间下，目录名与 mod 同名。
MODULE_PREFIX = "mods/sumitm/rs422_railgun_ultra/retarget_"

# key, 中文名, 英文名
OPTIONS = [
    ("precision", "轨道精准", "Orbital Precision Strike"),
    ("gas", "轨道毒气", "Orbital Gas Strike"),
    ("railcannon", "轨道炮", "Orbital Railcannon Strike"),
    ("ems", "轨道电磁", "Orbital EMS Strike"),
]

LUA_TEMPLATE = r'''-- HD2-Addon: __MODULE__
-- RS-422 Railgun Ultra（__LABEL__）。离线改数据表，不加载 FFI、不读内存。
-- 首次启动写入 dl_bin，重启游戏后生效。
local LABEL = "__LABEL__"
local MOD_VERSION = "__VERSION__"
local TARGET_FILE = "data/game/generated_projectile_settings.dl_bin"
local LOG_FILE = "RS422Retarget.log"
local EXPECT_SIZE = __SIZE__
local EXPECT_EPK = "__EPK__"
-- 要改写的密文偏移（升序）；每个已知状态在这些偏移上的字节签名
local OFFSETS = __OFFSETS__
local TARGET = "__TARGET__"
local KNOWN = __KNOWN__

local function log(message)
    local f = io.open(LOG_FILE, "ab")
    if f then
        f:write(os.date("%Y-%m-%d %H:%M:%S "), "[RS-422 Railgun Ultra ", MOD_VERSION, "/",
                LABEL, "] ", tostring(message), "\n")
        f:close()
    end
    print("[RS-422 Railgun Ultra] " .. tostring(message))
end

local function slurp(handle)
    -- Lua 5.1 wants "*a"; 5.3+ only accepts "a".
    local ok, body = pcall(handle.read, handle, "a")
    if ok and body then return body end
    handle:seek("set", 0)
    return handle:read("*a")
end

local function hex(s)
    return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

local function signature(buf)
    local out = {}
    for i = 1, #OFFSETS do
        out[i] = string.format("%02x", buf:byte(OFFSETS[i] + 1))
    end
    return table.concat(out)
end

local f = io.open(TARGET_FILE, "rb")
if not f then
    log("找不到 " .. TARGET_FILE .. "，跳过（本 mod 只适用于 Steam 版数据目录）")
    return
end
local data = slurp(f)
f:close()

if #data ~= EXPECT_SIZE then
    log("文件大小 " .. #data .. " 与预期 " .. EXPECT_SIZE .. " 不符，游戏可能已更新，跳过")
    return
end
if hex(data:sub(1, 32)) ~= EXPECT_EPK then
    log("密封头与预期不符，游戏可能已更新，跳过")
    return
end

local sig = signature(data)
if sig == TARGET then
    log("已经是 " .. LABEL .. "，无需改动")
    return
end

local known = false
for i = 1, #KNOWN do
    if KNOWN[i] == sig then known = true break end
end
if not known then
    log("数据表与预期状态不符（游戏已更新，或装了别的改动），跳过以免写坏。sig="
        .. sig:sub(1, 32))
    return
end

local bytes = {}
for i = 1, #OFFSETS do
    bytes[i] = tonumber(TARGET:sub(i * 2 - 1, i * 2), 16)
end

local parts, prev = {}, 1
for i = 1, #OFFSETS do
    local at = OFFSETS[i] + 1
    parts[#parts + 1] = data:sub(prev, at - 1)
    parts[#parts + 1] = string.char(bytes[i])
    prev = at + 1
end
parts[#parts + 1] = data:sub(prev)
local rebuilt = table.concat(parts)

local w = io.open(TARGET_FILE, "wb")
if not w then log("无法写入 " .. TARGET_FILE .. "（权限？）") return end
w:write(rebuilt)
w:close()

local check = io.open(TARGET_FILE, "rb")
local back = check and slurp(check)
if check then check:close() end
if back ~= rebuilt then
    log("写入校验失败，文件可能已损坏；请重新校验游戏文件或还原 dl_bin")
    return
end

log("已写入 " .. LABEL .. "；重启游戏后生效")
'''


def build_archive(module: str, source: bytes) -> bytes:
    return build_lua_bundle(module, source)


def lua_source(key, label, baked) -> str:
    offsets = baked["union"]
    known = ", ".join('"%s"' % s for s in baked["states"].values())
    return (LUA_TEMPLATE
            .replace("__MODULE__", MODULE_PREFIX + key)
            .replace("__LABEL__", label)
            .replace("__VERSION__", VERSION)
            .replace("__SIZE__", str(len(SRC.read_bytes())))
            .replace("__EPK__", SRC.read_bytes()[:32].hex())
            .replace("__OFFSETS__", "{ " + ", ".join(str(o) for o in offsets) + " }")
            .replace("__TARGET__", baked["variants"][label]["sig"])
            .replace("__KNOWN__", "{ " + known + " }"))


def manifest() -> dict:
    path = ROOT / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    name = "%s %s" % (BASE_NAME, VERSION)
    if data.get("Name") != name:
        data["Name"] = name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not data.get("Guid"):
        raise SystemExit("manifest.json 缺少 Guid")
    if len(data.get("Options", [])) != len(OPTIONS):
        raise SystemExit("manifest.json 的选项数与 OPTIONS 不一致")
    for key, label, _ in OPTIONS:
        opt = next((o for o in data["Options"] if ("options/" + key) in o["Include"]), None)
        if opt is None:
            raise SystemExit("manifest.json 缺少选项 " + label)
        for rel in [opt["Image"], data["IconPath"]]:
            if not (ROOT / rel).is_file():
                raise SystemExit("缺少图片 " + rel)
    return data


def main():
    import make_icons
    make_icons.main()

    baked = json.loads((ROOT / "build" / "lua_patches.json").read_text(encoding="utf-8"))
    man = manifest()
    DIST.mkdir(exist_ok=True)
    target = DIST / ZIP_NAME

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=2))
        z.write(ROOT / "thumbnail.png", "thumbnail.png")
        z.write(ROOT / "README.md", "README.md")
        for key, label, _ in OPTIONS:
            if label not in baked["variants"]:
                raise SystemExit("补丁表里没有 %s，先跑 bake_lua_patch.py" % label)
            source = lua_source(key, label, baked).encode("utf-8")
            archive = build_archive(MODULE_PREFIX + key, source)
            if read_lua_bundle(archive)[1] != source:
                raise SystemExit("选项 %s 的归档读回来不是预期 lua" % key)
            base = "options/%s/%s" % (key, BUNDLE)
            z.writestr(base + ".patch_0", archive)
            z.writestr(base + ".patch_0.gpu_resources", b"")
            z.writestr(base + ".patch_0.stream", b"")
            z.write(ROOT / "images" / (key + ".png"), "images/%s.png" % key)
            print(f"{label:<8} lua={len(source):,}B archive={len(archive):,}B")

    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:
            raise SystemExit("ZIP 完整性校验失败")
        names = z.namelist()
        for key, _, _ in OPTIONS:
            if "options/%s/%s.patch_0" % (key, BUNDLE) not in names:
                raise SystemExit("ZIP 缺少选项归档 " + key)
    print(f"zip: {target}  {target.stat().st_size:,}B  files={len(names)}")


if __name__ == "__main__":
    main()
