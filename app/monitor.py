"""Shared cross-process state, notification delivery, and bounded service cycles."""
import fcntl
import hashlib
import json
import math
import time
from contextlib import contextmanager
from datetime import datetime, timezone
import requests
import unifi_core as core
import store_api as store

@contextmanager
def data_lock():
    core.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with (core.STATE_FILE.parent / '.state.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield

def read(path, default):
    try: return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError): return default

def identity(settings):
    return settings.get('region','us'), settings.get('region_revision','')

def notify(title, message, settings=None, click_url=None):
    settings = settings or core.load_settings()
    server = str(settings.get('ntfy_url','')).rstrip('/')
    topic = str(settings.get('ntfy_topic',''))
    if not server or not topic: return False
    headers = {'Title': requests.utils.requote_uri(str(title)), 'Priority': str(settings.get('ntfy_priority','high')), 'Tags':'bell,white_check_mark'}
    # RFC 2047 avoids Latin-1 header errors for Unicode product names.
    from email.header import Header
    headers['Title'] = Header(str(title), 'utf-8').encode()
    if settings.get('ntfy_token'): headers['Authorization'] = 'Bearer ' + settings['ntfy_token']
    if click_url: headers['Click'] = click_url
    for attempt in range(3):
        try:
            response = requests.post(server+'/'+topic, data=message.encode('utf-8'), headers=headers, timeout=10, allow_redirects=False)
            if 200 <= response.status_code < 300: return True
            if response.status_code != 429 and response.status_code < 500:
                raise RuntimeError('ntfy rejected notification (HTTP %s)' % response.status_code)
            try: wait = float(response.headers.get('Retry-After',0))
            except (TypeError,ValueError): wait = 0
            if not math.isfinite(wait): wait=0
        except (requests.Timeout, requests.ConnectionError):
            wait=0
        except requests.RequestException:
            raise RuntimeError('ntfy connection failed; check server and credentials') from None
        if attempt < 2: time.sleep(min(15,max(2**attempt,wait)))
    raise RuntimeError('ntfy delivery failed after three attempts')

def record(slug, product, settings):
    """Commit only current-region results. Atomic replace alone cannot prevent races."""
    with data_lock():
        current=core.load_settings()
        if identity(current)!=identity(settings): return False
        items=core.load_watched()
        item=next((i for i in items if i.get('slug')==slug),None)
        if item is None: return False
        state=read(core.STATE_FILE,{})
        previous=state.get(slug,{})
        now=datetime.now(timezone.utc).isoformat()
        stock=store.is_available(product); price=store.get_price(product)
        entry={'in_stock':stock,'price':price,'last_checked':now,'coming_soon':store.is_coming_soon(product),
               'restock_note':store.describe_restock(product),'sold_out_note':store.describe_sold_out_for(product)}
        state[slug]=entry
        history=read(core.HISTORY_FILE,{'events':[],'stats':{}})
        stats=read(core.STATE_FILE.parent/'check_counts.json',history.setdefault('stats',{}))
        history['stats']=stats;stats['total_checks']=int(stats.get('total_checks',0))+1
        core.atomic_json(core.STATE_FILE.parent/'check_counts.json',stats)
        changed=previous.get('in_stock') is not None and previous.get('in_stock')!=stock
        price_changed=bool(price and previous.get('price') and price!=previous['price'])
        if not previous or changed or price_changed:
            history.setdefault('events',[]).append(dict(entry,ts=now,slug=slug,title=str(item.get('title') or slug),price_was=previous.get('price')))
            history['events']=history['events'][-2000:]
        should_save=not previous or changed or price_changed or time.time()-history.get('counts_saved_at',0)>=600
        if should_save:
            history['counts_saved_at']=time.time();core.atomic_json(core.HISTORY_FILE,history)
        core.atomic_json(core.STATE_FILE,state)
        if price and item.get('price')!=price:
            item['price']=price;core.save_watched(items)
        wants=current.get('ntfy_enabled',True) and not item.get('muted') and (not current.get('alert_favourites_only') or item.get('favourite'))
        alerts=[]
        if wants and stock and previous.get('in_stock') is False:
            alerts.append(('IN STOCK: '+str(item['title']),str(item['title'])+' is now available'+(' ('+price+')' if price else '')))
        if wants and current.get('price_drop_alerts') and price_changed and store._same_currency(price,previous['price']):
            old,new=store.price_amount(previous['price']),store.price_amount(price)
            if old is not None and new is not None and new<old: alerts.append(('PRICE DROP: '+str(item['title']),f"Price dropped from {previous['price']} to {price}"))
        if alerts: stats['in_stock_alerts']=int(stats.get('in_stock_alerts',0))+int(changed and stock)
        core.atomic_json(core.STATE_FILE.parent/'check_counts.json',stats)
        if alerts: core.atomic_json(core.HISTORY_FILE,history)
        # Keep the settings/state transaction locked through dispatch so a region
        # switch cannot complete and then receive an old-region notification.
        for title,message in alerts:
            notify(title,message,current,store.product_url(slug,current.get('region','us')))
        return True

def check_one(slug):
    if not store.is_valid_slug(slug): raise ValueError('Invalid product slug')
    settings=core.load_settings();region=settings.get('region','us')
    product=store._find_product(store._fetch_product_page(store.get_build_id(region),slug,region),slug)
    if product is None: raise store.ProductNotFound('Product not found in this regional store')
    if not record(slug,product,settings): raise RuntimeError('Settings changed during the check; result discarded')
    return store.is_available(product),store.get_price(product)

def cycle():
    settings=core.load_settings();region=settings.get('region','us')
    backoff_path=core.STATE_FILE.parent/'store_backoff.json'
    backoff=read(backoff_path,{})
    if backoff.get('region')==region and time.time()<backoff.get('until',0):
        print('Store backoff active; next timer run will retry when due.');return
    items=core.load_watched()
    if not items: print('No watched items; add products in the web GUI.');return
    errors=[]
    try:
        products={p['slug']:p for p in core.fetch_all_products(region,error_cb=lambda cat,e:errors.append(e))}
        refused=any(store.is_refusal(e) for e in errors)
        failures=0
        for item in items:
            slug=item['slug'];product=products.get(slug)
            if product is None:
                saved=read(core.STATE_FILE,{}).get(slug,{})
                if time.time()<saved.get('retry_after',0): continue
                if refused: continue
                try:
                    product=store._find_product(store._fetch_product_page(store.get_build_id(region),slug,region),slug)
                except Exception as error:
                    if store.is_refusal(error): refused=True
                    failures+=1
                    print('Product check unavailable: '+slug);continue
                if product is None:
                    with data_lock():
                        if identity(core.load_settings())==identity(settings):
                            state=read(core.STATE_FILE,{})
                            state.setdefault(slug,{}).update(check_error='Product unavailable or delisted',retry_after=time.time()+21600)
                            core.atomic_json(core.STATE_FILE,state)
                    continue
            if product is not None:
                record(slug,product,settings)
                print(('IN STOCK ' if store.is_available(product) else 'out of stock ')+str(item.get('title') or slug))
        if refused: raise store.StoreError('Store rate limited part of this check')
        if failures: raise store.StoreError('Some watched products could not be checked')
        core.atomic_json(backoff_path,{'region':region,'until':0,'delay':0})
    except Exception:
        delay=min(1800,max(120,int(backoff.get('delay',0))*2))
        core.atomic_json(backoff_path,{'region':region,'until':time.time()+delay,'delay':delay})
        raise
