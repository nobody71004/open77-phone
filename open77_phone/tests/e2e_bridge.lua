-- open77_phone tests: the server's own rules behind a pipe, for the page's end-to-end test (tools/phone/tests).
--
-- One JSON request per line on stdin, one JSON answer per line on stdout. The caller hands in the time (`now`, ms)
-- with every request. Run from the resource folder:
--   lua5.4 tests/e2e_bridge.lua
--
--   {"op":"request","player":1,"name":"Alice","ip":"127.0.0.1:5000","now":0}   -> {"what":"granted","detail":{...}}
--   {"op":"release","player":1}                                                 -> {"released":true}
--   {"op":"http","req":{"method":"GET","path":"/open77_phone/v1/check","headers":{...}}}
--                                                                               -> {"status":204,"body":"","note":"..."}
local Json = dofile("tests/json.lua")
dofile("server/lease.lua")
dofile("server/http.lua")

local now = 0
-- (selftests: the rehearsal checks the whole way more often than a server start ever would)
local lease = PhoneLease.new({ clock = function() return now end, selftestLimit = 100 })
local env = { encode = Json.encode, version = "e2e", configured = true }

local function answer(value)
    io.write(Json.encode(value), "\n")
    io.flush()
end

for line in io.lines() do
    local ok, msg = pcall(Json.decode, line)
    if not ok or type(msg) ~= "table" then
        answer({ error = "bad_request" })
    else
        now = tonumber(msg.now) or now
        if msg.op == "request" then
            local what, detail = lease:request(msg.player, msg.name, now, msg.ip, msg.seq)
            answer({ what = what, detail = detail })
        elseif msg.op == "release" then
            local released, _, late = lease:release(msg.player, msg.seq)
            answer({ released = released, late = late == true })
        elseif msg.op == "heartbeat" then
            answer({ ok = lease:heartbeat(msg.player, msg.idleMs, now) })
        elseif msg.op == "expire" then
            answer({ revoked = lease:expire(now) })
        elseif msg.op == "http" then
            local status, body, headers, note = PhoneHttp.handle(lease, msg.req or {}, env)
            answer({ status = status, body = body, headers = headers, note = note })
        elseif msg.op == "status" then
            answer(lease:status(now))
        else
            answer({ error = "unknown_op" })
        end
    end
end
