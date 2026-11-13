-- HD2-Addon: mods/codex/sentry_ammo_mdl_adapter
-- MDL3 入口：直接 require 弹药核心。不含加载器、不扫描归档、不代管其他加载器。
local RESOURCE = 'mods/codex/sentry_ammo_overhead'
local MAX_ERRORS = 20

local module, enabled, errors = nil, false, 0

local definition = {
    name = '炮塔头顶弹药显示',
    version = '1.4.6',
    description = '弹药核心由 MDL3 直接挂载，设置走 MDL3 菜单。',
}

local function say(text)
    if type(print) == 'function' then
        pcall(print, '[Sentry Ammo] '..tostring(text))
    end
end

-- 每次取一份干净的实例：停用后再启用时不会沿用上次的 world/snapshot。
local function acquire()
    pcall(function() package.loaded[RESOURCE] = nil end)
    local ok, value = pcall(require, RESOURCE)
    if not ok then return nil, 'require failed: '..tostring(value) end
    if type(value) ~= 'table' then return nil, 'resource returned '..type(value) end
    if type(value.update) ~= 'function' then return nil, 'core has no update' end
    return value
end

function definition.on_enable()
    if enabled then return end
    local value, why = acquire()
    if not value then
        definition.status = 'error'
        error('Sentry Ammo: '..tostring(why), 0)
    end
    module, enabled, errors = value, true, 0
    definition.status = 'running'
    say('core '..tostring(value.version)..' attached through MDL3')
end

function definition.on_update(_, dt)
    if not enabled or not module then return end
    local ok, err = pcall(module.update, dt)
    if ok then return end
    errors = errors + 1
    if errors <= 3 then say('update error: '..tostring(err)) end
    if errors < MAX_ERRORS then return end
    -- 连续报错就自己停掉，别拖累 MDL3 的其他插件。
    enabled = false
    local failed = module
    module = nil
    pcall(failed.shutdown)
    definition.status = 'stopped'
    say('stopped after '..errors..' update errors')
end

function definition.on_disable()
    local current = module
    module, enabled = nil, false
    if not current then return end
    local ok, err = pcall(current.shutdown)
    definition.status = ok and 'stopped' or 'error'
    if not ok then say('shutdown error: '..tostring(err)) end
end

return definition
