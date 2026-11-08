-- open77_phone tests: the rules (server/lease.lua), the web server's question (server/http.lua) and the client half
-- (client/main.lua) against a stand-in of the game's client runtime. Run from the resource folder:
--   lua5.4 tests/run.lua
local Json = dofile("tests/json.lua")
dofile("server/lease.lua")
dofile("server/http.lua")

local passed, failed = 0, 0
local function check(what, cond, detail)
    if cond then
        passed = passed + 1
        print("  ok   " .. what)
    else
        failed = failed + 1
        print("  FAIL " .. what .. (detail ~= nil and ("\n       " .. tostring(detail)) or ""))
    end
end
local function dump(v) return Json.encode(v) end

-- A random source the tests can see through: the real one in the lease, recorded.
local now = 0
local function newLease(opts)
    opts = opts or {}
    opts.clock = function() return now end
    return PhoneLease.new(opts)
end

print("== the lease: who holds the phone")
now = 1000
local L = newLease()
local what, d = L:request(1, "Alice", now, "203.0.113.5:40000")
check("the first to ask gets it, with a ticket of 32 hex characters", what == "granted" and d.fresh == true
    and PhoneLease.validTicket(d.ticket) and d.ticketSeconds == 30, dump(d))
local t1 = d.ticket
local ok, who, note = L:check(t1, now, "203.0.113.5")
check("the ticket opens the screen once, for its player, from the player's own address", ok == true and who == 1
    and note == "the player's own address", tostring(who) .. " " .. tostring(note))
ok, who = L:check(t1, now, "203.0.113.5")
check("... and never twice", ok == false and who == "ticket_used", who)

what, d = L:request(2, "Bob", now + 500, "198.51.100.7:5000")
check("the second is told who has it, and for how long", what == "busy" and d.holder == "Alice" and d.heldMs == 500, dump(d))
check("... and waits to be told", L.waiting[2] == "Bob")
local released, waiting = L:release(1)
check("the holder puts it down: the waiting player is handed over to be told", released == true and waiting[2] == "Bob"
    and L.holder == nil and next(L.waiting) == nil, dump(waiting))
what, d = L:request(2, "Bob", now + 600, "198.51.100.7:5000")
check("... and then gets it", what == "granted" and L.holder.player == 2)
ok, who = L:check(t1, now + 600)
check("the old holder's spent ticket stays spent", ok == false)

print("== tickets: once, briefly, only for the holder, only from the holder's address")
now = 100000
L = newLease()
_, d = L:request(1, "Alice", now, "203.0.113.5:1")
local a1 = d.ticket
_, d = L:request(1, "Alice", now + 100, "203.0.113.5:1")
local a2 = d.ticket
check("a new ticket for the same player retires the unused older one", L:check(a1, now + 100, "203.0.113.5") == false
    and select(2, L:check(a1, now + 100)) == "unknown_ticket")
check("... and the new one works", L:check(a2, now + 200, "203.0.113.5") == true)
_, d = L:request(1, "Alice", now + 300, "203.0.113.5:1")
ok, who = L:check(d.ticket, now + 300 + 30001, "203.0.113.5")
check("a ticket older than 30 s is refused", ok == false and who == "ticket_expired", who)
_, d = L:request(1, "Alice", now + 40000, "203.0.113.5:1")
ok, who = L:check(d.ticket, now + 40000, "198.51.100.9")
check("from another address it is refused, and spent", ok == false and who == "other_address"
    and select(2, L:check(d.ticket, now + 40000, "203.0.113.5")) == "unknown_ticket", who)
_, d = L:request(1, "Alice", now + 41000, "203.0.113.5:1")
ok, who, note = L:check(d.ticket, now + 41000, "2001:db8::77")
check("an IPv6 WebSocket for an IPv4 player is let through (cannot be compared), and says so", ok == true
    and note:find("IPv6 here, IPv4 in game", 1, true) ~= nil, note)
_, d = L:request(1, "Alice", now + 42000, "203.0.113.5:1")
ok, who, note = L:check(d.ticket, now + 42000, nil)
check("no address from the web server: let through, and said", ok == true and note:find("did not say", 1, true) ~= nil, note)
local L2 = newLease()
_, d = L2:request(9, "NoAddr", now, nil)
ok, who, note = L2:check(d.ticket, now, "203.0.113.200")
check("a player whose address the game could not read: not compared", ok == true and note == "address not compared", note)
check("addresses: ip:port, [v6]:port, v4-mapped v6, bare v6", PhoneLease.address("203.0.113.5:40000") == "203.0.113.5"
    and select(2, PhoneLease.address("203.0.113.5")) == 4
    and PhoneLease.address("[2001:DB8::1]:5000") == "2001:db8::1" and select(2, PhoneLease.address("[2001:db8::1]:5000")) == 6
    and PhoneLease.address("::ffff:203.0.113.5") == "203.0.113.5" and PhoneLease.address("2001:db8::2") == "2001:db8::2"
    and PhoneLease.address("") == nil and PhoneLease.address("not an address") == nil)
L = newLease()
_, d = L:request(1, "Alice", now, nil, 1)
_, d2 = L:request(1, "Alice", now + 20, nil, 2)              -- opening 2 asked before opening 1's put-down arrived
local rel, _, late = L:release(1, 1)
check("a put-down for an older opening than the holder's last request changes nothing (Staging, 2026-11-07)",
    rel == false and late == true and L.holder ~= nil and L.holder.player == 1 and (L:check(d2.ticket, now + 30)) == true)
rel, _, late = L:release(1, 2)
check("... the put-down for the opening it is: released", rel == true and not late and L.holder == nil)
_, d = L:request(1, "Alice", now, nil, 5)
rel = L:release(1)
check("a client that sends no count (1.0.1): its put-down still works", rel == true and L.holder == nil)
L = newLease()
_, d = L:request(1, "Alice", now)
L:release(1)
_, d2 = L:request(2, "Bob", now)
ok, who = L:check(d.ticket, now)
check("a ticket made for somebody who no longer holds the phone is refused", ok == false
    and (who == "unknown_ticket" or who == "not_the_holder"), who)
for _, bad in ipairs({ "", "XYZ", string.rep("A", 32), string.rep("g", 32), string.rep("a", 31) }) do
    ok, who = L:check(bad, now)
    check("refused: " .. string.format("%q", bad):sub(1, 40), ok == false and (who == "no_ticket" or who == "malformed_ticket"), who)
end
ok, who = L:check(nil, now)
check("refused: no ticket at all", ok == false and who == "no_ticket", who)
ok, who = L:check(string.rep("a", 32), now)
check("refused: a well-formed ticket nobody made", ok == false and who == "unknown_ticket", who)

print("== going quiet, going idle, leaving")
now = 0
L = newLease()
L:request(1, "Alice", now)
L:heartbeat(1, 0, 30000)
L:heartbeat(1, 0, 60000)
check("heartbeats keep the phone", L:expire(100000) == nil and L.holder ~= nil)
local r = L:expire(60000 + 45001)
check("45 s without one: the phone is free again (quiet)", r ~= nil and r.reason == "quiet" and r.player == 1
    and L.holder == nil, dump(r))
L = newLease()
L:request(1, "Alice", 0)
L:heartbeat(1, 600000, 1000)
check("idle for ten minutes with nobody waiting: kept", L:expire(2000) == nil and L.holder ~= nil)
L:request(2, "Bob", 3000)
L:heartbeat(1, 600000, 3500)
r = L:expire(4000)
check("idle with somebody waiting: handed on (idle)", r ~= nil and r.reason == "idle" and L.holder == nil, dump(r))
local w = L:takeWaiting()
check("... and the waiting player is handed over to be told, once", w[2] == "Bob" and next(L:takeWaiting()) == nil)
check("a heartbeat from someone who does not hold it does nothing", L:heartbeat(5, 0, 5000) == false)
L = newLease()
L:request(1, "Alice", 0)
L:request(2, "Bob", 10)
local was, ww = L:drop(1)
check("the holder leaves: free, the waiting handed over", was == true and ww[2] == "Bob" and L.holder == nil)
L:request(3, "Cy", 20)
L:request(4, "Di", 30)
was = L:drop(4)
check("a waiting player leaves: no longer waiting, the holder unchanged", was == false and L.waiting[4] == nil
    and L.holder.player == 3)

print("== this machine's own check (selftest)")
L = newLease({ selftestLimit = 3 })
L:request(1, "Alice", 0)
local st = L:selftest(0)
ok, who = L:check(st, 0, "192.34.102.143")
check("a selftest ticket opens once while somebody holds the phone, for player 0", ok == true and who == 0)
L:selftest(0); L:selftest(0)
local st4, why4 = L:selftest(0)
check("a few per start, then no more", st4 == nil and why4 == "selftest_limit")

print("== tickets are not guessable from each other")
L = newLease()
local seen, chars = {}, {}
for i = 1, 2000 do
    local t = L:token()
    seen[t] = true
    for c in t:gmatch(".") do chars[c] = (chars[c] or 0) + 1 end
end
local n, minC, maxC = 0, math.huge, 0
for _ in pairs(seen) do n = n + 1 end
for _, c in pairs(chars) do minC = math.min(minC, c); maxC = math.max(maxC, c) end
check("2000 tickets, all different, every hex digit about as common as the others", n == 2000 and minC > 3600 and maxC < 4400,
    string.format("%d different, digits %d..%d", n, minC, maxC))

print("== the web server's question (server/http.lua)")
now = 0
L = newLease()
local env = { encode = Json.encode, version = "test", configured = true }
_, d = L:request(7, "Eve", 0, "203.0.113.50:3333")
local status, bodyText, headers, logNote = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/v1/check",
    headers = { ["X-Original-URI"] = "/open77-phone-staging/ws?t=" .. d.ticket, ["X-Real-IP"] = "203.0.113.50" } }, env)
check("nginx's check with the original address and the player's own address: 204, logged without the ticket",
    status == 204 and bodyText == "" and logNote:find("let through", 1, true) ~= nil and not logNote:find(d.ticket, 1, true),
    tostring(status) .. " " .. tostring(logNote))
status, bodyText, headers, logNote = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/v1/check",
    headers = { ["x-original-uri"] = "/open77-phone-staging/ws?t=" .. d.ticket } }, env)
check("the same ticket again (header named in lower case): 403 ticket_used", status == 403
    and Json.decode(bodyText).error == "ticket_used" and logNote:find("refused", 1, true) ~= nil, bodyText)
_, d = L:request(7, "Eve", 10, "203.0.113.50:3333")
status = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/v1/check",
    headers = { { "X-Original-URI", "/open77-phone-staging/ws?x=1&t=" .. d.ticket }, { "X-Real-IP", "203.0.113.50" } } }, env)
check("headers as a list of pairs, the ticket after another parameter: 204", status == 204, status)
_, d = L:request(7, "Eve", 20, "203.0.113.50:3333")
status = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/v1/check", query = { t = d.ticket }, headers = {} }, env)
check("a direct call with ?t= in the query: 204", status == 204, status)
status, bodyText = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/v1/check",
    headers = { ["X-Original-URI"] = "/open77-phone-staging/ws" } }, env)
check("no ticket in the original address: 403 no_ticket", status == 403 and Json.decode(bodyText).error == "no_ticket", bodyText)
status = PhoneHttp.handle(L, { method = "POST", path = "/open77_phone/v1/check", headers = {} }, env)
check("POST to the check: 405", status == 405)
status, bodyText = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/v1/health", headers = {} }, env)
local h = Json.decode(bodyText)
check("health: 200, configured, held, never a ticket", status == 200 and h.ok == true and h.configured == true
    and h.held == true and not bodyText:find("ticket", 1, true), bodyText)
status, bodyText = PhoneHttp.handle(L, { method = "POST", path = "/open77_phone/v1/selftest", remoteAddress = "127.0.0.1",
    headers = {} }, env)
local stt = Json.decode(bodyText)
check("selftest from this machine: 200 and a ticket", status == 200 and PhoneLease.validTicket(stt.ticket), bodyText)
status = PhoneHttp.handle(L, { method = "POST", path = "/open77_phone/v1/selftest", remoteAddress = "127.0.0.1",
    headers = { ["X-Forwarded-For"] = "198.51.100.1" } }, env)
check("selftest passed on by a web server: 403", status == 403)
status = PhoneHttp.handle(L, { method = "POST", path = "/open77_phone/v1/selftest", remoteAddress = "198.51.100.1",
    headers = {} }, env)
check("selftest from elsewhere: 403", status == 403)
status = PhoneHttp.handle(L, { method = "POST", path = "/open77_phone/v1/selftest", remoteAddress = "127.0.0.1",
    headers = { ["X-Original-URI"] = "/open77-phone-staging/ws" } }, env)
check("selftest through nginx's check location: 403", status == 403)
status = PhoneHttp.handle(L, { method = "GET", path = "/open77_phone/nothing", headers = {} }, env)
check("anything else: 404", status == 404)

-- =====================================================================================================================
print("== the client half (client/main.lua), in a stand-in of the game's client runtime")

local function client(opts)
    opts = opts or {}
    local rt = { logs = {}, sent = {}, events = {}, pageEvents = {}, focus = {}, chat = {}, kvp = opts.kvp or {},
        threads = {}, handlers = {}, netHandlers = {}, rebinds = {}, resets = 0, mappings = {}, created = {} }
    local env = setmetatable({}, { __index = _G })
    env.print = function(s) rt.logs[#rt.logs + 1] = tostring(s) end
    env.GetCurrentResourceName = function() return "open77_phone" end
    env.GetGameTimer = function() return rt.clock or 0 end
    env.TriggerServerEvent = function(name, payload) rt.sent[#rt.sent + 1] = { name = name, payload = payload } end
    env.TriggerEvent = function(name, payload) if name == "chat:addMessage" then rt.chat[#rt.chat + 1] = payload end end
    env.RegisterNetEvent = function(name, fn) rt.netHandlers[name] = fn end
    env.AddEventHandler = function(name, fn) rt.handlers[name] = fn end
    env.CreateThread = function(fn) rt.threads[#rt.threads + 1] = coroutine.create(fn) end
    env.Wait = function(ms) coroutine.yield(ms) end
    local page = { handlers = {} }
    function page:on(name, fn) self.handlers[name] = fn; return #rt.pageEvents end
    function page:send(name, payload) rt.pageEvents[#rt.pageEvents + 1] = { name = name, payload = payload } end
    function page:setFocus(k, c) rt.focus[#rt.focus + 1] = { k, c }; return opts.focusRefused ~= true, opts.focusRefused and "refused" or nil end
    env.WebUI = { create = function(spec)
        rt.created[#rt.created + 1] = spec
        if opts.policyRefused and spec.policy ~= nil then error("unknown policy") end
        -- the game still preparing the resource's files: the first `preparing` calls answer nil, "resource_preparing"
        if opts.preparing and #rt.created <= opts.preparing then return nil, "resource_preparing" end
        return page
    end }
    local mapping = nil
    local input = {
        registerKeyMapping = not opts.noMapping and function(spec)
            if opts.noConsume and spec.consume then return false, "input_consumption_unavailable" end
            mapping = spec
            rt.mappings[#rt.mappings + 1] = spec
            return true, rt.saved or spec.key
        end or nil,
        keyFor = function(id) return mapping and (rt.saved or mapping.key) or nil end,
        rebind = function(res, id, key)
            rt.rebinds[#rt.rebinds + 1] = { res, id, key }
            if opts.rebindRefused then return false, "keybind_save_failed" end
            rt.saved = key:upper()
            return true, rt.saved
        end,
        reset = function(res, id) rt.resets = rt.resets + 1; rt.saved = nil; return true, "F1" end,
        mappings = function()
            return { { resource = "open77_phone", id = "phone.toggle", key = rt.saved or "F1", name = "Phone" },
                     { resource = "other", id = "x", key = "F2", name = "Other thing" } }
        end,
    }
    local kvpCalls = 0
    env.Open77 = { input = input, kvp = {
        get = function(k)
            kvpCalls = kvpCalls + 1
            if opts.kvpNoSession and kvpCalls <= opts.kvpNoSession then return nil, "no_session" end
            return rt.kvp[k]
        end,
        set = function(k, v) rt.kvp[k] = v; return true end } }
    env.RegisterKeyMapping = opts.legacy and function(id, name, key, fn) mapping = { id = id, key = key, onPressed = fn }; return true, key end or nil
    if opts.noMapping and not opts.legacy then env.Open77.input.registerKeyMapping = nil end
    local chunk = assert(loadfile("client/main.lua", "t", env))
    chunk()
    rt.page, rt.env = page, env
    rt.press = function() mapping.onPressed("phone.toggle") end
    rt.mapping = function() return mapping end
    rt.fromPage = function(name, payload) page.handlers[name](payload) end
    rt.fromServer = function(name, payload) rt.netHandlers[name](payload) end
    rt.last = function(name)
        for i = #rt.pageEvents, 1, -1 do if rt.pageEvents[i].name == name then return rt.pageEvents[i].payload end end
    end
    rt.lastSent = function(name)
        for i = #rt.sent, 1, -1 do if rt.sent[i].name == name then return rt.sent[i].payload end end
    end
    rt.count = function(name) local n = 0; for _, s in ipairs(rt.sent) do if s.name == name then n = n + 1 end end; return n end
    rt.log = function() return table.concat(rt.logs, "\n") end
    return rt
end

local C = client()
local spec = C.created[1]
check("the page: menu layer, 1920x1080, visible, transparent, may open the screen's WebSocket (policy=remote)",
    spec.entry == "web/phone.html" and spec.layer == "menu" and spec.visible == true and spec.transparent == true
    and spec.policy == "remote" and spec.width == 1920, dump(spec))
check("the key: F1, consumed, in the game's registry, and the log says so", C.mapping().key == "F1" and C.mapping().consume == true
    and C.log():find("key F1 (registered, consumed)", 1, true) ~= nil, C.log())
C.fromPage("phone:ready", {})
local st0 = C.last("phone:state")
check("page ready: told the state (closed, F1, the settings)", st0 and st0.open == false and st0.key == "F1"
    and st0.prefs.size == "standard", dump(st0))
C.press()
check("the key opens it: keyboard and mouse to the page, the server asked", C.focus[#C.focus][1] == true
    and C.focus[#C.focus][2] == true and C.lastSent("open77_phone:request").why == "key"
    and C.last("phone:state").open == true and C.last("phone:state").phase == "asking")
local ticket = string.rep("ab", 16)
C.fromServer("open77_phone:granted", { url = "wss://xyberrp.duckdns.org/open77-phone-staging/ws", ticket = ticket,
    ticketSeconds = 30, fresh = true, label = "Android" })
local conn = C.last("phone:connect")
check("granted: the page is given the screen and its ticket; the log names the host, never the ticket",
    conn and conn.ticket == ticket and conn.url:find("open77-phone-staging", 1, true) ~= nil
    and C.log():find("xyberrp.duckdns.org", 1, true) ~= nil and not C.log():find(ticket, 1, true))
-- the heartbeat thread: it waits ten seconds, then speaks while the phone is open and ours
local hb = C.threads[1]
coroutine.resume(hb)
coroutine.resume(hb)
check("every ten seconds while open and held: a heartbeat with the page's idle time", C.count("open77_phone:heartbeat") == 1)
C.fromPage("phone:stats", { w = 1280, h = 720, kbps = 900, ups = 24, idleMs = 12345 })
coroutine.resume(hb)
check("... carrying how long the player has not touched it", C.lastSent("open77_phone:heartbeat").idleMs == 12345)
C.fromPage("phone:report", { status = "live", detail = "1280x720, attempt 1" })
check("the page's report goes to the log and on to the server", C.lastSent("open77_phone:report").status == "live"
    and C.log():find("screen live -- 1280x720", 1, true) ~= nil and C.last("phone:state").phase == "live")
C.fromPage("phone:close", { why = "escape" })
check("the page puts it away: focus back to the game, the phone handed back, closed",
    C.focus[#C.focus][1] == false and C.lastSent("open77_phone:release").why == "escape"
    and C.last("phone:state").open == false)
coroutine.resume(hb)
check("no heartbeat once it is put away", C.count("open77_phone:heartbeat") == 2)

C.clock = (C.clock or 0) + 1000
C.press()
C.fromServer("open77_phone:busy", { holder = "Bob", heldMs = 90000 })
check("busy: the page says who has it", C.last("phone:busy").holder == "Bob" and C.last("phone:state").phase == "busy")
C.fromServer("open77_phone:free", { label = "Android" })
check("told it is free while waiting on the busy screen: asks again by itself", C.lastSent("open77_phone:request").why == "free")
C.fromPage("phone:close", { why = "key" })
local before = C.count("open77_phone:release")
C.fromServer("open77_phone:granted", { url = "wss://x/y", ticket = ticket })
check("granted after it was put away: handed straight back", C.count("open77_phone:release") == before + 1
    and C.last("phone:connect").url ~= "wss://x/y")
C.fromServer("open77_phone:free", { label = "Android" })
check("told it is free while it is away: a note in the corner", C.last("phone:free") ~= nil and C.last("phone:free").key == "F1")

C.fromPage("phone:rebind", { key = "g" })
local kr = C.last("phone:keyResult")
check("CHANGE in the phone's settings moves the key in the game's registry", C.rebinds[1][1] == "open77_phone"
    and C.rebinds[1][2] == "phone.toggle" and C.rebinds[1][3] == "g" and kr.ok == true and kr.key == "G"
    and C.last("phone:state").key == "G", dump(kr))
C.fromPage("phone:rebind", { key = "W" })
kr = C.last("phone:keyResult")
check("a key the player walks with is refused before the game is asked", #C.rebinds == 1 and kr.ok == false
    and kr.text:find("walk", 1, true) ~= nil, dump(kr))
C.fromServer("open77_phone:key", { key = "F2" })
check("/phonekey F2: moved, and the answer in the chat (another mapping on F2 is named)", C.rebinds[2][3] == "F2"
    and C.chat[#C.chat].text:find("F2", 1, true) ~= nil and C.chat[#C.chat].text:find("Other thing", 1, true) ~= nil,
    dump(C.chat[#C.chat]))
C.fromServer("open77_phone:key", { reset = true })
check("/phonekey reset: back to F1", C.resets == 1 and C.last("phone:keyResult").key == "F1")
C.fromServer("open77_phone:key", { choose = true })
check("/phonekey alone: the phone opens on its key chooser", C.last("phone:settings").capture == true
    and C.last("phone:state").open == true)
C.fromPage("phone:prefs", { size = "large", side = "left", dim = false, stats = true, pointer = "android", junk = "x" })
check("settings saved for this server: size, side, dim, numbers, pointer", C.kvp["phone.size"] == "large"
    and C.kvp["phone.side"] == "left" and C.kvp["phone.dim"] == false and C.kvp["phone.stats"] == true
    and C.kvp["phone.pointer"] == "android")
C.fromPage("phone:prefs", { size = "gigantic", side = "up" })
check("nonsense settings are ignored", C.kvp["phone.size"] == "large" and C.kvp["phone.side"] == "left")
local C2 = client({ kvp = C.kvp })
C2.fromPage("phone:ready", {})
C2.press()
local p2 = C2.last("phone:state").prefs
check("the next session opens with them", p2.size == "large" and p2.side == "left" and p2.dim == false and p2.stats == true
    and p2.pointer == "android", dump(p2))
C2.fromServer("open77_phone:revoked", { reason = "idle" })
check("taken back: the page is told why", C2.last("phone:revoked").reason == "idle" and C2.last("phone:state").phase == "revoked")
C2.fromServer("open77_phone:denied", { reason = "not_configured" })
check("not set up on this server: the page is told", C2.last("phone:error").reason == "not_configured")
C2.fromServer("open77_phone:toggle", {})
check("/phone puts it away again", C2.last("phone:state").open == false)

local C3 = client({ noConsume = true })
check("a game that cannot consume keys: the key is registered to be observed", C3.mapping().consume == nil
    and C3.log():find("registered, observed", 1, true) ~= nil, C3.log())
local C4 = client({ noMapping = true })
C4.fromPage("phone:ready", {})
check("a game with no key registry: /phone still works, the page says the key cannot move",
    C4.last("phone:state").keyMode == "none" and C4.log():find("type /phone", 1, true) ~= nil)
C4.fromPage("phone:rebind", { key = "G" })
check("... and CHANGE says to use the game's key bindings", C4.last("phone:keyResult").ok == false
    and C4.last("phone:keyResult").text:find("Key bindings", 1, true) ~= nil)
local C5 = client({ policyRefused = true })
check("a game that does not know policy=remote: the page is made without it, and the log says so",
    #C5.created == 2 and C5.created[2].policy == nil and C5.log():find("WITHOUT policy=remote", 1, true) ~= nil)
local C6 = client({ rebindRefused = true })
C6.fromPage("phone:ready", {})
C6.fromPage("phone:rebind", { key = "G" })
check("the game refusing a key: said, with its reason", C6.last("phone:keyResult").ok == false
    and C6.last("phone:keyResult").text:find("keybind_save_failed", 1, true) ~= nil)
local C7 = client({ focusRefused = true })
C7.press()
check("focus refused: the log says the phone key still closes it", C7.log():find("NO (refused)", 1, true) ~= nil)
check("no log line anywhere holds a ticket", not C.log():find(ticket, 1, true) and not C2.log():find(ticket, 1, true))

-- Staging, 2026-11-07: the page put the phone away on F1, and the game's binding saw the same press 20 ms later
local G = client()
G.fromPage("phone:ready", {})
G.clock = 1000
G.press()
G.fromServer("open77_phone:granted", { url = "wss://xyberrp.duckdns.org/open77-phone-staging/ws", ticket = ticket, fresh = true })
G.clock = 9000
G.fromPage("phone:close", { why = "key" })
local asked = G.count("open77_phone:request")
G.clock = 9020
G.press()
check("the phone key 20 ms after the page put the phone away: the same press -- ignored, and the log says so",
    G.count("open77_phone:request") == asked and G.last("phone:state").open == false
    and G.log():find("the phone key 20 ms after the phone put itself away: the same press, ignored", 1, true) ~= nil, G.log())
G.clock = 9700
G.press()
check("... 700 ms later it is a new press: the phone opens", G.count("open77_phone:request") == asked + 1
    and G.last("phone:state").open == true)
local r1, r2 = G.sent[1].payload, G.lastSent("open77_phone:request")
check("each opening is counted, and its put-down names it (the server ignores a late one)", r1.seq == 1 and r2.seq == 2
    and G.lastSent("open77_phone:release").seq == 1 and G.log():find("opened (key, opening 2)", 1, true) ~= nil, dump(r2))
-- no answer: asked again after 3 s, three times in all, then the player is told
local ask = G.threads[#G.threads]
local function tick(ms) G.clock = G.clock + ms; coroutine.resume(ask) end
coroutine.resume(ask)                                    -- the thread starts and waits
tick(1000)
check("no answer yet after 1 s: nothing asked again", G.count("open77_phone:request") == asked + 1)
tick(2500)
check("no answer in 3 s: asked again (try 2 of 3)", G.count("open77_phone:request") == asked + 2
    and G.lastSent("open77_phone:request").why == "no answer" and G.lastSent("open77_phone:request").seq == 2
    and G.log():find("no answer from the server in 3 s; asking again (try 2 of 3)", 1, true) ~= nil, G.log())
tick(3000)
tick(3000)
check("three unanswered asks: the player is told (no_answer), nothing more is asked",
    G.count("open77_phone:request") == asked + 3 and G.last("phone:error").reason == "no_answer"
    and G.last("phone:state").phase == "error" and G.log():find("did not answer 3 times; told the player", 1, true) ~= nil)
tick(3000)
check("... and it stays told", G.count("open77_phone:request") == asked + 3)
local H = client()
H.fromPage("phone:ready", {})
H.press()
local hask = H.threads[#H.threads]
coroutine.resume(hask)
H.fromServer("open77_phone:busy", { holder = "Bob", heldMs = 1000 })
H.clock = 10000
coroutine.resume(hask)
check("an answer (busy) stops the clock: no ask again while waiting in line", H.count("open77_phone:request") == 1)

-- Staging, 2026-11-07: the game was still preparing the resource's files when the client half started, so the page
-- was never made and F1 did nothing anyone could see.
local C8 = client({ preparing = 2 })
check("files still being prepared at the start: no page, no try without policy=remote, and the log says why",
    #C8.created == 1 and C8.created[1].policy == "remote"
    and C8.log():find("no page yet (resource_preparing; try 1, start): the game is still preparing", 1, true) ~= nil, C8.log())
C8.press()
check("the key while there is no page: tried again at once, still none -- not opened, the server not asked, the player told",
    #C8.created == 2 and C8.count("open77_phone:request") == 0 and #C8.chat == 1
    and C8.chat[1].text:find("still loading", 1, true) ~= nil and C8.chat[1].text:find("F1", 1, true) ~= nil
    and C8.log():find("not opened (key): no page yet (resource_preparing, 2 tries); told the player", 1, true) ~= nil,
    C8.log() .. " | chat: " .. dump(C8.chat))
local retry = C8.threads[2]
local okr, waitMs = coroutine.resume(retry)
check("a thread tries again every 3 s", okr and waitMs == 3000, tostring(waitMs))
C8.clock = 7400
coroutine.resume(retry)
check("... and makes the page once the files are ready, with policy=remote; the log says on which try and how long after",
    #C8.created == 3 and C8.created[3].policy == "remote"
    and C8.log():find("page created (may reach the phone's screen) on try 3, 7.4 s after the first (retry)", 1, true) ~= nil
    and coroutine.status(retry) == "dead", C8.log())
C8.fromPage("phone:ready", {})
C8.press()
check("then the key opens the phone as usual", C8.count("open77_phone:request") == 1 and C8.last("phone:state").open == true)
local C8b = client({ preparing = 1 })
C8b.press()
check("the files ready by the first press: the page is made then, and the phone opens", #C8b.created == 2
    and C8b.count("open77_phone:request") == 1 and #C8b.chat == 0
    and C8b.log():find("page created (may reach the phone's screen) on try 2", 1, true) ~= nil, C8b.log())
local C8c = client({ preparing = 60 })
local r3 = C8c.threads[2]
local waits = {}
for _ = 1, 45 do local _, w = coroutine.resume(r3); waits[#waits + 1] = w end
local logged = select(2, C8c.log():gsub("no page yet", ""))
check("a page that never comes: tried every 3 s, then every 30 s; logged on the 1st, 10th, 20th... try, not every one",
    waits[1] == 3000 and waits[39] == 3000 and waits[41] == 30000 and logged == 5, dump(waits) .. " logged " .. logged)

local C9 = client({ kvp = { ["phone.size"] = "compact" }, kvpNoSession = 1 })
local ahead = C9.threads[2]
coroutine.resume(ahead)
coroutine.resume(ahead)
check("settings read ahead: not before the game's session answers", not C9.log():find("settings: ", 1, true), C9.log())
coroutine.resume(ahead)
check("... then once, with how long it took (the first press waited 78 ms on something on Staging)",
    C9.log():find("settings: compact, on the right, game dimmed, numbers hidden, pointer game (read ahead, in 0 ms)", 1, true) ~= nil
    and coroutine.status(ahead) == "dead", C9.log())
C9.press()
check("... and the first press does not read them again", select(2, C9.log():gsub("settings: ", "")) == 1, C9.log())


-- =====================================================================================================================
print("== the server half (server/main.lua), in a stand-in of the dedicated server")

local function server(opts)
    opts = opts or {}
    local rt = { logs = {}, toClient = {}, net = {}, handlers = {}, commands = {}, threads = {}, clock = 1000,
        printed = {} }
    local env = setmetatable({}, { __index = _G })
    env.print = function(s) rt.logs[#rt.logs + 1] = tostring(s) end
    env.json = Json
    env.GetGameTimer = function() return rt.clock end
    env.GetPlayerName = function(p) return ({ [1] = "Alice", [2] = "Bob", [3] = "Cy" })[p] end
    env.RegisterNetEvent = function(name, fn) rt.net[name] = fn end
    env.TriggerClientEvent = function(name, target, ...) rt.toClient[#rt.toClient + 1] = { name = name, target = target, args = { ... } } end
    env.AddEventHandler = function(name, fn) rt.handlers[name] = fn end
    env.RegisterCommand = function(name, fn) rt.commands[name] = fn end
    env.CreateThread = function(fn) rt.threads[#rt.threads + 1] = coroutine.create(fn) end
    env.Wait = function(ms) coroutine.yield(ms) end
    env.Open77 = { http = { listen = function(prefix, fn) rt.route = fn; return "/open77_phone/" end },
        players = { endpoint = function(p) return ({ [1] = "203.0.113.5:40000", [2] = "198.51.100.7:1" })[p] end } }
    env.PhoneConfig = { wsUrl = opts.wsUrl == nil and "wss://xyberrp.duckdns.org/open77-phone-staging/ws" or opts.wsUrl }
    for _, f in ipairs({ "server/lease.lua", "server/http.lua", "server/main.lua" }) do
        assert(loadfile(f, "t", env))()
    end
    rt.as = function(player, event, payload) env.source = player; rt.net[event](payload); env.source = nil end
    rt.cmd = function(player, name, args) rt.commands[name](player, args or {}, "/" .. name) end
    rt.http = function(req)
        local out = {}
        rt.route(req, { send = function(status, body, headers) out.status, out.body, out.headers = status, body, headers end })
        return out
    end
    rt.lastTo = function(target, name)
        for i = #rt.toClient, 1, -1 do
            local m = rt.toClient[i]
            if m.target == target and m.name == name then return m.args[1] or {}, m end
        end
    end
    rt.log = function() return table.concat(rt.logs, "\n") end
    return rt
end

local S = server()
check("the start line: version, where the screen is, the web server's check on its route",
    S.log():find("v1.0.2", 1, true) ~= nil and S.log():find("the screen is wss://xyberrp.duckdns.org/open77-phone-staging/ws", 1, true) ~= nil
    and S.log():find("check on /open77_phone/", 1, true) ~= nil, S.log())
S.as(1, "open77_phone:request", { why = "key" })
local g = S.lastTo(1, "open77_phone:granted")
check("Alice asks: granted, with the screen's address and a ticket", g ~= nil and g.url == "wss://xyberrp.duckdns.org/open77-phone-staging/ws"
    and PhoneLease.validTicket(g.ticket), dump(g))
check("... logged without the ticket", not S.log():find(g.ticket, 1, true) and S.log():find("Alice", 1, true) ~= nil)
local n0 = #S.toClient
S.as(1, "open77_phone:request", { why = "key" })
check("asked again within a second: ignored (no second ticket)", #S.toClient == n0 and S.log():find("ignored", 1, true) ~= nil)
S.as(2, "open77_phone:request", { why = "key" })
local b = S.lastTo(2, "open77_phone:busy")
do
    local T = server()
    T.as(1, "open77_phone:request", { why = "key", seq = 1 })
    T.clock = T.clock + 1500
    T.as(1, "open77_phone:request", { why = "key", seq = 2 })     -- opening 2, ahead of opening 1's put-down
    local g2 = T.lastTo(1, "open77_phone:granted")
    T.as(1, "open77_phone:release", { why = "key", seq = 1 })
    local st = T.http({ method = "GET", path = "/open77_phone/v1/check",
        headers = { ["X-Original-URI"] = "/open77-phone-staging/ws?t=" .. g2.ticket, ["X-Real-IP"] = "203.0.113.5" } })
    check("the server: a put-down for opening 1 after opening 2's request is ignored -- the newer ticket still opens",
        st.status == 204 and T.log():find("a put-down for opening 1 came after a newer opening's request; ignored", 1, true) ~= nil,
        T.log())
    T.as(1, "open77_phone:release", { why = "key", seq = 2 })
    check("... and opening 2's put-down frees it", T.log():find("put the phone down (key)", 1, true) ~= nil)
end
check("Bob asks: busy, Alice has it", b ~= nil and b.holder == "Alice", dump(b))
local res = S.http({ method = "GET", path = "/open77_phone/v1/check",
    headers = { ["X-Original-URI"] = "/open77-phone-staging/ws?t=" .. g.ticket, ["X-Real-IP"] = "203.0.113.5" } })
check("the web server asks about Alice's ticket from Alice's address: 204, and the log says so",
    res.status == 204 and S.log():find("let through (player 1; original; the player's own address)", 1, true) ~= nil, S.log())
res = S.http({ method = "GET", path = "/open77_phone/v1/check", headers = { ["X-Original-URI"] = "/open77-phone-staging/ws?t=" .. g.ticket } })
check("... and again: 403", res.status == 403)
S.as(2, "open77_phone:heartbeat", { idleMs = 0 })
check("a heartbeat from Bob, who does not hold it: told it is not his", S.lastTo(2, "open77_phone:revoked").reason == "lost")
S.as(1, "open77_phone:report", { status = "live", detail = "1280x720, attempt 1" })
check("Alice's page report reaches the server's log", S.log():find("player 1 (Alice): the phone live -- 1280x720", 1, true) ~= nil)
S.as(1, "open77_phone:release", { why = "escape" })
check("Alice puts it down: Bob is told it is free", S.lastTo(2, "open77_phone:free") ~= nil
    and S.log():find("put the phone down (escape)", 1, true) ~= nil)
S.clock = S.clock + 2000
S.as(2, "open77_phone:request", { why = "free" })
check("Bob gets it", S.lastTo(2, "open77_phone:granted") ~= nil)
S.clock = S.clock + 2000
S.as(3, "open77_phone:request", { why = "key" })
-- the server's own clock runs on: Bob's client goes quiet
local tick = S.threads[1]
coroutine.resume(tick)
S.clock = S.clock + 46000
coroutine.resume(tick)
check("Bob's client goes quiet for 45 s: he is told, and Cy (waiting) is told it is free",
    S.lastTo(2, "open77_phone:revoked").reason == "quiet" and S.lastTo(3, "open77_phone:free") ~= nil, S.log())
S.clock = S.clock + 2000
S.as(3, "open77_phone:request", {})
S.handlers["onPlayerDisconnected"]("3")
check("Cy leaves while holding it: the log says it is free", S.log():find("player 3 left while holding the phone", 1, true) ~= nil)
S.cmd(1, "phone")
check("/phone: the player's game is told to open or put it away", S.lastTo(1, "open77_phone:toggle") ~= nil)
S.cmd(1, "phonekey", { "page", "up" })
check("/phonekey page up: the key PAGEUP asked for, and answered", S.lastTo(1, "open77_phone:key").key == "pageup"
    and S.lastTo(1, "open77:command:result") ~= nil)
S.cmd(1, "phonekey", { "reset" })
check("/phonekey reset", S.lastTo(1, "open77_phone:key").reset == true)
S.cmd(1, "phonekey", {})
check("/phonekey alone: the chooser", S.lastTo(1, "open77_phone:key").choose == true)
S.cmd(1, "phonestatus")
local sr = select(2, S.lastTo(1, "open77:command:result"))
check("/phonestatus: free or who has it, never a ticket", sr ~= nil and tostring(sr.args[3]):find("Phone (Android): free", 1, true) ~= nil,
    sr and dump(sr.args))
S.cmd(0, "phonestatus")
check("/phonestatus at the console: the counts too", S.log():find("checks", 1, true) ~= nil)
S.cmd(0, "phone")
check("/phone at the console: says it is a player's", S.log():find("type /phone in game", 1, true) ~= nil)
local S2 = server({ wsUrl = "" })
S2.as(1, "open77_phone:request", {})
check("a server with no screen set up: the player is told, the log says why",
    S2.lastTo(1, "open77_phone:denied").reason == "not_configured" and S2.log():find("NOT set up (wsUrl empty)", 1, true) ~= nil)
local S3 = server({ wsUrl = "http://example.com/x" })
check("an address that is not wss://host/path is not used", S3.log():find("NOT set up (wsUrl not wss://host/path)", 1, true) ~= nil)
local allLogs = S.log() .. S2.log()
check("no server log line holds a ticket or a player's address", not allLogs:find(g.ticket, 1, true)
    and not allLogs:find("203.0.113.5", 1, true) and not allLogs:find("198.51.100.7", 1, true))

print(string.format("\n%d checks passed, %d failed", passed, failed))
os.exit(failed == 0 and 0 or 1)
