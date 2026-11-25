#!/usr/bin/env python3
"""Enforce the private-interface restriction even after configuration edits."""
import os
from pathlib import Path
import sys
from configure import allowed_host

host = os.environ.get("UNIFI_WEB_HOST", "127.0.0.1")
port = int(os.environ.get("UNIFI_WEB_PORT", "8765"))
if not allowed_host(host) or not 1 <= port <= 65535:
    raise SystemExit("Web GUI is restricted to localhost or a Tailscale IP.")
bind = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
gunicorn = str(Path(sys.executable).parent / "gunicorn")
os.execv(gunicorn, [gunicorn, "--workers", "2", "--threads", "2", "--timeout", "180",
                    "--access-logfile", "-", "--bind", bind, "unifi_stock_web:app"])
