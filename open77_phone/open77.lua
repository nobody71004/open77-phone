resource "open77_phone"
version "1.0.2"
open77_version ">=0.0.1"
auto_start true

-- A phone in the player's hand: the server's own Android (Waydroid, on the game
-- server's machine), drawn over the game as a phone, driven with the mouse and
-- keyboard. F1 opens and closes it until the player moves the key (Pause >
-- Settings > Key bindings, the phone's own settings, or /phonekey <key>); /phone
-- opens it with no key at all.
--
-- It is ONE Android, so one player drives it at a time: the server decides who
-- (server/lease.lua), and everyone else is told who has it and told again when it
-- is free. The picture is Android's VNC screen, through the web server, behind a
-- ticket the game server makes for the player who holds the phone
-- (server/http.lua answers the web server's question). README.md has the whole
-- path and what the web server needs.

-- Order is load order: the rules first, the wiring last.
server_script "server/config.lua"
server_script "server/lease.lua"
server_script "server/http.lua"
server_script "server/main.lua"
client_script "client/main.lua"

-- The page is created by client/main.lua (with policy = "remote", so it may open
-- Android's WebSocket), never by the manifest.
web_ui_page "web/phone.html"
web_ui_auto_create false
web_files { "web/**" }

-- The suite (tests/run.lua) and the bridge the page's end-to-end test drives the
-- rules through (tests/e2e_bridge.lua) live beside the resource and are not
-- installed with it: nothing in the game reads them.

permissions {
    -- The phone's requests, the server's answers, the heartbeats.
    "network.events",
    -- The key, its registry entry, moving it, and asking what it is.
    "input.actions",
    -- The web server's ticket check: Open77.http.listen on the server's own
    -- (loopback) httpHandlers listener.
    "http.serve",
    -- The address a player plays from, so their tickets open from nowhere else.
    -- Kept with the ticket for its few seconds; never logged, never sent on.
    "players.identity.sensitive",
}
