-- open77_phone -- server/main.lua
--
-- The server half: who holds the phone (server/lease.lua), the web server's
-- ticket check (server/http.lua, through Open77.http.listen), the players'
-- requests, and the three commands. Everything it decides goes to the log,
-- "[open77_phone] ..." -- never a ticket, never an address.

local VERSION = "1.0.2"
local CFG = type(PhoneConfig) == "table" and PhoneConfig or {}
local WS_URL = type(CFG.wsUrl) == "string" and CFG.wsUrl or ""
local LABEL = type(CFG.label) == "string" and CFG.label ~= "" and CFG.label:sub(1, 40) or "Android"
-- wss://host[:port]/path -- the only shape the page will be given.
local CONFIGURED = WS_URL:match("^wss://[%w%.%-]+:?%d*/[%w%-%._/]*$") ~= nil
    or WS_URL:match("^ws://127%.0%.0%.1:%d+/[%w%-%._/]*$") ~= nil    -- (a rehearsal on one machine)

local function log(fmt, ...)
    print("[open77_phone] " .. string.format(fmt, ...))
end

local lease = PhoneLease.new({
    clock = function() return GetGameTimer() end,
    random = math.random,
    ticketSeconds = CFG.ticketSeconds,
    leaseSeconds = CFG.leaseSeconds,
    idleSeconds = CFG.idleSeconds,
})

local function nameOf(player)
    if type(GetPlayerName) == "function" then
        local ok, name = pcall(GetPlayerName, player)
        if ok and type(name) == "string" and name ~= "" then return name:sub(1, 48) end
    end
    return "player " .. tostring(player)
end

-- The address a player plays from, so their tickets open from nowhere else
-- (server/lease.lua). Declared in the manifest as players.identity.sensitive;
-- read here and nowhere else, kept with the ticket, never logged.
local endpointNote = nil
local function addressOf(player)
    local fn = type(Open77.players) == "table" and Open77.players.endpoint or nil
    if type(fn) ~= "function" then
        endpointNote = endpointNote or "this server cannot say where players play from: tickets are not tied to an address"
        return nil
    end
    local ok, value, reason = pcall(fn, player)
    if ok and type(value) == "string" and value ~= "" then return value end
    if endpointNote == nil then
        endpointNote = "a player's address could not be read (" .. tostring(ok and reason or value) ..
            "): tickets are not tied to an address"
        log("%s", endpointNote)
    end
    return nil
end

local function send(player, event, payload)
    TriggerClientEvent(event, player, payload or {})
end

local function tellFree(waiting)
    local n = 0
    for player in pairs(waiting or {}) do
        send(player, "open77_phone:free", { label = LABEL })
        n = n + 1
    end
    if n > 0 then log("the phone is free: told %d waiting player(s)", n) end
end

local function seconds(ms)
    return string.format("%.0f s", (tonumber(ms) or 0) / 1000)
end

-- One request a second per player: a held key or a looping page is not a reason
-- to mint a hundred tickets.
local lastAsk = {}

-- A count the client sends (its openings), or nil when it is not a whole number.
local function wholeNumber(v)
    local n = tonumber(v)
    if n == nil or n ~= n or n < 0 or n > 2 ^ 31 or n ~= math.floor(n) then return nil end
    return math.floor(n)
end

RegisterNetEvent("open77_phone:request", function(payload)
    local source = source
    if source == nil or source <= 0 then return end
    local now = GetGameTimer()
    if lastAsk[source] ~= nil and now - lastAsk[source] < 1000 then
        log("player %d (%s): asked again within a second; ignored", source, nameOf(source))
        return
    end
    lastAsk[source] = now
    local why = type(payload) == "table" and tostring(payload.why or ""):sub(1, 24) or ""
    if not CONFIGURED then
        log("player %d (%s): asked for the phone (%s); this server has no phone screen set up (wsUrl)", source,
            nameOf(source), why)
        send(source, "open77_phone:denied", { reason = "not_configured", label = LABEL })
        return
    end
    local name = nameOf(source)
    local seq = type(payload) == "table" and wholeNumber(payload.seq) or nil
    local what, detail, revoked = lease:request(source, name, now, addressOf(source), seq)
    if revoked ~= nil then
        log("player %d (%s) lost the phone (%s, after %s)", revoked.player, revoked.name, revoked.reason,
            seconds(revoked.heldMs))
        send(revoked.player, "open77_phone:revoked", { reason = revoked.reason, label = LABEL })
    end
    if what == "granted" then
        log("player %d (%s): the phone is theirs (%s; %s); a ticket for %d s", source, name,
            detail.fresh and "picked up" or "still theirs", why ~= "" and why or "asked", detail.ticketSeconds)
        send(source, "open77_phone:granted", { url = WS_URL, ticket = detail.ticket, ticketSeconds = detail.ticketSeconds,
            fresh = detail.fresh, label = LABEL })
    else
        log("player %d (%s): asked for the phone; %s has it (for %s)", source, name, detail.holder, seconds(detail.heldMs))
        send(source, "open77_phone:busy", { holder = detail.holder, heldMs = detail.heldMs, label = LABEL })
    end
end)

RegisterNetEvent("open77_phone:heartbeat", function(payload)
    local source = source
    if source == nil or source <= 0 then return end
    local idle = type(payload) == "table" and tonumber(payload.idleMs) or 0
    if not lease:heartbeat(source, idle) then
        -- The client believes it holds the phone and the server does not: say so,
        -- so its page stops showing a screen it no longer owns.
        log("player %d (%s): a heartbeat for a phone they no longer hold; told", source, nameOf(source))
        send(source, "open77_phone:revoked", { reason = "lost", label = LABEL })
    end
end)

RegisterNetEvent("open77_phone:release", function(payload)
    local source = source
    if source == nil or source <= 0 then return end
    local seq = type(payload) == "table" and wholeNumber(payload.seq) or nil
    local released, waiting, late = lease:release(source, seq)
    if late then
        log("player %d (%s): a put-down for opening %s came after a newer opening's request; ignored (the phone "
            .. "stays theirs)", source, nameOf(source), tostring(seq))
    elseif released then
        local why = type(payload) == "table" and tostring(payload.why or ""):sub(1, 24) or ""
        log("player %d (%s): put the phone down (%s)", source, nameOf(source), why ~= "" and why or "closed")
        tellFree(waiting)
    end
end)

-- What the player's page saw: connected (and at what size), lost, failed. The
-- server's log is where an operator reads a session end to end.
RegisterNetEvent("open77_phone:report", function(payload)
    local source = source
    if source == nil or source <= 0 or type(payload) ~= "table" then return end
    local status = tostring(payload.status or "?"):gsub("[^%w_%-]", ""):sub(1, 24)
    local detail = tostring(payload.detail or ""):gsub("[%c]", " "):sub(1, 160)
    log("player %d (%s): the phone %s%s", source, nameOf(source), status, detail ~= "" and (" -- " .. detail) or "")
end)

local function dropped(player)
    player = tonumber(player)
    if player == nil then return end
    lastAsk[player] = nil
    local wasHolder, waiting = lease:drop(player)
    if wasHolder then
        log("player %d left while holding the phone; it is free", player)
        tellFree(waiting)
    end
end
AddEventHandler("onPlayerDisconnected", function(playerId) dropped(playerId) end)
AddEventHandler("playerDropped", function() dropped(source) end)

CreateThread(function()
    while true do
        Wait(5000)
        local ok, revoked = pcall(lease.expire, lease)
        if ok and revoked ~= nil then
            log("player %d (%s) lost the phone (%s, after %s)", revoked.player, revoked.name, revoked.reason,
                seconds(revoked.heldMs))
            send(revoked.player, "open77_phone:revoked", { reason = revoked.reason, label = LABEL })
            tellFree(lease:takeWaiting())
        end
    end
end)

-- =============================================================================
-- THE WEB SERVER'S QUESTION
-- =============================================================================

local httpRoute, httpReason = nil, nil
local function listen()
    if type(Open77.http) ~= "table" or type(Open77.http.listen) ~= "function" then
        httpReason = "no Open77.http.listen on this server"
        return
    end
    local env = { encode = json.encode, version = VERSION, configured = CONFIGURED }
    local ok, route, reason = pcall(Open77.http.listen, "/", function(req, res)
        local handled, status, body, headers, note = pcall(PhoneHttp.handle, lease, req, env)
        if not handled then
            log("the web server's request failed here: %s", tostring(status))
            res.send(500, json.encode({ ok = false, error = "handler_error" }), { ["Content-Type"] = "application/json" })
            return
        end
        if note ~= nil then log("%s", note) end
        res.send(status, body, headers)
    end)
    if ok and route then
        httpRoute = route
    else
        httpReason = tostring(ok and reason or route)
    end
end
listen()

-- =============================================================================
-- COMMANDS, FOR EVERY PLAYER
-- =============================================================================
-- A typed command reaches the server whatever the client could have done with it
-- (the chat sends every `/` line to the host's dispatcher), so the three commands
-- live here and send the one client its answer.

local function tell(source, raw, success, text)
    if source == nil or source <= 0 then
        print(text)
        return
    end
    TriggerClientEvent("open77:command:result", source, raw or "", success == true, text)
    if success == true then
        TriggerClientEvent("opx:net:runtime:commandAnswer", source, raw or "", "success", text, false)
    end
end

RegisterCommand("phone", function(source, args, raw)
    if source == nil or source <= 0 then
        return tell(source, raw, false, "the phone belongs to a player; type /phone in game")
    end
    send(source, "open77_phone:toggle", {})
end, false)

RegisterCommand("phonekey", function(source, args, raw)
    if source == nil or source <= 0 then
        return tell(source, raw, false, "the phone key belongs to a player; type /phonekey in game")
    end
    -- Joined without spaces, so `/phonekey page up` is the Page Up key.
    local wanted = table.concat(args or {}, ""):sub(1, 24)
    if wanted == "" then
        send(source, "open77_phone:key", { choose = true })
        return tell(source, raw, true, "Phone: the key chooser is open on your phone")
    end
    if wanted:lower() == "reset" then
        send(source, "open77_phone:key", { reset = true })
        return tell(source, raw, true, "Phone: asked your game to put the phone key back to its default")
    end
    send(source, "open77_phone:key", { key = wanted })
    tell(source, raw, true, "Phone: asked your game to move the phone key to " .. wanted)
end, false)

RegisterCommand("phonestatus", function(source, args, raw)
    local s = lease:status()
    local text
    if not CONFIGURED then
        text = "Phone: not set up on this server"
    elseif s.holder ~= nil then
        text = string.format("Phone (%s): %s has it (for %s); %d waiting", LABEL, s.holder, seconds(s.heldMs), s.waiting)
    else
        text = string.format("Phone (%s): free; %d waiting", LABEL, s.waiting)
    end
    if source == nil or source <= 0 then
        text = text .. string.format(" | asked %d, busy %d, checks %d (refused %d), handed back %d",
            s.counts.granted + s.counts.busy, s.counts.busy, s.counts.checked, s.counts.refused, s.counts.revoked)
    end
    tell(source, raw, true, text)
end, false)

-- What this server's phone is, said once at start: the first line to read when
-- the phone does not do what a player expected.
log("v%s: %s; the web server's check %s; a ticket opens once within %d s, a quiet holder loses the phone after "
    .. "%d s, an idle one after %d s if somebody waits",
    VERSION,
    CONFIGURED and ("the screen is " .. WS_URL) or ("NOT set up (wsUrl " .. (WS_URL == "" and "empty" or "not wss://host/path") .. ")"),
    httpRoute and ("on " .. tostring(httpRoute)) or ("UNAVAILABLE: " .. tostring(httpReason)),
    lease.ticketMs // 1000, lease.leaseMs // 1000, lease.idleMs // 1000)
