#!/usr/bin/env python3
"""从原始核心生成「自带 ot」版，并把长度压回归档里的原槽位。"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parents[1] / "toolchain"))
from hd2tool import idstring64  # noqa: E402
from net_type_scan import SCAN as PROBE  # noqa: E402
SOURCE = ROOT / "tools" / "source" / "e02b68d623c30acd.orig.lua"
CORE = ROOT / "core" / "e02b68d623c30acd.lua"
SLOT = 224_000          # 归档里这一行的总长度，与 patch_archive.GROW[CORE_ID] 同源
SOURCE_LIMIT = SLOT - 8

OT = r'''local ot=(function()
local G=_G
local S=G.stingray or G
local os_=G.os
local clock=os_ and os_.clock or function() return 0 end
local logfile=(function()
    if type(G.io)~='table' or type(G.io.open)~='function' then return nil end
    local dir=''
    if os_ and type(os_.getenv)=='function' then
        local ok,value=pcall(os_.getenv,'LOCALAPPDATA')
        if ok and type(value)=='string' then dir=value..'\\' end
    end
    local ok,file=pcall(G.io.open,dir..'SentryAmmoDisplay.log','w')
    return ok and file or nil
end)()
local t,f=0,0
local function stamp()
    if type(os_)~='table' or type(os_.date)~='function' then return '' end
    local ok,value=pcall(os_.date,'%H:%M:%S')
    return ok and type(value)=='string' and value..' ' or ''
end
return {
engine=S,
time=function() return t end,
frame=function() return f end,
clock=function() return clock() end,
risky=function(_,fn,...) return pcall(fn,...) end,
log=function(text,...)
    local ok,line=pcall(G.string.format,text,...)
    if not ok then
        -- 格式化失败时至少把参数拼上去，否则整行只剩模板。
        local n=select('#',...)
        if n<1 then line=tostring(text)
        else
            local parts={tostring(text)}
            for i=1,n do parts[i+1]=tostring((select(i,...))) end
            line=table.concat(parts,' ')
        end
    end
    if logfile then
        pcall(logfile.write,logfile,stamp(),line,'\n')
        pcall(logfile.flush,logfile)
    elseif type(G.print)=='function' then
        pcall(G.print,'[Sentry Ammo]',line)
    end
end,
options_menu=function()
    local m=G.ModOptionsMenu
    if type(m)=='table' and type(m.register_option)=='function' and type(m.get)=='function' then return m end
end,
advance=function(dt)
    if type(dt)=='number' and dt>0 and dt<10 then t=t+dt end
    f=f+1
end,
}
end)()
'''

TAIL = r'''
-- MDL3 每帧驱动：先推进 ot 的时钟，再跑原来的 update。
local update_core=module.update
module.update=function(dt)
    ot.advance(dt)
    apply_groups()
    pcall(scan_tick,ot.time())
    return update_core(dt)
end
return module
'''

# 核心在查表前会 gsub('^0+','') 去掉前导零，所以 models 的键与 binding 的 model_hash
# 都必须写成去零后的形态；带前导零的键永远查不到。
def norm_hash(value):
    return value.lower().lstrip("0") or "0"

# census 逐项遍历模型清单做 unit 查询，上游按自己 9 个模型把上限定在 10。
# 这里补了同族别名与广撒网绑定，运行时是 15 项，必须同步放宽，否则遍历到第 11 项就 break，
# 表现为 census complete=false / queries=0，一个单位都扫不到。
CENSUS_LIMIT = 32
CENSUS_LIMIT_OLD = "local MAX_TARGETS,MAX_RAW_UNITS,MAX_RESOURCES=128,128,10"
CENSUS_LIMIT_NEW = "local MAX_TARGETS,MAX_RAW_UNITS,MAX_RESOURCES=128,128,%d" % CENSUS_LIMIT

# 资源名换成本仓库的命名空间（与 patch_archive.py 的行标签 / 哈希替换必须一致）。
OLD_ADDON = "mods/codex/sentry_ammo_overhead"
NEW_ADDON = "mods/sumitm/sentry_ammo_display/core"
OLD_RESOURCE_PREFIX = "mods/codex/sentry_ammo/"
NEW_RESOURCE_PREFIX = "mods/sumitm/sentry_ammo_display/"

# 三类单位共用同族兄弟的字段布局：毒气/电磁迫击与普通迫击同型。
# 模型与资源名补进清单，schema 直接克隆兄弟那条，避免手写几十个字段哈希。
ALIASES = (
    ("51a0812e3bce2d74", "299c0d3dfd2f0994",
     "content/fac_helldivers/hellpod/mortar_turret/mortar_turret_gas"),
    ("51a0812e3bce2d74", "b2053a1838092f8b",
     "content/fac_helldivers/hellpod/mortar_turret/mortar_turret_staticfield"),
)

# 这几种单位的网络类型在 1.4.6 的清单里没有：榴弹兵墙挂的是独立的榴弹发射器单位，
# 武装补给舱与补给车共用一挺自行炮塔。清单里每条类型都试一遍，实际只会命中它自己的
# 那一条——game_object_is_type 与 schema 校验会挡掉其余候选项。
WIDE = (
    ("29ae906ea485a7fa",
     "content/fac_helldivers/hellpod/expandable_cover/expandable_cover"),
    ("2eecd0b1fa49630",
     "content/fac_helldivers/equipment/support_weapons/grenade_launcher/grenade_launcher"),
    ("73681ffd58fa1a90",
     "content/fac_helldivers/hellpod/ammo_rack_mounted_turret/ammo_rack_mounted_turret"),
    ("85c1edb038ec24e",
     "content/fac_helldivers/vehicles/frv/armaments/frv_mg/frv_mg"),
    ("444880f62ca7eae8",
     "content/fac_helldivers/vehicles/frv_heavy/armaments/"
     "frv_flamethrower/frv_flamethrower"),
)


GROUPS = r'''
local GROUPS={
    ammo={'37cde43876ba26bb','61d9dda22a59e9bc','cbfe1fc7158347f3','37079568dc86e9c6','820cc3bafe962858'},
    mortar={'51a0812e3bce2d74','299c0d3dfd2f0994','b2053a1838092f8b'},
    laser={'56070f36cfffa8a8'},
    tesla={'74599e56f72f9d7e'},
    emplacement={'e977c49db7604f9','2b11c9e4980ec479','29ae906ea485a7fa','2eecd0b1fa49630'},
    supply={'73681ffd58fa1a90'},
    mounted={'85c1edb038ec24e','444880f62ca7eae8'},
}
-- 关掉的组连绑定一起摘掉：绑定校验要求每条 binding 的 model 都在 models 里，
-- 只删 models 会让整个 profile 判为无效，所有炮塔一起不显示。
local groups_applied=false
local function apply_groups()
    if groups_applied then return end
    local on=type(_G)=='table' and _G.SENTRY_AMMO_GROUPS or nil
    if type(on)~='table' then return end
    groups_applied=true
    local live=false
    for g,ids in pairs(GROUPS) do
        if on[g] then
            live=true
        else
            for i=1,#ids do
                network_profile.models[ids[i]]=nil
                network_profile.model_resources[ids[i]]=nil
            end
        end
    end
    if not live then return end
    local keep,n={},0
    for i=1,#network_profile.bindings do
        local b=network_profile.bindings[i]
        if network_profile.models[b.model_hash] then n=n+1;keep[n]=b end
    end
    network_profile.bindings=keep
end
'''

# 模型→类型映射在 profile.bindings 里，types 只有类型 schema、不带 model_hash。
# 同一模型可能对应多条 binding（多个候选 schema），必须全部克隆，只留第一条会少掉观测路径。
# 找不到基准只跳过：单条缺失不该让整个插件在 MDL3 里加载失败。
ALIAS_HEAD = """
do
    local P=network_profile
    local function alias(base,new,path)
        -- models 的键必须是去前导零的形态：运行时的绑定校验会先把 model_hash
        -- 去掉前导零再查这张表，键带零就永远查不到，整块 profile 判为无效。
        new=new:gsub('^0+','')
        P.models[new]=true
        P.model_resources[new]=path
        local n=0
        for i=1,#P.bindings do
            local b=P.bindings[i]
            if b.model_hash==base then
                local c={}
                for k,v in pairs(b) do c[k]=v end
                c.model_hash=new
                P.bindings[#P.bindings+1]=c
                n=n+1
            end
        end
        if n==0 then
            ot.log('SENTRY-ALIAS no static binding for %s -> %s',base,path)
        end
    end
    local function bind_all(new,path)
        new=new:gsub('^0+','')
        P.models[new]=true
        P.model_resources[new]=path
        local have,n={},0
        for i=1,#P.bindings do have[P.bindings[i].type_hash]=true end
        local count=#P.bindings
        for i=1,count do
            local b=P.bindings[i]
            local t=b.type_hash
            if have[t] then
                have[t]=nil
                local c={}
                for k,v in pairs(b) do c[k]=v end
                c.model_hash=new
                P.bindings[#P.bindings+1]=c
                n=n+1
            end
        end
        ot.log('SENTRY-ALIAS wide %s -> %d types',path,n)
    end
"""

ALIAS_TAIL = "end\n"


def alias_block():
    lines = [ALIAS_HEAD]
    for base, new, path in ALIASES:
        lines.append("    alias('%s', '%s', '%s')\n" % (base, new, path))
    for new, path in WIDE:
        lines.append("    bind_all('%s', '%s')\n" % (new, path))
    lines.append(ALIAS_TAIL)
    return "".join(lines)


BINDING_ID_RE = re.compile(r"model_hash='([0-9a-f]{8,16})'")


def profile_models(text):
    """清单里的模型 id；census 的上限约束的就是这个数量。"""
    m = re.search(r"model_resources\s*=\s*\{(.*?)\n    \}", text, re.S) or \
        re.search(r"model_resources\s*=\s*\{(.*?)\}", text, re.S)
    return set(re.findall(r"\['([0-9a-f]{8,16})'\]", m.group(1))) if m else set()


def check_alias_bases(text):
    """别名基准必须真的在 profile 的 bindings 里，数据重生成换了 id 就在构建期报错。"""
    present = set(BINDING_ID_RE.findall(text))
    missing = [base for base, _, _ in ALIASES if base not in present]
    if missing:
        raise SystemExit("alias base missing from profile bindings: %s" % ", ".join(missing))
    taken = [new for _, new, _ in ALIASES if new in present]
    if taken:
        raise SystemExit("alias target already bound: %s" % ", ".join(taken))


def check_profile_links(text):
    """每条 binding 的 model_hash 去零后必须能命中 models 的键。

    核心解析 profile 时先用 gsub('^0+','') 归一化再查 models，键里带前导零就永远查不到，
    一条不中整表判 invalid_static_bindings，表现为所有炮塔都不显示。
    """
    keys = {norm_hash(k) for k in profile_models(text)}
    bad = sorted({m for m in BINDING_ID_RE.findall(text) if norm_hash(m) not in keys})
    if bad:
        raise SystemExit("binding 的 model_hash 不在 models 清单里：%s" % ", ".join(bad))
    print("profile links:", len(keys), "models /", len(BINDING_ID_RE.findall(text)), "bindings")


def check_wide_paths():
    """WIDE 里的 id 必须是那条 unit 路径的哈希，资源查询按名字找，对不上就什么都查不到。"""
    bad = ["%s <- %s" % (model, path) for model, path in WIDE
           if norm_hash(model) != norm_hash("%016x" % idstring64(path))]
    if bad:
        raise SystemExit("WIDE 的模型 id 与路径哈希不符：%s" % ", ".join(bad))


def check_id_forms():
    """模型 id 一律写去前导零的形态。

    models 的键与 binding 的 model_hash 都按这个形态写，运行时再 gsub('^0+','')
    归一化后查表；数据里带零虽然跑得起来，但对不上 profile 原有的键，排查成本很高。
    """
    bad = [model for model, _ in WIDE if model != norm_hash(model)]
    bad += [new for _, new, _ in ALIASES if new != norm_hash(new)]
    if bad:
        raise SystemExit("模型 id 必须去掉前导零：%s" % ", ".join(bad))


def strip_comments(source):
    """只留下代码骨架，用来确认删注释没有动到逻辑。"""
    out = []
    for line in source.splitlines():
        cut = line.split("--", 1)[0].rstrip()
        if cut:
            out.append(cut)
    return "\n".join(out)


def main():
    text = SOURCE.read_text(encoding="utf-8")
    if "local ot=..." not in text:
        raise SystemExit("核心已经改过：找不到 local ot=...")

    text = text.replace("local ot=...\n", OT, 1)

    version = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    stamp = re.search(r"^local VERSION='[^']*'$", text, re.M)
    if not stamp:
        raise SystemExit("找不到 VERSION 常量")
    text = text[:stamp.start()] + "local VERSION='%s'" % version + text[stamp.end():]

    if text.count(CENSUS_LIMIT_OLD) != 1:
        raise SystemExit("census 上限那行不是预期形态，清单结构可能已变")
    text = text.replace(CENSUS_LIMIT_OLD, CENSUS_LIMIT_NEW, 1)

    total = len(profile_models(text)) + len(ALIASES) + len(WIDE)
    if total > CENSUS_LIMIT:
        raise SystemExit("模型数 %d 超过 census 上限 %d" % (total, CENSUS_LIMIT))
    print("census models:", total, "limit:", CENSUS_LIMIT)

    check_alias_bases(text)
    check_wide_paths()
    check_id_forms()

    if OLD_ADDON not in text:
        raise SystemExit("核心里的 addon 名不是预期的 %s" % OLD_ADDON)
    text = text.replace(OLD_ADDON, NEW_ADDON)
    if OLD_RESOURCE_PREFIX in text:
        text = text.replace(OLD_RESOURCE_PREFIX, NEW_RESOURCE_PREFIX)
    if "mods/codex/" in text:
        raise SystemExit("核心 lua 里还有上游的命名空间")

    marker = "local network_profile = (function()"
    start = text.index(marker)
    close = text.index("\nend)()\n", start) + len("\nend)()\n")
    text = text[:close] + alias_block() + GROUPS + text[close:]
    print("aliased models:", len(ALIASES), "wide bindings:", len(WIDE))
    print("group filter:", "apply_groups" in text)
    check_profile_links(text)

    anchor = "\nreturn {\n    version=VERSION,"
    if text.count(anchor) != 1:
        raise SystemExit("找不到唯一的导出表")
    text = text.replace(anchor, "\nlocal module = {\n    version=VERSION,", 1)
    if not text.endswith("}\n"):
        raise SystemExit("文件末尾不是导出表的右括号")
    text = text.rstrip("\n") + "\n" + PROBE + TAIL
    expected = strip_comments(text)

    # 超长就从最长的一行纯注释开始删，代码骨架必须保持不变。
    lines = text.splitlines()
    excess = len(text.encode("utf-8")) - SOURCE_LIMIT
    if excess > 0:
        candidates = sorted(
            (len(line.encode("utf-8")), i) for i, line in enumerate(lines)
            if line.lstrip().startswith("--") and len(line.encode("utf-8")) > 20)
        removed = 0
        for size, index in candidates:
            if removed >= excess:
                break
            if not lines[index].lstrip().startswith("--"):
                continue
            removed += size + 1
            lines[index] = None
        lines = [line for line in lines if line is not None]
        print("trimmed comment bytes:", removed, "needed:", excess)

    text = "\n".join(lines) + "\n"
    if strip_comments(text) != expected:
        raise SystemExit("删注释时动到了代码，已中止")

    size = len(text.encode("utf-8"))
    if size > SOURCE_LIMIT:
        raise SystemExit("还是超长：%d > %d" % (size, SOURCE_LIMIT))
    text += "\n" * (SOURCE_LIMIT - size)
    CORE.write_text(text, encoding="utf-8", newline="")  # 保持 LF，长度必须逐字节对齐槽位
    print("core source: %d bytes, slot %d" % (SOURCE_LIMIT, SLOT))


if __name__ == "__main__":
    main()
