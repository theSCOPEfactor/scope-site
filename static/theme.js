/* Scope: look & feel settings (saved on this device) + sidebar tools + app install */
(function () {
  var KEY = 'scope_prefs';
  var D = { mode: 'dark', skin: 'ocean', nav: 'full', fs: 'md', dense: '0', fx: 'full', cb: '0', dockhide: '0', dockmag: '1' };
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
    h.setAttribute('data-dockhide', p.dockhide);
    h.setAttribute('data-dockmag', p.dockmag);
    try { var sd = document.getElementById('side'); if (sd) clearMag(sd); } catch (e) {}
    var t = document.querySelector('meta[name=theme-color]');
    if (t) t.setAttribute('content', BAR[m] || BAR.dark);
  }
  function save(p) { try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) {} }

  function clearMag(side) {
    [].forEach.call(side.querySelectorAll('.menu a, .brand'), function (a) { a.style.transform = ''; });
  }
  function isDock() {
    var n = document.documentElement.getAttribute('data-nav');
    return (n === 'dockb' || n === 'dockt') && window.innerWidth > 860;
  }

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
    var nt = $('navtog'), at = $('autotog'), tt = $('themetog'), dt = $('docktog');
    if (dt) {
      var onDock = p.nav === 'dockb' || p.nav === 'dockt';
      $('docktog-l').textContent = onDock ? 'Side menu' : 'Dock menu';
      setTip(dt, onDock ? 'Back to the side menu' : 'Dock menu (icons like Mac / Linux)');
      dt.classList.toggle('on', onDock);
    }
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
    var nt = $('navtog'), at = $('autotog'), tt = $('themetog'), dt = $('docktog'), qj = $('qjtog');
    if (dt) dt.onclick = function (e) {
      e.preventDefault(); var n = read().nav;
      ScopePrefs.set('nav', (n === 'dockb' || n === 'dockt') ? 'full' : 'dockb');
    };
    if (qj) qj.onclick = function (e) { e.preventDefault(); quick(true); };
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
    /* icon-only menu / dock: floating name label + dock magnification */
    if (side) {
      var tip = document.createElement('div'); tip.className = 'navtip'; b.appendChild(tip);
      var hide = function () { tip.classList.remove('on'); };
      var place = function (a) {
        var nav = document.documentElement.getAttribute('data-nav'), r = a.getBoundingClientRect();
        tip.textContent = a.getAttribute('data-tip');
        if (nav === 'dockb' || nav === 'dockt') {
          tip.className = 'navtip ' + (nav === 'dockb' ? 'up' : 'down');
          tip.style.left = (r.left + r.width / 2) + 'px';
          tip.style.top = (nav === 'dockb' ? r.top - 12 : r.bottom + 12) + 'px';
        } else {
          tip.className = 'navtip';
          tip.style.left = (r.right + 12) + 'px'; tip.style.top = (r.top + r.height / 2) + 'px';
        }
        tip.classList.add('on');
      };
      side.addEventListener('mouseover', function (e) {
        var nav = document.documentElement.getAttribute('data-nav');
        if (window.innerWidth <= 860 || !(nav === 'mini' || nav === 'dockb' || nav === 'dockt')) return;
        var a = e.target.closest ? e.target.closest('.menu a, .brand') : null;
        if (!a || !a.getAttribute('data-tip')) { hide(); return; }
        place(a);
      });
      side.addEventListener('mousemove', function (e) {
        if (!isDock()) return;
        var top = document.documentElement.getAttribute('data-nav') === 'dockt';
        var mag = read().dockmag === '1' && side.scrollWidth <= side.clientWidth + 2;
        var mx = e.clientX - side.getBoundingClientRect().left;
        [].forEach.call(side.querySelectorAll('.menu a, .brand'), function (a) {
          if (!mag) { a.style.transform = ''; return; }
          var c = a.offsetLeft + a.offsetWidth / 2, d = Math.abs(mx - c);
          var k = Math.max(0, 1 - d / 130), sc = 1 + 0.62 * k * k;
          a.style.transformOrigin = top ? '50% 0' : '50% 100%';
          a.style.transform = sc > 1.01 ? 'scale(' + sc.toFixed(3) + ')' : '';
        });
      });
      side.addEventListener('mouseleave', function () { hide(); clearMag(side); });
      side.addEventListener('scroll', hide);
    }
    /* phone drawer */
    var burger = $('burger'), scrim = $('scrim');
    var drawer = function (f) { b.classList.toggle('drawer', f); };
    if (burger) burger.onclick = function () { drawer(!b.classList.contains('drawer')); };
    if (scrim) scrim.onclick = function () { drawer(false); };
  }

  /* ---------- quick jump (Ctrl/Cmd + K, or "/") ---------- */
  var QJ = null, STOCKS = null, qsel = 0, qitems = [];
  function loadStocks() {
    if (STOCKS !== null) return; STOCKS = [];
    fetch('/api/stocks', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
      STOCKS = (d && d.stocks) || [];
      if (QJ && QJ.classList.contains('on')) qrender();
    }).catch(function () {});
  }
  function pages() {
    var out = [];
    [].forEach.call(document.querySelectorAll('#side .menu a[href]'), function (a) {
      var h = a.getAttribute('href');
      if (!h || h === '#' || /logout|backup/.test(h)) return;
      var t = a.getAttribute('data-tip') || a.textContent.trim();
      out.push({ t: t, h: h, k: 'Page' });
    });
    out.push({ t: 'Terms & Privacy', h: '/terms', k: 'Page' });
    return out;
  }
  function qrender() {
    var q = QJ.querySelector('input').value.trim().toLowerCase(), ul = QJ.querySelector('ul');
    var res = pages().filter(function (x) { return !q || x.t.toLowerCase().indexOf(q) > -1; });
    if (q && STOCKS && STOCKS.length) {
      var n = 0;
      for (var i = 0; i < STOCKS.length && n < 8; i++) {
        var s = STOCKS[i], sym = String(s.symbol || ''), nm = String(s.name || '');
        if (sym.toLowerCase().indexOf(q) === 0 || sym.toLowerCase().indexOf(q) > -1 || nm.toLowerCase().indexOf(q) > -1) {
          res.push({ t: sym + (nm ? ' · ' + nm : ''), h: '/stock/' + encodeURIComponent(sym), k: 'Stock' }); n++;
        }
      }
    }
    qitems = res.slice(0, 14); if (qsel >= qitems.length) qsel = 0;
    ul.innerHTML = qitems.length ? qitems.map(function (x, i) {
      return '<li data-i="' + i + '" class="' + (i === qsel ? 'on' : '') + '"><span>' + x.t.replace(/[&<>]/g, '') + '</span><em>' + x.k + '</em></li>';
    }).join('') : '<li class="none">Nothing found</li>';
  }
  function quick(open) {
    if (!document.getElementById('side')) return;
    if (!QJ) {
      QJ = document.createElement('div'); QJ.className = 'qj';
      QJ.innerHTML = '<div class="box"><input type="text" placeholder="Jump to a page or search a stock..." autocomplete="off" spellcheck="false">' +
        '<ul></ul><div class="qh">↑ ↓ to move · Enter to open · Esc to close</div></div>';
      document.body.appendChild(QJ);
      QJ.addEventListener('mousedown', function (e) {
        var li = e.target.closest('li[data-i]');
        if (li) { location.href = qitems[+li.dataset.i].h; } else if (e.target === QJ) { quick(false); }
      });
      QJ.querySelector('input').addEventListener('input', function () { qsel = 0; qrender(); });
      QJ.querySelector('input').addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { qsel = (qsel + 1) % Math.max(1, qitems.length); qrender(); e.preventDefault(); }
        else if (e.key === 'ArrowUp') { qsel = (qsel - 1 + qitems.length) % Math.max(1, qitems.length); qrender(); e.preventDefault(); }
        else if (e.key === 'Enter') { if (qitems[qsel]) location.href = qitems[qsel].h; }
        else if (e.key === 'Escape') { quick(false); }
      });
    }
    if (open) { QJ.classList.add('on'); qsel = 0; var i = QJ.querySelector('input'); i.value = ''; qrender(); loadStocks(); setTimeout(function () { i.focus(); }, 30); }
    else QJ.classList.remove('on');
  }
  document.addEventListener('keydown', function (e) {
    var tg = e.target, typing = tg && (/^(INPUT|TEXTAREA|SELECT)$/.test(tg.tagName) || tg.isContentEditable);
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); quick(!(QJ && QJ.classList.contains('on'))); }
    else if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); quick(true); }
    else if (e.key === 'Escape' && QJ && QJ.classList.contains('on')) quick(false);
  });
  window.addEventListener('resize', function () { var sd = document.getElementById('side'); if (sd) clearMag(sd); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire); else wire();
})();
