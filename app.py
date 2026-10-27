"""
Scope website (Flask) - PythonAnywhere free par chale che.
Pages: /journal  /levels  /stage2
Environment variables (WSGI file ma set karvana):
  SCOPE_PASSWORD   - login password (khali hoy to login nathi, local test mate)
  SECRET_KEY       - koi pan lambo random text
  GITHUB_TOKEN     - fine-grained token (repo: Contents read-only)
  GITHUB_REPO      - e.g. mrunal/scope-site
"""
import os, io, re, csv, json, time, sqlite3, datetime as dt
from collections import defaultdict
import requests
import accounts as acct
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, g, send_file)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "journal.db")
CACHE_PATH = os.path.join(BASE, "data_cache.json")
LOCAL_DATA = os.path.join(BASE, "data.json")   # local test mate

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
PASSWORD = os.environ.get("SCOPE_PASSWORD", "")


@app.context_processor
def inject_ver():
    """cache-busting number for static files (changes whenever a static file changes)"""
    try:
        d = os.path.join(BASE, "static")
        return {"ver": max(int(os.path.getmtime(os.path.join(d, f))) for f in os.listdir(d))}
    except Exception:
        return {"ver": 1}


# ---------------------------------------------------------------- DB
_ready = set()


def my_db_path():
    """owner -> journal.db ; bija user -> potani alag database (data mix thato nathi)"""
    u = g.get("user")
    if u is None:
        return DB_PATH
    p = acct.user_db_path(u["id"])
    if p not in _ready:
        init_db(p); _ready.add(p)
    return p


def db():
    if "db" not in g:
        g.db = sqlite3.connect(my_db_path())
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db(path=None):
    con = sqlite3.connect(path or DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS trades(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      dir TEXT NOT NULL DEFAULT 'L',          -- L / S
      kind TEXT NOT NULL DEFAULT 'C',         -- C=cash, F=F&O
      qty INTEGER NOT NULL,
      entry_price REAL NOT NULL,
      entry_date TEXT NOT NULL,
      exit_price REAL,
      exit_date TEXT,
      note TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS holdings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol TEXT NOT NULL,
      qty REAL NOT NULL,
      avg_price REAL NOT NULL,
      buy_date TEXT,
      stoploss REAL,
      target REAL,
      note TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS execs(k TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS favs(symbol TEXT PRIMARY KEY, added TEXT);
    INSERT OR IGNORE INTO settings VALUES('capital','1000000');
    """)
    for tbl, col in (("holdings", "manual_price REAL"), ("holdings", "manual_date TEXT"),
                     ("trades", "charges REAL"), ("trades", "tag TEXT")):      # charges NULL = auto estimate
        try:
            con.execute("ALTER TABLE %s ADD COLUMN %s" % (tbl, col))
        except Exception:
            pass
    con.commit(); con.close()


init_db()


# ---------------------------------------------------------------- auth (accounts.py)
acct.init_app(app)


@app.before_request
def guard():
    return acct.guard(bool(PASSWORD))


# ---------------------------------------------------------------- data (GitHub / local)
_mem = {"t": 0, "data": None, "src": "-", "err": ""}


def _clean(o):
    """NaN/Infinity are not valid JSON for browsers: turn them into null."""
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def _get_base_data():
    """GitHub (fresh) -> otherwise the newest of cached copy / local data.json. Reason is kept in _mem for /api/status."""
    if _mem["data"] and time.time() - _mem["t"] < 1800:
        return _mem["data"]
    data, src, err = None, "", ""
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/data.json",
                             headers={"Authorization": f"Bearer {tok}",
                                      "Accept": "application/vnd.github.raw"}, timeout=30)
            if r.ok:
                data, src = _clean(json.loads(r.text)), "GitHub (live)"
                with open(CACHE_PATH, "w") as f:
                    json.dump(data, f)
            else:
                err = f"GitHub answered {r.status_code}: {r.text[:160]}"
        except Exception as e:
            err = f"GitHub fetch failed: {e}"
    else:
        err = "GITHUB_TOKEN or GITHUB_REPO is not set in the WSGI file"
    if data is None:
        best = None
        for p, name in ((CACHE_PATH, "saved copy"), (LOCAL_DATA, "data.json on this server")):
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        d = _clean(json.load(f))
                except Exception:
                    continue
                if best is None or str(d.get("updated", "")) > str(best[0].get("updated", "")):
                    best = (d, name)
        if best:
            data, src = best
    if data is None:
        data, src = {"updated": "-", "count": 0, "stocks": []}, "nothing found"
    _mem.update(t=time.time() if not err else time.time() - 1680, data=data, src=src, err=err)   # on failure retry in ~2 min
    return data


@app.route("/api/status")
def api_status():
    d = get_data()
    return jsonify({"source": _mem["src"], "problem": _mem["err"] or None, "updated": d.get("updated"),
                    "stocks": len(d.get("stocks", []))})


_dmem = {"t": 0, "data": None}
DETAILS_CACHE = os.path.join(BASE, "details_cache.json")
LOCAL_DETAILS = os.path.join(BASE, "details.json")


def get_details():
    """details.json (fundamentals, news, index constituents) - GitHub, else saved copy. Never raises."""
    if _dmem["data"] is not None and time.time() - _dmem["t"] < 3600:
        return _dmem["data"]
    data = None
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/details.json",
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw"}, timeout=30)
            if r.ok:
                data = _clean(json.loads(r.text))
                with open(DETAILS_CACHE, "w") as f:
                    json.dump(data, f)
        except Exception:
            data = None
    if data is None:
        for p in (DETAILS_CACHE, LOCAL_DETAILS):
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        d = _clean(json.load(f))
                    if data is None or str(d.get("updated", "")) > str(data.get("updated", "")):
                        data = d
                except Exception:
                    pass
    if data is None:
        data = {"updated": None, "indices": {}, "stocks": {}}
    _dmem.update(t=time.time() if data["updated"] else time.time() - 3300, data=data)
    return data


_nmem = {"t": 0, "data": None}
_lmem = {"t": 0, "data": None}
_gmem = {"key": None, "data": None}


def _gh_json(name, mem, ttl, empty):
    """GitHub repo ni json file (cache sathe). Never raises."""
    if mem["data"] is not None and time.time() - mem["t"] < ttl:
        return mem["data"]
    stem = name.rsplit(".", 1)[0]
    data, cache, local = None, os.path.join(BASE, stem + "_cache.json"), os.path.join(BASE, name)
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/{name}",
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw"}, timeout=20)
            if r.ok:
                data = _clean(json.loads(r.text))
                with open(cache, "w") as f:
                    json.dump(data, f)
        except Exception:
            data = None
    if data is None:
        for pth in (cache, local):
            if os.path.exists(pth):
                try:
                    with open(pth) as f:
                        data = _clean(json.load(f)); break
                except Exception:
                    pass
    if data is None:
        data = empty
    mem.update(t=time.time(), data=data)
    return data


def get_new():
    """new.json = navi listing (IPO) no live/intraday data (GitHub, har ~15 min). Never raises."""
    return _gh_json("new.json", _nmem, 300, {"updated": None, "stocks": []})


def get_live():
    """live.json = badha stocks/indices/futures no live price (GitHub, har ~15 min). Never raises."""
    return _gh_json("live.json", _lmem, 120, {"updated": None, "stocks": {}, "indices": []})


def _zone_of(price, f):
    if not f:
        return None
    if price > f["breakout"]:
        return "Above VHIGH"
    if price < f["breakdown"]:
        return "Below VLOW"
    if price >= f["sell_reversal"]:
        return "Near VHIGH (sell zone)"
    if price <= f["buy_reversal"]:
        return "Near VLOW (buy zone)"
    return "Inside range"


def _merge_index(base_ix, live_ix, futs, lts):
    """Index row = daily levels + live price; futures joined (basis = future - spot)."""
    r = dict(base_ix)
    lv = live_ix or {}
    if lv.get("price") is not None:
        r["price"] = lv["price"]
        if lv.get("chg") is not None:
            r["chg"] = lv["chg"]
        r["lts"] = lts
    ld = lv.get("day")
    if ld and (not r.get("day") or ld.get("date", "") >= r["day"].get("date", "")):
        r["day"] = ld
    fb = futs.get(base_ix["symbol"])
    fl = lv.get("fut")
    if fb or fl:
        f = dict(fb or {})
        f.pop("token", None); f.pop("exch", None)
        if fl:
            for k in ("contract", "expiry"):
                if fl.get(k):
                    f[k] = fl[k]
            if fl.get("price") is not None:
                f["price"] = fl["price"]; f["chg"] = fl.get("chg", f.get("chg")); f["lts"] = lts
            if fl.get("oi"):
                f["oi"] = fl["oi"]
            fd = fl.get("day")
            if fd and (not f.get("day") or fd.get("date", "") >= f["day"].get("date", "")):
                f["day"] = fd
        if f.get("price") is not None and r.get("price") is not None:
            f["basis"] = round(f["price"] - r["price"], 2)
        r["fut"] = f
    return r


def get_data():
    """Daily data.json + live prices (live.json) + live new-listing rows (new.json)."""
    base = _get_base_data()
    nw, lv = get_new(), get_live()
    rows = nw.get("stocks") or []
    lstocks = lv.get("stocks") or {}
    use_live = bool(lv.get("updated")) and (lstocks or lv.get("indices")) and str(lv["updated"]) >= str(base.get("updated") or "")
    if not rows and not use_live and not base.get("futures"):
        return base
    key = (id(base), nw.get("updated"), lv.get("updated") if use_live else None)
    if _gmem["key"] == key:
        return _gmem["data"]
    stocks = list(base.get("stocks", []))
    if use_live:
        for i, s in enumerate(stocks):
            v = lstocks.get(s["symbol"])
            if v and v[0]:
                b = dict(s)
                b["price"] = v[0]
                if v[1] is not None:
                    b["chg"] = v[1]
                b["lts"] = lv["updated"]
                if b.get("fib"):
                    b["zone"] = _zone_of(b["price"], b["fib"]) or b.get("zone")
                if b.get("hi52"):
                    b["from_hi"] = round((b["price"] / b["hi52"] - 1) * 100, 1)
                stocks[i] = b
    idx = {x["symbol"]: i for i, x in enumerate(stocks)}
    for r in rows:
        i = idx.get(r["symbol"])
        if i is None:
            stocks.append(r)
        else:
            b = dict(stocks[i])
            for k in ("price", "chg", "live", "live_ts", "intra", "listing"):
                if r.get(k) is not None:
                    b[k] = r[k]
            stocks[i] = b
    merged = dict(base); merged["stocks"] = stocks
    if rows:
        merged["new_updated"] = nw.get("updated")
    if use_live:
        futs = {f["index"]: f for f in base.get("futures", [])}
        lix = {x["symbol"]: x for x in lv.get("indices", [])}
        merged["indices"] = [_merge_index(x, lix.get(x["symbol"]), futs, lv["updated"]) for x in base.get("indices", [])]
        merged["live_updated"] = lv["updated"]
    else:
        futs = {f["index"]: f for f in base.get("futures", [])}
        if futs:
            merged["indices"] = [_merge_index(x, None, futs, None) for x in base.get("indices", [])]
    _gmem.update(key=key, data=merged)
    return merged


_bmem = {"t": 0, "data": None}


def get_backtest():
    """backtest.json (GitHub, else saved copy). Never raises."""
    if _bmem["data"] is not None and time.time() - _bmem["t"] < 3600:
        return _bmem["data"]
    data, cache, local = None, os.path.join(BASE, "backtest_cache.json"), os.path.join(BASE, "backtest.json")
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/backtest.json",
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw"}, timeout=30)
            if r.ok:
                data = _clean(json.loads(r.text))
                with open(cache, "w") as f:
                    json.dump(data, f)
        except Exception:
            data = None
    if data is None:
        for pth in (cache, local):
            if os.path.exists(pth):
                try:
                    with open(pth) as f:
                        data = _clean(json.load(f)); break
                except Exception:
                    pass
    if data is None:
        data = {"updated": None, "rows": [], "by_stock": {}}
    _bmem.update(t=time.time() if data["updated"] else time.time() - 3300, data=data)
    return data


_omem = {"t": 0, "data": None}


def get_openzone():
    """openzone.json (GitHub, else saved copy). Never raises."""
    if _omem["data"] is not None and time.time() - _omem["t"] < 3600:
        return _omem["data"]
    data, cache, local = None, os.path.join(BASE, "openzone_cache.json"), os.path.join(BASE, "openzone.json")
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/openzone.json",
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw"}, timeout=30)
            if r.ok:
                data = _clean(json.loads(r.text))
                with open(cache, "w") as f:
                    json.dump(data, f)
        except Exception:
            data = None
    if data is None:
        for pth in (cache, local):
            if os.path.exists(pth):
                try:
                    with open(pth) as f:
                        data = _clean(json.load(f)); break
                except Exception:
                    pass
    if data is None:
        data = {"updated": None, "indices": {}}
    _omem.update(t=time.time() if data["updated"] else time.time() - 3300, data=data)
    return data


@app.route("/api/openzone")
def api_openzone():
    return jsonify(get_openzone())


@app.route("/openzone")
def openzone_page():
    return render_template("openzone.html", active="openzone", updated=get_data()["updated"])


_opmem = {"t": 0, "data": None}


def get_options():
    """options.json (GitHub, else saved copy). 5 min cache. Never raises."""
    if _opmem["data"] is not None and time.time() - _opmem["t"] < 300:
        return _opmem["data"]
    data, cache, local = None, os.path.join(BASE, "options_cache.json"), os.path.join(BASE, "options.json")
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/options.json",
                             headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github.raw"}, timeout=30)
            if r.ok:
                data = _clean(json.loads(r.text))
                with open(cache, "w") as f:
                    json.dump(data, f)
        except Exception:
            data = None
    if data is None:
        for pth in (cache, local):
            if os.path.exists(pth):
                try:
                    with open(pth) as f:
                        data = _clean(json.load(f)); break
                except Exception:
                    pass
    if data is None:
        data = {"updated": None, "indices": {}}
    _opmem.update(t=time.time() if data.get("updated") else time.time() - 240, data=data)
    return data


@app.route("/api/options")
def api_options():
    return jsonify(get_options())


def _tg_state_path():
    return os.path.join(acct.DATA_DIR, "tg_state.json")


def _tg_load():
    try:
        with open(_tg_state_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _tg_save(st):
    try:
        with open(_tg_state_path(), "w") as f:
            json.dump(st, f)
    except Exception as e:
        print("tg state save error:", e)


def _tg_send(chat, text):
    tok = os.environ.get("TG_BOT_TOKEN", "")
    return requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                         data={"chat_id": chat, "text": text}, timeout=20)


@app.route("/tg/<secret>", methods=["POST"])
def tg_hook(secret):
    """Telegram bot webhook: 'NIFTY low high poc' -> option chain zones reply. Fakt owner na chat ne j jawab."""
    import hmac
    from flask import abort
    want = os.environ.get("TG_SECRET", "")
    tok = os.environ.get("TG_BOT_TOKEN", "")
    if not want or not tok or not hmac.compare_digest(secret, want):
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), want):
        abort(404)
    try:
        m = (request.get_json(silent=True) or {}).get("message") or {}
        chat = str((m.get("chat") or {}).get("id") or "")
        text = m.get("text") or ""
        if chat:
            owner = os.environ.get("TG_CHAT_ID", "").strip()
            if not owner:
                out = f"Tamaro chat id: {chat}\nAa number PythonAnywhere ni WSGI file ma TG_CHAT_ID tarike mukine Reload karo."
            elif chat != owner:
                out = None
            elif text.strip().lower() in ("/start", "start", "help", "/help"):
                from zones import HELP
                out = HELP
            else:
                import zones
                opt = get_options()
                out = zones.reply_for(text, opt)
                st = _tg_load()
                zones.register_levels(st, text, opt, acct.now_ist().date().isoformat())
                _tg_save(st)
            if out:
                _tg_send(chat, out)
    except Exception as e:
        print("tg_hook error:", e)
    return "ok"


@app.route("/tg/cron/<secret>")
def tg_cron(secret):
    """GitHub Action aa link kholse.
    task=auto   : market time ma - aajna levels hoy to auto reading / alert
    task=eod    : sanje - favourite stocks level ni najik hoy to digest
    task=weekly : shanivare - journal nu week review"""
    import hmac
    from flask import abort
    import zones
    want = os.environ.get("TG_SECRET", "")
    owner = os.environ.get("TG_CHAT_ID", "").strip()
    if not want or not hmac.compare_digest(secret, want):
        abort(404)
    if not owner:
        return "idle (TG_CHAT_ID is not set in the WSGI file)"
    task = request.args.get("task", "auto")
    if task in ("eod", "weekly"):
        msgs = []
        try:
            if task == "eod":
                _mem["t"] = 0                                   # taaja data.json lavo
                d = get_data()
                favs = [r["symbol"] for r in db().execute("SELECT symbol FROM favs ORDER BY added, symbol").fetchall()]
                t = zones.fav_digest(favs, d.get("stocks", []), d.get("updated"))
                if t:
                    msgs.append(t)
            else:
                msgs.append(zones.review_text(review_data("week")))
            res = []
            for t in msgs:
                r = _tg_send(owner, t)
                res.append(str(r.status_code) + ("" if r.status_code == 200 else " " + r.text[:150]))
            return f"task={task}: sent {len(msgs)}" + (f" (telegram: {', '.join(res)})" if res else " (nothing to send: no favourites found in the data)")
        except Exception as e:
            print("tg_cron task error:", e)
            return f"task={task}: ERROR {type(e).__name__}: {str(e)[:200]}"
    now = acct.now_ist()
    if now.weekday() >= 5 or not (9 <= now.hour < 16):
        return "idle (market closed; the automatic alert runs Mon-Fri 9:00-16:00 IST)"
    today = now.date().isoformat()
    _opmem["t"] = 0                          # taaje taaru options.json lavo
    opt = get_options()
    st = _tg_load()
    msgs, st = zones.cron_step(st, opt, today)
    if not any(v.get("date") == today for k, v in st.items() if not k.startswith("_")) and st.get("_nudge") != today:
        msgs.append("Aaj na Low/High haji nathi malya. Moklo: NIFTY 22406 22545 22480 (naam, Low, High, POC)")
        st["_nudge"] = today
    res = []
    for t in msgs:
        try:
            r = _tg_send(owner, t)
            res.append(str(r.status_code) + ("" if r.status_code == 200 else " " + r.text[:150]))
        except Exception as e:
            print("tg_cron send error:", e)
            res.append("error " + str(e)[:100])
    _tg_save(st)
    return f"sent {len(msgs)}" + (f" (telegram: {', '.join(res)})" if res else "")


@app.route("/settings")
def settings_page():
    return render_template("settings.html", active="settings")


@app.route("/manifest.webmanifest")
def manifest():
    icons = [{"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
             {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
             {"src": url_for("static", filename="icons/maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    m = {"name": "Scope", "short_name": "Scope", "description": "Scope - precision . vision . success .",
         "id": "/", "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any",
         "background_color": "#050914", "theme_color": "#050914", "icons": icons}
    r = app.response_class(json.dumps(m), mimetype="application/manifest+json")
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.route("/sw.js")
def service_worker():
    r = send_file(os.path.join(BASE, "static", "sw.js"), mimetype="application/javascript")
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Service-Worker-Allowed"] = "/"
    return r


@app.route("/offline")
def offline_page():
    return render_template("offline.html")


@app.route("/options")
def options_page():
    return render_template("options.html", active="options", updated=get_data()["updated"])


@app.route("/api/backtest")
def api_backtest():
    return jsonify(get_backtest())


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("&", "and"))


@app.route("/api/stocks")
def api_stocks():
    d = get_data()
    det = get_details().get("stocks", {})
    for s in d.get("stocks", []):                      # industry comes from details.json (used by the Sectors page)
        if not s.get("industry") and det.get(s["symbol"], {}).get("industry"):
            s["industry"] = det[s["symbol"]]["industry"]
    return jsonify(d)


@app.route("/api/detail/<sym>")
def api_detail(sym):
    sym = _norm_sym(sym)
    d, det = get_data(), get_details()
    stock = next((s for s in d["stocks"] if s["symbol"] == sym), None)
    detail = det.get("stocks", {}).get(sym) or {}
    member = [v["label"] for k, v in {x["label"]: x for x in det.get("indices", {}).values()}.items() if sym in v["symbols"]]

    # Peers = same industry (from details), ranked by RS then market cap
    peers = []
    ind = (detail.get("industry") or detail.get("y_industry") or "").strip()
    if ind:
        by_sym = {s["symbol"]: s for s in d.get("stocks", [])}
        cand = []
        for psym, pdet in (det.get("stocks") or {}).items():
            if psym == sym:
                continue
            pind = (pdet.get("industry") or pdet.get("y_industry") or "").strip()
            if pind != ind:
                continue
            row = by_sym.get(psym)
            if not row:
                continue
            cand.append({
                "symbol": psym,
                "name": pdet.get("name"),
                "price": row.get("price"),
                "chg": row.get("chg"),
                "rs": row.get("rs"),
                "stage": row.get("stage"),
                "zone": row.get("zone"),
                "mcap_cr": pdet.get("mcap_cr"),
                "pe": pdet.get("pe"),
            })
        cand.sort(key=lambda x: (
            -(x["rs"] if x["rs"] is not None else -1),
            -(x["mcap_cr"] if x["mcap_cr"] is not None else -1),
        ))
        peers = cand[:12]

    return jsonify({
        "symbol": sym,
        "stock": stock,
        "detail": detail,
        "member_of": member,
        "peers": peers,
        "peers_industry": ind or None,
        "history": get_backtest().get("by_stock", {}).get(sym),
        "details_updated": det.get("updated"),
        "updated": d.get("updated"),
    })

@app.route("/api/index")
def api_index():
    name = request.args.get("name", "")
    d, det = get_data(), get_details()
    row = next((i for i in d.get("indices", []) if i["symbol"] == name), None)
    ent = det.get("indices", {}).get(_norm(name))
    members, missing = [], 0
    if ent:
        by = {s["symbol"]: s for s in d["stocks"]}
        for sym in ent["symbols"]:
            if sym in by:
                s = dict(by[sym]); s["industry"] = det.get("stocks", {}).get(sym, {}).get("industry"); members.append(s)
            else:
                missing += 1
    return jsonify({"name": name, "index": row, "found": bool(ent), "total": len(ent["symbols"]) if ent else 0,
                    "missing": missing, "members": members, "updated": d.get("updated")})


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    return render_template("home.html", active="home", updated=get_data()["updated"])


@app.route("/levels")
def levels():
    return render_template("levels.html", active="levels", updated=get_data()["updated"])


@app.route("/stage2")
def stage2():
    return render_template("stage2.html", active="stage2", updated=get_data()["updated"])


@app.route("/journal")
def journal():
    return render_template("journal.html", active="journal", start="trades")


@app.route("/plan")
def plan_page():
    return render_template("journal.html", active="plan", start="plan")


@app.route("/stock")
@app.route("/stock/<sym>")
def stock_page(sym=""):
    return render_template("stock.html", active="stock", sym=_norm_sym(sym), updated=get_data()["updated"])


@app.route("/index/<path:name>")
def index_page(name):
    return render_template("index.html", active="home", name=name, updated=get_data()["updated"])


@app.route("/backtest")
def backtest_page():
    return render_template("backtest.html", active="backtest", updated=get_data()["updated"])


@app.route("/help")
def help_page():
    return render_template("help.html", active="help", updated=get_data()["updated"])


@app.route("/sectors")
def sectors():
    return render_template("sectors.html", active="sectors", updated=get_data()["updated"])


@app.route("/portfolio")
def portfolio():
    return render_template("portfolio.html", active="portfolio", updated=get_data()["updated"])


@app.route("/backup")
def backup():
    """download the whole journal + portfolio database (keep a copy somewhere safe)"""
    return send_file(my_db_path(), as_attachment=True, download_name="scope-backup-" + dt.date.today().isoformat() + ".db")


# ---------------------------------------------------------------- journal logic
def get_capital():
    row = db().execute("SELECT v FROM settings WHERE k='capital'").fetchone()
    return float(row["v"]) if row else 0.0


def pnl_of(t, ltp):
    """return (gross pnl, pct_move, exit_or_ltp)"""
    px = t["exit_price"] if t["exit_price"] is not None else ltp
    if px is None:
        return 0.0, 0.0, None
    sign = 1 if t["dir"] == "L" else -1
    pnl = (px - t["entry_price"]) * t["qty"] * sign
    pct = (px / t["entry_price"] - 1) * 100 * sign
    return pnl, pct, px


def get_brokerage():
    """(delivery per order, intraday per order cap) in Rs - settings ma thi"""
    out = []
    for k, d in (("brk_del", 0.0), ("brk_intra", 20.0)):
        r = db().execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        try:
            out.append(float(r["v"]) if r else d)
        except ValueError:
            out.append(d)
    return out


def est_charges(t, brk):
    """Estimated taxes + charges (Rs): cash, futures (F), options (O) - NSE, approx.
    Rates badlay to code ma numbers sudharo."""
    q, ep = t["qty"], t["entry_price"]
    if t.get("kind") in ("F", "O"):
        opt = t["kind"] == "O"
        legs = [("B" if t["dir"] == "L" else "S", ep * q)]
        if t.get("exit_price") is not None:
            legs.append(("S" if t["dir"] == "L" else "B", t["exit_price"] * q))
        tot = 0.0
        for side, v in legs:
            brokerage = brk[1]                                  # flat per order (F&O)
            txn = (0.0003553 if opt else 0.0000183) * v         # NSE F&O exchange charge
            sebi = 0.000001 * v
            gst = 0.18 * (brokerage + txn + sebi)
            stt = ((0.0015 if opt else 0.0005) * v) if side == "S" else 0.0      # STT from 1 Apr 2026
            stamp = round((0.00003 if opt else 0.00002) * v) if side == "B" else 0.0   # broker whole rupee ma ganave che
            tot += brokerage + txn + sebi + gst + stt + stamp
        return round(tot, 2)
    if t.get("kind") != "C":
        return 0.0
    closed = t.get("exit_price") is not None
    intraday = closed and t.get("exit_date") and t["exit_date"] == t["entry_date"]
    legs = [("B" if t["dir"] == "L" else "S", ep * q)]
    if closed:
        legs.append(("S" if t["dir"] == "L" else "B", t["exit_price"] * q))
    tot = 0.0
    for side, v in legs:
        brokerage = min(brk[1], 0.0003 * v) if intraday else brk[0]
        if brokerage and brk[0] == 0 and not intraday:
            brokerage = 0
        txn = 0.0000297 * v                      # exchange transaction charge
        sebi = 0.000001 * v                      # Rs 10 per crore
        gst = 0.18 * (brokerage + txn + sebi)
        if intraday:
            stt = 0.00025 * v if side == "S" else 0.0
            stamp = 0.00003 * v if side == "B" else 0.0
        else:
            stt = 0.001 * v
            stamp = 0.00015 * v if side == "B" else 0.0
        dp = 15.93 if (not intraday and side == "S" and t["dir"] == "L") else 0.0   # DP charge on sell
        tot += brokerage + txn + sebi + gst + stt + stamp + dp
    return round(tot, 2)


def net_of(t, ltp, brk):
    """(gross, charges, net, pct, px, charges_is_auto)"""
    gross, pct, px = pnl_of(t, ltp)
    auto = t.get("charges") is None
    ch = est_charges(t, brk) if auto else float(t["charges"])
    if px is None:                 # LTP nathi: charges (entry side) batavo, net ganvo nahi
        return 0.0, ch, 0.0, 0.0, None, auto
    return gross, ch, gross - ch, pct, px, auto


def days_between(a, b):
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except Exception:
        return 0


@app.route("/api/journal")
def api_journal():
    month = request.args.get("month") or dt.date.today().strftime("%Y-%m")
    prices = {s["symbol"]: s["price"] for s in get_data()["stocks"]}
    today = dt.date.today().isoformat()
    rows = db().execute("SELECT * FROM trades ORDER BY entry_date DESC, id DESC").fetchall()

    brk = get_brokerage()
    out, month_totals = [], defaultdict(float)
    realized = unreal = gross_real = ch_real = 0.0
    wins, losses = [], []
    exposure, open_n = 0.0, 0
    for r in rows:
        t = dict(r)
        ltp = prices.get(t["symbol"])
        gross, ch, pnl, pct, px, auto = net_of(t, ltp, brk)
        closed = t["exit_price"] is not None
        if closed:
            month_totals[t["exit_date"][:7]] += pnl
        in_month = (t["entry_date"][:7] == month) or (closed and t["exit_date"][:7] == month) or (not closed)
        if not closed:
            open_n += 1
            exposure += t["entry_price"] * t["qty"]
        if not in_month:
            continue
        held = days_between(t["entry_date"], t["exit_date"] if closed else today)
        t.update(pnl=round(pnl, 2), gross=round(gross, 2), ch=round(ch, 2), ch_auto=auto,
                 pct=round(pct, 2), px=px, closed=closed, held=held)
        out.append(t)
        if closed and t["exit_date"][:7] == month:
            realized += pnl; gross_real += gross; ch_real += ch
            (wins if pnl > 0 else losses).append(t)
        elif not closed:
            unreal += pnl

    cap = get_capital()
    n_closed = len(wins) + len(losses)
    avg = lambda a: round(sum(a) / len(a), 1) if a else 0
    stats = {
        "capital": cap, "exposure": round(exposure), "open": open_n,
        "realized": round(realized), "unrealized": round(unreal),
        "gross_realized": round(gross_real), "charges": round(ch_real),
        "brk_del": brk[0], "brk_intra": brk[1],
        "roi": round((realized + unreal) / cap * 100, 1) if cap else 0,
        "wl": f"{len(wins)} / {len(losses)}",
        "batting": round(len(wins) / n_closed * 100) if n_closed else 0,
        "win_pct": round(sum(w["pct"] for w in wins) / len(wins), 1) if wins else 0,
        "loss_pct": round(sum(l["pct"] for l in losses) / len(losses), 1) if losses else 0,
        "win_amt": round(sum(w["pnl"] for w in wins)),
        "loss_amt": round(sum(l["pnl"] for l in losses)),
        "avg_held": avg([t["held"] for t in wins + losses]),
        "w_held": avg([t["held"] for t in wins]),
        "l_held": avg([t["held"] for t in losses]),
    }
    return jsonify({"trades": out, "stats": stats, "months": {k: round(v) for k, v in month_totals.items()}})


def _tag_in(d):
    return (d.get("tag") or "").strip()[:40]


def _charges_in(d):
    v = d.get("charges")
    if v in (None, ""):
        return None            # auto estimate
    try:
        return max(0.0, float(v))
    except ValueError:
        return None


def _range_for(period, today):
    mon = today - dt.timedelta(days=today.weekday())
    first = today.replace(day=1)
    if period == "week":
        return mon, today
    if period == "lastweek":
        return mon - dt.timedelta(days=7), mon - dt.timedelta(days=1)
    if period == "lastmonth":
        e = first - dt.timedelta(days=1)
        return e.replace(day=1), e
    if period == "all":
        return dt.date(2000, 1, 1), today
    return first, today


@app.route("/api/review")
def api_review():
    return jsonify(review_data(request.args.get("period", "week")))


def review_data(period):
    """Closed trades (exit date in the period) -> win rate, payoff, profit factor, weekday / tag split, plain tips."""
    today = dt.date.today()
    a, b = _range_for(period, today)
    brk = get_brokerage()
    rows = db().execute("SELECT * FROM trades WHERE exit_price IS NOT NULL AND exit_date IS NOT NULL "
                        "ORDER BY exit_date, id").fetchall()
    tr = []
    for r in rows:
        t = dict(r)
        if not (a.isoformat() <= t["exit_date"] <= b.isoformat()):
            continue
        gross, ch, net, pct, px, auto = net_of(t, None, brk)
        tr.append({"symbol": t["symbol"], "date": t["exit_date"], "net": net, "gross": gross, "ch": ch,
                   "tag": (t.get("tag") or "").strip()})
    n = len(tr)
    out = {"range": [a.isoformat(), b.isoformat()], "n": n, "tips": []}
    if not n:
        out["tips"].append("No closed trades in this period yet.")
        return out
    wins = [t for t in tr if t["net"] > 0]
    losses = [t for t in tr if t["net"] <= 0]
    gw, gl = sum(t["net"] for t in wins), -sum(t["net"] for t in losses)
    avg_w = gw / len(wins) if wins else 0.0
    avg_l = gl / len(losses) if losses else 0.0
    net = sum(t["net"] for t in tr)
    chg = sum(t["ch"] for t in tr)
    streak = cur = 0
    for t in tr:
        cur = cur + 1 if t["net"] <= 0 else 0
        streak = max(streak, cur)
    wd = defaultdict(lambda: [0, 0.0, 0])
    tg = defaultdict(lambda: [0, 0.0, 0])
    for t in tr:
        d = dt.date.fromisoformat(t["date"]).strftime("%a")
        for acc, key in ((wd, d), (tg, t["tag"] or "(no tag)")):
            acc[key][0] += 1; acc[key][1] += t["net"]; acc[key][2] += 1 if t["net"] > 0 else 0
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    best = max(tr, key=lambda t: t["net"]); worst = min(tr, key=lambda t: t["net"])
    out.update(
        wins=len(wins), losses=len(losses), win_rate=round(len(wins) / n * 100), net=round(net),
        gross=round(sum(t["gross"] for t in tr)), charges=round(chg),
        avg_win=round(avg_w), avg_loss=round(-avg_l),
        payoff=round(avg_w / avg_l, 2) if avg_l else None,
        pf=round(gw / gl, 2) if gl else None,
        expectancy=round(net / n), streak=streak,
        best={"symbol": best["symbol"], "net": round(best["net"]), "date": best["date"]},
        worst={"symbol": worst["symbol"], "net": round(worst["net"]), "date": worst["date"]},
        weekday=[{"d": d, "n": wd[d][0], "net": round(wd[d][1]), "wr": round(wd[d][2] / wd[d][0] * 100)} for d in order if d in wd],
        tags=sorted([{"tag": k, "n": v[0], "net": round(v[1]), "wr": round(v[2] / v[0] * 100)} for k, v in tg.items()],
                    key=lambda x: x["net"]))
    tips = out["tips"]
    inr_ = lambda v: ("-" if v < 0 else "") + "₹" + format(abs(int(round(v))), ",")
    if n < 5:
        tips.append(f"Only {n} closed trade{'s' if n != 1 else ''} in this period, so treat these numbers as a rough guide, not a verdict.")
    if avg_l and avg_w and avg_l > avg_w:
        tips.append(f"Your average loss ({inr_(avg_l)}) is bigger than your average win ({inr_(avg_w)}). Cutting losers sooner or letting winners run a little longer would help most.")
    if len(wins) / n < 0.4 and avg_l and avg_w / avg_l < 1.5:
        tips.append("Win rate is under 40% and your wins are not much bigger than your losses. This mix loses money over time; be pickier about entries.")
    gp = sum(t["gross"] for t in tr if t["gross"] > 0)
    if gp > 0 and chg > 0.25 * gp:
        tips.append(f"Charges took {round(chg / gp * 100)}% of your gross profit ({inr_(chg)}). Fewer, better-planned trades keep more of what you earn.")
    if streak >= 3:
        tips.append(f"Longest losing run: {streak} trades in a row. After 2 losses a break or a smaller size protects your capital.")
    bad = [t for t in out["tags"] if t["tag"].lower().startswith("mistake") and t["net"] < 0]
    if bad:
        tips.append("Trades tagged as mistakes cost you " + inr_(-sum(t["net"] for t in bad)) + ". Naming them is the first step to stopping them.")
    wk = [d for d in out["weekday"] if d["n"] >= 2]
    if len(wk) >= 2:
        w = min(wk, key=lambda d: d["net"])
        if w["net"] < 0:
            tips.append(f"Your weakest day is {w['d']} ({inr_(w['net'])} over {w['n']} trades).")
    if net > 0 and out["expectancy"] > 0:
        tips.append(f"On average each trade earned {inr_(out['expectancy'])} after charges. Keep your size and rules the same.")
    elif net <= 0:
        tips.append(f"On average each trade lost {inr_(-out['expectancy'])} after charges. Review the worst trade ({worst['symbol']}) first.")
    return out


@app.route("/api/brokerage", methods=["POST"])
def api_brokerage():
    d = request.get_json(force=True)
    for k in ("brk_del", "brk_intra"):
        try:
            v = max(0.0, min(1000.0, float(d.get(k, 0))))
        except (TypeError, ValueError):
            continue
        db().execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/trade", methods=["POST"])
def api_trade_add():
    d = request.get_json(force=True)
    db().execute(
        "INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date,exit_price,exit_date,note,charges,tag) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (d["symbol"].strip().upper(), d.get("dir", "L"), d.get("kind", "C"), int(d["qty"]),
         float(d["entry_price"]), d["entry_date"],
         float(d["exit_price"]) if d.get("exit_price") not in (None, "") else None,
         d.get("exit_date") or None, d.get("note", ""), _charges_in(d), _tag_in(d)))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/trade/<int:tid>", methods=["PUT", "DELETE"])
def api_trade_edit(tid):
    if request.method == "DELETE":
        db().execute("DELETE FROM trades WHERE id=?", (tid,))
    else:
        d = request.get_json(force=True)
        db().execute(
            "UPDATE trades SET symbol=?,dir=?,kind=?,qty=?,entry_price=?,entry_date=?,exit_price=?,exit_date=?,note=?,charges=?,tag=? WHERE id=?",
            (d["symbol"].strip().upper(), d.get("dir", "L"), d.get("kind", "C"), int(d["qty"]),
             float(d["entry_price"]), d["entry_date"],
             float(d["exit_price"]) if d.get("exit_price") not in (None, "") else None,
             d.get("exit_date") or None, d.get("note", ""), _charges_in(d), _tag_in(d), tid))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/favs")
def api_favs():
    rows = db().execute("SELECT symbol FROM favs ORDER BY added, symbol").fetchall()
    return jsonify(symbols=[r["symbol"] for r in rows])


@app.route("/api/fav", methods=["POST"])
def api_fav():
    d = request.get_json(force=True, silent=True) or {}
    sym = _norm_sym(d.get("symbol", ""))
    if not sym or len(sym) > 30:
        return jsonify(ok=False), 400
    if d.get("on"):
        n = db().execute("SELECT COUNT(*) FROM favs").fetchone()[0]
        if n >= 200:
            return jsonify(ok=False, error="Up to 200 favourites"), 400
        db().execute("INSERT OR IGNORE INTO favs(symbol,added) VALUES(?,?)", (sym, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    else:
        db().execute("DELETE FROM favs WHERE symbol=?", (sym,))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/plan", methods=["GET", "POST"])
def api_plan():
    """Goal plan (target earning / time / % benefit). Sheet browser ma banay che."""
    con = db()
    if request.method == "POST":
        con.execute("INSERT OR REPLACE INTO settings VALUES('plan',?)", (json.dumps(request.get_json(force=True)),))
        con.commit()
        return jsonify(ok=True)
    row = con.execute("SELECT v FROM settings WHERE k='plan'").fetchone()
    plan = json.loads(row["v"]) if row else None
    start = (plan or {}).get("start") or "0000-01-01"
    prices = {s["symbol"]: s["price"] for s in get_data()["stocks"]}
    realized = unreal = 0.0
    wins, losses = [], []
    brk = get_brokerage()
    for r in con.execute("SELECT * FROM trades").fetchall():
        t = dict(r)
        _g, _c, pnl, pct, _px, _a = net_of(t, prices.get(t["symbol"]), brk)
        if t["exit_price"] is not None:
            (wins if pnl > 0 else losses).append(pct)      # all-time stats
            if t["exit_date"] and t["exit_date"] >= start:
                realized += pnl
        else:
            unreal += pnl
    n = len(wins) + len(losses)
    actual = {
        "realized": round(realized, 2), "unrealized": round(unreal, 2), "n_closed": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else None,
    }
    return jsonify(plan=plan, capital=get_capital(), actual=actual)


@app.route("/api/pnl")
def api_pnl():
    """closed trades (exit date + P&L) and open unrealized P&L: feeds the plan sheet's Actual column"""
    prices = {s["symbol"]: s["price"] for s in get_data()["stocks"]}
    closed, unreal = [], 0.0
    brk = get_brokerage()
    for r in db().execute("SELECT * FROM trades").fetchall():
        t = dict(r)
        _g, _c, pnl, _p, _x, _a = net_of(t, prices.get(t["symbol"]), brk)
        if t["exit_price"] is not None:
            if t["exit_date"]:
                closed.append({"date": t["exit_date"], "pnl": round(pnl, 2), "symbol": t["symbol"]})
        else:
            unreal += pnl
    return jsonify({"closed": closed, "unrealized": round(unreal, 2)})


# ---------------------------------------------------------------- portfolio (holdings)
def _num(x, default=None):
    try:
        s = str(x).replace(",", "").replace("₹", "").strip()
        return float(s) if s != "" else default
    except ValueError:
        return default


def _norm_sym(s):
    s = str(s or "").strip().upper().split(":")[-1]          # NSE:RELIANCE -> RELIANCE
    for suf in ("-EQ", ".NS", "-BE"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip()


@app.route("/api/holdings")
def api_holdings():
    data = get_data()
    stocks = {s["symbol"]: s for s in data["stocks"]}
    out = []
    for r in db().execute("SELECT * FROM holdings ORDER BY symbol").fetchall():
        h = dict(r)
        s = stocks.get(h["symbol"]) or {}
        h.update(ltp=s.get("price"), chg=s.get("chg"), stage=s.get("stage"), rs=s.get("rs"), zone=s.get("zone"),
                 vlow=s.get("vlow"), vhigh=s.get("vhigh"), known=bool(s), manual=False)
        if not s and h.get("manual_price"):                 # exchange par nathi (unlisted) -> tamaro nakhelo price
            h.update(ltp=h["manual_price"], manual=True)
        out.append(h)
    return jsonify({"holdings": out, "updated": data.get("updated")})


@app.route("/api/holding", methods=["POST"])
def api_holding_add():
    d = request.get_json(force=True)
    sym, qty, avg = _norm_sym(d.get("symbol")), _num(d.get("qty")), _num(d.get("avg_price"))
    if not sym or not qty or qty <= 0 or not avg or avg <= 0:
        return jsonify(ok=False, error="Symbol, quantity and average price are required"), 400
    mp = _num(d.get("manual_price"))
    db().execute("INSERT INTO holdings(symbol,qty,avg_price,buy_date,stoploss,target,note,manual_price,manual_date) VALUES(?,?,?,?,?,?,?,?,?)",
                 (sym, qty, avg, d.get("buy_date") or None, _num(d.get("stoploss")), _num(d.get("target")), d.get("note", ""),
                  mp, dt.date.today().isoformat() if mp else None))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/holding/<int:hid>", methods=["PUT", "DELETE"])
def api_holding_edit(hid):
    if request.method == "DELETE":
        db().execute("DELETE FROM holdings WHERE id=?", (hid,))
    else:
        d = request.get_json(force=True)
        sym, qty, avg = _norm_sym(d.get("symbol")), _num(d.get("qty")), _num(d.get("avg_price"))
        if not sym or not qty or qty <= 0 or not avg or avg <= 0:
            return jsonify(ok=False, error="Symbol, quantity and average price are required"), 400
        mp = _num(d.get("manual_price"))
        old = db().execute("SELECT manual_price, manual_date FROM holdings WHERE id=?", (hid,)).fetchone()
        mdate = (old["manual_date"] if old and old["manual_price"] == mp else dt.date.today().isoformat()) if mp else None
        db().execute("UPDATE holdings SET symbol=?,qty=?,avg_price=?,buy_date=?,stoploss=?,target=?,note=?,manual_price=?,manual_date=? WHERE id=?",
                     (sym, qty, avg, d.get("buy_date") or None, _num(d.get("stoploss")), _num(d.get("target")),
                      d.get("note", ""), mp, mdate, hid))
    db().commit()
    return jsonify(ok=True)


def _pick(row, names):
    norm = {re.sub(r"[^a-z0-9]", "", (k or "").lower()): v for k, v in row.items()}
    for n in names:
        if n in norm and str(norm[n]).strip() != "":
            return norm[n]
    return None


@app.route("/api/holdings/import", methods=["POST"])
def api_holdings_import():
    """Broker holdings CSV (e.g. Zerodha: Instrument, Qty., Avg. cost). Same symbol = updated, new symbol = added."""
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="No file"), 400
    text = f.read().decode("utf-8-sig", errors="ignore")
    con, added, updated = db(), 0, 0
    for row in csv.DictReader(io.StringIO(text)):
        sym = _norm_sym(_pick(row, ["symbol", "instrument", "tradingsymbol", "scrip", "stock", "name"]))
        qty = _num(_pick(row, ["qty", "quantity", "shares"]))
        avg = _num(_pick(row, ["avgcost", "averagecost", "avgprice", "averageprice", "buyprice", "avgbuyprice", "buyavg", "price"]))
        if not sym or not qty or qty <= 0 or not avg or avg <= 0:
            continue
        ex = con.execute("SELECT id FROM holdings WHERE symbol=?", (sym,)).fetchone()
        if ex:
            con.execute("UPDATE holdings SET qty=?, avg_price=? WHERE id=?", (qty, avg, ex["id"]))
            updated += 1
        else:
            con.execute("INSERT INTO holdings(symbol,qty,avg_price) VALUES(?,?,?)", (sym, qty, avg))
            added += 1
    con.commit()
    return jsonify(ok=True, added=added, updated=updated)


@app.route("/api/capital", methods=["POST"])
def api_capital():
    v = float(request.get_json(force=True)["capital"])
    db().execute("INSERT OR REPLACE INTO settings VALUES('capital',?)", (str(v),))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/clear", methods=["POST"])
def api_clear():
    db().execute("DELETE FROM trades")
    db().execute("DELETE FROM execs")
    db().commit()
    return jsonify(ok=True)


def _norm_key(k):
    return re.sub(r"[^a-z0-9]+", "_", (k or "").strip().lower()).strip("_")


def _col(row, *names):
    for n in names:
        for k in row:
            if _norm_key(k) == n and row[k] not in (None, ""):
                return row[k]
    return None


_MON = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def _parse_when(v):
    """'2026-10-22T09:31:05', '22-10-2026 09:31', '22/10/26', '22-Oct-2026', ... -> ('2026-10-22', '09:31:05')"""
    v = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ]+(\d{1,2}:\d{2}(?::\d{2})?))?", v)
    if m:
        y, mo, d, tm = m.groups()
    else:
        m = re.match(r"^(\d{1,2})[-/ .]([A-Za-z]{3,9}|\d{1,2})[-/ .](\d{2,4})(?:[T ,]+(\d{1,2}:\d{2}(?::\d{2})?))?", v)
        if not m:
            return None, ""
        d, mo, y, tm = m.groups()
        if not mo.isdigit():
            mo = _MON.get(mo[:3].lower())
            if not mo:
                return None, ""
        if len(y) == 2:
            y = "20" + y
    try:
        iso = dt.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None, ""
    tm = tm or ""
    return iso, (tm + ":00" if tm.count(":") == 1 else tm)


def _clean_sym(x):
    x = str(x).strip().upper()
    x = re.sub(r"^(NSE|BSE)[:\-]", "", x)
    x = re.sub(r"(-EQ|-BE|\.NS|\.BO)$", "", x)
    return x


@app.route("/api/import", methods=["POST"])
def api_import():
    """Tradebook CSV (Zerodha / Angel One / Dhan jevi). Buy/sell ne FIFO thi jodi ne trades banave che.
    - same-day (intraday) trades samay (time) mujab, na hoy to file na kram mujab
    - juna open trades sathe pan joday (kal kharidyu, aaje vechyu)
    - bijivaar same file upload karo to duplicate nathi banta"""
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="File nathi"), 400
    text = f.read().decode("utf-8-sig", errors="ignore")
    lines = text.splitlines()
    # kyarek top ma client info hoy che: header line shodho
    hdr = 0
    for n, ln in enumerate(lines[:40]):
        low = ln.lower()
        if ("qty" in low or "quantity" in low) and ("price" in low or "rate" in low) and ("symbol" in low or "scrip" in low or "name" in low or "stock" in low or "instrument" in low):
            hdr = n
            break
    execs, idx, skipped = [], 0, 0
    for row in csv.DictReader(io.StringIO("\n".join(lines[hdr:]))):
        sym = _col(row, "symbol", "tradingsymbol", "trading_symbol", "scrip", "scrip_name", "stock", "stock_name",
                   "security_name", "instrument", "instrument_name", "name")
        when = _col(row, "trade_date", "date", "order_execution_time", "trade_time", "exchange_timestamp",
                    "execution_time", "order_date", "trade_date_time", "time")
        side = _col(row, "trade_type", "type", "side", "buy_sell", "buy_sell_flag", "transaction_type", "b_s", "action")
        qty = _col(row, "quantity", "qty", "traded_qty", "trade_quantity", "trade_qty", "executed_qty")
        price = _col(row, "price", "trade_price", "avg_price", "average_price", "rate", "trade_rate", "net_rate")
        tm_col = _col(row, "trade_time", "time", "exchange_time", "order_execution_time")
        tid = _col(row, "trade_id", "order_id", "exchange_order_id", "trade_no", "trade_number") or ""
        if not (sym and when and side and qty and price):
            skipped += 1
            continue
        dte, tme = _parse_when(when)
        if not tme and tm_col:
            tme = _parse_when("2000-01-01 " + str(tm_col))[1] or ""
        if not dte:
            skipped += 1
            continue
        sd = str(side).strip().lower()
        sign = 1 if sd.startswith("b") else (-1 if sd.startswith("s") else 0)
        q, p = _num(qty), _num(price)
        if not sign or not q or not p or q <= 0 or p <= 0:
            skipped += 1
            continue
        sym = _clean_sym(sym)
        key = "|".join([dte, tme, sym, str(sign), str(int(q)), "%.4f" % p, str(tid)])
        execs.append((dte, tme, idx, sym, sign, int(q), p, key))
        idx += 1
    if not execs:
        return jsonify(ok=False, error="Koi trade vanchay nahi. File ma symbol, date, buy/sell, quantity, price column joiye. "
                       "Header ni pehli line mane moklo to hu mate sudhari aapu."), 400
    desc = execs[0][0] > execs[-1][0]
    execs.sort(key=lambda e: (e[0], e[1], -e[2] if desc else e[2]))
    con = db()
    have = {r["k"] for r in con.execute("SELECT k FROM execs")}
    execs = [e for e in execs if e[7] not in have]
    dup = idx - len(execs)
    if not execs:
        return jsonify(ok=True, created=0, closed=0, dup=dup, skipped=skipped)
    # juna open trades (je symbols aa file ma che) ne lots tarike lo
    books = defaultdict(list)       # sym -> [sign, qty, price, date, row_id|None]
    for sym in {e[3] for e in execs}:
        for r in con.execute("SELECT * FROM trades WHERE symbol=? AND exit_price IS NULL AND kind='C' "
                             "ORDER BY entry_date, id", (sym,)):
            books[sym].append([1 if r["dir"] == "L" else -1, r["qty"], r["entry_price"], r["entry_date"], r["id"]])
    created = closed = 0
    for dte, tme, _i, sym, sign, qty, price, key in execs:
        lots = books[sym]
        while qty > 0 and lots and lots[0][0] != sign:
            lot = lots[0]
            m = min(qty, lot[1])
            d = "L" if lot[0] == 1 else "S"
            if lot[4]:       # juno open trade: close kari do (adhuro hoy to split)
                if m == lot[1]:
                    con.execute("UPDATE trades SET exit_price=?, exit_date=? WHERE id=?", (price, dte, lot[4]))
                else:
                    con.execute("UPDATE trades SET qty=?, exit_price=?, exit_date=? WHERE id=?", (m, price, dte, lot[4]))
                    r0 = con.execute("SELECT * FROM trades WHERE id=?", (lot[4],)).fetchone()
                    cur = con.execute("INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date,note) VALUES(?,?,?,?,?,?,?)",
                                      (sym, d, "C", lot[1] - m, lot[2], lot[3], r0["note"] or ""))
                    lot[4] = cur.lastrowid
                    lot[1] -= m; qty -= m
                    closed += 1
                    continue
            else:
                con.execute("INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date,exit_price,exit_date) VALUES(?,?,?,?,?,?,?,?)",
                            (sym, d, "C", m, lot[2], lot[3], price, dte))
                created += 1
            closed += 1
            lot[1] -= m; qty -= m
            if lot[1] == 0:
                lots.pop(0)
        if qty > 0:
            lots.append([sign, qty, price, dte, None])
    for sym, lots in books.items():
        for sign, qty, price, d0, rid in lots:
            if rid is None:
                con.execute("INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date) VALUES(?,?,?,?,?,?)",
                            (sym, "L" if sign == 1 else "S", "C", qty, price, d0))
                created += 1
    con.executemany("INSERT OR IGNORE INTO execs(k) VALUES(?)", [(e[7],) for e in execs])
    con.commit()
    return jsonify(ok=True, created=created, closed=closed, dup=dup, skipped=skipped)
@app.route("/compare")
def compare():
    return render_template("compare.html", active="compare", updated=get_data()["updated"])

if __name__ == "__main__":
    app.run(debug=True, port=5000)