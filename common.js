'use strict';
// Общие помощники: безопасное создание DOM (никакого innerHTML с данными), запросы к API, деньги, уведомления.
const tg = (window.Telegram && window.Telegram.WebApp) || null;

function h(tag, attrs) {
  const el = document.createElement(tag);
  attrs = attrs || {};
  for (const k of Object.keys(attrs)) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (let i = 2; i < arguments.length; i++) add(el, arguments[i]);
  return el;
}
function add(el, c) {
  if (c === null || c === undefined || c === false) return;
  if (Array.isArray(c)) return c.forEach(x => add(el, x));
  el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

const S = { cfg: { currency: 'BYN' } };
function money(minor) {
  const v = Math.round(Number(minor) || 0);
  let s = Math.floor(v / 100) + '.' + String(v % 100).padStart(2, '0');
  if (s.endsWith('.00')) s = s.slice(0, -3);
  return s + ' ' + (S.cfg.currency || 'BYN');
}
function toMinor(str) {
  const n = parseFloat(String(str).replace(',', '.').replace(/\s/g, ''));
  return isFinite(n) && n >= 0 ? Math.round(n * 100) : NaN;
}
function fromMinor(m) { return m ? (m / 100).toString() : ''; }
function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const p = n => String(n).padStart(2, '0');
  return p(d.getDate()) + '.' + p(d.getMonth() + 1) + '.' + d.getFullYear() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}

async function api(method, path, body) {
  const headers = { 'Content-Type': 'application/json' };
  const init = tg && tg.initData;
  if (init) headers['X-Tg-Init-Data'] = init;
  else { const dev = new URLSearchParams(location.search).get('dev'); if (dev) headers['X-Dev-User'] = dev; }
  let res;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) { throw new Error('Нет связи с сервером'); }
  let data = {};
  try { data = await res.json(); } catch (e) { /* пустой ответ */ }
  if (!res.ok) {
    const err = new Error((data.error && data.error.message) || 'Ошибка ' + res.status);
    err.code = data.error && data.error.code; err.status = res.status; throw err;
  }
  return data;
}

let toastTimer;
function toast(msg, kind) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'show ' + (kind || '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.className = ''; }, 3200);
}
function haptic(kind) { try { tg && tg.HapticFeedback && (kind === 'ok' ? tg.HapticFeedback.notificationOccurred('success') : tg.HapticFeedback.impactOccurred('light')); } catch (e) { /* нет */ } }
function store(key, val) {
  try { if (val === undefined) return JSON.parse(localStorage.getItem(key) || 'null'); localStorage.setItem(key, JSON.stringify(val)); } catch (e) { return null; }
}
function openTg(user) {
  const url = 'https://t.me/' + String(user).replace(/^@/, '');
  if (tg && tg.openTelegramLink) tg.openTelegramLink(url); else window.open(url, '_blank', 'noopener');
}

// Нижняя шторка/окно
function sheet(title, body, opts) {
  opts = opts || {};
  const layer = document.getElementById('layer');
  const close = () => { clear(layer); layer.className = ''; document.body.classList.remove('lock'); if (opts.onclose) opts.onclose(); };
  clear(layer); layer.className = 'on'; document.body.classList.add('lock');
  const panel = h('div', { class: 'sheet' + (opts.full ? ' full' : '') },
    h('div', { class: 'sheet-head' }, h('h3', { text: title }), h('button', { class: 'icon-btn', 'aria-label': 'Закрыть', onclick: close, text: '✕' })),
    h('div', { class: 'sheet-body' }, body));
  layer.appendChild(h('div', { class: 'backdrop', onclick: close }));
  layer.appendChild(panel);
  return { close, panel };
}
function confirmBox(text, okText) {
  return new Promise(res => {
    const layer = document.getElementById('layer');
    const done = v => { clear(layer); layer.className = ''; document.body.classList.remove('lock'); res(v); };
    clear(layer); layer.className = 'on'; document.body.classList.add('lock');
    layer.appendChild(h('div', { class: 'backdrop', onclick: () => done(false) }));
    layer.appendChild(h('div', { class: 'dialog' }, h('p', { text }),
      h('div', { class: 'row' }, h('button', { class: 'btn ghost', onclick: () => done(false), text: 'Отмена' }), h('button', { class: 'btn danger', onclick: () => done(true), text: okText || 'Да' }))));
  });
}
function field(label, input, hint) {
  return h('label', { class: 'field' }, h('span', { class: 'lbl', text: label }), input, hint ? h('span', { class: 'hint', text: hint }) : null);
}
function gradFor(name) {
  let x = 0; for (const ch of name) x = (x * 31 + ch.charCodeAt(0)) >>> 0;
  const a = x % 360, b = (a + 60) % 360;
  return 'linear-gradient(135deg, hsl(' + a + ' 70% 30%), hsl(' + b + ' 75% 18%))';
}
function productImage(p, cls) {
  if (p.image) return h('img', { class: cls || 'pimg', src: p.image, alt: '', loading: 'lazy' });
  return h('div', { class: (cls || 'pimg') + ' ph', style: { background: gradFor(p.name) } }, h('span', { text: (p.name || '?').trim().slice(0, 1).toUpperCase() }));
}
