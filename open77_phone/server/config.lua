-- open77_phone -- server/config.lua
--
-- This game server's own settings. Set wsUrl to the route the web server publishes
-- for this game server (INSTALL.md, steps 3 and 6): the one thing that differs
-- between servers is where their players reach the phone's screen, one WebSocket
-- per game server, each asking that server's own tickets. (XyberRP's kit writes
-- this file for each of its servers.)
--
-- Server only: it is never sent to a client as a file. A client is told `wsUrl`
-- together with a ticket, when it asks for the phone.

PhoneConfig = {
    -- The WebSocket the phone page opens, through the web server's ticket check:
    -- wss://<host>/open77-phone-<role>/ws. Empty: the phone tells players it is
    -- not set up on this server, and nothing else happens.
    wsUrl = "",

    -- What the phone calls the device in its messages.
    label = "Android",

    -- A ticket opens the screen once, within this many seconds of being made.
    ticketSeconds = 30,

    -- The holder's client says "still open" every ten seconds; after this long
    -- without a word the phone is free again (a crashed game, a dropped link).
    leaseSeconds = 45,

    -- A holder who has not touched the phone for this long gives it up -- but only
    -- when somebody else is waiting for it.
    idleSeconds = 600,
}
