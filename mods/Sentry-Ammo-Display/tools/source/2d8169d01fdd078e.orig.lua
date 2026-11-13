-- HD2-Addon: mods/codex/sentry_ammo_mdl_adapter
-- MDL3 dev.32+ discovers declared addons but no longer dispatches foreign boots.
-- Start the existing independent sentry loader; do not replace its ammo logic.
local RESOURCE='mods/codex/sentry_ammo_loader'
local STATE_KEY='__SentryAmmoIndependentBoot'
local environment=getfenv(1)
local shared=_G
local base_rawget,base_rawset=rawget,rawset
local callbacks,owned_state,enabled=nil,nil,false
local definition={
    name='炮塔头顶弹药显示',
    version='1.4.6',
    description='沿用自带弹药加载器；通过 MDL3 注册启动及游戏内设置。',
}

-- MDL's _G is a proxy. Native rawset(proxy, key, value) would otherwise hide
-- the singleton on just that proxy and permit another loader to start twice.
-- Its ordinary indexing/assignment forwards to shared globals and lets MDL
-- track ownership. Keep inherited menu/require capabilities from our environment.
local host=setmetatable({}, {__index=environment})
host.rawget=function(target,key)
    if target==host then return shared[key] end
    return base_rawget(target,key)
end
host.rawset=function(target,key,value)
    if target==host then shared[key]=value;return target end
    return base_rawset(target,key,value)
end

function definition.on_enable()
    if enabled then return end
    local engine=host.stingray or host
    local app=type(engine)=='table' and engine.Application
    if type(app)~='table' or type(app.can_get)~='function' then
        error('Sentry Ammo: Lua resource lookup is unavailable',0)
    end
    local checked,available=pcall(app.can_get,'lua',RESOURCE)
    if not checked or available~=true then
        error('Sentry Ammo: independent loader resource is missing',0)
    end
    local loaded,start=pcall(require,RESOURCE)
    if not loaded or type(start)~='function' then
        error('Sentry Ammo: could not load independent runtime: '..tostring(start),0)
    end
    local started,value,reason=pcall(start,host)
    if not started then error('Sentry Ammo: startup failed: '..tostring(value),0) end
    if value==nil and reason=='already_started' then
        -- A standalone boot owns the callbacks. Never dispatch or stop them.
        definition.status='already_running'
        return
    end
    if type(value)~='table' then
        error('Sentry Ammo: independent startup refused: '..tostring(reason),0)
    end
    for _,name in ipairs({'init','update','render','shutdown'}) do
        if type(value[name])~='function' then
            error('Sentry Ammo: missing independent callback: '..name,0)
        end
    end
    callbacks,owned_state=value,shared[STATE_KEY]
    enabled=true
    local ok,err=pcall(callbacks.init)
    if not ok then
        -- MDL invokes on_disable during failed on_enable teardown.
        definition.status='error'
        error('Sentry Ammo: initialization failed: '..tostring(err),0)
    end
    definition.status='running'
end

function definition.on_update(_,dt)
    if not enabled or not callbacks then return end
    -- MDL's managed addon contract exposes on_update, not on_render. The
    -- existing renderer updates persistent world/UI objects after sampling.
    callbacks.update(dt)
    callbacks.render()
end

function definition.on_disable()
    if not enabled or not callbacks then return end
    enabled=false
    local current=callbacks
    callbacks=nil
    local ok,err=pcall(current.shutdown)
    if ok and type(owned_state)=='table' and owned_state.stopped==true
        and shared[STATE_KEY]==owned_state then
        shared[STATE_KEY]=nil
    end
    owned_state=nil
    definition.status=ok and 'stopped' or 'error'
    if not ok then error('Sentry Ammo: shutdown failed: '..tostring(err),0) end
end

return definition
