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
    INSERT OR IGNORE INTO settings VALUES('capital','1000000');
    """)
    for tbl, col in (("holdings", "manual_price REAL"), ("holdings", "manual_date TEXT"),
                     ("trades", "charges REAL")):      # charges NULL = auto estimate
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
_gmem = {"key": None, "data": None}


def get_new():
    """new.json = navi listing (IPO) no live/intraday data (GitHub, har ~15 min). Never raises."""
    if _nmem["data"] is not None and time.time() - _nmem["t"] < 300:
        return _nmem["data"]
    data, cache, local = None, os.path.join(BASE, "new_cache.json"), os.path.join(BASE, "new.json")
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/new.json",
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
        data = {"updated": None, "stocks": []}
    _nmem.update(t=time.time(), data=data)
    return data


def get_data():
    """Daily data.json + live new-listing rows (new.json). New symbols are added, known ones get fresher price."""
    base = _get_base_data()
    nw = get_new()
    rows = nw.get("stocks") or []
    if not rows:
        return base
    key = (id(base), nw.get("updated"))
    if _gmem["key"] == key:
        return _gmem["data"]
    stocks = list(base.get("stocks", []))
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
    merged = dict(base); merged["stocks"] = stocks; merged["new_updated"] = nw.get("updated")
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
    """Estimated taxes + charges (Rs) for one cash trade (NSE equity, approx).
    Rates badlay to code ma upar na numbers sudharo. F&O ma 0 (manual lakho)."""
    if t.get("kind") != "C":
        return 0.0
    q, ep = t["qty"], t["entry_price"]
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


def _charges_in(d):
    v = d.get("charges")
    if v in (None, ""):
        return None            # auto estimate
    try:
        return max(0.0, float(v))
    except ValueError:
        return None


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
        "INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date,exit_price,exit_date,note,charges) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (d["symbol"].strip().upper(), d.get("dir", "L"), d.get("kind", "C"), int(d["qty"]),
         float(d["entry_price"]), d["entry_date"],
         float(d["exit_price"]) if d.get("exit_price") not in (None, "") else None,
         d.get("exit_date") or None, d.get("note", ""), _charges_in(d)))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/trade/<int:tid>", methods=["PUT", "DELETE"])
def api_trade_edit(tid):
    if request.method == "DELETE":
        db().execute("DELETE FROM trades WHERE id=?", (tid,))
    else:
        d = request.get_json(force=True)
        db().execute(
            "UPDATE trades SET symbol=?,dir=?,kind=?,qty=?,entry_price=?,entry_date=?,exit_price=?,exit_date=?,note=?,charges=? WHERE id=?",
            (d["symbol"].strip().upper(), d.get("dir", "L"), d.get("kind", "C"), int(d["qty"]),
             float(d["entry_price"]), d["entry_date"],
             float(d["exit_price"]) if d.get("exit_price") not in (None, "") else None,
             d.get("exit_date") or None, d.get("note", ""), _charges_in(d), tid))
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