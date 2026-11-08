# open77_phone — the server's Android, in the player's hand

A phone drawn over the game that shows the **native Android** the game server's machine runs
(Waydroid, the same one the TV rooms' Android tile shows), driven with the mouse and keyboard.

```
F1 (or /phone)  ->  the phone comes up: "Picking up the phone" -> "Opening the screen" -> Android, live
click, drag, wheel, type          on the screen, as on a touch screen with a keyboard
Back / Home / volume              the buttons under the phone, and the phone's own side keys
gear                              settings: the key, the size, where it sits, dimming, the pointer, the numbers
F1 or Esc (or the power key)      puts it away
```

## The key

**Phone: open / close**, F1 until the player moves it, in the game's own key registry:

- **Pause › Settings › Key bindings** lists it like every other key;
- the phone's **settings** (gear) — *Change* takes the next key pressed, *Reset* puts F1 back;
- **`/phonekey <key>`** in the chat (`/phonekey page up`, `/phonekey reset`; `/phonekey` alone
  opens the chooser).

The choice is kept by the game on the player's machine, for every server. It is registered as a
*consuming* mapping where the game can (`consume = true`), so the key does nothing else while it
opens the phone. Keys a player walks, aims, talks or interacts with (W A S D, Space, Shift, Ctrl,
Alt, Tab, Enter, T, F, E, R, Backspace, the two main mouse buttons) are refused. While the phone is
open the page has the keyboard and the registry is paused, so the page itself closes on the same
key (Lua tells it which) and on Escape -- when the key is **let go**, and only if the page saw it
pressed. Put away on the press, the game got the keyboard back while the key was still down and
its own binding opened the phone again 20 ms later (1.0.1 on Staging, 2026-11-07: F1 could not
close it, only Escape could); and the release of the press that opened the phone, which the page
also sees, must not close it. Lua ignores the binding for half a second after the page put the
phone away, as a second lock on the same door.

## One Android, one player at a time

The server decides who holds the phone (`server/lease.lua`): the first to ask gets it and keeps it
while their game says it is still open (a heartbeat every ten seconds). It is free again when they
put it away, leave, go quiet for 45 s (a crash, a dropped link), or — only while somebody else is
waiting — leave it untouched for ten minutes. Everyone else sees **In use: V has the phone (3 min)**
and is put through by themselves when it is free; a player who put the phone away while waiting
gets a note in the corner: *The phone is free — press F1*. `/phonestatus` says who has it.

Every opening is counted by the client, and the request and the put-down carry that count: the
server does not always handle one client's events in the order they were sent (Staging,
2026-11-07: a put-down landed after the next opening's request and took back its fresh ticket), so
a put-down for an older opening than the holder's last request is ignored. A request the server
does not answer (its one-a-second limit drops it, or a packet is lost) is asked again after 3 s,
three times in all, before the phone says *The server did not answer*.

## How the picture gets there

```
the phone page (CEF, policy=remote)
  -- wss://xyberrp.duckdns.org/open77-phone-<role>/ws?t=<ticket> -->  nginx
       auth_request -> this game server's open77_phone (/open77_phone/v1/check on its httpHandlers listener)
       204 -> websockify (127.0.0.1:8001) -> wayvnc (127.0.0.1:5900) -> Android
```

The page asks Lua, Lua asks the server, the server makes a **ticket** for the player who holds the
phone: 128 random bits, good **once**, for **30 s**, only while that player still holds the phone,
and — when the game knows the address the player plays from — only from that address (nginx says
where the WebSocket came from in `X-Real-IP`; an IPv6 WebSocket for an IPv4 player cannot be
compared and is let through). A reconnect asks for a new ticket. No ticket is ever logged by the
page, the client or the server, and nginx's two locations keep no access log. websockify itself is
not reachable from outside the machine (the host's firewall drops it); the ticket check is the only
way in.

The web server's two locations per game server are written, checked and taken out again by
this repository's `tools/phone_server.py` (as root on the machine: `look`, `fix`, `verify`,
`putback`; the operator's kit runs it as `tv_kit.py phone`, `phone-fix`, `phone-verify`,
`phone-putback`): it finds the server block that serves the players' host on 443, adds an
`internal` check location and the WebSocket after the TV locations, runs `nginx -t`, reloads, and
then checks the whole way with a ticket the game server makes for the machine itself
(`POST /open77_phone/v1/selftest`, loopback only, a few per start): no ticket 403, the ticket 101
and VNC's own greeting, the same ticket again 403. Anything not right is put back by itself.

## This server's settings (`server/config.lua`)

Set for each game server (the release's `INSTALL.md`, step 3; XyberRP's kit writes it for each of its servers):

| | |
|---|---|
| `wsUrl` | where this server's players reach the screen: `wss://<host>/open77-phone-<role>/ws`. Empty: the phone says *Not set up here*. |
| `label` | what the phone calls the device (*Android*) |
| `ticketSeconds` | 30 |
| `leaseSeconds` | 45 |
| `idleSeconds` | 600 (only held against a holder while somebody waits) |

The server also needs `httpHandlers` on, on loopback (`server.jsonc`; `INSTALL.md`, step 4).

## What it logs

- client: `[open77_phone] key F1 (registered, consumed)`, `opened (key, opening 1); keyboard and mouse to the
  phone: yes; asking the server`, `the phone is ours (picked up); opening the screen at xyberrp.duckdns.org`,
  `screen live -- 1280x720, attempt 1`, a line of numbers a minute (size, data rate, updates/s, idle),
  `closed (escape, opening 3); the phone is free for others`, and everything the page reports as
  `page: ...`; `the phone key 20 ms after the phone put itself away: the same press, ignored`,
  `no answer from the server in 3 s; asking again (try 2 of 3)`;
  `settings: ... (read ahead, in N ms)`, and while the game is still preparing the resource's files
  when it starts, `no page yet (resource_preparing; try 1, start): ...` and then `page created (...)
  on try N, S s after the first (retry)` -- the page is tried again every 3 s (every 30 s after 40
  tries) and once more on the phone key, and a key that finds no page tells the player in the chat
  (`not opened (key): no page yet (...)`) instead of doing nothing (1.0.1; 1.0.0 tried once, too
  early, on Staging on 2026-11-07, and F1 showed nothing);
- server: the start line (version, where the screen is, the check's route), every grant, busy,
  hand-back and refusal (and `a put-down for opening 2 came after a newer opening's request;
  ignored`), each check the web server asked (`check: let through (player 3; original;
  the player's own address)`, `check: refused (ticket_used, original)`), and what each player's page
  saw (`player 3 (V): the phone live -- 1280x720, attempt 1`).

## Tests

```bash
lua5.4 tests/run.lua         # from this folder: the rules, the web server's question, the client and
                             # server halves (125 checks)
python3 ../tests/t_phone.py  # end to end on one machine: Xvfb "Android", x11vnc, websockify, nginx with
                             # TLS and auth_request, the rules through tests/e2e_bridge.lua,
                             # phone_server.py's look/fix/verify/putback, the page in Chromium
```

## Limits

- One Android for the whole machine: the phone and the TV rooms' Android tile show the same device.
- Pictures only: VNC carries no sound, so Android's sound does not reach the phone.
- While the phone has the keyboard and mouse, the player stands still.
- If two pointers show on the screen (the game's and one Android draws itself), settings › *Pointer
  on the screen › Android's* hides the game's over the screen.

noVNC 1.6.0 (MPL-2.0) is bundled as `web/vendor/novnc-rfb.js` (`web/vendor/noVNC-LICENSE.txt`),
built from npm `@novnc/novnc@1.6.0` with one change, said in the file's first line.
