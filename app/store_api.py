"""Store parsing ported from jamesccupps/UnifiStockWatcher 3515b84d (MIT).
Linux persistence is supplied by this fork; no Windows or GUI imports.
"""
import os, re, json, time, threading, logging, base64
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
log = logging.getLogger("unifi_watcher")
class StoreError(RuntimeError): pass
class ProductNotFound(StoreError): pass
DATA_DIR = Path(os.environ.get("UNIFI_WATCHER_DATA_DIR", "/var/lib/unifi-stock-watcher"))
def read_json(path, default):
    try: return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError): return default
def write_json(path, value):
    from unifi_core import atomic_json
    atomic_json(Path(path), value)
def category_file(region): return DATA_DIR / ("category_cache_" + region + ".json")
STORE_REGIONS = {
    "us": {"label": "United States", "path": "us/en"},
    "eu": {"label": "Europe",        "path": "eu/en"},
    "uk": {"label": "United Kingdom", "path": "uk/en"},
    "ca": {"label": "Canada",        "path": "ca/en"},
}

STORE_BASE = "https://store.ui.com"
MAX_REDIRECTS = 4
CATEGORY_WORKERS = 4
PAGE_REFRESH = 600          # seconds a kept category page may answer "not modified" before a full download
CATEGORY_DISCOVERY_TTL = 24 * 3600   # re-check the store's category list daily

# Category slugs the store actually serves today. These get renamed - cameras
# moved from all-cameras-nvrs to all-physical-security, and all-advanced-hosting
# became an alias for the accessories page - so _fetch_category follows
# redirects and logs a warning when one of these drifts.
CATEGORIES = [
    "category/all-cloud-gateways",
    "category/all-switching",
    "category/all-wifi",
    "category/all-physical-security",
    "category/all-door-access",
    "category/all-integrations",
    "category/accessories-cables-dacs",
    "category/network-storage",
]

CATEGORY_LABELS = {
    "category/all-cloud-gateways":      "Cloud Gateways",
    "category/all-switching":           "Switching",
    "category/all-wifi":                "WiFi",
    "category/all-physical-security":   "Cameras & Physical Security",
    "category/all-door-access":         "Door Access",
    "category/all-integrations":        "Integrations",
    "category/accessories-cables-dacs": "Accessories, Cables & DACs",
    "category/network-storage":         "Network Storage",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html",
}

# A store slug is lowercase letters, digits and hyphens. Anything else - from a
# hand-edited or shared watch list - would be pasted into store URLs.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$", re.IGNORECASE)


def is_valid_slug(slug):
    return isinstance(slug, str) and bool(_SLUG_RE.fullmatch(slug)) and ".." not in slug


def store_home(region="us"):
    return f"{STORE_BASE}/{STORE_REGIONS.get(region, STORE_REGIONS['us'])['path']}"


def product_url(slug, region="us"):
    """The store page for a product, or the store's home for a bad slug."""
    if not is_valid_slug(slug):
        return store_home(region)
    return f"{store_home(region)}/products/{slug}"

# ── HTTP session ─────────────────────────────────────────────────────────────

_session = None
_session_lock = threading.Lock()


def get_session():
    """Process-wide requests.Session.

    Every call previously opened a fresh connection, paying a full TCP and TLS
    handshake per request - nine of them per poll cycle, plus one per quick
    check. A pooled session reuses the connection and adds bounded retries for
    transient network and 5xx failures.
    """
    global _session
    with _session_lock:
        if _session is None:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            s = requests.Session()
            s.headers.update(HEADERS)
            adapter = HTTPAdapter(
                pool_connections=4,
                pool_maxsize=CATEGORY_WORKERS * 2,
                max_retries=Retry(
                    total=2,
                    connect=2,
                    read=2,
                    status=2,
                    backoff_factor=0.5,
                    status_forcelist=(500, 502, 504),
                    allowed_methods=frozenset(["GET"]),
                    raise_on_status=False,
                ),
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session


def close_session():
    """Release pooled connections (tests, and clean shutdown)."""
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
            _session = None


# ── Build ID cache ───────────────────────────────────────────────────────────

class BuildIdCache:
    """Cache the Next.js buildId per region, fetching it at most once at a time.

    Keyed by region: the id is currently identical across the four stores, but
    nothing in the API guarantees that, and serving a US id for an EU request
    would 404 every product on the first cycle after a region switch.

    The fetch happens under the lock so that a fleet of category workers all
    starting at once - or all reacting to the same rotation - makes one
    homepage request between them rather than one each.
    """

    def __init__(self, ttl_seconds=300):
        self._lock    = threading.Lock()
        self._entries = {}          # region -> (build_id, fetched_at)
        self._ttl     = ttl_seconds

    def _fresh(self, region):
        entry = self._entries.get(region)
        if not entry:
            return None
        build_id, fetched = entry
        if (datetime.now() - fetched).total_seconds() >= self._ttl:
            return None
        return build_id

    def get(self, region="us", force=False):
        with self._lock:
            if not force:
                cached = self._fresh(region)
                if cached:
                    return cached

            store_home = f"{STORE_BASE}/{STORE_REGIONS[region]['path']}"
            r = get_session().get(store_home, headers=HEADERS, timeout=15)
            r.raise_for_status()
            m = re.search(r'"buildId":"([^"]+)"', r.text)
            if not m:
                raise StoreError("Could not find buildId on store homepage.")

            build_id = m.group(1)
            self._entries[region] = (build_id, datetime.now())
            return build_id

    def invalidate(self, region=None):
        with self._lock:
            if region is None:
                self._entries.clear()
            else:
                self._entries.pop(region, None)


# Global instance
_build_cache = BuildIdCache()


def get_build_id(region="us", force=False):
    return _build_cache.get(region, force)


def invalidate_build_id(region=None):
    _build_cache.invalidate(region)


# ── Store API ────────────────────────────────────────────────────────────────

def discover_category_paths(region="us"):
    """Category paths linked from the store homepage.

    The store does not publish a category index as JSON, but the rendered
    homepage links every one of them.
    """
    region_path = STORE_REGIONS[region]["path"]
    r = get_session().get(f"{STORE_BASE}/{region_path}", timeout=15)
    r.raise_for_status()
    pattern = rf'/{re.escape(region_path)}/(category/[a-z0-9\-]+)'
    return sorted(set(re.findall(pattern, r.text)))


def effective_categories(region="us"):
    """The category list to fetch: the built-ins plus any learned extras."""
    cache = read_json(category_file(region), {})
    extras = cache.get("extra") if isinstance(cache, dict) else None
    extras = [c for c in (extras or []) if isinstance(c, str)]
    return list(dict.fromkeys(list(CATEGORIES) + extras))


def refresh_category_coverage(build_id, region="us", force=False):
    """Notice categories the store has that we do not, and adopt the useful ones.

    Renames already self-heal, because _fetch_category follows the store's
    redirects. This covers the other direction: a genuinely *new* category.

    Most discovered paths are sub-views that return their parent's full
    product set - all eight extras found at the time of writing added zero
    unique products - so adopting every discovered path would double the
    request count per cycle for nothing. Each unknown category is therefore
    fetched once, kept only if it contributes products the built-ins miss,
    and otherwise recorded as redundant so it is not re-examined.

    Runs at most once a day. Returns the list of newly adopted categories.
    """
    cache = read_json(category_file(region), {})
    if not isinstance(cache, dict):
        cache = {}
    checked = _parse_ts(cache.get("checked_at"))
    if not force and checked:
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        if age < CATEGORY_DISCOVERY_TTL:
            return []

    try:
        discovered = discover_category_paths(region)
    except Exception as e:
        log.warning("Could not discover store categories: %s", e)
        return []
    if not discovered:
        log.warning("Category discovery found nothing; leaving the list alone")
        return []

    known = set(effective_categories(region))
    redundant = set(cache.get("redundant") or [])
    unknown = [c for c in discovered if c not in known and c not in redundant]

    adopted = []
    if unknown:
        covered = set()
        for cat in known:
            try:
                covered |= set(_fetch_category(build_id, region, cat))
            except Exception:
                pass                      # a broken built-in is reported elsewhere

        for cat in unknown:
            try:
                found = _fetch_category(build_id, region, cat)
            except Exception as e:
                log.warning("Could not examine new category %s: %s", cat, e)
                continue
            new_slugs = set(found) - covered
            if new_slugs:
                adopted.append(cat)
                covered |= new_slugs
                log.warning("Adopted new store category %s (+%d products)",
                            cat, len(new_slugs))
            else:
                redundant.add(cat)

    cache = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "extra": sorted(set(cache.get("extra") or []) | set(adopted)),
        "redundant": sorted(redundant),
        "seen": discovered,
    }
    try:
        write_json(category_file(region), cache)
    except OSError as e:
        log.warning("Could not save the category cache: %s", e)
    return adopted


def _objects(value):
    """The dicts in a list from the store's data, or none."""
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


# Category pages as last downloaded: path -> (build id, ETag, body, when).
# The store answers a request carrying the ETag with 304 and no body when the
# page hasn't changed. Downloading every page every check came to ~430 KB
# (3.7 MB of JSON) a check: ~600 MB a day at the default minute. The body is
# kept, not the parsed products: a few MB rather than tens, and each check
# still gets products of its own.
_pages = {}
_pages_lock = threading.Lock()


def _kept_page(build_id, path):
    """(ETag, body) of the page kept for this build, or None. A page older
    than PAGE_REFRESH is downloaded whole again, so a store that answered 304
    wrongly could only hide a change for that long."""
    with _pages_lock:
        kept = _pages.get(path)
    if kept and kept[0] == build_id and time.monotonic() - kept[3] < PAGE_REFRESH:
        return kept[1], kept[2]
    return None


def _keep_page(build_id, path, etag, body):
    with _pages_lock:
        _pages[path] = (build_id, etag, body, time.monotonic())


def forget_pages():
    """Drop the kept category pages: the next check downloads them all."""
    with _pages_lock:
        _pages.clear()

def load_pages():
    cached=read_json(DATA_DIR/'page_cache.json',{})
    if not isinstance(cached,dict): return
    with _pages_lock:
        for key,value in cached.items():
            try:
                build,etag,body,when=value
                age=time.time()-when
                if 0<=age<PAGE_REFRESH and len(body)<4_000_000:
                    _pages[key]=(build,etag,base64.b64decode(body),time.monotonic()-age)
            except (ValueError,TypeError): continue

def save_pages():
    with _pages_lock:
        cached={key:[b,e,base64.b64encode(body).decode('ascii'),time.time()-(time.monotonic()-stamp)]
                for key,(b,e,body,stamp) in _pages.items() if time.monotonic()-stamp<PAGE_REFRESH}
    write_json(DATA_DIR/'page_cache.json',cached)


def _fetch_category(build_id, region, cat):
    """Fetch one category page. Returns {slug: product}.

    Follows the store's own redirects. Categories get renamed - cameras moved
    from all-cameras-nvrs to all-physical-security - and the old path then
    answers HTTP 200 with a redirect directive and no products. Without this
    the whole category silently disappeared from the catalog: no error, no
    empty-result signal, just 135 products that stopped being watched.

    A 404 usually means the buildId rotated between the homepage fetch and
    this call, so refresh it once and retry with the new id.

    A page downloaded before is asked for with its ETag; "not modified" (304)
    reads the kept copy (`_kept_page`).
    """
    path = f"/{STORE_REGIONS[region]['path']}/{cat}"
    found = {}
    refreshed = False

    for _ in range(MAX_REDIRECTS):
        kept = _kept_page(build_id, path)
        try:
            r = get_session().get(_data_url(build_id, path), timeout=15,
                                  headers={"If-None-Match": kept[0]} if kept else None)
            if r.status_code == 304:
                if not kept:
                    raise StoreError(f"{cat}: 304 for a page that wasn't asked about")
                data = json.loads(kept[1])
            else:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("Location") or r.headers.get("x-nextjs-redirect")
                    if not loc:
                        raise StoreError(
                            f"{cat}: {r.status_code} with no redirect target")
                    path = urlsplit(loc).path.removesuffix(".json")
                    continue
                r.raise_for_status()
                data = r.json()
            page_props = data.get("pageProps") if isinstance(data, dict) else None
            if not isinstance(page_props, dict):
                raise StoreError(f"{cat}: the page has no product data")
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404 and not refreshed:
                refreshed = True
                fresh = get_build_id(region, force=True)
                if fresh != build_id:
                    build_id = fresh
                    continue
            raise

        target = page_props.get("__N_REDIRECT")
        if target:
            path = urlsplit(target).path.removesuffix(".json")
            if "/category/" not in path:
                # Landing pages like whats-new redirect to the homepage. That
                # is not category drift, so it does not deserve a warning.
                log.debug("%s redirects out of the category space (%s)", cat, path)
                return {}
            log.warning("Category %s now redirects to %s - update CATEGORIES",
                        cat, path)
            continue

        for subcat in _objects(page_props.get("subCategories")):
            for p in _objects(subcat.get("products")):
                if isinstance(p.get("slug"), str) and p["slug"]:
                    p["_category"] = cat
                    found[p["slug"]] = p
        # Belt-and-suspenders: a flat products list, in case UI ever switches a
        # category page to that shape. setdefault so the richer subCategory
        # entry wins if both exist.
        for p in _objects(page_props.get("products")):
            if isinstance(p.get("slug"), str) and p["slug"]:
                p.setdefault("_category", cat)
                found.setdefault(p["slug"], p)
        etag = r.headers.get("ETag")
        if r.status_code == 200 and etag:
            _keep_page(build_id, path, etag, r.content)
        return found

    raise StoreError(f"{cat}: too many redirects (>{MAX_REDIRECTS})")


def fetch_all_products(build_id, region="us", progress_cb=None, error_cb=None):
    """Fetch every category, deduplicated by slug. Returns a list of dicts.

    progress_cb(int_0_to_100) is called as categories complete.
    error_cb(category_slug, exception) reports per-category failures so callers
    can surface them rather than losing them to stdout. When every category
    fails, it raises StoreError: an empty catalog read as "no products" and
    made every watched product fall back to its own page.

    The nine category pages are independent, so they are fetched concurrently.
    Sequentially, with a 0.3s pause between each, a cycle spent most of its time
    waiting on round trips it did not need to serialise.
    """
    products = {}
    done = 0
    failures = []
    with ThreadPoolExecutor(max_workers=CATEGORY_WORKERS,
                            thread_name_prefix="unifi-cat") as pool:
        cats = effective_categories(region)
        futures = {pool.submit(_fetch_category, build_id, region, cat): cat
                   for cat in cats}
        for fut in as_completed(futures):
            cat = futures[fut]
            try:
                products.update(fut.result())
            except Exception as e:
                log.warning("Category fetch failed: %s — %s", cat, e)
                failures.append(e)
                if error_cb:
                    try:
                        error_cb(cat, e)
                    except Exception:
                        log.exception("error_cb raised")
            done += 1
            if progress_cb:
                try:
                    progress_cb(int(done / len(cats) * 100))
                except Exception:
                    log.exception("progress_cb raised")

    if cats and len(failures) == len(cats):
        raise StoreError(f"no category could be read ({failures[0]})") from failures[0]
    log.info("Fetched %d unique products across %d categories",
             len(products), len(cats))
    return list(products.values())


def is_refusal(error):
    """A 429 or 503 from the store: rate limiting, or overloaded. Asking
    product by product then would only multiply the requests it's refusing."""
    return getattr(getattr(error, "response", None), "status_code", None) in (429, 503)


def _variants(product):
    """A product's variants that are objects. The store's data is read as it
    comes: "variants": null or a stray string in the list once failed the
    whole cycle, every watched product with it."""
    variants = product.get("variants") if isinstance(product, dict) else None
    return [v for v in variants if isinstance(v, dict)] if isinstance(variants, list) else []


def is_available(product):
    return any(v.get("status") == "Available" for v in _variants(product))


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _format_price(price_val):
    """Format a price value which may be a Money dict, number, or string;
    None for anything else, such as an amount that isn't a number."""
    if isinstance(price_val, str):
        return price_val.strip()[:40] or None
    if isinstance(price_val, dict):
        amount   = price_val.get("amount", 0)
        currency = price_val.get("currency", "USD")
        if not _is_number(amount) or not isinstance(currency, str):
            return None
        symbols  = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$",
                     "AUD": "A$", "SEK": "", "NOK": "", "DKK": ""}
        sym = symbols.get(currency, "")
        formatted = f"{amount / 100:,.2f}"
        if sym:
            return f"{sym}{formatted}"
        return f"{formatted} {currency[:8]}"
    if _is_number(price_val):
        return f"${price_val:,.2f}"
    return None


def _is_unpriced(price_val):
    """True for a Money value of zero, which the store uses for "no price yet".

    Announced-but-unreleased products carry amount 0, and rendering that as
    "$0.00" reads as free rather than unpriced. Nothing in this store is
    legitimately free, so treating zero as absent is the safer reading.
    """
    if isinstance(price_val, dict):
        return not price_val.get("amount")
    if isinstance(price_val, (int, float)):
        return not price_val
    return False


def price_amount(text):
    """A price as _format_price writes it, as a number ("C$1,299.00" -> 1299.0),
    or None."""
    try:
        return float(re.sub(r"[^\d.]", "", text))
    except (TypeError, ValueError):
        return None


def _same_currency(a, b):
    return re.sub(r"[\d.,\s]", "", a) == re.sub(r"[\d.,\s]", "", b)


def get_price(product):
    """Extract the display price from a product dict, or None if unpriced.

    displayPrice can be a Money dict like {'amount': 39900, 'currency': 'USD'}
    or a plain number or string.
    """
    for v in _variants(product):
        price = v.get("displayPrice") or v.get("price")
        if price is not None and not _is_unpriced(price):
            text = _format_price(price)
            if text:
                return text
    return None


def price_value(product):
    """The first price as a number of cents, for sorting; None if unpriced.
    Only numbers count: a text amount can't be compared with the others."""
    for v in _variants(product):
        price = v.get("displayPrice") or v.get("price")
        if isinstance(price, dict):
            price = price.get("amount")
            if _is_number(price) and price:
                return price
        elif _is_number(price) and price:
            return price * 100
    return None


def product_title(product):
    """A product's name as text: its title, else its slug. Read from the store
    as it comes, so a null or missing title can't fail a list of them."""
    if not isinstance(product, dict):
        return ""
    title = product.get("title")
    if title is None or title == "":
        title = product.get("slug") or ""
    return str(title)


def is_coming_soon(product):
    """True when a product is announced but not yet purchasable.

    Distinct from sold out: a ComingSoon item has never been on sale, so
    "back ~" and "sold out for" would both be wrong.
    """
    statuses = {v.get("status") for v in _variants(product)}
    return bool(statuses) and "Available" not in statuses and "ComingSoon" in statuses


# ── Restock estimates ────────────────────────────────────────────────────────
#
# The store already sends these on every catalog fetch and the app used to
# discard them. Roughly a third of out-of-stock products carry a restock date
# and half carry a sell-out timestamp, which is exactly what a restock watcher
# wants on screen.

def _parse_ts(value):
    """Parse an ISO timestamp or date, returning an aware datetime or None.

    The Z is replaced: Python 3.10's fromisoformat rejected it.
    A bare date ("2026-10-01") is that calendar day wherever you are, so it
    becomes noon UTC: any time zone's local date is then the same day.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if "T" not in value and " " not in value.strip():
        return dt.replace(hour=12, tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _local_now():
    return datetime.now().astimezone()


def get_restock_eta(product):
    """Earliest restock estimate across variants, as a datetime, or None."""
    etas = [_parse_ts(v.get("restockEtaAt")) for v in _variants(product)]
    etas = [d for d in etas if d]
    return min(etas) if etas else None


def get_sold_out_at(product):
    """Most recent sell-out timestamp across variants, as a datetime, or None."""
    vals = [_parse_ts(v.get("soldOutAt")) for v in _variants(product)]
    vals = [d for d in vals if d]
    return max(vals) if vals else None


def _plural(n, unit):
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def describe_restock(product, now=None):
    """Short human phrase for a product's restock estimate, or None.

    e.g. "back ~7 Sep", "back ~tomorrow", "restock date passed".
    """
    eta = get_restock_eta(product)
    if eta is None:
        return None
    # Calendar days where you are: in UTC, an evening in the Americas is
    # already tomorrow, and "back ~tomorrow" would mean the day after.
    now = now or _local_now()
    eta = eta.astimezone(now.tzinfo)
    days = (eta.date() - now.date()).days
    if days < 0:
        return "restock date passed"
    if days == 0:
        return "back ~today"
    if days == 1:
        return "back ~tomorrow"
    if days <= 14:
        return f"back ~{_plural(days, 'day')}"
    return f"back ~{eta.strftime('%d %b').lstrip('0')}"


def describe_sold_out_for(product, now=None):
    """Short human phrase for how long a product has been sold out, or None."""
    since = get_sold_out_at(product)
    if since is None:
        return None
    now = now or _local_now()
    since = since.astimezone(now.tzinfo)
    # Calendar days, not elapsed 24-hour periods: something that sold out on
    # the 6th reads as "13 days" on the 19th, which is how people count, and
    # it matches describe_restock.
    days = (now.date() - since.date()).days
    if days < 0:
        return None
    if days == 0:
        return "sold out today"
    if days == 1:
        return "sold out 1 day"
    if days < 60:
        return f"sold out {days} days"
    return f"sold out {_plural(days // 30, 'month')}"


def _data_url(build_id, path):
    return f"{STORE_BASE}/_next/data/{build_id}{path}.json"


def _redirect_path(target, slug):
    """Normalise a redirect target to a /<region>/<...> data path."""
    path = urlsplit(target).path.split("?")[0].removesuffix(".json")
    if path.rstrip("/").endswith("/404"):
        raise ProductNotFound(f"{slug}: no longer on the store")
    return path


def _fetch_product_page(build_id, slug, region="us"):
    """Return the pageProps of a product page, following Next.js data redirects.

    The store answers /products/<slug>.json for roughly half the catalog with
    HTTP 200 and a body of {"pageProps": {"__N_REDIRECT": "<canonical path>"}},
    and occasionally with a bare 307 carrying no Location header. Both mean the
    product lives under a category/collection path, not at the bare slug.
    """
    path = f"/{STORE_REGIONS[region]['path']}/products/{slug}"
    for _ in range(MAX_REDIRECTS):
        r = get_session().get(_data_url(build_id, path), timeout=15)

        if r.status_code in (301, 302, 303, 307, 308):
            # Next.js signals its own redirects out of band; a plain 307 here
            # usually carries x-nextjs-redirect rather than Location.
            loc = r.headers.get("Location") or r.headers.get("x-nextjs-redirect")
            if not loc:
                raise StoreError(
                    f"{slug}: store returned {r.status_code} with no redirect target")
            path = _redirect_path(loc, slug)
            continue

        r.raise_for_status()
        # Read as it comes: a page with no object here has no product, the
        # same as one without the key. (An AttributeError here was taken for
        # a network failure and retried, with sleeps, every cycle.)
        data = r.json()
        page_props = data.get("pageProps") if isinstance(data, dict) else None
        if not isinstance(page_props, dict):
            page_props = {}

        target = page_props.get("__N_REDIRECT")
        if target:
            path = _redirect_path(target, slug)
            continue

        return page_props

    raise StoreError(f"{slug}: too many redirects (>{MAX_REDIRECTS})")


def _find_product(page_props, slug):
    """Pick the requested product out of a product page.

    A product page carries its whole collection plus related/upsell products.
    Matching must be by identity - the previous implementation walked the JSON
    for the first "variants" key it could find, which on a page like ua-g2
    returns ua-g3's variants and reports the wrong product's stock.
    """
    if not isinstance(page_props, dict):
        return None
    collection = page_props.get("collection")
    candidates = _objects(collection.get("products") if isinstance(collection, dict) else None)
    for key in ("product", "currentProduct"):
        if isinstance(page_props.get(key), dict):
            candidates.append(page_props[key])

    for p in candidates:
        if p.get("slug") == slug:
            return p

    for p in candidates:                       # renamed product
        # A list only: `in` on a string would match any slug inside it.
        historical = p.get("historicalSlugs")
        if isinstance(historical, list) and slug in historical:
            return p

    # Last resort. The page was reached by requesting this slug and following
    # the store's own redirects, so whatever it considers "current" is what the
    # slug resolves to. Checked last so an exact slug match always wins.
    current_id = page_props.get("currentProductId")
    if current_id:
        for p in candidates:
            if p.get("id") == current_id:
                return p
    return None


def check_slug(build_id, slug, region="us", retries=3):
    """Check a single product slug. Returns (in_stock: bool, price: str|None).

    Raises ProductNotFound if the slug no longer resolves, and StoreError if
    the store cannot be reached. It never reports a fabricated "out of stock" -
    that silently defeats the entire point of a stock watcher.
    """
    if not is_valid_slug(slug):
        raise ProductNotFound(f"{slug!r}: not a store product id")
    last_err = None
    for attempt in range(retries):
        try:
            page_props = _fetch_product_page(build_id, slug, region)
            product = _find_product(page_props, slug)
            if product is None:
                raise ProductNotFound(f"{slug}: not present on its own page")
            return is_available(product), get_price(product)

        except ProductNotFound:
            raise
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                # Either the buildId rotated or the product is gone. Ask for a
                # fresh buildId and compare: only a genuine rotation justifies
                # discarding the cached id that every other item is using.
                fresh = get_build_id(region, force=True)
                if fresh != build_id:
                    build_id = fresh
                    last_err = e
                    continue
                raise ProductNotFound(f"{slug}: no longer on the store") from e
            last_err = e
        except Exception as e:
            last_err = e

        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    raise StoreError(f"{slug}: failed after {retries} attempts") from last_err




