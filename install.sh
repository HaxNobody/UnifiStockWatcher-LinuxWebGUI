#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT=/opt/unifi-stock-watcher
DATA=/var/lib/unifi-stock-watcher
CONFIG=/etc/unifi-stock-watcher-web.conf
MODE="${1:---auto}"
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || die 'Run sudo bash install.sh'
[[ $# -le 1 && "$MODE" =~ ^--(auto|localhost|tailscale)$ ]] || die 'Usage: sudo bash install.sh [--localhost|--tailscale]'
[[ -d /run/systemd/system ]] || die 'A running systemd installation is required.'
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) die 'Supported installer targets: Ubuntu and Debian.' ;; esac
for file in app/unifi_core.py app/unifi_watcher.py web/unifi_stock_web.py web/templates/index.html web/static/app.js web/static/app.css web/static/icon.svg scripts/configure.py scripts/run-web.py scripts/unifi-stock-web-control requirements.txt systemd/unifi-stock-watcher.service systemd/unifi-stock-watcher.timer systemd/unifi-stock-watcher-web.service; do
    [[ -f "$SOURCE_DIR/$file" ]] || die "Missing release file: $file"
done
[[ "$SOURCE_DIR" != "$ROOT" && "$SOURCE_DIR" != "$ROOT/app" && "$SOURCE_DIR" != "$ROOT/web" ]] || die 'Run the installer from a separate clone/extraction directory.'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates python3 python3-venv sudo
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10 or newer is required"'
URL="$(python3 "$SOURCE_DIR/scripts/configure.py" "$CONFIG" "$DATA" "$MODE" --check)"
TIMER_ENABLED=1
if [[ -d "$ROOT/web" ]] && ! systemctl is-enabled --quiet unifi-stock-watcher.timer; then TIMER_ENABLED=0; fi
getent group unifistock >/dev/null || groupadd --system unifistock
id unifistock >/dev/null 2>&1 || useradd --system --gid unifistock --home-dir "$DATA" --shell /usr/sbin/nologin unifistock
install -d -o root -g root -m 0755 "$ROOT"
BACKUP="$(mktemp -d "$ROOT/install-backup-XXXXXXXX")"
chmod 0700 "$BACKUP"
for target in "$ROOT/app" "$ROOT/web" "$ROOT/scripts" "$CONFIG" /etc/systemd/system/unifi-stock-watcher.service /etc/systemd/system/unifi-stock-watcher.timer /etc/systemd/system/unifi-stock-watcher-web.service /etc/sudoers.d/unifi-stock-watcher-web; do
    if [[ -e "$target" ]]; then cp -a --parents "$target" "$BACKUP/"; fi
done
printf 'Private installation backup: %s\n' "$BACKUP"
python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/python3" -m pip install --disable-pip-version-check -r "$SOURCE_DIR/requirements.txt"
systemctl stop unifi-stock-watcher.timer unifi-stock-watcher.service unifi-stock-watcher-web.service 2>/dev/null || true
trap 'printf "Installation interrupted. Services may be stopped. Backup: %s\n" "$BACKUP" >&2' ERR
if [[ -d "$DATA" ]]; then cp -a --parents "$DATA" "$BACKUP/"; fi
# Remove old schedule overrides from active use, but retain every file in backup.
if [[ -d /etc/systemd/system/unifi-stock-watcher.timer.d ]]; then
    mv /etc/systemd/system/unifi-stock-watcher.timer.d "$BACKUP/timer-dropins"
fi
install -d -o root -g root -m 0755 "$ROOT/app" "$ROOT/web/templates" "$ROOT/web/static" "$ROOT/scripts"
install -o root -g root -m 0644 "$SOURCE_DIR"/app/*.py "$ROOT/app/"
install -o root -g root -m 0644 "$SOURCE_DIR/web/unifi_stock_web.py" "$ROOT/web/"
install -o root -g root -m 0644 "$SOURCE_DIR/web/templates/index.html" "$ROOT/web/templates/"
install -o root -g root -m 0644 "$SOURCE_DIR"/web/static/* "$ROOT/web/static/"
install -o root -g root -m 0644 "$SOURCE_DIR/scripts/configure.py" "$SOURCE_DIR/scripts/run-web.py" "$ROOT/scripts/"
install -o root -g root -m 0755 "$SOURCE_DIR/scripts/unifi-stock-web-control" /usr/local/sbin/unifi-stock-web-control
install -o root -g root -m 0644 "$SOURCE_DIR"/systemd/* /etc/systemd/system/
install -d -o unifistock -g unifistock -m 0750 "$DATA"
python3 "$SOURCE_DIR/scripts/configure.py" "$CONFIG" "$DATA" "$MODE" >/dev/null
chown -R unifistock:unifistock "$DATA"
chmod 0750 "$DATA"
find "$DATA" -maxdepth 1 -type f -name '*.json' -exec chmod 0640 {} +
printf 'unifistock ALL=(root) NOPASSWD: /usr/local/sbin/unifi-stock-web-control *\n' > /etc/sudoers.d/unifi-stock-watcher-web
chmod 0440 /etc/sudoers.d/unifi-stock-watcher-web
visudo -cf /etc/sudoers.d/unifi-stock-watcher-web >/dev/null
"$ROOT/venv/bin/python3" -m compileall -q "$ROOT/app" "$ROOT/web" "$ROOT/scripts"
systemctl daemon-reload
INTERVAL="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1])).get("poll_interval",120)))' "$DATA/settings.json")"
/usr/local/sbin/unifi-stock-web-control interval "$INTERVAL"
systemctl enable unifi-stock-watcher-web.service
systemctl restart unifi-stock-watcher-web.service
if [[ "$TIMER_ENABLED" == 1 ]]; then systemctl enable --now unifi-stock-watcher.timer; fi
sleep 2
systemctl is-active --quiet unifi-stock-watcher-web.service || die "Web service failed. Run journalctl -u unifi-stock-watcher-web.service. Backup: $BACKUP"
"$ROOT/venv/bin/python3" - "$URL" <<'PY'
import sys
import time
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(10):
    try:
        with opener.open(sys.argv[1] + 'healthz', timeout=3) as response:
            if response.status == 200:
                break
    except OSError:
        if attempt == 9:
            raise SystemExit('Web health check failed; inspect journalctl -u unifi-stock-watcher-web.service')
        time.sleep(1)
PY
trap - ERR
printf '\nInstalled UniFi Stock Watcher 1.4.0.\nOpen this URL for ntfy setup:\n  %s#notifications\n' "$URL"
printf '\nThen use Add products on the Watch list. No password is required.\n'
if [[ "$URL" == http://127.0.0.1:* ]]; then
    printf 'Localhost mode: open the URL on this server, or use an SSH tunnel.\n'
    printf 'For remote Tailscale access, connect Tailscale and rerun: sudo bash install.sh --tailscale\n'
fi
