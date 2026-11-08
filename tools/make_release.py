#!/usr/bin/env python3
"""Builds the release package server owners download: dist/open77-phone-<version>.zip (and its .sha256).

    python3 tools/make_release.py [--out dist]

What is in it (docs/INSTALL.md walks through it):

    open77-phone-<version>/
      INSTALL.md                                    docs/INSTALL.md
      LICENSE                                       GPL-3.0
      SHA256SUMS                                    every other file's checksum
      resources/open77_phone/...                    the resource, without its tests (nothing in the game reads them)
      tools/phone_server.py                         the web server's two locations: look / fix / verify / putback
      nginx/open77-phone-locations.conf.example     the same two locations, made by phone_server.py's own code

The zip is the same bytes every time for the same files: entries sorted, one fixed time on all of them (2026-11-07
00:00), 0644 / 0755 modes.
"""
import hashlib
import importlib.util
import io
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RES = os.path.join(REPO, "open77_phone")
STAMP = (2026, 11, 7, 0, 0, 0)
EXECUTABLE = {"tools/phone_server.py"}


def version():
    text = open(os.path.join(RES, "open77.lua"), encoding="utf-8").read()
    m = re.search(r'^version\s+"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("open77_phone/open77.lua has no version")
    return m.group(1)


def nginx_example():
    """The two locations, written by phone_server.py's own block_lines() for a game server called `myserver`."""
    spec = importlib.util.spec_from_file_location("phone_server", os.path.join(HERE, "phone_server.py"))
    mod = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, ["phone_server.py"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = argv
    mod.ROLES["myserver"] = {"server": "/path/to/your/server", "path": "/open77-phone-myserver"}
    lines = mod.block_lines("myserver", 11781, "127.0.0.1:8001", "release")
    head = [
        "# open77-phone: the web server's two locations for ONE game server, as tools/phone_server.py writes them.",
        "# Paste them inside the server { listen 443 ssl; server_name YOUR.HOST; ... } block your players reach, then",
        "#   myserver         -> a short name for that game server (its route: /open77-phone-myserver)",
        "#   11781            -> the port of that game server's httpHandlers listener (server.jsonc, on 127.0.0.1)",
        "#   127.0.0.1:8001   -> websockify, in front of Android's VNC screen",
        "# and nginx -t && systemctl reload nginx.  The route with no ticket must answer 403 (INSTALL.md, step 6).",
        "# Keep the comment line just above the two locations: it is the mark phone_server.py looks for, so its look,",
        "# verify and putback know these lines (putback takes them out again).",
        "",
    ]
    return "\n".join(head + [ln.strip() for ln in lines]) + "\n"


def files():
    """(path in the package, bytes) for everything but SHA256SUMS."""
    out = [("INSTALL.md", open(os.path.join(REPO, "docs", "INSTALL.md"), "rb").read()),
           ("LICENSE", open(os.path.join(REPO, "LICENSE"), "rb").read()),
           ("tools/phone_server.py", open(os.path.join(HERE, "phone_server.py"), "rb").read()),
           ("nginx/open77-phone-locations.conf.example", nginx_example().encode("utf-8"))]
    for dp, dns, fns in os.walk(RES):
        dns[:] = sorted(d for d in dns if d not in ("tests", "__pycache__", ".git"))
        for fn in sorted(fns):
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, RES).replace(os.sep, "/")
            out.append(("resources/open77_phone/" + rel, open(full, "rb").read()))
    return sorted(out)


def build(out_dir):
    ver = version()
    top = f"open77-phone-{ver}"
    entries = files()
    sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in entries).encode()
    entries.append(("SHA256SUMS", sums))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(f"{top}/{name}", date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o100755 if name in EXECUTABLE else 0o100644) << 16)
            info.create_system = 3                     # unix: the modes above mean what they say
            z.writestr(info, data)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{top}.zip")
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    digest = hashlib.sha256(buf.getvalue()).hexdigest()
    with open(path + ".sha256", "w", encoding="ascii", newline="\n") as f:
        f.write(f"{digest}  {top}.zip\n")
    print(f"{path}: {len(entries)} files, {len(buf.getvalue())} bytes, sha256 {digest}")
    return path


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(REPO, "dist")
    build(out)
