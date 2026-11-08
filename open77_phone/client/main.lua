-- open77_phone -- client/main.lua
--
-- The phone on the player's screen: one WebUI page (web/phone.html), drawn over
-- the game, that shows the server's Android and lets the player drive it with
-- the mouse and keyboard. This half owns the key, the page's focus, the talk
-- with the server (who holds the phone, the ticket that opens its screen) and the
-- player's settings; the page owns the picture.
--
-- THE KEY. "Phone: open / close" in the game's own registry: F1 until the player
-- moves it -- in Pause > Settings > Key bindings, in the phone's own settings
-- (CHANGE takes the next key pressed), or with /phonekey <key>. The registry
-- keeps the choice on the player's machine, for every server. Registered as a
-- consuming mapping where the game can, so the key does nothing else while it
-- opens the phone. While the phone is open the page has the keyboard and the
-- registry is paused, so the page closes itself on the same key (it is told
-- which) and on Escape.
--
-- Every step goes to the client log as "[open77_phone] ..."; what the page sees
-- comes back as "[open77_phone] page: ...". No ticket is ever logged.

local KEY_ID = "phone.toggle"
local KEY_NAME = "Phone: open / close"
local DEFAULT_KEY = "F1"
local HEARTBEAT_MS = 10000
local RESOURCE = "open77_phone"
if type(GetCurrentResourceName) == "function" then
    local ok, name = pcall(GetCurrentResourceName)
    if ok and type(name) == "string" and name ~= "" then RESOURCE = name end
end

-- Keys the phone may not take: the ones a player walks, aims, talks and
-- interacts with. A consuming mapping on W would stop the player walking.
local FORBIDDEN = {
    W = true, A = true, S = true, D = true, SPACE = true, SHIFT = true, CTRL = true, CONTROL = true, ALT = true,
    TAB = true, ENTER = true, RETURN = true, ESC = true, ESCAPE = true, T = true, F = true, E = true, R = true,
    BACKSPACE = true, MOUSE1 = true, MOUSE2 = true,
}

local SIZES = { compact = true, standard = true, large = true }
local SIDES = { left = true, center = true, right = true }
local POINTERS = { game = true, android = true }

local function log(fmt, ...)
    print("[open77_phone] " .. string.format(fmt, ...))
end

local page, pageReady = nil, false
local isOpen, holding = false, false
local phase = "closed"           -- closed | asking | connecting | live | busy | error | revoked
local label = "Android"
local keyMode = "none"           -- mapping | mapping-observed | legacy | none
local keyNow = DEFAULT_KEY
local prefs = { size = "standard", side = "right", dim = true, stats = false, pointer = "game" }
local prefsLoaded = false
local pendingConnect = nil       -- what the page is to open once it is ready
local idleMs = 0
local lastStatsLog = 0
local reportsSent = 0
local openSeq = 0                -- this client's openings, counted: the server ignores a put-down for an older one
local lastPageClose = -100000    -- when the page last put the phone away (its own key, Escape, a button)
local askedAt, askTries = nil, 0 -- the server asked and not answered yet: since when, how many times
-- The game's key binding right after the page put the phone away is the same press reaching the game once the
-- page let go of the keyboard (Staging, 2026-11-07: closed, and opened again 20 ms later). The page now acts on
-- the key's release; this is the second lock on the same door.
local PAGE_CLOSE_GUARD_MS = 500
local ASK_TIMEOUT_MS, ASK_TRIES = 3000, 3

local function sendPage(event, payload)
    if page ~= nil and pageReady then page:send(event, payload or {}) end
end

local function hostOf(url)
    return tostring(url or ""):match("^%a+://([^/]+)") or "?"
end

-- =============================================================================
-- THE KEY
-- =============================================================================

local function currentKey()
    local input = type(Open77) == "table" and Open77.input or nil
    if input ~= nil and type(input.keyFor) == "function" then
        local ok, key = pcall(input.keyFor, KEY_ID)
        if ok and type(key) == "string" and key ~= "" then return key end
    end
    return keyNow
end

local function pushState()
    keyNow = currentKey()
    sendPage("phone:state", { open = isOpen, phase = phase, key = keyNow, keyMode = keyMode, prefs = prefs,
        label = label, holding = holding })
end

local toggle -- (below)

local function alsoBound(key)
    local input = type(Open77) == "table" and Open77.input or nil
    if input == nil or type(input.mappings) ~= "function" then return {} end
    local ok, list = pcall(input.mappings)
    if not ok or type(list) ~= "table" then return {} end
    local out = {}
    for _, m in ipairs(list) do
        if type(m) == "table" and tostring(m.key or ""):upper() == tostring(key):upper()
            and not (m.resource == RESOURCE and m.id == KEY_ID) then
            out[#out + 1] = string.format("%s (%s)", tostring(m.name or m.id), tostring(m.resource))
        end
    end
    return out
end

local function registerKey()
    local input = type(Open77) == "table" and Open77.input or nil
    local function pressed()
        local since = GetGameTimer() - lastPageClose
        if not isOpen and since >= 0 and since < PAGE_CLOSE_GUARD_MS then
            log("the phone key %d ms after the phone put itself away: the same press, ignored", since)
            return
        end
        toggle("key")
    end
    if input ~= nil and type(input.registerKeyMapping) == "function" then
        local spec = { id = KEY_ID, name = KEY_NAME, key = DEFAULT_KEY, consume = true, onPressed = pressed }
        local ok, effective = input.registerKeyMapping(spec)
        if ok then
            keyMode = "mapping"
        elseif tostring(effective):find("consumption_unavailable", 1, true) then
            spec.consume = nil
            ok, effective = input.registerKeyMapping(spec)
            if ok then keyMode = "mapping-observed" end
        end
        if ok then
            keyNow = tostring(effective or DEFAULT_KEY)
            local others = alsoBound(keyNow)
            log("key %s (%s)%s", keyNow, keyMode == "mapping" and "registered, consumed" or "registered, observed",
                #others > 0 and ("; also bound to it: " .. table.concat(others, ", ")) or "")
            return
        end
        log("the key could not be registered (%s); type /phone to open the phone", tostring(effective))
    elseif type(RegisterKeyMapping) == "function" then
        local ok, effective = RegisterKeyMapping(KEY_ID, KEY_NAME, DEFAULT_KEY, pressed)
        if ok then
            keyMode = "legacy"
            keyNow = tostring(effective or DEFAULT_KEY)
            log("key %s (registered)", keyNow)
            return
        end
        log("the key could not be registered (%s); type /phone to open the phone", tostring(effective))
    else
        log("this game cannot register a key; type /phone to open the phone")
    end
    keyMode = "none"
end

local function chat(ok, text)
    TriggerEvent("chat:addMessage", { type = ok and "info" or "error", author = "Phone", text = text })
end

---Moves the phone key. `from` is "page" (the phone's settings) or "command"
---(/phonekey); the answer goes back the same way.
local function rebind(key, from)
    local function answer(ok, text)
        log("key change (%s): %s", from, text)
        if from == "command" then chat(ok, text) end
        sendPage("phone:keyResult", { ok = ok, key = keyNow, text = text })
        pushState()
    end
    if type(key) ~= "string" or key == "" or #key > 24 then return answer(false, "no key was named") end
    local wanted = key:upper()
    if FORBIDDEN[wanted] then
        return answer(false, wanted .. " is a key you walk, aim, talk or interact with; pick another")
    end
    local input = type(Open77) == "table" and Open77.input or nil
    if keyMode == "none" or input == nil or type(input.rebind) ~= "function" then
        return answer(false, "this game cannot move the key from here; use Pause > Settings > Key bindings")
    end
    local ok, result = input.rebind(RESOURCE, KEY_ID, key)
    if not ok then
        return answer(false, "the game refused " .. key .. " (" .. tostring(result) .. ")")
    end
    keyNow = tostring(result or key)
    local others = alsoBound(keyNow)
    answer(true, "the phone opens with " .. keyNow .. " now" ..
        (#others > 0 and (" (also bound to it: " .. table.concat(others, ", ") .. ")") or ""))
end

local function resetKey(from)
    local input = type(Open77) == "table" and Open77.input or nil
    local ok, result = false, "this game cannot reset the key from here"
    if keyMode ~= "none" and input ~= nil and type(input.reset) == "function" then
        ok, result = input.reset(RESOURCE, KEY_ID)
    end
    if ok then keyNow = tostring(result or DEFAULT_KEY) end
    local text = ok and ("the phone opens with " .. keyNow .. " again") or tostring(result)
    log("key reset (%s): %s", from, text)
    if from == "command" then chat(ok, text) end
    sendPage("phone:keyResult", { ok = ok, key = keyNow, text = text })
    pushState()
end

AddEventHandler("open77:keybinds:changed", function()
    local before = keyNow
    keyNow = currentKey()
    if keyNow ~= before then log("key is %s now (moved in the game's key bindings)", keyNow) end
    pushState()
end)

-- =============================================================================
-- THE PLAYER'S SETTINGS (Open77.kvp: this server, this resource)
-- =============================================================================

local function kvpGet(key)
    local store = type(Open77) == "table" and Open77.kvp or nil
    if store == nil or type(store.get) ~= "function" then return nil, "no kvp" end
    local ok, value, reason = pcall(store.get, key)
    if not ok then return nil, tostring(value) end
    return value, reason
end

local function kvpSet(key, value)
    local store = type(Open77) == "table" and Open77.kvp or nil
    if store == nil or type(store.set) ~= "function" then return false end
    local ok, done = pcall(store.set, key, value)
    return ok and done == true
end

local function loadPrefs(from)
    if prefsLoaded then return end
    local t0 = GetGameTimer()
    local size, why = kvpGet("phone.size")
    if size == nil and why ~= nil and tostring(why):find("session", 1, true) then return end   -- not yet: ask again later
    prefsLoaded = true
    if SIZES[size] then prefs.size = size end
    local side = kvpGet("phone.side")
    if SIDES[side] then prefs.side = side end
    local dim = kvpGet("phone.dim")
    if type(dim) == "boolean" then prefs.dim = dim end
    local stats = kvpGet("phone.stats")
    if type(stats) == "boolean" then prefs.stats = stats end
    local pointer = kvpGet("phone.pointer")
    if POINTERS[pointer] then prefs.pointer = pointer end
    log("settings: %s, on the %s, game %s, numbers %s, pointer %s (read %s, in %d ms)", prefs.size, prefs.side,
        prefs.dim and "dimmed" or "not dimmed", prefs.stats and "shown" or "hidden", prefs.pointer,
        tostring(from or "on opening"), GetGameTimer() - t0)
end

local function savePrefs(p)
    if type(p) ~= "table" then return end
    local changed = {}
    if SIZES[p.size] and p.size ~= prefs.size then prefs.size = p.size; changed[#changed + 1] = "size " .. p.size end
    if SIDES[p.side] and p.side ~= prefs.side then prefs.side = p.side; changed[#changed + 1] = "side " .. p.side end
    if type(p.dim) == "boolean" and p.dim ~= prefs.dim then prefs.dim = p.dim; changed[#changed + 1] = "dim " .. tostring(p.dim) end
    if type(p.stats) == "boolean" and p.stats ~= prefs.stats then prefs.stats = p.stats; changed[#changed + 1] = "numbers " .. tostring(p.stats) end
    if POINTERS[p.pointer] and p.pointer ~= prefs.pointer then prefs.pointer = p.pointer; changed[#changed + 1] = "pointer " .. p.pointer end
    if #changed == 0 then return end
    local saved = kvpSet("phone.size", prefs.size) and kvpSet("phone.side", prefs.side) and kvpSet("phone.dim", prefs.dim)
        and kvpSet("phone.stats", prefs.stats) and kvpSet("phone.pointer", prefs.pointer)
    log("settings changed: %s%s", table.concat(changed, ", "), saved and "" or " (not saved: kept until you leave)")
    pushState()
end

-- =============================================================================
-- OPEN AND CLOSE
-- =============================================================================

local function focus(want)
    if page == nil then return false, "no page" end
    local ok, a, b
    if want then
        ok, a, b = pcall(page.setFocus, page, true, true)
    else
        ok, a, b = pcall(page.setFocus, page, false, false)
    end
    if not ok then return false, tostring(a) end
    return a == true, b
end

local createPage -- (below, THE PAGE)
local pageWhy = nil              -- why the last try at making the page failed
local pageTries = 0

local function setOpen(want, why)
    if want == isOpen then
        pushState()
        return
    end
    if want and page == nil and not createPage(why) then
        -- No page yet (2026-11-07 on Staging: "resource_preparing" -- the game was still preparing this resource's
        -- files when it started): nothing could be drawn, so the phone stays put away and the player is told,
        -- instead of a key that silently does nothing.
        log("not opened (%s): no page yet (%s, %d tries); told the player", why, tostring(pageWhy), pageTries)
        chat(false, "The phone is still loading -- press " .. currentKey() .. " again in a few seconds.")
        return
    end
    isOpen = want
    if want then
        loadPrefs("on opening")
        phase = "asking"
        pendingConnect = nil
        openSeq = openSeq + 1
        local focused, reason = focus(true)
        log("opened (%s, opening %d); keyboard and mouse to the phone: %s; asking the server", why, openSeq,
            focused and "yes" or ("NO (" .. tostring(reason) .. "): the phone key still closes it"))
        askedAt, askTries = GetGameTimer(), 1
        TriggerServerEvent("open77_phone:request", { why = why, seq = openSeq })
    else
        focus(false)
        -- Only a held phone is put down: a player still waiting stays in the queue and is told when it is free (a
        -- grant that crosses this close on the way is handed straight back when it lands).
        if holding then TriggerServerEvent("open77_phone:release", { why = why, seq = openSeq }) end
        log("closed (%s, opening %d)%s", why, openSeq, holding and "; the phone is free for others" or "")
        holding = false
        phase = "closed"
        pendingConnect = nil
        askedAt = nil
        idleMs = 0
    end
    pushState()
end

toggle = function(why)
    setOpen(not isOpen, why or "key")
end

-- =============================================================================
-- THE SERVER
-- =============================================================================

RegisterNetEvent("open77_phone:granted", function(payload)
    if type(payload) ~= "table" then return end
    label = type(payload.label) == "string" and payload.label or label
    if not isOpen then
        -- Closed before the answer came: give it straight back.
        TriggerServerEvent("open77_phone:release", { why = "closed before the answer" })
        log("the server gave us the phone after it was closed; handed back")
        return
    end
    askedAt = nil
    holding = true
    phase = "connecting"
    pendingConnect = { url = payload.url, ticket = payload.ticket, label = label, ticketSeconds = payload.ticketSeconds }
    log("the phone is ours (%s); opening the screen at %s", payload.fresh and "picked up" or "still ours",
        hostOf(payload.url))
    sendPage("phone:connect", pendingConnect)
    pushState()
end)

RegisterNetEvent("open77_phone:busy", function(payload)
    if type(payload) ~= "table" or not isOpen then return end
    askedAt = nil
    phase = "busy"
    log("%s is using the phone; waiting to be told it is free", tostring(payload.holder))
    sendPage("phone:busy", { holder = payload.holder, heldMs = payload.heldMs, label = payload.label or label })
    pushState()
end)

RegisterNetEvent("open77_phone:denied", function(payload)
    if type(payload) ~= "table" then return end
    askedAt = nil
    phase = "error"
    log("the server said no: %s", tostring(payload.reason))
    sendPage("phone:error", { reason = payload.reason, label = payload.label or label })
    pushState()
end)

RegisterNetEvent("open77_phone:revoked", function(payload)
    holding = false
    if not isOpen then return end
    phase = "revoked"
    local reason = type(payload) == "table" and payload.reason or "?"
    log("the phone was taken back (%s)", tostring(reason))
    sendPage("phone:revoked", { reason = reason })
    pushState()
end)

RegisterNetEvent("open77_phone:free", function(payload)
    if isOpen and phase == "busy" then
        log("the phone is free; asking again")
        phase = "asking"
        askedAt, askTries = GetGameTimer(), 1
        TriggerServerEvent("open77_phone:request", { why = "free", seq = openSeq })
        pushState()
        return
    end
    log("the phone is free (told; it is closed here)")
    sendPage("phone:free", { key = currentKey(), label = type(payload) == "table" and payload.label or label })
end)

RegisterNetEvent("open77_phone:toggle", function()
    toggle("command")
end)

RegisterNetEvent("open77_phone:key", function(payload)
    if type(payload) ~= "table" then return end
    if payload.reset then return resetKey("command") end
    if type(payload.key) == "string" then return rebind(payload.key, "command") end
    if payload.choose then
        setOpen(true, "key chooser")
        sendPage("phone:settings", { capture = true })
    end
end)

-- =============================================================================
-- THE PAGE
-- =============================================================================

-- The page is made when the resource starts. The game may still be preparing the
-- resource's files then: WebUI.create answers nil, "resource_preparing" (seen on
-- Staging, 2026-11-07, for this resource and others at the same moment), and the
-- page is tried again every few seconds until it is made, and once more whenever
-- the player opens the phone. Only a refusal of policy=remote itself (an older
-- game raises an error for a policy it does not know) makes the page without it.
local PAGE_RETRY_MS, PAGE_RETRY_SLOW_MS, PAGE_RETRY_FAST_TRIES = 3000, 30000, 40
local pageFirstTry, pageRetrying = nil, false

createPage = function(why)
    if page ~= nil then return true end
    pageTries = pageTries + 1
    local t0 = GetGameTimer()
    pageFirstTry = pageFirstTry or t0
    local spec = {
        entry = "web/phone.html",
        layer = "menu",
        width = 1920,
        height = 1080,
        -- Android's screen is a moving picture; thirty is what the television's
        -- near screens get too.
        fps = 30,
        zIndex = 720,
        transparent = true,
        -- Never false: a surface created hidden never uploads a frame on this
        -- build. The page draws nothing while the phone is closed.
        visible = true,
        -- The page opens a WebSocket to the web server (Android's screen).
        policy = "remote",
    }
    local ok, created, reason = pcall(WebUI.create, spec)
    local policyRefused = not ok or (created == nil and tostring(reason):find("invalid_webui_options", 1, true) ~= nil)
    if policyRefused then
        log("the page with policy=remote was refused (%s); trying without it", tostring(ok and reason or created))
        spec.policy = nil
        ok, created, reason = pcall(WebUI.create, spec)
    end
    if not ok or created == nil then
        pageWhy = tostring(ok and reason or created)
        if pageTries == 1 or pageTries % 10 == 0 then          -- (an opening that finds no page says so itself)
            log("no page yet (%s; try %d, %s): %s", pageWhy, pageTries, tostring(why),
                pageWhy == "resource_preparing" and "the game is still preparing this resource's files; trying again"
                or "trying again")
        end
        return false
    end
    page = created
    pageWhy = nil

    page:on("phone:ready", function()
        pageReady = true
        log("page ready")
        pushState()
        if isOpen and pendingConnect ~= nil then sendPage("phone:connect", pendingConnect) end
    end)

    page:on("phone:close", function(payload)
        lastPageClose = GetGameTimer()
        setOpen(false, type(payload) == "table" and tostring(payload.why or "page"):sub(1, 24) or "page")
    end)

    page:on("phone:again", function()
        if not isOpen then return end
        phase = "asking"
        log("asking again (from the phone)")
        askedAt, askTries = GetGameTimer(), 1
        TriggerServerEvent("open77_phone:request", { why = "again", seq = openSeq })
        pushState()
    end)

    page:on("phone:rebind", function(payload)
        rebind(type(payload) == "table" and payload.key or nil, "page")
    end)

    page:on("phone:resetKey", function()
        resetKey("page")
    end)

    page:on("phone:prefs", function(payload)
        savePrefs(payload)
    end)

    page:on("phone:log", function(payload)
        if type(payload) ~= "table" then return end
        local text = tostring(payload.text or ""):gsub("[%c]", " "):sub(1, 400)
        log("page: %s%s", payload.level == "warn" and "!! " or "", text)
    end)

    -- What the page's connection did: live (and at what size), lost, failed. Told
    -- to the server too (a few per opening), so its log has the whole session.
    page:on("phone:report", function(payload)
        if type(payload) ~= "table" then return end
        local status = tostring(payload.status or "?"):sub(1, 24)
        local detail = tostring(payload.detail or ""):gsub("[%c]", " "):sub(1, 160)
        if status == "live" then phase = "live" elseif status == "failed" or status == "lost" then phase = "error" end
        log("screen %s%s", status, detail ~= "" and (" -- " .. detail) or "")
        reportsSent = reportsSent + 1
        if reportsSent <= 20 then
            TriggerServerEvent("open77_phone:report", { status = status, detail = detail })
        end
        pushState()
    end)

    page:on("phone:stats", function(payload)
        if type(payload) ~= "table" then return end
        idleMs = math.max(0, math.floor(tonumber(payload.idleMs) or 0))
        local now = GetGameTimer()
        if now - lastStatsLog >= 60000 then
            lastStatsLog = now
            log("screen %sx%s, %s kbit/s, %s updates/s, idle %d s", tostring(payload.w), tostring(payload.h),
                tostring(payload.kbps), tostring(payload.ups), idleMs // 1000)
        end
    end)
    log("page created (%s)%s", spec.policy and "may reach the phone's screen" or "WITHOUT policy=remote: the screen may not open",
        pageTries > 1 and string.format(" on try %d, %.1f s after the first (%s)", pageTries,
            (GetGameTimer() - pageFirstTry) / 1000, tostring(why)) or "")
    return true
end

local function keepTryingPage()
    if page ~= nil or pageRetrying then return end
    pageRetrying = true
    CreateThread(function()
        while page == nil do
            Wait(pageTries < PAGE_RETRY_FAST_TRIES and PAGE_RETRY_MS or PAGE_RETRY_SLOW_MS)
            if page == nil then createPage("retry") end
        end
        pageRetrying = false
    end)
end

CreateThread(function()
    while true do
        Wait(HEARTBEAT_MS)
        if isOpen and holding then
            TriggerServerEvent("open77_phone:heartbeat", { idleMs = idleMs })
        end
    end
end)

if not createPage("start") then keepTryingPage() end
registerKey()

-- The player's settings read ahead, once the game can answer (it cannot before
-- the session is up), so the first press does not wait on them.
CreateThread(function()
    local tries = 0
    while not prefsLoaded and tries < 90 do
        Wait(2000)
        tries = tries + 1
        loadPrefs("ahead")
    end
end)

-- A question the server never answered (a request inside its one-a-second limit is dropped, a packet can be lost):
-- asked again after 3 s, three times in all, then the player is told.
CreateThread(function()
    while true do
        Wait(500)
        if isOpen and phase == "asking" and askedAt ~= nil and GetGameTimer() - askedAt >= ASK_TIMEOUT_MS then
            if askTries < ASK_TRIES then
                askTries = askTries + 1
                askedAt = GetGameTimer()
                log("no answer from the server in %d s; asking again (try %d of %d)", ASK_TIMEOUT_MS // 1000, askTries,
                    ASK_TRIES)
                TriggerServerEvent("open77_phone:request", { why = "no answer", seq = openSeq })
            else
                askedAt = nil
                phase = "error"
                log("the server did not answer %d times; told the player", ASK_TRIES)
                sendPage("phone:error", { reason = "no_answer", label = label })
                pushState()
            end
        end
    end
end)
