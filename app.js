'use strict';
// Клиентская часть магазина: витрина, карточка товара, корзина, оформление, личный кабинет.
S.cart = store('cart_v1') || [];
S.tab = 'shop'; S.catId = 0; S.query = ''; S.promo = ''; S.form = {}; S.quote = null; S.catalog = { categories: [], products: [] };

const appEl = () => document.getElementById('app');
const cartCount = () => S.cart.reduce((a, l) => a + l.qty, 0);
const saveCart = () => store('cart_v1', S.cart);

function stockOf(p, fid) {
  if (p.has_flavors) { const f = p.flavors.find(x => x.id === fid); return f ? f.stock : 0; }
  return p.stock;
}

function addToCart(p, fid, qty) {
  const max = stockOf(p, fid);
  const line = S.cart.find(l => l.product_id === p.id && (l.flavor_id || null) === (fid || null));
  const have = line ? line.qty : 0;
  if (have + qty > max) { toast('В наличии только ' + max + ' шт.', 'err'); return false; }
  if (line) line.qty += qty; else S.cart.push({ product_id: p.id, flavor_id: fid || null, qty });
  saveCart(); haptic(); refreshNav();
  return true;
}

// ---------------------------------------------------------------- загрузка
async function loadCatalog() { S.catalog = await api('GET', '/api/catalog'); }
async function loadMe() { S.me = await api('GET', '/api/me'); }

async function boot() {
  try {
    if (tg) { tg.ready(); tg.expand(); try { tg.setHeaderColor('#070E24'); tg.setBackgroundColor('#070E24'); tg.setBottomBarColor && tg.setBottomBarColor('#0B1633'); } catch (e) { /* старый клиент */ } }
    S.cfg = await api('GET', '/api/config');
    document.title = S.cfg.shop_name;
    await loadMe();
    if (S.cfg.age_gate && !S.me.user.age_confirmed) return ageGate();
    await loadCatalog();
    S.form = { phone: S.me.user.phone, city: S.me.user.city, address: S.me.user.address, tg_username: S.me.user.username, comment: '' };
    render();
  } catch (e) {
    clear(appEl()).appendChild(h('div', { class: 'empty' }, h('div', { class: 'big', text: '🔒' }), h('p', { text: e.message }),
      h('button', { class: 'btn small', onclick: () => location.reload(), text: 'Повторить' })));
  }
}

function ageGate() {
  clear(appEl());
  const box = h('div', { class: 'age' });
  const show = no => {
    clear(box);
    if (no) { box.appendChild(h('h1', { text: 'Доступ закрыт' })); box.appendChild(h('p', { text: 'Магазин только для совершеннолетних.' })); if (tg) setTimeout(() => tg.close(), 1800); return; }
    box.appendChild(h('img', { src: '/logo.png', alt: '' }));
    box.appendChild(h('h1', { text: S.cfg.shop_name }));
    box.appendChild(h('p', { text: 'В магазине представлены товары только для совершеннолетних. Продажа лицам младше 18 лет запрещена.' }));
    box.appendChild(h('h2', { text: 'Вам есть 18 лет?' }));
    box.appendChild(h('div', { class: 'btns' },
      h('button', { class: 'btn green', text: 'Да, мне есть 18', onclick: async () => {
        try { await api('POST', '/api/age-confirm', {}); box.remove(); S.me.user.age_confirmed = true; await loadCatalog(); S.form = { phone: S.me.user.phone, city: S.me.user.city, address: S.me.user.address, tg_username: S.me.user.username, comment: '' }; render(); }
        catch (e) { toast(e.message, 'err'); }
      } }),
      h('button', { class: 'btn ghost', text: 'Нет', onclick: () => show(true) })));
  };
  show(false);
  document.body.appendChild(box);
}

// ---------------------------------------------------------------- каркас
let navEl = null;
function refreshNav() {
  if (!navEl) return;
  clear(navEl);
  const tabs = [['shop', '🛍', 'Витрина'], ['cart', '🛒', 'Корзина'], ['profile', '👤', 'Профиль']];
  if (S.me && S.me.is_admin) tabs.push(['admin', '⚙️', 'Админ']);
  const inner = h('div', { class: 'nav-in' });
  tabs.forEach(([id, ico, name]) => inner.appendChild(h('button', { class: S.tab === id ? 'on' : '', onclick: () => { S.tab = id; render(); window.scrollTo(0, 0); } },
    h('span', { class: 'ico', text: ico }), name, id === 'cart' && cartCount() ? h('span', { class: 'cnt', text: cartCount() }) : null)));
  navEl.appendChild(inner);
}

function render() {
  const root = clear(appEl());
  if (!navEl) { navEl = h('div', { class: 'nav' }); document.body.appendChild(navEl); }
  refreshNav();
  if (S.tab === 'shop') renderShop(root);
  else if (S.tab === 'cart') renderCart(root);
  else if (S.tab === 'profile') renderProfile(root);
  else if (S.tab === 'admin' && S.me.is_admin) renderAdmin(root);
}

// ---------------------------------------------------------------- витрина
function renderShop(root) {
  const top = h('div', { class: 'top' },
    h('div', { class: 'brand' }, h('img', { src: '/logo.png', alt: '' }), h('div', {}, h('b', { text: S.cfg.shop_name }), h('small', { text: 'Каталог · ' + S.catalog.products.length + ' позиций в наличии' }))),
    h('input', { class: 'search', type: 'text', placeholder: 'Поиск', value: S.query, oninput: e => { S.query = e.target.value; fillGrid(); } }));
  const chips = h('div', { class: 'chips' });
  const grid = h('div', { class: 'grid' });
  const fillChips = () => {
    clear(chips);
    chips.appendChild(h('button', { class: 'chip' + (S.catId === 0 ? ' on' : ''), text: 'Все', onclick: () => { S.catId = 0; fillChips(); fillGrid(); } }));
    S.catalog.categories.forEach(c => {
      if (!S.catalog.products.some(p => p.category_id === c.id)) return;       // пустые категории не показываем
      chips.appendChild(h('button', { class: 'chip' + (S.catId === c.id ? ' on' : ''), text: c.name, onclick: () => { S.catId = c.id; fillChips(); fillGrid(); } }));
    });
  };
  const fillGrid = () => {
    clear(grid);
    const q = S.query.trim().toLowerCase();
    const list = S.catalog.products.filter(p => (!S.catId || p.category_id === S.catId) && (!q || p.name.toLowerCase().includes(q) || p.flavors.some(f => f.name.toLowerCase().includes(q))));
    if (!list.length) { grid.style.display = 'block'; grid.appendChild(h('div', { class: 'empty' }, h('div', { class: 'big', text: '🔍' }), h('p', { text: q ? 'Ничего не найдено' : 'Пока ничего нет в наличии' }))); return; }
    grid.style.display = '';
    list.forEach(p => grid.appendChild(productCard(p)));
  };
  top.appendChild(chips);
  root.appendChild(top);
  if (!S.cfg.shop_open) root.appendChild(h('div', { class: 'notice warn', text: S.cfg.closed_message || 'Заказы временно не принимаются' }));
  else if (S.cfg.welcome) root.appendChild(h('div', { class: 'notice', text: S.cfg.welcome }));
  root.appendChild(grid);
  fillChips(); fillGrid();
}

function productCard(p) {
  const thr = S.cfg.low_stock_threshold;
  const off = p.old_price_minor > p.price_minor ? Math.round((1 - p.price_minor / p.old_price_minor) * 100) : 0;
  const open = () => openProduct(p);
  return h('div', { class: 'card' },
    h('div', { class: 'imgwrap', onclick: open }, productImage(p),
      p.badge ? h('span', { class: 'badge' + (/new/i.test(p.badge) ? ' new' : ''), text: p.badge }) : null,
      thr && p.stock <= thr ? h('span', { class: 'low', text: 'Осталось ' + p.stock }) : null),
    h('div', { class: 'cbody' },
      h('div', { class: 'cname', onclick: open, text: p.name }),
      p.has_flavors ? h('div', { class: 'cflav', text: 'Вкусы ⬇ · ' + p.flavors.length }) : null,
      h('div', { class: 'price' }, h('b', { text: money(p.price_minor) }), off ? h('s', { text: money(p.old_price_minor) }) : null, off ? h('span', { class: 'off', text: '−' + off + '%' }) : null),
      h('button', { class: 'btn small block', text: 'В корзину', onclick: () => {
        if (p.has_flavors) return openProduct(p);
        if (addToCart(p, null, 1)) toast('Добавлено в корзину', 'ok');
      } })));
}

function openProduct(p) {
  let fid = null, qty = 1;
  const body = h('div');
  const addBtn = h('button', { class: 'btn green block', onclick: () => {
    if (p.has_flavors && !fid) return toast('Выберите вкус', 'err');
    if (addToCart(p, fid, qty)) { toast('Добавлено в корзину', 'ok'); sh.close(); }
  } });
  const stepTxt = h('span', { text: '1' });
  const flavBox = h('div', { class: 'flav-list' });
  const upd = () => {
    stepTxt.textContent = qty;
    addBtn.textContent = 'В корзину · ' + money(p.price_minor * qty);
    clear(flavBox);
    p.flavors.forEach(f => flavBox.appendChild(h('button', { class: 'flav' + (fid === f.id ? ' on' : ''), onclick: () => { fid = f.id; qty = Math.min(qty, f.stock); upd(); } },
      f.name, S.cfg.low_stock_threshold && f.stock <= S.cfg.low_stock_threshold ? h('small', { text: 'осталось ' + f.stock }) : null)));
  };
  body.appendChild(h('div', { class: 'phero' }, productImage(p)));
  body.appendChild(h('div', { class: 'price' }, h('b', { text: money(p.price_minor) }), p.old_price_minor > p.price_minor ? h('s', { text: money(p.old_price_minor) }) : null));
  if (p.description) body.appendChild(h('div', { class: 'desc', text: p.description }));
  if (p.has_flavors) { body.appendChild(h('h4', { text: 'Вкус' })); body.appendChild(flavBox); }
  body.appendChild(h('div', { class: 'row', style: { alignItems: 'center', marginBottom: '14px' } },
    h('div', { class: 'stepper' }, h('button', { text: '−', onclick: () => { qty = Math.max(1, qty - 1); upd(); } }), stepTxt,
      h('button', { text: '+', onclick: () => { const max = p.has_flavors ? (fid ? stockOf(p, fid) : 99) : p.stock; qty = Math.min(max, qty + 1); upd(); } })),
    h('span', { class: 'hint', style: { textAlign: 'right' }, text: p.has_flavors ? '' : 'В наличии: ' + p.stock })));
  body.appendChild(addBtn);
  const sh = sheet(p.name, body);
  upd();
}

// ---------------------------------------------------------------- корзина
let quoteSeq = 0;
async function refreshQuote() {
  const seq = ++quoteSeq;
  const q = await api('POST', '/api/quote', { items: S.cart, promo: S.promo });
  if (seq !== quoteSeq) return null;
  // приводим корзину в соответствие с остатками
  let changed = false;
  q.problems.forEach(pr => {
    const i = S.cart.findIndex(l => l.product_id === pr.product_id && (l.flavor_id || null) === (pr.flavor_id || null));
    if (i < 0) return;
    if (pr.max_qty) S.cart[i].qty = pr.max_qty; else S.cart.splice(i, 1);
    changed = true;
  });
  if (changed) saveCart();
  S.quote = q;
  return q;
}

function renderCart(root) {
  root.appendChild(h('h2', { text: 'Корзина' }));
  if (!S.cart.length) {
    root.appendChild(h('div', { class: 'empty' }, h('div', { class: 'big', text: '🛒' }), h('p', { text: 'Корзина пуста' }),
      h('button', { class: 'btn small', text: 'К витрине', onclick: () => { S.tab = 'shop'; render(); } })));
    return;
  }
  const box = h('div'); root.appendChild(box);
  const draw = async () => {
    let q;
    try { q = await refreshQuote(); } catch (e) { return toast(e.message, 'err'); }
    if (!q) return;
    refreshNav(); clear(box);
    if (!q.lines.length) { box.appendChild(h('div', { class: 'empty' }, h('div', { class: 'big', text: '🛒' }), h('p', { text: 'Товары закончились. Корзина пуста.' }))); q.problems.forEach(p => box.appendChild(h('div', { class: 'problem', text: p.reason }))); return; }
    q.problems.forEach(p => box.appendChild(h('div', { class: 'problem', text: p.reason })));
    q.lines.forEach(l => {
      const set = n => { const c = S.cart.find(x => x.product_id === l.product_id && (x.flavor_id || null) === (l.flavor_id || null)); if (!c) return; c.qty = n; if (n <= 0) S.cart.splice(S.cart.indexOf(c), 1); saveCart(); draw(); };
      box.appendChild(h('div', { class: 'line' }, productImage({ name: l.name, image: l.image }),
        h('div', { class: 'info' }, h('div', { class: 'nm', text: l.name }), l.flavor_name ? h('div', { class: 'fl', text: l.flavor_name }) : null,
          h('div', { class: 'bottom' },
            h('div', { class: 'stepper' }, h('button', { text: '−', onclick: () => set(l.qty - 1) }), h('span', { text: l.qty }), h('button', { text: '+', onclick: () => { if (l.qty + 1 > l.stock) return toast('Больше нет в наличии', 'err'); set(l.qty + 1); } })),
            h('b', { text: money(l.sum_minor) }), h('button', { class: 'del', 'aria-label': 'Удалить', text: '🗑', onclick: () => set(0) })))));
    });
    // лояльность
    const lo = q.loyalty;
    if (lo.enabled && lo.next_is_discount) box.appendChild(h('div', { class: 'gift' }, h('b', { text: '🎁 Ваша ' + lo.every_n + '-я покупка! ' }), q.loyalty_discount_minor ? 'Скидка ' + money(q.loyalty_discount_minor) + ' уже учтена.' : 'Скидка применится при подходящей сумме заказа.'));
    else if (lo.enabled) box.appendChild(h('div', { class: 'notice', text: 'Ещё ' + lo.remaining + ' ' + plural(lo.remaining, 'покупка', 'покупки', 'покупок') + ' — и на следующей скидка ' + (lo.type === 'percent' ? lo.value + '%' : money(lo.value)) }));
    // промокод
    const pin = h('input', { type: 'text', placeholder: 'Промокод', value: S.promo, autocapitalize: 'characters', maxlength: '32' });
    box.appendChild(h('div', { class: 'promo' }, pin, h('button', { class: 'btn ghost', text: 'Применить', onclick: () => { S.promo = pin.value.trim(); draw(); } })));
    if (q.promo_error) box.appendChild(h('div', { class: 'problem', text: q.promo_error }));
    if (q.promo_code && q.promo_discount_minor) box.appendChild(h('div', { class: 'hint', style: { color: 'var(--green)', marginBottom: '8px' }, text: 'Промокод ' + q.promo_code + ' применён' }));
    // итого
    const sum = h('div', { class: 'sum' }, row('Товары', money(q.subtotal_minor)));
    if (q.promo_discount_minor) sum.appendChild(row('Промокод ' + q.promo_code, '−' + money(q.promo_discount_minor), 'disc'));
    if (q.loyalty_discount_minor) sum.appendChild(row('Скидка за лояльность', '−' + money(q.loyalty_discount_minor), 'disc'));
    sum.appendChild(row('Доставка', q.delivery_minor ? money(q.delivery_minor) : 'бесплатно'));
    sum.appendChild(row('Итого', money(q.total_minor), 'total'));
    box.appendChild(sum);
    // форма
    box.appendChild(h('h4', { text: 'Данные для заказа' }));
    const F = S.form, inp = (k, label, attrs) => field(label, h('input', Object.assign({ type: 'text', value: F[k] || '', oninput: e => { F[k] = e.target.value; } }, attrs || {})));
    box.appendChild(inp('phone', 'Телефон', { type: 'tel', placeholder: '+375 29 123-45-67', autocomplete: 'tel' }));
    box.appendChild(inp('city', 'Город', { placeholder: 'Минск' }));
    box.appendChild(inp('address', 'Адрес доставки', { placeholder: 'Улица, дом, квартира' }));
    box.appendChild(inp('tg_username', 'Telegram username', { placeholder: '@username' }));
    box.appendChild(field('Комментарий к заказу', h('textarea', { placeholder: 'Удобное время, пожелания…', maxlength: '500', oninput: e => { F.comment = e.target.value; } }, F.comment || '')));
    const belowMin = q.subtotal_minor < q.min_order_minor;
    if (belowMin) box.appendChild(h('div', { class: 'problem', text: 'Минимальная сумма заказа — ' + money(q.min_order_minor) }));
    if (!S.cfg.shop_open) box.appendChild(h('div', { class: 'problem', text: S.cfg.closed_message }));
    const btn = h('button', { class: 'btn green block', text: 'Оформить заказ · ' + money(q.total_minor), disabled: belowMin || !S.cfg.shop_open, onclick: async () => {
      btn.disabled = true;
      try {
        const r = await api('POST', '/api/order', Object.assign({ items: S.cart, promo: S.promo }, S.form));
        S.cart = []; S.promo = ''; saveCart(); haptic('ok');
        await Promise.all([loadMe(), loadCatalog()]);
        S.tab = 'profile'; render(); window.scrollTo(0, 0);
        orderDone(r.order);
      } catch (e) {
        toast(e.message, 'err'); btn.disabled = false;
        if (e.code === 'stock') draw();
      }
    } });
    box.appendChild(btn);
  };
  draw();
}
function row(a, b, cls) { return h('div', { class: 'r ' + (cls || '') }, h('span', { text: a }), h('span', { text: b })); }
function plural(n, a, b, c) { const m = n % 10, k = n % 100; return m === 1 && k !== 11 ? a : (m >= 2 && m <= 4 && (k < 10 || k >= 20) ? b : c); }

function orderDone(o) {
  const body = h('div', {}, h('p', { text: 'Мы получили заказ и скоро свяжемся с вами.' }), h('div', { class: 'sum' }, row('Сумма', money(o.total_minor), 'total')),
    h('button', { class: 'btn block', text: 'Отлично', onclick: () => document.querySelector('#layer .icon-btn').click() }));
  sheet('Заказ №' + o.id + ' принят ✅', body);
}

// ---------------------------------------------------------------- профиль
function renderProfile(root) {
  const u = S.me.user, lo = S.me.loyalty;
  root.appendChild(h('div', { class: 'top' }, h('div', { class: 'brand' }, h('img', { src: '/logo.png', alt: '' }),
    h('div', {}, h('b', { text: u.first_name || 'Профиль' }), h('small', { text: u.username ? '@' + u.username : 'ID ' + u.id })))));
  if (lo.enabled) {
    const dots = h('div', { class: 'dots' });
    for (let i = 0; i < lo.every_n - 1; i++) dots.appendChild(h('div', { class: 'dot' + (i < lo.filled ? ' f' : ''), text: i < lo.filled ? '✓' : String(i + 1) }));
    dots.appendChild(h('div', { class: 'dot gift-d' + (lo.next_is_discount ? ' hot' : ''), text: '🎁' }));
    const what = lo.type === 'percent' ? lo.value + '%' : money(lo.value);
    root.appendChild(h('div', { class: 'loyal' }, h('h3', { text: 'Программа лояльности' }),
      h('p', { text: lo.next_is_discount ? 'Следующая покупка — со скидкой ' + what + '!' : 'Каждая ' + lo.every_n + '-я покупка со скидкой ' + what + '. Осталось ' + lo.remaining + ' ' + plural(lo.remaining, 'покупка', 'покупки', 'покупок') + '.' }), dots));
  }
  const form = { phone: u.phone, city: u.city, address: u.address };
  root.appendChild(h('h4', { text: 'Мои данные' }));
  ['phone:Телефон', 'city:Город', 'address:Адрес'].forEach(s => { const [k, l] = s.split(':'); root.appendChild(field(l, h('input', { type: k === 'phone' ? 'tel' : 'text', value: form[k] || '', oninput: e => { form[k] = e.target.value; } }))); });
  root.appendChild(h('button', { class: 'btn ghost block', text: 'Сохранить данные', onclick: async () => { try { await api('POST', '/api/me', form); Object.assign(S.me.user, form); Object.assign(S.form, form); toast('Сохранено', 'ok'); } catch (e) { toast(e.message, 'err'); } } }));
  root.appendChild(h('h4', { text: 'Мои заказы' }));
  if (!S.me.orders.length) root.appendChild(h('div', { class: 'empty', style: { padding: '20px' }, text: 'Заказов пока нет' }));
  S.me.orders.forEach(o => root.appendChild(orderCard(o)));
  if (S.cfg.support_contact) root.appendChild(h('div', { class: 'actions' }, h('button', { class: 'btn ghost block', text: 'Написать в поддержку', onclick: () => openTg(S.cfg.support_contact) })));
}

function orderCard(o, extra) {
  return h('div', { class: 'order' },
    h('div', { class: 'hd' }, h('b', { text: 'Заказ №' + o.id }), h('span', { class: 'status s-' + o.status, text: o.status_ru })),
    h('div', { class: 'meta', text: fmtDate(o.created_at) }),
    o.items.map(i => h('div', { class: 'it' }, h('span', { text: i.name + (i.flavor_name ? ' · ' + i.flavor_name : '') + ' × ' + i.qty }), h('span', { text: money(i.price_minor * i.qty) }))),
    o.promo_discount_minor ? h('div', { class: 'it' }, h('span', { text: 'Промокод ' + o.promo_code }), h('span', { text: '−' + money(o.promo_discount_minor) })) : null,
    o.loyalty_discount_minor ? h('div', { class: 'it' }, h('span', { text: 'Скидка за лояльность' }), h('span', { text: '−' + money(o.loyalty_discount_minor) })) : null,
    h('div', { class: 'tot' }, h('span', { text: 'Итого' }), h('span', { text: money(o.total_minor) })),
    extra || null);
}

boot();
