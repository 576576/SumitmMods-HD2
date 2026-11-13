#!/usr/bin/env python3
"""从原始核心生成「自带 ot」版，并把长度压回归档里的原槽位。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tools" / "source" / "e02b68d623c30acd.orig.lua"
CORE = ROOT / "core" / "e02b68d623c30acd.lua"
SLOT = 204_800          # 归档里这一行的总长度（现在由我们重排 TOC，可自行放大）
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
    local ok,file=pcall(G.io.open,dir..'SentryAmmoOverhead.log','w')
    return ok and file or nil
end)()
local t,f=0,0
return {
engine=S,
time=function() return t end,
frame=function() return f end,
clock=function() return clock() end,
risky=function(_,fn,...) return pcall(fn,...) end,
log=function(text,...)
    if logfile then
        local ok,line=pcall(G.string.format,text,...)
        pcall(logfile.write,logfile,ok and line or tostring(text),'\n')
        pcall(logfile.flush,logfile)
    elseif type(G.print)=='function' then
        pcall(G.print,'[Sentry Ammo]',text,...)
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
    return update_core(dt)
end
return module
'''

# 原版清单里 HMG 阵地的 id 少了一个前导 0，永远匹配不上；先修这个再谈别的。
BROKEN_MANNED_ID = "'e977c49db7604f9'"
FIXED_MANNED_ID = "'0e977c49db7604f9'"

# 三类单位共用同族兄弟的字段布局：毒气/电磁迫击与普通迫击同型，榴弹兵墙与 HMG 阵地同型。
# 模型与资源名补进清单，schema 直接克隆兄弟那条，避免手写几十个字段哈希。
ALIASES = (
    ("0e977c49db7604f9", "29ae906ea485a7fa",
     "content/fac_helldivers/hellpod/expandable_cover/expandable_cover"),
    ("51a0812e3bce2d74", "299c0d3dfd2f0994",
     "content/fac_helldivers/hellpod/mortar_turret/mortar_turret_gas"),
    ("51a0812e3bce2d74", "b2053a1838092f8b",
     "content/fac_helldivers/hellpod/mortar_turret/mortar_turret_staticfield"),
)


GROUPS = r'''
local GROUPS={
    ammo={'37cde43876ba26bb','61d9dda22a59e9bc','cbfe1fc7158347f3','37079568dc86e9c6','820cc3bafe962858'},
    mortar={'51a0812e3bce2d74','299c0d3dfd2f0994','b2053a1838092f8b'},
    laser={'56070f36cfffa8a8'},
    tesla={'74599e56f72f9d7e'},
    emplacement={'0e977c49db7604f9','2b11c9e4980ec479','29ae906ea485a7fa'},
}
local groups_applied=false
local function apply_groups()
    if groups_applied then return end
    local on=type(_G)=='table' and _G.SENTRY_AMMO_GROUPS or nil
    if type(on)~='table' then return end
    groups_applied=true
    for g,ids in pairs(GROUPS) do
        if not on[g] then
            for i=1,#ids do
                network_profile.models[ids[i]]=nil
                network_profile.model_resources[ids[i]]=nil
            end
        end
    end
end
'''

ALIAS_HEAD = """
do
    local P=network_profile
    local function alias(base,new,path)
        P.models[new]=true
        P.model_resources[new]=path
        for i=1,#P.types do
            local e=P.types[i]
            if e.model_hash==base then
                local c={}
                for k,v in pairs(e) do c[k]=v end
                c.model_hash=new
                P.types[#P.types+1]=c
                return
            end
        end
        error('alias base not found: '..base)
    end
"""

ALIAS_TAIL = "end\n"


def alias_block():
    lines = [ALIAS_HEAD]
    for base, new, path in ALIASES:
        lines.append("    alias('%s', '%s', '%s')\n" % (base, new, path))
    lines.append(ALIAS_TAIL)
    return "".join(lines)



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

    if BROKEN_MANNED_ID not in text:
        raise SystemExit("找不到 HMG 阵地那条残缺 id，清单可能已变")
    fixed = text.count(BROKEN_MANNED_ID)
    text = text.replace(BROKEN_MANNED_ID, FIXED_MANNED_ID)
    print("fixed manned id occurrences:", fixed)

    marker = "local network_profile = (function()"
    start = text.index(marker)
    close = text.index("\nend)()\n", start) + len("\nend)()\n")
    text = text[:close] + alias_block() + GROUPS + text[close:]
    print("aliased models:", len(ALIASES))
    print("group filter:", "apply_groups" in text)

    anchor = "\nreturn {\n    version=VERSION,"
    if text.count(anchor) != 1:
        raise SystemExit("找不到唯一的导出表")
    text = text.replace(anchor, "\nlocal module = {\n    version=VERSION,", 1)
    if not text.endswith("}\n"):
        raise SystemExit("文件末尾不是导出表的右括号")
    text = text.rstrip("\n") + "\n" + TAIL
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
