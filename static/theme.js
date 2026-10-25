/* Scope: look & feel settings (saved on this device) + sidebar tools + app install */
(function () {
  var KEY = 'scope_prefs';
  var D = { mode: 'dark', skin: 'ocean', nav: 'full', fs: 'md', dense: '0', fx: 'full', cb: '0' };
  var BAR = { dark: '#050914', xdark: '#000000', light: '#eef2fa' };
  var MODE_NAME = { dark: 'Dark', xdark: 'Extra dark', light: 'Light', auto: 'Auto' };
  var ORDER = ['dark', 'xdark', 'light', 'auto'];
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function read() {
    var p = {};
    try { p = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { p = {}; }
    if (!p.nav) {                       /* old menu setting: '1' = icons only, '0' = full */
      try { var o = localStorage.getItem('scope_nav'); if (o === '1') p.nav = 'mini'; } catch (e) {}
    }
    var r = {};
    for (var k in D) r[k] = (p[k] !== undefined && p[k] !== null) ? String(p[k]) : D[k];
    return r;
  }
  function apply(p) {
    var h = document.documentElement, m = p.mode;
    if (m === 'auto') m = (mq && mq.matches) ? 'light' : 'dark';
    h.setAttribute('data-mode', m);
    h.setAttribute('data-skin', p.skin);
    h.setAttribute('data-nav', p.nav);
    h.setAttribute('data-fs', p.fs);
    h.setAttribute('data-dense', p.dense);
    h.setAttribute('data-fx', p.fx);
    h.setAttribute('data-cb', p.cb);
    var t = document.querySelector('meta[name=theme-color]');
    if (t) t.setAttribute('content', BAR[m] || BAR.dark);
  }
  function save(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) {} }

  var P = read();
  apply(P);

  window.ScopePrefs = {
    get: function () { return read(); },
    set: function (k, v) {
      P = read(); P[k] = String(v); save(P); apply(P); sync();
      try { document.dispatchEvent(new CustomEvent('scopeprefs', { detail: P })); } catch (e) {}
    },
    reset: function () {
      try { localStorage.removeItem(KEY); localStorage.removeItem('scope_nav'); } catch (e) {}
      P = read(); apply(P); sync();
      try { document.dispatchEvent(new CustomEvent('scopeprefs', { detail: P })); } catch (e) {}
    },
    modeName: MODE_NAME
  };
  if (mq && mq.addEventListener) mq.addEventListener('change', function () { if (read().mode === 'auto') apply(read()); });

  /* ---------- install as app ---------- */
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault(); window.__pwa = e;
    try { document.dispatchEvent(new Event('pwaready')); } catch (x) {}
  });
  window.addEventListener('appinstalled', function () {
    window.__pwa = null;
    try { document.dispatchEvent(new Event('pwaready')); } catch (x) {}
  });
  window.ScopeInstall = function () {
    var e = window.__pwa; if (!e) return Promise.resolve(false);
    e.prompt();
    return e.userChoice.then(function (c) { window.__pwa = null; return c && c.outcome === 'accepted'; });
  };
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});
    });
  }

  /* ---------- sidebar tools ---------- */
  var ICON = {
    moon: '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>',
    xmoon: '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" fill="currentColor"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 000 18z" fill="currentColor"/>'
  };
  function $(id) { return document.getElementById(id); }
  function setTip(el, t) { if (el) el.setAttribute('data-tip', t); }
  function sync() {
    var p = read(), b = document.body; if (!b) return;
    var nt = $('navtog'), at = $('autotog'), tt = $('themetog');
    if (nt) {
      $('navtog-l').textContent = p.nav === 'full' ? 'Collapse to icons' : 'Show full names';
      setTip(nt, p.nav === 'full' ? 'Collapse to icons' : 'Show full names');
      nt.classList.toggle('on', p.nav !== 'full');
    }
    if (at) {
      $('autotog-l').textContent = p.nav === 'auto' ? 'Auto-hide: on' : 'Auto-hide menu';
      setTip(at, p.nav === 'auto' ? 'Auto-hide is on (click to turn off)' : 'Auto-hide menu');
      at.classList.toggle('on', p.nav === 'auto');
    }
    if (tt) {
      $('themetog-l').textContent = 'Mode: ' + MODE_NAME[p.mode];
      setTip(tt, 'Mode: ' + MODE_NAME[p.mode] + ' (click to change)');
      $('themetog-i').innerHTML = ICON[p.mode === 'dark' ? 'moon' : p.mode === 'xdark' ? 'xmoon' : p.mode === 'light' ? 'sun' : 'auto'];
    }
  }
  function wire() {
    var b = document.body;
    sync();
    var nt = $('navtog'), at = $('autotog'), tt = $('themetog');
    if (nt) nt.onclick = function (e) { e.preventDefault(); ScopePrefs.set('nav', read().nav === 'full' ? 'mini' : 'full'); };
    if (at) at.onclick = function (e) { e.preventDefault(); ScopePrefs.set('nav', read().nav === 'auto' ? 'full' : 'auto'); };
    if (tt) tt.onclick = function (e) {
      e.preventDefault();
      var i = ORDER.indexOf(read().mode); ScopePrefs.set('mode', ORDER[(i + 1) % ORDER.length]);
    };
    /* auto-hide: slide in when the mouse touches the left edge */
    var side = $('side'), edge = $('edge'), tm;
    if (side && edge) {
      var open = function () { clearTimeout(tm); b.classList.add('peek'); };
      var shut = function () {
        clearTimeout(tm);
        tm = setTimeout(function () { if (!side.matches(':hover') && !edge.matches(':hover')) b.classList.remove('peek'); }, 280);
      };
      edge.addEventListener('mouseenter', open); edge.addEventListener('mouseleave', shut);
      side.addEventListener('mouseenter', open); side.addEventListener('mouseleave', shut);
    }
    /* icon-only menu: floating name label */
    if (side) {
      var tip = document.createElement('div'); tip.className = 'navtip'; b.appendChild(tip);
      var hide = function () { tip.classList.remove('on'); };
      side.addEventListener('mouseover', function (e) {
        if (document.documentElement.getAttribute('data-nav') !== 'mini' || window.innerWidth <= 860) return;
        var a = e.target.closest ? e.target.closest('.menu a, .brand') : null;
        if (!a || !a.getAttribute('data-tip')) { hide(); return; }
        var r = a.getBoundingClientRect();
        tip.textContent = a.getAttribute('data-tip');
        tip.style.left = (r.right + 12) + 'px'; tip.style.top = (r.top + r.height / 2) + 'px';
        tip.classList.add('on');
      });
      side.addEventListener('mouseleave', hide);
      side.addEventListener('scroll', hide);
    }
    /* phone drawer */
    var burger = $('burger'), scrim = $('scrim');
    var drawer = function (f) { b.classList.toggle('drawer', f); };
    if (burger) burger.onclick = function () { drawer(!b.classList.contains('drawer')); };
    if (scrim) scrim.onclick = function () { drawer(false); };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();
})();
