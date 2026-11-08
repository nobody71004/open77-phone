#!/usr/bin/env python3
"""The phone's way to Android's screen on the dedicated machine (2026-11-07).  tv_kit.py sends this to a work folder of
its own and runs it as root:

    python3 phone_server.py look    WORKDIR [--roles staging,production,f2077,fr-us]          READ ONLY
    python3 phone_server.py fix     WORKDIR --roles staging --ts TS
    python3 phone_server.py verify  WORKDIR --roles staging                                    READ ONLY (one selftest)
    python3 phone_server.py putback WORKDIR --roles staging --ts TS

The in-game phone (the open77_phone resource) shows the native Android: Waydroid's screen as VNC (wayvnc on
127.0.0.1:5900), as a WebSocket (websockify, open77-android-native-web, port 8001 -- reachable from the Docker bridge
only).  A player's game reaches it through the web server, one path per game server:

    wss://<host>/open77-phone-<role>/ws?t=<ticket>  ->  auth_request: that game server's open77_phone answers whether
                                                        the ticket may open the screen (its httpHandlers listener)
                                                    ->  websockify (127.0.0.1:8001)  ->  wayvnc  ->  Android

look   says, changing nothing: the web server's site that serves <host> on 443 (the file, the server block, the phone
       locations already in it); for every role its game server (folder, httpHandlers listener, open77_phone installed
       and answering, its wsUrl); websockify and VNC (the greeting, the security it asks for, the screen's size); and
       what <host>/open77-phone-<role>/ws answers now.
fix    puts the two locations of each role into that server block (an `internal` check location and the WebSocket),
       the site kept first (/opt/open77-backups/phone-nginx-TS/), checks it with nginx -t, reloads nginx, and then
       checks the whole way (verify).  Anything not right: the kept site is put back and nginx reloaded.  A role whose
       game server's open77_phone does not answer is not touched (the check would have nobody to ask).
verify asks the whole way, end to end, for each role: no ticket -> 403; a ticket open77_phone makes for this machine
       (POST /open77_phone/v1/selftest, loopback only) -> HTTP 101 through the web server and VNC's own greeting;
       the same ticket again -> 403.
putback takes this script's locations out of the site again (kept first), nginx -t, reload.

Never prints a ticket.  The last line is "PHONE result: {...}".
"""
import base64
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

MODE = sys.argv[1] if len(sys.argv) > 1 else ""
W = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ".")
ROOT = os.environ.get("O77ROOT", "")                                  # the rehearsal's root of fake files
NGINX = os.environ.get("O77_NGINX", "nginx").split()                  # the rehearsal: nginx -c its own conf
SYSTEMCTL = os.environ.get("O77_SYSTEMCTL", "systemctl").split()
CAFILE = os.environ.get("O77_CAFILE", "")                             # the rehearsal's own certificate authority
BACKUPS = "/opt/open77-backups"
WAIT_HEALTHY = int(os.environ.get("O77_WAIT_HEALTHY", "120"))       # seconds a just-restarted server gets to answer
MARK = "open77_phone"
LINE_MAX = 320

ROLES = {
    "staging": {"server": "/opt/open77-xbuniverse-staging", "path": "/open77-phone-staging"},
    "production": {"server": "/opt/open77-xbuniverse", "path": "/open77-phone-xb"},
    "f2077": {"server": "/opt/open77-f2077", "path": "/open77-phone-f2077"},
    "fr-us": {"server": "/opt/open77-freeroamusa", "path": "/open77-phone-fr-us"},
}


def arg(name, default=""):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


PUBLIC_HOST = arg("--public-host", "xyberrp.duckdns.org")
HTTPS_PORT = int(arg("--https-port", os.environ.get("O77_HTTPS_PORT", "443")))
WS_UNIT = "open77-android-native-web.service"


def path(p):
    return ROOT + p


def onfs(f):
    """A file nginx names: in the rehearsal nginx already reads it under ROOT."""
    return f if ROOT and f.startswith(ROOT + "/") else path(f)


def shown(f):
    """A file as this machine names it (the rehearsal's ROOT taken off)."""
    return f[len(ROOT):] if ROOT and f.startswith(ROOT + "/") else f


def say(text="", limit=LINE_MAX):
    for line in str(text).splitlines() or [""]:
        print("PHONE " + line[:limit], flush=True)


def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


# ---- the game servers ---------------------------------------------------------------------------------------------

def jsonc(text):
    """JSON with comments and trailing commas (server.jsonc), read without touching strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def game_server(role):
    """What a role's game server says about itself: its folder, the httpHandlers listener, open77_phone."""
    info = {"role": role, "dir": ROLES[role]["server"], "path": ROLES[role]["path"]}
    d = path(info["dir"])
    info["exists"] = os.path.isdir(d)
    if not info["exists"]:
        return info
    cfg = os.path.join(d, "server.jsonc")
    try:
        conf = jsonc(open(cfg, encoding="utf-8").read())
        hh = conf.get("httpHandlers") or {}
        info["http_enabled"] = hh.get("enabled") is True
        m = re.match(r"^https?://(127\.0\.0\.1|localhost):(\d+)/?$", str(hh.get("listenUrl", "")))
        info["listener"] = int(m.group(2)) if m else None
        info["listen_url"] = hh.get("listenUrl")
    except (OSError, ValueError) as e:
        info["config_error"] = f"{e.__class__.__name__}: {e}"
    found = []
    for dp, dns, fns in os.walk(os.path.join(d, "resources")):
        dns[:] = [x for x in dns if x not in ("node_modules", ".git", "cache", "data")]
        if os.path.basename(dp) == "open77_phone" and "open77.lua" in fns:
            found.append(dp)
    info["installed"] = [p[len(ROOT):] if ROOT else p for p in found]
    for p in found:
        try:
            text = open(os.path.join(p, "open77.lua"), encoding="utf-8").read()
            m = re.search(r'^version\s+"([^"]+)"', text, re.M)
            info["version"] = m.group(1) if m else "?"
            conf = open(os.path.join(p, "server", "config.lua"), encoding="utf-8").read()
            m = re.search(r'wsUrl\s*=\s*"([^"]*)"', conf)
            info["ws_url"] = m.group(1) if m else None
        except OSError:
            pass
    if info.get("listener"):
        info["health"] = health(info["listener"])
    return info


def health(port, timeout=5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/open77_phone/v1/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": f"{e.__class__.__name__}: {getattr(e, 'reason', e)}"}


def selftest_ticket(port):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/open77_phone/v1/selftest", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("ticket"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, f"{e.__class__.__name__}: {getattr(e, 'reason', e)}"


# ---- the web server --------------------------------------------------------------------------------------------------

def nginx_files():
    """nginx -T: every configuration file nginx reads, as it reads them."""
    code, out = run(NGINX + ["-T"], timeout=60)
    files, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"^# configuration file (.+):$", line)
        if m:
            cur = m.group(1)
            files[cur] = []
            continue
        if cur is not None:
            files[cur].append(line)
    return code, files, out


def blocks(lines):
    """The server { } blocks of one file, as (first line, last line): braces counted outside comments and quotes, each
    block named by the directive in front of its brace."""
    out, stack, stmt = [], [], ""
    for i, raw in enumerate(lines):
        line = re.sub(r"#.*$", "", re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', '""', raw))
        for tok in re.findall(r"[{};]|[^{};]+", line):
            if tok == "{":
                words = stmt.split()
                stack.append((words[0] if words else "", i))
                stmt = ""
            elif tok == "}":
                if stack:
                    name, first = stack.pop()
                    if name == "server":
                        out.append((first, i))
                stmt = ""
            elif tok == ";":
                stmt = ""
            else:
                stmt += " " + tok
    return out


def server_blocks(files):
    found = []
    for f, lines in files.items():
        for first, last in blocks(lines):
            body = "\n".join(lines[first:last + 1])
            names = " ".join(re.findall(r"^\s*server_name\s+([^;]+);", body, re.M)).split()
            listens = re.findall(r"^\s*listen\s+([^;]+);", body, re.M)
            found.append({"file": f, "first": first, "last": last, "names": names, "listens": listens,
                          "ssl": any("443" in l or "ssl" in l for l in listens)})
    return found


def the_block(files):
    """The one server block that serves PUBLIC_HOST over TLS (HTTPS_PORT), or why there is not exactly one."""
    cands = [b for b in server_blocks(files) if PUBLIC_HOST in b["names"]
             and any(re.search(rf"(^|[\s:]){HTTPS_PORT}(\s|$)", l) for l in b["listens"])]
    if len(cands) != 1:
        return None, cands
    return cands[0], cands


def block_lines(name, port, upstream, ts):
    """The two locations a role needs, as one comment and two lines (the comment carries this script's mark)."""
    p = ROLES[name]["path"]
    return [
        f"    # {MARK} {name}: the phone's screen (websockify {upstream}) behind open77_phone's ticket check on "
        f"127.0.0.1:{port} (phone_server.py {ts})",
        f"    location = {p}/auth {{ internal; proxy_pass http://127.0.0.1:{port}/open77_phone/v1/check; "
        f"proxy_pass_request_body off; proxy_set_header Content-Length \"\"; proxy_set_header X-Original-URI $request_uri; "
        f"proxy_set_header X-Real-IP $remote_addr; proxy_connect_timeout 3s; proxy_read_timeout 5s; access_log off; }}",
        f"    location = {p}/ws {{ auth_request {p}/auth; proxy_pass http://{upstream}/websockify; proxy_http_version 1.1; "
        f"proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection \"upgrade\"; proxy_set_header Host $host; "
        f"proxy_read_timeout 3600s; proxy_send_timeout 3600s; proxy_buffering off; access_log off; }}",
    ]


def ours(line, role=None):
    """A line this script wrote: its comment, or one of a role's two locations."""
    if f"# {MARK} " in line and "phone_server.py" in line:
        return role is None or f"# {MARK} {role}:" in line
    if role is None:
        return bool(re.search(r"^\s*location = /open77-phone-[\w-]+/(auth|ws) \{", line))
    return bool(re.search(r"^\s*location = " + re.escape(ROLES[role]["path"]) + r"/(auth|ws) \{", line))


def websockify_upstream():
    """Where websockify listens, from its unit's command line (0.0.0.0:8001 -> 127.0.0.1:8001)."""
    code, out = run(SYSTEMCTL + ["show", WS_UNIT, "-p", "ExecStart", "-p", "ActiveState"])
    props = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
    m = re.search(r"websockify\S*\s+(?:--\S+\s+)*(?:([\d.]+|\[[^\]]*\]):)?(\d+)\s+([\d.]+):(\d+)", props.get("ExecStart", ""))
    if not m:
        override = os.environ.get("O77_WS_UPSTREAM")
        return (override, props.get("ActiveState", "?"), None) if override else (None, props.get("ActiveState", "?"), None)
    return f"127.0.0.1:{m.group(2)}", props.get("ActiveState", "?"), f"{m.group(3)}:{m.group(4)}"


# ---- VNC and the WebSocket -------------------------------------------------------------------------------------------

VNC_TYPES = {1: "None", 2: "VNC password", 16: "Tight", 18: "TLS", 19: "VeNCrypt", 30: "Apple"}


def rfb_size(host, port):
    """VNC's greeting, its security types, and (with None) the screen's size and name. A shared look, then gone."""
    try:
        s = socket.create_connection((host, port), timeout=5)
        s.settimeout(5)
        ver = s.recv(12)
        if not ver.startswith(b"RFB "):
            return {"error": f"not VNC: {ver[:12]!r}"}
        s.sendall(b"RFB 003.008\n")
        n = s.recv(1)
        types = list(s.recv(n[0])) if n and n[0] else []
        out = {"version": ver.decode().strip(), "security": [VNC_TYPES.get(t, t) for t in types]}
        if 1 in types:
            s.sendall(b"\x01")
            res = s.recv(4)
            if res == b"\0\0\0\0":
                s.sendall(b"\x01")                                     # ClientInit: shared
                head = b""
                while len(head) < 24:
                    chunk = s.recv(24 - len(head))
                    if not chunk:
                        break
                    head += chunk
                if len(head) == 24:
                    w, h = struct.unpack(">HH", head[:4])
                    nlen = struct.unpack(">I", head[20:24])[0]
                    name = s.recv(min(nlen, 200)).decode("utf-8", "replace") if nlen else ""
                    out.update(width=w, height=h, name=name)
        s.close()
        return out
    except OSError as e:
        return {"error": f"{e.__class__.__name__}: {e}"}


def https_get(p, extra_headers=None):
    """GET https://PUBLIC_HOST<p> from this machine to this machine's own web server (127.0.0.1:HTTPS_PORT with
    PUBLIC_HOST's name, certificate checked): (status line, headers, the rest of the socket)."""
    ctx = ssl.create_default_context(cafile=CAFILE or None)
    raw = socket.create_connection(("127.0.0.1", HTTPS_PORT), timeout=10)
    s = ctx.wrap_socket(raw, server_hostname=PUBLIC_HOST)
    s.settimeout(10)
    lines = [f"GET {p} HTTP/1.1", f"Host: {PUBLIC_HOST}", "User-Agent: open77-phone-check"]
    lines += [f"{k}: {v}" for k, v in (extra_headers or {}).items()]
    s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("latin-1")
    return status, head.decode("latin-1"), rest, s


def code_of(status):
    m = re.match(r"HTTP/\S+\s+(\d+)", status or "")
    return int(m.group(1)) if m else 0


def ws_open(p):
    """A WebSocket through the web server to p; (HTTP code, the first message's start)."""
    key = base64.b64encode(os.urandom(16)).decode()
    status, head, rest, s = https_get(p, {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Key": key,
                                          "Sec-WebSocket-Version": "13", "Sec-WebSocket-Protocol": "binary",
                                          "Origin": f"https://{PUBLIC_HOST}"})
    code = code_of(status)
    first = b""
    try:
        if code == 101:
            buf = rest
            deadline = time.time() + 8
            while len(buf) < 2 and time.time() < deadline:
                buf += s.recv(4096)
            if len(buf) >= 2:
                ln = buf[1] & 0x7F
                off = 2 + (2 if ln == 126 else 8 if ln == 127 else 0)
                if ln == 126 and len(buf) >= 4:
                    ln = struct.unpack(">H", buf[2:4])[0]
                while len(buf) < off + min(ln, 12) and time.time() < deadline:
                    buf += s.recv(4096)
                first = buf[off:off + min(ln, 12)]
                if buf[0] & 0x0F == 1:                                     # a text frame: base64
                    try:
                        first = base64.b64decode(first + b"==")[:12]
                    except ValueError:
                        pass
            try:
                s.sendall(bytes([0x88, 0x80]) + os.urandom(4))               # close, masked, empty
            except OSError:
                pass
    finally:
        s.close()
    return code, first


def route_answer(role):
    """What PUBLIC_HOST/open77-phone-<role>/ws answers with no ticket: 404 (no route), 403 (the check said no: the
    route and the game server answer), 500 (the check could not be asked)."""
    try:
        status, _, _, s = https_get(ROLES[role]["path"] + "/ws")
        s.close()
        return code_of(status), None
    except ssl.SSLError as e:
        return 0, f"TLS: {e.__class__.__name__}: {getattr(e, 'reason', e)}"
    except OSError as e:
        return 0, f"{e.__class__.__name__}: {e}"


def routed(role, target, tries):
    """Open the WebSocket; a 404 here comes from an nginx worker still on the configuration before the reload (it
    never asks open77_phone, so a ticket it was shown is still unused): asked again, up to `tries` times.  The t_phone
    rehearsal hit exactly that once in two runs: 403 without a ticket, then 404 with one, from the old worker."""
    stale = 0
    for i in range(tries):
        try:
            code, first = ws_open(target)
        except (OSError, ssl.SSLError) as e:
            code, first = 0, f"{e.__class__.__name__}: {e}".encode()
        if code != 404 or i == tries - 1:
            return code, first, stale
        stale += 1
        time.sleep(0.5)
    return code, first, stale


def verify_role(role, gs, tries=10):
    """The whole way for one role: no ticket 403; a selftest ticket 101 + VNC's greeting; the same ticket again 403."""
    res = {"role": role}
    code, err = 0, None
    for i in range(tries):
        code, err = route_answer(role)
        if code == 403:
            break
        time.sleep(1)                                                        # a reload hands over to new workers
    res["no_ticket"] = code if not err else err
    say(f"  {role}: {ROLES[role]['path']}/ws with no ticket: " + (f"HTTP {code}" if not err else err)
        + {403: " (the route is there and open77_phone said no: right)", 404: " (no such route)",
           500: " (the route is there, but open77_phone could not be asked)"}.get(code, ""))
    if code != 403:
        res["ok"] = False
        return res
    ticket, why = selftest_ticket(gs["listener"])
    if not ticket:
        say(f"  {role}: open77_phone made no selftest ticket: {why}")
        res.update(ok=False, selftest=why)
        return res
    wcode, first, stale = routed(role, f"{ROLES[role]['path']}/ws?t={ticket}", tries)
    res["with_ticket"] = wcode
    res["greeting"] = first.decode("latin-1", "replace").strip()
    say(f"  {role}: with a ticket open77_phone made for this machine: HTTP {wcode}"
        + (f", and VNC's own greeting through it: {res['greeting']!r}" if first.startswith(b"RFB ") else
           f", but no VNC greeting ({first[:40]!r})" if wcode == 101 else "")
        + (f" (after {stale} answer(s) from a worker still on the old configuration)" if stale else ""))
    again, _, stale = routed(role, f"{ROLES[role]['path']}/ws?t={ticket}", tries)
    res["again"] = again
    say(f"  {role}: the same ticket again: HTTP {again}" + (" (once only: right)" if again == 403 else " -- NOT refused"))
    res["ok"] = wcode == 101 and first.startswith(b"RFB ") and again == 403
    return res


# ---- the modes -------------------------------------------------------------------------------------------------------

def roles_asked():
    names = [r.strip() for r in arg("--roles", "staging,production,f2077,fr-us").split(",") if r.strip()]
    bad = [r for r in names if r not in ROLES]
    if bad:
        raise SystemExit(f"unknown role(s): {', '.join(bad)} (known: {', '.join(ROLES)})")
    return names


def look():
    say(f"===== the phone's way to Android's screen, for {PUBLIC_HOST} (READ ONLY)")
    code, files, out = nginx_files()
    say(f"  nginx -T: exit {code}, {len(files)} file(s)")
    block, cands = the_block(files)
    res = {"site": None, "roles": {}}
    if block is None:
        say(f"  !! {len(cands)} server block(s) serve {PUBLIC_HOST} on {HTTPS_PORT}: "
            + ("; ".join(f"{shown(c['file'])} lines {c['first'] + 1}-{c['last'] + 1}" for c in cands) or "none"))
    else:
        f = block["file"]
        real = os.path.realpath(onfs(f))
        lines = files[f][block["first"]:block["last"] + 1]
        phone = [ln.strip()[:120] for ln in lines if ours(ln)]
        tv = [ln.strip().split("{")[0].strip() for ln in lines if "location /tv-browser-" in ln]
        say(f"  the site: {shown(f)}" + (f" (a link to {shown(real)})" if os.path.islink(onfs(f)) else "")
            + f", its server block for {PUBLIC_HOST}:{HTTPS_PORT} at lines {block['first'] + 1}-{block['last'] + 1}"
            f" ({len(lines)} lines; {', '.join(tv) or 'no TV locations'})")
        say("  the phone's locations already there: " + ("; ".join(phone) if phone else "none"))
        res["site"] = {"file": shown(f), "first": block["first"] + 1, "last": block["last"] + 1, "phone_lines": len(phone)}
    up, state, vnc = websockify_upstream()
    say(f"  websockify ({WS_UNIT}): {state}, listening for the web server at {up or '?'}, carrying VNC to {vnc or '?'}")
    res["websockify"] = {"upstream": up, "state": state, "vnc": vnc}
    if vnc:
        host, port = vnc.rsplit(":", 1)
        rf = rfb_size(host, int(port))
        if "error" in rf:
            say(f"  VNC ({vnc}): !! {rf['error']}")
        else:
            say(f"  VNC ({vnc}): {rf['version']}, security {', '.join(map(str, rf['security']))}"
                + (f"; the screen is {rf['width']}x{rf['height']} ('{rf.get('name', '')}')" if "width" in rf else ""))
        res["vnc"] = rf
    for role in roles_asked():
        gs = game_server(role)
        res["roles"][role] = gs
        if not gs["exists"]:
            say(f"  {role}: no game server at {gs['dir']}")
            continue
        h = gs.get("health") or {}
        say(f"  {role} ({gs['dir']}): httpHandlers " + ("on" if gs.get("http_enabled") else "OFF")
            + f" at {gs.get('listen_url') or '?'}; open77_phone "
            + (f"{gs.get('version', '?')} installed in {', '.join(gs['installed'])}" if gs["installed"] else "not installed")
            + (f", its wsUrl {gs.get('ws_url')!r}" if gs["installed"] else "")
            + ("; answering: v" + str(h.get("version")) + (", configured" if h.get("configured") else ", NOT configured")
               + (", somebody holds the phone" if h.get("held") else "") if "version" in h else
               f"; not answering ({h.get('error', 'no listener')})" if gs.get("listener") else ""))
        if gs.get("config_error"):
            say(f"    !! server.jsonc: {gs['config_error']}")
        code, err = route_answer(role)
        gs["route"] = code if not err else err
        say(f"    {PUBLIC_HOST}{ROLES[role]['path']}/ws now: " + (f"HTTP {code}" if not err else err))
    return res


def edit_site(block, files, roles, ts, add):
    """The site's file with each role's lines added (add) or taken out; returns (path, old text, new text)."""
    f = block["file"]
    real = os.path.realpath(onfs(f))
    text = open(real, encoding="utf-8", newline="").read()
    lines = text.split("\n")
    eol = "\r" if lines and lines[0].endswith("\r") else ""                 # a file with CRLF keeps CRLF
    bare = [ln.rstrip("\r") for ln in lines]
    # nginx -T shows the file as nginx read it: find the block's lines again in the file itself
    want = [ln.rstrip("\r") for ln in files[f][block["first"]:block["last"] + 1]]
    starts = [i for i in range(len(bare)) if bare[i:i + len(want)] == want]
    if len(starts) != 1:
        raise RuntimeError(f"the server block was not found once in {real} (found {len(starts)} times)")
    first, last = starts[0], starts[0] + len(want) - 1
    body = lines[first:last + 1]
    if add:
        for role, port, upstream in roles:
            if any(ours(ln, role) for ln in body):
                continue
            new = [ln + eol for ln in block_lines(role, port, upstream, ts)]
            anchor = max((i for i, ln in enumerate(body) if "location /tv-browser-" in ln or ours(ln)), default=None)
            if anchor is None:
                anchor = next((i - 1 for i, ln in enumerate(body) if re.match(r"\s*location\s+/\s*\{", ln)), len(body) - 2)
            body[anchor + 1:anchor + 1] = new
    else:
        names = [r for r, _, _ in roles]
        body = [ln for ln in body if not any(ours(ln, r) for r in names)]
    lines[first:last + 1] = body
    return real, text, "\n".join(lines)


def reload_nginx():
    code, out = run(NGINX + ["-t"], timeout=60)
    if code != 0:
        return False, out.strip()[-800:]
    code, out2 = run(SYSTEMCTL + ["reload", "nginx"], timeout=60)
    return code == 0, (out + out2).strip()[-400:]


def keep(real, ts):
    d = path(os.path.join(BACKUPS, f"phone-nginx-{ts}"))
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(real))
    if not os.path.exists(dest):
        shutil.copy2(real, dest)
    with open(os.path.join(d, "README.txt"), "w") as f:
        f.write(f"nginx site files as they were before phone_server.py ({ts}); {shown(real)}\n")
    return dest


def twin_of(real):
    """sites-enabled/X and sites-available/X kept as two copies (the move did that): the other one, if it is a copy."""
    if os.path.islink(real):
        return None
    base, name = os.path.split(real)
    other = None
    if base.endswith("/sites-enabled"):
        other = os.path.join(base[:-len("sites-enabled")] + "sites-available", name)
    elif base.endswith("/sites-available"):
        other = os.path.join(base[:-len("sites-available")] + "sites-enabled", name)
    if other and os.path.isfile(other) and not os.path.islink(other):
        return other
    return None


def fix(ts):
    say(f"===== the phone's way to Android's screen: the web server's locations for {', '.join(roles_asked())}")
    code, files, out = nginx_files()
    block, cands = the_block(files)
    if block is None:
        say(f"  !! not exactly one server block serves {PUBLIC_HOST} on {HTTPS_PORT} ({len(cands)}): nothing changed")
        return {"result": "not changed", "why": "site"}
    up, state, vnc = websockify_upstream()
    if not up:
        say(f"  !! where websockify listens could not be read from {WS_UNIT}: nothing changed")
        return {"result": "not changed", "why": "websockify"}
    todo, res = [], {"roles": {}}
    for role in roles_asked():
        gs = game_server(role)
        h = gs.get("health") or {}
        if not gs["exists"] or not gs.get("http_enabled") or not gs.get("listener"):
            say(f"  {role}: its game server has no httpHandlers listener ({gs.get('listen_url') or 'none'}): not this role")
            res["roles"][role] = "no listener"
            continue
        if "version" not in h and gs.get("installed"):
            # Installed but not answering yet: a server that has just been restarted (PH1 runs this right after the
            # install) answers its listener only once its resources are up -- measured 2026-11-07 on Staging: a
            # timeout 7 s after the restart, an answer 4 s later.
            say(f"  {role}: open77_phone is installed but does not answer yet ({h.get('error', '?')}): waiting for "
                f"the server to finish starting (up to {WAIT_HEALTHY} s)")
            t0 = time.time()
            end, said = t0 + WAIT_HEALTHY, t0
            while "version" not in h and time.time() < end:
                time.sleep(3)
                h = health(gs["listener"], timeout=max(2, min(8, end - time.time())))
                if "version" not in h and time.time() - said >= 15:
                    said = time.time()
                    say(f"  {role}: still waiting ({said - t0:.0f} s): {h.get('error', '?')}")
            if "version" in h:
                say(f"  {role}: open77_phone answers now, after {time.time() - t0:.0f} s (v{h.get('version')}, "
                    + ("configured)" if h.get("configured") else "NOT configured)"))
                gs["health"] = h
        if "version" not in h:
            say(f"  {role}: open77_phone does not answer on 127.0.0.1:{gs['listener']} ({h.get('error', '?')}): "
                + ("install it on that server first" if not gs.get("installed") else "is that server running?")
                + " -- the web server's check would have nobody to ask")
            res["roles"][role] = "open77_phone not answering"
            continue
        todo.append((role, gs["listener"], up, gs))
    if not todo:
        return dict(res, result="not changed")
    real, old, new = edit_site(block, files, [(r, p, u) for r, p, u, _ in todo], ts, add=True)
    if new == old:
        say("  the locations are already in the site: nothing to add")
    else:
        kept = keep(real, ts)
        twin = twin_of(real)
        twin_same = twin is not None and open(twin, encoding="utf-8", newline="").read() == old
        if twin is not None:
            keep(twin, ts)
        with open(real, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        ok, detail = reload_nginx()
        if not ok:
            with open(real, "w", encoding="utf-8", newline="") as f:
                f.write(old)
            run(SYSTEMCTL + ["reload", "nginx"])
            say("  !! nginx refused the new site; the kept one is back:")
            say("     " + detail.replace("\n", "\n     "))
            return dict(res, result="put back", why="nginx -t")
        added = len([ln for ln in new.split("\n") if ours(ln)]) - len([ln for ln in old.split("\n") if ours(ln)])
        say(f"  {shown(real)}: {added} line(s) added (kept first in {shown(kept)}); "
            "nginx -t accepted it, nginx reloaded")
        if twin_same:
            shutil.copy2(real, twin)
            say(f"  {shown(twin)}: the same file again (the two copies stay equal)")
    say("===== the whole way, checked")
    all_ok = True
    for role, port, upstream, gs in todo:
        v = verify_role(role, gs)
        res["roles"][role] = "working" if v.get("ok") else "NOT working"
        all_ok = all_ok and v.get("ok")
    if not all_ok and new != old:
        with open(real, "w", encoding="utf-8", newline="") as f:
            f.write(old)
        if twin_same:
            shutil.copy2(real, twin)
        ok, detail = reload_nginx()
        say("  !! not every role works: the kept site is back and nginx reloaded" + ("" if ok else f" (!! {detail})"))
        return dict(res, result="put back")
    skipped = [r for r, v in res["roles"].items() if v not in ("working", "NOT working")]
    if all_ok and skipped:
        say(f"  not done (see above): {', '.join(skipped)}")
        return dict(res, result="partly")
    return dict(res, result="fixed" if all_ok else "not working")


def verify():
    say(f"===== the phone's way to Android's screen, end to end, for {', '.join(roles_asked())}")
    res, ok = {"roles": {}}, True
    for role in roles_asked():
        gs = game_server(role)
        if not gs.get("listener"):
            say(f"  {role}: no httpHandlers listener")
            res["roles"][role] = "no listener"
            ok = False
            continue
        v = verify_role(role, gs, tries=3)
        res["roles"][role] = "working" if v.get("ok") else "NOT working"
        ok = ok and v.get("ok")
    res["result"] = "working" if ok else "not working"
    return res


def putback(ts):
    say(f"===== the phone's locations taken out of the web server's site for {', '.join(roles_asked())}")
    code, files, out = nginx_files()
    block, cands = the_block(files)
    if block is None:
        say("  !! the site could not be found: nothing changed")
        return {"result": "not changed"}
    real, old, new = edit_site(block, files, [(r, 0, "") for r in roles_asked()], ts, add=False)
    if new == old:
        say("  none of this script's locations are there: nothing to take out")
        return {"result": "nothing"}
    kept = keep(real, ts)
    twin = twin_of(real)
    twin_same = twin is not None and open(twin, encoding="utf-8", newline="").read() == old
    with open(real, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    ok, detail = reload_nginx()
    if not ok:
        with open(real, "w", encoding="utf-8", newline="") as f:
            f.write(old)
        run(SYSTEMCTL + ["reload", "nginx"])
        say(f"  !! nginx refused the site without them; nothing changed: {detail[-300:]}")
        return {"result": "not changed"}
    if twin_same:
        shutil.copy2(real, twin)
    say(f"  {shown(real)}: the phone's lines taken out (kept first in {shown(kept)});"
        " nginx reloaded")
    for role in roles_asked():
        code = 0
        for _ in range(10):
            code, err = route_answer(role)
            if code == 404:
                break
            time.sleep(1)
        say(f"  {role}: {ROLES[role]['path']}/ws now answers HTTP {code}" + (" (gone: right)" if code == 404 else ""))
    return {"result": "put back"}


def main():
    if MODE not in ("look", "fix", "verify", "putback"):
        print(__doc__)
        return 2
    ts = arg("--ts") or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    try:
        if MODE == "look":
            res = look()
        elif MODE == "verify":
            res = verify()
        elif MODE == "fix":
            res = fix(ts)
        else:
            res = putback(ts)
        good = {"look": None, "fix": ("fixed",), "verify": ("working",), "putback": ("put back", "nothing")}[MODE]
        rc = 0 if good is None or res.get("result") in good else 1
    except SystemExit as e:
        say(f"!! {e}")
        res, rc = {"result": "error", "error": str(e)}, 2
    except Exception as e:  # noqa: BLE001
        import traceback
        say(f"!! {e.__class__.__name__}: {e}")
        for ln in traceback.format_exc().splitlines()[-6:]:
            say("   " + ln)
        res, rc = {"result": "error"}, 1
    say("")
    say("result: " + json.dumps(res, separators=(",", ":"), default=str)[:6000], limit=100000)
    return rc


if __name__ == "__main__":
    sys.exit(main())
