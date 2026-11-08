-- open77_phone -- server/http.lua
--
-- What the web server and this machine may ask. Served through
-- `Open77.http.listen` on the game server's own listener (`httpHandlers`,
-- loopback); nothing here is published as it is. The web server publishes ONE
-- thing, the phone's WebSocket, and asks this file first:
--
--   GET  /v1/check     nginx `auth_request` before a WebSocket reaches Android's
--                      screen. The ticket is in the original request's address
--                      (X-Original-URI: /open77-phone-<role>/ws?t=<ticket>), or in
--                      this request's own query for a direct call. 204 lets it
--                      through; 403 refuses it (and says why, for the log). nginx
--                      also says where the WebSocket came from (X-Real-IP), which
--                      a ticket made for a player's own address must match.
--   GET  /v1/health    is it up, which version, whether somebody holds the phone
--   POST /v1/selftest  a ticket for this machine's own end-to-end check, from this
--                      machine only (not through the web server), a few per start
--
-- Pure: the lease and the JSON encoder are handed in, so the tests drive it with
-- plain tables. The answer is (status, body, headers, note) -- note is what the
-- caller logs, never a ticket.

PhoneHttp = {}

local JSON_HEADERS = {
    ["Content-Type"] = "application/json; charset=utf-8",
    ["Cache-Control"] = "no-store",
}

---A request header, whatever shape the host hands them in (a map, case-insensitive
---or not, or a list of pairs).
function PhoneHttp.header(req, name)
    local headers = req and req.headers
    if type(headers) ~= "table" then return nil end
    local lower = name:lower()
    local direct = headers[name] or headers[lower]
    if direct ~= nil then return direct end
    for k, v in pairs(headers) do
        if type(k) == "string" and k:lower() == lower then return v end
        if type(v) == "table" then
            local n = v.name or v.key or v[1]
            if type(n) == "string" and n:lower() == lower then return v.value or v[2] end
        end
    end
    return nil
end

---True for a request made on this machine and not passed on by a web server.
function PhoneHttp.isLocal(req)
    local remote = tostring(req.remoteAddress or "")
    local loopback = remote == "127.0.0.1" or remote == "::1" or remote == "::ffff:127.0.0.1"
    if not loopback then return false end
    return PhoneHttp.header(req, "X-Forwarded-For") == nil and PhoneHttp.header(req, "X-Real-IP") == nil
        and PhoneHttp.header(req, "X-Original-URI") == nil
end

local function ticketIn(text)
    if type(text) ~= "string" then return nil end
    return text:match("[?&]t=([^&#]*)")
end

---The ticket a check is about: the original request's (nginx), else this one's.
function PhoneHttp.ticketOf(req)
    local original = PhoneHttp.header(req, "X-Original-URI")
    if original ~= nil then return ticketIn(tostring(original)), "original" end
    if type(req.query) == "table" and type(req.query.t) == "string" then return req.query.t, "query" end
    if type(req.query) == "string" then
        local t = ticketIn("?" .. req.query)
        if t then return t, "query" end
    end
    return ticketIn(tostring(req.path or "")), "query"
end

---@param env table { encode = fn(table) -> string, version = string, configured = bool }
function PhoneHttp.handle(lease, req, env)
    local function json(status, value, note)
        return status, env.encode(value), JSON_HEADERS, note
    end
    local method = tostring(req.method or "GET"):upper()
    local path = tostring(req.path or "/")
    path = path:gsub("[?#].*$", "")
    path = path:gsub("^/open77_phone", "")
    if path == "" then path = "/" end
    if #path > 1 then path = path:gsub("/+$", "") end

    if path == "/v1/check" then
        if method ~= "GET" and method ~= "HEAD" then return json(405, { ok = false, error = "method_not_allowed" }) end
        local ticket, where = PhoneHttp.ticketOf(req)
        -- nginx says where the WebSocket came from (X-Real-IP: $remote_addr); a
        -- ticket made for a player's own address opens from nowhere else.
        local ok, who, note = lease:check(ticket, nil, PhoneHttp.header(req, "X-Real-IP"))
        if ok then
            return 204, "", { ["Cache-Control"] = "no-store" },
                string.format("check: let through (%s; %s; %s)",
                    who == 0 and "this machine's selftest" or ("player " .. tostring(who)), where, note)
        end
        return json(403, { ok = false, error = who }, string.format("check: refused (%s, %s)", who, where))
    end

    if path == "/v1/health" or path == "/" or path == "/v1" then
        if method ~= "GET" then return json(405, { ok = false, error = "method_not_allowed" }) end
        local s = lease:status()
        return json(200, { ok = true, service = "open77_phone", version = env.version, configured = env.configured == true,
            held = s.holder ~= nil, waiting = s.waiting })
    end

    if path == "/v1/selftest" then
        if method ~= "POST" then return json(405, { ok = false, error = "method_not_allowed" }) end
        if not PhoneHttp.isLocal(req) then
            return json(403, { ok = false, error = "local_only" }, "selftest: refused (not this machine)")
        end
        local ticket, reason = lease:selftest()
        if ticket == nil then return json(429, { ok = false, error = reason }, "selftest: refused (" .. reason .. ")") end
        return json(200, { ok = true, ticket = ticket, ticketSeconds = lease.ticketMs // 1000 },
            "selftest: a ticket for this machine's check")
    end

    return json(404, { ok = false, error = "not_found" })
end
