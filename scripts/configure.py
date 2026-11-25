"""Create private, local installation configuration; never ship user state."""
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def allowed_host(host):
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10") or address in ipaddress.ip_network("fd7a:115c:a1e0::/48")
    except ValueError:
        return False


def configuration(config, mode):
    values = {}
    if config.exists():
        for line in config.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            parts = shlex.split(value, comments=True)
            values[key.strip()] = parts[0] if len(parts) == 1 else ""
    host = values.get("UNIFI_WEB_HOST", "")
    if mode == "--localhost":
        host = "127.0.0.1"
    elif mode == "--tailscale" or not host:
        try:
            result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=10, check=True)
            host = result.stdout.strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            if mode == "--tailscale":
                raise SystemExit("No Tailscale IPv4 address found. Install/connect Tailscale first, or use --localhost.")
            host = "127.0.0.1"
    if not allowed_host(host):
        raise SystemExit("Refusing to bind outside localhost/Tailscale. Use --localhost or --tailscale.")
    port = int(values.get("UNIFI_WEB_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise SystemExit("Invalid web port")
    return host, port


def main():
    config, data = Path(sys.argv[1]), Path(sys.argv[2])
    host, port = configuration(config, sys.argv[3])
    if "--check" not in sys.argv:
        config.write_text(f"UNIFI_WEB_HOST={host}\nUNIFI_WEB_PORT={port}\n")
        config.chmod(0o600)
        defaults = {"poll_interval": 120, "region": "us", "max_retries": 3,
                    "ntfy_url": "https://ntfy.sh", "ntfy_topic": "", "ntfy_priority": "high",
                    "ntfy_enabled": True, "price_drop_alerts": False, "alert_favourites_only": False}
        for name, value in [("settings.json", defaults), ("watched_items.json", []),
                            ("stock_state.json", {}), ("stock_history.json", {"events": [], "stats": {}})]:
            path = data / name
            if not path.exists():
                path.write_text(json.dumps(value, indent=2) + "\n")
                path.chmod(0o640)
    print(f"http://{'[' + host + ']' if ':' in host else host}:{port}/")


if __name__ == "__main__":
    main()
