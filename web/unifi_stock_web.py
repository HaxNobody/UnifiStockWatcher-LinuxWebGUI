#!/usr/bin/env python3
"""Browser control panel for the Linux UniFi Stock Watcher service."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for
import requests


APP_DIR = Path(os.environ.get("UNIFI_WATCHER_APP_DIR", "/opt/unifi-stock-watcher/app"))
DATA_DIR = Path(os.environ.get("UNIFI_WATCHER_DATA_DIR", "/var/lib/unifi-stock-watcher"))
CONTROL = os.environ.get("UNIFI_WEB_CONTROL", "/usr/local/sbin/unifi-stock-web-control")
DEMO = os.environ.get("UNIFI_WEB_DEMO") == "1"

if APP_DIR.exists():
    sys.path.insert(0, str(APP_DIR))

try:
    from unifi_core import (  # type: ignore
        CATEGORY_LABELS,
        STORE_REGIONS,
        check_slug,
        fetch_all_products,
        get_price,
        is_available,
        notify_ntfy,
    )
except ImportError:
    if not DEMO:
        raise
    CATEGORY_LABELS = {}
    STORE_REGIONS = {
        "us": {"label": "United States", "path": "us/en"},
        "eu": {"label": "Europe", "path": "eu/en"},
        "uk": {"label": "United Kingdom", "path": "uk/en"},
        "ca": {"label": "Canada", "path": "ca/en"},
    }

    def fetch_all_products(*_args, **_kwargs):
        return []

    def check_slug(slug, *_args, **_kwargs):
        item = next((item for item in DEMO_WATCHED if item["slug"] == slug), None)
        if not item:
            raise RuntimeError("Product not found")
        return slug == "usw-flex-mini", item.get("price")

    def get_price(product):
        return product.get("price")

    def is_available(product):
        return bool(product.get("in_stock"))

    def notify_ntfy(*_args, **_kwargs):
        return None


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

_catalog_lock = threading.Lock()
_data_lock = threading.Lock()
_catalog_cache: dict[str, object] = {"region": None, "at": 0.0, "products": []}
WEB_VERSION = "1.3.0"


DEMO_WATCHED = [
    {"title": "Dream Machine Pro Max", "slug": "udm-pro-max", "favourite": True, "price": "$599.00"},
    {"title": "Switch Flex Mini", "slug": "usw-flex-mini", "favourite": True, "price": "$29.00"},
    {"title": "Switch Pro Max 24 PoE", "slug": "usw-pro-max-24-poe", "favourite": False, "price": "$799.00"},
    {"title": "U7 Pro Max", "slug": "u7-pro-max", "favourite": False, "price": "$279.00"},
    {"title": "UniFi Express 7", "slug": "ux7", "favourite": False, "price": "$199.00"},
]


def _json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _watched():
    items = list(DEMO_WATCHED) if DEMO else _json(DATA_DIR / "watched_items.json", [])
    state = _state()
    for item in items:
        item.setdefault("favourite", False)
        item.setdefault("muted", False)
        saved = state.get(item.get("slug"), {})
        item["in_stock"] = saved.get("in_stock")
        item["last_checked"] = saved.get("last_checked")
        if not item.get("price") and saved.get("price"):
            item["price"] = saved["price"]
    return items


def _state():
    if DEMO:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "udm-pro-max": {"in_stock": False, "last_checked": now},
            "usw-flex-mini": {"in_stock": True, "last_checked": now},
            "usw-pro-max-24-poe": {"in_stock": False, "last_checked": now},
            "u7-pro-max": {"in_stock": False, "last_checked": now},
            "ux7": {"in_stock": False, "last_checked": now},
        }
    return _json(DATA_DIR / "stock_state.json", {})


def _settings():
    base = {
        "region": "us",
        "poll_interval": 120,
        "ntfy_url": "https://ntfy.sh",
        "ntfy_topic": "",
        "ntfy_priority": "high",
        "ntfy_enabled": True,
        "max_retries": 3,
        "alert_favourites_only": False,
        "price_drop_alerts": False,
        "theme": "system",
        "accent": "blue",
        "text_size": "normal",
    }
    if not DEMO:
        base.update(_json(DATA_DIR / "settings.json", {}))
    return base


def _history():
    if DEMO:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "events": [
                {"ts": now, "slug": "usw-flex-mini", "title": "Switch Flex Mini", "in_stock": True, "price": "$29.00"},
                {"ts": now, "slug": "udm-pro-max", "title": "Dream Machine Pro Max", "in_stock": False, "price": "$599.00"},
                {"ts": now, "slug": "u7-pro-max", "title": "U7 Pro Max", "in_stock": False, "price": "$279.00"},
            ],
            "stats": {"total_checks": 16, "in_stock_alerts": 1},
        }
    return _json(DATA_DIR / "stock_history.json", {"events": [], "stats": {}})


def _run_control(*args: str, timeout: int = 15):
    if DEMO:
        now = datetime.now(timezone.utc).isoformat()
        return (
            "timer_active=active\n"
            "timer_enabled=enabled\n"
            "service_active=inactive\n"
            "service_result=success\n"
            "interval=120\n"
            f"last_trigger={now}\n"
            f"last_run={now}\n"
            f"next_run={now}\n"
        )
    result = subprocess.run(
        ["sudo", CONTROL, *args],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "control command failed").strip())
    return result.stdout


def _status():
    values = {"timer_active": "unknown", "service_active": "unknown", "interval": 120}
    try:
        for line in _run_control("status").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = int(value) if key == "interval" and value.isdigit() else value
    except Exception as exc:
        values["error"] = str(exc)
    values["watching"] = values.get("timer_active") == "active"
    latest = None
    for saved in _state().values():
        raw = saved.get("last_checked") if isinstance(saved, dict) else None
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if latest is None or parsed > latest:
                latest = parsed
        except (TypeError, ValueError):
            continue
    if latest:
        values["last_data_check"] = latest.astimezone(timezone.utc).isoformat()
        values["state_age_seconds"] = max(
            0, int((datetime.now(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds())
        )
    else:
        values["last_data_check"] = ""
        values["state_age_seconds"] = None
    interval = int(values.get("interval") or 120)
    age = values.get("state_age_seconds")
    values["stale"] = bool(values["watching"] and (age is None or age > max(interval * 3, 600)))
    values["healthy"] = bool(
        values["watching"]
        and not values["stale"]
        and values.get("service_result") in {None, "", "success"}
        and not values.get("error")
    )
    return values


def _catalog_product(product):
    category = product.get("_category", "")
    return {
        "slug": product.get("slug", ""),
        "title": product.get("title") or product.get("name") or product.get("slug", ""),
        "category": CATEGORY_LABELS.get(category, category.replace("category/", "").replace("-", " ").title()),
        "price": get_price(product),
        "in_stock": is_available(product),
    }


def _catalog(region):
    now = time.monotonic()
    with _catalog_lock:
        if _catalog_cache["region"] == region and now - float(_catalog_cache["at"]) < 300:
            return _catalog_cache["products"]
        products = [_catalog_product(p) for p in fetch_all_products(region)]
        products.sort(key=lambda p: (p["in_stock"], p["title"].casefold()))
        _catalog_cache.update(region=region, at=now, products=products)
        return products


@app.before_request
def _same_origin_writes():
    # Network access is controlled by Tailscale. This stateless browser check
    # prevents an unrelated website from submitting control-panel actions.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site" or (
            origin and origin.rstrip("/") != request.host_url.rstrip("/")
        ):
            return jsonify(error="Open this control panel directly to make changes."), 403
    return None


@app.route("/login", methods=["GET", "POST"])
@app.route("/logout", methods=["GET", "POST"])
def legacy_bookmark():
    return redirect(url_for("index"))


@app.get("/")
def index():
    return render_template("index.html", demo=DEMO)


@app.get("/api/bootstrap")
def bootstrap():
    settings = _settings()
    return jsonify(
        watched=_watched(),
        settings=settings,
        status=_status(),
        regions={key: value["label"] for key, value in STORE_REGIONS.items()},
        history=_history(),
        version=WEB_VERSION,
    )


@app.get("/api/catalog")
def catalog():
    if DEMO:
        extras = [
            {"title": "AI Pro", "slug": "uvc-ai-pro", "category": "Cameras & Physical Security", "price": "$499.00", "in_stock": True},
            {"title": "Cloud Gateway Fiber", "slug": "ucg-fiber", "category": "Cloud Gateways", "price": "$279.00", "in_stock": True},
            {"title": "Dream Router 7", "slug": "udr7", "category": "Cloud Gateways", "price": "$279.00", "in_stock": False},
            {"title": "G6 Bullet", "slug": "uvc-g6-bullet", "category": "Cameras & Physical Security", "price": "$199.00", "in_stock": False},
            {"title": "Access Hub", "slug": "ua-hub", "category": "Door Access", "price": "$199.00", "in_stock": True},
        ]
        return jsonify(extras + [dict(item, category="Watching") for item in DEMO_WATCHED])
    region = _settings().get("region", "us")
    try:
        return jsonify(_catalog(region))
    except Exception as exc:
        return jsonify(error=str(exc)), 502


@app.put("/api/watchlist")
def replace_watchlist():
    items = request.get_json(silent=True) or []
    if not isinstance(items, list) or len(items) > 1000:
        return jsonify(error="Invalid watch list."), 400
    clean = []
    for item in items:
        slug = str(item.get("slug", "")).strip()
        title = str(item.get("title", "")).strip()
        if not slug or not title or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in slug):
            return jsonify(error=f"Invalid product: {title or slug}"), 400
        clean.append({
            "title": title[:240],
            "slug": slug,
            "favourite": bool(item.get("favourite")),
            "muted": bool(item.get("muted")),
            "price": item.get("price"),
            "added_at": item.get("added_at") or datetime.now(timezone.utc).isoformat(),
        })
    if not DEMO:
        _atomic_json(DATA_DIR / "watched_items.json", clean)
    return jsonify(watched=clean)


@app.post("/api/items/<slug>/check")
def check_item(slug):
    item = next((entry for entry in _watched() if entry.get("slug") == slug), None)
    if not item:
        return jsonify(error="That product is not on the watch list."), 404
    try:
        settings = _settings()
        in_stock, price = check_slug(slug, settings.get("region", "us"), settings.get("max_retries", 3))
        now = datetime.now(timezone.utc).isoformat()
        previous = _state().get(slug, {})
        if not DEMO:
            with _data_lock:
                state = _state()
                previous = state.get(slug, {})
                state[slug] = {"in_stock": in_stock, "last_checked": now, "price": price}
                _atomic_json(DATA_DIR / "stock_state.json", state)

                history = _history()
                history.setdefault("events", []).append({
                    "ts": now,
                    "slug": slug,
                    "title": item["title"],
                    "in_stock": in_stock,
                    "price": price,
                })
                stats = history.setdefault("stats", {})
                stats["total_checks"] = int(stats.get("total_checks", 0)) + 1
                if in_stock and previous.get("in_stock") is False:
                    stats["in_stock_alerts"] = int(stats.get("in_stock_alerts", 0)) + 1
                history["events"] = history["events"][-2000:]
                _atomic_json(DATA_DIR / "stock_history.json", history)

                watched = _json(DATA_DIR / "watched_items.json", [])
                for entry in watched:
                    if entry.get("slug") == slug and price:
                        entry["price"] = price
                        break
                _atomic_json(DATA_DIR / "watched_items.json", watched)

            wants_alert = (
                settings.get("ntfy_enabled", True)
                and not item.get("muted", False)
                and (
                    not settings.get("alert_favourites_only", False)
                    or item.get("favourite", False)
                )
            )
            if in_stock and previous.get("in_stock") is False and wants_alert:
                path = STORE_REGIONS[settings.get("region", "us")]["path"]
                notify_ntfy(
                    f"IN STOCK: {item['title']}",
                    f"{item['title']} is now available{f' ({price})' if price else ''}!",
                    settings,
                    click_url=f"https://store.ui.com/{path}/products/{slug}",
                )
        return jsonify(ok=True, in_stock=in_stock, price=price, checked_at=now)
    except Exception as exc:
        return jsonify(error=str(exc)), 502


@app.put("/api/settings")
def save_settings():
    incoming = request.get_json(silent=True) or {}
    current = _settings()
    old_region = current.get("region", "us")
    allowed = {
        "region", "poll_interval", "ntfy_url", "ntfy_topic", "ntfy_priority", "ntfy_enabled",
        "max_retries", "alert_favourites_only", "price_drop_alerts", "theme",
        "accent", "text_size",
    }
    for key in allowed:
        if key in incoming:
            current[key] = incoming[key]
    region = str(current.get("region", "us"))
    if region not in STORE_REGIONS:
        return jsonify(error="Invalid store region."), 400
    try:
        interval = int(current.get("poll_interval", 120))
        retries = int(current.get("max_retries", 3))
    except (TypeError, ValueError):
        return jsonify(error="Interval and retry count must be numbers."), 400
    if interval < 15 or interval > 86400:
        return jsonify(error="Check interval must be between 15 seconds and 24 hours."), 400
    if retries < 1 or retries > 10:
        return jsonify(error="Retry count must be between 1 and 10."), 400
    current["poll_interval"] = interval
    current["max_retries"] = retries
    current["ntfy_url"] = str(current.get("ntfy_url", "")).strip().rstrip("/")
    current["ntfy_topic"] = str(current.get("ntfy_topic", "")).strip()
    if not DEMO:
        _atomic_json(DATA_DIR / "settings.json", current)
        if region != old_region:
            _atomic_json(DATA_DIR / "stock_state.json", {})
            with _catalog_lock:
                _catalog_cache.update(region=None, at=0.0, products=[])
        _run_control("interval", str(interval))
    return jsonify(settings=current, status=_status())


@app.post("/api/action/<action>")
def action(action):
    if action not in {"start", "stop", "check", "test"}:
        return jsonify(error="Unknown action."), 404
    try:
        if action == "test":
            settings = _settings()
            ntfy_url = str(settings.get("ntfy_url", "")).rstrip("/")
            topic = str(settings.get("ntfy_topic", ""))
            if not ntfy_url or not topic:
                raise RuntimeError("Configure the ntfy server and topic first.")
            response = requests.post(
                f"{ntfy_url}/{topic}",
                data="Sent from the browser control panel.".encode("utf-8"),
                headers={
                    "Title": "TEST: UniFi Stock Watcher works!",
                    "Priority": str(settings.get("ntfy_priority", "high")),
                    "Tags": "bell,white_check_mark",
                },
                timeout=10,
            )
            response.raise_for_status()
        else:
            _run_control(action)
        return jsonify(ok=True, status=_status())
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.get("/api/logs")
def logs():
    try:
        lines = request.args.get("lines", "300")
        if not lines.isdigit() or not 20 <= int(lines) <= 2000:
            return jsonify(error="Log line count must be between 20 and 2000."), 400
        return jsonify(text=_run_control("logs", lines))
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.delete("/api/history")
def clear_history():
    if not DEMO:
        _atomic_json(DATA_DIR / "stock_history.json", {"events": [], "stats": {"total_checks": 0, "in_stock_alerts": 0}})
    return jsonify(ok=True)


@app.get("/api/history.csv")
def export_history():
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["When", "Product", "Slug", "Change", "Price"])
    for event in reversed(_history().get("events", [])):
        writer.writerow([
            event.get("ts", ""), event.get("title", ""), event.get("slug", ""),
            "In stock" if event.get("in_stock") else "Sold out", event.get("price", ""),
        ])
    return Response("\ufeff" + stream.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=unifi-stock-history.csv"})


@app.get("/healthz")
def health():
    return jsonify(ok=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("UNIFI_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("UNIFI_WEB_PORT") or "8765"))
    parser.add_argument("--strictPort", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
