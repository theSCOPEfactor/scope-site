/* Scope shared UI helpers */
(function () {
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* typewriter tagline */
  function typeInto(el, text, speed) {
    if (!el) return;
    if (reduce) { el.textContent = text; return; }
    clearInterval(el._t);
    el.textContent = '';
    let i = 0;
    el._t = setInterval(function () {
      el.textContent = text.slice(0, ++i);
      if (i >= text.length) clearInterval(el._t);
    }, speed || 75);
  }
  window.typeInto = typeInto;

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.typer').forEach(function (el, k) {
      const txt = el.getAttribute('data-text');
      setTimeout(function () { typeInto(el, txt, 80); }, 350 + k * 200);
      const host = el.closest('.brand, .hero');
      if (host) host.addEventListener('mouseenter', function () {
        if (el.textContent.length === txt.length) typeInto(el, txt, 45);
      });
    });
  });

  /* mouse-follow glow on glass cards */
  document.addEventListener('mousemove', function (e) {
    const c = e.target.closest && e.target.closest('.card, .tile');
    if (!c) return;
    const r = c.getBoundingClientRect();
    c.style.setProperty('--mx', (e.clientX - r.left) + 'px');
    c.style.setProperty('--my', (e.clientY - r.top) + 'px');
  });

  /* number count-up */
  window.animateNum = function (el, to, fmt, dur) {
    fmt = fmt || function (v) { return Math.round(v).toLocaleString('en-IN'); };
    if (reduce || !isFinite(to)) { el.textContent = fmt(to); return; }
    const t0 = performance.now(); dur = dur || 900;
    (function step(t) {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(to * e);
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  };

  /* tiny toast */
  window.toast = function (msg) {
    let t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add('on');
    clearTimeout(t._h); t._h = setTimeout(function () { t.classList.remove('on'); }, 1400);
  };

  /* gentle 3D tilt on home tiles */
  document.addEventListener('mousemove', function (e) {
    if (reduce) return;
    const t = e.target.closest && e.target.closest('.tile');
    document.querySelectorAll('.tile.tilting').forEach(function (x) {
      if (x !== t) { x.style.transform = ''; x.classList.remove('tilting'); }
    });
    if (!t) return;
    const r = t.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5, py = (e.clientY - r.top) / r.height - 0.5;
    t.classList.add('tilting');
    t.style.transform = 'perspective(800px) rotateX(' + (-py * 6).toFixed(2) + 'deg) rotateY(' + (px * 8).toFixed(2) + 'deg) translateY(-4px)';
  });

  /* magnet icon: drawn in place of any text label for the volume-profile magnet level */
  window.MAGNET = function (s) {
    s = s || 16;
    return '<svg class="magnet" width="' + s + '" height="' + s + '" viewBox="0 0 24 24" role="img" aria-label="magnet"><title>Magnet</title>' +
      '<path d="M6 3.5v9.5a6 6 0 0 0 12 0V3.5" fill="none" stroke="#c9d3e6" stroke-width="4.4"/>' +
      '<path d="M6 3.5v4.2" fill="none" stroke="#ef4444" stroke-width="4.4"/>' +
      '<path d="M18 3.5v4.2" fill="none" stroke="#3b82f6" stroke-width="4.4"/>' +
      '<path d="M3.8 4.2h4.4M15.8 4.2h4.4" stroke="#fff" stroke-width="1.5"/></svg>';
  };

  /* target-plan maths (used by Journal plan tab and Home goal card) */
  const P = window.ScopePlan = {};
  P.iso = function (d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  };
  P.parse = function (s) { const a = String(s).split('-').map(Number); return new Date(a[0], a[1] - 1, a[2]); };
  P.tradingDays = function (start, calDays) {
    const out = [], d = new Date(start);
    for (let i = 0; i < calDays; i++) {
      const w = d.getDay();
      if (w !== 0 && w !== 6) out.push(P.iso(d));
      d.setDate(d.getDate() + 1);
    }
    return out;
  };
  /* plan = {mode:'target'|'rate', target, rate, period:'daily'|'weekly'|'monthly', days, start, compound} */
  P.build = function (p, cap) {
    if (!p || !(cap > 0)) return null;
    const start = P.parse(p.start || P.iso(new Date()));
    const days = P.tradingDays(start, Math.max(1, +p.days || 0));
    const N = days.length; if (!N) return null;
    const comp = p.compound !== false;
    let R;
    if (p.mode === 'target') R = (+p.target || 0) / cap;
    else {
      const n = p.period === 'daily' ? N : p.period === 'weekly' ? N / 5 : N / 21, r = (+p.rate || 0) / 100;
      R = comp ? Math.pow(1 + r, n) - 1 : r * n;
    }
    if (!(R > 0)) return null;
    const g = Math.pow(1 + R, 1 / N) - 1, T = cap * R, eq = [cap];
    for (let i = 1; i <= N; i++) eq.push(comp ? cap * Math.pow(1 + g, i) : cap + T * i / N);
    const rates = comp ? { d: g, w: Math.pow(1 + g, 5) - 1, m: Math.pow(1 + g, 21) - 1 }
                       : { d: R / N, w: 5 * R / N, m: 21 * R / N };
    return { C: cap, T: T, R: R, N: N, days: days, eq: eq, rates: rates, comp: comp, last: days[N - 1] };
  };
})();

/* ===== shared: level names (flip NEUTRAL_NAMES to true for chart-style wording) + price ladder ===== */
window.NEUTRAL_NAMES = false;
window.LVNAME = function (k) {
  var o = {d1:'Deep buy reversal 1', d2:'Deep buy reversal 2', t1:'Target 1', sr:'Sell reversal', br:'Buy reversal'};
  var n = {d1:'Deep reversal 1', d2:'Deep reversal 2', t1:'Extension', sr:'Upper reversal', br:'Lower reversal'};
  return (window.NEUTRAL_NAMES ? n : o)[k];
};
window.ZONENAME = function (z) { return (window.NEUTRAL_NAMES && z) ? z.replace(' (sell zone)', '').replace(' (buy zone)', '') : z; };
window.ScopeLadder = (function () {
  var f2 = function (n) { return n == null ? '-' : Number(n).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}); };
  function levelsOf(s) {
    var F = s.fib || {}, R = s.vhigh - s.vlow;
    var deep = function (m) { var v = s.vlow - m * R; return v > 0 ? v : null; };
    return [
      {k:'d1', name:LVNAME('d1'), v:deep(0.272), c:'#38bdf8'},
      {k:'d2', name:LVNAME('d2'), v:deep(0.618), c:'#818cf8'},
      {k:'t1', name:LVNAME('t1'), v:F.target1, c:'#a78bfa'},
      {k:'hi', name:'VHIGH · Breakout', v:s.vhigh, c:'#fb7185'},
      {k:'sr', name:LVNAME('sr'), v:F.sell_reversal, c:'#fb923c'},
      {k:'mg', name:'', v:s.point, c:'#f5c04a', mag:true},
      {k:'br', name:LVNAME('br'), v:F.buy_reversal, c:'#34d399'},
      {k:'lo', name:'VLOW · Break down', v:s.vlow, c:'#2dd4bf'}
    ].filter(function (x) { return x.v != null; });
  }
  function svg(s, L) {
    var vals = L.map(function (x) { return x.v; }).concat([s.price]);
    var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals), pad = (mx - mn) * .08 || 1; mn -= pad; mx += pad;
    var top = 26, bot = 400, Y = function (v) { return top + (mx - v) / (mx - mn) * (bot - top); };
    L.forEach(function (x) { x.y = Y(x.v); }); L.sort(function (a, b) { return a.y - b.y; });
    var prev = -1e9; L.forEach(function (x) { x.ly = Math.max(x.y, prev + 34); prev = x.ly; });
    var H = Math.max(bot + 34, prev + 30), py = Y(s.price), hi = L.find(function (x) { return x.k == 'hi'; }), lo = L.find(function (x) { return x.k == 'lo'; });
    var g = '<svg class="ladder" viewBox="0 0 470 ' + H + '" role="img" aria-label="Price ladder">';
    if (hi && lo) g += '<rect x="78" y="' + hi.y + '" width="200" height="' + Math.max(2, lo.y - hi.y) + '" rx="6" fill="rgba(45,212,191,.07)" stroke="rgba(45,212,191,.2)"/>';
    g += '<line x1="78" y1="' + (top - 8) + '" x2="78" y2="' + (bot + 10) + '" stroke="rgba(255,255,255,.2)"/>';
    L.forEach(function (x, i) {
      var lab = x.mag ? '<g transform="translate(314,' + (x.ly - 11) + ') scale(1)">' + MAGNET(22) + '</g>' : '<text class="nm" x="314" y="' + (x.ly - 4) + '">' + x.name + '</text>';
      g += '<g class="lvl" data-k="' + x.k + '" style="--c:' + x.c + '">' +
        '<rect class="hit" x="0" y="' + (Math.min(x.y, x.ly) - 14) + '" width="470" height="' + (Math.abs(x.ly - x.y) + 30) + '"/>' +
        '<line class="ln" x1="78" y1="' + x.y + '" x2="278" y2="' + x.y + '" style="animation-delay:' + (i * 70) + 'ms"/>' +
        '<polyline class="ln" points="278,' + x.y + ' 296,' + x.ly + ' 308,' + x.ly + '" fill="none" style="animation-delay:' + (i * 70) + 'ms;stroke-width:1.2"/>' +
        '<g class="lt" style="animation-delay:' + (200 + i * 70) + 'ms">' + lab + '<text class="vl" x="' + (x.mag ? 340 : 314) + '" y="' + (x.mag ? x.ly + 5 : x.ly + 13) + '">' + f2(x.v) + '</text></g></g>';
    });
    g += '<g class="pm"><line x1="78" y1="' + py + '" x2="278" y2="' + py + '" stroke="#fff" stroke-opacity=".5" stroke-dasharray="4 4"/>' +
      '<circle class="ring" cx="78" cy="' + py + '" r="7" fill="#fff" fill-opacity=".4"/><circle cx="78" cy="' + py + '" r="6" fill="#fff" stroke="#0a1226" stroke-width="2"/>' +
      '<text x="68" y="' + (py + 4) + '" text-anchor="end" fill="#fff" font-size="12" font-weight="700">' + f2(s.price) + '</text></g></svg>';
    return g;
  }
  function chips(s, L) {
    return L.slice().sort(function (a, b) { return b.v - a.v; }).map(function (x) {
      var d = (x.v / s.price - 1);
      return '<div class="lvc" data-k="' + x.k + '" style="--c:' + x.c + '"><small>' + (x.mag ? MAGNET(20) : x.name) + '</small><b>' + f2(x.v) + '</b><em>' + (d >= 0 ? '+' : '') + (d * 100).toFixed(1) + '% from price</em></div>';
    }).join('');
  }
  function html(s) {          /* ladder + chips for one stock */
    var L = levelsOf(s);
    return '<div class="dgrid"><div>' + svg(s, L) + '</div><div class="lv">' + chips(s, L) + '</div></div>';
  }
  function bind(box) {
    var set = function (k, on) { box.querySelectorAll('[data-k="' + k + '"]').forEach(function (el) { el.classList.toggle('hl', on); }); };
    box.querySelectorAll('[data-k]').forEach(function (el) {
      el.addEventListener('mouseenter', function () { set(el.dataset.k, true); });
      el.addEventListener('mouseleave', function () { set(el.dataset.k, false); });
    });
  }
  return {levelsOf: levelsOf, html: html, bind: bind};
})();

/* ---- favourites (star): saved on the server per member, copy kept in this browser for speed ---- */
window.Watchlist = (function () {
  var KEY = 'scope_favs', set = {}, subs = [];
  try { JSON.parse(localStorage.getItem(KEY) || '[]').forEach(function (s) { set[s] = 1; }); } catch (e) {}
  function save() { try { localStorage.setItem(KEY, JSON.stringify(Object.keys(set))); } catch (e) {} }
  function fire() { subs.forEach(function (f) { try { f(); } catch (e) {} }); }
  function paint() {
    [].forEach.call(document.querySelectorAll('.star[data-sym]'), function (b) {
      var on = !!set[b.dataset.sym]; b.classList.toggle('on', on); b.textContent = on ? '★' : '☆';
      b.title = on ? 'Remove from favourites' : 'Add to favourites';
    });
  }
  function sync() {
    return fetch('/api/favs', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      set = {}; (d.symbols || []).forEach(function (s) { set[s] = 1; }); save(); paint(); fire();
    }).catch(function () {});
  }
  function toggle(sym) {
    var on = !set[sym]; if (on) set[sym] = 1; else delete set[sym];
    save(); paint(); fire();
    return fetch('/api/fav', { method: 'POST', credentials: 'same-origin', body: JSON.stringify({ symbol: sym, on: on }) }).catch(function () {});
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.star[data-sym]');
    if (b) { e.preventDefault(); toggle(b.dataset.sym); }
  });
  document.addEventListener('DOMContentLoaded', function () { if (document.getElementById('side')) sync(); });
  return {
    has: function (s) { return !!set[s]; }, list: function () { return Object.keys(set); }, toggle: toggle, sync: sync,
    onChange: function (f) { subs.push(f); },
    starBtn: function (sym) {
      var on = !!set[sym], q = String(sym).replace(/[^A-Za-z0-9&._-]/g, '');
      return '<button class="star' + (on ? ' on' : '') + '" data-sym="' + q + '" title="' + (on ? 'Remove from favourites' : 'Add to favourites') + '">' + (on ? '★' : '☆') + '</button>';
    }
  };
})();
