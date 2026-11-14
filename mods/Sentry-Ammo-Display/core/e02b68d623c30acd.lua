local SENTRY_ADDON='mods/sumitm/sentry_ammo_display/core'
local ot=(function()
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
local S=ot.engine
local App,Unit=S.Application or {},S.Unit or {}
local VERSION='1.5.37'
local create_network = (function()
-- Read-only ammunition/temperature and explicitly estimated Tesla lifetime provider.
-- factory(ot, profile, identity) -> reset(world), sample(unit, model_hash), forget(unit),
-- shutdown(), status(). No game memory access, setters, or spawning.
--
-- profile.types: dense list of schema entries. Legacy rounds_slot means ammo;
-- metric_kind=heat uses temperature and explicit FLOAT schema bounds. Static
-- heat thresholds normalize an explicitly estimated percentage, never a verified max.
-- lifetime uses a static duration from first verified observation, not actual birth.
-- profile.models: normalized .unit hash -> true/name, the sentry whitelist.
-- profile.steam_build/schema_sha256 are provenance, not proof of live success.
--
-- An existing bidirectional UnitSynchronizer mapping takes priority. An optional
-- identity provider may instead supply a same-frame inference from a proven
-- static model/type binding and a complete, unique vector match. That inference
-- is reported separately from a native mapping and still needs live validation.
-- Both paths require the current session, object type, and complete field schema
-- to match before reading raw rounds/temperature or updating a lifetime estimate.
-- No synchronizer creation/configuration is performed here.
--
-- Official contracts:
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/obj_stingray_UnitSynchronizer.html
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/obj_stingray_GameSession.html
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/ns_stingray_Network.html
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/tab_network__type__info.html
-- Field hashes: rounds_remaining=d7a5d63e, temperature=a5023836 (Murmur64 high32).
return function(ot, profile, identity)
    ot=type(ot)=='table' and ot or {}
    profile=type(profile)=='table' and profile or {}
    identity=type(identity)=='table' and identity or nil
    local S=type(ot.engine)=='table' and ot.engine or {}
    local App,Unit,V3=S.Application or {},S.Unit or {},S.Vector3 or {}
    local Net,GS,US=S.Network or {},S.GameSession or {},S.UnitSynchronizer or {}
    -- 诊断：把引擎各模块暴露的名字打一遍。网络类型名是混淆串，只能整串命中，
    -- 先确认有没有「读对象类型」这类接口，省得靠猜。
    if type(ot.log)=='function' then
        local function names(t)
            local list={}
            if type(t)=='table' then
                for key in pairs(t) do
                    if type(key)=='string' then list[#list+1]=key end
                end
            end
            table.sort(list)
            return table.concat(list,',')
        end
        local function dump(label,t)
            local ok,text=pcall(names,t)
            pcall(ot.log,'SENTRY-API %s: %s',label,ok and text or 'unreadable')
        end
        dump('engine',S)
        dump('Network',Net)
        dump('GameSession',GS)
        dump('UnitSynchronizer',US)
    end
    local METRICS={
        ammo={field='rounds_remaining',hash='d7a5d63e',source='network.rounds_remaining'},
        heat={field='temperature',hash='a5023836',source='network.temperature'},
        lifetime={source='estimated.deployment_remaining'}
    }
    local PER_SAMPLE,PER_FRAME,DISCOVERY_SECONDS=4,8,0.0005
    local CACHE_LIMIT=256
    local world,context,last_session=nil,nil,nil
    local lifetime_last_time=nil
    local cache,cache_count,schemas={},0,{}
    local reason='no_world'
    local call_detail=nil
    local sync_state='not_checked'
    local mapping_seen,positional_used,rounds_seen,temperature_seen=false,false,false,false
    local identity_problem,identity_ready=nil,false
    local count={sampled=0,returned=0,ammo_returned=0,heat_returned=0,lifetime_returned=0,type_checks=0,schema_checks=0,errors=0}
    local logged={}
    local missing={}
    local function finite(v)
        return type(v)=='number' and v==v and v>-math.huge and v<math.huge
    end
    local function integer(v,lo,hi)
        return finite(v) and v%1==0 and v>=lo and v<=hi
    end
    local function hash32(value)
        if type(value)=='userdata' then
            local ok,text=pcall(tostring,value)
            if not ok then return nil end
            value=text
        end
        if type(value)~='string' then return nil end
        local hex=value:lower():gsub('^0x','')
        if not hex:match('^%x+$') or #hex>8 then return nil end
        return string.rep('0',8-#hex)..hex
    end
    local function model_hash(value)
        if type(value)~='string' then return nil end
        local hex=value:match('#ID%[(%x+)%]')
        if not hex then return nil end
        hex=hex:lower():gsub('^0+','')
        return hex~='' and hex or '0'
    end
    local function required(name,fn)
        if type(fn)~='function' then missing[#missing+1]=name end
    end
    required('Application.worlds',App.worlds)
    required('Unit.alive',Unit.alive)
    required('Unit.world',Unit.world)
    required('Unit.debug_name',Unit.debug_name)
    required('Unit.world_position',Unit.world_position)
    required('Vector3.to_elements',V3.to_elements)
    required('Network.game_session',Net.game_session)
    required('Network.object_info',Net.object_info)
    required('GameSession.game_object_exists',GS.game_object_exists)
    required('GameSession.game_object_is_type',GS.game_object_is_type)
    required('GameSession.game_object_field',GS.game_object_field)
    required('ot.frame',ot.frame)
    required('ot.clock',ot.clock)
    if type(Net.INT)~='number' then missing[#missing+1]='Network.INT' end
    local sync_available=type(GS.unit_synchronizer)=='function'
        and type(US.unit_to_game_object_id)=='function'
        and type(US.game_object_id_to_unit)=='function'
    local types,types_by_hash,models={},{},profile.models
    local profile_problem=nil
    -- 校验只给出一个错误码时，日志里看不出是哪一条出的问题，跟着补一条定位信息。
    local profile_detail=nil
    if type(models)~='table' or type(profile.types)~='table'
        or #profile.types<1 or #profile.types>4096 then
        profile_problem='missing_network_profile'
        profile_detail=string.format('models=%s types=%s',type(models),type(profile.types))
    else
        local seen={}
        for i=1,#profile.types do
            local entry=profile.types[i]
            local id=type(entry)=='table' and hash32(entry.type_hash)
            profile_detail=string.format('types[%d] type_hash=%s',i,tostring(id))
            if not id or type(entry.call)~='string' or #entry.call<1
                or #entry.call>128 or seen[id] then
                profile_problem='invalid_network_profile'
                break
            end
            local metric_kind=entry.metric_kind or 'ammo'
            local metric=METRICS[metric_kind]
            local metric_slot=entry.metric_slot or entry.rounds_slot
            if not metric then profile_problem='unsupported_profile_metric';break end
            local fields={}
            if type(entry.fields)~='table' or #entry.fields<1 or #entry.fields>4096
                or (metric_kind~='lifetime' and not integer(metric_slot,1,#entry.fields)) then
                profile_problem='invalid_network_field_profile'
                break
            end
            local metric_count=0
            for slot=1,#entry.fields do
                fields[slot]=hash32(entry.fields[slot])
                if not fields[slot] then profile_problem='invalid_network_field_profile';break end
                if fields[slot]==metric.hash then metric_count=metric_count+1 end
            end
            if profile_problem then break end
            if metric_kind~='lifetime' and (metric_count~=1 or fields[metric_slot]~=metric.hash) then
                profile_problem=metric_kind=='ammo' and 'invalid_profile_rounds_slot' or 'invalid_profile_temperature_slot'
                break
            end
            if metric_kind=='heat' and (entry.schema_bits~=7 or entry.schema_min~=0
                or entry.schema_max~=4000 or not finite(entry.static_heat_threshold)
                or entry.static_heat_threshold<=0) then
                profile_problem='invalid_profile_temperature_bounds';break
            end
            if metric_kind=='lifetime' and (not finite(entry.estimated_lifetime_seconds)
                or entry.estimated_lifetime_seconds<=0 or entry.estimated_lifetime_seconds>86400
                or entry.metric_slot~=nil or entry.rounds_slot~=nil) then
                profile_problem='invalid_profile_lifetime_estimate';break
            end
            seen[id]=true
            local validated={call=entry.call,type_hash=id,fields=fields,rounds_slot=entry.rounds_slot,
                metric_kind=metric_kind,metric=metric,metric_slot=metric_slot,
                schema_bits=entry.schema_bits,schema_min=entry.schema_min,schema_max=entry.schema_max,
                static_heat_threshold=metric_kind=='heat' and entry.static_heat_threshold or nil,
                estimated_lifetime_seconds=metric_kind=='lifetime' and entry.estimated_lifetime_seconds or nil}
            types[#types+1]=validated
            types_by_hash[id]=validated
        end
    end
    -- Independently constrain inferred records to the static model/type pairs.
    -- Missing bindings disable this path only; a real native mapping can work
    -- without a coordinate-based association.
    local binding_types,binding_problem={},nil
    local binding_detail=nil
    if type(profile.bindings)~='table' or #profile.bindings<1 or #profile.bindings>256 then
        binding_problem='static_bindings_unavailable'
        binding_detail='bindings='..type(profile.bindings)
    else
        for index,binding in ipairs(profile.bindings) do
            local mh=type(binding)=='table' and binding.model_hash
            if type(mh)=='string' then
                mh=mh:lower():gsub('^0x','')
                if mh:match('^%x+$') and #mh<=16 then
                    mh=mh:gsub('^0+','')
                    if mh=='' then mh='0' end
                else mh=nil end
            else mh=nil end
            local id=type(binding)=='table' and hash32(binding.type_hash)
            local entry=id and types_by_hash[id]
            if not mh or type(models)~='table' or not models[mh] or not entry or binding.call~=entry.call
                or type(binding.fields)~='table' or #binding.fields~=#entry.fields then
                binding_problem='invalid_static_bindings'
                local why='model_missing'
                if not mh then why='model_hash_unparsable'
                elseif not entry then why='type_missing'
                elseif binding.call~=entry.call then why='call_mismatch'
                elseif type(binding.fields)~='table' or #binding.fields~=#entry.fields then why='fields_length'
                end
                binding_detail=string.format('bindings[%d] %s model=%s type=%s',
                    index,why,tostring(mh),tostring(id or binding.type_hash))
                break
            end
            for slot=1,#entry.fields do
                if hash32(binding.fields[slot])~=entry.fields[slot] then
                    binding_problem='static_binding_schema_mismatch'
                    break
                end
            end
            if binding_problem then break end
            binding_types[mh]=binding_types[mh] or {}
            binding_types[mh][id]=true
        end
    end
    local function note(key,message)
        if logged[key] then return end
        logged[key]=true
        if type(ot.log)=='function' then pcall(ot.log,'SENTRY-NETWORK %s',message) end
    end
    if profile_problem then note('profile',profile_problem..' '..tostring(profile_detail)) end
    if binding_problem then note('bindings',binding_problem..' '..tostring(binding_detail)) end
    -- Record the first native mapping calls in the loader's crash journal.
    -- A Lua pcall cannot catch native failures; a failed prior launch can skip
    -- this path on the next launch instead of repeating it.
    local function native(key,fn,...)
        if type(ot.risky)=='function' then
            return ot.risky('network.'..key,fn,...)
        end
        return pcall(fn,...)
    end
    local function call_problem(key,ok,value)
        local detail
        if not ok then
            detail='error: '..tostring(value):gsub('[%c]',' '):sub(1,240)
        elseif value==nil then
            detail='returned nil'
        else
            detail='returned '..type(value)..': '..tostring(value):gsub('[%c]',' '):sub(1,120)
        end
        call_detail=key..': '..detail
        note('call:'..key..':'..(ok and 'returned' or 'error'),call_detail)
        return detail
    end
    local function fail(value)
        reason=value
        return nil,value
    end
    local function forget(unit)
        if cache[unit]~=nil then cache[unit]=nil;cache_count=cache_count-1 end
    end
    local function forget_unknown(unit)
        local row=cache[unit]
        -- This preserves only an already-established estimate origin. It never
        -- authorizes a sample: every later return still validates current identity,
        -- session, existence, type, schema, clock and position again.
        if row and row.kind and row.kind.metric_kind=='lifetime'
            and finite(row.lifetime_started) and row.lifetime_type==row.kind.type_hash then return end
        forget(unit)
    end
    local function forget_unknown_rows()
        local units={}
        for unit in pairs(cache) do units[#units+1]=unit end
        for _,unit in ipairs(units) do forget_unknown(unit) end
    end
    local function clear_session()
        cache,schemas,cache_count={}, {},0
        mapping_seen,positional_used,rounds_seen,temperature_seen=false,false,false,false
        sync_state='not_checked'
        lifetime_last_time=nil
    end
    local function current_context()
        local ok,frame=pcall(ot.frame)
        if not ok or not integer(frame,0,9007199254740991) then
            return nil,'frame_unavailable'
        end
        if context and context.frame==frame then return context,context.problem end
        local tick_ok,tick=pcall(ot.clock)
        context={frame=frame,checks=0,discovery_elapsed=0}
        if not tick_ok or not finite(tick) then
            context.problem='clock_unavailable';return context,context.problem
        end
        local worlds_ok,worlds=pcall(App.worlds)
        local live=false
        if worlds_ok and type(worlds)=='table' then
            for _,candidate in pairs(worlds) do if candidate==world then live=true;break end end
        end
        if not live then
            if worlds_ok and type(worlds)=='table' then clear_session();last_session=nil end
            context.problem='world_unavailable';return context,context.problem
        end
        local session_ok,session=pcall(Net.game_session)
        if not session_ok or session==nil then
            forget_unknown_rows()
            context.problem='session_unavailable';return context,context.problem
        end
        if session~=last_session then
            clear_session()
            last_session=session
        end
        context.session=session
        if not sync_available then
            sync_state='api_missing'
            context.sync_problem='synchronizer_api_unavailable'
            return context
        end
        local sync_ok,sync=native('unit_synchronizer',GS.unit_synchronizer,session)
        if not sync_ok or sync==nil then
            sync_state=sync_ok and 'returned_nil' or 'call_error'
            call_problem('GameSession.unit_synchronizer',sync_ok,sync)
            context.sync_problem=sync_ok and 'synchronizer_returned_nil' or 'synchronizer_call_error'
            return context
        end
        sync_state='available'
        context.sync=sync
        return context
    end
    local function budget_available(ctx)
        if ctx.checks>=PER_FRAME or ctx.discovery_elapsed>=DISCOVERY_SECONDS then return false end
        local ok,tick=pcall(ot.clock)
        return ok and finite(tick),tick
    end
    local function charge_discovery(ctx,started)
        local ok,tick=pcall(ot.clock)
        if not ok or not finite(tick) or tick<started then
            ctx.discovery_elapsed=math.huge
        else
            ctx.discovery_elapsed=ctx.discovery_elapsed+(tick-started)
        end
    end
    local function schema(entry)
        local known=schemas[entry.type_hash]
        if known then return known,known.problem end
        count.schema_checks=count.schema_checks+1
        local ok,info=pcall(Net.object_info,entry.call)
        if not ok or type(info)~='table' or type(info.fields)~='table' then
            return nil,'schema_unavailable'
        end
        local result={}
        local total=0
        for slot,_ in pairs(info.fields) do
            total=total+1
            if total>#entry.fields or not integer(slot,1,#entry.fields) then
                result.problem='network_schema_shape_mismatch'
                break
            end
        end
        if not result.problem and total~=#entry.fields then
            result.problem='network_schema_shape_mismatch'
        end
        if not result.problem then
            for slot=1,#entry.fields do
                local field=info.fields[slot]
                if type(field)~='table' or hash32(field.id)~=entry.fields[slot] then
                    result.problem='network_schema_fields_mismatch'
                    break
                end
            end
        end
        if result.problem then
            schemas[entry.type_hash]=result
            return result,result.problem
        end
        if entry.metric_kind=='lifetime' then
            -- The complete object schema is validated, but no field is claimed
            -- to encode deployment time. The value below is explicitly estimated.
            schemas[entry.type_hash]=result
            return result
        end
        local found=info.fields[entry.metric_slot]
        local heat=entry.metric_kind=='heat'
        if not found then result.problem=heat and 'temperature_not_declared' or 'rounds_not_declared'
        elseif heat and type(Net.FLOAT)~='number' then result.problem='temperature_float_api_unavailable'
        elseif found.type~=(heat and Net.FLOAT or Net.INT) then
            result.problem=heat and 'temperature_not_float_schema' or 'rounds_not_integer_schema'
        elseif not integer(found.bits,1,53) then
            result.problem=heat and 'invalid_temperature_bits' or 'invalid_rounds_bits'
        elseif not finite(found.min) or not finite(found.max) or found.min>found.max then
            result.problem=heat and 'invalid_temperature_bounds' or 'invalid_rounds_bounds'
        elseif heat and (found.bits~=entry.schema_bits or found.min~=entry.schema_min or found.max~=entry.schema_max) then
            result.problem='temperature_schema_bounds_mismatch'
        else
            result.minimum=found.min
            result.maximum=found.max
            result.bits=found.bits
        end
        schemas[entry.type_hash]=result
        return result,result.problem
    end
    local function find_type(ctx,goid,row)
        if row.kind then
            local ok,match=pcall(GS.game_object_is_type,ctx.session,goid,row.kind.call)
            if not ok or type(match)~='boolean' then count.errors=count.errors+1;return nil,'type_check_failed' end
            if match==true then return row.kind end
            row.kind=nil
            row.lifetime_started=nil
            row.lifetime_type=nil
            row.next_type=1
        end
        if row.next_type>#types then return nil,'object_type_outside_profile' end
        local checked=0
        while row.next_type<=#types and checked<PER_SAMPLE do
            local allowed,started=budget_available(ctx)
            if not allowed then return nil,'type_discovery_budget' end
            local entry=types[row.next_type]
            row.next_type=row.next_type+1
            ctx.checks=ctx.checks+1
            checked=checked+1
            count.type_checks=count.type_checks+1
            local ok,match=pcall(GS.game_object_is_type,ctx.session,goid,entry.call)
            charge_discovery(ctx,started)
            if not ok or type(match)~='boolean' then
                -- An unreadable check is not evidence that the type differs.
                row.next_type=row.next_type-1
                count.errors=count.errors+1
                return nil,'type_check_failed'
            end
            if match==true then row.kind=entry;return entry end
        end
        return nil,row.next_type>#types and 'object_type_outside_profile' or 'discovering_object_type'
    end
    local api={}
    local map_logged={}
    function api.reset(next_world)
        world=next_world
        call_detail=nil
        context,last_session=nil,nil
        clear_session()
        logged={}
        identity_ready=false
        identity_problem=nil
        if identity and type(identity.reset)=='function' and type(identity.resolve)=='function' then
            local ok,err=pcall(identity.reset,next_world)
            identity_ready=ok
            if not ok then
                identity_problem='identity_reset_failed'
                call_problem('identity.reset',ok,err)
            end
        elseif identity then
            identity_problem='identity_api_unavailable'
        end
        reason=world and 'native_mapping_unverified' or 'no_world'
    end
    function api.sample(unit,expected_model)
        count.sampled=count.sampled+1
        if world==nil then return fail('no_world') end
        if profile_problem then return fail(profile_problem) end
        if #missing>0 then
            note('api','unavailable: '..table.concat(missing,', '))
            return fail('api_unavailable')
        end
        if type(unit)~='userdata' then return fail('invalid_unit') end
        local ctx,problem=current_context()
        if not ctx or problem then return fail(problem or 'context_unavailable') end
        local ok,alive=pcall(Unit.alive,unit)
        if not ok or type(alive)~='boolean' then forget_unknown(unit);return fail('unit_liveness_unavailable') end
        if alive==false then forget(unit);return fail('unit_destroyed') end
        local world_ok,unit_world=pcall(Unit.world,unit)
        if not world_ok or unit_world==nil then forget_unknown(unit);return fail('unit_world_unavailable') end
        if unit_world~=world then forget(unit);return fail('unit_world_mismatch') end
        local name_ok,name=pcall(Unit.debug_name,unit)
        local actual=name_ok and model_hash(name)
        if not actual then forget_unknown(unit);return fail('unit_model_unavailable') end
        if cache[unit] and cache[unit].model~=actual then forget(unit) end
        if not map_logged[actual] then
            map_logged[actual]=true
            local where='unavailable'
            local pose_ok,pose=pcall(Unit.world_position,unit,1)
            if pose_ok and pose~=nil then
                -- 注意：`a and pcall(...)` 只会返回一个值，必须分开写。
                local xyz_ok,x,y,z=pcall(V3.to_elements,pose)
                if xyz_ok and type(x)=='number' and type(y)=='number' and type(z)=='number' then
                    where=string.format('(%.2f,%.2f,%.2f)',x,y,z)
                end
            end
            pcall(ot.log,'SENTRY-MAP model=%s pos=%s',tostring(actual),where)
        end
        if not models[actual] or actual~=expected_model then
            if not models[actual] then
                note('model:'..tostring(actual),'unit model not in profile: '..tostring(actual))
            end
            forget_unknown(unit);return fail('unit_not_in_sentry_profile')
        end
        local goid,basis,fallback_kind
        local retained_identity,established_frame=false,nil
        local mapping_problem=ctx.sync_problem
        if ctx.sync~=nil then
            local id_ok,id=native('unit_to_game_object_id',US.unit_to_game_object_id,ctx.sync,unit)
            if not id_ok or not integer(id,0,2147483647) or id==32767 then
                call_problem('UnitSynchronizer.unit_to_game_object_id',id_ok,id)
                mapping_problem=not id_ok and 'unit_mapping_call_error' or 'unit_has_no_network_mapping'
            else
                local unit_ok,mapped=native('game_object_id_to_unit',US.game_object_id_to_unit,ctx.sync,id)
                if not unit_ok or mapped~=unit then
                    call_problem('UnitSynchronizer.game_object_id_to_unit',unit_ok,mapped)
                    mapping_problem=not unit_ok and 'reverse_mapping_call_error' or 'network_mapping_not_exact'
                else
                    goid,basis=id,'native_bidirectional_unit_mapping'
                end
            end
        end
        if goid==nil and identity_ready then
            local resolved_ok,resolved,resolution_problem=pcall(identity.resolve,unit,actual)
            if not resolved_ok then
                identity_problem='identity_resolve_failed'
                call_problem('identity.resolve',resolved_ok,resolved)
            elseif resolved==nil then
                identity_problem=type(resolution_problem)=='string' and resolution_problem or 'identity_unavailable'
            elseif type(resolved)~='table' then
                identity_problem='identity_record_invalid'
            elseif resolved.session~=ctx.session or resolved.frame~=ctx.frame then
                identity_problem='identity_context_mismatch'
            elseif resolved.identity_basis~='static_type_and_unique_vector_match' then
                identity_problem='identity_basis_invalid'
            elseif not integer(resolved.goid,0,2147483647) or resolved.goid==32767 then
                identity_problem='identity_object_invalid'
            else
                local entry=types_by_hash[hash32(resolved.type_hash)]
                if not entry or resolved.call~=entry.call then
                    identity_problem='identity_type_outside_profile'
                elseif binding_problem then
                    identity_problem=binding_problem
                elseif not binding_types[actual] or not binding_types[actual][entry.type_hash] then
                    identity_problem='identity_model_type_mismatch'
                else
                    goid,basis,fallback_kind=resolved.goid,resolved.identity_basis,entry
                    retained_identity=resolved.identity_retained==true
                    established_frame=resolved.identity_established_frame
                    identity_problem=nil
                end
            end
        end
        if goid==nil then
            forget_unknown(unit)
            return fail(identity_problem or mapping_problem or 'unit_has_no_network_mapping')
        end
        local exists_ok,exists=pcall(GS.game_object_exists,ctx.session,goid)
        if not exists_ok or type(exists)~='boolean' then
            forget_unknown(unit);return fail('network_object_existence_unavailable')
        end
        if exists==false then forget(unit);return fail('network_object_gone') end
        if basis=='native_bidirectional_unit_mapping' then mapping_seen=true end
        local row=cache[unit]
        if row==nil then
            if cache_count>=CACHE_LIMIT then return fail('unit_cache_full') end
            row={goid=goid,model=actual,next_type=1}
            cache[unit]=row
            cache_count=cache_count+1
        elseif row.goid~=goid or row.model~=actual then
            row={goid=goid,model=actual,next_type=1}
            cache[unit]=row
        end
        -- resolve() must supply current-frame validation of the original pair.
        -- Persistent identity never authorizes reusing ammo: rounds below are
        -- always read again from this frame's checked object.
        local kind,type_problem
        if fallback_kind then
            count.type_checks=count.type_checks+1
            local type_ok,match=pcall(GS.game_object_is_type,ctx.session,goid,fallback_kind.call)
            if not type_ok or type(match)~='boolean' then
                count.errors=count.errors+1
                return fail('type_check_failed')
            end
            if match~=true then forget(unit);return fail('identity_type_mismatch') end
            kind=fallback_kind
            if row.kind and row.kind.type_hash~=kind.type_hash then
                row.lifetime_started=nil;row.lifetime_type=nil
                row.aux_frame=nil;row.metric_log_frame=nil
            end
            row.kind=kind
        else
            kind,type_problem=find_type(ctx,goid,row)
            if not kind then return fail(type_problem) end
            identity_problem=nil
        end
        local declared,schema_problem=schema(kind)
        if not declared or schema_problem then return fail(schema_problem or 'schema_unavailable') end
        local heat=kind.metric_kind=='heat'
        local lifetime=kind.metric_kind=='lifetime'
        local value,lifetime_now
        if lifetime then
            local time_ok,now=pcall(function() return type(ot.time)=='function' and ot.time() or nil end)
            if not time_ok or not finite(now) or now<0 then
                return fail('lifetime_clock_unavailable')
            end
            if lifetime_last_time~=nil and now<lifetime_last_time then
                -- A backwards game clock invalidates every prior observation in
                -- this session, including other Tesla units not sampled yet.
                for _,cached in pairs(cache) do
                    cached.lifetime_started=nil;cached.lifetime_type=nil
                    cached.lifetime_log_frame=nil
                end
            end
            lifetime_last_time=now
            lifetime_now=now
        else
            local value_ok
            value_ok,value=pcall(GS.game_object_field,ctx.session,goid,kind.metric.field)
            if not value_ok then
                count.errors=count.errors+1
                return fail(heat and 'temperature_read_failed' or 'rounds_read_failed')
            end
            local value_valid=finite(value)
            if not heat then value_valid=integer(value,0,2147483647) end
            if not value_valid or value<0 or value<declared.minimum or value>declared.maximum then
                return fail(heat and 'temperature_unavailable_or_invalid' or 'rounds_unavailable_or_invalid')
            end
        end
        local pos_ok,position=pcall(Unit.world_position,unit,1)
        if not pos_ok or position==nil then return fail('position_unavailable') end
        local xyz_ok,x,y,z=pcall(V3.to_elements,position)
        if not xyz_ok or not finite(x) or not finite(y) or not finite(z) then
            return fail('position_invalid')
        end
        if lifetime then
            if row.lifetime_started==nil or row.lifetime_type~=kind.type_hash then
                row.lifetime_started=lifetime_now
                row.lifetime_type=kind.type_hash
                row.lifetime_log_frame=nil
            end
            value=math.max(0,kind.estimated_lifetime_seconds-(lifetime_now-row.lifetime_started))
        end
        -- Diagnostic fields never alter the displayed raw rounds. Their own
        -- sample frame is recorded because these optional reads are throttled.
        if not lifetime and (not row.aux_frame or ctx.frame-row.aux_frame>=120) then
            row.aux_frame=ctx.frame
            row.aux={}
            local optional={ec64918b='magazines_remaining',['4a893e74']='chamber_empty',f6275c53='is_overheated'}
            for _,field_hash in ipairs(kind.fields) do
                local field_name=optional[field_hash]
                if field_name then
                    local extra_ok,extra=pcall(GS.game_object_field,ctx.session,goid,field_name)
                    if extra_ok and (type(extra)=='boolean' or finite(extra)) then row.aux[field_name]=extra end
                end
            end
        end
        if heat then
            temperature_seen=true;count.heat_returned=count.heat_returned+1
            -- Observed extrema are diagnostics only. They never become a
            -- capacity, ratio, forecast, or display maximum.
            row.temperature_min=math.min(row.temperature_min or value,value)
            row.temperature_max=math.max(row.temperature_max or value,value)
            if not row.metric_log_frame or ctx.frame-row.metric_log_frame>=120 then
                row.metric_log_frame=ctx.frame
                if type(ot.log)=='function' then
                    pcall(ot.log,'SENTRY-HEAT type=%s goid=%s temperature=%.3f observed_min=%.3f observed_max=%.3f static_threshold=%.3f metric_max_verified=false frame=%s',
                        kind.type_hash,tostring(goid),value,row.temperature_min,row.temperature_max,
                        kind.static_heat_threshold,tostring(ctx.frame))
                end
            end
        elseif lifetime then
            count.lifetime_returned=count.lifetime_returned+1
            if not row.lifetime_log_frame or ctx.frame-row.lifetime_log_frame>=120 then
                row.lifetime_log_frame=ctx.frame
                if type(ot.log)=='function' then
                    pcall(ot.log,'SENTRY-LIFETIME type=%s goid=%s remaining_estimate=%.3f first_verified_observation=%.3f observed_age=%.3f static_lifetime=%.3f metric_max_verified=false basis=first_verified_observation frame=%s',
                        kind.type_hash,tostring(goid),value,row.lifetime_started,lifetime_now-row.lifetime_started,
                        kind.estimated_lifetime_seconds,tostring(ctx.frame))
                end
            end
        else rounds_seen=true;count.ammo_returned=count.ammo_returned+1 end
        if basis=='static_type_and_unique_vector_match' then positional_used=true end
        count.returned=count.returned+1
        reason=lifetime and 'estimated_lifetime_from_first_observation' or heat and 'sampled_network_temperature' or 'sampled_network_rounds'
        note('sample:'..kind.type_hash..':'..basis,(lifetime and 'estimated lifetime from first verified observation' or 'sampled declared '..kind.metric.field)..'; identity='..basis..'; type='..kind.type_hash)
        local result={verified=true,unit=unit,world=world,x=x,y=y,z=z,
            metric_kind=kind.metric_kind,metric_value=value,metric_value_verified=not lifetime,metric_max_verified=false,
            sampled_frame=ctx.frame,network_goid=goid,network_type=kind.type_hash,
            auxiliary_sample_frame=row.aux_frame,auxiliary=row.aux,
            source=kind.metric.source,schema_sha256=profile.schema_sha256,
            identity_basis=basis,identity_inferred=basis=='static_type_and_unique_vector_match',
            identity_retained=retained_identity,identity_established_frame=established_frame}
        if heat then
            result.temperature=value;result.static_heat_threshold=kind.static_heat_threshold
            result.metric_max=kind.static_heat_threshold
            result.metric_max_source='static_overheat_threshold'
            result.metric_max_sampled_frame=ctx.frame
            result.metric_estimated=true
            result.metric_estimate_basis='static_overheat_threshold'
        elseif lifetime then
            result.metric_max=kind.estimated_lifetime_seconds
            result.metric_max_source='static_lifetime'
            result.metric_max_sampled_frame=ctx.frame
            result.metric_estimated=true
            result.metric_estimate_basis='first_verified_observation'
            result.first_verified_observation_time=row.lifetime_started
        else result.rounds=value end
        return result
    end
    api.forget=forget
    function api.shutdown()
        world,context,last_session=nil,nil,nil
        clear_session()
        identity_ready=false
        identity_problem=nil
        if identity and type(identity.shutdown)=='function' then
            local ok,err=pcall(identity.shutdown)
            if not ok then
                identity_problem='identity_shutdown_failed'
                call_problem('identity.shutdown',ok,err)
            end
        end
        reason='shutdown'
    end
    function api.status()
        return {reason=reason,call_detail=call_detail,tracked=cache_count,types=#types,sampled=count.sampled,
            returned=count.returned,type_checks=count.type_checks,
            schema_checks=count.schema_checks,errors=count.errors,
            steam_build=profile.steam_build,schema_sha256=profile.schema_sha256,
            native_mapping_verified=mapping_seen,network_rounds_verified=rounds_seen,
            network_temperature_verified=temperature_seen,network_metric_verified=rounds_seen or temperature_seen,
            ammo_returned=count.ammo_returned,heat_returned=count.heat_returned,lifetime_returned=count.lifetime_returned,
            positional_mapping_used=positional_used,identity_reason=identity_problem,
            identity_inference_validated=false,
            synchronizer_state=sync_state,
            validation_reason=positional_used and 'vector_identity_inference_not_runtime_validated'
                or ((sync_state=='returned_nil' or sync_state=='call_error' or sync_state=='api_missing')
                and 'native_synchronizer_unavailable'
                or (not mapping_seen and 'native_mapping_not_runtime_verified')
                or (not rounds_seen and not temperature_seen and 'network_metric_not_runtime_verified'
                    or 'game_metric_behavior_not_validated')),
            game_validated=false}
    end
    return api
end


end)()
local create_identity = (function()
-- Read-only inferred Unit/network identity, using static model-to-type bindings.
-- factory(ot,profile) -> reset(world), update(targets), resolve(unit,model_hash),
-- shutdown(), status(). update must precede resolve in the same frame.
-- profile.bindings: {model_hash,type_hash,call,position_field,position_slot,fields}.
-- fields is the complete ordered ID32 list from this build's network_config.
-- position_field may be a verified generated ID32 call alias; never guess it.
-- targets: dense {unit,model_hash} array of ALL tracked sentry candidates.
--
-- This is inference, NOT a native pointer mapping. A match needs compatible
-- static type and a candidate network vector within 1 mm of the Unit root,
-- unique in BOTH directions. That vector's semantics are unverified. Negative
-- classifications are rechecked after a ten-second cooldown with a throughput-sized age limit
-- capped at 120 game seconds. Complete classification is required to ADMIT a
-- new identity. Established Unit/GOID pairs survive discovery work only while
-- their original object row, current type/vector, and complete Unit census
-- remain consistent and unique. No previous ammo value is stored here.
-- Only Unit handles and numerical/cache data persist; no Vector3 is retained.
-- No setters, RPCs, memory access, spawning, or nearest-distance fallback.
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/obj_stingray_GameSession.html
-- peers(session) -> string[]; objects_owned_by(session,peer) -> integer[].
return function(ot,profile)
    local S=ot.engine
    local App,Unit,V3,Net,GS=S.Application or {},S.Unit or {},S.Vector3 or {},S.Network or {},S.GameSession or {}
    local MAX_CHECKS,SECONDS,MAX_POSITIVES=256,0.0005,128
    local RECHECK_INTERVAL,BASE_AGE,MAX_AGE,EPS2=10,12,120,0.001^2
    -- 结构类单位（补给舱、载具）的 unit 原点与网络对象坐标不重合，精确到毫米会全部落空。
    local NEAR2=2.0^2
    -- 补给车的网络对象坐标在落点生成后就不再跟随载具，z 停在落点上方二十几米，
    -- 只有 x/y 还贴着车。放宽只给车体模型本身：这个类型经 bind_all 也挂在别的
    -- 模型上，按类型放宽会让它们一起去抢同一个对象。
    -- 三项合成一张表，只占一个 upvalue：下面那个大函数的 60 个名额已经很紧。
    local VEHICLE={model='9b2140378640432e',type='6d745627',horiz2=8.0^2}
    -- 该类型是否落在这个 unit 与这个对象之间。
    local function within(type_hash,u,c)
        local dx,dy=u.x-c.x,u.y-c.y
        if type_hash==VEHICLE.type and u.model_hash==VEHICLE.model then
            return dx*dx+dy*dy<=VEHICLE.horiz2
        end
        local dz=u.z-c.z
        return dx*dx+dy*dy+dz*dz<=EPS2
    end
    local world,session,rows,schemas=nil,nil,{},{}
    local pending,refresh,ph,rh={},{},1,1
    local resolved,resolved_frame={},nil
    local established,retired_units={},{}
    local unresolved_log_time=nil
    local discovery_complete,discovery_reason,retained_count,new_count=false,'awaiting_discovery',0,0
    -- 字段表与运行时 schema 不符的类型：只停用这一条，不参与候选。
    -- 若把它计入 candidate_problem，discovery 永远不 complete，所有型号一起不显示。
    local schema_bad={}
    local validation_time,validation_frame=nil,nil
    local reason,problem='no_world',nil
    local problem_detail=nil
    local budget_frame,checks,spent=nil,0,0
    local objects,unclassified,expired,bound,reclassified=0,0,0,0,0
    local candidate_count,nearest_distance,nearest_type,distance_frame=0,nil,nil,nil
    local nearest_unit_position,nearest_network_vector=nil,nil
    local types,models,by_type={},{},{}
    local all_types,active_types,scope_signature={}, {},nil
    local diag_logged={}
    local vehicle_logged={}
    local age_limit,required_age,classification_rate=BASE_AGE,BASE_AGE,nil
    local rate_started,rate_last_time,rate_checks,sweep_seconds,oldest_negative_age=nil,nil,0,nil,0
    local throughput_limited=false
    local target_members={}
    local positive_verifications,known_positive_count=0,0
    local function finite(v) return type(v)=='number' and v==v and v>-math.huge and v<math.huge end
    local function integer(v,lo,hi) return finite(v) and v%1==0 and v>=lo and v<=hi end
    local function hex(v,width,pad)
        if type(v)~='string' then return nil end
        v=v:lower():gsub('^0x','')
        if not v:match('^%x+$') or #v>width then return nil end
        if pad then return string.rep('0',width-#v)..v end
        v=v:gsub('^0+','');return v~='' and v or '0'
    end
    local function call(fn,...)
        if type(fn)~='function' then return false,nil end
        return pcall(fn,...)
    end
    local function fail(why)
        reason=why;resolved,resolved_frame,bound={},nil,0
        return false,why
    end
    local function clear()
        rows,schemas,pending,refresh,ph,rh={},{},{},{},1,1
        resolved,resolved_frame={},nil
        established,retired_units={},{}
        discovery_complete,discovery_reason,retained_count,new_count=false,'awaiting_discovery',0,0
        validation_time,validation_frame=nil,nil
        objects,unclassified,expired,bound=0,0,0,0
        candidate_count,nearest_distance,nearest_type,distance_frame=0,nil,nil,nil
        nearest_unit_position,nearest_network_vector=nil,nil
        budget_frame,checks,spent=nil,0,0
        age_limit,required_age,classification_rate=BASE_AGE,BASE_AGE,nil
        rate_started,rate_last_time,rate_checks,sweep_seconds,oldest_negative_age=nil,nil,0,nil,0
        throughput_limited=false
        target_members={}
        positive_verifications,known_positive_count=0,0
    end
    local required={App.worlds,Unit.alive,Unit.world,Unit.debug_name,Unit.world_position,V3.to_elements,
        Net.game_session,Net.peer_id,Net.object_info,GS.peers,GS.objects_owned_by,
        GS.game_object_exists,GS.game_object_is_type,GS.game_object_field,ot.frame,ot.clock,ot.time}
    -- Count explicitly: nil holes in an API list must not shorten validation.
    for i=1,17 do if type(required[i])~='function' then problem='api_unavailable' end end
    if type(Net.VECTOR3)~='number' then problem='vector3_metadata_unavailable' end
    local bindings=type(profile)=='table' and profile.bindings
    if type(bindings)~='table' or #bindings<1 or #bindings>256 then
        problem='static_bindings_unavailable'
    else
        for index,b in ipairs(bindings) do
            local mh=type(b)=='table' and hex(b.model_hash,16,false)
            local th=type(b)=='table' and hex(b.type_hash,8,true)
            -- 位置字段可以整条缺省：这类 schema 里根本没有 c{i(uTh>，只能靠唯一对象认领。
            local has_position=b.position_field~=nil or b.position_slot~=nil
            if not mh or not th or type(b.call)~='string' or #b.call<1 or #b.call>128
                or type(b.fields)~='table' or #b.fields<1 or #b.fields>512
                or (has_position and (type(b.position_field)~='string'
                    or #b.position_field<1 or #b.position_field>128
                    or not integer(b.position_slot,1,#b.fields))) then
                problem='invalid_static_bindings'
                problem_detail=string.format('bindings[%d] model=%s type=%s',index,tostring(mh),tostring(th))
                break
            end
            local fields={}
            for i=1,#b.fields do fields[i]=hex(b.fields[i],8,true) end
            local fields_valid=true
            for i=1,#b.fields do if not fields[i] then fields_valid=false end end
            if not fields_valid then problem='invalid_static_fields';break end
            if has_position then
                local position_hash=hex(b.position_field_hash,8,true)
                if not position_hash or fields[b.position_slot]~=position_hash then
                    problem='invalid_static_fields';break
                end
            end
            local t=by_type[th]
            if t then
                if t.call~=b.call or t.position_field~=b.position_field or t.position_slot~=b.position_slot
                    or #t.fields~=#fields then problem='conflicting_static_bindings';break end
                for i=1,#fields do if t.fields[i]~=fields[i] then problem='conflicting_static_bindings' end end
            else
                t={type_hash=th,call=b.call,position_field=b.position_field,
                    position_slot=b.position_slot,fields=fields,models={}}
                by_type[th]=t;types[#types+1]=t
            end
            t.models[mh]=true;models[mh]=true
        end
    end
    all_types=types
    if problem and type(ot.log)=='function' then
        pcall(ot.log,'SENTRY-IDENTITY %s %s',problem,tostring(problem_detail))
    end
    local function live_world()
        local ok,list=call(App.worlds)
        if not ok or type(list)~='table' then return nil end
        for _,w in pairs(list) do if w==world then return true end end
        return false
    end
    local function owned()
        local ok,peer=call(Net.peer_id)
        local good,peers=call(GS.peers,session)
        if not ok or type(peer)~='string' or peer=='' or not good or type(peers)~='table' then
            return nil,'peer_list_unavailable'
        end
        local owners={[peer]=true}
        for _,p in pairs(peers) do
            if type(p)~='string' or p=='' then return nil,'invalid_peer_list' end
            owners[p]=true
        end
        local ids,n,owner_count={},0,0
        for p in pairs(owners) do
            owner_count=owner_count+1
            if owner_count>64 then return nil,'owner_limit' end
            local readable,list=call(GS.objects_owned_by,session,p)
            if not readable or type(list)~='table' then return nil,'owned_list_unavailable' end
            for _,id in pairs(list) do
                if not integer(id,0,2147483647) then return nil,'invalid_owned_id' end
                if not ids[id] then n=n+1;ids[id]=true end
                if n>32768 then return nil,'object_limit' end
            end
        end
        return ids
    end
    local function enqueue(row,queue)
        if row.queued==queue then return end
        row.queued=queue;row.next_type=row.next_type or 1
        row.job=(row.job or 0)+1
        local q=queue=='pending' and pending or refresh
        q[#q+1]={row=row,job=row.job}
    end
    local function reconcile(ids,now)
        for id in pairs(rows) do if not ids[id] then rows[id]=nil end end
        for id in pairs(ids) do
            local row=rows[id]
            if not row then row={id=id,next_type=1,seen_at=now};rows[id]=row;enqueue(row,'pending')
            end
        end
    end
    local function set_scope(targets)
        local wanted,keys,next_members,new_target={},{},{},false
        for _,target in ipairs(targets) do
            local model=type(target)=='table' and hex(target.model_hash,16,false)
            local unit=type(target)=='table' and target.unit
            if model and models[model] and unit then
                next_members[unit]=model
                if target_members[unit]~=model then new_target=true end
            end
            if model and models[model] and not wanted[model] then
                wanted[model]=true;keys[#keys+1]=model
            end
        end
        table.sort(keys)
        local signature=table.concat(keys,',')
        target_members=next_members
        if signature==scope_signature and not new_target then return #types>0 end
        local selected,next_set,added={},{},false
        for _,t in ipairs(all_types) do
            for model in pairs(t.models) do
                if wanted[model] then
                    selected[#selected+1]=t;next_set[t.type_hash]=true
                    if not active_types[t.type_hash] then added=true end
                    break
                end
            end
        end
        types,active_types,scope_signature=selected,next_set,signature
        for _,row in pairs(rows) do
            row.next_type,row.started=1,nil
            if (row.kind and not next_set[row.kind.type_hash]) or (row.kind==false and (added or new_target)) then
                row.kind=nil;row.queued=nil;enqueue(row,'pending')
            end
        end
        return #types>0
    end
    local function positive_rows()
        local list={}
        for _,row in pairs(rows) do
            if row.kind then
                list[#list+1]=row
                if #list>MAX_POSITIVES then return nil,'positive_candidate_limit' end
            end
        end
        known_positive_count=#list
        return list
    end
    local function verify_positive(row,frame)
        if positive_verifications>=MAX_POSITIVES then return false,'positive_verification_limit' end
        positive_verifications=positive_verifications+1
        row.checked_frame=nil;row.verify_attempt_frame=frame
        local ok,exists=call(GS.game_object_exists,session,row.id)
        if not ok or type(exists)~='boolean' then return false,'candidate_exists_unavailable' end
        if exists==false then rows[row.id]=nil;return false,'candidate_object_disappeared' end
        -- Required current-frame evidence is bounded by candidate count, not
        -- a sub-millisecond timer. A 1 ms os.clock tick must not discard every
        -- label midway through five otherwise valid turret verifications.
        local checked,match=call(GS.game_object_is_type,session,row.id,row.kind.call)
        if not checked or type(match)~='boolean' then return false,'type_check_unavailable' end
        if not match then
            row.kind=nil;row.next_type=1;enqueue(row,'pending')
            return false,'candidate_type_changed'
        end
        row.checked_frame=frame
        return true
    end
    local function check_type(id,t)
        if checks>=MAX_CHECKS or spent>=SECONDS then return nil,'classification_budget' end
        local before_ok,before=call(ot.clock)
        if not before_ok or not finite(before) then return nil,'clock_unavailable' end
        checks=checks+1
        local ok,result=call(GS.game_object_is_type,session,id,t.call)
        local after_ok,after=call(ot.clock)
        if not after_ok or not finite(after) or after<before then spent=math.huge
        else spent=spent+after-before end
        if not ok or type(result)~='boolean' then return nil,'type_check_unavailable' end
        return result
    end
    local function classify(queue,head,name,frame,now)
        local stop=#queue
        while head<=stop do
            local ticket=queue[head]
            local row=ticket.row
            if rows[row.id]~=row or row.queued~=name or row.job~=ticket.job then head=head+1
            else
                -- FIFO completion times keep the refresh queue in due order.
                -- Waiting is deliberate idle time, not evidence of low CPU throughput.
                if name=='refresh' and not row.started and now<(row.refresh_due or 0) then return head end
                local t=types[row.next_type]
                local match,why=check_type(row.id,t)
                if match==nil then return head,why end
                row.started=row.started or now
                if match or row.next_type==#types then
                    row.kind=match and t or false;row.at=row.started;row.next_type,row.started=1,nil
                    row.queued=nil;head=head+1;reclassified=reclassified+1
                    if not match then row.refresh_due=now+RECHECK_INTERVAL;enqueue(row,'refresh') end
                    -- Jobs appended by a refresh completion wait for the next update.
                else row.next_type=row.next_type+1 end
            end
        end
        return head
    end
    local function observe_classification(now,work)
        local elapsed=rate_last_time and now-rate_last_time or 0
        rate_last_time=now
        if elapsed<0 or elapsed>1 then
            rate_started,rate_checks=0,0
            return
        end
        if work<=0 then return end
        -- Measure only updates that actually perform classification. The ten
        -- second refresh cooldown must not be mistaken for a slower scanner.
        rate_started=(rate_started or 0)+elapsed
        rate_checks=rate_checks+work
        if rate_started>=0.25 then
            local measured=rate_checks/rate_started
            if not classification_rate or measured<classification_rate then
                classification_rate=measured
            else classification_rate=classification_rate*0.8+measured*0.2 end
            rate_started,rate_checks=0,0
        end
    end
    local function schema(t)
        local saved=schemas[t.type_hash]
        if saved~=nil then return saved.valid,saved.reason end
        if not integer(t.position_slot,1,#t.fields) then
            schemas[t.type_hash]={valid=false,reason='no_position_field'}
            return false,'no_position_field'
        end
        local function detail(value) return tostring(value):gsub('[\r\n]',' '):sub(1,64) end
        local function bad(code)
            schemas[t.type_hash]={valid=false,reason=code}
            return false,code
        end
        local ok,info=call(Net.object_info,t.call)
        if not ok or type(info)~='table' or type(info.fields)~='table' then
            return false,'type_metadata_unavailable'
        end
        local count=0
        for index in pairs(info.fields) do
            count=count+1
            if not integer(index,1,#t.fields) then return bad('schema_field_shape_mismatch:index='..detail(index)) end
        end
        if count~=#t.fields then return bad('schema_field_count_mismatch:expected='..#t.fields..':actual='..count) end
        for i=1,#t.fields do
            local field=info.fields[i]
            local field_id=type(field)=='table' and field.id or nil
            if type(field_id)=='userdata' then
                local readable,text=pcall(tostring,field_id)
                field_id=readable and text or nil
            end
            -- Only an entire bare/0x hexadecimal ID is accepted. Do not extract
            -- digits from arbitrary userdata addresses or diagnostic strings.
            if type(field)~='table' or hex(field_id,8,true)~=t.fields[i] then
                return bad('schema_field_order_mismatch:slot='..i..':expected='..t.fields[i]..':actual='..detail(type(field)=='table' and field.id or field))
            end
        end
        if info.fields[t.position_slot].type~=Net.VECTOR3 then return bad('candidate_field_not_vector3:slot='..t.position_slot..':actual='..detail(info.fields[t.position_slot].type)) end
        schemas[t.type_hash]={valid=true};return true
    end
    local function xyz(value)
        local ok,x,y,z=call(V3.to_elements,value)
        if ok and finite(x) and finite(y) and finite(z) then return {x=x,y=y,z=z} end
    end
    local function targets_now(targets)
        local out,seen={},{}
        for i=1,#targets do
            local row=targets[i]
            local mh=type(row)=='table' and hex(row.model_hash,16,false)
            local unit=type(row)=='table' and row.unit
            if not unit or not mh then return nil,'invalid_target' end
            local ok,alive=call(Unit.alive,unit)
            if not ok then return nil,'target_alive_unavailable' end
            if alive==true and models[mh] and not seen[unit] then
                local wok,uw=call(Unit.world,unit)
                local nok,name=call(Unit.debug_name,unit)
                local actual=nok and type(name)=='string' and name:match('#ID%[(%x+)%]')
                if not wok or uw~=world or not actual or hex(actual,16,false)~=mh then return nil,'target_identity_changed' end
                local pok,p=call(Unit.world_position,unit,1)
                local pos=pok and xyz(p)
                if not pos then return nil,'target_position_unavailable' end
                pos.unit,pos.model_hash=unit,mh;out[#out+1]=pos;seen[unit]=true
            end
        end
        return out
    end
    local function complete(now)
        objects,unclassified,expired,oldest_negative_age=0,0,0,0
        local nonpositive,backwards=0,false
        for _,row in pairs(rows) do
            objects=objects+1
            if not row.kind then nonpositive=nonpositive+1 end
            if row.kind==nil then unclassified=unclassified+1
            elseif row.kind==false then
                oldest_negative_age=math.max(oldest_negative_age,now-row.at)
                if now<row.at then backwards=true end
            end
        end
        -- Allow the intentional ten-second cooldown plus time to cover every
        -- nonpositive ID against every currently relevant type. The full owned
        -- set must still be classified, and actual negative ages remain bounded.
        if nonpositive==0 then required_age,sweep_seconds=BASE_AGE,0
        elseif classification_rate and classification_rate>0 then
            sweep_seconds=nonpositive*#types/classification_rate
            required_age=BASE_AGE+1.5*sweep_seconds
        elseif classification_rate==0 then
            required_age,sweep_seconds=MAX_AGE+1,nil
        else required_age,sweep_seconds=BASE_AGE,nil end
        throughput_limited=required_age>MAX_AGE
        local desired=math.min(MAX_AGE,required_age)
        if desired>age_limit then age_limit=desired
        elseif unclassified==0 and oldest_negative_age<=desired then
            -- Do not shrink under evidence that was accepted using a longer
            -- window until the whole retained set is fresh enough for it.
            age_limit=desired
        end
        for _,row in pairs(rows) do
            if row.kind==false and (now-row.at>age_limit or now<row.at) then expired=expired+1 end
        end
        if backwards then return false,'game_time_reversed' end
        if unclassified>0 then return false,'classifying_new_objects' end
        if expired>0 then
            return false,throughput_limited and 'classification_throughput_below_limit' or 'negative_cache_expired'
        end
        return true
    end
    local function compact()
        local function tail(q,h)
            if h<128 then return q,h end
            local out={};for i=h,#q do out[#out+1]=q[i] end;return out,1
        end
        pending,ph=tail(pending,ph);refresh,rh=tail(refresh,rh)
    end
    local api={}
    function api.reset(next_world)
        world=next_world;session=nil;clear();scope_signature,active_types=nil,{}
        reason=world and 'awaiting_targets' or 'no_world'
    end
    function api.update(targets)
        resolved,resolved_frame,bound={},nil,0
        positive_verifications,known_positive_count=0,0
        retained_count,new_count,discovery_complete=0,0,false
        if problem then return fail(problem) end
        if not world then return fail('world_unavailable') end
        local world_live=live_world()
        if world_live~=true then
            if world_live==false then established,retired_units={},{} end
            return fail('world_unavailable')
        end
        if type(targets)~='table' or #targets>256 then
            return fail('invalid_targets')
        end
        local fok,frame=call(ot.frame);local tok,now=call(ot.time)
        if not fok or not integer(frame,0,9007199254740991) or not tok or not finite(now) then
            return fail('time_unavailable')
        end
        if (validation_time and now<validation_time) or (validation_frame and frame<validation_frame) then
            established,retired_units={},{};validation_time,validation_frame=now,frame
            return fail('identity_time_reversed')
        end
        if budget_frame~=frame then budget_frame,checks,spent=frame,0,0 end
        local sok,current=call(Net.game_session)
        if not sok then return fail('session_read_unavailable') end
        if current==nil then session=nil;clear();return fail('session_unavailable') end
        if current~=session then session=current;clear();budget_frame=frame end
        validation_time,validation_frame=now,frame
        if #targets==0 then established,retired_units={},{};return fail('no_targets') end

        -- The full current turret census is required even for retained pairs:
        -- a newly deployed overlapping Unit must block its affected old label.
        local units,err=targets_now(targets)
        if not units then return fail(err) end
        if not set_scope(targets) then return fail('no_static_types_for_targets') end
        local unit_by={}
        for _,u in ipairs(units) do unit_by[u.unit]=u end
        for unit in pairs(established) do if not unit_by[unit] then established[unit]=nil end end
        for unit in pairs(retired_units) do if not unit_by[unit] then retired_units[unit]=nil end end
        local ids,why=owned();if not ids then return fail(why) end
        reconcile(ids,now)
        local positives,positive_problem=positive_rows()
        if not positives then return fail(positive_problem) end
        local candidate_problem
        for _,row in ipairs(positives) do
            local verified,verify_problem=verify_positive(row,frame)
            if not verified then candidate_problem=candidate_problem or verify_problem end
        end
        local classification_before=checks
        ph,why=classify(pending,ph,'pending',frame,now)
        if not why then rh,why=classify(refresh,rh,'refresh',frame,now) end
        compact()
        observe_classification(now,checks-classification_before)
        local discovery_problem=why and why~='classification_budget' and why or nil
        local done,wait=complete(now)
        if wait=='game_time_reversed' then established,retired_units={},{};return fail(wait) end
        ids,why=owned();if not ids then return fail(why) end
        reconcile(ids,now)
        local current_done,current_wait=complete(now)
        done=done and current_done;wait=current_wait or wait
        positives,positive_problem=positive_rows()
        if not positives then return fail(positive_problem) end
        for _,row in ipairs(positives) do
            if row.checked_frame~=frame and row.verify_attempt_frame~=frame then
                local verified,verify_problem=verify_positive(row,frame)
                if not verified then candidate_problem=candidate_problem or verify_problem end
            end
        end
        -- A successful check must still refer to the same observed object row.
        -- Never replace an established pair with a new GOID or recycled row.
        -- 对象消失后这台单位不再认旧对象，但之后新部署的对象仍可认领。
        -- 战备会复用单位句柄，只按单位永久封禁会让重新部署的那一台永远不显示。
        for unit,p in pairs(established) do
            local row=rows[p.goid]
            if not row or row~=p.object_row or not row.kind or row.kind.type_hash~=p.type_hash
                or unit_by[unit].model_hash~=p.model_hash then
                established[unit]=nil;retired_units[unit]=now
            end
        end
        local candidates,candidate_by={},{}
        for id,row in pairs(rows) do
            -- 没有位置字段的类型不参与向量匹配，也不算缺失候选，否则 discovery 永远完不成。
            local trackable=row.kind and integer(row.kind.position_slot,1,#row.kind.fields)
            if not trackable then
                -- 交给下面「唯一对象」那一层处理。
            elseif row.checked_frame==frame then
                local valid,schema_reason=schema(row.kind)
                if not valid then
                    if schema_bad[row.kind.type_hash]==nil then
                        schema_bad[row.kind.type_hash]=schema_reason
                        if type(ot.log)=='function' then
                            pcall(ot.log,'SENTRY-SCHEMA unusable type=%s reason=%s',
                                row.kind.type_hash,schema_reason)
                        end
                    end
                else
                    local ok,value=call(GS.game_object_field,session,id,row.kind.position_field)
                    local pos=ok and xyz(value)
                    if not pos then
                        candidate_problem=candidate_problem or ('candidate_vector_read_failed:'..row.kind.type_hash)
                    else
                        pos.id,pos.kind,pos.object_row=id,row.kind,row
                        candidates[#candidates+1]=pos;candidate_by[id]=pos
                    end
                end
            elseif row.kind then candidate_problem=candidate_problem or 'candidate_type_not_checked_this_frame' end
        end
        discovery_complete=done and not discovery_problem and not candidate_problem
        discovery_reason=discovery_problem or candidate_problem or wait or 'complete'
        candidate_count,nearest_distance,nearest_type,distance_frame=#candidates,nil,nil,frame
        nearest_unit_position,nearest_network_vector=nil,nil
        local unit_count,id_count,choice={},{},{}
        for _,u in ipairs(units) do
            for _,c in ipairs(candidates) do
                if c.kind.models[u.model_hash] then
                    local distance2=(u.x-c.x)^2+(u.y-c.y)^2+(u.z-c.z)^2
                    if not nearest_distance or distance2<nearest_distance^2 then
                        nearest_distance,nearest_type=math.sqrt(distance2),c.kind.type_hash
                        nearest_unit_position={x=u.x,y=u.y,z=u.z}
                        nearest_network_vector={x=c.x,y=c.y,z=c.z}
                    end
                    if within(c.kind.type_hash,u,c) then
                        unit_count[u.unit]=(unit_count[u.unit] or 0)+1
                        id_count[c.id]=(id_count[c.id] or 0)+1;choice[u.unit]=c
                    end
                end
            end
        end
        -- 车载武器的网络对象只固定在落点、不跟车，而且同族对象可能不止一个，
        -- 按全局唯一判定会一直 not_unique。只对车体模型本身放宽：该模型在场上
        -- 只有一个单位时，按水平最近取一个尚未被占用的候选；车开走之后退化为
        -- 「全图该类型唯一对象」。已经配过的对象优先，避免车开动后锚点乱跳。
        local vehicle_choice={}
        do
            local per_model,per_type={},{}
            for _,u in ipairs(units) do per_model[u.model_hash]=(per_model[u.model_hash] or 0)+1 end
            for _,c in ipairs(candidates) do
                local t=c.kind.type_hash
                if t==VEHICLE.type then per_type[t]=(per_type[t] or 0)+1 end
            end
            for _,u in ipairs(units) do
                if u.model_hash==VEHICLE.model and per_model[u.model_hash]==1 and not choice[u.unit] then
                    local best,best2=nil,nil
                    local p=established[u.unit]
                    if p then
                        -- 沿用旧锚点也必须先确认它现在仍是这个类型：空投舱换成车体后
                        -- 对象类型会变，此时继续认旧 goid 会让采样每帧 type_mismatch。
                        for _,c in ipairs(candidates) do
                            if c.id==p.goid and not id_count[c.id]
                                and c.kind.type_hash==VEHICLE.type and c.kind.models[u.model_hash] then
                                best,best2=c,0
                            end
                        end
                    end
                    for _,c in ipairs(candidates) do
                        if c.kind.type_hash==VEHICLE.type and c.kind.models[u.model_hash]
                            and not id_count[c.id] then
                            local dx,dy=u.x-c.x,u.y-c.y
                            local d2=dx*dx+dy*dy
                            if d2<=VEHICLE.horiz2 and (not best2 or d2<best2) then best,best2=c,d2 end
                        end
                    end
                    if not best then
                        for _,c in ipairs(candidates) do
                            local t=c.kind.type_hash
                            if t==VEHICLE.type and c.kind.models[u.model_hash] and per_type[t]==1
                                and not id_count[c.id] then
                                if best then best=false;break end
                                best=c
                            end
                        end
                    end
                    if best then
                        unit_count[u.unit],id_count[best.id],choice[u.unit]=1,1,best
                        vehicle_choice[u.model_hash]=best
                    end
                end
            end
        end
        -- 结构类的 unit 原点与网络对象坐标并不重合，两米内再给一次机会；
        -- 只有「一个单位对一个对象」互相唯一时才认，避免把邻近的另一台设备抢过来。
        local near_choice,near_units,near_ids={},{},{}
        for _,u in ipairs(units) do
            if not choice[u.unit] then
                for _,c in ipairs(candidates) do
                    if c.kind.models[u.model_hash] and not id_count[c.id]
                        and c.kind.type_hash~=VEHICLE.type then
                        local distance2=(u.x-c.x)^2+(u.y-c.y)^2+(u.z-c.z)^2
                        if distance2>EPS2 and distance2<=NEAR2 then
                            near_units[u.unit]=(near_units[u.unit] or 0)+1
                            near_ids[c.id]=(near_ids[c.id] or 0)+1
                            near_choice[u.unit]=c
                        end
                    end
                end
            end
        end
        for unit,c in pairs(near_choice) do
            if near_units[unit]==1 and near_ids[c.id]==1 and not id_count[c.id] then
                unit_count[unit],id_count[c.id],choice[unit]=1,1,c
            end
        end
        -- 没有位置字段的类型进不了候选。已配对的单位先锁住自己那个对象，
        -- 剩下没被占用的同类型对象唯一时才认领；否则第二台部署后两边都不唯一，
        -- 整组一起失效。
        local pinned,taken={},{}
        for _,u in ipairs(units) do
            local p=established[u.unit]
            local row=p and rows[p.goid]
            if row and row.kind and row==p.object_row and row.kind.models[u.model_hash]
                and not integer(row.kind.position_slot,1,#row.kind.fields) then
                pinned[u.unit]=row;taken[row.id]=true
            end
        end
        local type_total,type_only={},{}
        for id,row in pairs(rows) do
            if row.kind and not taken[id] then
                local t=row.kind.type_hash
                type_total[t]=(type_total[t] or 0)+1;type_only[t]=row
            end
        end
        for _,u in ipairs(units) do
            if not choice[u.unit] then
                local pick=pinned[u.unit]
                if not pick then
                    for _,row in pairs(type_only) do
                        if type_total[row.kind.type_hash]==1 and row.kind.models[u.model_hash]
                            and not integer(row.kind.position_slot,1,#row.kind.fields)
                            and not id_count[row.id] then
                            if pick then pick=false;break end
                            pick=row
                        end
                    end
                end
                if pick then
                    local c={id=pick.id,kind=pick.kind,object_row=pick,x=u.x,y=u.y,z=u.z}
                    candidates[#candidates+1]=c;candidate_by[c.id]=c
                    unit_count[u.unit],id_count[c.id],choice[u.unit]=1,1,c
                end
            end
        end
        -- 同一帧落下多台时上一步没有唯一对象可认：未认领的单位数与未占用的对象数
        -- 相等就按 goid 顺序配对。同型号、同初始弹药，配错只影响相邻两台。
        do
            local open_by_model={}
            for _,u in ipairs(units) do
                if not choice[u.unit] then
                    local list=open_by_model[u.model_hash]
                    if not list then list={};open_by_model[u.model_hash]=list end
                    list[#list+1]=u
                end
            end
            local free={}
            for id,row in pairs(rows) do
                if row.kind and not taken[id] and not id_count[id]
                    and not integer(row.kind.position_slot,1,#row.kind.fields) then
                    free[#free+1]=row
                end
            end
            table.sort(free,function(a,b) return a.id<b.id end)
            for model,list in pairs(open_by_model) do
                local pool={}
                for i=1,#free do
                    if free[i].kind.models[model] then pool[#pool+1]=free[i] end
                end
                if #pool>1 and #pool==#list then
                    for i=1,#list do
                        local u,row=list[i],pool[i]
                        local c={id=row.id,kind=row.kind,object_row=row,x=u.x,y=u.y,z=u.z}
                        candidates[#candidates+1]=c;candidate_by[c.id]=c
                        unit_count[u.unit],id_count[c.id],choice[u.unit]=1,1,c
                    end
                end
            end
        end
        -- 认不出来的模型每 60 秒重打一次：最近的网络对象是什么类型、有多远。
        if type(ot.log)=='function' then
            for _,u in ipairs(units) do
                local last=diag_logged[u.model_hash]
                if not choice[u.unit] and (not last or now-last>=60) then
                    local best,best2=nil,nil
                    for _,c in ipairs(candidates) do
                        local distance2=(u.x-c.x)^2+(u.y-c.y)^2+(u.z-c.z)^2
                        if not best2 or distance2<best2 then best,best2=c,distance2 end
                    end
                    diag_logged[u.model_hash]=now
                    if best then
                        pcall(ot.log,'SENTRY-IDENTITY unbound model=%s nearest_type=%s distance=%.2f',
                            u.model_hash,best.kind.type_hash,math.sqrt(best2))
                    else
                        pcall(ot.log,'SENTRY-IDENTITY unbound model=%s no_network_objects',u.model_hash)
                    end
                end
            end
        end
        -- 每 15 秒说明还有谁没认出来，附上卡在哪一步。
        local blocked=nil
        for _,u in ipairs(units) do
            local p=established[u.unit]
            local c=p and candidate_by[p.goid] or choice[u.unit]
            local same=p and c and c.object_row==p.object_row and c.kind.type_hash==p.type_hash
            local original=same and choice[u.unit]==c
            local unique=c and unit_count[u.unit]==1 and id_count[c.id]==1
            local retained=p and original and unique
            local retired_at=retired_units[u.unit]
            local fresh=retired_at and c and c.object_row and c.object_row.seen_at
                and c.object_row.seen_at>=retired_at
            local admit=not p and (not retired_at or fresh) and discovery_complete and unique
            if retained or admit then
                if admit then
                    p={goid=c.id,type_hash=c.kind.type_hash,model_hash=u.model_hash,
                        object_row=c.object_row,established_frame=frame,established_time=now}
                    established[u.unit]=p;new_count=new_count+1
                else retained_count=retained_count+1 end
                resolved[u.unit]={goid=p.goid,type_hash=p.type_hash,call=c.kind.call,session=session,
                    frame=frame,model_hash=u.model_hash,identity_basis='static_type_and_unique_vector_match',
                    identity_retained=retained==true,identity_established_frame=p.established_frame}
                bound=bound+1
            else
                blocked=blocked or {}
                blocked[#blocked+1]=u.model_hash..':'..(not c and 'no_match'
                    or (retired_at and not fresh and 'retired')
                    or (not unique and 'not_unique')
                    or (not discovery_complete and 'discovery') or 'other')
            end
        end
        if blocked and type(ot.log)=='function' and now-(unresolved_log_time or -1e9)>=15 then
            unresolved_log_time=now
            pcall(ot.log,'SENTRY-IDENTITY unresolved %d: %s',#blocked,table.concat(blocked,' '))
        end
        -- 车载类型的配对结果：pick 出现说明走了唯一性豁免，missing 说明这个模型
        -- 在场却没有能配上的对象。
        if type(ot.log)=='function' then
            for model,c in pairs(vehicle_choice) do
                local last=vehicle_logged[model]
                if not last or now-last>=60 then
                    vehicle_logged[model]=now
                    pcall(ot.log,'SENTRY-IDENTITY vehicle model=%s type=%s pick=%s',model,c.kind.type_hash,tostring(c.id))
                end
            end
        end
        resolved_frame=frame
        if bound>0 then
            reason=discovery_complete and 'inferred_unique_bindings' or 'retained_verified_bindings'
            return true
        end
        reason=discovery_complete and 'no_unique_vector_match' or discovery_reason
        return discovery_complete,reason
    end
    function api.resolve(unit,model)
        local ok,frame=call(ot.frame);local row=resolved[unit]
        if not ok or frame~=resolved_frame or not row or row.frame~=frame or row.model_hash~=hex(model,16,false) then return nil,reason end
        -- Check session and liveness before passing retained handles to another API.
        local sok,current=call(Net.game_session)
        if not sok or current~=session then resolved[unit]=nil;return nil,'identity_expired' end
        local aok,alive=call(Unit.alive,unit)
        if not aok or alive~=true then resolved[unit]=nil;return nil,'identity_expired' end
        local wok,uw=call(Unit.world,unit)
        if not wok or uw~=world then resolved[unit]=nil;return nil,'identity_expired' end
        local eok,exists=call(GS.game_object_exists,session,row.goid)
        if not eok or exists~=true then resolved[unit]=nil;return nil,'identity_expired' end
        return {goid=row.goid,type_hash=row.type_hash,call=row.call,session=row.session,frame=row.frame,identity_basis=row.identity_basis,
            identity_retained=row.identity_retained,identity_established_frame=row.identity_established_frame}
    end
    function api.shutdown() api.reset(nil);reason='shutdown' end
    function api.status()
        return {reason=reason,objects=objects,unclassified=unclassified,expired_negatives=expired,
            bound=bound,types=#types,type_checks_this_frame=checks,reclassified=reclassified,
            discovery_complete=discovery_complete,discovery_reason=discovery_reason,
            retained_bindings=retained_count,new_bindings=new_count,
            background_type_checks_this_frame=checks,positive_verifications_this_update=positive_verifications,
            positive_candidates=known_positive_count,positive_candidate_limit=MAX_POSITIVES,
            negative_recheck_interval=RECHECK_INTERVAL,
            candidates=candidate_count,minimum_same_model_distance=nearest_distance,
            nearest_type_hash=nearest_type,distance_sample_frame=distance_frame,
            nearest_unit_position=nearest_unit_position,nearest_network_vector=nearest_network_vector,
            negative_cache_max_age=age_limit,negative_cache_hard_limit=MAX_AGE,
            classification_checks_per_second=classification_rate,estimated_negative_sweep_seconds=sweep_seconds,
            required_negative_cache_age=required_age,oldest_negative_age=oldest_negative_age,
            negative_cache_window_capped=throughput_limited,
            classification_throughput_limited=throughput_limited and expired>0,position_tolerance_metres=0.001,
            identity_basis='static_type_and_unique_vector_match',position_semantics_verified=false,
            identity_inferred=true,game_validated=false}
    end
    return api
end


end)()
local create_census = (function()
-- Complete current sentry candidates from exact resource queries, Lua 5.1.
-- factory(ot,profile) -> reset(world), sample(), status(), shutdown().
-- profile.models contains every allowed model hash; profile.model_resources
-- contains its full authored .unit path, independently hash-checked at build time.
-- No World.units scan, negative cache, or persistent Unit classification is used.
-- The current frame uses at most one query per configured resource. All returned
-- live Units must match world/model before any candidate list is published.
return function(ot,profile)
    ot=type(ot)=='table' and ot or {}
    profile=type(profile)=='table' and profile or {}
    local S=type(ot.engine)=='table' and ot.engine or {}
    local App,World,Unit=S.Application or {},S.World or {},S.Unit or {}
    local MAX_TARGETS,MAX_RAW_UNITS,MAX_RESOURCES=128,128,32
    local resources,problem={},nil
    local world,frame,cached=nil,nil,nil
    local reason='no_world'
    local queries,units_checked,target_count,elapsed_ms=0,0,0,0
    local census_logged={}
    -- 已知的附属部件：挂在真正带武器的单位上，自己从来没有弹药。
    -- 只统计不参与识别，否则每 15 秒的 unresolved 里会一直挂着它们。
    local ACCESSORY={['29ae906ea485a7fa']=true,['583ecde0d7718126']=true}
    local complete=false
    local function finite(x)
        return type(x)=='number' and x==x and x>-math.huge and x<math.huge
    end
    local function integer(x,low,high)
        return finite(x) and x%1==0 and x>=low and x<=high
    end
    local function model_hash(value)
        if type(value)~='string' then return nil end
        value=value:lower():gsub('^0x','')
        if #value>16 or not value:match('^%x+$') then return nil end
        value=value:gsub('^0+','')
        return value~='' and value or '0'
    end
    local function call(fn,...)
        if type(fn)~='function' then return false,nil end
        return pcall(fn,...)
    end
    local required={App.worlds,World.units_by_resource,Unit.alive,Unit.world,
        Unit.debug_name,ot.frame,ot.clock}
    for i=1,7 do
        if type(required[i])~='function' then problem='resource_query_api_unavailable' end
    end
    local models,paths=profile.models,profile.model_resources
    if type(models)~='table' or type(paths)~='table' then
        problem='resource_profile_unavailable'
    else
        local hashes,names={},{}
        for hash in pairs(models) do
            local normalized=model_hash(hash)
            local path=paths[hash]
            if not normalized or hashes[normalized] or type(path)~='string'
                or #path<1 or #path>512 or path:find('%c') or names[path] then
                problem='invalid_resource_profile'
                break
            end
            hashes[normalized],names[path]=true,true
            resources[#resources+1]={model_hash=normalized,path=path}
            if #resources>MAX_RESOURCES then problem='resource_profile_limit';break end
        end
        if #resources<1 then problem='resource_profile_empty' end
        table.sort(resources,function(a,b) return a.model_hash<b.model_hash end)
    end
    local function fail(why)
        cached=nil;complete=false;target_count=0;reason=why
        return nil,why
    end
    local function clear()
        frame,cached=nil,nil
        queries,units_checked,target_count,elapsed_ms=0,0,0,0
        complete=false
    end
    local function live_world()
        local ok,list=call(App.worlds)
        if not ok or type(list)~='table' then return false end
        for _,candidate in pairs(list) do if candidate==world then return true end end
        return false
    end
    local function validate(unit,expected)
        if type(unit)~='userdata' then return nil,'invalid_unit_handle' end
        local ok,alive=call(Unit.alive,unit)
        if not ok or type(alive)~='boolean' then return nil,'unit_liveness_unavailable' end
        if not alive then return false end
        local wok,uw=call(Unit.world,unit)
        if not wok or uw~=world then return nil,'unit_world_mismatch' end
        local nok,name=call(Unit.debug_name,unit)
        local actual=nok and type(name)=='string' and name:match('#ID%[(%x+)%]')
        if not actual or model_hash(actual)~=expected then return nil,'unit_model_mismatch' end
        return true
    end
    local function copy_targets(list)
        local out={}
        for i=1,#list do
            local row=list[i]
            out[i]={unit=row.unit,model_hash=row.model_hash}
        end
        return out
    end
    local api={}
    function api.reset(next_world)
        world=next_world;clear()
        reason=world and 'awaiting_sample' or 'no_world'
    end
    function api.sample()
        if not world then return fail('no_world') end
        if problem then return fail(problem) end
        local fok,current=call(ot.frame)
        if not fok or not integer(current,0,9007199254740991) then return fail('frame_unavailable') end
        if not live_world() then return fail('world_unavailable') end
        if frame==current then
            if not cached then return nil,reason end
            -- Re-check returned handles if another caller asks in this frame.
            -- The complete query set is never reissued or replaced with a subset.
            for i=1,#cached do
                local row=cached[i]
                local valid,why=validate(row.unit,row.model_hash)
                if valid~=true then return fail(why or 'cached_unit_destroyed') end
            end
            return copy_targets(cached),'complete'
        end
        frame=current;cached=nil;complete=false
        queries,units_checked,target_count,elapsed_ms=0,0,0,0
        local tok,started=call(ot.clock)
        if not tok or not finite(started) then return fail('clock_unavailable') end
        local function measure()
            local ok,tick=call(ot.clock)
            if not ok or not finite(tick) or tick<started then return false,'clock_unavailable' end
            elapsed_ms=(tick-started)*1000
            return true
        end
        local targets,seen={},{}
        local raw_total=0
        for _,resource in ipairs(resources) do
            queries=queries+1
            local ok,list=call(World.units_by_resource,world,resource.path)
            if not ok or type(list)~='table' then return fail('resource_query_unavailable') end
            local allowed,why=measure()
            if not allowed then return fail(why) end
            local count,largest=0,0
            for index,unit in pairs(list) do
                count=count+1;raw_total=raw_total+1
                if raw_total>MAX_RAW_UNITS then return fail('resource_result_limit') end
                if not integer(index,1,MAX_RAW_UNITS) or type(unit)~='userdata' then
                    return fail('resource_result_not_dense')
                end
                if index>largest then largest=index end
            end
            if largest~=count then return fail('resource_result_not_dense') end
            for i=1,count do
                local unit=list[i]
                if seen[unit] then return fail('duplicate_resource_unit') end
                seen[unit]=true;units_checked=units_checked+1
                local valid,why=validate(unit,resource.model_hash)
                if valid==nil then return fail(why) end
                if valid and not ACCESSORY[resource.model_hash] then
                    targets[#targets+1]={unit=unit,model_hash=resource.model_hash}
                    if #targets>MAX_TARGETS then return fail('sentry_candidate_limit') end
                end
                if i%32==0 then
                    local allowed,why=measure();if not allowed then return fail(why) end
                end
            end
            if type(ot.log)=='function' and census_logged[resource.path]~=count then
                census_logged[resource.path]=count
                pcall(ot.log,'SENTRY-CENSUS %s model=%s units=%d%s',resource.path,resource.model_hash,count,
                    ACCESSORY[resource.model_hash] and ' accessory' or '')
            end
        end
        local allowed,why=measure()
        if not allowed then return fail(why) end
        cached=targets;complete=true;target_count=#targets;reason='complete'
        return copy_targets(cached),reason
    end
    function api.shutdown()
        world=nil;clear();reason='shutdown'
    end
    function api.status()
        return {reason=reason,queries=queries,units_checked=units_checked,targets=target_count,
            elapsed_ms=elapsed_ms,complete=complete,frame=frame,resources=#resources,
            basis='current_resource_queries',game_validated=false}
    end
    return api
end

end)()
local create_camera_units = (function()
-- Current-frame camera candidates without scanning every Unit in the world.
-- The resource hash is observed in Objective Tracker's actual camera logs.
-- IdString64.from_hex is used by installed-hud-plus.lua:780-792; its result
-- is temporary, so construct and consume it inside every sample call.
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/obj_stingray_IdString64.html
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/obj_stingray_World.html
return function(ot)
    local S=ot.engine
    local App,World,Unit,Id=S.Application or {},S.World or {},S.Unit or {},S.IdString64 or {}
    local MODEL,MAX_UNITS='465f4895f3dc98d1',64
    local reason,total,candidates,frame='not_sampled',0,0,nil
    local function call(fn,...)
        if type(fn)~='function' then return false,nil end
        return pcall(fn,...)
    end
    local function reject(value) reason=value;return nil,value end
    local function current_frame()
        local ok,value=call(ot.frame)
        if ok and type(value)=='number' and value>=0 and value<math.huge and value%1==0 then
            return value
        end
    end
    local function sample(world)
        reason,total,candidates,frame='sampling',0,0,current_frame()
        if frame==nil then return reject('camera_frame_unavailable') end
        if world==nil then return reject('camera_world_unavailable') end
        local listed,worlds=call(App.worlds)
        if not listed or type(worlds)~='table' then return reject('camera_world_list_unavailable') end
        local live_world=false
        for _,value in pairs(worlds) do if value==world then live_world=true end end
        if not live_world then return reject('camera_world_not_live') end
        if type(Id.from_hex)~='function' then return reject('camera_id64_from_hex_unavailable') end
        local made,key=call(Id.from_hex,MODEL)
        if not made or type(key)~='userdata' then return reject('camera_resource_key_unavailable') end
        -- Do not turn MODEL into an ordinary hex-string resource name: that
        -- would hash the hexadecimal text instead of using the observed ID64.
        local queried,units=call(World.units_by_resource,world,key)
        key=nil
        if not queried or type(units)~='table' then return reject('camera_resource_query_failed') end
        local highest=0
        for index,unit in pairs(units) do
            total=total+1
            if total>MAX_UNITS then return reject('camera_resource_limit') end
            if type(index)~='number' or index%1~=0 or index<1 or index>MAX_UNITS
                or type(unit)~='userdata' then return reject('camera_resource_list_invalid') end
            if index>highest then highest=index end
        end
        if highest~=total then return reject('camera_resource_list_incomplete') end
        local result,seen={},{}
        for i=1,total do
            local unit=units[i]
            if not seen[unit] then
                seen[unit]=true
                local alive_ok,alive=call(Unit.alive,unit)
                if not alive_ok or (alive~=true and alive~=false) then
                    return reject('camera_unit_liveness_unavailable')
                end
                if alive then
                    local world_ok,unit_world=call(Unit.world,unit)
                    if not world_ok or unit_world~=world then return reject('camera_unit_world_mismatch') end
                    local named,name=call(Unit.debug_name,unit)
                    local hash=named and type(name)=='string' and name:match('^#ID%[(%x+)%]$') or nil
                    if not hash or hash:lower():gsub('^0+','')~=MODEL then
                        return reject('camera_unit_model_mismatch')
                    end
                    result[#result+1]=unit
                end
            end
        end
        if current_frame()~=frame then return reject('camera_frame_changed_during_query') end
        candidates=#result
        -- Normal complete game back-buffer rendering remains an integration
        -- assumption; the selector still checks mono/perspective and its pose.
        result.full_back_buffer_verified=true
        result.frame=frame
        reason=candidates>0 and 'complete' or 'no_camera_candidates'
        return result
    end
    return {
        sample=sample,
        status=function()
            return {reason=reason,total=total,candidates=candidates,frame=frame,
                resource_hash=MODEL,source='World.units_by_resource',
                resource_key_source='IdString64.from_hex',
                viewport_source='full_back_buffer_assumption',game_validated=false}
        end,
    }
end
end)()
local create_camera = (function()
-- Camera adapter for sentry_labels.lua.
-- Source only: no active-camera discovery is claimed by this module.
-- A gameplay integration must identify the active camera and its viewport in
-- the current frame. A moving unit or a model hash does not establish that.
-- Stingray: camera forward is matrix Y, right is X, and up is Z.
-- Never retain the returned pose across frames (temporary engine userdata).
return function(ot)
    local S=ot.engine
    local App,Unit,Camera,M=S.Application,S.Unit,S.Camera,S.Matrix4x4
    local reason='waiting_for_active_camera'
    local function finite(value)
        return type(value)=='number' and value==value
            and value~=math.huge and value~=-math.huge
    end
    local function reject(value)
        reason=value
        return nil,value
    end
    local function call(fn,...)
        if type(fn)~='function' then return false,nil end
        return pcall(fn,...)
    end
    local function live_world(world)
        if not world then return false end
        local ok,worlds=call(App and App.worlds)
        if not ok or type(worlds)~='table' then return false end
        for _,candidate in pairs(worlds) do
            if candidate==world then return true end
        end
        return false
    end
    local function orthonormal(elements)
        local function dot(a,b)
            return elements[a]*elements[b]+elements[a+1]*elements[b+1]
                +elements[a+2]*elements[b+2]
        end
        for _,axis in ipairs({1,4,7}) do
            if math.abs(dot(axis,axis)-1)>0.001 then return false end
        end
        return math.abs(dot(1,4))<0.001 and math.abs(dot(1,7))<0.001
            and math.abs(dot(4,7))<0.001
    end
    local function sample(world,active)
        if type(active)~='table' or active.verified~=true then
            return reject('waiting_for_active_camera')
        end
        local okFrame,frame=call(ot.frame)
        if not okFrame or not finite(frame) or active.frame~=frame then
            return reject('stale_camera_selection')
        end
        if active.world~=world or not live_world(world) then
            return reject('world_unavailable')
        end
        local unit,index=active.unit,active.camera_index
        if not unit or not finite(index) or index<1 or index%1~=0 then
            return reject('invalid_camera_selection')
        end
        local width,height=active.viewport_width,active.viewport_height
        if not finite(width) or not finite(height) or width<16 or height<16
            or width>32768 or height>32768 then
            return reject('viewport_unavailable')
        end
        local okAlive,alive=call(Unit and Unit.alive,unit)
        if not okAlive or alive~=true then return reject('camera_unit_destroyed') end
        local okWorld,ownerWorld=call(Unit and Unit.world,unit)
        if not okWorld or ownerWorld~=world then return reject('camera_world_mismatch') end
        local okCount,count=call(Unit and Unit.num_cameras,unit)
        if not okCount or not finite(count) or index>count then
            return reject('camera_index_unavailable')
        end
        local okCamera,camera=call(Unit and Unit.camera,unit,index)
        if not okCamera or camera==nil then return reject('camera_unavailable') end
        local okProjection,projection=call(Camera and Camera.projection_type,camera)
        if not okProjection or not Camera or Camera.PERSPECTIVE==nil
            or projection~=Camera.PERSPECTIVE then
            return reject('unsupported_projection')
        end
        local okMode,mode=call(Camera.mode,camera)
        if not okMode or Camera.MONO==nil or mode~=Camera.MONO then
            return reject('unsupported_camera_mode')
        end
        local okFov,fov=call(Camera.vertical_fov,camera)
        if not okFov or not finite(fov) or fov<=0.01 or fov>=math.pi-0.01 then
            return reject('invalid_vertical_fov')
        end
        local okPose,pose=call(Camera.world_pose,camera)
        if not okPose or pose==nil or not M or type(M.to_elements)~='function' then
            return reject('camera_pose_unavailable')
        end
        local packed={pcall(M.to_elements,pose)}
        if packed[1]~=true or #packed~=13 then return reject('invalid_camera_pose') end
        local elements={}
        for i=1,12 do
            local value=packed[i+1]
            if not finite(value) then return reject('invalid_camera_pose') end
            elements[i]=value
        end
        if not orthonormal(elements) then return reject('scaled_camera_pose') end
        local okNear,near=call(Camera.near_range,camera)
        if not okNear or not finite(near) or near<=0 then
            return reject('near_plane_unavailable')
        end
        reason='ready'
        return {
            verified=true,world=world,frame=frame,pose=pose,
            x=elements[10],y=elements[11],z=elements[12],
            forward_x=elements[4],forward_y=elements[5],forward_z=elements[6],
            vertical_fov_radians=fov,viewport_width=width,viewport_height=height,
            near_range=near,projection='perspective',
        }
    end
    return {
        sample=sample,
        status=function() return {reason=reason} end,
    }
end

end)()
local create_camera_select = (function()
-- Read-only camera matcher, called from the render callback.
-- factory(ot) -> select(world, camera_units), reset(), status().
-- camera_units is a dense array of the world's known camera-bearing Units.
-- Optional metadata: viewport, window; without a viewport the caller asserts
-- full_back_buffer_verified=true. Window-sized rendering is an explicit
-- integration assumption, not something Gui.resolution() alone proves.
--
-- Match World.debug_camera_pose to a unique current candidate first. If none
-- matches, compare ONLY frame-1's numerical candidate snapshots, then select
-- that same Unit/index's CURRENT camera. Never retain temporary Matrix4x4,
-- Vector3, or Camera userdata in history, or reuse an old selected identity.
-- Switching cameras can still select the previous camera for one render frame.
-- Missing history, gaps, world changes, unreadable candidates or ambiguity hide
-- labels. The new one-frame timing path still needs an in-game rerun.
--
-- https://help.autodesk.com/cloudhelp/ENU/Stingray-Help/lua_ref/obj_stingray_Gui.html
-- Gui.resolution(viewport?, window?) returns viewport GUI pixel dimensions;
-- without viewport, it returns the back-buffer resolution.
-- https://help.autodesk.com/cloudhelp/2019/ENU/Max-Interactive-Help/lua_ref/obj_stingray_World.html
-- World.debug_camera_pose(world) returns the last render camera's pose.
return function(ot)
    local S=ot.engine
    local App,World,Unit,Camera,Gui,M=S.Application,S.World,S.Unit,S.Camera,S.Gui,S.Matrix4x4
    local reason,matches,checked='not_sampled',0,0
    local viewport_source,selection_basis,latency='unresolved','none',nil
    local current_snapshot,previous_snapshot=nil,nil
    local MAX_UNITS,MAX_CAMERAS=64,8
    local AXIS_EPS,POSITION_EPS=0.00001,0.001
    local function finite(value)
        return type(value)=='number' and value==value
            and value~=math.huge and value~=-math.huge
    end
    local function call(fn,...)
        if type(fn)~='function' then return false,nil end
        return pcall(fn,...)
    end
    local function reject(value) reason=value;return nil,value end
    local function matrix(pose)
        if pose==nil then return nil end
        local result={call(M and M.to_elements,pose)}
        if result[1]~=true or #result~=13 then return nil end
        local values={}
        for i=1,12 do
            if not finite(result[i+1]) then return nil end
            values[i]=result[i+1]
        end
        return values
    end
    local function same_pose(a,b)
        for i=1,12 do
            if math.abs(a[i]-b[i])>(i<=9 and AXIS_EPS or POSITION_EPS) then return false end
        end
        return true
    end
    local function live_world(world)
        if world==nil then return false end
        local ok,worlds=call(App and App.worlds)
        if not ok or type(worlds)~='table' then return false end
        for _,candidate in pairs(worlds) do if candidate==world then return true end end
        return false
    end
    local function prior_frame(world,frame)
        for _,snapshot in pairs({current_snapshot,previous_snapshot}) do
            if snapshot.world==world and snapshot.frame==frame-1 then return snapshot end
        end
    end
    local function remember(world,frame,entries,prior)
        -- Same-frame repeated renders replace only this frame's snapshot.
        previous_snapshot=prior
        current_snapshot={world=world,frame=frame,entries=entries}
    end
    local function select_camera(world,units)
        matches,checked=0,0
        viewport_source,selection_basis,latency='unresolved','none',nil
        if type(units)~='table' then return reject('camera_candidates_unavailable') end
        if #units>MAX_UNITS then return reject('too_many_camera_candidates') end
        if not live_world(world) then return reject('world_unavailable') end
        local frame_ok,frame=call(ot.frame)
        if not frame_ok or not finite(frame) or frame<0 or frame%1~=0 then
            return reject('frame_unavailable')
        end
        if units.viewport==nil and units.full_back_buffer_verified~=true then
            return reject('viewport_unverified')
        end
        viewport_source=units.viewport~=nil and 'explicit_viewport' or 'full_back_buffer_assumption'
        local size_ok,width,height
        if units.viewport==nil and units.window==nil then
            size_ok,width,height=call(Gui and Gui.resolution)
        else
            size_ok,width,height=call(Gui and Gui.resolution,units.viewport,units.window)
        end
        if not size_ok or not finite(width) or not finite(height)
            or width<16 or height<16 or width>32768 or height>32768 then
            return reject('viewport_unavailable')
        end
        local debug_ok,debug_pose=call(World and World.debug_camera_pose,world)
        local last=debug_ok and matrix(debug_pose) or nil
        if not last then return reject('last_render_pose_unavailable') end
        local selected,seen,entries,live_candidates=nil,{},{},{}
        for i=1,#units do
            local unit=units[i]
            if unit==nil then return reject('incomplete_camera_candidates') end
            if not seen[unit] then
                seen[unit]=true
                local alive_ok,alive=call(Unit and Unit.alive,unit)
                if not alive_ok then return reject('candidate_alive_unreadable') end
                if alive==true then
                    local world_ok,unit_world=call(Unit and Unit.world,unit)
                    if not world_ok then return reject('candidate_world_unreadable') end
                    if unit_world==world then
                        local count_ok,count=call(Unit and Unit.num_cameras,unit)
                        if not count_ok or not finite(count) or count<0 or count%1~=0
                            or count>MAX_CAMERAS then
                            return reject('candidate_camera_count_unavailable')
                        end
                        live_candidates[unit]={}
                        for index=1,count do
                            local camera_ok,camera=call(Unit.camera,unit,index)
                            if not camera_ok or camera==nil then return reject('candidate_camera_unavailable') end
                            local pose_ok,pose=call(Camera and Camera.world_pose,camera)
                            local values=pose_ok and matrix(pose) or nil
                            if not values then return reject('candidate_pose_unreadable') end
                            checked=checked+1
                            local candidate={unit=unit,camera_index=index,camera=camera}
                            live_candidates[unit][index]=candidate
                            entries[#entries+1]={unit=unit,index=index,values=values}
                            if same_pose(last,values) then matches=matches+1;selected=candidate end
                        end
                    end
                end
            end
        end
        local prior=prior_frame(world,frame)
        remember(world,frame,entries,prior)
        if matches>1 then return reject('ambiguous_render_camera') end
        if matches==1 then
            selection_basis,latency='unique_current_pose_match_to_last_render',0
        elseif prior then
            for _,entry in ipairs(prior.entries) do
                if same_pose(last,entry.values) then
                    matches=matches+1
                    local candidates=live_candidates[entry.unit]
                    selected=candidates and candidates[entry.index]
                end
            end
            if matches>1 then return reject('ambiguous_previous_frame_render_camera') end
            if matches==1 and selected==nil then return reject('previous_frame_camera_unavailable') end
            if matches==1 then selection_basis,latency='previous_frame_render_match',1 end
        end
        if selected==nil then return reject('no_current_or_previous_frame_pose_match') end
        local mode_ok,mode=call(Camera and Camera.mode,selected.camera)
        local projection_ok,projection=call(Camera and Camera.projection_type,selected.camera)
        if not mode_ok or not Camera or Camera.MONO==nil or mode~=Camera.MONO then
            return reject('unsupported_camera_mode')
        end
        if not projection_ok or Camera.PERSPECTIVE==nil or projection~=Camera.PERSPECTIVE then
            return reject('unsupported_projection')
        end
        local fov_ok,fov=call(Camera.vertical_fov,selected.camera)
        if not fov_ok or not finite(fov) or fov<=0.01 or fov>=math.pi-0.01 then
            return reject('invalid_camera_fov')
        end
        reason=latency==0 and 'unique_current_pose_match' or 'unique_previous_frame_pose_match'
        return {
            verified=true,frame=frame,world=world,
            unit=selected.unit,camera_index=selected.camera_index,
            viewport_width=width,viewport_height=height,viewport_source=viewport_source,
            selection_basis=selection_basis,selection_latency_frames=latency,
        }
    end
    return {
        select=select_camera,
        reset=function()
            reason='not_sampled';matches,checked=0,0
            viewport_source,selection_basis,latency='unresolved','none',nil
            current_snapshot,previous_snapshot=nil,nil
        end,
        status=function()
            return {reason=reason,matches=matches,checked=checked,
                viewport_source=viewport_source,selection_basis=selection_basis,
                selection_latency_frames=latency,game_validated=false}
        end,
    }
end

end)()
local create_labels = (function()
-- Depth-tested labels: prominent nearby, with a readable screen-size floor at range.
-- Ammo snapshots keep their real sampling time and expire after at most 80 ms.
-- All native vectors/matrices/colors stay inside the current render callback.
-- Ammo digits are real samples; ratios retain their existing calculation basis.
-- Laser percentages use a static heat threshold; Tesla time is estimated upstream.
local function finite(v) return type(v)=='number' and v==v and v>-math.huge and v<math.huge end
local function integer(v,lo,hi) return finite(v) and v%1==0 and v>=lo and v<=hi end
local function option(v,default,lo,hi)
    if not finite(v) then return default end
    return math.max(lo,math.min(hi,v))
end
local function callable(v)
    if type(v)=='function' then return true end
    local mt=type(v)=='table' and getmetatable(v)
    return type(mt)=='table' and type(mt.__call)=='function'
end

local function valid_uv(uv)
    if type(uv)~='table' then return false end
    for _,k in ipairs({'u0','v0','u1','v1'}) do
        if not finite(uv[k]) or uv[k]<0 or uv[k]>1 then return false end
    end
    return uv.u1>uv.u0 and uv.v1>uv.v0
end

return function(ot,config)
    config=type(config)=='table' and config or {}
    local S=ot.engine
    local World,Gui,App,Unit=S.World or {},S.Gui or {},S.Application or {},S.Unit or {}
    local M,V2,V3,Color=S.Matrix4x4 or {},S.Vector2,S.Vector3,S.Color
    local height=option(config.pixel_height,24,10,48)
    local icon_size=option(config.icon_size,36,12,64)
    local bar_width=option(config.bar_width,64,30,100)
    local bar_height=option(config.bar_height,3,1,6)
    local icon_gap=8
    local segment_gap=3
    local near_scale=option(config.near_scale,4.5,1,8)
    local near_distance=option(config.near_distance,8,1,30)
    local reference_height=option(config.reference_viewport_height,1600,720,4320)
    local minimum_height=option(config.min_pixel_height,22,8,40)
    local frame_scale=option(config.frame_scale,1.4,1.1,2)
    local frame_size=icon_size*frame_scale
    local frame_alpha=option(config.frame_alpha,90,0,255)
    local head_offset=option(config.head_offset,2.5,0,10)
    local max_distance=option(config.max_distance,120,1,300)
    local max_labels=math.floor(option(config.max_labels,32,1,64))
    local text_alpha=option(config.text_alpha,185,0,255)
    local icon_alpha=option(config.icon_alpha,145,0,255)
    local bar_alpha=option(config.bar_alpha,145,0,255)
    local font=config.font
    local font_material=config.font_material
    local icon_material,icon_texture=config.icon_material,config.icon_texture
    local bar_uv,frame_uv=config.bar_uv,config.frame_uv
    local icons=type(config.icons)=='table' and config.icons or {}
    -- 按模型兜底：同一个 unit 模型的不同网络类型（旧数据残留的类型 id、野生的
    -- 固定阵地）不一定都收在 icons 里，缺项时整套图标与四角框都不会画，只剩弹药条。
    -- 放在模块内而不是 profile：profile 是与 MDL3 共享的表，不往里加新键。
    local model_icons={
        ['e977c49db7604f9']={u0=0.00390625,v0=0.00390625,u1=0.24609375,v1=0.24609375}, -- manned_turret 人操重机枪
        ['37cde43876ba26bb']={u0=0.50390625,v0=0.00390625,u1=0.74609375,v1=0.24609375}, -- turret_machinegun_gpmg
        ['61d9dda22a59e9bc']={u0=0.75390625,v0=0.00390625,u1=0.99609375,v1=0.24609375}, -- turret 加特林
        ['cbfe1fc7158347f3']={u0=0.00390625,v0=0.50390625,u1=0.24609375,v1=0.74609375}, -- aa_turret 机炮
    }
    local lift_delta=type(config.type_lift_delta)=='table' and config.type_lift_delta or {}
    local world,gui,disabled=nil,nil,false
    local reason,drawn,eligible,primitives,icons_drawn='no_world',0,0,0,0
    local missing,logged={},{}
    local extents,extent_count,font_ratio={},0,nil
    local peaks={}
    local assets_ready=false
    local function require_fn(name,fn) if type(fn)~='function' then missing[#missing+1]=name end end
    for _,row in ipairs({
        {'World.create_world_gui',World.create_world_gui},{'World.destroy_gui',World.destroy_gui},
        {'Application.worlds',App.worlds},{'Application.can_get',App.can_get},
        {'Gui.text_3d',Gui.text_3d},{'Gui.text_extents',Gui.text_extents},
        {'Gui.bitmap_3d_uv',Gui.bitmap_3d_uv},{'Gui.reset',Gui.reset},
        {'Unit.alive',Unit.alive},{'Unit.world',Unit.world},{'ot.frame',ot.frame},{'ot.time',ot.time},
        {'Matrix4x4.identity',M.identity},{'Matrix4x4.rotation',M.rotation},
        {'Matrix4x4.to_elements',M.to_elements},{'Matrix4x4.set_rotation',M.set_rotation},
        {'Matrix4x4.set_translation',M.set_translation},{'Matrix4x4.set_scale',M.set_scale},
        {'Vector2.to_elements',V2 and V2.to_elements}
    }) do require_fn(row[1],row[2]) end
    if not callable(V2) then missing[#missing+1]='Vector2 constructor' end
    if not callable(V3) then missing[#missing+1]='Vector3 constructor' end
    if not callable(Color) then missing[#missing+1]='Color constructor' end
    for _,name in ipairs({'font','font_material','icon_material','icon_texture'}) do
        if type(config[name])~='string' or #config[name]==0 then missing[#missing+1]=name..' resource' end
    end
    if not valid_uv(bar_uv) then missing[#missing+1]='bar_uv white atlas region' end
    if not valid_uv(frame_uv) then missing[#missing+1]='frame_uv open corner region' end
    local function log_once(key,message)
        if logged[key] then return end
        logged[key]=true
        if type(ot.log)=='function' then pcall(ot.log,'SENTRY-LABELS %s',message) end
    end
    local function call(key,fn,...)
        if type(ot.risky)=='function' then return ot.risky('labels:'..key,fn,...) end
        return pcall(fn,...)
    end
    local function live_world()
        if not world then return false end
        local ok,list=pcall(App.worlds)
        if not ok or type(list)~='table' then return false end
        for _,w in pairs(list) do if w==world then return true end end
        return false
    end
    local function dispose()
        local old=gui;gui=nil
        if old and live_world() then
            local ok,err=call('destroy_gui',World.destroy_gui,world,old)
            if not ok then log_once('destroy',tostring(err)) end
        end
    end
    local function stop(err)
        disabled=true;reason='backend_error';drawn,primitives,icons_drawn=0,0,0
        if gui and live_world() then pcall(Gui.reset,gui) end
        log_once('backend_error','disabled for this world: '..tostring(err))
    end
    local function check_assets()
        if assets_ready then return true end
        for _,r in ipairs({{'font',font},{'material',font_material},
                           {'material',icon_material},{'texture',icon_texture}}) do
            local ok,ready=pcall(App.can_get,r[1],r[2])
            if not ok or ready~=true then
                reason='awaiting_asset:'..r[2]
                log_once(reason,reason)
                return false
            end
        end
        assets_ready=true
        return true
    end
    local function camera_valid(c,frame)
        if type(c)~='table' or c.verified~=true or c.world~=world or c.frame~=frame
            or c.projection~='perspective' or type(c.pose)~='userdata' then return false end
        for _,k in ipairs({'x','y','z','forward_x','forward_y','forward_z',
                          'vertical_fov_radians','viewport_width','viewport_height','near_range'}) do
            if not finite(c[k]) then return false end
        end
        local norm=c.forward_x^2+c.forward_y^2+c.forward_z^2
        return math.abs(norm-1)<.01 and c.vertical_fov_radians>.01 and c.vertical_fov_radians<math.pi-.01
            and c.viewport_width>=1 and c.viewport_height>=1 and c.near_range>0
    end
    local function fresh(r,frame,now)
        return type(r)=='table' and r.verified==true and r.short_snapshot==true
            and r.world==world and type(r.unit)=='userdata'
            and r.validation_frame==frame and r.position_frame==frame
            and integer(r.sampled_frame,0,frame) and finite(r.sample_time) and finite(r.expires_at)
            and now>=r.sample_time and now<r.expires_at and r.expires_at-r.sample_time<=.080001
            and ((r.source=='network.rounds_remaining' and integer(r.rounds,0,999999)
                    and (r.metric_kind==nil or r.metric_kind=='ammo'))
                or (r.metric_kind=='heat' and r.source=='network.temperature'
                    and r.metric_value_verified==true and finite(r.metric_value) and r.metric_value>=0
                    and finite(r.metric_max) and r.metric_max>0 and r.metric_estimated==true)
                or (r.metric_kind=='lifetime' and r.source=='estimated.deployment_remaining'
                    and r.metric_value_verified==false and r.metric_estimated==true
                    and finite(r.metric_value) and r.metric_value>=0
                    and finite(r.metric_max) and r.metric_max>0 and r.metric_value<=r.metric_max))
    end
    local function valid_icon(r)
        local uv=icons[r.network_type] or model_icons[r.model_hash]
        return valid_uv(uv) and uv or nil
    end
    local function display_value(r,now)
        local kind=r.metric_kind or 'ammo'
        if kind=='heat' then
            local fraction=math.max(0,math.min(1,r.metric_value/r.metric_max))
            return string.format('%d%%',math.floor(fraction*100+.5)),fraction,kind
        elseif kind=='lifetime' then
            local seconds=math.ceil(r.metric_value)
            return string.format('%d:%02d',math.floor(seconds/60),seconds%60),
                math.max(0,math.min(1,r.metric_value/r.metric_max)),kind
        end
        local peak=peaks[r.unit]
        if not peak or peak.id~=r.network_goid or peak.session~=r.session or peak.kind~=r.network_type then
            peak={id=r.network_goid,session=r.session,kind=r.network_type,max=r.rounds}
            peaks[r.unit]=peak
        end
        peak.max=math.max(peak.max,r.rounds);peak.seen=now
        return string.format('%d',r.rounds),peak.max>0 and r.rounds/peak.max or 0,'ammo'
    end
    local function stage_rgb(kind,fraction)
        if kind=='heat' then
            -- A filling heat gauge becomes more urgent as overheat approaches.
            if fraction<=.25 then return 255,255,255
            elseif fraction<=.5 then return 102,210,95
            elseif fraction<=.75 then return 255,210,55
            else return 255,80,70 end
        end
        if fraction>.75 then return 255,255,255
        elseif fraction>.5 then return 102,210,95
        elseif fraction>.25 then return 255,210,55
        else return 255,80,70 end
    end
    local draw_report={}
    local function collect(c,records,frame,now)
        local labels,seen={},{}
        -- 每个型号每种结果每 10 秒说一次，用来定位「有记录却没画出来」。
        local function note(r,result,z,px,py)
            if type(ot.log)~='function' then return end
            local key=tostring(r.model_hash)..'/'..result
            local last=draw_report[key]
            if last and now>=last and now-last<10 then return end
            draw_report[key]=now
            local extra=''
            if finite(z) then
                extra=string.format(' z=%.2f',z)
                if finite(px) and finite(py) then extra=extra..string.format(' px=%.0f py=%.0f',px,py) end
            end
            pcall(ot.log,'SENTRY-DRAW %s model=%s type=%s%s',result,tostring(r.model_hash),
                tostring(r.network_type),extra)
        end
        local basis={M.to_elements(c.pose)}
        for i=1,9 do if not finite(basis[i]) then error('invalid camera basis') end end
        local factor=2*math.tan(c.vertical_fov_radians/2)/c.viewport_height
        for unit,p in pairs(peaks) do if now-p.seen>2 or now<p.seen then peaks[unit]=nil end end
        for i=1,math.min(type(records)=='table' and #records or 0,128) do
            local r=records[i]
            if not (fresh(r,frame,now) and not seen[r.unit] and finite(r.x) and finite(r.y) and finite(r.z)) then
                note(r,'drop_record')
            else
                local ok,alive=pcall(Unit.alive,r.unit)
                local wok,uw=false,nil
                if ok and alive==true then wok,uw=pcall(Unit.world,r.unit) end
                if not (wok and uw==world) then
                    note(r,'drop_unit')
                else
                    seen[r.unit]=true
                    local text,fraction,kind=display_value(r,now)
                    local x,y,z=r.x,r.y,r.z+head_offset+(lift_delta[r.network_type] or 0)
                    local dx,dy,dz=x-c.x,y-c.y,z-c.z
                    local depth=dx*c.forward_x+dy*c.forward_y+dz*c.forward_z
                    local distance2=dx*dx+dy*dy+dz*dz
                    if not (depth>c.near_range+.01 and distance2<=max_distance^2) then
                        note(r,depth>c.near_range+.01 and 'drop_far' or 'drop_behind',z)
                    else
                        local scale=depth*factor
                        local px=c.viewport_width/2+(dx*basis[1]+dy*basis[2]+dz*basis[3])/scale
                        local py=c.viewport_height/2+(dx*basis[7]+dy*basis[8]+dz*basis[9])/scale
                        local uv=valid_icon(r)
                        -- Projection keeps the world anchor fixed. Only layout size changes.
                        local distance=math.sqrt(distance2)
                        local distance_scale=math.max(1,near_scale*math.min(1,near_distance/distance))
                        -- Keep the selected range floor, but do not let it stop
                        -- the main text-size control from reducing the label.
                        local ui_scale=math.max(math.min(minimum_height,height)/height,
                            c.viewport_height/reference_height*distance_scale)
                        local column_width=bar_width+math.max(0,#text-4)*height*.7
                        local width=column_width+(uv and frame_size+icon_gap or 0)
                        local layout_height=math.max(height+bar_height+4,uv and frame_size or 0)
                        if px+width*ui_scale/2>=0 and px-width*ui_scale/2<=c.viewport_width
                            and py+layout_height*ui_scale>=0 and py<=c.viewport_height then
                            labels[#labels+1]={x=x,y=y,z=z,scale=scale*ui_scale,ui_scale=ui_scale,distance2=distance2,
                                text=text,uv=uv,width=width,column_width=column_width,index=i,
                                fraction=fraction,kind=kind}
                            note(r,'shown',z,px,py)
                        else
                            note(r,'drop_screen',z,px,py)
                        end
                    end
                end
            end
        end
        table.sort(labels,function(a,b)
            if a.distance2==b.distance2 then return a.index<b.index end
            return a.distance2<b.distance2
        end)
        return labels
    end
    local function measure(text)
        local found=extents[text]
        if found then return found end
        local lo,hi=Gui.text_extents(gui,text,font,32)
        local x0,y0=V2.to_elements(lo)
        local x1,y1=V2.to_elements(hi)
        if not finite(x0) or not finite(y0) or not finite(x1) or not finite(y1)
            or x1<=x0 or y1<=y0 then error('invalid font extents') end
        if extent_count>=256 then extents={};extent_count=0 end
        found={x0=x0,y0=y0,x1=x1,y1=y1};extents[text]=found;extent_count=extent_count+1
        return found
    end
    local function matrix(rotation,label,scale)
        local tm=M.identity()
        M.set_rotation(tm,rotation)
        M.set_scale(tm,V3(scale,scale,scale))
        M.set_translation(tm,V3(label.x,label.y,label.z))
        return tm
    end
    local function render(c,labels)
        if not font_ratio then
            local box=measure('0123456789')
            font_ratio=height/(box.y1-box.y0)
        end
        local rotation=M.rotation(c.pose)
        -- Scale companion ink too: zero text/bar opacity must not leave a
        -- floating shadow or dark track. Original defaults remain 85 and 65.
        local shadow=Color(math.floor(85*text_alpha/185+.5),0,0,0)
        local tint,track=Color(icon_alpha,255,255,255),Color(math.floor(65*bar_alpha/145+.5),43,43,43)
        local frame_tint=Color(frame_alpha,255,255,255)
        for _,l in ipairs(labels) do
            if drawn>=max_labels then break end
            local tm=matrix(rotation,l,l.scale)
            local left=-l.width/2
            local column=left+(l.uv and frame_size+icon_gap or 0)
            local bar_y=l.uv and math.max(0,(frame_size-height-bar_height-4)/2) or 0
            local red,green,blue=stage_rgb(l.kind,l.fraction)
            local foreground=Color(text_alpha,red,green,blue)
            local fill=Color(bar_alpha,red,green,blue)
            local cells_left=column
            local cell_width=(l.column_width-segment_gap*3)/4
            local function bar(x,w,layer,color)
                Gui.bitmap_3d_uv(gui,icon_material,V2(bar_uv.u0,bar_uv.v0),V2(bar_uv.u1,bar_uv.v1),
                    tm,V3(x,bar_y,0),layer,V2(w,bar_height),color)
                primitives=primitives+1
            end
            for segment=1,4 do
                local x=cells_left+(segment-1)*(cell_width+segment_gap)
                bar(x,cell_width,0,track)
                local fraction=math.max(0,math.min(1,l.fraction*4-(segment-1)))
                local fill_width=cell_width*fraction
                if fill_width*l.ui_scale>=.03125 then bar(x,fill_width,1,fill) end
            end
            if l.uv then
                -- Four short open corners; no filled plate behind the original artwork.
                Gui.bitmap_3d_uv(gui,icon_material,V2(frame_uv.u0,frame_uv.v0),V2(frame_uv.u1,frame_uv.v1),
                    tm,V3(left,0,0),0,V2(frame_size,frame_size),frame_tint)
                local uv=l.uv
                local padding=(frame_size-icon_size)/2
                Gui.bitmap_3d_uv(gui,icon_material,V2(uv.u0,uv.v0),V2(uv.u1,uv.v1),
                    tm,V3(left+padding,padding,0),1,V2(icon_size,icon_size),tint)
                primitives=primitives+2;icons_drawn=icons_drawn+1
            end
            local box=measure(l.text)
            local text_width=(box.x1-box.x0)*font_ratio
            local tx=column+(l.column_width-text_width)/2-box.x0*font_ratio
            local ty=bar_y+bar_height+4-box.y0*font_ratio
            local text_tm=matrix(rotation,l,l.scale*font_ratio)
            Gui.text_3d(gui,l.text,font,32,font_material,text_tm,
                V3((tx+.65)/font_ratio,(ty-.65)/font_ratio,0),2,shadow)
            Gui.text_3d(gui,l.text,font,32,font_material,text_tm,
                V3(tx/font_ratio,ty/font_ratio,0),3,foreground)
            primitives=primitives+2
            drawn=drawn+1
        end
    end
    local api={}
    -- Only visual scalars may change. Assets, depth materials, network reads,
    -- maximum record count, camera validation and GUI ownership stay fixed.
    local option_bounds={
        pixel_height={10,48},icon_size={12,64},
        text_alpha={0,255},icon_alpha={0,255},bar_alpha={0,255},frame_alpha={0,255},
        head_offset={0,10},near_scale={1,8},near_distance={1,30},
        min_pixel_height={8,40},frame_scale={1.1,2},
        bar_width={30,100},bar_height={1,6},max_distance={1,300},
    }
    function api.options()
        return {pixel_height=height,icon_size=icon_size,
            text_alpha=text_alpha,icon_alpha=icon_alpha,bar_alpha=bar_alpha,frame_alpha=frame_alpha,
            head_offset=head_offset,near_scale=near_scale,near_distance=near_distance,
            min_pixel_height=minimum_height,frame_scale=frame_scale,
            bar_width=bar_width,bar_height=bar_height,max_distance=max_distance}
    end
    function api.configure(values)
        if type(values)~='table' then return false,'visual_options_must_be_table' end
        local next_values=api.options()
        local changed=false
        -- Validate the complete request first. A malformed field cannot partly
        -- apply another field or cause a native call from the settings callback.
        for name,value in pairs(values) do
            local bounds=option_bounds[name]
            if not bounds then return false,'unknown_visual_option:'..tostring(name) end
            if not finite(value) then return false,'invalid_visual_option:'..name end
            local adjusted=math.max(bounds[1],math.min(bounds[2],value))
            if next_values[name]~=adjusted then changed=true end
            next_values[name]=adjusted
        end
        if not changed then return false end
        -- Font extents were measured at a fixed font size and remain valid;
        -- only their scalar ratio depends on the selected pixel height.
        if height~=next_values.pixel_height then font_ratio=nil end
        height,icon_size=next_values.pixel_height,next_values.icon_size
        text_alpha,icon_alpha=next_values.text_alpha,next_values.icon_alpha
        bar_alpha,frame_alpha=next_values.bar_alpha,next_values.frame_alpha
        head_offset=next_values.head_offset
        near_scale,near_distance=next_values.near_scale,next_values.near_distance
        minimum_height=next_values.min_pixel_height
        frame_scale=next_values.frame_scale
        frame_size=icon_size*frame_scale
        bar_width,bar_height=next_values.bar_width,next_values.bar_height
        max_distance=next_values.max_distance
        return true
    end
    function api.reset(next_world)
        dispose();world=next_world;disabled=false;logged={};peaks={}
        extents,extent_count,font_ratio={},0,nil;assets_ready=false
        drawn,eligible,primitives,icons_drawn=0,0,0,0
        reason=world and 'awaiting_verified_camera' or 'no_world'
    end
    function api.update(c,records)
        drawn,eligible,primitives,icons_drawn=0,0,0,0
        if disabled then return end
        if #missing>0 then reason='api_unavailable';log_once('api',table.concat(missing,', '));return end
        if not world then reason='no_world';return end
        if not live_world() then gui=nil;disabled=true;reason='world_no_longer_live';return end
        if gui then local ok,err=pcall(Gui.reset,gui);if not ok then stop(err);return end end
        local fok,frame=pcall(ot.frame);local tok,now=pcall(ot.time)
        if not fok or not integer(frame,0,9007199254740991) or not tok or not finite(now) then
            reason='time_unavailable';return
        end
        if not camera_valid(c,frame) then reason='awaiting_verified_camera';return end
        local ok,list=pcall(collect,c,records,frame,now)
        if not ok then stop(list);return end
        eligible=#list
        if eligible==0 then reason='awaiting_verified_ammo_in_range';return end
        if not check_assets() then return end
        if not gui then
            local made,new_gui=call('create_world_gui',function()
                return World.create_world_gui(world,M.identity(),1,1,'immediate')
            end)
            if not made or type(new_gui)~='userdata' then stop('create_world_gui: '..tostring(new_gui));return end
            gui=new_gui
        end
        local rendered,err=call('render',render,c,list)
        if not rendered then stop(err);return end
        reason='submitted_depth_gui'
    end
    function api.shutdown()
        dispose();world=nil;peaks={};disabled=false;assets_ready=false
        reason='shutdown';drawn,eligible,primitives,icons_drawn=0,0,0,0
    end
    function api.status()
        return {reason=reason,backend='world_gui_depth_materials',disabled=disabled,
            submitted=drawn,eligible=eligible,primitives=primitives,icons=icons_drawn,
            pixel_height=height,icon_size=icon_size,max_distance=max_distance,assets_ready=assets_ready,
            near_scale=near_scale,near_distance=near_distance,min_pixel_height=minimum_height,
            effective_min_pixel_height=math.min(minimum_height,height),
            reference_viewport_height=reference_height,frame_size=frame_size,frame_scale=frame_scale,
            text_alpha=text_alpha,icon_alpha=icon_alpha,bar_alpha=bar_alpha,frame_alpha=frame_alpha,
            head_offset=head_offset,bar_width=bar_width,bar_height=bar_height,
            bar_reference='estimated_highest_observed_sample',bar_segments=4,estimate_marker=false,
            bar_backend='textured_depth_bitmap',game_validated=false}
    end
    return api
end

end)()
local create_snapshot = (function()
-- Bounded display snapshot of verified identities and explicitly sourced metrics.
-- factory(ot,profile) -> capture(world,records), snapshot(world), clear(reason),
-- reset(world), shutdown(), status(). Full sampling is scheduled by the caller.
-- Samples expire after 80 ms; validation never renews their time/frame or reads
-- or predicts metrics. Every render validates world/session, Unit/model, GOID/type,
-- current 1 mm position agreement, and bidirectional uniqueness within the cohort.
-- New unrelated objects are detected by full sampling, not this bounded cache.
-- No full enumeration, setters, or retained native vectors.
return function(ot,profile)
    local S=ot.engine
    local App,Unit,Net,GS,V3=S.Application or {},S.Unit or {},S.Network or {},S.GameSession or {},S.Vector3 or {}
    local MAX_AGE,MAX_RECORDS,EPS2=0.08,128,0.001^2
    -- 结构类单位与网络对象坐标本来就是错开的（补给舱差半米），复核沿用同一套容差。
    local NEAR2=2.0^2
    -- 车载武器的对象坐标固定在落点、不跟车，位置校验对它们没有意义，
    -- 只靠 goid + 类型 + 模型核对。
    local VEHICLE_TYPE='6d745627'
    local function within(type_hash,a,b)
        if type_hash==VEHICLE_TYPE then return true end
        local dx,dy=a.x-b.x,a.y-b.y
        local dz=a.z-b.z
        return dx*dx+dy*dy+dz*dz<=NEAR2
    end
    local cache,world,session={},nil,nil
    local sampled_frame,sample_time,expires_at,last_frame,last_time=nil,nil,nil,nil,nil
    local reason,problem='empty',nil
    local captures,validations,dropped,returned=0,0,0,0
    local types,metric_specs={},{}
    local metric_dropped,last_metric_drop=0,nil
    local function finite(v) return type(v)=='number' and v==v and v>-math.huge and v<math.huge end
    local function integer(v,lo,hi) return finite(v) and v%1==0 and v>=lo and v<=hi end
    local function hex(v,width,pad)
        if type(v)~='string' then return nil end
        v=v:lower():gsub('^0x','')
        if not v:match('^%x+$') or #v>width then return nil end
        if pad then return string.rep('0',width-#v)..v end
        v=v:gsub('^0+','');return v~='' and v or '0'
    end
    local function call(fn,...)
        if type(fn)~='function' then return false,nil end
        return pcall(fn,...)
    end
    local required={App.main_world,App.worlds,Unit.alive,Unit.world,Unit.debug_name,
        Unit.world_position,Net.game_session,GS.game_object_exists,GS.game_object_is_type,
        GS.game_object_field,V3.to_elements,ot.frame,ot.time}
    for i=1,13 do if type(required[i])~='function' then problem='snapshot_api_unavailable' end end
    if type(profile)=='table' and type(profile.types)=='table' then
        for _,entry in ipairs(profile.types) do
            if type(entry)=='table' then
                local th=hex(entry.type_hash,8,true)
                if th then metric_specs[th]=entry end
            end
        end
    end
    if type(profile)~='table' or type(profile.bindings)~='table' or #profile.bindings==0 then
        problem='snapshot_bindings_unavailable'
    else
        for _,b in ipairs(profile.bindings) do
            local model=type(b)=='table' and hex(b.model_hash,16,false)
            local th=type(b)=='table' and hex(b.type_hash,8,true)
            -- 位置字段可以整条缺省：这类 schema 里没有 c{i(uTh>，由身份模块按唯一对象认领。
            local has_position=b.position_field~=nil or b.position_slot~=nil
            if not model or not th or type(b.call)~='string' or #b.call==0
                or type(b.fields)~='table' or #b.fields<1
                or (has_position and (type(b.position_field)~='string' or #b.position_field==0
                    or hex(b.position_field_hash,8,true)~='9a9b14c4'
                    or not integer(b.position_slot,1,#b.fields)
                    or hex(b.fields[b.position_slot],8,true)~='9a9b14c4')) then
                problem='snapshot_invalid_binding';break
            end
            local spec=metric_specs[th] or b
            local metric_kind=spec.metric_kind or 'ammo'
            if (b.metric_kind~=nil and b.metric_kind~=metric_kind)
                or (metric_kind~='ammo' and metric_kind~='heat' and metric_kind~='lifetime')
                or (metric_kind=='heat' and (not finite(spec.static_heat_threshold) or spec.static_heat_threshold<=0
                    or not finite(spec.schema_min) or not finite(spec.schema_max) or spec.schema_min>spec.schema_max))
                or (metric_kind=='lifetime' and (not finite(spec.estimated_lifetime_seconds) or spec.estimated_lifetime_seconds<=0)) then
                problem='snapshot_invalid_metric_binding';break
            end
            local kind=types[th]
            if kind and (kind.call~=b.call or kind.position_field~=b.position_field or kind.metric_kind~=metric_kind) then
                problem='snapshot_conflicting_binding';break
            end
            if not kind then
                kind={call=b.call,position_field=b.position_field,models={},metric_kind=metric_kind,
                    metric_min=spec.schema_min,metric_max=spec.schema_max,
                    heat_threshold=spec.static_heat_threshold,lifetime_seconds=spec.estimated_lifetime_seconds}
                types[th]=kind
            end
            kind.models[model]=true
        end
    end
    local api={}
    function api.clear(why)
        cache,session={},nil
        sampled_frame,sample_time,expires_at=nil,nil,nil
        returned=0;last_metric_drop=nil;reason=why or 'cleared'
    end
    function api.reset(next_world)
        api.clear(next_world and 'awaiting_capture' or 'no_world')
        world=next_world;last_frame,last_time=nil,nil
    end
    local function reject(why) api.clear(why);return nil,why end
    local function context(requested_world)
        if problem then return reject(problem) end
        local fok,frame=call(ot.frame);local tok,now=call(ot.time)
        if not fok or not integer(frame,0,9007199254740991) or not tok or not finite(now) then
            return reject('snapshot_time_unavailable')
        end
        if (last_frame and frame<last_frame) or (last_time and now<last_time) then
            last_frame,last_time=frame,now;return reject('snapshot_time_reversed')
        end
        last_frame,last_time=frame,now
        if requested_world==nil then return reject('snapshot_no_world') end
        local mok,main=call(App.main_world)
        local wok,worlds=call(App.worlds)
        local live=false
        if wok and type(worlds)=='table' then
            for _,w in pairs(worlds) do if w==requested_world then live=true;break end end
        end
        if not mok or main~=requested_world or not live then return reject('snapshot_world_unavailable') end
        local sok,current=call(Net.game_session)
        if not sok or current==nil then return reject('snapshot_session_unavailable') end
        return {frame=frame,now=now,session=current,world=requested_world}
    end
    local function unit_model(unit,expected_world)
        -- Never send a destroyed Unit to a later API.
        local aok,alive=call(Unit.alive,unit)
        if not aok or alive~=true then return nil,'snapshot_unit_destroyed' end
        local wok,uw=call(Unit.world,unit)
        if not wok or uw~=expected_world then return nil,'snapshot_unit_world_changed' end
        local nok,name=call(Unit.debug_name,unit)
        local model=nok and type(name)=='string' and name:match('#ID%[(%x+)%]')
        if not model then return nil,'snapshot_unit_model_unavailable' end
        return hex(model,16,false)
    end
    local function auxiliary(value)
        if type(value)~='table' then return nil end
        local out={}
        for _,key in ipairs({'magazines_remaining','chamber_empty','is_overheated'}) do
            local v=value[key]
            if type(v)=='boolean' or finite(v) then out[key]=v end
        end
        return out
    end
    -- Only these scalar fields cross the sampling/render boundary. No mutable
    -- metric tables or native vectors are retained, and no renderer time is used
    -- to recalculate a value. Legacy ammo records remain valid without metadata.
    local metric_keys={'metric_kind','metric_value','metric_value_verified','metric_max',
        'metric_max_source','metric_max_verified','metric_max_sampled_frame','metric_estimated',
        'metric_estimate_basis','temperature','static_heat_threshold','first_verified_observation_time'}
    local function metric_record(r,kind,ctx)
        local mk=r.metric_kind or 'ammo'
        if mk~=kind.metric_kind then return nil end
        if mk=='ammo' then
            if r.source~='network.rounds_remaining' or not integer(r.rounds,0,2147483647)
                or (r.metric_value~=nil and r.metric_value~=r.rounds)
                or (r.metric_value_verified~=nil and r.metric_value_verified~=true)
                or r.metric_max~=nil or r.metric_max_source~=nil or r.metric_max_sampled_frame~=nil
                or (r.metric_max_verified~=nil and r.metric_max_verified~=false)
                or (r.metric_estimated~=nil and r.metric_estimated~=false)
                or r.metric_estimate_basis~=nil or r.temperature~=nil
                or r.static_heat_threshold~=nil or r.first_verified_observation_time~=nil then return nil end
            return {metric_kind='ammo',metric_value=r.rounds,metric_value_verified=true,metric_max_verified=false}
        end
        if r.rounds~=nil or not finite(r.metric_value) or r.metric_value<0
            or r.metric_max_verified~=false or r.metric_estimated~=true
            or r.metric_max_sampled_frame~=ctx.frame then return nil end
        if mk=='heat' then
            if r.source~='network.temperature' or r.metric_value_verified~=true
                or r.metric_value<kind.metric_min or r.metric_value>kind.metric_max
                or r.metric_max~=kind.heat_threshold or r.metric_max_source~='static_overheat_threshold'
                or r.metric_estimate_basis~='static_overheat_threshold'
                or (r.temperature~=nil and r.temperature~=r.metric_value)
                or (r.static_heat_threshold~=nil and r.static_heat_threshold~=kind.heat_threshold)
                or r.first_verified_observation_time~=nil then return nil end
        else
            if r.source~='estimated.deployment_remaining' or r.metric_value_verified~=false
                or r.metric_max~=kind.lifetime_seconds or r.metric_value>kind.lifetime_seconds
                or r.metric_max_source~='static_lifetime' or r.metric_estimate_basis~='first_verified_observation'
                or not finite(r.first_verified_observation_time) or r.first_verified_observation_time>ctx.now
                or r.temperature~=nil or r.static_heat_threshold~=nil then return nil end
        end
        local out={}
        for _,key in ipairs(metric_keys) do out[key]=r[key] end
        return out
    end
    local function copy_record(r)
        local out={verified=true,unit=r.unit,world=r.world,rounds=r.rounds,
            sampled_frame=r.sampled_frame,sample_time=r.sample_time,expires_at=r.expires_at,
            session=r.session,model_hash=r.model_hash,network_goid=r.network_goid,network_type=r.network_type,
            source=r.source,schema_sha256=r.schema_sha256,identity_basis=r.identity_basis,
            identity_inferred=r.identity_inferred,auxiliary_sample_frame=r.auxiliary_sample_frame,
            auxiliary=auxiliary(r.auxiliary)}
        for _,key in ipairs(metric_keys) do out[key]=r[key] end
        return out
    end
    function api.capture(next_world,records)
        local ctx,why=context(next_world)
        if not ctx then return false,why end
        if type(records)~='table' or #records>MAX_RECORDS then return reject('snapshot_invalid_records') end
        local count=0
        for index in pairs(records) do
            count=count+1
            if not integer(index,1,#records) then return reject('snapshot_sparse_records') end
        end
        if count~=#records then return reject('snapshot_sparse_records') end
        if #records==0 then api.clear('snapshot_empty_source');world=next_world;return true end
        local next_cache,units,ids={},{},{}
        last_metric_drop=nil
        -- 单条记录不合格只丢这一条：一处配对或类型写错不该让所有型号一起没有弹药条。
        local function convert(r)
            if type(r)~='table' or r.verified~=true or r.short_snapshot==true
                or r.sampled_frame~=ctx.frame or r.world~=next_world or type(r.unit)~='userdata'
                or not integer(r.network_goid,0,2147483647)
                or r.network_goid==32767 or not finite(r.x) or not finite(r.y) or not finite(r.z)
                or (r.session~=nil and r.session~=ctx.session)
                or (profile.schema_sha256~=nil and r.schema_sha256~=profile.schema_sha256) then
                return nil,'snapshot_source_not_current_network_sample'
            end
            local model,err=unit_model(r.unit,next_world)
            if not model then return nil,err end
            local th=hex(r.network_type,8,true);local kind=types[th]
            if not kind or not kind.models[model] or (r.model_hash~=nil and hex(r.model_hash,16,false)~=model) then
                return nil,'snapshot_model_type_mismatch'
            end
            if units[r.unit] or ids[r.network_goid] then return nil,'snapshot_duplicate_source_identity' end
            local metric=metric_record(r,kind,ctx)
            if not metric then return nil,'snapshot_invalid_metric' end
            units[r.unit],ids[r.network_goid]=true,true
            local saved=copy_record(r)
            for _,key in ipairs(metric_keys) do saved[key]=metric[key] end
            saved.model_hash,saved.network_type,saved.session=model,th,ctx.session
            saved.sample_time,saved.expires_at=ctx.now,ctx.now+MAX_AGE
            return saved
        end
        for _,r in ipairs(records) do
            local saved,skip=convert(r)
            if saved then next_cache[#next_cache+1]=saved
            else
                dropped=dropped+1;metric_dropped=metric_dropped+1
                last_metric_drop=last_metric_drop or skip
            end
        end
        cache,world,session=next_cache,next_world,ctx.session
        sampled_frame,sample_time,expires_at=ctx.frame,ctx.now,ctx.now+MAX_AGE
        captures=captures+1;reason=last_metric_drop or 'captured';returned=0
        return true
    end
    local function xyz(fn,...)
        local ok,p=call(fn,...)
        if not ok or p==nil then return nil end
        local vok,x,y,z=call(V3.to_elements,p)
        if vok and finite(x) and finite(y) and finite(z) then return {x=x,y=y,z=z} end
    end
    -- 类型相关的距离判定统一走 within（见上）。


    local function validate(r,ctx)
        local model,why=unit_model(r.unit,ctx.world)
        if not model then return nil,why end
        if model~=r.model_hash then return nil,'snapshot_unit_model_changed' end
        local kind=types[r.network_type]
        local eok,exists=call(GS.game_object_exists,ctx.session,r.network_goid)
        if not eok or exists~=true then return nil,'snapshot_object_gone' end
        local tok,match=call(GS.game_object_is_type,ctx.session,r.network_goid,kind.call)
        if not tok or match~=true then return nil,'snapshot_object_type_changed' end
        local up=xyz(Unit.world_position,r.unit,1)
        if not up then return nil,'snapshot_position_unavailable' end
        -- 没有位置字段的类型只能靠 goid 与类型核对；有位置字段的允许装配偏移。
        local np=nil
        if kind.position_field then
            np=xyz(GS.game_object_field,ctx.session,r.network_goid,kind.position_field)
            if not np then return nil,'snapshot_position_unavailable' end
        end
        local matches=not kind.position_field
            or within(kind.type_hash,up,np)
        return {record=r,unit_position=up,network_position=np or up,kind=kind,pair_matches=matches}
    end
    function api.snapshot(requested_world)
        validations=validations+1;returned=0
        local ctx,why=context(requested_world)
        if not ctx then return {},why end
        if requested_world~=world then api.clear('snapshot_world_changed');return {},reason end
        if #cache==0 then return {},reason end
        if ctx.session~=session then api.clear('snapshot_session_changed');return {},reason end
        if ctx.frame<sampled_frame or ctx.now<sample_time then api.clear('snapshot_time_reversed');return {},reason end
        if ctx.now>=expires_at then api.clear('snapshot_expired');return {},reason end
        local valid,first_drop={},nil
        for _,r in ipairs(cache) do
            local item,err=validate(r,ctx)
            if item then valid[#valid+1]=item else dropped=dropped+1;first_drop=first_drop or err end
        end
        -- Drop every ambiguous participant; never redirect a saved Unit to a
        -- different GOID, even when the pair would match its current position.
        local unit_counts,id_counts={},{}
        for i,u in ipairs(valid) do
            for j,n in ipairs(valid) do
                if n.kind.models[u.record.model_hash]
                    and within(n.kind.type_hash,u.unit_position,n.network_position) then
                    unit_counts[i]=(unit_counts[i] or 0)+1;id_counts[j]=(id_counts[j] or 0)+1
                end
            end
        end
        local retained,out={},{}
        for i,item in ipairs(valid) do
            if item.pair_matches and unit_counts[i]==1 and id_counts[i]==1 then
                local saved=item.record;retained[#retained+1]=saved
                local r=copy_record(saved)
                r.x,r.y,r.z=item.unit_position.x,item.unit_position.y,item.unit_position.z
                r.validation_frame,r.position_frame=ctx.frame,ctx.frame
                r.short_snapshot,r.snapshot_age,r.max_age_seconds=true,ctx.now-r.sample_time,MAX_AGE
                out[#out+1]=r
            else dropped=dropped+1;first_drop=first_drop or (not item.pair_matches and 'snapshot_position_mismatch' or 'snapshot_ambiguous_cohort') end
        end
        cache=retained;returned=#out
        reason=first_drop or (#out>0 and 'validated_short_snapshot' or 'snapshot_empty')
        return out,reason
    end
    function api.shutdown() api.reset(nil);reason='shutdown' end
    function api.status()
        return {reason=reason,records=#cache,returned=returned,captures=captures,validations=validations,
            dropped=dropped,metric_dropped=metric_dropped,last_metric_drop=last_metric_drop,sampled_frame=sampled_frame,sample_time=sample_time,expires_at=expires_at,
            validation_frame=last_frame,max_age_seconds=MAX_AGE,max_records=MAX_RECORDS,
            identity_basis='retained_sample_with_current_identity_and_cohort_checks'}
    end
    return api
end

end)()
local network_profile = (function()
-- Generated from this install's network_config and EntitySettings/UnitComponentData.
-- String aliases use normal engine ID32 names, with full live schema checks.
-- The high precision Vector3 field's position semantics require live validation.
-- Laser temperature is raw; its 250 normalization threshold is explicitly estimated.
-- Tesla lifetime is an explicit 150s estimate from first verified observation, not birth.
return {
    steam_build='26088801',
    schema_sha256='1c49360016a54b682cc3604a9fb10aef4d97a189529a4c485d92454102ffebed',
    entity_data_sha256='c27d9d350b6f6138f9a040266d98958c8a755ef1b77e965d669c7b263178e15b',
    typelib_sha256='6cc78f1a8323d82cc319ba3f378b0dbf692f8ede3703cd0873f7e212ce11a18a',
    field_name='rounds_remaining', field_hash='d7a5d63e',
    models={['2b11c9e4980ec479']=true,['37079568dc86e9c6']=true,['37cde43876ba26bb']=true,['51a0812e3bce2d74']=true,['56070f36cfffa8a8']=true,['61d9dda22a59e9bc']=true,['74599e56f72f9d7e']=true,['820cc3bafe962858']=true,['cbfe1fc7158347f3']=true,['e977c49db7604f9']=true},
    model_resources={['2b11c9e4980ec479']='content/fac_helldivers/hellpod/antitank_emplacement/antitank_emplacement',['37079568dc86e9c6']='content/fac_helldivers/hellpod/rocket_turret/rocket_turret',['37cde43876ba26bb']='content/fac_helldivers/hellpod/turret_machinegun_gpmg/turret_machinegun_gpmg',['51a0812e3bce2d74']='content/fac_helldivers/hellpod/mortar_turret/mortar_turret',['56070f36cfffa8a8']='content/fac_helldivers/hellpod/laser_cannon_turret/laser_cannon_turret',['61d9dda22a59e9bc']='content/fac_helldivers/hellpod/turret/turret',['74599e56f72f9d7e']='content/fac_helldivers/hellpod/tesla_turret/tesla_turret',['820cc3bafe962858']='content/fac_helldivers/hellpod/flamethrower_turret/flamethrower_turret',['cbfe1fc7158347f3']='content/fac_helldivers/hellpod/aa_turret/aa_turret',['e977c49db7604f9']='content/fac_helldivers/hellpod/manned_turret/manned_turret'},
    types={
        {type_hash='0162dd9e',call='zdZ~yhg!',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700','5e5865a0'}},
        {type_hash='13a71430',call='Es}/~kND',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='14c9c273',call='i1j-1MHz',metric_kind='lifetime',estimated_lifetime_seconds=150,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','7030c54e'}},
        {type_hash='2363e364',call='T[tD_-{~',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='2596fa0a',call='N+d%Kq$1',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf'}},
        {type_hash='3416fff4',call='4%EXhX(8',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='3ae02f80',call='mZ(LD/!&',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','7030c54e'}},
        {type_hash='3bd04c18',call='941ND^v~',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','67526982','31182caf','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3','c4c9b9ef'}},
        {type_hash='3c709c50',call='):e&;~2_',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {type_hash='6d3f8345',call='oYVe)p+i',rounds_slot=31,fields={'50ac6619','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {type_hash='947ed108',call='JMR4x`Ao',rounds_slot=35,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3'}},
        {type_hash='992ff297',call='G`Y1$voS',rounds_slot=37,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3'}},
        {type_hash='9d474323',call='Og,VB9b3',metric_kind='heat',metric_slot=41,schema_bits=7,schema_min=0,schema_max=4000,static_heat_threshold=250,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','67526982','31182caf','ec64918b','a5023836','f6275c53','88b7cf6d'}},
        {type_hash='9fdd94c0',call='}>|mEsa.',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='a37f0894',call='$Leg(yqP',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='b551b138',call='ok$F9qrS',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {type_hash='be95e875',call='9I~t$THc',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='bf35608c',call='Mm{:#c*%',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','67526982','31182caf','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3','c4c9b9ef'}},
        {type_hash='ec516bbd',call='<j9*h3.S',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {type_hash='ee956607',call='<3hkntZ?',rounds_slot=37,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3'}},
        {type_hash='ef108792',call='tXoU]Yb{',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {type_hash='f1dbc135',call='h:(x&X8#',rounds_slot=38,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','c20428c6','6d1d5cfb','67526982','31182caf','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3','c4c9b9ef'}},
        {type_hash='f53d4725',call='f$XD&/}6',rounds_slot=39,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
    },
    bindings={
        {model_hash='61d9dda22a59e9bc',type_hash='3c709c50',call='):e&;~2_',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {model_hash='51a0812e3bce2d74',type_hash='3416fff4',call='4%EXhX(8',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='61d9dda22a59e9bc',type_hash='ef108792',call='tXoU]Yb{',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {model_hash='51a0812e3bce2d74',type_hash='ec516bbd',call='<j9*h3.S',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='61d9dda22a59e9bc',type_hash='6d3f8345',call='oYVe)p+i',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=3,position_semantics_verified=false,fields={'50ac6619','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {model_hash='37079568dc86e9c6',type_hash='be95e875',call='9I~t$THc',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='e977c49db7604f9',type_hash='f1dbc135',call='h:(x&X8#',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','c20428c6','6d1d5cfb','67526982','31182caf','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3','c4c9b9ef'}},
        {model_hash='56070f36cfffa8a8',type_hash='9d474323',call='Og,VB9b3',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','67526982','31182caf','ec64918b','a5023836','f6275c53','88b7cf6d'}},
        {model_hash='e977c49db7604f9',type_hash='bf35608c',call='Mm{:#c*%',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','67526982','31182caf','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3','c4c9b9ef'}},
        {model_hash='e977c49db7604f9',type_hash='992ff297',call='G`Y1$voS',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3'}},
        {model_hash='37cde43876ba26bb',type_hash='13a71430',call='Es}/~kND',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='e977c49db7604f9',type_hash='947ed108',call='JMR4x`Ao',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3'}},
        {model_hash='cbfe1fc7158347f3',type_hash='9fdd94c0',call='}>|mEsa.',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='cbfe1fc7158347f3',type_hash='a37f0894',call='$Leg(yqP',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='820cc3bafe962858',type_hash='3ae02f80',call='mZ(LD/!&',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','7030c54e'}},
        {model_hash='61d9dda22a59e9bc',type_hash='b551b138',call='ok$F9qrS',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700'}},
        {model_hash='37cde43876ba26bb',type_hash='f53d4725',call='f$XD&/}6',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='51a0812e3bce2d74',type_hash='2363e364',call='T[tD_-{~',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'}},
        {model_hash='61d9dda22a59e9bc',type_hash='2596fa0a',call='N+d%Kq$1',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf'}},
        {model_hash='2b11c9e4980ec479',type_hash='3bd04c18',call='941ND^v~',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','67526982','31182caf','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3','c4c9b9ef'}},
        {model_hash='e977c49db7604f9',type_hash='ee956607',call='<3hkntZ?',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','3132608f','788b9753','99901914','1dd07f05','39d141d6','ac08fe53','7a4bdbbe','5dd8181b','b6347304','d7082cbf','6d2d83f8','7d9563dd','d8819b3e','49c250a6','31f86165','09d8bef4','6f3ab277','4cbcc2a2','7030c54e','95c46304','ec64918b','d7a5d63e','4a893e74','6fa852e3'}},
        {model_hash='61d9dda22a59e9bc',type_hash='0162dd9e',call='zdZ~yhg!',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','b741b700','5e5865a0'}},
        {model_hash='74599e56f72f9d7e',type_hash='14c9c273',call='i1j-1MHz',position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=10,position_semantics_verified=false,fields={'50ac6619','dab52fe8','0565f571','169d5ed7','82830aed','cad5b537','9ddf0683','95417727','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','c4c9b9ef','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','7030c54e'}},
    },
}

end)()

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
    alias('51a0812e3bce2d74', '299c0d3dfd2f0994', 'content/fac_helldivers/hellpod/mortar_turret/mortar_turret_gas')
    alias('51a0812e3bce2d74', 'b2053a1838092f8b', 'content/fac_helldivers/hellpod/mortar_turret/mortar_turret_staticfield')
    local function late_type(hash,call,rounds_slot,fields)
        P.types[#P.types+1]={type_hash=hash,call=call,rounds_slot=rounds_slot,fields=fields}
        return P.types[#P.types]
    end
    local t_17db8107=late_type('17db8107','!!!H[UA2',24,{'50ac6619','8bbeb160','20605662','2d27198c','98b4aaf0','c336a25c','3132608f','67526982','31182caf','49c250a6','31f86165','09d8bef4','c20428c6','6d1d5cfb','788b9753','99901914','1dd07f05','39d141d6','c3a3f66c','6f3ab277','4cbcc2a2','7030c54e','ec64918b','d7a5d63e','4a893e74','cd889dbc'})
    local t_4e64c4dc=late_type('4e64c4dc','!!!:^yT5',31,{'50ac6619','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e'})
    local t_6d745627=late_type('6d745627','!!!yz6.F',31,{'50ac6619','98b4aaf0','9a9b14c4','c336a25c','3ccffa7f','d22b948c','c20428c6','6d1d5cfb','a4c332a8','9bbb856e','b897e2d8','a1659a75','d5a66654','d7082cbf','ac08fe53','7a4bdbbe','5dd8181b','b6347304','078c5d3b','60e13e87','e0a79052','9611c029','cc0b45e5','9d5d6cc7','49c250a6','31f86165','09d8bef4','3132608f','95c46304','ec64918b','d7a5d63e','4a893e74','67526982','31182caf','6f3ab277','4cbcc2a2','7030c54e','cd889dbc'})
    P.bindings[#P.bindings+1]={model_hash='2eecd0b1fa49630',type_hash=t_17db8107.type_hash,call=t_17db8107.call,fields=t_17db8107.fields}
    P.bindings[#P.bindings+1]={model_hash='73681ffd58fa1a90',type_hash=t_4e64c4dc.type_hash,call=t_4e64c4dc.call,fields=t_4e64c4dc.fields,position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=3}
    P.bindings[#P.bindings+1]={model_hash='9b2140378640432e',type_hash=t_6d745627.type_hash,call=t_6d745627.call,fields=t_6d745627.fields,position_field='c{i(uTh>',position_field_hash='9a9b14c4',position_slot=3}
    bind_all('29ae906ea485a7fa', 'content/fac_helldivers/hellpod/expandable_cover/expandable_cover')
    bind_all('2eecd0b1fa49630', 'content/fac_helldivers/equipment/support_weapons/grenade_launcher/grenade_launcher')
    bind_all('73681ffd58fa1a90', 'content/fac_helldivers/hellpod/ammo_rack_mounted_turret/ammo_rack_mounted_turret')
    bind_all('85c1edb038ec24e', 'content/fac_helldivers/vehicles/frv/armaments/frv_mg/frv_mg')
    bind_all('444880f62ca7eae8', 'content/fac_helldivers/vehicles/frv_heavy/armaments/frv_flamethrower/frv_flamethrower')
    P.models['9b2140378640432e']=true
    P.model_resources['9b2140378640432e']='content/fac_helldivers/vehicles/frv_supply/frv_supply'
    P.models['583ecde0d7718126']=true
    P.model_resources['583ecde0d7718126']='content/fac_helldivers/hellpod/hellpod/hellpod'
end

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
local create_visual_settings = (function()
-- Optional MDL3 menu bridge. The sentry's existing loader owns rendering.
-- MDL's ModOptionsMenu provider owns persistence; this module only polls
-- validated visual scalars. No callbacks are registered or retained by MDL.
local PREFIX='sentry_ammo.visual.'
local MOD_ID='sentry_ammo_overhead'
local MOD_TITLE='炮塔头顶弹药显示'
local POLL_SECONDS=0.5

local function finite(v)
    return type(v)=='number' and v==v and v>-math.huge and v<math.huge
end

local DEFINITIONS={
    {key='pixel_height',label='数字基础字号（像素）',min=10,max=48,step=1,default=24,
        description='设置数字的基础高度；近距离放大和远距离最小字号会共同影响最终大小。'},
    {key='icon_size',label='战备图标大小（像素）',min=12,max=64,step=1,default=36,
        description='调整炮塔战备图标大小，保留游戏原有图案和颜色。'},
    {key='text_alpha',label='数字不透明度（%）',min=0,max=100,step=0.1,default=185,alpha=true,gap=true,
        description='0%完全透明，100%完全不透明。'},
    {key='icon_alpha',label='图标不透明度（%）',min=0,max=100,step=0.1,default=145,alpha=true,
        description='0%完全透明，100%完全不透明。'},
    {key='bar_alpha',label='弹药条不透明度（%）',min=0,max=100,step=0.1,default=145,alpha=true,
        description='同时调整四格弹药条和底轨的不透明度。'},
    {key='frame_alpha',label='装饰框不透明度（%）',min=0,max=100,step=0.1,default=90,alpha=true,
        description='调整战备图标周围装饰框的不透明度。'},
    {key='head_offset',label='头顶显示高度（米）',min=0,max=10,step=0.1,default=2.5,gap=true,
        description='调整整组显示相对炮塔位置的高度。'},
    {key='near_scale',label='近距离放大倍数',min=1,max=8,step=0.1,default=4.5,
        description='近距离最大放大倍数；1表示不额外放大。'},
    {key='near_distance',label='近距离放大范围（米）',min=1,max=30,step=1,default=8,
        description='此距离内使用最大放大倍数，超出后逐渐缩小。'},
    {key='min_pixel_height',label='远距离最小字号（像素）',min=8,max=40,step=1,default=22,
        description='保持远处数字可读；实际最小字号不超过数字基础字号。'},
    {key='frame_scale',label='装饰框相对大小',min=1.1,max=2,step=0.1,default=1.4,gap=true,
        description='相对于战备图标的大小倍数。'},
    {key='bar_width',label='弹药条宽度（像素）',min=30,max=100,step=1,default=64,
        description='调整四格弹药条的总宽度。'},
    {key='bar_height',label='弹药条高度（像素）',min=1,max=6,step=1,default=3,
        description='调整四格弹药条的高度。'},
    {key='max_distance',label='最远显示距离（米）',min=1,max=300,step=1,default=120,gap=true,
        description='超过此距离不显示炮塔弹药；增大可能增加显示数量和开销。'},
}

local function snap(def,value)
    local steps=math.floor((value-def.min)/def.step+0.5)
    return math.max(def.min,math.min(def.max,def.min+steps*def.step))
end

local function ui_default(def,defaults)
    local value=defaults[def.key]
    local lo,hi=def.min,def.max
    if def.alpha then lo,hi=0,255 end
    if not finite(value) or value<lo or value>hi then value=def.default end
    if def.alpha then value=value*100/255 end
    return snap(def,value)
end

local function renderer_value(def,value)
    if not finite(value) or value<def.min or value>def.max then return nil end
    -- Reject malformed API values instead of silently changing the saved value.
    local adjusted=snap(def,value)
    if math.abs(adjusted-value)>1e-5 then return nil end
    if def.alpha then return math.floor(adjusted*255/100+0.5) end
    return adjusted
end

return function(ot,labels,defaults)
    ot=type(ot)=='table' and ot or {}
    defaults=type(defaults)=='table' and defaults or {}
    local api={}
    local stopped=false
    local elapsed=POLL_SECONDS
    local current_menu=nil
    local applied={}
    local logged={}
    local states=setmetatable({},{__mode='k'})
    local reason='awaiting_mdl3'
    local registration_count=0
    local specs={}
    for index,def in ipairs(DEFINITIONS) do
        specs[index]={type='slider',label=def.label,mod=MOD_TITLE,mod_id=MOD_ID,
            description=def.description,min=def.min,max=def.max,step=def.step,
            default=ui_default(def,defaults),gap=def.gap==true}
    end

    local function log_once(key,message)
        if logged[key] then return end
        logged[key]=true
        if type(ot.log)=='function' then pcall(ot.log,'SENTRY-SETTINGS %s',message) end
    end

    local function lookup()
        if type(ot.options_menu)~='function' then return nil end
        local ok,menu=pcall(ot.options_menu)
        if not ok or type(menu)~='table' or menu.provider~='MDL' then return nil end
        if type(menu.register_option)~='function' or type(menu.get)~='function' then return nil end
        if type(menu.ready)=='function' then
            local ready_ok,ready=pcall(menu.ready)
            if not ready_ok or not ready then return nil end
        end
        return menu
    end

    local function poll()
        local menu=lookup()
        if not menu then
            current_menu=nil
            reason='awaiting_mdl3'
            return
        end
        local state=states[menu]
        if not state then
            state={registered={},failed={}}
            states[menu]=state
            for index,def in ipairs(DEFINITIONS) do
                local id=PREFIX..def.key
                local ok,registered,err=pcall(menu.register_option,id,specs[index])
                if ok and registered==true then
                    state.registered[index]=true
                    registration_count=registration_count+1
                else
                    state.failed[index]=true
                    log_once('register:'..id,'option registration failed: '..id..' ('..tostring(ok and err or registered)..')')
                end
            end
        end
        if current_menu~=menu then
            current_menu=menu
            applied={}
        end
        local patch={}
        local has_changes=false
        local invalid=false
        for index,def in ipairs(DEFINITIONS) do
            if state.registered[index] then
                local id=PREFIX..def.key
                local ok,value=pcall(menu.get,id)
                value=ok and renderer_value(def,value) or nil
                if value==nil then
                    invalid=true
                    log_once('invalid:'..id,'invalid stored visual value ignored: '..id)
                elseif applied[def.key]~=value then
                    patch[def.key]=value
                    has_changes=true
                end
            end
        end
        reason=next(state.failed) and 'partial_registration' or invalid and 'invalid_option' or 'ready'
        if has_changes then
            if type(labels)~='table' or type(labels.configure)~='function' then
                reason='renderer_unavailable'
                return
            end
            local ok,changed,err=pcall(labels.configure,patch)
            if not ok or changed==false and err~=nil then
                reason='configure_failed'
                log_once('configure','visual settings update failed: '..tostring(ok and err or changed))
                return
            end
            for key,value in pairs(patch) do applied[key]=value end
        end
        if reason=='ready' then
            log_once('ready','MDL3 visual options ready; 14 settings; persistence provided by MDL3')
        end
    end

    function api.update(dt)
        if stopped then return end
        if not finite(dt) or dt<0 then dt=0 end
        elapsed=elapsed+math.min(dt,POLL_SECONDS)
        if elapsed<POLL_SECONDS then return end
        elapsed=0
        poll()
    end

    function api.shutdown()
        stopped=true
        current_menu=nil
        states=setmetatable({},{__mode='k'})
        applied={}
        reason='stopped'
    end

    function api.status()
        return {reason=reason,registered=registration_count,option_count=#DEFINITIONS,
            poll_seconds=POLL_SECONDS,provider='MDL',active=current_menu~=nil and not stopped}
    end
    return api
end
end)()

local visual_profile = (function()
-- Original game icon artwork/colors; exact stratagem payload to network type mapping.
-- Unmapped variants deliberately omit the icon rather than borrow a wrong one.
return {
    pixel_height=24, icon_size=36, bar_width=64, bar_height=3,
    near_scale=4.5, near_distance=8, reference_viewport_height=1600, min_pixel_height=22,
    frame_scale=1.4, frame_alpha=90,
    text_alpha=185, icon_alpha=145, bar_alpha=145,
    max_distance=120, max_labels=32, head_offset=2.5,
    font='core/performance_hud/debug',
    font_material='mods/sumitm/sentry_ammo_display/font_depth',
    icon_material='mods/sumitm/sentry_ammo_display/icons_depth',
    icon_texture='mods/sumitm/sentry_ammo_display/icons_atlas',
    bar_uv={u0=0.873046875,v0=0.873046875,u1=0.876953125,v1=0.876953125},
    frame_uv={u0=0.00390625,v0=0.75390625,u1=0.24609375,v1=0.99609375},
    icons={
        ['9d474323']={u0=0.50390625,v0=0.50390625,u1=0.74609375,v1=0.74609375}, -- StratagemTurretLaserCannon
        ['14c9c273']={u0=0.75390625,v0=0.50390625,u1=0.99609375,v1=0.74609375}, -- StratagemEmplacementsTeslatower
        ['13a71430']={u0=0.50390625,v0=0.00390625,u1=0.74609375,v1=0.24609375}, -- StratagemTurretsMachinegun
        ['2363e364']={u0=0.00390625,v0=0.25390625,u1=0.24609375,v1=0.49609375}, -- StratagemTurretsMortar
        ['3416fff4']={u0=0.25390625,v0=0.25390625,u1=0.49609375,v1=0.49609375}, -- StratagemTurretsMortarStaticfield
        ['3ae02f80']={u0=0.25390625,v0=0.50390625,u1=0.49609375,v1=0.74609375}, -- StratagemTurretFlamethrower
        ['3bd04c18']={u0=0.25390625,v0=0.75390625,u1=0.49609375,v1=0.99609375}, -- StratagemEmplacementAntiTank
        ['3c709c50']={u0=0.75390625,v0=0.00390625,u1=0.99609375,v1=0.24609375}, -- StratagemTurretsGatling
        ['a37f0894']={u0=0.00390625,v0=0.50390625,u1=0.24609375,v1=0.74609375}, -- StratagemTurretsAutocannon
        ['be95e875']={u0=0.75390625,v0=0.25390625,u1=0.99609375,v1=0.49609375}, -- StratagemTurretsRocket
        ['bf35608c']={u0=0.00390625,v0=0.00390625,u1=0.24609375,v1=0.24609375}, -- StratagemMannedMachinegun
        ['ec516bbd']={u0=0.50390625,v0=0.25390625,u1=0.74609375,v1=0.49609375}, -- StratagemTurretsGassentry
        ['17db8107']={u0=0.25390625,v0=0.00390625,u1=0.49609375,v1=0.24609375}, -- 榴弹兵防卫墙
        ['4e64c4dc']={u0=0.50390625,v0=0.75390625,u1=0.74609375,v1=0.99609375}, -- 武装补给舱的炮塔
    },
    -- 墙上发射器的单位原点和墙根重合，全局 2.5 米会浮在 1.24 米高的墙上方一米多。
    -- 减到离墙顶约 0.65 米；再往下会陷进墙体，被深度缓冲挡住。
    -- 补给车车体原点在车底，抬一米让标签落在车顶上方而不是车身里。
    type_lift_delta={['17db8107']=-0.6,['4e64c4dc']=-1.75,['6d745627']=1.0},
}

end)()

local network,identity,census,camera_candidates,camera,camera_select,labels,snapshot
local visual_settings
local world=nil
local tracked,records={},{}
local now,sample_cursor,tracked_count=0,0,0
local sample_fail_tick=0
local SAMPLE_INTERVAL=0.05
local next_sample_time,last_heavy_frame,last_clock_time=nil,nil,nil
local heavy_samples,last_sample_time=0,nil
local cadence_reason='awaiting_clock'
local last_status,last_signature,last_sample_log=-math.huge,nil,-math.huge
local camera_reason,world_reason='not_sampled','no_world'
local errors={}
local function log(message,...)
    if type(ot.log)=='function' then ot.log('SENTRY-AMMO '..message,...) end
end
local function finite(value)
    return type(value)=='number' and value==value and value>-math.huge and value<math.huge
end
local function invoke(key,fn,...)
    if type(fn)~='function' then return false,nil end
    local ok,value=pcall(fn,...)
    if not ok and not errors[key] then
        errors[key]=true
        log('%s failed: %s',key,tostring(value):gsub('[\r\n]',' '):sub(1,160))
    end
    return ok,value
end
local function current_world()
    local ok,value=invoke('Application.main_world',App.main_world)
    if not ok then world_reason='main_world_read_failed';return nil,false end
    if value==nil then world_reason='no_main_world';return nil,true end
    local listed,worlds=invoke('Application.worlds',App.worlds)
    if not listed or type(worlds)~='table' then world_reason='world_list_read_failed';return nil,false end
    if listed and type(worlds)=='table' then
        for _,candidate in pairs(worlds) do
            if candidate==value then world_reason='ready';return value,true end
        end
    end
    world_reason='main_world_not_live'
    return nil,true
end
local function forget(unit)
    tracked[unit]=nil
    if network then invoke('network.forget',network.forget,unit) end
end
local function reset(next_world)
    world=next_world
    tracked,records={},{}
    sample_cursor,tracked_count=0,0
    sample_fail_tick=0
    next_sample_time,last_sample_time=nil,nil
    camera_reason='not_sampled'
    cadence_reason=world and 'awaiting_sample' or 'no_world'
    if network then invoke('network.reset',network.reset,world) end
    if census then invoke('census.reset',census.reset,world) end
    if snapshot then invoke('snapshot.reset',snapshot.reset,world) end
    if camera_select then invoke('camera_select.reset',camera_select.reset) end
    if labels then invoke('labels.reset',labels.reset,world) end
end
local function clear_snapshot(why)
    records={}
    if snapshot then invoke('snapshot.clear',snapshot.clear,why) end
end
local function refresh_clock()
    local ok,value=invoke('ot.time',ot.time)
    if not ok or not finite(value) then
        cadence_reason='sample_clock_unavailable';clear_snapshot(cadence_reason);return false
    end
    now=value
    if last_clock_time and now<last_clock_time then
        last_clock_time=now;next_sample_time=nil
        last_status,last_sample_log=-math.huge,-math.huge
        cadence_reason='sample_clock_reversed';clear_snapshot(cadence_reason);return false
    end
    last_clock_time=now
    return true
end
local function sample_ammo()
    -- The complete candidate census, identity classification, and ammo reads run
    -- at most 20 times per second. Rendering uses only the bounded snapshot.
    local ok,targets=invoke('census.sample',census.sample)
    if not ok or type(targets)~='table' then
        -- An unreadable census is not evidence that all Unit lifetimes ended.
        clear_snapshot('census_sample_failed');return false
    end
    local current,count={},0
    for _,row in ipairs(targets) do
        if type(row)~='table' or type(row.unit)~='userdata' or type(row.model_hash)~='string' then
            clear_snapshot('census_sample_invalid');return false
        end
        if not current[row.unit] then count=count+1 end
        current[row.unit]=row.model_hash
    end
    for unit in pairs(tracked) do if not current[unit] then forget(unit) end end
    tracked,tracked_count=current,count
    local updated,available=invoke('identity.update',identity.update,targets)
    if #targets==0 then
        clear_snapshot('snapshot_empty_source')
        return true
    end
    -- Pending discovery can still return freshly validated established pairs.
    -- network.sample always reads their current raw ammo again.
    if not updated or available~=true then clear_snapshot('identity_sample_incomplete');return false end
    local fresh={}
    local status=nil
    sample_cursor=sample_cursor%#targets
    local sampler=network and network.sample
    for i=1,#targets do
        local target=targets[(sample_cursor+i-1)%#targets+1]
        -- invoke 只回传第一个返回值，采样失败的原因在第二个上，这里直接 pcall。
        local ok,sampled,record=false,nil,nil
        if type(sampler)=='function' then ok,sampled,record=pcall(sampler,target.unit,target.model_hash) end
        if ok and sampled and type(record)=='table' and record.verified==true then
            fresh[#fresh+1]=record
            status=status or {}
            status[#status+1]=target.model_hash..':ok'
        else
            -- 采样失败时 sample 只返回原因、不打日志，型号一多就完全看不到卡在哪。
            status=status or {}
            status[#status+1]=target.model_hash..':'..(ok and tostring(record) or 'call_error')
        end
    end
    sample_cursor=(sample_cursor+1)%#targets
    -- 20 Hz 采样，200 次约 10 秒。成功与失败都列出来，缺席的型号才看得见。
    sample_fail_tick=sample_fail_tick+1
    if sample_fail_tick>=200 then
        sample_fail_tick=0
        log('sample targets=%d ok=%d %s',#targets,#fresh,table.concat(status or {},' '))
    end
    local captured,accepted=invoke('snapshot.capture',snapshot.capture,world,fresh)
    if not captured or accepted~=true then clear_snapshot('snapshot_capture_failed');return false end
    return true
end
local function status_of(module)
    if not module then return {reason='module_unavailable'} end
    local ok,value=invoke('module.status',module.status)
    return ok and type(value)=='table' and value or {reason='status_unavailable'}
end
local function reason_of(module) return tostring(status_of(module).reason or 'unknown') end
local function report()
    local ns,ls,ids,cs,ss=status_of(network),status_of(labels),status_of(identity),status_of(census),status_of(snapshot)
    local nr,lr,ir,cr=tostring(ns.reason),tostring(ls.reason),tostring(ids.reason),tostring(cs.reason)
    local signature=table.concat({world_reason,tostring(tracked_count),tostring(#records),nr,ir,camera_reason,lr,cr,tostring(ss.reason),cadence_reason},'|')
    if now-last_status>=15 or (signature~=last_signature and now-last_status>=5) then
        log('tracked=%d records=%d network=%s identity=%s camera=%s labels=%s world=%s',
            tracked_count,#records,nr,ir,camera_reason,lr,world_reason)
        local sample_age=finite(ss.sample_time) and math.max(0,now-ss.sample_time) or nil
        log('snapshot=%s records=%s returned=%s dropped=%s age_ms=%s expires_at=%s sampled_frame=%s validation_frame=%s cadence=%s interval_ms=50 heavy_samples=%d last_sample_time=%s',
            tostring(ss.reason),tostring(ss.records),tostring(ss.returned),tostring(ss.dropped),
            tostring(sample_age and sample_age*1000),tostring(ss.expires_at),
            tostring(ss.sampled_frame),tostring(ss.validation_frame),cadence_reason,heavy_samples,tostring(last_sample_time))
        log('draw submitted=%s eligible=%s icons=%s primitives=%s assets_ready=%s bar_reference=%s',
            tostring(ls.submitted),tostring(ls.eligible),tostring(ls.icons),tostring(ls.primitives),
            tostring(ls.assets_ready),tostring(ls.bar_reference))
        log('census=%s complete=%s queries=%s units_checked=%s candidates=%s elapsed_ms=%s',
            cr,tostring(cs.complete),tostring(cs.queries),tostring(cs.units_checked),tostring(cs.targets),tostring(cs.elapsed_ms))
        if tracked_count>0 then
            log('identity discovery_complete=%s discovery_reason=%s retained=%s admitted=%s',
                tostring(ids.discovery_complete),tostring(ids.discovery_reason),
                tostring(ids.retained_bindings),tostring(ids.new_bindings))
            log('identity objects=%s pending=%s expired=%s candidates=%s bound=%s nearest_metres=%s nearest_type=%s',
                tostring(ids.objects),tostring(ids.unclassified),tostring(ids.expired_negatives),
                tostring(ids.candidates),tostring(ids.bound),tostring(ids.minimum_same_model_distance),tostring(ids.nearest_type_hash))
            log('identity progress checks_per_second=%s estimated_sweep_seconds=%s cache_seconds=%s oldest_negative_seconds=%s throughput_limited=%s',
                tostring(ids.classification_checks_per_second),tostring(ids.estimated_negative_sweep_seconds),
                tostring(ids.negative_cache_max_age),tostring(ids.oldest_negative_age),tostring(ids.classification_throughput_limited))
            local up,np=ids.nearest_unit_position,ids.nearest_network_vector
            if type(up)=='table' and type(np)=='table' then
                log('identity closest sample frame=%s unit=(%.5f,%.5f,%.5f) network=(%.5f,%.5f,%.5f)',
                    tostring(ids.distance_sample_frame),up.x,up.y,up.z,np.x,np.y,np.z)
            end
        end
        last_status,last_signature=now,signature
    end
    if #records>0 and now-last_sample_log>=2 then
        local samples={}
        for i=1,math.min(#records,8) do
            local r=records[i]
            local extra=r.auxiliary or {}
            local metric
            if r.metric_kind=='heat' then
                metric=string.format('heat=%s estimated_threshold=%s max_verified=%s',
                    tostring(r.metric_value),tostring(r.metric_max),tostring(r.metric_max_verified))
            elseif r.metric_kind=='lifetime' then
                metric=string.format('estimated_seconds=%s estimate_basis=%s',
                    tostring(r.metric_value),tostring(r.metric_estimate_basis))
            else metric='rounds='..tostring(r.rounds) end
            samples[#samples+1]=string.format('object=%s type=%s %s magazines=%s chamber_empty=%s aux_frame=%s identity=%s',
                tostring(r.network_goid),tostring(r.network_type),metric,
                tostring(extra.magazines_remaining),tostring(extra.chamber_empty),
                tostring(r.auxiliary_sample_frame),tostring(r.identity_basis))
        end
        log('live samples: %s',table.concat(samples,'; '))
        last_sample_log=now
    end
end
local ready=type(create_network)=='function' and type(create_identity)=='function'
    and type(create_census)=='function' and type(create_camera_units)=='function'
    and type(create_camera)=='function' and type(create_camera_select)=='function'
    and type(create_labels)=='function' and type(create_snapshot)=='function'
    and type(network_profile)=='table' and type(visual_profile)=='table'
    and type(ot.time)=='function' and type(ot.frame)=='function'
    and type(App.main_world)=='function' and type(App.worlds)=='function'
    and type(Unit.alive)=='function' and type(Unit.world)=='function'
if ready then
    identity=create_identity(ot,network_profile)
    network=create_network(ot,network_profile,identity)
    census=create_census(ot,network_profile)
    snapshot=create_snapshot(ot,network_profile)
    camera_candidates=create_camera_units(ot)
    camera=create_camera(ot)
    camera_select=create_camera_select(ot)
    labels=create_labels(ot,visual_profile)
    local settings_ok,value=invoke('settings.create',create_visual_settings,ot,labels,visual_profile)
    if settings_ok then visual_settings=value end
end
log('version=%s build=%s ready=%s; 20 Hz complete sampling, bounded 80 ms snapshots; game validation pending',
    VERSION,tostring(type(network_profile)=='table' and network_profile.steam_build),tostring(ready))
local module = {
    version=VERSION,
    update=function(dt)
        if visual_settings then invoke('settings.update',visual_settings.update,dt) end
        if not ready then return end
        -- Use the provider/snapshot clock. dt accumulation would drift away
        -- from the samples' real timestamps and could extend their lifetime.
        local clock_ready=refresh_clock()
        local latest,world_known=current_world()
        if world_known and latest~=world then reset(latest) end
        if not world_known then clear_snapshot('world_context_unavailable') end
        if not clock_ready or not world_known or not world then return end
        local fok,frame=invoke('ot.frame',ot.frame)
        if not fok or not finite(frame) or frame<0 or frame%1~=0 then
            cadence_reason='sample_frame_unavailable';clear_snapshot(cadence_reason);return
        end
        if frame==last_heavy_frame then return end
        if next_sample_time==nil then next_sample_time=now end
        if now+1e-9<next_sample_time then return end
        -- Skip missed slots after a long frame; never run a catch-up loop.
        local skipped=math.floor(math.max(0,now-next_sample_time)/SAMPLE_INTERVAL+1e-8)
        next_sample_time=next_sample_time+(skipped+1)*SAMPLE_INTERVAL
        last_heavy_frame,last_sample_time=frame,now
        heavy_samples=heavy_samples+1
        cadence_reason=sample_ammo() and 'sampled_20hz' or 'sample_failed'
    end,
    render=function()
        if not ready then return end
        local clock_ready=refresh_clock()
        local latest,world_known=current_world()
        if world_known and latest~=world then reset(latest) end
        if not world_known then clear_snapshot('world_context_unavailable') end
        local view=nil
        records={}
        if world and world_known and clock_ready then
            local valid,current=invoke('snapshot.snapshot',snapshot.snapshot,world)
            if valid and type(current)=='table' then records=current
            else clear_snapshot('snapshot_validation_failed') end
        end
        if world and world_known then
            local listed,units=invoke('camera_units.sample',camera_candidates.sample,world)
            if listed and type(units)=='table' then
                local selected,active=invoke('camera_select.select',camera_select.select,world,units)
                if selected and active then
                    local sampled,result=invoke('camera.sample',camera.sample,world,active)
                    if sampled then view=result end
                    camera_reason=reason_of(camera)
                else camera_reason=reason_of(camera_select) end
            else camera_reason=reason_of(camera_candidates) end
        else camera_reason=world_known and 'no_world' or 'world_context_unavailable' end
        invoke('labels.update',labels.update,view,records)
        report()
    end,
    shutdown=function()
        if visual_settings then invoke('settings.shutdown',visual_settings.shutdown) end
        records,tracked={},{}
        tracked_count=0
        if labels then invoke('labels.shutdown',labels.shutdown) end
        if snapshot then invoke('snapshot.shutdown',snapshot.shutdown) end
        if camera_select then invoke('camera_select.reset',camera_select.reset) end
        if census then invoke('census.shutdown',census.shutdown) end
        if network then invoke('network.shutdown',network.shutdown) end
        world=nil
        ready=false
    end,
}

-- 诊断用：全类型扫描。前 SCAN_REAL 个是模组自带真名，其余由 32 位 hash 碰撞生成。
local SCAN_NAMES={
    'zdZ~yhg!','Es}/~kND','i1j-1MHz','T[tD_-{~','N+d%Kq$1','4%EXhX(8',
    'mZ(LD/!&','941ND^v~','):e&;~2_','oYVe)p+i','JMR4x`Ao','G`Y1$voS',
    'Og,VB9b3','}>|mEsa.','$Leg(yqP','ok$F9qrS','9I~t$THc','Mm{:#c*%',
    '<j9*h3.S','<3hkntZ?','tXoU]Yb{','h:(x&X8#','f$XD&/}6','!!!*Xbo`',
    '!!#%|?i%','!!!}eItu','!!!&oQdd','!!!Zr4)C','!!#+pj~y','!!$?+htx',
    '!!!SNq5/','!!!&TFBn','!!!O_M+e','!!!}CJtT','!!#l?inc','!!!(0?og',
    '!!!%TsR$','!!!.ri5R','!!#N<kvX','!!!I>J%t','!!#F:(G;','!!!2Qp5a',
    '!!!(f^q0','!!#tVor|','!!%S.l.n','!!!076KW','!!!CQ|)n','!!!nS,VR',
    '!!#P2b!A','!!!(SG+;','!!!FTj2t','!!!hLEIq','!!!,DRPw','!!!S!jJ(',
    '!!#/s.9K','!!!CgjuO','!!!m>#<4','!!!?T:1R','!!!/Nycs','!!!nmKQS',
    '!!#0[6Ki','!!!D/]Y0','!!%1[c{I','!!$1rGUk','!!!i8;Ax','!!!}mBdQ',
    '!!!:^yT5','!!!UAg&H','!!!*+Lmx','!!!*iQO-','!!!3MS~~','!!!yv1N!',
    '!!!:Z[s;','!!$2qdS6','!!!Jd7QF','!!!yz6.F','!!!6nV+I','!!!a}!;t',
    '!!!%OOWO','!!$Iu^tl','!!#R2gPt','!!#F?E|o','!!#(w9PI','!!!C@;8J',
    '!!#gM.XI','!!!HspVN','!!#zL,WR','!!!>j;{;','!!!(a)l_','!!!&JV5K',
    '!!!pJh]b','!!!5mMqb','!!!sS/,I','!!#H:oyg','!!$32@jL','!!!`,gdg',
    '!!#!J#R7','!!!00~E/','!!!50KFr','!!!|cBa#','!!!-et-=','!!)I]Oub',
    '!!!,2(~+','!!!F-a|v','!!!qBUtp','!!!X;jTK','!!!@[,A,','!!!*~Qur',
    '!!#y7#4N','!!#,roD{','!!!ACIn$','!!!lJNNB','!!#JyED)','!!!#L+S0',
    '!!!-d{/C','!!!#gBZZ','!!!2)y4?','!!!Z,oja','!!#!OYRd','!!!;y0#%',
    '!!!&z/;J','!!!)#?MM','!!!F)e?%','!!!Q1#6}','!!!>$X5b','!!!0$6%w',
    '!!!-k[+U','!!!mCOQH','!!!PrBGW','!!!ulJM`','!!#+e.d}','!!#!LNYX',
    '!!!CqDXv','!!!g`vBJ','!!#RzoEp','!!!Cq:>d','!!!$XArw','!!!-^QO&',
    '!!!Km{g!','!!!&y$m/','!!#*X[vV','!!!+m2A=','!!#*FOO`','!!#W!)HA',
    '!!!/sqVv','!!!ud:H[','!!!YUV2W','!!!$g7m$','!!!0**![','!!#iDSnv',
    '!!!B%vlJ','!!!HW]v3','!!!#H&3~','!!$iVY.*','!!!t,C<<','!!!-=h5J',
    '!!#-V!sT','!!!@E%*L','!!!$-M*2','!!#*Js`)','!!!`rOCu','!!!$~u{i',
    '!!#Lc=OS','!!!!:ZFK','!!!zs#6Z','!!#v<(r$','!!!H[UA2','!!!vAK4T',
    '!!#wMqg/','!!!Ov=6!','!!!)g|p`','!!!zl[nD','!!!m9<Ni','!!!4NMZd',
    '!!!8/@YE','!!#0G9u%','!!!%Uozg','!!#>f/aA','!!!RD,rj','!!#Hz]fZ',
    '!!!igVl:','!!!m%xJB','!!!b8#QZ','!!!@[]xn','!!!g<yhR','!!!UFE$)',
    '!!!Yr=6A','!!!*4cp*','!!!Wy60W','!!#5s/cR','!!!|#959','!!!$C%kw',
    '!!#I@QWu','!!!)J_RX','!!!b|]8d','!!#R[hk(','!!#^lE]X','!!#8HI#-',
    '!!#%jL)Q','!!!m=W`G','!!$-s/Hs','!!!`_?]u','!!!R11W7','!!!PB0sJ',
    '!!!N^u.}','!!!kl5yB','!!!~TQr(','!!!&P_GX','!!!TS}&>','!!!DK[Gz',
    '!!!quMaL','!!!zFmf9','!!!bq,aL','!!!0`dOs','!!!6~jvk','!!!(,%<u',
    '!!#q:<_d','!!!?BTXY','!!$<73!c','!!#a,OyW','!!#5kBMA','!!!yE?Hd',
    '!!!:-IlX','!!$.>=zp','!!!VWg:]','!!$$9aU^','!!!~Wx~J','!!!m@a+A',
    '!!!&FWme','!!!!3qh3','!!!@xU>/','!!!,[zyI','!!!rdt:h','!!!O(+ja',
    '!!!SUh(]','!!!#n}Bv','!!#>E_hD','!!!lsg/6','!!!6jU1$','!!!.]PJ[',
    '!!!IIIU?','!!#[I)/e','!!!`>mVX','!!!5>B0^','!!!.!!b7','!!%)%uGz',
    '!!$C}huC','!!#f+F$y','!!!1:(*;','!!$I.*}U','!!!6&Ukk','!!!;m~xq',
    '!!!#GIo>','!!!)fZiG','!!#GoQKb','!!!;piVn','!!!?RLS;','!!#=rDY,',
    '!!$z]?fE','!!!4/;Xp','!!!a(Dbf','!!!e$DnB','!!!J4+au','!!!/YHj3',
    '!!!f!<kn','!!!4K#gI','!!!$Qh2;','!!!,s/Hc','!!!XwZo+','!!#:wkts',
    '!!!4M2.S','!!!3cE1_','!!!)#+!?','!!!#ye.u','!!!g5pM2','!!!LDp:Y',
    '!!$QLMf[','!!!.1g-2','!!!9;^Tv','!!!(JMRA','!!!)pVKZ','!!!sS}{(',
    '!!!_VHo,','!!!6<ZjW','!!!j/$Sl','!!!etlK+','!!!!/H_~','!!!TX{:r',
    '!!!R<sut','!!!94pr#','!!!`9MUA','!!!=1%*1','!!!+^;G6','!!$RPjPz',
    '!!!I01=Y','!!!c$;Fk','!!!Lq>?Q','!!!8jlFS','!!!J!V)m','!!!!(/-b',
    '!!!0Y]AH','!!!$MbJa','!!!7~hu=','!!!t{!/G','!!!L{biO','!!!=ey%?',
    '!!#D>fk}','!!!&+![%','!!#8Y[Ou','!!#IQ|+^','!!!aRh8E','!!#.F;)z',
    '!!!>zS+X','!!#eX)$f','!!#DH2ql','!!$qtg2`','!!!3l/j=','!!!Rtl|+',
    '!!!P+#qh','!!!bkY3]','!!!ahP^k','!!#K^%Nc','!!#E#$LZ','!!!S1FpR',
    '!!!{CXF]','!!!@?7V;','!!!B2_#?','!!!^^dC*','!!!MDI/2','!!!@_FRm',
    '!!!3?^A;','!!#*Dt`#','!!#t[wN`','!!#3I49i','!!!1qx;H','!!#2@)7y',
    '!!!_)18k','!!!.wuoL','!!!nV9;u','!!!)GUN1','!!!4V*D3','!!!s;-bQ',
    '!!#?i-v;','!!!1EOI}','!!!DKCs3','!!!&avfz','!!!4@Yy$','!!!%W/y.',
    '!!!-71Kb','!!!}ah{n','!!$^Hh({','!!!PQ&c!','!!!q*t4k','!!!Bz&!~',
    '!!!<VM|T','!!$#)BK=','!!!%(O&J','!!!#K4#F','!!!<bD9E','!!!5Hbq-',
    '!!!6oKJ4','!!!/r{Z|','!!!VFBg_','!!#eqtl,','!!!G({4+','!!!;nrlI',
    '!!!D|{5)','!!!NZtx9','!!!2wYlM','!!$96%9:','!!!X:}`R','!!!a5f/r',
    '!!!d--IW','!!!Sp0C{','!!!#ZaOD','!!!9-!Sd','!!!l,z#_','!!!8Ca&K',
    '!!#0HR&Q','!!!qw&lD','!!!8P!i@','!!!I/QO7','!!!kgB`S','!!!lOOlQ',
    '!!![x[M0','!!!!?9j9','!!!Q]4=A','!!!h<A[C','!!!gkUSC','!!!eT;W2',
    '!!##d!l[','!!!<xGzR','!!!<*9Nf','!!!v@|br','!!!6[XmX','!!!(5WLJ',
    '!!!q-A*w','!!!Qn+uR','!!#,43if','!!!$Q>{z','!!!&v45.','!!!.Y7%)',
    '!!#m|@Wj','!!!!Ve:5','!!$=/OP5','!!!1-.P@','!!!Q^nbk','!!!3-|Ru',
    '!!#Ye!qR','!!!m|CD?','!!#ySL9^','!!#8R`:7','!!!H<Ov1','!!!BYsB:',
    '!!!=$!/a','!!!nM7(}','!!!(,Y&J','!!!R9Hpv','!!!]i-US','!!%&mG)O',
    '!!!>%=dy','!!!+5Wu9','!!!P7c,K','!!!9dfFX','!!!)R2xv','!!!&hUSA',
    '!!!H3=kE','!!!ADj?`','!!!)-4&P','!!!3eSNz','!!!e]Wb5','!!!+Xyvt',
    '!!!H.pxd','!!!*{n*M','!!!;SeKR','!!!J.?JX','!!#/CA;q','!!#T$iD)',
    '!!!#d9yj','!!!:S1iU','!!#7}yo:','!!!Sf|fl','!!#Tt`iT','!!!Ik?l)',
    '!!!^,F8_','!!#A_,G#','!!!2suD-','!!!nLa;p','!!!i(}~-','!!!*0%gm',
    '!!!5__eE','!!!b8b%h','!!!HUSw8','!!!>3I{r','!!!6/bj4','!!!&J3c@',
    '!!!^wDih','!!!Cu_:0','!!!:+RFs','!!!48cK^','!!!0S%hc','!!!Z-)/H',
    '!!!$$KYw','!!##)]T@','!!!L*u{h','!!!e.m)9','!!!{$#SU','!!!:jBQb',
    '!!!)Mw>:','!!#lq<.R','!!!3{`;=','!!!6Sk,|','!!!+!|e&','!!!D{TPL',
    '!!!2*QE*','!!#qGc@K','!!!|32y[','!!!FjJ{:','!!#,|{7M','!!!%cz9.',
    '!!!?bkn-','!!!I1|MN','!!!N~wu)','!!#Royae','!!!cylcX','!!#1[9j9',
    '!!#%CY<K','!!#.?{hr','!!!~Lbbp','!!!Qx%w)','!!#iigOq',
}
local SCAN_HASH={
    '0162dd9e','13a71430','14c9c273','2363e364','2596fa0a','3416fff4',
    '3ae02f80','3bd04c18','3c709c50','6d3f8345','947ed108','992ff297',
    '9d474323','9fdd94c0','a37f0894','b551b138','be95e875','bf35608c',
    'ec516bbd','ee956607','ef108792','f1dbc135','f53d4725','0162dd9e',
    '01ab3e6d','048ce2de','07911f97','07b51587','0df416d3','10662676',
    '118f3c7c','13a71430','14c9c273','165fc36e','18a01c11','1958acb5',
    '1c7ca26c','1f37dab0','2199ab14','21cc854c','2299bf2f','2363e364',
    '251a4704','2596fa0a','28e9e58f','2c1744f8','2d3e1d28','3030a4d2',
    '316595a3','31e8e62d','3416fff4','358ef508','36853a8e','3926f217',
    '3ae02f80','3bd04c18','3c709c50','3ce90d0e','3d65d411','3f3d1d22',
    '401c4ef1','40b7da1a','42f7db08','46d7c60e','49d7859c','4aa644a9',
    '4e64c4dc','4ea4ba77','52e0b352','53ae74ee','5b56efd2','5db58a35',
    '5db9387c','65e4986b','6d3f8345','6d745627','6ddbf2ae','6f4709b5',
    '71480bc3','74c60ac0','7a66ef6c','7aa2db54','7d736a8b','7e40750e',
    '88496cde','8e23220e','9075faf0','947ed108','95ce31b6','992ff297',
    '9a28415a','9d474323','9fdd94c0','a37f0894','a8282593','a98a506a',
    'ab1f1577','abbd222f','ae41dcfe','b551b138','b9acb226','be95e875',
    'bf35608c','c208c35d','c5321867','c704e0f9','c7970a97','c9099597',
    'ca34a097','cab3a3cf','ce4427b2','cfaa6860','d0b36b3b','d55e91c6',
    'd677753d','dc715109','de88a043','dfa631b9','ec516bbd','ee956607',
    'ef108792','f0d154d1','f1dbc135','f32cebf7','f37e3ebf','f53d4725',
    'f5915517','f7bfb8db','fb8c4465','0113bc49','02444e51','02e7c3f2',
    '0303c9a0','03547194','038a00cc','0422a6f2','04645757','04f392ce',
    '05256e47','055bb169','057f233e','06887ef8','06a1047a','06fee72c',
    '07bd233c','086d544c','0a482e89','0a84375b','0be23e31','0bee9119',
    '0c10c622','0c3c0eb3','0cb747b7','0cf47b26','0dff968f','0ea37e5d',
    '0f1c7231','107fa9fc','11d8c98b','12584790','1310d067','14c75f20',
    '15ae0889','17783758','17b02218','17d1f072','17db8107','18ba422a',
    '193ffddf','194bdfa6','1962a5a9','1ab72be4','1ab955e9','1bc830af',
    '1bec6c1a','1cb22371','1d301481','1f3765c1','20c29e45','20ec5b6f',
    '228e0659','22929ccb','2432c30a','2444bbce','26acc614','26eccc06',
    '2ac9b588','2c0c01f7','2c341346','2c4d7b3c','2cef5e3b','2d552a28',
    '2ddd091d','2df95dfe','2e4749c7','2ef8c28a','2f027b59','2fad831f',
    '3000b371','30294946','303d2220','30b55bbc','312febbf','314a1bf5',
    '3244dc49','32fba243','336e46ba','3478da46','34e1bc3a','350dc8fa',
    '3622f8dc','36433ca8','366d3622','37193958','371c20a3','388b859b',
    '396381a9','39a71a1d','3b2e3645','3b718900','3bf357e5','3c16b6ef',
    '3c189aa5','3c3cec57','3cecce6c','3d191f7a','3d538455','3d7ed060',
    '3df247f4','3f79f6e2','3f87a7e8','3fb302d8','41386847','41d645a7',
    '41de83ec','422c03cb','425bb7e3','4321fb69','4796fd39','47c89128',
    '48d9a540','499fd7a8','4a62a3a9','4cf7fdda','4daea8e1','4ee55ac0',
    '520b5165','523bde80','532cf6bd','53dc48fe','54fb3142','5590232e',
    '5652ed49','57f34b28','58245c0c','58594a4a','58feebf5','59044eaa',
    '5aa3bfa1','5b1b61bf','5b5264ec','5bdc7b24','5c661180','5c813e43',
    '5c957a4f','5d52c1d0','5d9752f9','5dfc55bb','5e0cd6f6','5e95439c',
    '5e9637d0','5f3517bf','605a5bb9','60cac78a','611b6bf9','64f91671',
    '656d507b','66611cce','6876b30a','6947919e','69971ef9','6a073cc6',
    '6b2c3bed','6c26a65d','6cc38f79','6e2ed40c','6ec2a850','6f0a0db2',
    '6f5ed5c0','716fc7fe','7405f976','74a2e57a','7559f271','767d2004',
    '76b90dcd','77dc330e','79bc04a2','7a694dd3','7b729b3a','7bfb2e06',
    '7c2f5100','7d16f96c','7d9a46dc','7ea9ed9a','7f4b0695','803159b3',
    '80b2dcfd','80d3298e','814c5e3d','81583e65','82a8d3c2','83d1036e',
    '8583e435','866289c6','870a7011','8859444f','89a4486a','8a3c6fee',
    '8a71b839','8b2ee1ef','8b40a204','8b50028e','8c0aee6e','8c299f75',
    '8c51d16e','8c57c12f','8c6aebd1','8faefd04','8fcdd35c','919e45b3',
    '91dd0f48','91f10108','942a0601','94dd9957','95c5dc52','960653cb',
    '9652785b','967a3fed','96c78961','96c8cffd','96cb35d7','96e240f2',
    '97425204','986600d1','9a2b9962','9c06f074','9c2e7f43','9c6bc008',
    '9ca7fac8','9ce82ac5','9d76c858','9dbc9204','9e122e44','9e18f0cf',
    '9f16a5a2','a09f5a5c','a261d7f0','a327969b','a41ddfcd','a4e5b53d',
    'a4eff4fa','a523ef8a','a5fcd71d','a6445802','a7d06d7b','a821c752',
    'a8dda382','aa907e6c','aac34d6b','ab04027e','ab38cade','aca565f4',
    'ad0dc3c8','ae35e7d7','ae9503a3','aeaf939f','aef68099','b060f4a4',
    'b13a8345','b2bfd847','b30f446f','b3246bb7','b5badd9f','b66d1624',
    'b697955d','b7574b90','b936384d','ba161726','bae3f0a6','bb2729e9',
    'bd5f17dd','bd69189c','be010cc7','be9b793e','beb09041','bf749fb2',
    'c05dbc1b','c0693f8c','c0e5aa7f','c0ff5282','c200d4f5','c204018b',
    'c2556456','c3457794','c3fd169b','c4235b66','c573e903','c5a989c9',
    'c61aae4c','c656df34','c7dfd9eb','c8102937','c828dca5','c920c2d3',
    'c9680f06','c9cc2fac','ca367ec7','ca6aa021','cd0e4b4a','cdb1e951',
    'cdccbaa6','cee3775a','cfaa9ddc','d020931a','d0454672','d100e0c8',
    'd126f103','d1a2adab','d1bf765a','d372022f','d3c9a08c','d4398a6d',
    'd496b132','d5e0448b','d675eb10','d6f22bc8','d724a2f9','d7dde809',
    'db0fe7b4','db56604e','dd163572','dd9f1091','ddb625c5','de0cb0ab',
    'decce39b','deeeea0b','def25ee4','e18d64e9','e1f10066','e2ab08d5',
    'e32ff89c','e3d29b1f','e42d5902','e645b6ee','e86fb7d6','ea3cabd2',
    'eaa3a6da','eb38d34e','eb621141','eb784ac7','ebc18699','ec698151',
    'ef8fb68e','ef90a60b','efa02c2f','f179599a','f259751e','f36a1cd6',
    'f3d3ab0e','f439bdce','f45000d7','f45f531b','f4e6c4a4','f5553215',
    'f7bd2602','f96b8f11','f9bb2f35','fbbfc6a3','fbc7f2e1','fbd58864',
    'fc0812f8','fc1ece86','fd115815','fd585726','fde8ab3d','fe07a595',
    'fe16d7e6','fe760204','ff14374b','ff3cf8d8','ffc5251f',
}
local SCAN_POS='11111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'
local SCAN_REAL=23             -- 表头几个是真名，命中的第一个用来校验自造名

local SCAN_BUDGET=120          -- 每帧最多几次 is_type
local SCAN_FIRST=15           -- 进图后多少秒开始第一轮
local SCAN_REFRESH=10         -- 每轮结束后隔多久重新取对象池
local SCAN_MIN_POOL=8         -- 对象太少时先等，避免在空图里白扫

local scan_known={}           -- goid -> 类型 hash（'?' 表示扫过但没有匹配）
local scan_call={}            -- goid -> 命中时用的 call 名（读字段要用它）
local scan_session,scan_ids=nil,nil
local scan_i,scan_j=1,1
local scan_passes,scan_new,scan_objs=0,0,0
local scan_started,scan_next_pool=nil,nil
local scan_last_new,scan_logged_objs=0,-1
local scan_checked=false
local scan_xcheck=nil            -- nil 未验证 / true 自造名与真名等效 / false 不等效

local function scan_log(fmt,...)
    if type(ot.log)=='function' then pcall(ot.log,fmt,...) end
end

local function scan_pool()
    local S=type(ot.engine)=='table' and ot.engine or {}
    local Net,GS=S.Network or {},S.GameSession or {}
    if type(Net.game_session)~='function' or type(GS.peers)~='function'
        or type(GS.objects_owned_by)~='function' then return nil end
    local ok,session=pcall(Net.game_session)
    if not ok or session==nil then return nil end
    local good,peers=pcall(GS.peers,session)
    if not good or type(peers)~='table' then return nil end
    local ids,n={},0
    for _,peer in pairs(peers) do
        local got,list=pcall(GS.objects_owned_by,session,peer)
        if got and type(list)=='table' then
            for k=1,#list do
                n=n+1
                if n>512 then break end
                ids[n]=list[k]
            end
        end
    end
    return session,ids
end

local function scan_pos(session,id)
    local V3=ot.engine.Vector3
    if type(V3)~='table' or type(V3.to_elements)~='function' then return nil end
    local ok,value=pcall(ot.engine.GameSession.game_object_field,session,id,'c{i(uTh>')
    if not ok or value==nil then return nil end
    local good,x,y,z=pcall(V3.to_elements,value)
    if not good or type(x)~='number' or type(y)~='number' or type(z)~='number' then return nil end
    return string.format('(%.2f,%.2f,%.2f)',x,y,z)
end

local function scan_tick(now)
    if scan_started==nil then scan_started=now end
    if not scan_checked then
        scan_checked=true
        local Net=ot.engine.Network
        local function fields_of(name)
            if type(Net)~='table' or type(Net.object_info)~='function' then return -1 end
            local ok,info=pcall(Net.object_info,name)
            if not ok or type(info)~='table' or type(info.fields)~='table' then return -1 end
            local n=0
            for slot in pairs(info.fields) do
                if type(slot)=='number' then n=n+1 end
            end
            return n
        end
        scan_log('SENTRY-SCAN selfcheck real=%d made=%d',
            fields_of(SCAN_NAMES[1]),fields_of(SCAN_NAMES[SCAN_REAL+1]))
    end
    if now-scan_started<SCAN_FIRST then return end
    if scan_ids==nil then
        if scan_next_pool~=nil and now<scan_next_pool then return end
        local session,ids=scan_pool()
        scan_next_pool=now+SCAN_REFRESH
        local n=ids and #ids or -1
        if session==nil or ids==nil or n<SCAN_MIN_POOL then
            if n~=scan_logged_objs then
                scan_logged_objs=n
                scan_log('SENTRY-SCAN pool waiting objects=%d',n)
            end
            return
        end
        scan_session,scan_ids=session,ids
        scan_i,scan_j=1,1
        scan_objs=n
        if n~=scan_logged_objs or scan_passes==0 then
            scan_logged_objs=n
            scan_log('SENTRY-SCAN pool objects=%d types=%d pass=%d',n,#SCAN_NAMES,scan_passes+1)
        end
        return
    end
    local GS=ot.engine.GameSession
    local budget=SCAN_BUDGET
    while budget>0 and scan_i<=#scan_ids do
        local id=scan_ids[scan_i]
        local known=scan_known[id]
        if known~=nil then
            scan_i,scan_j=scan_i+1,1
        else
            local hit=nil
            while scan_j<=#SCAN_NAMES and budget>0 do
                local ok,match=pcall(GS.game_object_is_type,scan_session,id,SCAN_NAMES[scan_j])
                budget=budget-1
                if ok and match then hit=scan_j;break end
                scan_j=scan_j+1
            end
            if hit then
                local hash=SCAN_HASH[hit]
                if scan_xcheck==nil and hit<=SCAN_REAL then
                    for k=SCAN_REAL+1,#SCAN_NAMES do
                        if SCAN_HASH[k]==hash then
                            local mok,mmatch=pcall(GS.game_object_is_type,scan_session,id,SCAN_NAMES[k])
                            scan_xcheck=(mok and mmatch==true)
                            scan_log('SENTRY-SCAN xcheck type=%s real=ok made=%s',hash,tostring(mok and mmatch))
                            break
                        end
                    end
                end
                local where=' none'
                if SCAN_POS:sub(hit,hit)=='1' then where=scan_pos(scan_session,id) or ' unreadable' end
                scan_known[id]=hash
                scan_call[id]=SCAN_NAMES[hit]
                scan_new=scan_new+1
                scan_log('SENTRY-TYPE goid=%s type=%s pos=%s',tostring(id),hash,where)
                scan_i,scan_j=scan_i+1,1
            elseif scan_j>#SCAN_NAMES then
                scan_known[id]='?'
                scan_i,scan_j=scan_i+1,1
            end
        end
    end
    if scan_i>#scan_ids then
        scan_ids=nil
        scan_passes=scan_passes+1
        scan_next_pool=now+SCAN_REFRESH
        if scan_new>scan_last_new then
            scan_log('SENTRY-SCAN pass done objects=%d new=%d passes=%d',
                scan_objs,scan_new-scan_last_new,scan_passes)
        end
        scan_last_new=scan_new
    end
end

-- MDL3 每帧驱动：先推进 ot 的时钟，再跑原来的 update。
local update_core=module.update
module.update=function(dt)
    ot.advance(dt)
    apply_groups()
    pcall(scan_tick,ot.time())
    return update_core(dt)
end
return module
















































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































