-- open77_phone -- server/lease.lua
--
-- Who holds the phone, and the tickets that open its screen. The phone is ONE
-- Android (Waydroid on the game server's machine), so one player drives it at a
-- time: the first to ask gets it, keeps it while their client says it is still
-- open (a heartbeat), and gives it back by closing the phone, by leaving, or by
-- going quiet. Everyone else is told who has it, and told again when it is free.
--
-- A ticket is what the web server asks about before it lets a WebSocket through
-- to Android's screen (nginx `auth_request`, answered by server/http.lua): 128
-- random bits, good once, for a few seconds, and only while the player it was
-- made for still holds the phone. A reconnect asks for a new one. When the game
-- knows the address the player plays from, the ticket only opens from that
-- address too: `math.random` is not a cryptographic generator, and a ticket that
-- somebody guessed, or read in a log, is still no use from another machine. (A
-- player whose browser leaves over IPv6 while the game left over IPv4 is not
-- refused for that: the two cannot be compared.)
--
-- Pure: the clock (milliseconds) and the random source are handed in, so the
-- tests drive this with plain numbers and nothing here calls a native.

PhoneLease = {}
PhoneLease.__index = PhoneLease

local HEX = "0123456789abcdef"

---@param opts table { clock = fn() -> ms, random = fn(m, n) -> int, ticketSeconds, leaseSeconds, idleSeconds,
---                    selftestLimit }
function PhoneLease.new(opts)
    opts = opts or {}
    local self = setmetatable({}, PhoneLease)
    self.clock = assert(opts.clock, "PhoneLease.new: a clock is needed")
    self.random = opts.random or math.random
    self.ticketMs = math.floor((tonumber(opts.ticketSeconds) or 30) * 1000)
    self.leaseMs = math.floor((tonumber(opts.leaseSeconds) or 45) * 1000)
    -- Idle is only held against a holder while somebody is waiting: an idle phone
    -- that nobody else wants is nobody's problem.
    self.idleMs = math.floor((tonumber(opts.idleSeconds) or 600) * 1000)
    self.selftestLimit = tonumber(opts.selftestLimit) or 10
    self.holder = nil          -- { player, name, since, beat, idleMs }
    self.tickets = {}          -- [ticket] = { player, expires, used, selftest }
    self.waiting = {}          -- [player] = name: told when the phone is free
    self.selftests = 0
    self.counts = { granted = 0, busy = 0, checked = 0, refused = 0, revoked = 0 }
    return self
end

function PhoneLease:now()
    return self.clock()
end

---128 random bits as 32 lowercase hexadecimal characters.
function PhoneLease:token()
    local out = {}
    for i = 1, 32 do
        local n = self.random(1, 16)
        out[i] = HEX:sub(n, n)
    end
    return table.concat(out)
end

function PhoneLease.validTicket(t)
    return type(t) == "string" and #t == 32 and t:match("^[0-9a-f]+$") ~= nil
end

---The address part of "ip:port", "[v6]:port", a bare address or a v4-mapped v6
---one, and its family; nil for anything else.
function PhoneLease.address(text)
    if type(text) ~= "string" or text == "" then return nil end
    local s = text:gsub("^%s+", ""):gsub("%s+$", "")
    local bracketed = s:match("^%[([%x:%.]+)%]")
    if bracketed then s = bracketed end
    s = s:gsub("^::ffff:(%d+%.%d+%.%d+%.%d+)$", "%1")
    local v4 = s:match("^(%d+%.%d+%.%d+%.%d+)") 
    if v4 and (s == v4 or s:sub(#v4 + 1, #v4 + 1) == ":") then return v4, 4 end
    if s:match("^[%x:]+$") and s:find(":", 1, true) then return s:lower(), 6 end
    return nil
end

---A new ticket for `player`; that player's older unused tickets stop working.
function PhoneLease:mint(player, now, selftest, ip)
    for t, info in pairs(self.tickets) do
        if info.player == player and not info.used and not info.selftest then self.tickets[t] = nil end
    end
    local ticket = self:token()
    while self.tickets[ticket] ~= nil do ticket = self:token() end
    local address, family = PhoneLease.address(ip)
    self.tickets[ticket] = { player = player, expires = now + self.ticketMs, used = false, selftest = selftest == true,
        ip = address, family = family }
    return ticket
end

---Drops the tickets that can no longer be used, and the holder whose client went
---quiet (no heartbeat for leaseSeconds) or idle while somebody waits.
---@return table|nil revoked { player, name, reason } when the holder lost the phone here
function PhoneLease:expire(now)
    now = now or self:now()
    for t, info in pairs(self.tickets) do
        if info.expires < now then self.tickets[t] = nil end
    end
    local h = self.holder
    if h == nil then return nil end
    local reason = nil
    if now - h.beat > self.leaseMs then
        reason = "quiet"
    elseif next(self.waiting) ~= nil and (h.idleMs or 0) >= self.idleMs then
        reason = "idle"
    end
    if reason == nil then return nil end
    self.holder = nil
    self:forgetTickets(h.player)
    self.counts.revoked = self.counts.revoked + 1
    return { player = h.player, name = h.name, reason = reason, heldMs = now - h.since }
end

function PhoneLease:forgetTickets(player)
    for t, info in pairs(self.tickets) do
        if info.player == player and not info.selftest then self.tickets[t] = nil end
    end
end

---A player asks for the phone. `seq` is the client's count of its own openings: a put-down names the opening it
---ends, so one that arrives after the next opening's request (events from one client are not always handled in
---the order they were sent -- Staging, 2026-11-07) cannot take back the newer one.
---@return string what "granted" | "busy"
---@return table detail granted: { ticket, ticketSeconds, fresh }; busy: { holder, heldMs }
function PhoneLease:request(player, name, now, ip, seq)
    now = now or self:now()
    local revoked = self:expire(now)
    local h = self.holder
    if h ~= nil and h.player ~= player then
        self.waiting[player] = name or ("player " .. tostring(player))
        self.counts.busy = self.counts.busy + 1
        return "busy", { holder = h.name, heldMs = now - h.since }, revoked
    end
    local fresh = h == nil
    if fresh then
        h = { player = player, name = name or ("player " .. tostring(player)), since = now, beat = now, idleMs = 0 }
        self.holder = h
    else
        h.beat = now
        h.idleMs = 0
    end
    if type(seq) == "number" and (h.seq == nil or seq > h.seq) then h.seq = seq end
    self.waiting[player] = nil
    self.counts.granted = self.counts.granted + 1
    return "granted", { ticket = self:mint(player, now, false, ip), ticketSeconds = self.ticketMs // 1000, fresh = fresh },
        revoked
end

---The holder's client says the phone is still open (and how long since its
---player last touched it). Anyone else's heartbeat is ignored.
function PhoneLease:heartbeat(player, idleMs, now)
    now = now or self:now()
    local h = self.holder
    if h == nil or h.player ~= player then return false end
    h.beat = now
    h.idleMs = math.max(0, math.floor(tonumber(idleMs) or 0))
    return true
end

---The holder gives the phone back; waiting players are returned so they can be
---told. A player who was only waiting stops waiting. A put-down for an opening
---older than the one the holder asked for last is late, and changes nothing.
---@return boolean released, table waiting { [player] = name }, boolean|nil late
function PhoneLease:release(player, seq)
    local h = self.holder
    if h ~= nil and h.player == player and type(seq) == "number" and h.seq ~= nil and seq < h.seq then
        return false, {}, true
    end
    self.waiting[player] = nil
    if h == nil or h.player ~= player then return false, {} end
    self.holder = nil
    self:forgetTickets(player)
    return true, self:takeWaiting()
end

---The players waiting for the phone, handed over once: telling them is the
---caller's business, and each is told once.
function PhoneLease:takeWaiting()
    local out = self.waiting
    self.waiting = {}
    return out
end

---The web server asks whether a ticket may open the screen, for a WebSocket that
---came from `remote` (nginx's $remote_addr, when it says).
---@return boolean ok, string reasonOrPlayer, string addressNote
function PhoneLease:check(ticket, now, remote)
    now = now or self:now()
    self.counts.checked = self.counts.checked + 1
    local function no(reason)
        self.counts.refused = self.counts.refused + 1
        return false, reason
    end
    if ticket == nil or ticket == "" then return no("no_ticket") end
    if not PhoneLease.validTicket(ticket) then return no("malformed_ticket") end
    local info = self.tickets[ticket]
    if info == nil then return no("unknown_ticket") end
    if info.used then return no("ticket_used") end
    if info.expires < now then
        self.tickets[ticket] = nil
        return no("ticket_expired")
    end
    if not info.selftest then
        local h = self.holder
        if h == nil or h.player ~= info.player then
            self.tickets[ticket] = nil
            return no("not_the_holder")
        end
    end
    local note = "address not compared"
    if info.ip ~= nil then
        local address, family = PhoneLease.address(remote)
        if address == nil then
            note = "the web server did not say where it came from"
        elseif family ~= info.family then
            note = "IPv" .. family .. " here, IPv" .. info.family .. " in game: not compared"
        elseif address ~= info.ip then
            self.tickets[ticket] = nil
            return no("other_address")
        else
            note = "the player's own address"
        end
    end
    info.used = true
    return true, info.player, note
end

---A ticket for this machine's own end-to-end check (server/http.lua answers it
---to loopback callers only). Bounded per start of the resource.
function PhoneLease:selftest(now)
    now = now or self:now()
    if self.selftests >= self.selftestLimit then return nil, "selftest_limit" end
    self.selftests = self.selftests + 1
    return self:mint(0, now, true)
end

---A player left: they hold nothing and wait for nothing.
---@return boolean wasHolder, table waiting
function PhoneLease:drop(player)
    self.waiting[player] = nil
    local h = self.holder
    if h ~= nil and h.player == player then
        self.holder = nil
        self:forgetTickets(player)
        return true, self:takeWaiting()
    end
    self:forgetTickets(player)
    return false, {}
end

---What the status command and the health answer say: never a ticket.
function PhoneLease:status(now)
    now = now or self:now()
    local h = self.holder
    local live, waiting = 0, 0
    for _, info in pairs(self.tickets) do if not info.used then live = live + 1 end end
    for _ in pairs(self.waiting) do waiting = waiting + 1 end
    return {
        holder = h and h.name or nil,
        holderPlayer = h and h.player or nil,
        heldMs = h and (now - h.since) or 0,
        quietMs = h and (now - h.beat) or 0,
        idleMs = h and h.idleMs or 0,
        waiting = waiting,
        unusedTickets = live,
        counts = self.counts,
    }
end
