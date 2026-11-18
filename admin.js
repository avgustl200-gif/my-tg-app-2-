'use strict';
// Админ-панель владельца: заказы, товары и остатки, промокоды, лояльность и настройки. Доступна только id из ADMIN_IDS.
S.adm = { tab: 'orders', status: 'new' };
const ST_LABEL = { new: 'Новые', confirmed: 'Подтверждённые', shipped: 'Отправленные', done: 'Выполненные', cancelled: 'Отменённые', '': 'Все' };
const TRANS = { new: ['confirmed', 'cancelled'], confirmed: ['shipped', 'done', 'cancelled'], shipped: ['done', 'cancelled'], done: [], cancelled: [] };
const TR_BTN = { confirmed: 'Подтвердить', shipped: 'Отправлен', done: 'Выполнен', cancelled: 'Отменить' };

function renderAdmin(root) {
  root.appendChild(h('div', { class: 'top' }, h('div', { class: 'brand' }, h('img', { src: '/logo.png', alt: '' }), h('div', {}, h('b', { text: 'Админ-панель' }), h('small', { text: S.cfg.shop_name })))));
  const sub = h('div', { class: 'sub' });
  const box = h('div');
  const tabs = [['orders', 'Заказы'], ['products', 'Товары'], ['promos', 'Промокоды'], ['settings', 'Настройки']];
  const draw = () => {
    clear(sub); tabs.forEach(([id, n]) => sub.appendChild(h('button', { class: S.adm.tab === id ? 'on' : '', text: n, onclick: () => { S.adm.tab = id; draw(); } })));
    clear(box);
    ({ orders: admOrders, products: admProducts, promos: admPromos, settings: admSettings })[S.adm.tab](box);
  };
  root.appendChild(sub); root.appendChild(box); draw();
}
const fail = e => toast(e.message, 'err');

// ---------------------------------------------------------------- заказы
async function admOrders(box) {
  const stats = h('div', { class: 'stats' }), chips = h('div', { class: 'chips' }), list = h('div');
  box.appendChild(stats); box.appendChild(chips); box.appendChild(list);
  api('GET', '/api/admin/summary').then(r => {
    const s = r.stats;
    [['Новых заказов', s.orders_new], ['Всего заказов', s.orders_total], ['Выручка (подтверждённые)', money(s.revenue_minor)], ['Нет в наличии', s.out_of_stock]].forEach(([n, v]) => stats.appendChild(h('div', { class: 'stat' }, h('b', { text: v }), h('span', { text: n }))));
  }).catch(fail);
  const load = async () => {
    clear(chips); Object.keys(ST_LABEL).forEach(k => chips.appendChild(h('button', { class: 'chip' + (S.adm.status === k ? ' on' : ''), text: ST_LABEL[k], onclick: () => { S.adm.status = k; load(); } })));
    clear(list).appendChild(h('div', { class: 'spinner', style: { margin: '30px auto' } }));
    try {
      const r = await api('GET', '/api/admin/orders?status=' + encodeURIComponent(S.adm.status));
      clear(list);
      if (!r.orders.length) return list.appendChild(h('div', { class: 'empty', text: 'Заказов нет' }));
      r.orders.forEach(o => list.appendChild(adminOrder(o, load)));
    } catch (e) { fail(e); }
  };
  load();
}

function adminOrder(o, reload) {
  const who = (o.u_first_name || '') + (o.u_username ? ' @' + o.u_username : '');
  const tgName = o.tg_username || o.u_username;
  const acts = h('div', { class: 'actions' });
  TRANS[o.status].forEach(st => acts.appendChild(h('button', { class: 'btn small' + (st === 'cancelled' ? ' danger' : (st === 'done' ? ' green' : '')), text: TR_BTN[st], onclick: async () => {
    if (st === 'cancelled' && !(await confirmBox('Отменить заказ №' + o.id + '? Товары вернутся в наличие.', 'Отменить заказ'))) return;
    try { await api('POST', '/api/admin/order/' + o.id + '/status', { status: st }); toast('Статус обновлён', 'ok'); reload(); } catch (e) { fail(e); }
  } })));
  if (tgName) acts.appendChild(h('button', { class: 'btn small ghost', text: '✈️ @' + tgName, onclick: () => openTg(tgName) }));
  const tel = h('a', { href: 'tel:' + o.phone.replace(/[^+\d]/g, ''), text: o.phone });
  const meta = h('div', { class: 'meta' }, h('div', {}, '📞 ', tel), h('div', { text: '🏙 ' + o.city + ', ' + o.address }), h('div', { text: '👤 ' + (who || 'ID ' + o.user_id) }),
    o.comment ? h('div', { text: '💬 ' + o.comment }) : null);
  return orderCard(o, h('div', {}, meta, acts));
}

// ---------------------------------------------------------------- товары
async function admProducts(box) {
  const head = h('div', { class: 'row', style: { marginBottom: '12px' } },
    h('button', { class: 'btn small', text: '＋ Товар', onclick: () => productForm(null, load) }), h('button', { class: 'btn small ghost', text: 'Категории', onclick: () => categoriesForm(load) }));
  const list = h('div'); box.appendChild(head); box.appendChild(list);
  async function load() {
    try {
      const r = await api('GET', '/api/admin/catalog'); S.admCat = r; clear(list);
      if (!r.products.length) list.appendChild(h('div', { class: 'empty', text: 'Товаров пока нет' }));
      r.products.forEach(p => {
        const cat = (r.categories.find(c => c.id === p.category_id) || {}).name || 'Без категории';
        list.appendChild(h('div', { class: 'arow', onclick: () => productForm(p, load) }, productImage(p),
          h('div', { class: 'mid' }, h('b', { text: p.name }), h('span', { text: money(p.price_minor) + ' · ' + cat + ' · остаток ' + p.stock + (p.has_flavors ? ' (вкусов: ' + p.flavors.length + ')' : '') })),
          !p.active ? h('span', { class: 'tag off', text: 'СКРЫТ' }) : (!p.in_stock ? h('span', { class: 'tag', text: 'НЕТ' }) : null)));
      });
    } catch (e) { fail(e); }
  }
  load();
}

function productForm(p, reload) {
  const cats = (S.admCat && S.admCat.categories) || [];
  const d = p ? JSON.parse(JSON.stringify(p)) : { name: '', description: '', price_minor: 0, old_price_minor: 0, badge: '', image: '', stock: 0, active: true, category_id: cats.length ? cats[0].id : null, flavors: [] };
  const body = h('div');
  const img = h('div', { class: 'upl' });
  const drawImg = () => { clear(img); img.appendChild(productImage({ name: d.name || '?', image: d.image })); img.appendChild(h('div', {},
    h('label', { class: 'btn small ghost', style: { display: 'inline-flex', alignItems: 'center' } }, 'Загрузить фото',
      h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp,image/gif', style: { display: 'none' }, onchange: async e => {
        const f = e.target.files[0]; if (!f) return;
        if (f.size > 3 * 1024 * 1024) return toast('Файл больше 3 МБ', 'err');
        const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
        try { d.image = (await api('POST', '/api/admin/upload', { data })).path; drawImg(); } catch (er) { fail(er); }
      } })), d.image ? h('button', { class: 'btn small ghost', style: { marginLeft: '8px' }, text: 'Убрать', onclick: () => { d.image = ''; drawImg(); } }) : null)); };
  drawImg();
  const price = h('input', { type: 'text', inputmode: 'decimal', value: fromMinor(d.price_minor) });
  const old = h('input', { type: 'text', inputmode: 'decimal', value: fromMinor(d.old_price_minor), placeholder: 'например 25' });
  const stockField = h('div'), flavBox = h('div');
  const drawFl = () => {
    clear(flavBox); clear(stockField);
    d.flavors.forEach((f, i) => flavBox.appendChild(h('div', { class: 'frow' },
      h('input', { type: 'text', value: f.name, placeholder: 'Вкус', oninput: e => { f.name = e.target.value; } }),
      h('input', { type: 'number', min: '0', value: f.stock, oninput: e => { f.stock = parseInt(e.target.value || '0', 10); } }),
      h('input', { type: 'checkbox', title: 'Показывать', checked: f.active !== false, onchange: e => { f.active = e.target.checked; } }),
      h('button', { class: 'del', text: '✕', onclick: () => { d.flavors.splice(i, 1); drawFl(); } }))));
    if (!d.flavors.length) stockField.appendChild(field('Остаток, шт.', h('input', { type: 'number', min: '0', value: d.stock, oninput: e => { d.stock = parseInt(e.target.value || '0', 10); } }), 'Когда остаток 0 — товар сам пропадёт с витрины'));
    else stockField.appendChild(h('div', { class: 'hint', text: 'Остаток считается по вкусам. Вкус с остатком 0 пропадает с витрины сам; галочка — показывать вкус.' }));
  };
  drawFl();
  const catSel = h('select', { onchange: e => { d.category_id = e.target.value ? parseInt(e.target.value, 10) : null; } }, h('option', { value: '', text: 'Без категории' }),
    cats.map(c => h('option', { value: c.id, selected: c.id === d.category_id, text: c.name })));
  body.appendChild(img);
  body.appendChild(field('Название', h('input', { type: 'text', value: d.name, maxlength: '120', oninput: e => { d.name = e.target.value; } })));
  body.appendChild(field('Категория', catSel));
  body.appendChild(h('div', { class: 'row' }, field('Цена, ' + S.cfg.currency, price), field('Старая цена', old, 'для скидки')));
  body.appendChild(field('Ярлык', h('input', { type: 'text', value: d.badge, maxlength: '12', placeholder: 'NEW, SALE…', oninput: e => { d.badge = e.target.value; } })));
  body.appendChild(field('Описание', h('textarea', { maxlength: '2000', oninput: e => { d.description = e.target.value; } }, d.description)));
  body.appendChild(h('h4', { text: 'Вкусы и остатки' })); body.appendChild(flavBox);
  body.appendChild(h('button', { class: 'btn small ghost', text: '＋ Добавить вкус', onclick: () => { d.flavors.push({ name: '', stock: 0, active: true }); drawFl(); } }));
  body.appendChild(h('div', { style: { height: '12px' } })); body.appendChild(stockField);
  body.appendChild(h('label', { class: 'switch' }, h('span', { text: 'Показывать на витрине' }), h('input', { type: 'checkbox', checked: d.active, onchange: e => { d.active = e.target.checked; } })));
  const save = h('button', { class: 'btn green block', text: 'Сохранить', onclick: async () => {
    const pm = toMinor(price.value), om = old.value.trim() ? toMinor(old.value) : 0;
    if (isNaN(pm)) return toast('Укажите цену', 'err');
    if (isNaN(om)) return toast('Старая цена указана неверно', 'err');
    d.price_minor = pm; d.old_price_minor = om;
    save.disabled = true;
    try { await api('POST', '/api/admin/product', d); toast('Сохранено', 'ok'); sh.close(); reload(); } catch (e) { fail(e); save.disabled = false; }
  } });
  body.appendChild(save);
  if (p) body.appendChild(h('button', { class: 'btn danger block', style: { marginTop: '10px' }, text: 'Удалить товар', onclick: async () => {
    if (!(await confirmBox('Удалить «' + p.name + '» навсегда? Старые заказы сохранятся.', 'Удалить'))) return;
    try { await api('DELETE', '/api/admin/product/' + p.id); sh.close(); reload(); } catch (e) { fail(e); }
  } }));
  const sh = sheet(p ? 'Редактирование' : 'Новый товар', body, { full: true });
}

function categoriesForm(reload) {
  const cats = (S.admCat && S.admCat.categories) || [];
  const body = h('div'), list = h('div');
  const draw = () => {
    clear(list);
    cats.forEach(c => {
      const name = h('input', { type: 'text', value: c.name }), sort = h('input', { type: 'number', value: c.sort, title: 'Порядок' }), on = h('input', { type: 'checkbox', checked: !!c.active, title: 'Показывать' });
      list.appendChild(h('div', { class: 'frow' }, name, sort, on,
        h('button', { class: 'btn small ghost', text: '💾', onclick: async () => { try { await api('POST', '/api/admin/category', { id: c.id, name: name.value, sort: parseInt(sort.value || '0', 10), active: on.checked }); toast('Сохранено', 'ok'); reload(); } catch (e) { fail(e); } } }),
        h('button', { class: 'del', text: '🗑', onclick: async () => { if (!(await confirmBox('Удалить категорию «' + c.name + '»? Товары останутся без категории.', 'Удалить'))) return; try { await api('DELETE', '/api/admin/category/' + c.id); cats.splice(cats.indexOf(c), 1); sh.close(); reload(); } catch (e) { fail(e); } } })));
    });
  };
  draw(); body.appendChild(list);
  const nn = h('input', { type: 'text', placeholder: 'Новая категория' });
  body.appendChild(h('div', { class: 'frow' }, nn, h('button', { class: 'btn small', text: 'Добавить', onclick: async () => { try { await api('POST', '/api/admin/category', { name: nn.value, sort: cats.length + 1 }); sh.close(); await reload(); } catch (e) { fail(e); } } })));
  body.appendChild(h('div', { class: 'hint', text: 'Поля: название · порядок · показывать. Пустые категории на витрине не отображаются.' }));
  const sh = sheet('Категории', body);
}

// ---------------------------------------------------------------- промокоды
async function admPromos(box) {
  const list = h('div');
  box.appendChild(h('button', { class: 'btn small', style: { marginBottom: '12px' }, text: '＋ Промокод', onclick: () => promoForm(null, load) })); box.appendChild(list);
  async function load() {
    try {
      const r = await api('GET', '/api/admin/promos'); clear(list);
      if (!r.promos.length) list.appendChild(h('div', { class: 'empty', text: 'Промокодов пока нет' }));
      r.promos.forEach(p => list.appendChild(h('div', { class: 'arow', onclick: () => promoForm(p, load) },
        h('div', { class: 'mid' }, h('b', { text: p.code }), h('span', { text: (p.type === 'percent' ? p.value + '%' : money(p.value)) + ' · использован ' + p.used_count + (p.max_uses ? ' из ' + p.max_uses : '') + (p.expires_at ? ' · до ' + new Date(p.expires_at * 1000).toLocaleDateString('ru-RU') : '') })),
        !p.active ? h('span', { class: 'tag off', text: 'ВЫКЛ' }) : null)));
    } catch (e) { fail(e); }
  }
  load();
}

function promoForm(p, reload) {
  const d = p ? Object.assign({}, p) : { code: '', type: 'percent', value: 10, min_order_minor: 0, max_uses: 0, per_user_limit: 1, expires_at: 0, active: true, note: '' };
  const isPct = () => d.type === 'percent';
  const code = h('input', { type: 'text', value: d.code, maxlength: '32', autocapitalize: 'characters', placeholder: 'SUMMER10' });
  const val = h('input', { type: 'text', inputmode: 'decimal', value: isPct() ? d.value : fromMinor(d.value) });
  const valLbl = h('span', { class: 'lbl' });
  const typ = h('select', { onchange: e => { d.type = e.target.value; valLbl.textContent = isPct() ? 'Размер скидки, %' : 'Размер скидки, ' + S.cfg.currency; } },
    h('option', { value: 'percent', selected: isPct(), text: 'Процент' }), h('option', { value: 'fixed', selected: !isPct(), text: 'Фиксированная сумма' }));
  valLbl.textContent = isPct() ? 'Размер скидки, %' : 'Размер скидки, ' + S.cfg.currency;
  const minO = h('input', { type: 'text', inputmode: 'decimal', value: fromMinor(d.min_order_minor), placeholder: '0' });
  const maxU = h('input', { type: 'number', min: '0', value: d.max_uses }), perU = h('input', { type: 'number', min: '0', value: d.per_user_limit });
  const exp = h('input', { type: 'date', value: d.expires_at ? new Date(d.expires_at * 1000).toISOString().slice(0, 10) : '' });
  const note = h('input', { type: 'text', value: d.note, maxlength: '200', placeholder: 'Для кого/зачем' });
  const body = h('div', {}, field('Код', code), field('Тип скидки', typ), h('label', { class: 'field' }, valLbl, val), field('Минимальная сумма заказа', minO, '0 — без ограничений'),
    h('div', { class: 'row' }, field('Всего применений', maxU, '0 — без лимита'), field('На одного клиента', perU, '0 — без лимита')),
    field('Действует до', exp, 'пусто — без срока'), field('Заметка', note),
    h('label', { class: 'switch' }, h('span', { text: 'Активен' }), h('input', { type: 'checkbox', checked: d.active, onchange: e => { d.active = e.target.checked; } })));
  const save = h('button', { class: 'btn green block', text: 'Сохранить', onclick: async () => {
    const v = isPct() ? parseInt(val.value, 10) : toMinor(val.value), mo = minO.value.trim() ? toMinor(minO.value) : 0;
    if (!v || isNaN(v)) return toast('Укажите размер скидки', 'err');
    if (isNaN(mo)) return toast('Минимальная сумма указана неверно', 'err');
    const payload = { id: d.id, code: code.value, type: d.type, value: v, min_order_minor: mo, max_uses: parseInt(maxU.value || '0', 10), per_user_limit: parseInt(perU.value || '0', 10),
      expires_at: exp.value ? Math.floor(new Date(exp.value + 'T23:59:59').getTime() / 1000) : 0, active: d.active, note: note.value };
    save.disabled = true;
    try { await api('POST', '/api/admin/promo', payload); toast('Сохранено', 'ok'); sh.close(); reload(); } catch (e) { fail(e); save.disabled = false; }
  } });
  body.appendChild(save);
  if (p) body.appendChild(h('button', { class: 'btn danger block', style: { marginTop: '10px' }, text: 'Удалить', onclick: async () => { if (!(await confirmBox('Удалить промокод ' + p.code + '?', 'Удалить'))) return; try { await api('DELETE', '/api/admin/promo/' + p.id); sh.close(); reload(); } catch (e) { fail(e); } } }));
  const sh = sheet(p ? 'Промокод ' + p.code : 'Новый промокод', body);
}

// ---------------------------------------------------------------- настройки и лояльность
async function admSettings(box) {
  let s;
  try { s = (await api('GET', '/api/admin/summary')).settings; } catch (e) { return fail(e); }
  const f = {};
  const txt = (k, label, hint, attrs) => { f[k] = h('input', Object.assign({ type: 'text', value: s[k] }, attrs || {})); return field(label, f[k], hint); };
  const mon = (k, label, hint) => { f[k] = h('input', { type: 'text', inputmode: 'decimal', value: fromMinor(s[k]) }); return field(label, f[k], hint); };
  const sw = (k, label) => { f[k] = h('input', { type: 'checkbox', checked: !!s[k] }); return h('label', { class: 'switch' }, h('span', { text: label }), f[k]); };
  const isPct = () => f.loyalty_type.value === 'percent';
  f.loyalty_type = h('select', {}, h('option', { value: 'percent', selected: s.loyalty_type === 'percent', text: 'Процент' }), h('option', { value: 'fixed', selected: s.loyalty_type === 'fixed', text: 'Фиксированная сумма' }));
  f.loyalty_value = h('input', { type: 'text', inputmode: 'decimal', value: s.loyalty_type === 'percent' ? s.loyalty_value : fromMinor(s.loyalty_value) });
  f.loyalty_every_n = h('input', { type: 'number', min: '2', value: s.loyalty_every_n });
  f.notify = h('input', { type: 'text', value: (s.notify_chat_ids || []).join(', '), placeholder: '123456789, -1001234567890' });
  box.appendChild(h('h4', { text: 'Лояльность' }));
  box.appendChild(sw('loyalty_enabled', 'Включить программу лояльности'));
  box.appendChild(field('Каждая N-я покупка со скидкой', f.loyalty_every_n, 'Например 5: на 5-м, 10-м, 15-м заказе скидка. Клиент видит прогресс в профиле.'));
  box.appendChild(field('Тип скидки', f.loyalty_type)); box.appendChild(field('Размер скидки (% или ' + S.cfg.currency + ')', f.loyalty_value));
  box.appendChild(mon('loyalty_min_order_minor', 'Минимальная сумма заказа для скидки', '0 — без ограничений'));
  box.appendChild(sw('stack_discounts', 'Суммировать промокод и скидку лояльности'));
  box.appendChild(h('div', { class: 'hint', text: 'Если выключено — применяется самая выгодная из двух скидок.' }));
  box.appendChild(h('div', { class: 'sec' })); box.appendChild(h('h4', { text: 'Магазин' }));
  box.appendChild(txt('shop_name', 'Название', '', { maxlength: '60' })); box.appendChild(txt('currency', 'Валюта', '', { maxlength: '6' }));
  box.appendChild(field('Приветствие на витрине', (f.welcome = h('textarea', { maxlength: '400' }, s.welcome))));
  box.appendChild(sw('shop_open', 'Принимать заказы')); box.appendChild(txt('closed_message', 'Сообщение, когда закрыто', '', { maxlength: '300' }));
  box.appendChild(sw('age_gate', 'Окно «Мне есть 18 лет» при первом входе'));
  box.appendChild(h('div', { class: 'sec' })); box.appendChild(h('h4', { text: 'Заказы и доставка' }));
  box.appendChild(mon('min_order_minor', 'Минимальная сумма заказа', '0 — без ограничений')); box.appendChild(mon('delivery_fee_minor', 'Стоимость доставки'));
  box.appendChild(mon('free_delivery_from_minor', 'Бесплатная доставка от суммы', '0 — не использовать'));
  box.appendChild(field('Показывать «осталось N» при остатке ≤', (f.low_stock_threshold = h('input', { type: 'number', min: '0', value: s.low_stock_threshold })), '0 — не показывать'));
  box.appendChild(field('Дополнительные чаты для уведомлений о заказах', f.notify, 'Telegram ID через запятую (уведомления админам из ADMIN_IDS приходят всегда)'));
  box.appendChild(txt('support_contact', 'Контакт поддержки', '@username менеджера', { maxlength: '80' }));
  const save = h('button', { class: 'btn green block', style: { marginTop: '14px' }, text: 'Сохранить настройки', onclick: async () => {
    const m = k => { const v = f[k].value.trim() ? toMinor(f[k].value) : 0; if (isNaN(v)) throw new Error('Проверьте суммы'); return v; };
    try {
      const lv = isPct() ? parseInt(f.loyalty_value.value, 10) : toMinor(f.loyalty_value.value);
      if (!lv || isNaN(lv)) throw new Error('Укажите размер скидки лояльности');
      const ids = f.notify.value.split(/[,;\s]+/).filter(Boolean).map(x => parseInt(x, 10));
      if (ids.some(isNaN)) throw new Error('Чаты: только числовые ID');
      const patch = { loyalty_enabled: f.loyalty_enabled.checked, loyalty_every_n: parseInt(f.loyalty_every_n.value, 10), loyalty_type: f.loyalty_type.value, loyalty_value: lv,
        loyalty_min_order_minor: m('loyalty_min_order_minor'), stack_discounts: f.stack_discounts.checked, shop_name: f.shop_name.value, currency: f.currency.value, welcome: f.welcome.value,
        shop_open: f.shop_open.checked, closed_message: f.closed_message.value, age_gate: f.age_gate.checked, min_order_minor: m('min_order_minor'), delivery_fee_minor: m('delivery_fee_minor'),
        free_delivery_from_minor: m('free_delivery_from_minor'), low_stock_threshold: parseInt(f.low_stock_threshold.value || '0', 10), notify_chat_ids: ids, support_contact: f.support_contact.value };
      save.disabled = true;
      await api('POST', '/api/admin/settings', patch);
      S.cfg = await api('GET', '/api/config'); await loadMe(); toast('Настройки сохранены', 'ok'); save.disabled = false;
    } catch (e) { fail(e); save.disabled = false; }
  } });
  box.appendChild(save);
}
