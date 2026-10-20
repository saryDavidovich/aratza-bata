/* לוח המודעות - לוגיקה בצד הדפדפן */
(() => {
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const CSRF = (document.querySelector('meta[name=csrf]') || {}).content || '';
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
const shekel = v => (v === null || v === undefined || v === '') ? 'מחיר לפי בקשה' : '₪' + Number(v).toLocaleString('he-IL');
const roomsTxt = v => v ? (Number.isInteger(+v) ? String(+v) : String(v)) : '';
const DEAL = { sale: 'מכירה', rent: 'השכרה', vacation: 'נופש', commercial: 'מסחרי' };
const PRICE_SUFFIX = { rent: ' לחודש', vacation: ' ללילה' };

// ------------------------------------------------------------------
// ad card (used on the board and in the publish preview)
// ------------------------------------------------------------------
function adCard(a) {
  const c = el('button', 'ad' + (a.highlight && a.highlight !== 'none' ? ' hl-' + a.highlight : ''));
  c.type = 'button';
  if (a.image_url) {
    const p = el('div', 'pic');
    p.style.backgroundImage = `url("${a.image_url}")`;
    c.appendChild(p);
  }
  const b = el('div', 'body');
  const chips = el('div', 'chips');
  chips.appendChild(el('span', 'chip deal', DEAL[a.deal_type] || ''));
  if (a.highlight === 'color') chips.appendChild(el('span', 'chip hl', 'מודגש'));
  if (a.highlight === 'motion') chips.appendChild(el('span', 'chip hl', 'מודגש בתנועה'));
  if (a.source_paper) chips.appendChild(el('span', 'chip', a.source_paper));
  if (a.origin === 'sample') chips.appendChild(el('span', 'chip sample', 'דוגמה'));
  b.appendChild(chips);
  b.appendChild(el('div', 'price', shekel(a.price) + (a.price ? (PRICE_SUFFIX[a.deal_type] || '') : '')));
  b.appendChild(el('div', 'ttl', a.title || 'כותרת המודעה'));
  b.appendChild(el('div', 'meta', [a.city, a.neighborhood].filter(Boolean).join(' · ') || 'ריכוז'));
  const facts = [a.rooms ? roomsTxt(a.rooms) + ' חד׳' : '', a.size_sqm ? a.size_sqm + ' מ״ר' : '', a.floor ? 'קומה ' + a.floor : ''].filter(Boolean);
  if (facts.length) b.appendChild(el('div', 'meta', facts.join(' · ')));
  c.appendChild(b);
  return c;
}
window.LuachAdCard = adCard;

// ------------------------------------------------------------------
// moving lanes: fill one group until it is wider than the lane, then clone it
// ------------------------------------------------------------------
function fillLane(box, items, make, opts) {
  const inner = $('.inner', box);
  inner.innerHTML = '';
  inner.style.animation = 'none';
  if (!items.length) {
    const e = el('div', 'empty', opts.empty);
    inner.appendChild(e);
    return;
  }
  const vertical = opts.vertical;
  const group = el('div', 'group');
  inner.appendChild(group);
  const need = () => vertical ? box.clientHeight * 1.05 : box.clientWidth * 1.05;
  const have = () => vertical ? group.scrollHeight : group.scrollWidth;
  let reps = 0;
  do {
    items.forEach(it => group.appendChild(make(it)));
    reps++;
  } while (!reduced && have() < need() && reps < 8);
  if (reduced) return;
  const clone = group.cloneNode(true);
  clone.classList.add('clone');
  clone.setAttribute('aria-hidden', 'true');
  $$('button, a', clone).forEach(x => x.setAttribute('tabindex', '-1'));
  inner.appendChild(clone);
  // clones need the same click behaviour
  if (opts.bindClone) opts.bindClone(clone);
  const len = vertical ? group.offsetHeight : group.offsetWidth;
  inner.style.setProperty('--dur', Math.max(opts.minDur, len / opts.speed).toFixed(1) + 's');
  void inner.offsetWidth;
  inner.style.animation = '';
}

// ------------------------------------------------------------------
// board page
// ------------------------------------------------------------------
function initBoard() {
  const dataEl = $('#board-data');
  if (!dataEl) return;
  let data = JSON.parse(dataEl.textContent);
  const f = { deal: '', city: '', paper: '', rooms: '', pmin: '', pmax: '', q: '' };
  let page1 = 0, page2 = 0;
  const LIM1 = 12, LIM2 = 24;

  function fillSelect(sel, values, first) {
    const cur = sel.value;
    sel.innerHTML = '';
    sel.appendChild(new Option(first, ''));
    values.forEach(v => sel.appendChild(new Option(v, v)));
    sel.value = values.includes(cur) ? cur : '';
  }
  function uniq(key) { return [...new Set(data.ads.map(a => a[key]).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'he')); }

  function filtered() {
    const q = f.q.trim().toLowerCase();
    return data.ads.filter(a =>
      (!f.deal || a.deal_type === f.deal) &&
      (!f.city || a.city === f.city) &&
      (!f.paper || a.source_paper === f.paper) &&
      (!f.rooms || (a.rooms || 0) >= +f.rooms) &&
      (!f.pmin || (a.price || 0) >= +f.pmin) &&
      (!f.pmax || (a.price && a.price <= +f.pmax)) &&
      (!q || [a.title, a.description, a.city, a.neighborhood, a.source_paper].join(' ').toLowerCase().includes(q)));
  }
  const batch = (arr, page, lim) => arr.length <= lim ? arr : Array.from({ length: lim }, (_, i) => arr[(page * lim + i) % arr.length]);

  function bindAds(root) {
    $$('.ad', root).forEach(card => card.addEventListener('click', () => openAd(+card.dataset.id)));
  }
  function makeAd(a) { const c = adCard(a); c.dataset.id = a.id; c.addEventListener('click', () => openAd(a.id)); return c; }

  function renderAds() {
    const list = filtered();
    const hi = list.filter(a => a.highlight !== 'none');
    const all = list.filter(a => a.highlight === 'none');
    $('#count').textContent = list.length;
    $('#lane-hi').hidden = !hi.length;
    fillLane($('#track-hi'), batch(hi, page1, LIM1), makeAd, { empty: '', speed: 60, minDur: 30, bindClone: bindAds });
    fillLane($('#track-all'), batch(all, page2, LIM2), makeAd,
      { empty: list.length ? 'כל המודעות שנמצאו מודגשות ומופיעות למעלה.' : 'לא נמצאו מודעות. נסה לשנות את הסינון.', speed: 50, minDur: 40, bindClone: bindAds });
    // when a lane finishes a full round and there are more ads than fit, show the next ones
    const rot = (id, arr, lim, inc) => {
      const inner = $('#' + id + ' .inner');
      // (highlighted cards have their own animation; only the lane's own round counts)
      inner.onanimationiteration = arr.length > lim ? e => { if (e.target !== inner) return; inc(); renderAds(); } : null;
    };
    rot('track-hi', hi, LIM1, () => page1++);
    rot('track-all', all, LIM2, () => page2++);
  }

  // ---------- links ----------
  function makeLink(l) {
    const c = el('div', 'lk');
    const chips = el('div', 'chips');
    chips.appendChild(el('span', 'chip deal', l.store_label));
    if (l.sample) chips.appendChild(el('span', 'chip sample', 'דוגמה'));
    c.appendChild(chips);
    c.appendChild(el('div', 'ttl', l.title));
    if (l.description) c.appendChild(el('div', 'desc', l.description));
    c.appendChild(el('div', 'by', 'מאת ' + (l.marketer_name || '')));
    const row = el('div', 'row');
    const up = el('button', 'vote' + (l.my_vote === 1 ? ' on' : ''), '👍 ' + l.likes);
    const down = el('button', 'vote' + (l.my_vote === -1 ? ' on' : ''), '👎 ' + l.dislikes);
    up.type = down.type = 'button';
    up.title = 'תגובה חיובית'; down.title = 'תגובה שלילית';
    up.dataset.vote = '1'; down.dataset.vote = '-1';
    up.dataset.id = down.dataset.id = l.id;
    const go = el('a', 'btn small dark go', 'לקישור');
    go.href = l.url; go.target = '_blank'; go.rel = 'nofollow sponsored noopener';
    row.append(up, down, go);
    c.appendChild(row);
    return c;
  }
  function renderLinks() {
    fillLane($('#stream'), data.links, makeLink, { vertical: true, empty: 'עדיין אין קישורים. היה הראשון להוסיף!', speed: 22, minDur: 30 });
    const top = $('#top4');
    top.innerHTML = '';
    data.top.forEach(m => {
      const d = el('div');
      d.appendChild(el('b', '', m.name));
      d.appendChild(el('span', '', `👍 ${m.likes || 0} · ${m.links} קישורים`));
      top.appendChild(d);
    });
    top.hidden = !data.top.length;
  }
  $('#stream').addEventListener('click', async e => {
    const b = e.target.closest('.vote');
    if (!b) return;
    const res = await fetch('/luach/api/vote', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF': CSRF },
      body: JSON.stringify({ link_id: +b.dataset.id, value: +b.dataset.vote })
    });
    if (res.status === 401) { location.href = '/luach/login?next=/luach/'; return; }
    const j = await res.json();
    if (!j.ok) return;
    const l = data.links.find(x => x.id === +b.dataset.id);
    Object.assign(l, { likes: j.likes, dislikes: j.dislikes, my_vote: j.mine });
    // update every copy of this card in place (no restart of the stream)
    $$(`.vote[data-id="${l.id}"]`).forEach(v => {
      const isUp = v.dataset.vote === '1';
      v.textContent = (isUp ? '👍 ' : '👎 ') + (isUp ? l.likes : l.dislikes);
      v.classList.toggle('on', l.my_vote === (isUp ? 1 : -1));
    });
  });

  // ---------- ad dialog ----------
  const dlg = $('#addlg');
  function openAd(id) {
    const a = data.ads.find(x => x.id === id);
    if (!a) return;
    const box = $('.content', dlg);
    box.innerHTML = '';
    if (a.image_url) { const p = el('div', 'pic'); p.style.backgroundImage = `url("${a.image_url}")`; box.appendChild(p); }
    const inn = el('div', 'in');
    const chips = el('div', 'chips');
    chips.appendChild(el('span', 'chip deal', DEAL[a.deal_type] || ''));
    if (a.highlight !== 'none') chips.appendChild(el('span', 'chip hl', a.highlight === 'motion' ? 'מודגש בתנועה' : 'מודגש'));
    if (a.origin === 'sample') chips.appendChild(el('span', 'chip sample', 'מודעת דוגמה'));
    inn.appendChild(chips);
    inn.appendChild(el('h3', '', a.title));
    inn.appendChild(el('div', 'price', shekel(a.price) + (a.price ? (PRICE_SUFFIX[a.deal_type] || '') : '')));
    if (a.description) inn.appendChild(el('p', 'desc', a.description));
    const facts = el('dl', 'facts');
    const add = (k, v) => { if (!v) return; const d = el('div'); d.append(el('dt', '', k), el('dd', '', v)); facts.appendChild(d); };
    add('ריכוז · שכונה', [a.city, a.neighborhood].filter(Boolean).join(' · '));
    add('שטח', a.size_sqm ? a.size_sqm + ' מ״ר' : '');
    add('חדרים', roomsTxt(a.rooms));
    add('קומה', a.floor);
    add('גליון', [a.source_paper, a.gilyon].filter(Boolean).join(' · '));
    add('תאריך', a.published_date ? a.published_date.split('-').reverse().join('.') : '');
    inn.appendChild(facts);
    const ct = el('div', 'contact');
    const digits = (a.phone || '').replace(/\D/g, '');
    if (digits && !/^0+$/.test(digits)) {
      const call = el('a', 'btn dark', '📞 חיוג ' + a.phone); call.href = 'tel:' + digits;
      const intl = digits.startsWith('0') ? '972' + digits.slice(1) : digits;
      const wa = el('a', 'btn', 'וואטסאפ'); wa.href = 'https://wa.me/' + intl; wa.target = '_blank'; wa.rel = 'noopener';
      ct.append(call, wa);
    } else {
      ct.appendChild(el('span', 'meta', 'מודעת דוגמה, ללא טלפון אמיתי'));
    }
    if (a.contact_name) ct.appendChild(el('span', 'who2', a.contact_name));
    inn.appendChild(ct);
    box.appendChild(inn);
    dlg.showModal();
  }
  $('.x', dlg).addEventListener('click', () => dlg.close());
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });

  // ---------- filters ----------
  function setupFilters() {
    fillSelect($('#f-city'), uniq('city'), 'כל הריכוזים');
    fillSelect($('#f-paper'), uniq('source_paper'), 'כל הגליונות');
  }
  $$('.deal-btns button').forEach(b => b.addEventListener('click', () => {
    $$('.deal-btns button').forEach(x => x.classList.toggle('on', x === b));
    f.deal = b.dataset.deal; page1 = page2 = 0; renderAds();
  }));
  [['#f-city', 'city'], ['#f-paper', 'paper'], ['#f-rooms', 'rooms'], ['#f-pmin', 'pmin'], ['#f-pmax', 'pmax'], ['#f-q', 'q']].forEach(([s, k]) => {
    $(s).addEventListener('input', () => { f[k] = $(s).value; page1 = page2 = 0; renderAds(); });
  });
  $('#f-reset').addEventListener('click', () => {
    Object.keys(f).forEach(k => { f[k] = ''; });
    $$('.filters select, .filters input').forEach(i => { i.value = ''; });
    $$('.deal-btns button').forEach((x, i) => x.classList.toggle('on', i === 0));
    page1 = page2 = 0; renderAds();
  });

  setupFilters(); renderAds(); renderLinks();
  let lastW = innerWidth;
  addEventListener('resize', () => { if (Math.abs(innerWidth - lastW) > 80) { lastW = innerWidth; renderAds(); renderLinks(); } });
  // fresh data every few minutes (new ads and links appear without reloading)
  setInterval(async () => {
    if (document.hidden || dlg.open) return;
    try {
      const r = await fetch('/luach/api/board');
      if (!r.ok) return;
      data = await r.json();
      setupFilters(); renderAds(); renderLinks();
    } catch (e) { /* offline - keep the current board */ }
  }, 180000);
}

// ------------------------------------------------------------------
// stop / start motion (remembered in this browser)
// ------------------------------------------------------------------
function initMotionToggle() {
  const b = $('#motion');
  if (!b) return;
  let still = false;
  try { still = localStorage.getItem('luach-still') === '1'; } catch (e) { /* ignore */ }
  const apply = () => {
    document.body.classList.toggle('still', still);
    b.setAttribute('aria-pressed', still ? 'true' : 'false');
    b.textContent = still ? '▶ הפעלת תנועה' : '⏸ עצור תנועה';
  };
  apply();
  b.addEventListener('click', () => {
    still = !still;
    try { localStorage.setItem('luach-still', still ? '1' : '0'); } catch (e) { /* ignore */ }
    apply();
  });
}

// ------------------------------------------------------------------
// publish page: live preview
// ------------------------------------------------------------------
function initPublish() {
  const form = $('#pub-form');
  if (!form) return;
  const box = $('#preview');
  let imgUrl = '';
  const read = () => {
    const v = n => (form.elements[n] ? form.elements[n].value.trim() : '');
    return {
      title: v('title'), deal_type: v('deal_type'), city: v('city'), neighborhood: v('neighborhood'),
      price: v('price') ? +v('price').replace(/,/g, '') : null, rooms: v('rooms'), floor: v('floor'), size_sqm: v('size_sqm'),
      highlight: (form.querySelector('input[name=highlight]:checked') || {}).value || 'none',
      source_paper: '', origin: 'user', image_url: imgUrl
    };
  };
  const draw = () => { box.innerHTML = ''; box.appendChild(adCard(read())); };
  form.addEventListener('input', draw);
  form.addEventListener('change', draw);
  const file = form.elements.image;
  if (file) file.addEventListener('change', () => {
    const f = file.files[0];
    if (!f) { imgUrl = ''; draw(); return; }
    const r = new FileReader();
    r.onload = () => { imgUrl = r.result; draw(); };
    r.readAsDataURL(f);
  });
  draw();
}

// confirm before destructive buttons
document.addEventListener('submit', e => {
  const msg = e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

initMotionToggle();
initBoard();
initPublish();
})();
