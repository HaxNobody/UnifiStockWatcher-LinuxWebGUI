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
import monitor
import store_api as store


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
        store.DATA_DIR = self.data
        web.app.testing = True
        self.client = web.app.test_client()
        self.control = patch.object(web, "_run_control", side_effect=lambda *args, **kwargs:
            "timer_active=active\ntimer_enabled=enabled\nservice_result=success\ninterval=120\n" if args[0] == "status" else "")
        self.control.start()
        self.addCleanup(self.control.stop)
        network=patch('requests.sessions.Session.request',side_effect=AssertionError('Unexpected network request in offline tests'))
        network.start();self.addCleanup(network.stop)

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
        with patch.object(monitor, "check_one", return_value=(False, "$10.00")):
            self.assertEqual(self.client.post("/api/items/test-item/check").status_code, 200)

    def test_baseline_then_transition_and_mute(self):
        core.save_watched([{"slug": "test-item", "title": "Test", "muted": False}])
        product={'slug':'test-item','variants':[{'status':'Available','displayPrice':{'amount':1000,'currency':'USD'}}]}
        with patch.object(core, "fetch_all_products", return_value=[product]), patch.object(monitor, "notify") as notify:
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
        with patch.object(core, "fetch_all_products", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                watcher.run_once()

    def test_store_parsing_and_csv(self):
        self.assertFalse(store.is_available({'variants':None}))
        self.assertIsNone(store.get_price({'variants':[{'price':{'amount':'invalid'}}]}))
        self.assertEqual(store.product_title({'title':None,'slug':'a'}),'a')
        p={'slug':'new','historicalSlugs':['old'],'variants':[]}
        self.assertEqual(store._find_product({'product':p},'old'),p)
        self.assertIsNone(store._find_product({'product':dict(p,historicalSlugs='older')},'old'))
        for value in ['=1+1',' +cmd','@SUM(A1)']:
            self.assertTrue(web.csv_safe(value).startswith("'"))

    def test_region_race_and_sparse_history(self):
        core.save_watched([{'slug':'a','title':'A'}]); settings=core.load_settings()
        p={'slug':'a','variants':[{'status':'SoldOut'}]}
        self.assertTrue(monitor.record('a',p,settings))
        before=core.HISTORY_FILE.read_text()
        self.assertTrue(monitor.record('a',p,settings))
        self.assertEqual(before,core.HISTORY_FILE.read_text())
        self.assertEqual(json.loads((self.data/'check_counts.json').read_text())['total_checks'],2)
        core.save_settings(dict(settings,region='eu',region_revision='changed'))
        self.assertFalse(monitor.record('a',p,settings))

    def test_token_kept_private_and_clearable(self):
        self.assertEqual(self.client.put('/api/settings',json={'ntfy_token':'test-token'}).status_code,200)
        result=self.client.get('/api/bootstrap').json
        self.assertNotIn('ntfy_token',result['settings'])
        self.assertTrue(result['settings']['has_ntfy_token'])
        self.client.put('/api/settings',json={'ntfy_token':''})
        self.assertEqual(core.load_settings()['ntfy_token'],'test-token')
        self.client.put('/api/settings',json={'clear_ntfy_token':True})
        self.assertFalse(core.load_settings().get('ntfy_token'))

    def test_notification_retry_and_redaction(self):
        from unittest.mock import Mock
        bad=Mock(status_code=429,headers={'Retry-After':'nan'})
        good=Mock(status_code=200)
        settings={'ntfy_url':'https://ntfy.sh','ntfy_topic':'sensitive-topic','ntfy_token':'private-token'}
        with patch.object(monitor.requests,'post',side_effect=[bad,good]) as post, patch.object(monitor.time,'sleep'):
            self.assertTrue(monitor.notify('Title','Body',settings))
            self.assertEqual(post.call_count,2)
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'],'Bearer private-token')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
        with patch.object(monitor.requests,'post',return_value=Mock(status_code=403)):
            with self.assertRaisesRegex(RuntimeError,'HTTP 403') as error: monitor.notify('T','B',settings)
            self.assertNotIn('sensitive-topic',str(error.exception))

    def test_backoff_survives_next_cycle(self):
        core.save_watched([{'slug':'a','title':'A'}])
        with patch.object(core,'fetch_all_products',side_effect=RuntimeError('down')) as fetch:
            with self.assertRaises(RuntimeError):monitor.cycle()
            monitor.cycle()
            self.assertEqual(fetch.call_count,1)

    def test_category_redirect_etag_and_persistence(self):
        from unittest.mock import Mock
        def response(data,status=200,headers=None):
            r=Mock(status_code=status,headers=headers or {},content=json.dumps(data).encode())
            r.json.return_value=data
            return r
        redirect=response({'pageProps':{'__N_REDIRECT':'/us/en/category/all-physical-security'}})
        data={'pageProps':{'products':[{'slug':'camera','variants':[]}]}}
        full=response(data,headers={'ETag':'example-etag'})
        cached=response({},304)
        store.forget_pages()
        session=Mock();session.get.side_effect=[redirect,full]
        with patch.object(store,'get_session',return_value=session):
            self.assertIn('camera',store._fetch_category('build','us','category/all-cameras-nvrs'))
        store.save_pages();store.forget_pages();store.load_pages()
        session.get.side_effect=[cached]
        with patch.object(store,'get_session',return_value=session):
            self.assertIn('camera',store._fetch_category('build','us','category/all-physical-security'))
            self.assertEqual(session.get.call_args.kwargs['headers']['If-None-Match'],'example-etag')

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
