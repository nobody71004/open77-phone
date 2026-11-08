# open77-phone — the server's Android, in the player's hand

An in-game phone for Open77 servers (Cyberpunk 2077 multiplayer). **F1** takes out a phone drawn
over the game that shows the game server's own **Android** — a real Android 13 (Waydroid, with
Google apps) running on the server's machine, the same one the TV rooms' Android tile shows
([nobody71004/open77-tv](https://github.com/nobody71004/open77-tv)) — and the player drives it
with the mouse and keyboard.
F1 or Esc puts it away. One Android, so one player at a time: everyone else sees who has it and is
put through when it is free.

```
open77_phone/          the resource: client/ (the key, the page, the talk with the server), server/ (who
                       holds the phone, the tickets, the web server's check), web/ (the phone page and its
                       bundled noVNC), tests/ (the suite, and the rules behind a pipe for the end-to-end test)
tools/phone_server.py  the web server's side, run as root on the machine: the two nginx locations per game
                       server, written, checked end to end and taken out again (look / fix / verify / putback)
tests/t_phone.py       the whole way on one machine, every piece real but the game
build/                 rebuilds the page's noVNC (1.6.0, MPL-2.0) from npm
```

How it works, what the player sees, the key, the settings, the logs and the limits are in
[`open77_phone/README.md`](open77_phone/README.md).

## What a server needs

- **An Open77 game server with its `httpHandlers` listener on** (`server.jsonc`): the web server asks
  the resource there whether a ticket may open the screen. The resource declares
  `network.events`, `input.actions`, `http.serve` and `players.identity.sensitive`.
- **Android's screen as VNC behind websockify on the same machine**: the TV rooms' native Android
  (open77-tv's `shared-browser/android-native/`: wayvnc on 127.0.0.1:5900, websockify on port 8001,
  which the host's firewall keeps from the outside).
- **nginx with TLS for the players' host**: the phone page is a remote WebUI page and opens
  `wss://<host>/open77-phone-<role>/ws`. That route, behind its ticket check, is the only way to the
  screen from outside the machine.

## Putting it on a server

1. Copy `open77_phone/` into the game server's resources and add it to `resources.load`.
2. In `open77_phone/server/config.lua`, set `wsUrl` to that server's route, for example
   `wss://xyberrp.duckdns.org/open77-phone-staging/ws` (empty: the phone says it is not set up here).
3. Restart the game server. Its log says where the screen is:
   `[open77_phone] v1.0.2: the screen is wss://...; the web server's check on /open77_phone; ...`
4. On the machine, as root, add the web server's two locations and check the whole way:

   ```bash
   mkdir -p /root/phonework && cp tools/phone_server.py /root/phonework/ && cd /root/phonework
   python3 phone_server.py look   /root/phonework --roles staging                       # read only
   python3 phone_server.py fix    /root/phonework --roles staging --ts "$(date -u +%Y%m%d-%H%M%S)"
   python3 phone_server.py verify /root/phonework --roles staging                       # one selftest
   ```

   `fix` keeps the site first (`/opt/open77-backups/phone-nginx-TS/`), runs `nginx -t`, reloads,
   waits for a game server that is still starting (up to 120 s), and checks the route three ways:
   no ticket 403; a ticket the game server makes for the machine itself 101 with VNC's own greeting;
   the same ticket again 403. Anything not right is put back by itself; `putback` takes the
   locations out again. The roles, their game server folders and their paths are `ROLES` at the top
   of the script (XyberRP's machine: staging, production, f2077, fr-us); `--public-host` names the
   players' host.

## Tests

```bash
cd open77_phone && lua5.4 tests/run.lua    # the rules, the web server's question, the client and server halves (125)
python3 tests/t_phone.py                   # end to end on one machine (51; the operator's kit adds its runner and bats)
```

`tests/t_phone.py` stands up a stand-in of the server's machine with every piece real except the
game: an Xvfb "Android" behind x11vnc and websockify, nginx with TLS and `auth_request`, the
resource's own rules through `open77_phone/tests/e2e_bridge.lua`, `tools/phone_server.py`'s modes
and put-backs, and the phone page in a real Chromium as the game's WebUI (a ticket, the screen
through nginx's check, a tap and typing reaching "Android", the phone key and Escape, busy,
refused, settings and the key chooser). It needs Xvfb, openbox, x11vnc, websockify, nginx,
lua5.4, openssl and Python Playwright with its Chromium (`O77_CHROME` names another).

The bundled noVNC is rebuilt from npm with `cd build && npm ci && node build-novnc.mjs
../open77_phone/web/vendor/novnc-rfb.js` (byte for byte the vendored file).

## Versions

- **1.0.2** (2026-11-07) — F1 puts the phone away for good: the page acts on the key's release, and
  only if it saw the press (1.0.1 put it away on the press, the game's own binding saw the same
  press once the page let go of the keyboard, and the phone came back 20 ms later; Esc was the only
  way to close it). Each opening is counted, so a put-down that reaches the server after the next
  opening's request no longer takes back its fresh ticket. A request the server does not answer is
  asked again after 3 s, three times in all.
- **1.0.1** (2026-11-07) — the page is made once the game has prepared the resource's files
  (`resource_preparing` at start); 1.0.0 tried once, too early, and F1 had nothing to show.
- **1.0.0** (2026-11-07) — first run on XyberRP Staging.

## License

open77-phone is free software under the GNU General Public License v3 (`LICENSE`). noVNC 1.6.0,
bundled as `open77_phone/web/vendor/novnc-rfb.js`, keeps its own license, the Mozilla Public License
2.0 (`noVNC-LICENSE.txt` beside it).
