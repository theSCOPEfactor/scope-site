"""
Scope website (Flask) - PythonAnywhere free par chale che.
Pages: /journal  /levels  /stage2
Environment variables (WSGI file ma set karvana):
  SCOPE_PASSWORD   - login password (khali hoy to login nathi, local test mate)
  SECRET_KEY       - koi pan lambo random text
  GITHUB_TOKEN     - fine-grained token (repo: Contents read-only)
  GITHUB_REPO      - e.g. mrunal/scope-site
"""
import os, io, csv, json, time, sqlite3, datetime as dt
from collections import defaultdict
import requests
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, g)

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "journal.db")
CACHE_PATH = os.path.join(BASE, "data_cache.json")
LOCAL_DATA = os.path.join(BASE, "data.json")   # local test mate

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
PASSWORD = os.environ.get("SCOPE_PASSWORD", "")


# ---------------------------------------------------------------- DB
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    con = sqlite3.connect(DB_PATH)
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
    INSERT OR IGNORE INTO settings VALUES('capital','1000000');
    """)
    con.commit(); con.close()


init_db()


# ---------------------------------------------------------------- auth
@app.before_request
def guard():
    if not PASSWORD:
        return
    if request.endpoint in ("login", "static"):
        return
    if not session.get("ok"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["ok"] = True
            session.permanent = True
            return redirect(url_for("journal"))
        err = "Password khotu che"
    return render_template("login.html", err=err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- data (GitHub / local)
_mem = {"t": 0, "data": None}


def get_data():
    if _mem["data"] and time.time() - _mem["t"] < 1800:
        return _mem["data"]
    data = None
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPO")
    if tok and repo:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/contents/data.json",
                             headers={"Authorization": f"Bearer {tok}",
                                      "Accept": "application/vnd.github.raw"}, timeout=30)
            if r.ok:
                data = r.json()
                with open(CACHE_PATH, "w") as f:
                    json.dump(data, f)
        except Exception:
            data = None
    if data is None:
        for p in (CACHE_PATH, LOCAL_DATA):
            if os.path.exists(p):
                with open(p) as f:
                    data = json.load(f)
                break
    if data is None:
        data = {"updated": "-", "count": 0, "stocks": []}
    _mem.update(t=time.time(), data=data)
    return data


@app.route("/api/stocks")
def api_stocks():
    return jsonify(get_data())


# ---------------------------------------------------------------- pages
@app.route("/")
def home():
    return redirect(url_for("journal"))


@app.route("/levels")
def levels():
    return render_template("levels.html", active="levels", updated=get_data()["updated"])


@app.route("/stage2")
def stage2():
    return render_template("stage2.html", active="stage2", updated=get_data()["updated"])


@app.route("/journal")
def journal():
    return render_template("journal.html", active="journal")


# ---------------------------------------------------------------- journal logic
def get_capital():
    row = db().execute("SELECT v FROM settings WHERE k='capital'").fetchone()
    return float(row["v"]) if row else 0.0


def pnl_of(t, ltp):
    """return (pnl, pct_move, exit_or_ltp)"""
    px = t["exit_price"] if t["exit_price"] is not None else ltp
    if px is None:
        return 0.0, 0.0, None
    sign = 1 if t["dir"] == "L" else -1
    pnl = (px - t["entry_price"]) * t["qty"] * sign
    pct = (px / t["entry_price"] - 1) * 100 * sign
    return pnl, pct, px


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

    out, month_totals = [], defaultdict(float)
    realized = unreal = 0.0
    wins, losses = [], []
    exposure, open_n = 0.0, 0
    for r in rows:
        t = dict(r)
        ltp = prices.get(t["symbol"])
        pnl, pct, px = pnl_of(t, ltp)
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
        t.update(pnl=round(pnl, 2), pct=round(pct, 2), px=px, closed=closed, held=held)
        out.append(t)
        if closed and t["exit_date"][:7] == month:
            realized += pnl
            (wins if pnl > 0 else losses).append(t)
        elif not closed:
            unreal += pnl

    cap = get_capital()
    n_closed = len(wins) + len(losses)
    avg = lambda a: round(sum(a) / len(a), 1) if a else 0
    stats = {
        "capital": cap, "exposure": round(exposure), "open": open_n,
        "realized": round(realized), "unrealized": round(unreal),
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


@app.route("/api/trade", methods=["POST"])
def api_trade_add():
    d = request.get_json(force=True)
    db().execute(
        "INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date,exit_price,exit_date,note) VALUES(?,?,?,?,?,?,?,?,?)",
        (d["symbol"].strip().upper(), d.get("dir", "L"), d.get("kind", "C"), int(d["qty"]),
         float(d["entry_price"]), d["entry_date"],
         float(d["exit_price"]) if d.get("exit_price") not in (None, "") else None,
         d.get("exit_date") or None, d.get("note", "")))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/trade/<int:tid>", methods=["PUT", "DELETE"])
def api_trade_edit(tid):
    if request.method == "DELETE":
        db().execute("DELETE FROM trades WHERE id=?", (tid,))
    else:
        d = request.get_json(force=True)
        db().execute(
            "UPDATE trades SET symbol=?,dir=?,kind=?,qty=?,entry_price=?,entry_date=?,exit_price=?,exit_date=?,note=? WHERE id=?",
            (d["symbol"].strip().upper(), d.get("dir", "L"), d.get("kind", "C"), int(d["qty"]),
             float(d["entry_price"]), d["entry_date"],
             float(d["exit_price"]) if d.get("exit_price") not in (None, "") else None,
             d.get("exit_date") or None, d.get("note", ""), tid))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/capital", methods=["POST"])
def api_capital():
    v = float(request.get_json(force=True)["capital"])
    db().execute("INSERT OR REPLACE INTO settings VALUES('capital',?)", (str(v),))
    db().commit()
    return jsonify(ok=True)


@app.route("/api/clear", methods=["POST"])
def api_clear():
    db().execute("DELETE FROM trades")
    db().commit()
    return jsonify(ok=True)


def _col(row, *names):
    for n in names:
        for k in row:
            if k and k.strip().lower().replace(" ", "_") == n:
                return row[k]
    return None


@app.route("/api/import", methods=["POST"])
def api_import():
    """Tradebook CSV (Zerodha jevi): symbol, trade_date, trade_type, quantity, price.
    FIFO thi buy/sell jodi ne trades banave che."""
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="File nathi"), 400
    text = f.read().decode("utf-8-sig", errors="ignore")
    execs = []
    for row in csv.DictReader(io.StringIO(text)):
        sym = _col(row, "symbol", "tradingsymbol", "scrip", "stock")
        date = _col(row, "trade_date", "date", "order_execution_time")
        side = _col(row, "trade_type", "type", "side", "buy/sell")
        qty = _col(row, "quantity", "qty")
        price = _col(row, "price", "trade_price", "avg_price")
        if not (sym and date and side and qty and price):
            continue
        try:
            execs.append((str(date)[:10], sym.strip().upper(), 1 if side.strip().lower().startswith("b") else -1,
                          int(float(qty)), float(price)))
        except ValueError:
            continue
    execs.sort()
    books = defaultdict(list)     # symbol -> list of [sign, qty, price, date] (open lots)
    created = 0
    con = db()
    for date, sym, sign, qty, price in execs:
        lots = books[sym]
        while qty > 0 and lots and lots[0][0] != sign:
            lot = lots[0]
            m = min(qty, lot[1])
            d = "L" if lot[0] == 1 else "S"
            con.execute("INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date,exit_price,exit_date) VALUES(?,?,?,?,?,?,?,?)",
                        (sym, d, "C", m, lot[2], lot[3], price, date))
            created += 1
            lot[1] -= m; qty -= m
            if lot[1] == 0:
                lots.pop(0)
        if qty > 0:
            lots.append([sign, qty, price, date])
    for sym, lots in books.items():
        for sign, qty, price, date in lots:
            con.execute("INSERT INTO trades(symbol,dir,kind,qty,entry_price,entry_date) VALUES(?,?,?,?,?,?)",
                        (sym, "L" if sign == 1 else "S", "C", qty, price, date))
            created += 1
    con.commit()
    return jsonify(ok=True, created=created)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
