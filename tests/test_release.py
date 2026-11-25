"""Offline release checks; never contact stores, ntfy, or systemd."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
_initial = tempfile.TemporaryDirectory()
os.environ["UNIFI_WATCHER_APP_DIR"] = str(ROOT / "app")
os.environ["UNIFI_WATCHER_DATA_DIR"] = _initial.name
os.environ.pop("UNIFI_WEB_DEMO", None)
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "web"), str(ROOT / "scripts")]
import unifi_core as core
import unifi_watcher as watcher
import unifi_stock_web as web
import configure


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        for attribute, filename in [("CONFIG_FILE", "watched_items.json"), ("SETTINGS_FILE", "settings.json"),
                                    ("STATE_FILE", "stock_state.json"), ("HISTORY_FILE", "stock_history.json")]:
            setattr(core, attribute, self.data / filename)
        watcher.STATE_FILE = core.STATE_FILE
        watcher.stock_history = core.StockHistory(core.HISTORY_FILE)
        web.DATA_DIR = self.data
        web.app.testing = True
        self.client = web.app.test_client()
        self.control = patch.object(web, "_run_control", side_effect=lambda *args, **kwargs:
            "timer_active=active\ntimer_enabled=enabled\nservice_result=success\ninterval=120\n" if args[0] == "status" else "")
        self.control.start()
        self.addCleanup(self.control.stop)

    def test_anonymous_api_and_no_cookies(self):
        for url in ["/", "/api/bootstrap", "/api/logs", "/api/history.csv", "/healthz"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.headers.getlist("Set-Cookie"))
        self.assertIsNone(web.app.secret_key)
        self.assertNotIn("csrf", self.client.get("/api/bootstrap").json)
        self.assertIn("Notifications", self.client.get("/").text)
        self.assertEqual(self.client.post("/api/action/check").status_code, 200)
        self.assertEqual(self.client.put("/api/settings", json={"poll_interval": 120}).status_code, 200)

    def test_cross_site_action_rejected(self):
        self.assertEqual(self.client.post("/api/action/stop", headers={"Origin": "https://other.example"}).status_code, 403)

    def test_watchlist_persistence_and_item_check(self):
        self.assertEqual(self.client.put("/api/watchlist", json=[{"slug": "test-item", "title": "Test"}]).status_code, 200)
        with patch.object(web, "check_slug", return_value=(False, "$10.00")):
            self.assertEqual(self.client.post("/api/items/test-item/check").status_code, 200)
        self.assertFalse(json.loads(core.STATE_FILE.read_text())["test-item"]["in_stock"])

    def test_baseline_then_transition_and_mute(self):
        core.save_watched([{"slug": "test-item", "title": "Test", "muted": False}])
        with patch.object(watcher, "check_slug", return_value=(True, "$10.00")), patch.object(watcher, "notify_ntfy") as notify:
            watcher.run_once()
            notify.assert_not_called()
            core.atomic_json(core.STATE_FILE, {"test-item": {"in_stock": False}})
            watcher.run_once()
            notify.assert_called_once()
            core.save_watched([{"slug": "test-item", "title": "Test", "muted": True}])
            core.atomic_json(core.STATE_FILE, {"test-item": {"in_stock": False}})
            notify.reset_mock()
            watcher.run_once()
            notify.assert_not_called()

    def test_failed_product_check_fails_service(self):
        core.save_watched([{"slug": "test-item", "title": "Test"}])
        with patch.object(watcher, "check_slug", side_effect=RuntimeError("offline")):
            with self.assertRaises(SystemExit) as result:
                watcher.run_once()
            self.assertEqual(result.exception.code, 1)

    def test_private_bind_and_configuration(self):
        for host in ["127.0.0.1", "::1", "localhost", "100.64.0.1", "fd7a:115c:a1e0::1"]:
            self.assertTrue(configure.allowed_host(host), host)
        for host in ["0.0.0.0", "::", "192.168.1.1", "8.8.8.8", "example.com"]:
            self.assertFalse(configure.allowed_host(host), host)
        config = self.data / "web.conf"
        self.assertEqual(configure.configuration(config, "--localhost"), ("127.0.0.1", 8765))
        config.write_text("UNIFI_WEB_HOST=0.0.0.0\n")
        with self.assertRaises(SystemExit):
            configure.configuration(config, "--auto")


if __name__ == "__main__":
    unittest.main()
