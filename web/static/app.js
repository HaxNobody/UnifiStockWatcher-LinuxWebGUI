const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const app = {watched: [], settings: {}, status: {}, history: {events: [], stats: {}}, regions: {}, catalog: [], selected: new Set(), version: '', menuSlug: null};

async function api(path, options = {}) {
  const opts = {...options, headers: {...(options.headers || {})}};
  if (opts.body && typeof opts.body !== 'string') { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.body); }
  const response = await fetch(path, opts);
  const data = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function toast(message) { const element = $('#toast'); element.textContent = message; element.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => element.classList.remove('show'), 3500); }
function escapeHtml(value = '') { const element = document.createElement('div'); element.textContent = String(value); return element.innerHTML; }
function productUrl(slug) { const paths = {us: 'us/en', eu: 'eu/en', uk: 'uk/en', ca: 'ca/en'}; return `https://store.ui.com/${paths[app.settings.region || 'us'] || paths.us}/products/${encodeURIComponent(slug)}`; }
function parseTime(value) { if (!value) return null; let normalized = String(value).trim(); if (/^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(normalized) && !/(Z|[+-]\d\d:\d\d)$/.test(normalized)) normalized += 'Z'; const date = new Date(normalized); return Number.isNaN(date.valueOf()) ? null : date; }
function niceTime(value) { const date = parseTime(value); return date ? date.toLocaleString([], {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : value || 'not checked yet'; }
function relativeTime(value) { const date = parseTime(value); if (!date) return 'never'; const seconds = Math.max(0, Math.round((Date.now() - date.valueOf()) / 1000)); if (seconds < 60) return `${seconds} second${seconds === 1 ? '' : 's'} ago`; const minutes = Math.round(seconds / 60); if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`; const hours = Math.round(minutes / 60); if (hours < 48) return `${hours} hour${hours === 1 ? '' : 's'} ago`; const days = Math.round(hours / 24); return `${days} day${days === 1 ? '' : 's'} ago`; }
function intervalLabel(seconds) { seconds = Number(seconds); if (seconds < 60) return `${seconds} seconds`; if (seconds < 3600) return `${seconds / 60} minute${seconds === 60 ? '' : 's'}`; return `${seconds / 3600} hour${seconds === 3600 ? '' : 's'}`; }

function setTheme() {
  const root = document.documentElement; const theme = app.settings.theme || 'system';
  if (theme === 'system') root.removeAttribute('data-theme'); else root.dataset.theme = theme;
  document.body.classList.toggle('accent-green', app.settings.accent === 'green');
  document.body.classList.toggle('accent-purple', app.settings.accent === 'purple');
  document.body.classList.toggle('accent-orange', app.settings.accent === 'orange');
  document.body.classList.toggle('text-large', app.settings.text_size === 'large');
}

function renderStatus() {
  const status = app.status; const watching = !!status.watching;
  const problem = watching && (status.stale || (status.service_result && status.service_result !== 'success'));
  const dot = $('#status-dot'); dot.classList.toggle('on', watching && !problem); dot.classList.toggle('problem', problem); dot.classList.toggle('error', !!status.error);
  $('#status-label').textContent = status.error ? 'Status unavailable' : problem ? 'Attention' : watching ? 'Watching' : 'Stopped';
  $('#next-check').textContent = watching ? `every ${intervalLabel(status.interval || app.settings.poll_interval)}` : '';
  $('#toggle-watch').textContent = watching ? 'Stop watching' : 'Start watching';
  $('#store-summary').textContent = `${app.regions[app.settings.region] || 'United States'} store  ·  every ${intervalLabel(app.settings.poll_interval)}`;
  $('#autostart').checked = status.timer_enabled ? status.timer_enabled === 'enabled' : watching;
  $('#notification-setup-banner').classList.toggle('hidden', !!String(app.settings.ntfy_topic || '').trim());
  const banner = $('#health-banner');
  if (status.error || status.stale || (status.service_result && status.service_result !== 'success')) {
    banner.classList.remove('hidden');
    if (status.error) { $('#health-title').textContent = 'The service status could not be read.'; $('#health-detail').textContent = status.error; }
    else if (status.stale) { $('#health-title').textContent = 'The stored stock result is older than expected.'; $('#health-detail').textContent = `Last successful product check: ${relativeTime(status.last_data_check)} (${niceTime(status.last_data_check)}).`; }
    else { $('#health-title').textContent = 'The most recent stock-check service run failed.'; $('#health-detail').textContent = `systemd result: ${status.service_result}`; }
  } else banner.classList.add('hidden');
}

function groupHtml(title, items) { if (!items.length) return ''; const note = title === 'Watching' && app.settings.alert_favourites_only ? ' <span class="group-note">· alerts only for favorites</span>' : ''; return `<div class="group-title">${title.toUpperCase()} · ${items.length}${note}</div>${items.map(cardHtml).join('')}`; }
function cardHtml(item) {
  const status = item.in_stock === true ? 'in' : item.in_stock === false ? 'out' : 'unknown'; const label = item.check_error ? 'UNAVAILABLE' : item.coming_soon ? 'COMING SOON' : status === 'in' ? 'IN STOCK' : status === 'out' ? 'SOLD OUT' : 'UNKNOWN'; const checked = item.last_checked ? `${relativeTime(item.last_checked)} · ${niceTime(item.last_checked)}` : 'not checked yet'; const url = productUrl(item.slug);
  return `<article class="watch-card ${status === 'in' ? 'in-stock' : ''}" data-card-slug="${escapeHtml(item.slug)}"><button class="star ${item.favourite ? 'on' : ''}" data-slug="${escapeHtml(item.slug)}" title="${item.favourite ? 'Remove from' : 'Add to'} favorites">${item.favourite ? '★' : '☆'}</button><div><a class="item-title item-link" href="${url}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a>${item.muted ? ' <span title="Alerts muted">🔕</span>' : ''}<div class="item-note" title="${escapeHtml(niceTime(item.last_checked))}">checked ${escapeHtml(checked)}</div><div class="item-note">${escapeHtml([item.check_error,item.restock_note,item.sold_out_note].filter(Boolean).join(" · "))}</div></div><div class="item-price">${escapeHtml(item.price || '')}</div><div class="status-pill ${status}">${label}</div>${status === 'in' ? `<a class="store-button" target="_blank" rel="noopener" href="${url}">Open in store</a>` : '<span></span>'}<button class="card-menu" data-slug="${escapeHtml(item.slug)}" aria-label="More actions for ${escapeHtml(item.title)}" aria-haspopup="menu">•••</button></article>`;
}
function renderWatch() {
  const content = $('#watch-content'); const favorites = app.watched.filter(item => item.favourite); const watching = app.watched.filter(item => !item.favourite);
  if (!app.watched.length) { content.innerHTML = '<div class="empty"><h2>Nothing on your watch list</h2><p>Choose Add products… to pick products from the UniFi catalog.</p></div>'; return; }
  content.innerHTML = groupHtml('Favorites', favorites) + groupHtml('Watching', watching);
  $$('.star', content).forEach(button => button.addEventListener('click', () => toggleField(button.dataset.slug, 'favourite')));
  $$('.card-menu', content).forEach(button => button.addEventListener('click', event => openItemMenu(button.dataset.slug, event.currentTarget)));
  $$('[data-card-slug]', content).forEach(card => card.addEventListener('dblclick', event => { if (!event.target.closest('button,a')) window.open(productUrl(card.dataset.cardSlug), '_blank', 'noopener'); }));
}
async function saveWatch() { await api('/api/watchlist', {method: 'PUT', body: app.watched}); renderWatch(); }
async function toggleField(slug, field) { const item = app.watched.find(entry => entry.slug === slug); if (!item) return; item[field] = !item[field]; try { await saveWatch(); toast(field === 'muted' ? (item[field] ? 'Alerts muted' : 'Alerts unmuted') : (item[field] ? 'Added to favorites' : 'Removed from favorites')); } catch (error) { toast(error.message); } }

function closeMenus() { $('#app-menu').classList.add('hidden'); $('#more-button').setAttribute('aria-expanded', 'false'); $('#item-menu').classList.add('hidden'); app.menuSlug = null; }
function openItemMenu(slug, button) {
  const item = app.watched.find(entry => entry.slug === slug); if (!item) return; closeMenus(); app.menuSlug = slug;
  const menu = $('#item-menu'); $('[data-item-action="open"]', menu).href = productUrl(slug); $('[data-item-action="favorite"]', menu).textContent = item.favourite ? 'Remove from favorites' : 'Add to favorites'; $('[data-item-action="mute"]', menu).textContent = item.muted ? 'Unmute alerts' : 'Mute alerts'; menu.classList.remove('hidden');
  const rect = button.getBoundingClientRect(); const width = menu.offsetWidth; const height = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`; menu.style.top = `${rect.bottom + height > window.innerHeight ? Math.max(8, rect.top - height) : rect.bottom}px`;
}
async function checkItem(slug) {
  closeMenus(); const item = app.watched.find(entry => entry.slug === slug); if (!item) return; toast(`Checking ${item.title}…`);
  try { const result = await api(`/api/items/${encodeURIComponent(slug)}/check`, {method: 'POST'}); await refresh({preserveSettings: true}); toast(`${item.title}: ${result.in_stock ? 'in stock' : 'sold out'}${result.price ? ` · ${result.price}` : ''}`); } catch (error) { toast(`Check failed: ${error.message}`); }
}
async function itemAction(action) {
  const slug = app.menuSlug; const item = app.watched.find(entry => entry.slug === slug); if (!item) return closeMenus();
  if (action === 'check') return checkItem(slug);
  if (action === 'copy') { const url = productUrl(slug); try { await navigator.clipboard.writeText(url); } catch { const area = document.createElement('textarea'); area.value = url; document.body.append(area); area.select(); document.execCommand('copy'); area.remove(); } closeMenus(); toast('Store link copied'); return; }
  if (action === 'favorite') { closeMenus(); return toggleField(slug, 'favourite'); }
  if (action === 'mute') { closeMenus(); return toggleField(slug, 'muted'); }
  if (action === 'remove') { closeMenus(); if (confirm(`Remove ${item.title} from your watch list?`)) { app.watched = app.watched.filter(entry => entry.slug !== slug); try { await saveWatch(); toast('Removed from watch list'); } catch (error) { toast(error.message); } } }
}

function annotatedEvents() { const previous = new Map(); return (app.history.events || []).map(event => { const before = previous.get(event.slug); previous.set(event.slug, event.in_stock); return {...event, initial: before === undefined, changed: before !== undefined && before !== event.in_stock}; }); }
function renderHistory() {
  const watchedSlugs = new Set(app.watched.map(item => item.slug)); const only = $('#watched-only').checked; const filter = $('#history-filter').value.trim().toLowerCase(); const events = annotatedEvents().filter(event => (!only || watchedSlugs.has(event.slug)) && (!filter || String(event.title).toLowerCase().includes(filter))).reverse();
  $('#history-body').innerHTML = events.map(event => { const label = event.price_was && event.price && event.price_was !== event.price ? `Price: ${event.price_was} → ${event.price}` : event.initial ? `Added: ${event.in_stock ? 'in stock' : 'sold out'}` : event.changed ? (event.in_stock ? '▲ In stock' : '▼ Sold out') : `Checked: ${event.in_stock ? 'in stock' : 'sold out'}`; return `<tr><td title="${escapeHtml(niceTime(event.ts))}">${escapeHtml(relativeTime(event.ts))}</td><td><a class="item-link" href="${productUrl(event.slug)}" target="_blank" rel="noopener">${escapeHtml(event.title)}</a></td><td class="change-cell ${event.changed ? (event.in_stock ? 'in' : 'out') : ''}">${label}</td><td>${escapeHtml(event.price || '')}</td></tr>`; }).join('');
  const stats = app.history.stats || {}; $('#history-stats').textContent = `${stats.total_checks || events.length} checks of watched products  ·  ${stats.in_stock_alerts || 0} in-stock alert${stats.in_stock_alerts === 1 ? '' : 's'}`;
}
function renderChanges() {
  const events = annotatedEvents().filter(event => event.changed).slice(-100).reverse();
  $('#changes-feed').innerHTML = events.length ? events.map(event => `<div class="change-row"><span title="${escapeHtml(niceTime(event.ts))}">${escapeHtml(parseTime(event.ts)?.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) || '')}</span><span class="${event.in_stock ? 'change-in' : 'change-out'}">${event.in_stock ? '▲ In stock' : '▼ Sold out'}</span><span><a class="item-link" href="${productUrl(event.slug)}" target="_blank" rel="noopener">${escapeHtml(event.title)}</a></span><span>${escapeHtml(event.price || '')}</span></div>`).join('') : '<span class="muted-message">No recorded stock transitions yet.</span>';
  $('#session-count').textContent = `${events.length} recent change${events.length === 1 ? '' : 's'}`;
}

function fillSettings() {
  const settings = app.settings; $('#poll-interval').value = String(settings.poll_interval || 120);
  if (!$('#poll-interval').value) { const option = document.createElement('option'); option.value = settings.poll_interval; option.textContent = intervalLabel(settings.poll_interval); $('#poll-interval').append(option); $('#poll-interval').value = settings.poll_interval; }
  $('#region').innerHTML = Object.entries(app.regions).map(([key, value]) => `<option value="${key}">${escapeHtml(value)}</option>`).join(''); $('#region').value = settings.region;
  $('#ntfy-enabled').checked = settings.ntfy_enabled !== false; $('#price-drops').checked = !!settings.price_drop_alerts; $('#favorites-only').checked = !!settings.alert_favourites_only; $('#ntfy-url').value = settings.ntfy_url || ''; $('#ntfy-topic').value = settings.ntfy_topic || ''; $('#ntfy-priority').value = settings.ntfy_priority || 'high'; $('#theme').value = settings.theme || 'system'; $('#accent').value = settings.accent || 'blue'; $('#text-size').value = settings.text_size || 'normal';
  $('#ntfy-token').value = ''; $('#clear-ntfy-token').checked = false;
  $('#ntfy-token-status').textContent = settings.has_ntfy_token ? 'A token is saved. Leave blank to keep it.' : 'No token saved. Optional for protected topics.';
  renderNotificationSetup();
  if (!String(settings.ntfy_topic || '').trim()) $('#ntfy-setup-guide').open = true;
}
function collectSettings() { return {...app.settings, poll_interval: Number($('#poll-interval').value), region: $('#region').value, ntfy_token: $('#ntfy-token').value.trim(), clear_ntfy_token: $('#clear-ntfy-token').checked, ntfy_enabled: $('#ntfy-enabled').checked, price_drop_alerts: $('#price-drops').checked, alert_favourites_only: $('#favorites-only').checked, ntfy_url: $('#ntfy-url').value.trim(), ntfy_topic: $('#ntfy-topic').value.trim(), ntfy_priority: $('#ntfy-priority').value, theme: $('#theme').value, accent: $('#accent').value, text_size: $('#text-size').value}; }

function configureNotificationSettings() {
  const style = document.createElement('style');
  style.textContent = `
    .notification-intro, .notification-guide { padding: 16px 24px; border-bottom: 1px solid var(--border); }
    .notification-intro p { margin: 0 0 8px; }
    .notification-status { color: var(--muted); font-size: 13px; }
    .notification-guide summary { cursor: pointer; font-weight: 600; }
    .notification-guide li { padding: 6px 0 10px; }
    .notification-guide p { line-height: 1.6; }
    .notification-guide a { color: var(--accent); }
    .notification-note { padding: 12px; background: var(--surface2); border-left: 3px solid var(--accent); }
    .notification-subscription { display: grid; grid-template-columns: 65px minmax(0,1fr); gap: 6px 12px; padding: 12px; background: var(--surface2); }
    .notification-subscription dt { font-weight: 600; }
    .notification-subscription dd { margin: 0; overflow-wrap: anywhere; font-family: Consolas,monospace; }
    .notification-topic-controls { display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
    #notification-settings { scroll-margin-top: 20px; }
    #ntfy-test-result { max-width: 620px; overflow-wrap: anywhere; }
    @media(max-width:760px) { .notification-intro,.notification-guide { padding: 14px 16px; } .notification-topic-controls { width: 100%; } .notification-guide ol { padding-left: 22px; } }
  `;
  document.head.append(style);
}
function notificationValues() {
  return {server: $('#ntfy-url').value.trim().replace(/\/+$/, ''), topic: $('#ntfy-topic').value.trim()};
}
function renderNotificationSetup() {
  const {server, topic} = notificationValues();
  $('#ntfy-guide-server').textContent = server || 'Enter an ntfy server below';
  $('#ntfy-guide-topic').textContent = topic || 'Choose or generate a topic below';
  $('#copy-ntfy-details').disabled = !server || !topic;
  const saved = server === String(app.settings.ntfy_url || '').replace(/\/+$/, '') && topic === String(app.settings.ntfy_topic || '');
  $('#notification-setup-status').textContent = !topic ? 'Not configured yet — start with the setup guide.' : !saved ? 'Changes not saved. Subscribe on your phone, then save and test.' : `Server and topic saved. ${app.settings.ntfy_enabled === false ? 'Automatic alerts are off. ' : ''}Send a test to confirm your phone receives messages.`;
}
function validateNotificationSettings(requireTopic = false) {
  const {server, topic} = notificationValues();
  if (server) {
    let url; try { url = new URL(server); } catch { throw new Error('Enter a complete ntfy server address, such as https://ntfy.sh.'); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Use an http or https server address without a password, token, query, or fragment.');
  }
  if ((requireTopic || topic) && (!server || !topic)) throw new Error('Enter the ntfy server and choose a topic first.');
  if (topic && !/^[A-Za-z0-9_-]{1,64}$/.test(topic)) throw new Error('Use 1–64 letters, numbers, underscores, or hyphens for the topic, not a full URL.');
}
async function saveSettingsFromForm() {
  validateNotificationSettings();
  const data = await api('/api/settings', {method: 'PUT', body: collectSettings()});
  app.settings = data.settings; $('#ntfy-token').value=''; $('#clear-ntfy-token').checked=false; $('#ntfy-token-status').textContent=data.settings.has_ntfy_token?'A token is saved. Leave blank to keep it.':'No token saved.'; app.status = data.status; setTheme(); renderStatus(); renderWatch(); renderNotificationSetup();
  return data;
}
async function testNotificationSetup() {
  const button = $('#test-alert'); const save = $('#save-settings');
  let settingsSaved = false;
  button.disabled = true; save.disabled = true; $('#ntfy-test-result').textContent = '';
  try {
    validateNotificationSettings(true); $('#save-status').textContent = 'Saving…';
    await saveSettingsFromForm(); settingsSaved = true; $('#save-status').textContent = 'Saved';
    $('#ntfy-test-result').textContent = 'Sending test to ntfy…';
    await api('/api/action/test', {method: 'POST'});
    $('#ntfy-test-result').textContent = 'ntfy accepted the test. Check your phone to confirm delivery.';
    toast('Test accepted by ntfy — check your phone');
  } catch (error) {
    if (!settingsSaved) $('#save-status').textContent = 'Not saved';
    $('#ntfy-test-result').textContent = `Test not completed: ${error.message}`; toast(error.message);
  } finally { button.disabled = false; save.disabled = false; }
}
async function copyNotificationDetails() {
  const {server, topic} = notificationValues();
  const text = `ntfy server: ${server}\nTopic: ${topic}`;
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const input = document.createElement('textarea'); input.value = text; input.style.cssText = 'position:fixed;left:-9999px;top:0'; document.body.append(input);
      try { input.select(); if (!document.execCommand('copy')) throw new Error('Clipboard unavailable'); } finally { input.remove(); }
    }
    toast('Subscription details copied');
  } catch { $('#ntfy-topic').focus(); $('#ntfy-topic').select(); toast('Clipboard unavailable. Copy the server and topic shown in the guide.'); }
}

async function openCatalog() { const dialog = $('#catalog-dialog'); dialog.showModal(); $('#catalog-body').innerHTML = '<tr><td colspan="5">Loading the live catalog…</td></tr>'; try { app.catalog = await api('/api/catalog'); buildCategories(); filterCatalog(); } catch (error) { $('#catalog-body').innerHTML = `<tr><td colspan="5">${escapeHtml(error.message)}</td></tr>`; } }
function buildCategories() { const categories = [...new Set(app.catalog.map(item => item.category).filter(Boolean))].sort(); $('#catalog-category').innerHTML = '<option value="">All categories</option>' + categories.map(category => `<option>${escapeHtml(category)}</option>`).join(''); }
function filterCatalog() {
  const query = $('#catalog-search').value.toLowerCase(); const category = $('#catalog-category').value; const includeStock = $('#include-stock').checked; const watched = new Set(app.watched.map(item => item.slug)); const rows = app.catalog.filter(item => (includeStock || !item.in_stock) && (!query || item.title.toLowerCase().includes(query)) && (!category || item.category === category));
  $('#catalog-count').textContent = `${rows.length} shown · ${rows.filter(item => !item.in_stock).length} out of stock`;
  $('#catalog-body').innerHTML = rows.map(item => { const isWatched = watched.has(item.slug); return `<tr><td><input type="checkbox" data-slug="${escapeHtml(item.slug)}" ${isWatched ? 'checked disabled' : app.selected.has(item.slug) ? 'checked' : ''}></td><td class="catalog-product ${isWatched ? 'watched' : item.in_stock ? 'in-stock' : ''}"><a class="item-link" href="${productUrl(item.slug)}" target="_blank" rel="noopener">${escapeHtml(item.title)}</a></td><td>${escapeHtml(item.category || '')}</td><td>${escapeHtml(item.price || '')}</td><td class="catalog-status ${isWatched ? 'watched' : item.in_stock ? 'in' : ''}">${isWatched ? 'On your watch list' : item.coming_soon ? 'Coming soon' : item.in_stock ? 'In stock' : 'Sold out'}</td></tr>`; }).join('');
  $$('#catalog-body input:not(:disabled)').forEach(box => box.addEventListener('change', () => { box.checked ? app.selected.add(box.dataset.slug) : app.selected.delete(box.dataset.slug); selectedCount(); })); selectedCount();
}
function selectedCount() { $('#selected-count').textContent = `${app.selected.size} selected`; $('#confirm-add').textContent = app.selected.size ? `Add ${app.selected.size} to watch list` : 'Add to watch list'; $('#confirm-add').disabled = !app.selected.size; }
async function addSelected() { for (const slug of app.selected) { const item = app.catalog.find(entry => entry.slug === slug); if (item && !app.watched.some(entry => entry.slug === slug)) app.watched.push({...item, favourite: false, muted: false, added_at: new Date().toISOString()}); } try { await saveWatch(); app.selected.clear(); $('#catalog-dialog').close(); toast('Watch list updated'); } catch (error) { toast(error.message); } }

function showBottom(name) { $$('.bottom-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.bottom === name)); $('#changes-feed').classList.toggle('hidden', name !== 'changes'); $('#activity-feed').classList.toggle('hidden', name !== 'activity'); if (name === 'activity') loadLogs(); }
function showTab(name) { $$('.tab').forEach(tab => tab.classList.toggle('active', tab.dataset.tab === name)); $$('.view').forEach(view => view.classList.remove('active')); $(`#${name}-view`).classList.add('active'); if (name === 'history') renderHistory(); }
function formatActivityLogs(text) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit',
    hour12: true, timeZoneName: 'short'
  });
  return String(text).split(/\r?\n/).map(rawLine => {
    const line = rawLine.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '');
    // The control helper returns journalctl's short-iso format:
    // timestamp hostname source[pid]: message. Keep the source for diagnostics.
    const match = line.match(/^\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\s+\S+\s+(.*)$/);
    if (!match) {
      // Also support raw watcher output when a helper uses journalctl -o cat.
      const raw = line.match(/^\s*\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]\s*(.*)$/);
      const date = raw ? parseTime(raw[1]) : null;
      return date ? `${formatter.format(date)}  |  ${raw[2]}` : line;
    }
    const date = new Date(match[1]);
    if (Number.isNaN(date.valueOf())) return line;
    // Remove only the watcher's duplicate timestamp at the start of its message.
    // The journal timestamp above supplies the local date, time, and time zone.
    const message = match[2].replace(/^(python(?:3(?:\.\d+)?)?(?:\[\d+\])?:\s*)\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]\s*/, '$1');
    return `${formatter.format(date)}  |  ${message}`;
  }).join('\n');
}
function configureActivityPanel() {
  // Keep this update self-contained: no template or stylesheet replacement needed.
  if (!document.getElementById('activity-panel-update-styles')) {
    const style = document.createElement('style');
    style.id = 'activity-panel-update-styles';
    style.textContent = `
      #watch-view .watch-content { height: auto; max-height: 460px; padding-bottom: 16px; }
      #watch-view .bottom-panel {
        display: flex; flex-direction: column; height: 440px; min-height: 260px;
        max-height: 85vh; resize: vertical; overflow: hidden;
      }
      #watch-view .bottom-tabs { flex-shrink: 0; }
      #watch-view .bottom-panel .activity-feed {
        flex: 1 1 0; min-height: 0; height: auto; margin: 0;
        line-height: 1.65; overflow: auto; overflow-wrap: anywhere;
      }
      @media (max-width: 760px) {
        #watch-view .watch-content { max-height: 52vh; }
        #watch-view .bottom-panel { height: 380px; }
        #watch-view .bottom-tabs { gap: 4px; flex-wrap: wrap; height: auto; min-height: 40px; }
      }
    `;
    document.head.append(style);
  }
  const activityTab = document.querySelector('[data-bottom="activity"]');
  if (activityTab) {
    activityTab.textContent = 'Activity · local time';
    activityTab.title = 'Formatted journal timestamps in your browser time zone. Drag the panel’s bottom-right corner to resize it.';
  }
}
async function loadLogs() { try { const result = await api('/api/logs?lines=500'); $('#activity-feed').textContent = result.text ? formatActivityLogs(result.text) : 'No watcher activity is retained in the system journal.'; } catch (error) { $('#activity-feed').textContent = error.message; } }
async function checkAll() {
  const previous = app.status.last_data_check; $('#check-now').disabled = true;
  try { await api('/api/action/check', {method: 'POST'}); toast('Stock check started'); for (let attempt = 0; attempt < 60; attempt++) { await delay(2000); const data = await api('/api/bootstrap'); Object.assign(app, data); renderStatus(); renderWatch(); renderHistory(); renderChanges(); if (app.status.service_active !== 'active' && app.status.last_data_check && app.status.last_data_check !== previous) { toast(app.status.service_result === 'success' ? 'Stock check completed' : `Stock check ended: ${app.status.service_result}`); return; } } toast('The check is still running; see Activity for details.'); }
  catch (error) { toast(error.message); } finally { $('#check-now').disabled = false; }
}

function renderDiagnostics() {
  const status = app.status; $('#diag-timer').textContent = `${status.timer_enabled || 'unknown'}; ${status.timer_active || 'unknown'}; every ${intervalLabel(status.interval || app.settings.poll_interval)}`; $('#diag-last-trigger').textContent = status.last_trigger || 'Not reported by systemd'; $('#diag-next-run').textContent = status.next_run || 'Not scheduled'; $('#diag-last-run').textContent = status.last_run || 'Not reported by systemd'; $('#diag-last-data').textContent = status.last_data_check ? `${niceTime(status.last_data_check)} (${relativeTime(status.last_data_check)})` : 'No successful product check is stored'; $('#diag-result').textContent = status.service_result || status.error || 'Unknown'; $('#diag-version').textContent = app.version || 'Unknown';
}
function openDiagnostics() { closeMenus(); renderDiagnostics(); $('#diagnostics-dialog').showModal(); }
async function refresh({preserveSettings = false} = {}) { const data = await api('/api/bootstrap'); Object.assign(app, data); setTheme(); renderStatus(); renderWatch(); renderHistory(); renderChanges(); if (!preserveSettings) fillSettings(); renderDiagnostics(); if (!$('#activity-feed').classList.contains('hidden')) await loadLogs(); }

function bind() {
  $$('.tab').forEach(tab => tab.addEventListener('click', () => showTab(tab.dataset.tab))); $$('.bottom-tab').forEach(tab => tab.addEventListener('click', () => showBottom(tab.dataset.bottom)));
  $('#more-button').addEventListener('click', event => { event.stopPropagation(); const menu = $('#app-menu'); const opening = menu.classList.contains('hidden'); closeMenus(); menu.classList.toggle('hidden', !opening); $('#more-button').setAttribute('aria-expanded', String(opening)); });
  $$('[data-app-action]').forEach(control => control.addEventListener('click', () => { const action = control.dataset.appAction; closeMenus(); if (action === 'check') checkAll(); if (action === 'diagnostics') openDiagnostics(); if (action === 'logs') { showTab('watch'); showBottom('activity'); } }));
  $$('[data-item-action]').forEach(control => control.addEventListener('click', event => { if (control.tagName === 'A') closeMenus(); else { event.preventDefault(); itemAction(control.dataset.itemAction); } }));
  document.addEventListener('click', event => { if (!event.target.closest('.popup-menu,.card-menu,#more-button')) closeMenus(); }); document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMenus(); }); window.addEventListener('resize', closeMenus); window.addEventListener('scroll', closeMenus, true);
  $('#toggle-watch').addEventListener('click', async () => { try { const data = await api(`/api/action/${app.status.watching ? 'stop' : 'start'}`, {method: 'POST'}); app.status = data.status; renderStatus(); toast(app.status.watching ? 'Watcher started' : 'Watcher stopped'); } catch (error) { toast(error.message); } });
  $('#check-now').addEventListener('click', checkAll); $('#add-products').addEventListener('click', openCatalog); $('#catalog-search').addEventListener('input', filterCatalog); $('#catalog-category').addEventListener('change', filterCatalog); $('#include-stock').addEventListener('change', filterCatalog); $('#confirm-add').addEventListener('click', addSelected); $('#select-all').addEventListener('click', () => { $$('#catalog-body input:not(:disabled)').forEach(box => app.selected.add(box.dataset.slug)); filterCatalog(); });
  $('#history-filter').addEventListener('input', () => { clearTimeout(renderHistory.timer); renderHistory.timer=setTimeout(renderHistory,180); }); $('#watched-only').addEventListener('change', renderHistory); $('#clear-history').addEventListener('click', async () => { if (confirm('Delete the whole history of stock checks?')) { await api('/api/history', {method: 'DELETE'}); app.history = {events: [], stats: {}}; renderHistory(); renderChanges(); } });
  $('#save-settings').addEventListener('click', async () => { const button = $('#save-settings'); button.disabled = true; $('#test-alert').disabled = true; $('#save-status').textContent = 'Saving…'; try { await saveSettingsFromForm(); $('#save-status').textContent = 'Saved'; toast('Settings saved'); } catch (error) { $('#save-status').textContent = error.message; toast(error.message); } finally { button.disabled = false; $('#test-alert').disabled = false; } });
  $('#test-alert').addEventListener('click', testNotificationSetup); $('#autostart').addEventListener('change', () => $('#toggle-watch').click());
  $('#setup-notifications').addEventListener('click', () => { showTab('settings'); $('#ntfy-setup-guide').open = true; $('#notification-settings').scrollIntoView({behavior: 'smooth', block: 'start'}); $('#ntfy-setup-guide summary').focus(); });
  for (const id of ['ntfy-url', 'ntfy-topic']) $(`#${id}`).addEventListener('input', () => { renderNotificationSetup(); $('#ntfy-test-result').textContent = ''; });
  $('#generate-ntfy-topic').addEventListener('click', () => {
    if ($('#ntfy-topic').value.trim() && !confirm('Replace this topic? You will need to subscribe your phone to the new topic. Nothing changes until you save.')) return;
    const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); $('#ntfy-topic').value = `unifi-stock-${Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')}`; renderNotificationSetup(); $('#ntfy-test-result').textContent = ''; toast('Topic generated. Subscribe your phone, then save and test.');
  });
  $('#copy-ntfy-details').addEventListener('click', copyNotificationDetails);
  $('#theme').addEventListener('change', () => { app.settings.theme = $('#theme').value; setTheme(); }); $('#accent').addEventListener('change', () => { app.settings.accent = $('#accent').value; setTheme(); }); $('#text-size').addEventListener('change', () => { app.settings.text_size = $('#text-size').value; setTheme(); }); $('#clear-activity').addEventListener('click', () => { if ($('#activity-feed').classList.contains('hidden')) $('#changes-feed').innerHTML = ''; else $('#activity-feed').textContent = ''; });
  $('#banner-diagnostics').addEventListener('click', openDiagnostics); $('#close-diagnostics').addEventListener('click', () => $('#diagnostics-dialog').close()); $('#diag-check').addEventListener('click', () => { $('#diagnostics-dialog').close(); checkAll(); }); $('#diag-activity').addEventListener('click', () => { $('#diagnostics-dialog').close(); showTab('watch'); showBottom('activity'); }); $('#diag-refresh').addEventListener('click', async () => { await refresh({preserveSettings: true}); toast('Diagnostics refreshed'); });
}
async function init() { configureActivityPanel(); configureNotificationSettings(); bind(); try { await refresh(); if (location.hash === '#notifications') { showTab('settings'); $('#ntfy-setup-guide').open = true; $('#notification-settings').scrollIntoView({block: 'start'}); } setInterval(async () => { try { await refresh({preserveSettings: true}); } catch {} }, 30000); } catch (error) { toast(error.message); } }
document.addEventListener('DOMContentLoaded', init);
