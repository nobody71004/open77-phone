# Installing open77-phone on your server

This is the guide for **server owners**. It takes a machine that already runs an Open77 game server
to a phone your players take out with **F1**. Everything here is also in the release package
(`open77-phone-<version>.zip` on the [Releases](https://github.com/nobody71004/open77-phone/releases)
page), laid out the way the steps below use it:

```
open77-phone-1.0.2/
├── INSTALL.md                          this guide
├── LICENSE                             GPL-3.0
├── SHA256SUMS                          every file's checksum
├── resources/open77_phone/             the resource: copy it into your game server's resources
├── tools/phone_server.py               adds, checks and removes the web server's two locations (run as root)
└── nginx/open77-phone-locations.conf.example   the same two locations, for doing it by hand
```

**About 15 minutes**, if Android's screen is already on the machine (step 1). Every command is
safe to run again.

---

## 0. How the pieces fit

```
player's game ── wss://YOUR.HOST/open77-phone-NAME/ws?t=<ticket> ──▶ nginx (TLS, port 443)
                                                                       │ auth_request: "may this ticket open it?"
                                                                       ▼
                                          your game server's open77_phone  (httpHandlers listener, loopback)
                                                                       │ 204 yes / 403 no
                                                                       ▼
                                          websockify (127.0.0.1:8001) ──▶ VNC (127.0.0.1:5900) ──▶ Android
```

The game server hands the player who holds the phone a **ticket** (one use, 30 seconds, that
player's own address). nginx asks the game server about every WebSocket before it reaches
Android's screen, so nothing reaches Android without a ticket.

## 1. What the machine needs first

| | needed | check |
|---|---|---|
| **Open77 dedicated server** | one you can restart, and root on its machine | its log, `server.jsonc` |
| **Android's screen as VNC** | a VNC server showing Android, on loopback (wayvnc on 127.0.0.1:5900 in XyberRP's stack) | `ss -ltn \| grep 5900` |
| **websockify in front of it** | on loopback or the Docker bridge, e.g. `websockify 127.0.0.1:8001 127.0.0.1:5900` | `ss -ltn \| grep 8001` |
| **nginx with TLS** | a `server` block for the host your players use, listening on 443 | `nginx -T \| grep server_name` |
| **Python 3.8+** | for `tools/phone_server.py` | `python3 --version` |

**Android itself**: XyberRP runs Waydroid (Android 13 with Google apps, on the host's kernel; no
GPU needed) drawn into a headless sway and served by wayvnc, exactly as
[open77-tv's `shared-browser/android-native/`](https://github.com/nobody71004/open77-tv/tree/main/shared-browser/android-native)
installs it (`install.sh`: the packages, the image, the units, `open77-android-native-web.service`
running websockify on 8001). Any VNC server showing an Android works too, such as an emulator's.
The phone only needs websockify's address.

**Keep VNC and websockify off the internet.** Whatever reaches them can drive Android with no
password. The phone's route through nginx, behind its ticket check, is meant to be the only way in.

## 2. Copy the resource

Copy `resources/open77_phone/` into your game server's resources, anywhere under
`resources.root` (XyberRP uses `resources/assets/xyberrp/open77_phone`), and add it to
`resources.load` in `server.jsonc`:

```jsonc
"resources": { "load": [ /* ...yours... */ "open77_phone" ] }
```

The manifest declares the four permissions the phone uses, and nothing else:
`network.events` (requests, answers, heartbeats), `input.actions` (the key and its binding),
`http.serve` (the web server's ticket check) and `players.identity.sensitive` (the address a
player plays from, kept with the ticket for its few seconds and never logged).

## 3. Tell it where its screen is

Edit `open77_phone/server/config.lua`:

```lua
PhoneConfig = {
    wsUrl = "wss://YOUR.HOST/open77-phone-NAME/ws",  -- the route step 6 adds; empty = "Not set up here"
    label = "Android",          -- what the phone calls the device in its messages
    ticketSeconds = 30,         -- a ticket opens the screen once, within this many seconds
    leaseSeconds = 45,          -- a holder whose game goes quiet this long loses the phone
    idleSeconds = 600,          -- an untouched holder gives it up, but only when somebody waits
}
```

`NAME` is a short name for this game server (`staging`, `rp`, `freeroam`...). Each game server
on the machine gets its own route and its own tickets.

## 4. Turn on the game server's HTTP listener

The web server asks the resource about tickets over the game server's own HTTP listener. It is off
by default, and it must stay on loopback:

```jsonc
"httpHandlers": { "enabled": true, "listenUrl": "http://127.0.0.1:11781", "timeoutSeconds": 5 }
```

Pick a port nothing else uses. It is plain HTTP with no authentication, and nginx is the only
thing that should talk to it.

## 5. Restart the game server and read its first line

```
[open77_phone] v1.0.2: the screen is wss://YOUR.HOST/open77-phone-NAME/ws; the web server's check on
/open77_phone/; a ticket opens once within 30 s, a quiet holder loses the phone after 45 s, an idle
one after 600 s if somebody waits
```

`NOT set up (wsUrl empty)` means step 3 is missing; `the web server's check UNAVAILABLE` means
step 4 is.

## 6. Add the web server's two locations

### The script (recommended)

As root, from the unpacked package:

```bash
mkdir -p /root/phonework && cp tools/phone_server.py /root/phonework/ && cd /root/phonework

# READ ONLY: the site, your game server, open77_phone answering, websockify, VNC's greeting
python3 phone_server.py look /root/phonework --roles NAME --server-dir /path/to/your/server \
        --path /open77-phone-NAME --public-host YOUR.HOST --ws-upstream 127.0.0.1:8001 --vnc 127.0.0.1:5900

# the two locations, kept first, nginx -t, reload, then the whole way checked
python3 phone_server.py fix /root/phonework --roles NAME --server-dir /path/to/your/server \
        --path /open77-phone-NAME --public-host YOUR.HOST --ws-upstream 127.0.0.1:8001
```

`fix` copies the site to `/opt/open77-backups/phone-nginx-<time>/` first, adds the two locations
inside the `server` block that serves `YOUR.HOST` on 443, runs `nginx -t`, reloads nginx, waits
for a game server that is still starting (up to 120 s), and checks the route three ways:

| ask | must be |
|---|---|
| the route with no ticket | **HTTP 403**: the route is there and open77_phone said no |
| a ticket open77_phone makes for the machine itself (loopback only) | **HTTP 101**, then VNC's own greeting (`RFB 003.008`) |
| the same ticket again | **HTTP 403**: a ticket opens once |

If anything is not right, the kept site goes back and nginx is reloaded, so a failed run leaves
nothing behind. The last line of every run is `PHONE result: {...}`, and the script never prints a
ticket. `verify` runs the same three checks without changing anything, and `putback` removes the
two locations again.

On XyberRP's own machine, the roles, their folders and the websockify unit are built in
(`--roles staging`, and so on). Everywhere else, name them with the flags above.

### By hand

`nginx/open77-phone-locations.conf.example` holds the same two locations the script writes.
Paste them inside the `server { listen 443 ssl; server_name YOUR.HOST; ... }` block, change
`myserver` to your `NAME`, `11781` to your listener's port and `127.0.0.1:8001` to websockify, then
run `nginx -t && systemctl reload nginx`. Check it the same way:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://YOUR.HOST/open77-phone-NAME/ws   # 403
```

A 404 means the locations are not in the block that serves that host. A 500 means nginx could not
ask the game server: check the listener's port, and that the game server is running.

## 7. In game

Join, press **F1**: *Picking up the phone*, then *Opening the screen*, then Android. Click, drag,
scroll and type on it; Back, Home and volume are under it. **F1** or **Esc** puts it away.

Players can move the key in **Pause › Settings › Key bindings** (*Phone: open / close*), in the
phone's own settings (the gear: **Change**), or with `/phonekey <key>`. `/phone` opens it with no
key, and `/phonestatus` says who has it. At the server console, `/phonestatus` adds the counts:
asks, busy, checks, refusals and hand-backs.

## When it does not work

Everything the phone does goes to the logs, prefixed `[open77_phone]`, and no line ever holds a
ticket.

| you see | it means | do |
|---|---|---|
| *Not set up here* on the phone | `wsUrl` is empty | step 3, then restart |
| `no page yet (resource_preparing; ...)` in the client log | the game was still preparing the resource's files; the page is tried again every 3 s | nothing: `page created ... on try N` follows |
| *The phone is still loading* in the chat | F1 came before the page was made | press F1 again a moment later |
| *Trying again*, then *Can't reach the phone* | the route refused or is not there (403 / 404 / 500) | step 6: `look` says which |
| *The phone was handed on* | the holder went quiet (45 s) or idle while somebody waited | pick it up again |
| `check: refused (other_address, ...)` in the server log | the WebSocket came from another address than the player's game | a VPN or proxy on the player's side: the ticket is bound to the address the game plays from |
| `check: refused (ticket_used / ticket_expired, ...)` | a reconnect reused a ticket | nothing: the page asks for a new one by itself |
| *In use: V has the phone* | one Android, one player | wait: the phone puts you through when it is free |
| *The server did not answer* | three requests went unanswered | check the game server's log; **Try again** |

## Updating

Replace `resources/open77_phone/` with the new version, but keep your `server/config.lua`, then
restart the game server. The web server's locations stay as they are; run
`phone_server.py verify` once to check the whole way again.

## Removing

```bash
python3 phone_server.py putback /root/phonework --roles NAME --server-dir /path/to/your/server --path /open77-phone-NAME --public-host YOUR.HOST
```

Then take `open77_phone` out of `resources.load` and restart. The route answers 404 again and the
site is as it was. The copy `fix` made first is still in `/opt/open77-backups/`.
