# UniFi Stock Watcher — Linux + web GUI

A Linux background service and browser interface for monitoring products in the
Ubiquiti store. Add products, choose a region and check interval, and receive ntfy
notifications when stock returns or prices drop. Includes favorites, muted items,
history/CSV export, service diagnostics, readable logs, and an ntfy phone setup guide.

## Install

Requires Ubuntu or Debian with systemd, Python 3.10+, sudo/root access, and Internet
access for packages, store checks, and public ntfy. Tailscale is optional and must
already be installed and connected if you want remote Tailscale access.

After cloning this repository, run from the clone:

```bash
sudo bash install.sh
```

This installs **both backend and frontend**, dependencies, a dedicated service
account, and systemd services/timer. No additional upstream clone or patch is needed.
The installer prints the URL to open for ntfy setup. Follow the Notifications guide,
subscribe your phone, save and test, then add products on the Watch list. The default
check interval is two minutes. The browser can be closed while monitoring continues.

The installer uses the machine's Tailscale IPv4 address when available, otherwise
localhost. You can explicitly choose:

```bash
sudo bash install.sh --localhost
sudo bash install.sh --tailscale
```

For a headless server in localhost mode, forward port 8765 with SSH and open
`http://localhost:8765/#notifications` on your computer. Run the same installer to
upgrade; it preserves watch data/settings and creates a private backup under `/opt`.
Existing timer overrides are archived so the GUI's interval takes effect. The
installer restarts services and briefly pauses checks. Do not copy live settings
or backup directories into your fork.

## Localhost or Tailscale only, intentionally

There is **no application login**. Both the installer and service launcher reject
public/LAN/wildcard bind addresses. Anyone allowed to reach the interface can control
it; use Tailscale access rules to limit access. This project does not configure
Tailscale, firewall rules, HTTPS, public hosting, or a public reverse proxy. Do not
expose it using Tailscale Funnel or another public proxy. A same-origin browser
check blocks unrelated websites from submitting actions; it is not user authentication.

Basic ntfy.sh public topics need no account. The GUI explains optional accounts,
Android/iPhone subscription, and self-hosted servers. It currently publishes without
ntfy credentials; protected topics requiring publisher authentication are unsupported.

## Files and troubleshooting

- Code: `/opt/unifi-stock-watcher/`; state: `/var/lib/unifi-stock-watcher/`.
- Bind address/port: `/etc/unifi-stock-watcher-web.conf` (no credentials).
- Watcher: `journalctl -u unifi-stock-watcher.service`.
- Web GUI: `journalctl -u unifi-stock-watcher-web.service`.
- Timer: `systemctl list-timers --all unifi-stock-watcher.timer`.

The store's public page format can change, so monitoring is best-effort. This
Linux fork supports the included Linux setup, not the upstream Windows GUI.

Offline backend checks (after installing Python dependencies):
`python3 -m unittest discover -s tests -v`.

## Maintenance and contributions

This is a personal, AI-assisted project supplied as-is. **Pull requests will not be
reviewed or merged.** Fork it and ask your own AI agent to update, fix, or adapt the
project for your system. There is no promised support or compatibility with other
operating systems or deployments.

## Credits and license

MIT licensed. Based on [thefyfy/UnifiStockWatcher](https://github.com/thefyfy/UnifiStockWatcher),
which derives from [jamesccupps/UnifiStockWatcher](https://github.com/jamesccupps/UnifiStockWatcher).
See [LICENSE](LICENSE), [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md), and
[the preserved upstream MIT notice](licenses/upstream-MIT.txt).
