"""Rehearsal of the in-game phone, end to end, on one machine (2026-11-07).

A stand-in of the dedicated machine, every piece real except the game:
  * "Android": Xvfb 1280x720 with openbox and a kiosk Chromium showing a page that records taps and keys;
    x11vnc on it (127.0.0.1:5995, no password, shared), websockify in front (127.0.0.1:8095) -- the shape of
    wayvnc + open77-android-native-web on rl31;
  * the web server: nginx with TLS (a certificate made here), a site shaped like rl31's (an old host's block and
    xyberrp.duckdns.org's, with the TV locations and `location / { return 404; }`), its sites-available twin;
  * the game server's httpHandlers listener: an HTTP server whose /open77_phone/* is answered by the REAL rules
    (open77_phone/tests/e2e_bridge.lua: server/lease.lua and server/http.lua behind a pipe);
  * phone_server.py exactly as the kit sends it (the kit's remote/, the repository's tools/): look, fix (nginx -t, reload, the whole way checked), verify,
    putback, and its refusals and put-backs;
  * the phone page (open77_phone/web) in a real Chromium as the game's WebUI, with a stand-in of the Lua bridge:
    a ticket, the screen through nginx's ticket check, a tap and typing reaching "Android", the phone key and Escape,
    busy, refused, settings and the key chooser -- and pictures of each, for the eye.
"""
import glob
import http.server
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# Where this file lives: the operator's kit (tests/phone/t_phone.py, beside remote/phone_server.py), or the
# open77-phone repository (tests/t_phone.py, beside tools/phone_server.py and open77_phone/).
if os.path.isfile(os.path.join(HERE, "..", "..", "remote", "phone_server.py")):
    KIT = os.path.abspath(os.path.join(HERE, "..", ".."))
    SCRIPT = os.path.join(KIT, "remote", "phone_server.py")
else:
    KIT = os.path.abspath(os.path.join(HERE, ".."))
    SCRIPT = os.path.join(KIT, "tools", "phone_server.py")
# the resource: where O77_PHONE_RES says, beside this file in the repository, or the operator's working copy
RES = os.environ.get("O77_PHONE_RES") or next((p_ for p_ in (os.path.join(KIT, "open77_phone"),
                                                              "/home/claude/phone/open77_phone")
                                               if os.path.isfile(os.path.join(p_, "open77.lua"))), "")
RES = os.path.abspath(RES)
RES_VERSION = (re.search(r'^version\s+"([^"]+)"', open(os.path.join(RES, "open77.lua")).read(), re.M) or [None, "?"])[1] \
    if os.path.isfile(os.path.join(RES, "open77.lua")) else "?"
SHOTS = os.environ.get("O77_PHONE_SHOTS", "")
T = tempfile.mkdtemp(prefix="t-phone-")
ROOT = os.path.join(T, "root")
PROCS = []
fails = 0


def check(what, cond, detail=""):
    global fails
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else f"\n        {str(detail)[-2500:]}"), flush=True)
    fails += 0 if cond else 1


def write(p, text, mode=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    if mode:
        os.chmod(p, mode)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def spawn(cmd, **kw):
    p = subprocess.Popen(cmd, stdout=kw.pop("stdout", subprocess.DEVNULL), stderr=kw.pop("stderr", subprocess.DEVNULL), **kw)
    PROCS.append(p)
    return p


def cleanup():
    for p in reversed(PROCS):
        try:
            p.terminate()
        except OSError:
            pass
    time.sleep(0.4)
    for p in PROCS:
        try:
            p.kill()
        except OSError:
            pass
    subprocess.run(NGINX + ["-s", "stop"], capture_output=True)


def wait_port(port, secs=15):
    end = time.time() + secs
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


# ---- "Android" ------------------------------------------------------------------------------------------------------
DISPLAY = ":96"
VNC_PORT, WS_PORT = 5995, 8095
HP, LP, LP2 = free_port(), free_port(), free_port()
subprocess.run(["pkill", "-f", f"Xvfb {DISPLAY} "], capture_output=True)
subprocess.run(["pkill", "-f", f"rfbport {VNC_PORT}"], capture_output=True)
subprocess.run(["pkill", "-f", f"websockify 127.0.0.1:{WS_PORT}"], capture_output=True)
time.sleep(0.3)
spawn(["Xvfb", DISPLAY, "-screen", "0", "1280x720x24", "-nolisten", "tcp"])
time.sleep(1)
spawn(["openbox"], env=dict(os.environ, DISPLAY=DISPLAY))

ANDROID = {"ev": [], "value": "", "loads": 0}
ANDROID_PAGE = """<!doctype html><title>android</title>
<style>html,body{margin:0;height:100%;background:#2e7d32;font:28px sans-serif;color:#fff;overflow:hidden}
#b{position:absolute;left:100px;top:100px;width:300px;height:160px;background:#fff;color:#000;border:0;font-size:30px}
#i{position:absolute;left:100px;top:320px;width:600px;height:60px;font-size:28px}
#bar{position:absolute;left:0;right:0;bottom:0;height:48px;background:#111}</style>
<button id=b>TAP</button><input id=i autofocus><div id=bar></div>
<script>const ev=[];const i=document.getElementById('i');
document.getElementById('b').onclick=()=>{ev.push('tap');i.focus();};
addEventListener('keydown',e=>ev.push('k:'+e.key),true);
setInterval(()=>{fetch('/__android/report',{method:'POST',body:JSON.stringify({ev,value:i.value})})},250);</script>"""

# ---- the game server's listener: the real rules behind a pipe -------------------------------------------------------
BRIDGE = subprocess.Popen(["lua5.4", "tests/e2e_bridge.lua"], cwd=RES, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, bufsize=1)
PROCS.append(BRIDGE)
BLOCK = threading.Lock()
T0 = time.time()
NOTES = []


def bridge(msg):
    msg = dict(msg, now=int((time.time() - T0) * 1000))
    with BLOCK:
        BRIDGE.stdin.write(json.dumps(msg) + "\n")
        BRIDGE.stdin.flush()
        return json.loads(BRIDGE.stdout.readline())


# off: a game server still starting -- the port takes the request and the answer waits for the resources (`hang`
# seconds at most, then 503).  rl31, 2026-11-07: "TimeoutError: timed out" 7 s after Staging's restart, answering 4 s
# later.
LISTENER_UP = {"on": True, "hang": 0}


class Listener(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self):
        if not LISTENER_UP["on"]:
            end = time.time() + LISTENER_UP.get("hang", 0)
            while not LISTENER_UP["on"] and time.time() < end:
                time.sleep(0.1)
            if not LISTENER_UP["on"]:
                try:
                    self.send_response(503)
                    self.end_headers()
                except OSError:                 # the asker gave up waiting
                    pass
                return
        u = urllib.parse.urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else ""
        req = {"method": self.command, "path": u.path, "query": dict(urllib.parse.parse_qsl(u.query)), "body": body,
               "headers": {k: v for k, v in self.headers.items()}, "remoteAddress": self.client_address[0]}
        a = bridge({"op": "http", "req": req})
        if a.get("note"):
            NOTES.append(a["note"])
        try:
            self.send_response(a["status"])
            for k, v in (a.get("headers") or {}).items():
                self.send_header(k, v)
            data = (a.get("body") or "").encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:                         # held while "starting", and the asker gave up waiting
            pass

    do_GET = do_POST = do_HEAD = _go


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        if u.path == "/android.html":
            ANDROID["loads"] += 1
            return self._send(200, ANDROID_PAGE.encode(), "text/html")
        if u.path.startswith("/web/"):
            p = os.path.normpath(os.path.join(RES, u.path.lstrip("/")))
            if p.startswith(os.path.join(RES, "web")) and os.path.isfile(p):
                ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(os.path.splitext(p)[1], "application/octet-stream")
                return self._send(200, open(p, "rb").read(), ctype)
        if u.path == "/__test/request":
            q = dict(urllib.parse.parse_qsl(u.query))
            a = bridge({"op": "request", "player": int(q.get("player", 1)), "name": q.get("name", "Tester"), "ip": q.get("ip")})
            return self._send(200, json.dumps(a).encode(), "application/json")
        if u.path == "/__test/release":
            q = dict(urllib.parse.parse_qsl(u.query))
            a = bridge({"op": "release", "player": int(q.get("player", 1))})
            return self._send(200, json.dumps(a).encode(), "application/json")
        self._send(404, b"", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        if self.path == "/__android/report":
            try:
                ANDROID.update(json.loads(data))
            except ValueError:
                pass
        self._send(200, b"ok", "text/plain")

    def _send(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


for port, handler in ((LP, Listener), (HP, Site)):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

def chrome_path():
    """The Chromium "Android" shows its page in: O77_CHROME, or the one Python Playwright installed."""
    if os.environ.get("O77_CHROME"):
        return os.environ["O77_CHROME"]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw_:
        return pw_.chromium.executable_path


CHROME = chrome_path()
spawn([CHROME, "--no-sandbox", "--kiosk", "--window-position=0,0", "--no-first-run", "--disable-gpu",
       f"--user-data-dir={tempfile.mkdtemp(prefix='android-')}", f"http://127.0.0.1:{HP}/android.html"],
      env=dict(os.environ, DISPLAY=DISPLAY))
spawn(["x11vnc", "-display", DISPLAY, "-rfbport", str(VNC_PORT), "-localhost", "-nopw", "-shared", "-forever", "-quiet",
       "-noxdamage"])
time.sleep(1.5)
spawn(["websockify", f"127.0.0.1:{WS_PORT}", f"127.0.0.1:{VNC_PORT}"])
wait_port(WS_PORT)

# ---- the web server -------------------------------------------------------------------------------------------------
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=xyberrp.duckdns.org",
                "-addext", "subjectAltName=DNS:xyberrp.duckdns.org,DNS:xbuniverse.duckdns.org",
                "-keyout", os.path.join(T, "key.pem"), "-out", os.path.join(T, "cert.pem")], capture_output=True, check=True)
for d in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
    os.makedirs(os.path.join(T, "tmp", d), exist_ok=True)
write(os.path.join(T, "nginx.conf"), f"""user root;
worker_processes 1;
error_log {T}/nginx-error.log notice;
pid {T}/nginx.pid;
events {{ worker_connections 256; }}
http {{
    access_log {T}/nginx-access.log;
    client_body_temp_path {T}/tmp/body; proxy_temp_path {T}/tmp/proxy; fastcgi_temp_path {T}/tmp/fastcgi;
    uwsgi_temp_path {T}/tmp/uwsgi; scgi_temp_path {T}/tmp/scgi;
    include {ROOT}/etc/nginx/sites-enabled/*;
}}
""")
SITE = f"""server {{
    listen 127.0.0.1:8080;
    server_name xbuniverse.duckdns.org;
    location / {{ return 301 https://$host$request_uri; }}
}}

server {{
    listen 127.0.0.1:8443 ssl;
    server_name xbuniverse.duckdns.org;
    ssl_certificate {T}/cert.pem;
    ssl_certificate_key {T}/key.pem;
    location /tv-browser-xb-staging/ {{ proxy_pass http://127.0.0.1:18080; }}
    location / {{ return 404; }}
}}

server {{
    listen 127.0.0.1:8443 ssl;
    server_name xyberrp.duckdns.org;
    ssl_certificate {T}/cert.pem;
    ssl_certificate_key {T}/key.pem;
    # a comment with a brace {{ in it, and a "quoted }} brace"
    location /opx-watch-xb-staging/ {{ proxy_pass http://127.0.0.1:11843/opx_watchparty/; proxy_set_header X-Real-IP $remote_addr; }}
    location /tv-browser-xb-staging/ {{ proxy_pass http://127.0.0.1:18080; proxy_http_version 1.1; }}
    location /tv-browser-xb/ {{ proxy_pass http://127.0.0.1:18081; }}
    location /healthz {{ return 200 "ok\\n"; add_header Content-Type text/plain; }}
    location / {{ return 404; }}
}}
"""
write(os.path.join(ROOT, "etc/nginx/sites-enabled/redsync-cdn"), SITE)
write(os.path.join(ROOT, "etc/nginx/sites-available/redsync-cdn"), SITE)
NGINX = ["nginx", "-c", os.path.join(T, "nginx.conf")]
write(os.path.join(T, "bin", "nginx-wrap"), f"""#!/bin/bash
if [ "$1" = "-t" ] && [ -f {T}/fail-t ]; then echo "nginx: [emerg] the rehearsal refuses this configuration"; exit 1; fi
exec nginx -c {T}/nginx.conf "$@"
""", 0o755)
write(os.path.join(T, "bin", "systemctl"), f"""#!/bin/bash
case "$1" in
  reload) exec nginx -c {T}/nginx.conf -s reload ;;
  show) echo "ExecStart={{ path=/usr/bin/websockify ; argv[]=/usr/bin/websockify --web=/usr/share/novnc 0.0.0.0:{WS_PORT} 127.0.0.1:{VNC_PORT} ; ignore_errors=no ; start_time=[n/a] }}"
        echo "ActiveState=active" ;;
  *) echo "systemctl stand-in: $*" ;;
esac
""", 0o755)
r = subprocess.run(NGINX, capture_output=True, text=True)
check("(the stand-in web server starts)", r.returncode == 0 and wait_port(8443), r.stderr)

# ---- the game servers: staging has open77_phone, production does not -------------------------------------------------
STAGING = os.path.join(ROOT, "opt/open77-xbuniverse-staging")
write(os.path.join(STAGING, "server.jsonc"), f"""{{
  // the server's own settings (a comment, and a trailing comma)
  "name": "XyberRP Staging // not a comment",
  "httpHandlers": {{ "enabled": true, "listenUrl": "http://127.0.0.1:{LP}", "timeoutSeconds": 5, }},
  /* resources */ "resources": {{ "load": ["open77_phone"] }},
}}
""")
shutil.copytree(RES, os.path.join(STAGING, "resources/assets/xyberrp/open77_phone"),
                ignore=shutil.ignore_patterns("tests", "*.md"))
cfgp = os.path.join(STAGING, "resources/assets/xyberrp/open77_phone/server/config.lua")
write(cfgp, open(cfgp).read().replace('wsUrl = ""', 'wsUrl = "wss://xyberrp.duckdns.org/open77-phone-staging/ws"'))
write(os.path.join(ROOT, "opt/open77-xbuniverse/server.jsonc"),
      f'{{ "httpHandlers": {{ "enabled": true, "listenUrl": "http://127.0.0.1:{LP2}" }} }}')

ENV = dict(os.environ, O77ROOT=ROOT, O77_NGINX=os.path.join(T, "bin", "nginx-wrap"),
           O77_SYSTEMCTL=os.path.join(T, "bin", "systemctl"), O77_CAFILE=os.path.join(T, "cert.pem"))


def phone(mode, *args, env=None):
    w = tempfile.mkdtemp(prefix="pw-", dir=T)
    p = subprocess.run([sys.executable, SCRIPT, mode, w, "--https-port", "8443", *args], env=dict(ENV, **(env or {})),
                       capture_output=True, text=True, timeout=300)
    out = p.stdout + p.stderr
    m = re.search(r"PHONE result: (\{.*\})", out)
    return p.returncode, out, json.loads(m.group(1)) if m else {}


def site():
    return open(os.path.join(ROOT, "etc/nginx/sites-enabled/redsync-cdn")).read()


try:
    print("== look (READ ONLY)")
    before = site()
    code, out, res = phone("look", "--roles", "staging,production")
    check("look: exit 0; the site's block for xyberrp.duckdns.org found (not the old host's), no phone lines yet",
          code == 0 and "the site: /etc/nginx/sites-enabled/redsync-cdn" in out and "lines 16-27" in out
          and "the phone's locations already there: none" in out, out)
    check("look: websockify and VNC (greeting, None, 1280x720)", f"listening for the web server at 127.0.0.1:{WS_PORT}" in out
          and "RFB 003.008, security None" in out and "the screen is 1280x720" in out, out)
    check(f"look: staging's listener on, open77_phone {RES_VERSION} installed and answering, configured; production's not installed",
          f"httpHandlers on at http://127.0.0.1:{LP}" in out and f"open77_phone {RES_VERSION} installed in /opt/open77-xbuniverse-staging/resources/assets/xyberrp/open77_phone" in out
          and "answering: ve2e, configured" in out and "production (/opt/open77-xbuniverse): httpHandlers on" in out
          and "open77_phone not installed" in out, out)
    check("look: the routes are not there yet (404) -- and nothing changed", "/open77-phone-staging/ws now: HTTP 404" in out
          and site() == before, out)

    print("== fix staging and production: production is refused (open77_phone does not answer there)")
    code, out, res = phone("fix", "--roles", "staging,production", "--ts", "20261107-130000")
    s = site()
    ours = [ln for ln in s.split("\n") if "open77-phone" in ln or "# open77_phone" in ln]
    check("fix: the three lines added inside xyberrp.duckdns.org's block, after its last TV location", len(ours) == 3
          and s.index("location /tv-browser-xb/") < s.index("# open77_phone staging:") < s.index("location /healthz")
          and s.index("server_name xyberrp.duckdns.org") < s.index("# open77_phone staging:"), s)
    check("fix: kept first; nginx -t accepted it; reloaded; the sites-available twin is the same file again",
          os.path.isfile(os.path.join(ROOT, "opt/open77-backups/phone-nginx-20261107-130000/redsync-cdn"))
          and "nginx -t accepted it, nginx reloaded" in out
          and open(os.path.join(ROOT, "etc/nginx/sites-available/redsync-cdn")).read() == s, out)
    check("fix: the whole way checked -- no ticket 403; open77_phone's ticket: 101 and VNC's greeting; again 403",
          "with no ticket: HTTP 403 (the route is there and open77_phone said no: right)" in out
          and "HTTP 101, and VNC's own greeting through it: 'RFB 003.008'" in out
          and "the same ticket again: HTTP 403 (once only: right)" in out, out)
    check("fix: production refused by name, staging working; result partly, exit 1", res.get("result") == "partly"
          and res["roles"].get("staging") == "working" and res["roles"].get("production") == "open77_phone not answering"
          and code == 1 and "install it on that server first" in out, (code, res, out))
    check("the game server's log of it: let through (this machine's selftest), refused (ticket_used)",
          any("let through (this machine's selftest" in n for n in NOTES) and any("refused (ticket_used" in n for n in NOTES), NOTES)
    check("no ticket in phone_server.py's output", not re.search(r"t=[0-9a-f]{32}", out) and not re.search(r"\b[0-9a-f]{32}\b", out), out)

    code, out, res = phone("fix", "--roles", "staging", "--ts", "20261107-130500")
    check("fix again: already there, nothing added, still working (exit 0)", code == 0 and "already in the site" in out
          and len([ln for ln in site().split("\n") if "open77-phone" in ln or "# open77_phone" in ln]) == 3
          and res.get("result") == "fixed", out)
    code, out, res = phone("verify", "--roles", "staging")
    check("verify: working (exit 0)", code == 0 and res.get("result") == "working", out)

    print("== tv_kit.py phone / phone-verify, as the kit runs them (the SSH hop stood in for)")
    sys.path.insert(0, KIT)
    import argparse
    try:
        import tv_kit
        import av_kit
    except ImportError as e:            # the repository carries the server-side scripts, not the operator's runner
        tv_kit = None
        print(f"  skip the kit's own runner is not here ({e})")

    class Host:
        label = "rl31 (rehearsal)"

        def workdir(self):
            return tempfile.mkdtemp(prefix="kitw-", dir=T)

        def put(self, local, remote, mode=0o644):
            shutil.copy(local, remote)

        def run(self, cmd, timeout=60, tries=1, on_line=None):
            p_ = subprocess.run(["bash", "-c", cmd], env=dict(ENV, O77_HTTPS_PORT="8443"), capture_output=True, text=True,
                                timeout=timeout)
            if on_line:
                on_line(p_.stdout + p_.stderr)
            return p_.returncode, p_.stdout

    klog_path = os.path.join(T, "tvkit-phone.log")
    klog = av_kit.Log(klog_path) if tv_kit is not None else None

    def shown(text):
        for line in text.splitlines():
            line = line.rstrip()
            if line:
                body = line[len("PHONE "):] if line.startswith("PHONE ") else line
                klog("  " + body[:600])
    if tv_kit is not None:
        for cmdname in ("phone", "phone-verify"):
            a = argparse.Namespace(command=cmdname, roles="staging", public_host="")
            kc = tv_kit.run_phone(Host(), a, klog, shown)
            check(f"kit {cmdname}: exit 0", kc == 0, open(klog_path).read()[-1500:])
        kl = open(klog_path).read()
        check("kit: the log has the look and the end-to-end check, with no ticket", "the site: /etc/nginx/sites-enabled/redsync-cdn" in kl
              and "VNC's own greeting through it" in kl and not re.search(r"\b[0-9a-f]{32}\b", kl), kl[-2000:])

    if tv_kit is not None and os.path.isdir(os.path.join(KIT, "fivem2open77")):
        print("== the folder the user unpacks (make_phone_kit.py), through a real SSH server and the Studio's own SSH code")
        import make_phone_kit
        DL = os.path.join(T, "Downloads")
        PK = make_phone_kit.build(os.path.join(DL, "open77-phone"), RES, ["staging"])
        AVK = os.path.join(DL, "open77-av-kit")
        shutil.copytree(KIT, AVK, ignore=shutil.ignore_patterns("tests", ".git", "__pycache__", "bats", "phone_server.py"))
        # the user's AV kit as it is today: its tv_kit.py knows nothing of the phone
        old_tv_kit = subprocess.run(["git", "-C", KIT, "show", "7ffe2e8:tv_kit.py"], capture_output=True, text=True).stdout
        if old_tv_kit:
            write(os.path.join(AVK, "tv_kit.py"), old_tv_kit)
        cfg_staged = open(os.path.join(PK, "phone", "staging", "open77_phone", "server", "config.lua")).read()
        check("the folder: the bats, README, its own kit, the resource with Staging's wsUrl and no tests",
              all(os.path.isfile(os.path.join(PK, f)) for f in ("PH1-Phone-on-Staging.bat", "PH1b-Take-the-phone-off-Staging.bat",
                                                                 "PH1c-Phone-web-server-on-Staging.bat",
                                                                 "PH2-Phone-update-on-Staging.bat", "README.txt", "kit/tv_kit.py", "kit/remote/phone_server.py",
                                                                 "kit/remote/tv_sources.py", "phone/staging/open77_phone/open77.lua",
                                                                 "phone/staging/open77_phone/web/vendor/novnc-rfb.js"))
              and 'wsUrl = "wss://xyberrp.duckdns.org/open77-phone-staging/ws"' in cfg_staged
              and not os.path.exists(os.path.join(PK, "phone/staging/open77_phone/tests")), sorted(os.listdir(PK)))
        sshd_dir = os.path.join(T, "sshd")
        os.makedirs(sshd_dir)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", os.path.join(sshd_dir, "host_key")], check=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", os.path.join(sshd_dir, "user_key")], check=True)
        shutil.copy(os.path.join(sshd_dir, "user_key.pub"), os.path.join(sshd_dir, "authorized_keys"))
        os.chmod(os.path.join(sshd_dir, "authorized_keys"), 0o600)
        SSH_PORT = free_port()
        senv = dict(ENV, O77_HTTPS_PORT="8443")
        setenv = " ".join(f"{k}={senv[k]}" for k in ("PATH", "O77ROOT", "O77_NGINX", "O77_SYSTEMCTL", "O77_CAFILE", "O77_HTTPS_PORT"))
        write(os.path.join(sshd_dir, "sshd_config"), f"""Port {SSH_PORT}
ListenAddress 127.0.0.1
HostKey {sshd_dir}/host_key
AuthorizedKeysFile {sshd_dir}/authorized_keys
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
StrictModes no
UsePAM no
PidFile {sshd_dir}/sshd.pid
Subsystem sftp internal-sftp
SetEnv {setenv}
""")
        os.makedirs("/run/sshd", exist_ok=True)
        spawn(["/usr/sbin/sshd", "-D", "-e", "-f", os.path.join(sshd_dir, "sshd_config")])
        wait_port(SSH_PORT)
        ws = os.path.join(T, "studio")
        os.makedirs(ws)
        json.dump({"servers": [{"id": "xyberrp-staging", "kind": "ssh", "host": "127.0.0.1", "port": SSH_PORT, "user": "root",
                                "auth": "key", "key_file": os.path.join(sshd_dir, "user_key"), "servers_root": "/opt",
                                "names": ["open77-xbuniverse-staging"], "systemctl": "systemctl"}]},
                  open(os.path.join(ws, "settings.json"), "w"))

        def pkit(*args, named=True):
            # -I: the Studio's Python is an embedded build (a ._pth file): it ignores PYTHONPATH and does not put the
            # script's folder on sys.path -- isolated mode behaves the same way (2026-11-07: PYTHONPATH alone failed there)
            p_ = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(PK, "kit", "tv_kit.py")] + list(args) +
                                ["--host", f"root@127.0.0.1:{SSH_PORT}", "--key-from", "xyberrp-staging", "--root", "/opt",
                                 "--systemctl", "systemctl", "--workspace", ws],
                                capture_output=True, text=True, timeout=900,
                                env=dict(os.environ, PYTHONPATH=AVK, **({"O77_AV_KIT": AVK} if named else {})))
            logs = sorted(n for n in os.listdir(os.path.join(PK, "kit")) if n.startswith(f"tvkit-{args[0]}-"))
            return p_.returncode, p_.stdout + p_.stderr, (open(os.path.join(PK, "kit", logs[-1]), encoding="utf-8").read()
                                                          if logs else p_.stdout + p_.stderr)
        code, kout, klog2 = pkit("phone", "--roles", "staging")
        check("PH1's look, from the unpacked folder (its own tv_kit.py; the AV kit named in O77_AV_KIT, in an embedded-like "
              "Python that ignores PYTHONPATH): exit 0, the look in kit\\tvkit-phone-*.log",
              code == 0 and "the site: /etc/nginx/sites-enabled/redsync-cdn" in klog2
              and "RFB 003.008, security None" in klog2, (code, kout[-1500:], klog2[-2500:]))
        code, kout, klog2b = pkit("phone", "--roles", "staging", named=False)
        check("... and with nothing named: the kit finds the AV kit next to its own folder by itself", code == 0
              and "the site: /etc/nginx/sites-enabled/redsync-cdn" in klog2b, (code, kout[-1500:]))
        code, kout, klog3 = pkit("phone-verify", "--roles", "staging")
        check("phone-verify from the unpacked folder: exit 0, the whole way", code == 0 and "VNC's own greeting through it" in klog3,
              (code, klog3[-2000:]))
        code, kout, klog4 = pkit("sources")
        check("sources from the unpacked folder: exit 0, copied back into kit\\fetched\\tvsources-<time>",
              code == 0 and glob.glob(os.path.join(PK, "kit", "fetched", "tvsources-*", "MANIFEST.json")), (code, klog4[-1500:]))
        check("no ticket in any of those logs", not re.search(r"\b[0-9a-f]{32}\b", klog2 + klog3 + klog4))

    print("== the phone page, in a real Chromium, through all of it")
    from playwright.sync_api import sync_playwright
    STUB = """
      window.__emits = []; window.__handlers = {};
      window.Open77 = { on: (e, f) => { (window.__handlers[e] = window.__handlers[e] || []).push(f); },
        emit: (e, p) => { window.__emits.push({ e, p: JSON.parse(JSON.stringify(p || {})), t: Date.now() }); },
        ready: () => { window.__readyCalled = true; },
        cursor: { useCss: () => { window.__cursor = "css"; return Promise.resolve({ style: "css", drawn: "arrow" }); } } };
      window.__fire = (e, p) => (window.__handlers[e] || []).forEach((f) => f(p));
    """
    PREFS = {"size": "standard", "side": "right", "dim": True, "stats": True}
    WS = "wss://xyberrp.duckdns.org:8443/open77-phone-staging/ws"

    def ticket(player=1, ip="127.0.0.1:5000"):
        a = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{HP}/__test/request?player={player}&ip={urllib.parse.quote(ip)}").read())
        return a["detail"]["ticket"]

    def shot(page, name):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.screenshot(path=os.path.join(SHOTS, name + ".png"))

    with sync_playwright() as pw:
        # The game's host name mapped to this machine, the certificate made here accepted, and no proxy in between
        # (this sandbox's own proxy would take the WebSocket elsewhere).
        b = pw.chromium.launch(args=["--host-resolver-rules=MAP xyberrp.duckdns.org 127.0.0.1", "--ignore-certificate-errors",
                                     "--proxy-server=direct://", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1, ignore_https_errors=True)
        ctx.add_init_script(STUB)
        page = ctx.new_page()
        console = []
        page.on("console", lambda m: console.append(m.text))
        page.goto(f"http://127.0.0.1:{HP}/web/phone.html")
        page.wait_for_function("window.__emits.some(x => x.e === 'phone:ready')", timeout=10000)
        emits = lambda: page.evaluate("window.__emits")   # noqa: E731
        fire = lambda e, p: page.evaluate("([e, p]) => window.__fire(e, p)", [e, p])   # noqa: E731
        check("the page loads noVNC and says it is ready", any(x["e"] == "phone:log" and "noVNC 1.6.0" in x["p"].get("text", "")
                                                               for x in emits()), emits())
        check("closed: nothing is drawn", page.evaluate("getComputedStyle(document.getElementById('dock')).visibility") == "hidden")
        page.wait_for_timeout(200)
        check("the game's cursor is told to follow the page's CSS (a hand on the buttons)", page.evaluate("window.__cursor") == "css"
              and any("cursor follows the page (css)" in x["p"].get("text", "") for x in emits() if x["e"] == "phone:log"))
        fire("phone:state", {"open": True, "phase": "asking", "key": "F1", "keyMode": "mapping", "prefs": PREFS, "label": "Android"})
        page.wait_for_timeout(600)
        check("opened: the phone is drawn, asking", page.evaluate("getComputedStyle(document.getElementById('dock')).visibility") == "visible"
              and page.inner_text("#cardTitle") == "Picking up the phone")
        shot(page, "1-asking")
        t1 = ticket()
        fire("phone:connect", {"url": WS, "ticket": t1, "label": "Android"})
        page.wait_for_function("window.__emits.some(x => x.e === 'phone:report')", timeout=20000)
        rep = [x["p"] for x in emits() if x["e"] == "phone:report"]
        def why():
            tail = lambda f: open(os.path.join(T, f)).read()[-1500:] if os.path.exists(os.path.join(T, f)) else ""   # noqa: E731
            return {"reports": rep, "console": console[-12:], "notes": NOTES[-6:], "nginx-error": tail("nginx-error.log"),
                    "nginx-access": tail("nginx-access.log")}
        check("the screen is live through nginx's ticket check: 1280x720", rep and rep[0].get("status") == "live"
              and rep[0].get("detail", "").startswith("1280x720"), why())
        page.wait_for_timeout(1500)
        box = page.evaluate("(() => { const c = document.querySelector('#vnc canvas'); const r = c.getBoundingClientRect(); "
                            "return {x: r.x, y: r.y, w: r.width, h: r.height, cw: c.width, ch: c.height}; })()")
        check("the canvas holds Android's whole screen, scaled into the phone", box["cw"] == 1280 and box["ch"] == 720
              and 990 < box["w"] < 1010, box)
        shot(page, "2-live")
        px = page.evaluate("(() => { const c = document.querySelector('#vnc canvas'); const d = c.getContext('2d')"
                           ".getImageData(640, 600, 1, 1).data; return [d[0], d[1], d[2]]; })()")
        check("... and it is Android's picture (its green, through VNC)", abs(px[0] - 0x2e) < 12 and abs(px[1] - 0x7d) < 12
              and abs(px[2] - 0x32) < 12, px)
        k = box["w"] / 1280
        page.mouse.click(box["x"] + 250 * k, box["y"] + 180 * k)
        page.wait_for_timeout(1200)
        check("a click on the phone taps Android's button, where it is", "tap" in ANDROID["ev"], ANDROID)
        page.keyboard.type("hello", delay=60)
        page.wait_for_timeout(1500)
        check("typing reaches Android's text field", ANDROID["value"] == "hello", ANDROID)
        page.evaluate("window.__sent = []; const r = window.NoVNC; ")
        page.click("#navBack")
        page.wait_for_timeout(300)
        check("Back is sent to Android as a key (and logged)", any(x["e"] == "phone:log" and "sent back" in x["p"].get("text", "")
                                                                 for x in emits()), [x for x in emits() if x["e"] == "phone:log"][-5:])
        page.wait_for_timeout(5500)
        st = [x["p"] for x in emits() if x["e"] == "phone:stats"]
        check("every five seconds: the numbers (size, frame rate, data rate, idle time)", st and st[-1]["w"] == 1280
              and st[-1]["kbps"] >= 0 and "idleMs" in st[-1], st[-1:] if st else st)
        check("the numbers under the phone (settings: shown)", "1280×720" in page.inner_text("#stats"), page.inner_text("#stats"))
        closes = lambda: [x["p"] for x in emits() if x["e"] == "phone:close"]   # noqa: E731
        n0 = len(closes())
        page.keyboard.down("F1")
        page.wait_for_timeout(250)
        held = len(closes())
        page.keyboard.up("F1")
        page.wait_for_timeout(200)
        cl = closes()
        # Staging, 2026-11-07: put away on the PRESS, the game had the keyboard back while F1 was still down and its
        # own binding opened the phone again 20 ms later.  On the release, there is no press left for the game to see.
        check("the phone key puts it away when it is LET GO, not while it is held (Lua is asked once)",
              held == n0 and len(cl) == n0 + 1 and cl[-1]["why"] == "key", (held, n0, cl))
        check("... and neither its press nor its release reached Android", "k:F1" not in ANDROID["ev"], ANDROID["ev"])
        fire("phone:state", {"open": False, "phase": "closed", "key": "F1", "prefs": PREFS})
        page.wait_for_timeout(700)
        check("put away: the screen closed, nothing drawn", page.evaluate("document.querySelector('#vnc canvas')") is None
              and page.evaluate("getComputedStyle(document.getElementById('dock')).visibility") == "hidden")
        urllib.request.urlopen(f"http://127.0.0.1:{HP}/__test/release?player=1").read()

        print("== refused tickets, busy, settings")
        fire("phone:state", {"open": True, "phase": "asking", "key": "F1", "prefs": PREFS})
        page.wait_for_timeout(500)
        # the game's binding opened it on the press; the page gets only the release -- which must not close it
        n1 = len(closes())
        page.keyboard.up("F1")
        page.wait_for_timeout(200)
        check("the release of the press that OPENED the phone (the page never saw that press) does not close it",
              len(closes()) == n1 and page.evaluate("getComputedStyle(document.getElementById('dock')).visibility") == "visible")
        fire("phone:connect", {"url": WS, "ticket": t1, "label": "Android"})     # spent
        page.wait_for_function("window.__emits.filter(x => x.e === 'phone:report' && x.p.status === 'failed').length >= 1", timeout=15000)
        page.wait_for_function("window.__emits.some(x => x.e === 'phone:again')", timeout=8000)
        check("a spent ticket: refused by the web server; the page asks for a new one by itself",
              any("ticket_used" in n for n in NOTES), NOTES[-5:])
        t_other = ticket(player=1, ip="10.9.9.9:4000")
        fire("phone:connect", {"url": WS, "ticket": t_other, "label": "Android"})
        page.wait_for_timeout(2500)
        check("a ticket made for another address: refused (other_address)", any("other_address" in n for n in NOTES), NOTES[-5:])
        n2 = len(closes())
        page.keyboard.down("Escape")
        page.wait_for_timeout(200)
        held = len(closes())
        page.keyboard.up("Escape")
        page.wait_for_timeout(200)
        check("Escape puts it away, on its release too (the game never sees an Escape to open its own menu with)",
              held == n2 and len(closes()) == n2 + 1 and closes()[-1]["why"] == "escape", (held, n2, closes()[-2:]))
        fire("phone:busy", {"holder": "V", "heldMs": 185000, "label": "Android"})
        page.wait_for_timeout(300)
        check("busy: who has it and for how long", "V has the phone (3 min)" in page.inner_text("#cardText"), page.inner_text("#cardText"))
        shot(page, "3-busy")
        page.click("#openSettings")
        page.wait_for_timeout(500)
        shot(page, "4-settings")
        page.click("#keyChange")
        page.keyboard.press("g")
        page.wait_for_timeout(200)
        rb = [x["p"] for x in emits() if x["e"] == "phone:rebind"]
        check("settings: CHANGE takes the next key, in the game registry's spelling", rb and rb[-1]["key"] == "G", rb)
        fire("phone:keyResult", {"ok": True, "key": "G", "text": "the phone opens with G now"})
        page.wait_for_timeout(200)
        check("... the answer is shown, the key cap and the hint say G", page.inner_text("#keyFeedback") == "the phone opens with G now"
              and page.inner_text("#keyCap") == "G" and page.inner_text("#hintKey") == "G")
        page.click("#keyChange")
        page.keyboard.press("F7")
        rb = [x["p"] for x in emits() if x["e"] == "phone:rebind"]
        check("... F7 is F7, Escape cancels the chooser", rb[-1]["key"] == "F7")
        page.click("#segSize button[data-v='large']")
        page.wait_for_timeout(400)
        pr = [x["p"] for x in emits() if x["e"] == "phone:prefs"]
        w_large = page.evaluate("document.getElementById('screen').getBoundingClientRect().width")
        check("size Large: saved through Lua, and the phone grows", pr and pr[-1]["size"] == "large" and w_large > 1150, (pr[-1:], w_large))
        page.click("#segSide button[data-v='center']")
        page.wait_for_timeout(500)
        r = page.evaluate("document.getElementById('device').getBoundingClientRect().x + document.getElementById('device').getBoundingClientRect().width / 2")
        check("position Center: the phone in the middle", abs(r - 960) < 3, r)
        page.click("#segPointer button[data-v='android']")
        page.wait_for_timeout(200)
        pr = [x["p"] for x in emits() if x["e"] == "phone:prefs"]
        check("pointer on the screen: Android's -- saved, and the game's arrow hidden over the screen", pr[-1]["pointer"] == "android"
              and page.evaluate("document.body.classList.contains('pointer-android') && getComputedStyle(document.getElementById('vnc')).cursor") == "none")
        page.click("#segPointer button[data-v='game']")
        page.click("#settingsClose")
        fire("phone:error", {"reason": "not_configured", "label": "Android"})
        page.wait_for_timeout(300)
        check("not set up on this server: said plainly", page.inner_text("#cardTitle") == "Not set up here")
        shot(page, "5-not-set-up")
        fire("phone:state", {"open": False, "phase": "closed", "key": "G", "prefs": PREFS})
        page.wait_for_timeout(500)
        fire("phone:free", {"key": "G", "label": "Android"})
        page.wait_for_timeout(500)
        check("told it is free while away: a note in the corner with the key", "Press G to pick it up" in page.inner_text("#toast")
              and page.evaluate("document.getElementById('toast').classList.contains('show')"))
        shot(page, "6-free-toast")
        fire("phone:state", {"open": True, "phase": "asking", "key": "G", "prefs": dict(PREFS, size="standard", side="right")})
        page.wait_for_timeout(500)
        t2 = ticket()
        fire("phone:connect", {"url": WS, "ticket": t2, "label": "Android"})
        page.wait_for_function("window.__emits.filter(x => x.e === 'phone:report' && x.p.status === 'live').length >= 2", timeout=20000)
        page.wait_for_timeout(1500)
        shot(page, "7-live-again")
        logs = " ".join(x["p"].get("text", "") for x in emits() if x["e"] == "phone:log")
        # Chromium's own console line for a WebSocket that failed names its address (a spent or refused ticket); the
        # page's own words never hold one.
        own = [c for c in console if not c.startswith("WebSocket connection to ")]
        check("no ticket in anything the page logged", not re.search(r"[0-9a-f]{32}", logs)
              and not any(re.search(r"[0-9a-f]{32}", c) for c in own), (logs[-600:], own[-5:]))
        b.close()

    print("== the put-backs: nginx refusing, the whole way failing, putback")
    write(os.path.join(T, "fail-t"), "1")
    code, out, res = phone("putback", "--roles", "staging", "--ts", "20261107-131000")
    check("putback while nginx -t refuses: nothing changed", res.get("result") == "not changed"
          and len([ln for ln in site().split("\n") if "open77-phone" in ln or "# open77_phone" in ln]) == 3, out)
    os.remove(os.path.join(T, "fail-t"))
    code, out, res = phone("putback", "--roles", "staging", "--ts", "20261107-131500")
    check("putback: the lines taken out, kept first; the route answers 404; the twin follows", code == 0
          and "open77-phone" not in site() and "now answers HTTP 404 (gone: right)" in out
          and open(os.path.join(ROOT, "etc/nginx/sites-available/redsync-cdn")).read() == site(), out)
    check("... the site is byte for byte what it was before the phone", site() == before)
    write(os.path.join(T, "fail-t"), "1")
    code, out, res = phone("fix", "--roles", "staging", "--ts", "20261107-132000")
    check("fix while nginx -t refuses: the kept site is back, exit 1", code == 1 and res.get("result") == "put back"
          and site() == before and "nginx refused the new site" in out, out)
    os.remove(os.path.join(T, "fail-t"))
    subprocess.run(["pkill", "-f", f"websockify 127.0.0.1:{WS_PORT}"], capture_output=True)
    time.sleep(0.5)
    code, out, res = phone("fix", "--roles", "staging", "--ts", "20261107-132500")
    check("fix with Android's websockify down: the check passes, the screen does not; put back, exit 1",
          code == 1 and res.get("result") == "put back" and site() == before and "NOT working" in json.dumps(res), out)
    print("== a game server still starting (PH1 on 2026-11-07: phone-fix ran 7 s after Staging's restart)")
    LISTENER_UP.update(on=False, hang=9)
    t0 = time.time()
    code, out, res = phone("fix", "--roles", "staging", "--ts", "20261107-133000", env={"O77_WAIT_HEALTHY": "6"})
    took = time.time() - t0
    check("installed but never answering: it waits for the server (6 s here), then refuses before anything changes",
          code == 1 and site() == before and "waiting for the server to finish starting (up to 6 s)" in out
          and "does not answer" in out and "is that server running?" in out and 6 <= took < 25, (took, out))
    spawn(["websockify", f"127.0.0.1:{WS_PORT}", f"127.0.0.1:{VNC_PORT}"])
    check("(Android's websockify back up)", wait_port(WS_PORT))
    LISTENER_UP.update(on=False, hang=9)
    threading.Timer(6, lambda: LISTENER_UP.update(on=True)).start()
    t0 = time.time()
    code, out, res = phone("fix", "--roles", "staging", "--ts", "20261107-133500", env={"O77_WAIT_HEALTHY": "60"})
    took = time.time() - t0
    for ln in out.splitlines():
        if "staging:" in ln and ("wait" in ln or "answers now" in ln or "TimeoutError" in ln):
            print("       | " + ln.strip())
    check("its listener answering only 6 s in (the first ask timed out): fix waits, then adds the routes, the whole way "
          "works, exit 0", code == 0 and res.get("result") == "fixed" and res["roles"].get("staging") == "working"
          and "TimeoutError" in out and "open77_phone answers now, after" in out
          and "HTTP 101, and VNC's own greeting through it" in out and 6 <= took < 45, (took, out))
    code, out, res = phone("putback", "--roles", "staging", "--ts", "20261107-134000")
    check("... put back again: the site byte for byte what it was", code == 0 and site() == before, out)
finally:
    cleanup()

# =====================================================================================================================
def run_bat(name, rcs, choice="Y", exists=None, dp0="C:\\Users\\x\\Downloads\\open77-phone\\", files=None):
    """A bat line by line as cmd.exe runs it (the lines these bats use); the kit's runs answered with `rcs`. `files`: the
    Windows paths that exist (the user's layout); `exists` overrides it."""
    import ntpath
    if files is None:
        files = {"C:\\Users\\x\\AppData\\Local\\Programs\\fivem2open77\\runtime\\python\\python.exe",
                 "C:\\Users\\x\\Downloads\\open77-av-kit\\av_kit.py"}
        files |= {ntpath.join(dp0, f) for f in ("kit\\tv_kit.py", "phone\\staging\\open77_phone\\open77.lua")}
    norm = {ntpath.normcase(ntpath.normpath(f)) for f in files}
    exists = exists or (lambda p_: ntpath.normcase(ntpath.normpath(p_)) in norm)
    raw = open(os.path.join(KIT, "bats", name), "rb").read()
    lines = raw.decode("ascii").split("\r\n")
    labels = {ln[1:].strip(): i for i, ln in enumerate(lines) if ln.startswith(":")}
    var = {"LOCALAPPDATA": "C:\\Users\\x\\AppData\\Local", "USERPROFILE": "C:\\Users\\x"}
    out, i, ran, rcs = [], 0, [], list(rcs)
    errorlevel = 0

    def expand(t):
        t = t.replace("%~dp0", dp0)
        return re.sub(r"%([A-Za-z_0-9]+)%", lambda m: str(errorlevel) if m.group(1) == "ERRORLEVEL" else var.get(m.group(1), ""), t)

    def setvar(text):
        m_ = re.match(r'^set "([A-Za-z_0-9]+)=(.*)"$', text)
        if not m_:
            raise SystemExit(f"{name}: not a set: {text!r}")
        if m_.group(2) == "":
            var.pop(m_.group(1), None)
        else:
            var[m_.group(1)] = expand(m_.group(2))
    while i < len(lines):
        ln = lines[i].strip()
        i += 1
        low = ln.lower()
        if not ln or ln.startswith(":") or low.startswith(("@echo", "setlocal", "title ", "rem ", "pause")) or low == "rem":
            continue
        if low == "echo." or low.startswith("echo "):
            out.append(expand(ln[5:]))
            continue
        if re.match(r'^set "', ln):
            setvar(ln)
            continue
        m = re.match(r'^if not defined (\w+) (.*)$', ln)
        if m:
            if m.group(1) in var:
                continue
            ln = m.group(2)
            m2 = re.match(r'^goto (\w+)$', ln)
            if m2:
                i = labels[m2.group(1)] + 1
                continue
        m = re.match(r'^if exist "(.+?)" (set ".*")$', ln)
        if m:
            if exists(expand(m.group(1))):
                setvar(m.group(2))
            continue
        m = re.match(r'^if not exist "(.+)" goto (\w+)$', ln)
        if m:
            if not exists(expand(m.group(1))):
                i = labels[m.group(2)] + 1
            continue
        if ln.startswith('"%PY%"'):
            ran.append(expand(ln))
            errorlevel = rcs.pop(0) if rcs else 0
            continue
        m = re.match(r'^choice /C YN /M "(.*)"$', ln)
        if m:
            out.append("[choice] " + m.group(1))
            errorlevel = 1 if choice == "Y" else 2
            continue
        m = re.match(r'^if errorlevel (\d) goto (\w+)$', ln)
        if m:
            if errorlevel >= int(m.group(1)):
                i = labels[m.group(2)] + 1
            continue
        m = re.match(r'^if not "%(\w+)%"=="0" goto (\w+)$', ln)
        if m:
            if var.get(m.group(1)) != "0":
                i = labels[m.group(2)] + 1
            continue
        m = re.match(r'^exit /b (\S+)$', ln)
        if m:
            return int(expand(m.group(1))), "\n".join(out), ran
        raise SystemExit(f"{name}: a line this rehearsal does not know: {ln!r}")
    raise SystemExit(f"{name} ran off its end")


def bats_checks():
    print("== PH1, PH1b, PH1c and PH2, as cmd.exe runs them")
    for bat in ("PH1-Phone-on-Staging.bat", "PH1b-Take-the-phone-off-Staging.bat", "PH1c-Phone-web-server-on-Staging.bat",
                "PH2-Phone-update-on-Staging.bat"):
        raw = open(os.path.join(KIT, "bats", bat), "rb").read()
        check(f"{bat}: CRLF throughout, ASCII, no redirection characters",
              b"\n" not in raw.replace(b"\r\n", b"") and all(b < 128 for b in raw) and b"<" not in raw and b">" not in raw)
    H = "--host root@192.34.102.143 --key-from xyberrp-staging --root /opt --systemctl systemctl"
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [0, 0, 0, 0, 0], "Y")
    args = [r.split('.py" ', 1)[1] for r in ran]
    check("PH1, Y: look, sources, open77_phone installed on Staging, the routes, the look again; exit 0; says what to do",
          code == 0 and args == [f"phone --roles staging {H}", f"sources {H}",
                                 f'resinstall --to open77-xbuniverse-staging --resources "C:\\Users\\x\\Downloads\\open77-phone\\phone\\staging" --names open77_phone {H}',
                                 f"phone-fix --roles staging {H}", f"phone --roles staging {H}"]
          and "press F1" in bout and ran[2].split('"')[3].endswith("open77-av-kit\\av_kit.py"), (code, args, bout))
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [0, 0], "N")
    check("PH1, N: only the look and the sources, nothing installed, exit 0", code == 0 and len(ran) == 2 and "Nothing installed" in bout)
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [1])
    check("PH1, the look failing: no question, nothing installed, exit 1", code == 1 and len(ran) == 1, (code, ran))
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [0, 0, 3])
    check("PH1, somebody on Staging (exit 3): no routes, exit 3, says why", code == 3 and len(ran) == 3 and "somebody is on Staging" in bout,
          (code, ran, bout))
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [0, 0, 0, 1, 0])
    check("PH1, the routes not right: the look still runs, exit 1", code == 1 and len(ran) == 5 and "Done." not in bout, (code, ran))
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [], exists=lambda p_: "phone\\staging" not in p_)
    check("PH1, the phone's files missing: says so, nothing run", code == 1 and not ran and "phone's files are not next to" in bout)
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [], exists=lambda p_: "python.exe" not in p_)
    check("PH1, no Studio Python: says so", code == 1 and not ran and "Studio's Python" in bout)
    # the zip unpacked INSIDE the AV kit (what happened on 2026-11-07): open77-av-kit\open77-phone-kit-...\open77-phone\
    nested = "C:\\Users\\x\\Downloads\\open77-av-kit\\open77-phone-kit-20261107-0548\\open77-phone\\"
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [0, 0, 0, 0, 0], "Y", dp0=nested)
    check("PH1 unpacked inside the AV kit: it finds the AV kit two folders up, and runs as usual",
          code == 0 and len(ran) == 5 and "The AV kit: " + nested + "..\\.." in bout
          and ran[2].split('"')[3] == nested + "..\\..\\av_kit.py", (code, ran, bout))
    elsewhere = "C:\\Users\\x\\Desktop\\stuff\\open77-phone\\"
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [0, 0, 0, 0, 0], "Y", dp0=elsewhere)
    check("PH1 unpacked somewhere else: the AV kit in Downloads is used", code == 0 and len(ran) == 5
          and "The AV kit: C:\\Users\\x\\Downloads\\open77-av-kit" in bout, (code, bout))
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [], files={
        "C:\\Users\\x\\AppData\\Local\\Programs\\fivem2open77\\runtime\\python\\python.exe",
        "C:\\Users\\x\\Downloads\\open77-phone\\kit\\tv_kit.py"})
    check("PH1 with no AV kit anywhere: says where it looked, nothing run", code == 1 and not ran
          and "av_kit.py was not found" in bout, (code, bout))
    code, bout, ran = run_bat("PH1-Phone-on-Staging.bat", [], exists=lambda p_: not p_.endswith("tv_kit.py"))
    check("PH1 with its own kit missing: says so", code == 1 and not ran and "own kit" in bout, bout)
    code, bout, ran = run_bat("PH1c-Phone-web-server-on-Staging.bat", [0, 0])
    args = [r.split('.py" ', 1)[1] for r in ran]
    check("PH1c: only the routes and the look again (nothing installed, no restart, no question); exit 0; says press F1",
          code == 0 and args == [f"phone-fix --roles staging {H}", f"phone --roles staging {H}"] and "press F1" in bout
          and "[choice]" not in bout and all('kit\\tv_kit.py"' in r for r in ran), (code, args, bout))
    code, bout, ran = run_bat("PH1c-Phone-web-server-on-Staging.bat", [1, 0])
    check("PH1c, the routes not right: the look still runs, exit 1", code == 1 and len(ran) == 2 and "Done." not in bout
          and "Not everything finished" in bout, (code, ran, bout))
    code, bout, ran = run_bat("PH1c-Phone-web-server-on-Staging.bat", [0, 0], dp0=nested)
    check("PH1c unpacked inside the AV kit: finds it two folders up", code == 0 and len(ran) == 2
          and "The AV kit: " + nested + "..\\.." in bout, (code, bout))
    code, bout, ran = run_bat("PH1c-Phone-web-server-on-Staging.bat", [], files={
        "C:\\Users\\x\\AppData\\Local\\Programs\\fivem2open77\\runtime\\python\\python.exe",
        "C:\\Users\\x\\Downloads\\open77-phone\\kit\\tv_kit.py"})
    check("PH1c with no AV kit anywhere: says so, nothing run", code == 1 and not ran and "av_kit.py was not found" in bout)
    code, bout, ran = run_bat("PH2-Phone-update-on-Staging.bat", [0, 0, 0], "Y")
    args = [r.split('.py" ', 1)[1] for r in ran]
    check("PH2, Y: open77_phone installed on Staging (restart), the whole way checked, the look; exit 0; says press F1",
          code == 0 and args == [f'resinstall --to open77-xbuniverse-staging --resources "C:\\Users\\x\\Downloads\\open77-phone\\phone\\staging" --names open77_phone {H}',
                                 f"phone-fix --roles staging {H}", f"phone --roles staging {H}"]
          and "press F1" in bout and "Leave Staging first" in bout, (code, args, bout))
    code, bout, ran = run_bat("PH2-Phone-update-on-Staging.bat", [], "N")
    check("PH2, N: nothing run, exit 0", code == 0 and not ran and "Nothing installed" in bout)
    code, bout, ran = run_bat("PH2-Phone-update-on-Staging.bat", [3], "Y")
    check("PH2, somebody on Staging (exit 3, you too): nothing else run, says to leave and run it again", code == 3
          and len(ran) == 1 and "leave it and run this again" in bout, (code, ran, bout))
    code, bout, ran = run_bat("PH2-Phone-update-on-Staging.bat", [0, 1, 0], "Y")
    check("PH2, the check not right: the look still runs, exit 1", code == 1 and len(ran) == 3
          and "Not everything finished" in bout, (code, ran, bout))
    code, bout, ran = run_bat("PH2-Phone-update-on-Staging.bat", [0, 0, 0], "Y", dp0=nested)
    check("PH2 unpacked inside the AV kit: finds it two folders up", code == 0 and len(ran) == 3
          and "The AV kit: " + nested + "..\\.." in bout, (code, bout))
    code, bout, ran = run_bat("PH1b-Take-the-phone-off-Staging.bat", [0, 0], "Y")
    args = [r.split('.py" ', 1)[1] for r in ran]
    check("PH1b, Y: the routes taken out, open77_phone undone on Staging; exit 0",
          code == 0 and args == [f"phone-putback --roles staging {H}", f"resundo --on open77-xbuniverse-staging --names open77_phone {H}"], args)
    code, bout, ran = run_bat("PH1b-Take-the-phone-off-Staging.bat", [1, 0], "Y")
    check("PH1b, the routes not taken out: the resource is still undone, exit 1", code == 1 and len(ran) == 2)
    code, bout, ran = run_bat("PH1b-Take-the-phone-off-Staging.bat", [], "N")
    check("PH1b, N: nothing run", code == 0 and not ran)


if os.path.isfile(os.path.join(KIT, "bats", "PH1-Phone-on-Staging.bat")):
    bats_checks()
else:
    print("== PH1, PH1b, PH1c and PH2: skip (the operator's bats are not here)")

print(f"\n{'all checks passed' if fails == 0 else str(fails) + ' check(s) failed'}  (work: {T})")
sys.exit(1 if fails else 0)
