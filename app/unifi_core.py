"""
Unifi Stock Watcher — Core Module
Shared store API, configuration, notifications, price & stock history.
"""

import os
import tempfile
import re
import sys
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR       = Path(__file__).parent
DATA_DIR       = Path(os.environ.get("UNIFI_WATCHER_DATA_DIR", BASE_DIR))
CONFIG_FILE    = DATA_DIR / "watched_items.json"
SETTINGS_FILE  = DATA_DIR / "settings.json"
HISTORY_FILE   = DATA_DIR / "stock_history.json"
STATE_FILE     = DATA_DIR / "stock_state.json"


def atomic_json(path, value):
    path = Path(path)
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

import store_api as store

# ── Default settings ─────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "poll_interval":  120,
    "region":         "us",
    "max_retries":    3,
    "ntfy_url":       "https://ntfy.sh",
    "ntfy_topic":     "",
    "ntfy_priority":  "high",
}

# ── Settings load/save ───────────────────────────────────────────────────────

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            s = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
            merged = DEFAULT_SETTINGS.copy()
            merged.update(s)
            return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(s):
    atomic_json(SETTINGS_FILE, s)


# ── Config load/save ─────────────────────────────────────────────────────────

def load_watched():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            data = [dict(item, title=str(item.get("title") or item["slug"])) for item in data if isinstance(item,dict) and store.is_valid_slug(item.get("slug"))] if isinstance(data,list) else []
            for item in data:
                item.setdefault("favourite", False)
                item.setdefault("price", None)
                item.setdefault("added_at", None)
            return data
        except Exception:
            return []
    return []


def save_watched(items):
    atomic_json(CONFIG_FILE, items)


# ── Stock history ────────────────────────────────────────────────────────────

class StockHistory:
    """Persist stock check events to a JSON file for history/stats."""

    def __init__(self, path=HISTORY_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"events": [], "stats": {"total_checks": 0, "in_stock_alerts": 0}}

    def _save(self):
        # Keep only last 2000 events to prevent unbounded growth
        if len(self._data["events"]) > 2000:
            self._data["events"] = self._data["events"][-2000:]
        atomic_json(self._path, self._data)

    def record_check(self, slug, title, in_stock, price=None):
        with self._lock:
            self._data["stats"]["total_checks"] += 1
            if in_stock:
                self._data["stats"]["in_stock_alerts"] += 1
            self._data["events"].append({
                "ts":       datetime.now(timezone.utc).isoformat(),
                "slug":     slug,
                "title":    title,
                "in_stock": in_stock,
                "price":    price,
            })
            self._save()

    def get_stats(self):
        with self._lock:
            return self._data["stats"].copy()

    def get_events(self, slug=None, limit=50):
        with self._lock:
            events = self._data["events"]
            if slug:
                events = [e for e in events if e["slug"] == slug]
            return events[-limit:]

    def last_in_stock(self, slug):
        with self._lock:
            for e in reversed(self._data["events"]):
                if e["slug"] == slug and e["in_stock"]:
                    return e["ts"]
            return None

    def clear(self):
        with self._lock:
            self._data = {"events": [], "stats": {"total_checks": 0, "in_stock_alerts": 0}}
            self._save()


# Global instance
stock_history = StockHistory()


# ── Export / Import ──────────────────────────────────────────────────────────

def export_watchlist(filepath):
    """Export current watch list to a JSON file."""
    items = load_watched()
    Path(filepath).write_text(json.dumps(items, indent=2))
    return len(items)


def import_watchlist(filepath):
    """Import watch list from a JSON file, merging with existing."""
    new_items = json.loads(Path(filepath).read_text())
    existing  = load_watched()
    slugs     = {w["slug"] for w in existing}
    added     = 0
    for item in new_items:
        if item.get("slug") and item["slug"] not in slugs:
            item.setdefault("favourite", False)
            item.setdefault("price", None)
            item.setdefault("added_at", datetime.now().isoformat())
            existing.append(item)
            slugs.add(item["slug"])
            added += 1
    save_watched(existing)
    return added


# Compatibility layer for the Linux and web callers.
import store_api as store
STORE_REGIONS=store.STORE_REGIONS
STORE_BASE=store.STORE_BASE
CATEGORIES=store.CATEGORIES
CATEGORY_LABELS=store.CATEGORY_LABELS
is_available=store.is_available
get_price=store.get_price
def fetch_all_products(region="us", progress_cb=None, error_cb=None):
    store.load_pages()
    try:
        build=store.get_build_id(region)
        store.refresh_category_coverage(build,region)
        products=store.fetch_all_products(build,region,progress_cb,error_cb)
        if not products: raise store.StoreError('Store returned no products; check its page format')
        return products
    finally: store.save_pages()
def check_slug(slug, region="us", retries=3):
    return store.check_slug(store.get_build_id(region),slug,region,retries)
def notify_ntfy(title,message,settings=None,click_url=None):
    from monitor import notify
    return notify(title,message,settings,click_url)

