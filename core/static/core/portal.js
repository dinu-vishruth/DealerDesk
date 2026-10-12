/* DealerDesk portal. All business operations go through the existing REST API. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const money = (value) => new Intl.NumberFormat('en-IN', {style: 'currency', currency: 'INR', minimumFractionDigits: 2}).format(Number(value));
  const number = (value) => new Intl.NumberFormat('en-IN', {maximumFractionDigits: 2}).format(Number(value));
  const date = (value) => new Intl.DateTimeFormat('en-IN', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata'}).format(new Date(value));
  const uid = () => crypto.randomUUID();
  const state = {
    dealer: null, dealers: [], products: new Map(), cataloguePage: 1, orderPage: 1,
    catalogueRequest: 0, orderRequest: 0, detailRequest: 0, scope: 0,
    catalogueHasNext: false, ordersHasNext: false, carts: new Map(), chats: new Map(),
    reorder: [], reorderLoaded: false, reorderRequest: 0, mutation: false, booting: false,
  };
  let toastTimer;

  async function api(path, {method = 'GET', body, headers = {}, timeout = 60000} = {}) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) throw new Error('Invalid API destination.');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, {
        method, signal: controller.signal, headers: {Accept: 'application/json', ...(body !== undefined ? {'Content-Type': 'application/json'} : {}), ...headers},
        ...(body !== undefined ? {body: JSON.stringify(body)} : {}),
      });
      let data;
      try { data = await response.json(); } catch { throw new Error('The server returned an unexpected response. Please try again.'); }
      if (!response.ok) {
        throw new Error(response.status >= 500 ? 'The service is unavailable right now. Please try again shortly.' : (data.error?.message || 'The request could not be completed.'));
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The request timed out. Check My orders before retrying an order request.');
      if (error instanceof TypeError) throw new Error('Could not reach DealerDesk. Check your connection and try again.');
      throw error;
    } finally { clearTimeout(timer); }
  }

  async function allPages(path) {
    const items = [];
    const seen = new Set();
    while (path) {
      if (seen.has(path)) throw new Error('Could not finish loading this list. Please refresh.');
      seen.add(path);
      const data = await api(path);
      items.push(...data.results);
      path = data.next;
    }
    return items;
  }

  function toast(message, error = false) {
    clearTimeout(toastTimer);
    $('toast').textContent = message;
    $('toast').classList.toggle('error', error);
    $('toast').hidden = false;
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 8000 : 3500);
  }
  const run = (fn) => Promise.resolve().then(fn).catch((error) => toast(error.message, true));
  function emptyRow(columns, heading, explanation = '') {
    return `<tr><td colspan="${columns}" class="empty-state"><strong>${escape(heading)}</strong>${escape(explanation)}</td></tr>`;
  }
  function stockPill(stock) {
    return `<span class="pill ${stock === 0 ? 'out' : stock < 10 ? 'low' : ''}">${stock === 0 ? 'Out of stock' : `${number(stock)} in stock`}</span>`;
  }
  function statusPill(status) {
    const known = ['DRAFT', 'CONFIRMED', 'DISPATCHED', 'DELIVERED', 'CANCELLED'];
    const label = known.includes(status) ? status : 'UNKNOWN';
    return `<span class="pill ${label.toLowerCase()}">${label.charAt(0) + label.slice(1).toLowerCase()}</span>`;
  }
  function partCell(product, sku = product?.sku) {
    return `<div class="part-cell"><span class="part-icon" aria-hidden="true">${escape((product?.category || sku || 'PT').slice(0, 2).toUpperCase())}</span><div><button class="part-button" data-product="${escape(sku)}" type="button"><span class="part-title">${escape(product?.name || sku)}</span></button><span class="part-meta">${escape(sku)}${product?.brand ? ` · ${escape(product.brand)}` : ''}</span></div></div>`;
  }
  const activePage = () => ['catalogue', 'orders', 'assistant', 'reorder'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'catalogue';
  function route() {
    const page = activePage();
    const names = {catalogue: 'Catalogue', orders: 'My orders', assistant: 'Dealer assistant', reorder: 'Reorder insights'};
    document.querySelectorAll('.page').forEach((section) => { section.hidden = section.id !== `page-${page}`; });
    document.querySelectorAll('[data-page]').forEach((link) => {
      const active = link.dataset.page === page;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    $('breadcrumb-current').textContent = names[page];
    document.title = `${names[page]} · DealerDesk`;
    if (page === 'orders') run(loadOrders);
    if (page === 'assistant') renderChat();
    if (page === 'reorder') run(loadReorder);
  }

  async function loadCatalogue() {
    const request = ++state.catalogueRequest;
    const params = new URLSearchParams({page: state.cataloguePage, page_size: 10});
    [['search', 'catalogue-search'], ['category', 'category-filter'], ['vehicle', 'vehicle-filter']].forEach(([key, id]) => {
      if ($(id).value.trim()) params.set(key, $(id).value.trim());
    });
    $('catalogue-body').innerHTML = emptyRow(5, 'Loading parts…');
    $('catalogue-prev').disabled = $('catalogue-next').disabled = true;
    try {
      const data = await api(`/api/products/?${params}`);
      if (request !== state.catalogueRequest) return;
      data.results.forEach((product) => state.products.set(product.sku, product));
      $('catalogue-body').innerHTML = data.results.length ? data.results.map((p) => `<tr><td>${partCell(p)}</td><td class="fitment">${escape(p.vehicle_fitment)}</td><td>${stockPill(p.stock)}</td><td class="number price">${money(p.price)}</td><td class="number"><button class="add-button" type="button" data-add="${escape(p.sku)}" aria-label="Add ${escape(p.sku)} to cart" ${state.dealer ? '' : 'disabled'}>+ Add</button></td></tr>`).join('') : emptyRow(5, 'No parts found', 'Try a different part name, category, or vehicle.');
      const start = data.count ? (state.cataloguePage - 1) * 10 + 1 : 0;
      $('catalogue-range').textContent = `Showing ${start}–${start ? start + data.results.length - 1 : 0} of ${number(data.count)} parts`;
      $('catalogue-prev').disabled = !data.previous;
      $('catalogue-next').disabled = !data.next;
      state.catalogueHasNext = !!data.next;
    } catch (error) {
      if (request !== state.catalogueRequest) return;
      $('catalogue-body').innerHTML = emptyRow(5, 'Could not load the catalogue', error.message);
      $('catalogue-range').textContent = 'Use Refresh stock to try again';
      throw error;
    }
  }

  function cart() {
    if (!state.dealer) return {items: new Map(), key: null};
    if (!state.carts.has(state.dealer.id)) state.carts.set(state.dealer.id, {items: new Map(), key: uid()});
    return state.carts.get(state.dealer.id);
  }
  function updateCartBadge() {
    const quantity = [...cart().items.values()].reduce((sum, item) => sum + item.quantity, 0);
    $('cart-count').textContent = number(quantity);
    $('cart-open').setAttribute('aria-label', `Open draft cart, ${quantity} units`);
  }
  function addToCart(sku) {
    if (state.mutation) return;
    if (!state.dealer) return toast('Choose a dealership first.', true);
    const product = state.products.get(sku);
    if (!product) return toast('Refresh the catalogue to load this part.', true);
    const target = cart();
    const existing = target.items.get(sku);
    const quantity = (existing?.quantity || 0) + 1;
    if (!Number.isSafeInteger(quantity)) return toast('This quantity is too large. Please use a smaller quantity.', true);
    target.items.set(sku, {product, quantity});
    target.key = uid();
    $('cart-error').hidden = true;
    updateCartBadge();
    if ($('cart-dialog').open) renderCart();
    toast(`${sku} added to your draft cart`);
  }
  function renderCart() {
    const entries = [...cart().items.values()];
    $('cart-dealer').textContent = state.dealer ? `Ordering for ${state.dealer.name}` : 'Choose a dealership to create a draft.';
    $('cart-items').innerHTML = entries.length ? entries.map(({product: p, quantity}) => `<div class="cart-line"><div><strong>${escape(p.name)}</strong><small>${escape(p.sku)} · ${money(p.price)} per unit</small><div class="quantity-control"><label class="sr-only" for="qty-${escape(p.sku)}">Quantity for ${escape(p.sku)}</label><input type="number" id="qty-${escape(p.sku)}" data-quantity="${escape(p.sku)}" min="1" step="1" value="${quantity}" ${state.mutation ? 'disabled' : ''}><span>${money(Number(p.price) * quantity)}</span></div></div><button class="remove-item" type="button" data-remove="${escape(p.sku)}" aria-label="Remove ${escape(p.sku)} from cart" ${state.mutation ? 'disabled' : ''}>×</button></div>`).join('') : '<div class="empty-state"><strong>A new order starts here.</strong>Add parts from the catalogue to put your draft together.</div>';
    // Estimate in integer paise; the order API remains authoritative for money.
    let subtotal = 0, tax = 0;
    entries.forEach(({product, quantity}) => {
      const line = Math.round(Number(product.price) * 100) * quantity;
      subtotal += line;
      tax += Math.floor((line * 18 + 50) / 100);
    });
    $('cart-summary').innerHTML = entries.length ? `<div class="cart-summary"><div><span>Estimated subtotal</span><span>${money(subtotal / 100)}</span></div><div><span>Estimated GST · 18%</span><span>${money(tax / 100)}</span></div><div><span>Estimated total</span><span>${money((subtotal + tax) / 100)}</span></div></div>` : '';
    $('cart-submit').disabled = !entries.length || !state.dealer || state.mutation;
    $('cart-submit').textContent = state.mutation ? 'Creating your draft…' : 'Create draft order ↗';
    updateCartBadge();
  }
  function setMutation(busy) {
    state.mutation = busy;
    $('dealer-select').disabled = busy || !state.dealers.length;
  }
  async function createDraft() {
    const target = cart();
    if (state.mutation || !state.dealer || !target.items.size) return;
    const dealer = state.dealer;
    const body = {dealer_id: dealer.id, items: [...target.items.entries()].map(([sku, item]) => ({sku, quantity: item.quantity}))};
    setMutation(true);
    $('cart-error').hidden = true;
    renderCart();
    try {
      const order = await api('/api/orders/', {method: 'POST', body, headers: {'Idempotency-Key': target.key}});
      target.items.clear();
      target.key = uid();
      $('cart-dialog').close();
      updateCartBadge();
      toast(`Draft ${order.order_number} created`);
      showOrder(order);
      run(refreshDraftCount);
      if (activePage() === 'orders') run(loadOrders);
    } catch (error) {
      $('cart-error').textContent = error.message;
      $('cart-error').hidden = false;
    } finally { setMutation(false); renderCart(); }
  }

  async function refreshDraftCount() {
    const scope = state.scope;
    if (!state.dealer) { $('stat-drafts').textContent = '—'; return; }
    try {
      const data = await api(`/api/orders/?dealer_id=${state.dealer.id}&status=DRAFT&page_size=1`);
      if (scope === state.scope) $('stat-drafts').textContent = number(data.count);
    } catch (error) { if (scope === state.scope) $('stat-drafts').textContent = '—'; throw error; }
  }
  async function loadOrders() {
    const request = ++state.orderRequest;
    if (!state.dealer) { $('orders-body').innerHTML = emptyRow(5, 'Choose a dealership', 'Your orders will appear here.'); return; }
    const params = new URLSearchParams({dealer_id: state.dealer.id, page: state.orderPage, page_size: 10});
    if ($('order-status').value) params.set('status', $('order-status').value);
    $('orders-body').innerHTML = emptyRow(5, 'Loading orders…');
    $('orders-prev').disabled = $('orders-next').disabled = true;
    try {
      const data = await api(`/api/orders/?${params}`);
      if (request !== state.orderRequest) return;
      $('orders-body').innerHTML = data.results.length ? data.results.map((o) => `<tr><td><button class="part-button" type="button" data-order="${escape(o.order_number)}"><strong>${escape(o.order_number)}</strong></button></td><td class="muted">${date(o.created_at)}</td><td>${o.items.length} ${o.items.length === 1 ? 'part' : 'parts'}</td><td>${statusPill(o.status)}</td><td class="number"><button class="button small" type="button" data-order="${escape(o.order_number)}">View details ↗</button></td></tr>`).join('') : emptyRow(5, 'No orders here yet', $('order-status').value ? 'Try another status or create a new draft.' : 'Add a few parts to your cart to create your first draft.');
      $('orders-range').textContent = `${number(data.count)} orders · Page ${state.orderPage}`;
      $('orders-prev').disabled = !data.previous;
      $('orders-next').disabled = !data.next;
      state.ordersHasNext = !!data.next;
    } catch (error) {
      if (request !== state.orderRequest) return;
      $('orders-body').innerHTML = emptyRow(5, 'Could not load orders', error.message);
      $('orders-range').textContent = 'Use Refresh to try again';
      throw error;
    }
  }
  function openDetail(title, content) {
    $('detail-heading').textContent = title;
    $('detail-content').innerHTML = content;
    if (!$('detail-dialog').open) $('detail-dialog').showModal();
  }
  function showOrder(order) {
    if (!state.dealer || order.dealer_id !== state.dealer.id) return toast('Switch to the order’s dealership to view it.', true);
    const actions = {DRAFT: ['confirm', 'cancel'], CONFIRMED: ['dispatch', 'cancel'], DISPATCHED: ['deliver'], DELIVERED: [], CANCELLED: []};
    const labels = {confirm: 'Confirm order', cancel: 'Cancel order', dispatch: 'Mark dispatched', deliver: 'Mark delivered'};
    openDetail(order.order_number, `<div class="detail-meta"><span>${escape(state.dealer.name)} · ${date(order.created_at)}</span>${statusPill(order.status)}</div><div class="table-scroll"><table><thead><tr><th>Part</th><th class="number">Qty</th><th class="number">Unit price</th><th class="number">GST</th><th class="number">Line total</th></tr></thead><tbody>${order.items.map((item) => `<tr><td><span class="part-title">${escape(item.name)}</span><span class="part-meta">${escape(item.sku)}</span></td><td class="number">${number(item.quantity)}</td><td class="number">${money(item.unit_price)}</td><td class="number">${money(item.line_tax)}</td><td class="number">${money(item.line_total)}</td></tr>`).join('')}</tbody></table></div><div class="order-totals"><div><span>Subtotal</span><span>${money(order.subtotal)}</span></div><div><span>GST · 18%</span><span>${money(order.tax_total)}</span></div><div><span>Total</span><span>${money(order.grand_total)}</span></div></div><div id="order-error" class="alert" role="alert" hidden></div><div class="order-actions">${(actions[order.status] || []).map((action) => `<button type="button" class="button ${action === 'cancel' ? 'danger' : 'primary'}" data-order-action="${action}" data-number="${escape(order.order_number)}">${labels[action]}</button>`).join('') || '<span class="muted">This order is complete. No further actions are available.</span>'}</div>${order.status === 'DRAFT' ? '<p class="help-text">Stock is deducted only when you confirm. Draft prices follow the current catalogue.</p>' : ''}`);
  }
  async function loadOrder(orderNumber) {
    const request = ++state.detailRequest, scope = state.scope;
    openDetail('Order details', '<div class="empty-state">Loading order…</div>');
    try {
      const order = await api(`/api/orders/${encodeURIComponent(orderNumber)}/`);
      if (request !== state.detailRequest || scope !== state.scope || !$('detail-dialog').open) return;
      showOrder(order);
    } catch (error) {
      if (request === state.detailRequest && $('detail-dialog').open) $('detail-content').innerHTML = `<div class="alert" role="alert">${escape(error.message)}</div>`;
    }
  }
  async function orderAction(action, orderNumber) {
    if (state.mutation) return;
    const warnings = {confirm: 'Confirm this order? Its quantities will be deducted from available stock.', cancel: 'Cancel this order? If confirmed, its stock will be restored.', dispatch: 'Mark this confirmed order as dispatched?', deliver: 'Mark this dispatched order as delivered?'};
    if (!warnings[action] || !window.confirm(warnings[action])) return;
    setMutation(true);
    document.querySelectorAll('[data-order-action]').forEach((button) => { button.disabled = true; });
    try {
      const order = await api(`/api/orders/${encodeURIComponent(orderNumber)}/${action}/`, {method: 'POST', body: {}});
      showOrder(order);
      toast(`Order updated: ${order.status.toLowerCase()}`);
      run(refreshDraftCount);
      if (activePage() === 'orders') run(loadOrders);
      run(loadCatalogue);
      run(loadReorder);
    } catch (error) {
      if ($('order-error')) { $('order-error').textContent = error.message; $('order-error').hidden = false; }
      else toast(error.message, true);
    } finally {
      setMutation(false);
      document.querySelectorAll('[data-order-action]').forEach((button) => { button.disabled = false; });
    }
  }

  async function loadProduct(sku) {
    const request = ++state.detailRequest;
    openDetail('Part details', '<div class="empty-state">Loading part…</div>');
    try {
      const p = await api(`/api/products/${encodeURIComponent(sku)}/`);
      if (request !== state.detailRequest || !$('detail-dialog').open) return;
      state.products.set(p.sku, p);
      openDetail(p.sku, `<h3 class="part-detail-name">${escape(p.name)}</h3><div class="muted">${escape(p.brand)} · ${escape(p.category)}</div><div class="product-facts"><div><small>Current price</small><strong>${money(p.price)}</strong></div><div><small>Available stock</small><strong>${number(p.stock)} units</strong></div><div><small>Vehicle fitment</small><strong>${escape(p.vehicle_fitment)}</strong></div></div><p class="part-description">${escape(p.description || 'No description available.')}</p><div class="order-actions"><button class="button primary" type="button" data-add="${escape(p.sku)}">+ Add to draft cart</button><button class="button" type="button" data-ask="${escape(p.sku)}">Ask the assistant ↗</button></div>`);
    } catch (error) { if (request === state.detailRequest && $('detail-dialog').open) $('detail-content').innerHTML = `<div class="alert" role="alert">${escape(error.message)}</div>`; }
  }

  function conversation() {
    if (!state.dealer) return null;
    if (!state.chats.has(state.dealer.id)) state.chats.set(state.dealer.id, {id: uid(), messages: [], busy: false});
    return state.chats.get(state.dealer.id);
  }
  function renderChat() {
    const chat = conversation();
    $('chat-dealer').textContent = state.dealer ? `For ${state.dealer.name}` : 'Select your dealership above';
    $('chat-send').disabled = !chat || chat.busy;
    $('chat-input').disabled = !chat || chat.busy;
    $('chat-new').disabled = !chat || chat.busy;
    $('chat-send').textContent = chat?.busy ? 'Thinking…' : 'Send ↗';
    $('chat-messages').innerHTML = chat?.messages.length ? chat.messages.map((message) => `<article class="message ${message.role}"><span class="message-label">${message.role === 'user' ? 'YOU' : message.role === 'error' ? 'PLEASE TRY AGAIN' : 'DEALER ASSISTANT'}</span><div class="message-body">${escape(message.text)}</div>${message.calls?.length ? `<div class="message-tools">${message.calls.map((call) => `<span>${escape(({search_parts: 'Searched catalogue', check_stock: 'Checked live stock', create_draft_order: 'Requested draft'})[call.name] || call.name)}</span>`).join(' · ')}</div>` : ''}${message.skus?.length ? `<div class="message-actions">${message.skus.map((sku) => `<button class="button small" type="button" data-product="${escape(sku)}">${escape(sku)} ↗</button>`).join('')}</div>` : ''}${message.actions?.length ? `<div class="message-actions">${message.actions.filter((action) => action.type === 'draft_order_created').map((action) => `<button class="button small" type="button" data-order="${escape(action.order_number)}">View ${escape(action.order_number)} ↗</button>`).join('')}</div>` : ''}</article>`).join('') : '<div class="chat-welcome"><span class="assistant-avatar" aria-hidden="true">✳</span><h3>Let’s find what you need.</h3><p>Tell me the part and vehicle, or share a SKU. I can check availability and help build your next draft.</p></div>';
    if (chat?.busy) $('chat-messages').insertAdjacentHTML('beforeend', '<article class="message"><span class="message-label">DEALER ASSISTANT</span><div class="message-body">Working on your request…</div></article>');
    $('chat-messages').scrollTop = $('chat-messages').scrollHeight;
  }
  async function sendChat(event) {
    event.preventDefault();
    const chat = conversation(), dealer = state.dealer, message = $('chat-input').value.trim();
    if (!chat || chat.busy || !message) return;
    chat.messages.push({role: 'user', text: message});
    chat.busy = true;
    $('chat-input').value = '';
    renderChat();
    try {
      const data = await api('/api/assistant/chat/', {method: 'POST', body: {session_id: chat.id, dealer_id: dealer.id, message}});
      chat.messages.push({role: 'assistant', text: data.reply || 'I could not complete that request. Please try a more specific question.', calls: data.tool_calls, skus: data.cited_skus, actions: data.actions});
      if (data.actions?.length && state.dealer?.id === dealer.id) run(refreshDraftCount);
    } catch (error) { chat.messages.push({role: 'error', text: `${error.message} You can start a new conversation if this one cannot continue.`}); }
    finally {
      chat.busy = false;
      if (state.dealer?.id === dealer.id && state.chats.get(dealer.id) === chat) { renderChat(); $('chat-input').focus(); }
    }
  }

  async function loadReorder() {
    const request = ++state.reorderRequest;
    $('reorder-body').innerHTML = emptyRow(7, 'Loading replenishment suggestions…');
    try {
      const data = await api('/api/reorder-suggestions/');
      if (request !== state.reorderRequest) return;
      state.reorder = data.results;
      state.reorderLoaded = true;
      $('stat-reorders').textContent = number(data.results.filter((item) => item.reorder_qty > 0).length);
      renderReorder();
    } catch (error) {
      if (request !== state.reorderRequest) return;
      state.reorderLoaded = false;
      $('stat-reorders').textContent = '—';
      $('reorder-body').innerHTML = emptyRow(7, 'Could not load reorder suggestions', error.message);
      $('reorder-count').textContent = 'Use Refresh stock to try again';
      throw error;
    }
  }
  function renderReorder() {
    if (!state.reorderLoaded) return;
    const term = $('reorder-search').value.trim().toLowerCase();
    const filtered = state.reorder.filter((item) => `${item.sku} ${state.products.get(item.sku)?.name || ''}`.toLowerCase().includes(term) && (!$('reorder-needed').checked || item.reorder_qty > 0));
    $('reorder-body').innerHTML = filtered.length ? filtered.map((item) => `<tr><td>${partCell(state.products.get(item.sku), item.sku)}</td><td class="number">${number(item.current_stock)}</td><td class="number">${item.lead_time_weeks} ${item.lead_time_weeks === 1 ? 'week' : 'weeks'}</td><td class="number">${number(item.lead_time_demand)}</td><td class="number">${number(item.safety_stock)}</td><td class="number"><span class="${item.reorder_qty > 0 ? 'reorder-qty' : 'muted'}">${number(item.reorder_qty)}</span></td><td><button class="button small" type="button" data-forecast="${escape(item.sku)}">Forecast ↗</button></td></tr>`).join('') : emptyRow(7, 'No matching suggestions', 'Try another search or show all forecast parts.');
    $('reorder-count').textContent = `${filtered.length} of ${state.reorder.length} forecast parts`;
  }
  async function loadForecast(sku) {
    const request = ++state.detailRequest;
    openDetail(`Forecast · ${sku}`, '<div class="empty-state">Loading forecast…</div>');
    try {
      const [data, suggestions] = await Promise.all([api(`/api/forecast/${encodeURIComponent(sku)}/`), api('/api/reorder-suggestions/')]);
      if (request !== state.detailRequest || !$('detail-dialog').open) return;
      const item = suggestions.results.find((row) => row.sku === sku);
      const maximum = Math.max(1, ...data.forecast.map((row) => row.forecast_units));
      const labelDate = (value) => new Intl.DateTimeFormat('en-IN', {day: 'numeric', month: 'short', timeZone: 'UTC'}).format(new Date(`${value}T00:00:00Z`));
      const period = data.forecast.length ? `${labelDate(data.forecast[0].week_start)} – ${labelDate(data.forecast.at(-1).week_start)} ${data.forecast[0].week_start.slice(0, 4)}` : 'No forecast available';
      $('forecast-period').textContent = period;
      openDetail(`Forecast · ${sku}`, `<h3 class="part-detail-name">${escape(state.products.get(sku)?.name || sku)}</h3><p class="muted">Weekly demand · ${escape(period)}</p><div class="forecast-chart" role="img" aria-label="${escape(data.forecast.map((row) => `${row.week_start}: ${row.forecast_units} units`).join('; '))}">${data.forecast.map((row) => `<div class="forecast-column"><strong>${number(row.forecast_units)}</strong><div class="forecast-bar" style="height:${Math.max(0, row.forecast_units / maximum * 155)}px"></div><span>${labelDate(row.week_start)}</span></div>`).join('')}</div>${item ? `<div class="product-facts"><div><small>Live stock</small><strong>${number(item.current_stock)} units</strong></div><div><small>Reorder point</small><strong>${number(item.reorder_point)} units</strong></div><div><small>Suggested reorder</small><strong>${number(item.reorder_qty)} units</strong></div></div><p class="help-text">Lead time: ${item.lead_time_weeks} weeks · Forecast demand during lead time: ${number(item.lead_time_demand)} units · Safety stock: ${number(item.safety_stock)} units.</p>` : ''}<p class="help-text">Forecasts are estimates for the stated weeks. Reorder suggestions use current stock and do not create a purchase order.</p>`);
    } catch (error) { if (request === state.detailRequest && $('detail-dialog').open) $('detail-content').innerHTML = `<div class="alert" role="alert">${escape(error.message)}</div>`; }
  }

  function selectDealer(id) {
    state.dealer = state.dealers.find((dealer) => dealer.id === Number(id)) || null;
    state.scope += 1;
    state.orderRequest += 1;
    state.detailRequest += 1;
    state.orderPage = 1;
    $('dealer-meta').textContent = state.dealer ? [state.dealer.dealer_code, state.dealer.city].filter(Boolean).join(' · ') : 'No dealers available';
    if (state.dealer) { try { localStorage.setItem('dealerdesk.dealer', String(state.dealer.id)); } catch { /* Storage is optional. */ } }
    $('chat-input').value = '';
    $('cart-error').hidden = true;
    $('cart-dialog').close();
    $('detail-dialog').close();
    updateCartBadge();
    renderChat();
    run(refreshDraftCount);
    if (activePage() === 'orders') run(loadOrders);
    // Catalogue Add controls depend on whether a dealership has been selected.
    document.querySelectorAll('[data-add]').forEach((button) => { button.disabled = !state.dealer; });
  }
  async function boot() {
    if (state.booting) return;
    state.booting = true;
    $('global-error').hidden = true;
    $('connection-status').innerHTML = '<span class="status-dot"></span> Connecting';
    try {
      const [dealers, products] = await Promise.all([allPages('/api/dealers/?page_size=100'), allPages('/api/products/?page_size=100')]);
      state.dealers = dealers;
      state.products = new Map(products.map((p) => [p.sku, p]));
      $('stat-products').textContent = number(products.length);
      $('dealer-select').innerHTML = dealers.length ? dealers.map((dealer) => `<option value="${dealer.id}">${escape(dealer.name)}</option>`).join('') : '<option value="">No dealers available</option>';
      $('dealer-select').disabled = !dealers.length;
      const selectedCategory = $('category-filter').value;
      $('category-filter').innerHTML = '<option value="">All categories</option>' + [...new Set(products.map((p) => p.category))].sort().map((category) => `<option value="${escape(category)}">${escape(category)}</option>`).join('');
      $('category-filter').value = selectedCategory;
      let saved;
      try { saved = localStorage.getItem('dealerdesk.dealer'); } catch { /* Storage is optional. */ }
      const selected = dealers.find((dealer) => dealer.id === Number(state.dealer?.id || saved)) || dealers[0];
      if (selected) $('dealer-select').value = String(selected.id);
      selectDealer(selected?.id);
      $('connection-status').innerHTML = '<span class="status-dot"></span> API connected';
      await loadCatalogue();
      run(loadReorder);
    } catch (error) {
      $('connection-status').textContent = 'Connection interrupted';
      $('global-error').querySelector('span').textContent = error.message;
      $('global-error').hidden = false;
    } finally { state.booting = false; }
  }

  $('catalogue-filters').addEventListener('submit', (event) => { event.preventDefault(); state.cataloguePage = 1; run(loadCatalogue); });
  $('clear-filters').addEventListener('click', () => { $('catalogue-filters').reset(); state.cataloguePage = 1; run(loadCatalogue); });
  $('catalogue-refresh').addEventListener('click', () => run(loadCatalogue));
  $('catalogue-prev').addEventListener('click', () => { if (state.cataloguePage > 1) { state.cataloguePage -= 1; run(loadCatalogue); } });
  $('catalogue-next').addEventListener('click', () => { if (state.catalogueHasNext) { state.cataloguePage += 1; run(loadCatalogue); } });
  $('order-status').addEventListener('change', () => { state.orderPage = 1; run(loadOrders); });
  $('orders-refresh').addEventListener('click', () => run(loadOrders));
  $('orders-prev').addEventListener('click', () => { if (state.orderPage > 1) { state.orderPage -= 1; run(loadOrders); } });
  $('orders-next').addEventListener('click', () => { if (state.ordersHasNext) { state.orderPage += 1; run(loadOrders); } });
  $('reorder-refresh').addEventListener('click', () => run(loadReorder));
  $('reorder-search').addEventListener('input', renderReorder);
  $('reorder-needed').addEventListener('change', renderReorder);
  $('dealer-select').addEventListener('change', (event) => selectDealer(event.target.value));
  $('retry-load').addEventListener('click', () => run(boot));
  $('cart-open').addEventListener('click', () => { renderCart(); $('cart-dialog').showModal(); });
  $('cart-submit').addEventListener('click', () => run(createDraft));
  $('chat-form').addEventListener('submit', sendChat);
  $('chat-input').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } });
  $('chat-new').addEventListener('click', () => { if (state.dealer && !conversation().busy) { state.chats.delete(state.dealer.id); renderChat(); $('chat-input').focus(); } });
  $('detail-dialog').addEventListener('close', () => { state.detailRequest += 1; });
  for (const id of ['cart-dialog', 'detail-dialog']) $(id).addEventListener('cancel', (event) => { if (state.mutation) event.preventDefault(); });
  $('cart-items').addEventListener('change', (event) => {
    const sku = event.target.dataset.quantity;
    if (!sku || state.mutation) return;
    const quantity = Number(event.target.value);
    if (!Number.isSafeInteger(quantity) || quantity < 1) { toast('Use a positive whole-number quantity.', true); renderCart(); return; }
    const item = cart().items.get(sku);
    if (item) { item.quantity = quantity; cart().key = uid(); }
    $('cart-error').hidden = true;
    renderCart();
  });
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    const data = button.dataset;
    if (data.close && !state.mutation) $(data.close).close();
    if (data.add) addToCart(data.add);
    if (data.remove && !state.mutation) { cart().items.delete(data.remove); cart().key = uid(); $('cart-error').hidden = true; renderCart(); }
    if (data.product) run(() => loadProduct(data.product));
    if (data.order) run(() => loadOrder(data.order));
    if (data.orderAction) run(() => orderAction(data.orderAction, data.number));
    if (data.forecast) run(() => loadForecast(data.forecast));
    if (data.ask || data.prompt) {
      $('detail-dialog').close();
      location.hash = 'assistant';
      $('chat-input').value = data.prompt || `Please check the current stock and price for ${data.ask}.`;
      $('chat-input').focus();
    }
  });
  window.addEventListener('hashchange', route);
  route();
  run(boot);
})();
