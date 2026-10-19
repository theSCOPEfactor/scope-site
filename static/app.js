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
