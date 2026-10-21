"""
Scope New-listing job  (GitHub Actions ma har ~15 min market hours ma chale che)

Navi listing (IPO) no data turant lavva mate. Fib / 52-week levels NATHI banavta;
fakat: price, listing-day open, intraday (aaj na divas na) levels.

Kya stocks:
  1. Angel master ma je token navo dekhay (pehla vaar joyo) -> 30 divas sudhi watch
  2. watch.txt ma tame lakhel symbols (dar line ma ek)
  3. data.json ma je stock ni history 20 divas thi ochhi che (new)
Output: new.json   (website e vanche che)

Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
Optional env: EXTRA_SYMBOLS="ABC,XYZ" (manual run mate)
"""
import os, sys, json, time, datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KNOWN = os.path.join(ROOT, "known_tokens.json")
WATCH = os.path.join(ROOT, "watch.txt")
KEEP_DAYS = 30
MAX_WATCH = 80
NEW_BARS = 20          # data.json ma 20 divas thi ochha -> new


def build_row(sym, m5, token=None):
    """5-min candles -> ek row (pure function, test thai sake)."""
    from scope_daily import volume_profile
    m5 = m5.sort_values("ts").drop_duplicates("ts")
    t = m5["ts"].dt.time
    m5 = m5[(t >= dt.time(9, 15)) & (t <= dt.time(15, 30))].copy()
    if len(m5) < 2:
        return None
    m5["date"] = m5["ts"].dt.date
    g = m5.groupby("date").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"),
                               c=("c", "last"), v=("v", "sum"))
    price = float(g["c"].iloc[-1])
    first_open = float(g["o"].iloc[0])
    prev = float(g["c"].iloc[-2]) if len(g) > 1 else first_open     # pehla divas: te divas na open thi
    row = {
        "symbol": sym, "price": round(price, 2), "chg": round((price / prev - 1) * 100, 2),
        "chg_basis": "close" if len(g) > 1 else "open",
        "stage": 0, "days_s2": 0, "rs_raw": None, "rs": None,
        "hi52": round(float(g["h"].max()), 2), "lo52": round(float(g["l"].min()), 2),
        "from_hi": round((price / float(g["h"].max()) - 1) * 100, 1),
        "listed_days": int(len(g)), "bars": int(len(g)), "new": True, "live": True,
        "avg_vol": int(g["v"].tail(20).mean()),
        "live_ts": str(m5["ts"].iloc[-1]),
        "listing": {"date": str(g.index[0]), "open": round(first_open, 2),
                    "high": round(float(g["h"].max()), 2), "low": round(float(g["l"].min()), 2),
                    "vs_open": round((price / first_open - 1) * 100, 2)},
    }
    last = g.index[-1]
    d = m5[m5["date"] == last]
    if len(d) >= 6:
        p = volume_profile(d, bins=40)
        if p:
            row["intra"] = {"date": str(last), "open": round(float(d["o"].iloc[0]), 2),
                            "hi": round(float(d["h"].max()), 2), "lo": round(float(d["l"].min()), 2),
                            **{k: round(v, 2) for k, v in p.items()}}
    return row


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def pick_token(rows, name):
    """Symbol na naam thi NSE token: EQ pehla, pachhi bija series."""
    c = [r for r in rows if r.get("exch_seg") == "NSE" and r.get("name") == name]
    c.sort(key=lambda r: (0 if str(r.get("symbol", "")).endswith("-EQ") else 1))
    return c[0]["token"] if c else None


def main():
    import requests
    from scope_daily import login, fetch_hourly, SCRIP_URL, SLEEP
    rows = requests.get(SCRIP_URL, timeout=180).json()
    today = dt.date.today().isoformat()

    # 1) navi tokens
    cur = {}
    for r in rows:
        if r.get("exch_seg") == "NSE" and "-" in str(r.get("symbol", "")) \
                and str(r["symbol"]).rsplit("-", 1)[1] in ("EQ", "BE", "SM", "ST", "BZ"):
            cur.setdefault(r["token"], r["name"])
    st = read_json(KNOWN, {"known": [], "recent": {}})
    known = set(st.get("known", []))
    recent = st.get("recent", {})
    if known:
        for tok, nm in cur.items():
            if tok not in known:
                recent[tok] = {"symbol": nm, "first_seen": today}
    st["known"] = sorted(set(known) | set(cur))
    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    st["recent"] = {k: v for k, v in recent.items() if v["first_seen"] >= cutoff}
    with open(KNOWN, "w") as f:
        json.dump(st, f)

    # 2) watch.txt + EXTRA_SYMBOLS
    names = [v["symbol"] for v in st["recent"].values()]
    if os.path.exists(WATCH):
        for ln in open(WATCH):
            ln = ln.split("#")[0].strip().upper()
            if ln:
                names.append(ln.replace("-EQ", ""))
    names += [x.strip().upper() for x in os.environ.get("EXTRA_SYMBOLS", "").split(",") if x.strip()]
    # 3) data.json ma ochha divas vala
    data = read_json(os.path.join(ROOT, "data.json"), {"stocks": []})
    for s in data.get("stocks", []):
        if s.get("new") and (s.get("bars") or 999) < NEW_BARS:
            names.append(s["symbol"])
    seen, watch = set(), []
    for n in names:
        if n not in seen:
            seen.add(n); watch.append(n)
    watch = watch[:MAX_WATCH]
    print("Watch:", watch)

    out = {"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "stocks": [], "missing": []}
    if watch:
        api = login()
        for n in watch:
            tok = pick_token(rows, n)
            if not tok:
                out["missing"].append(n); print("master ma nathi:", n); continue
            m5 = fetch_hourly(api, tok, interval="FIVE_MINUTE", days=30); time.sleep(SLEEP)
            if m5 is None or len(m5) < 2:
                out["missing"].append(n); print("candle nathi:", n); continue
            r = build_row(n, m5)
            if r:
                out["stocks"].append(r)
    with open(os.path.join(ROOT, "new.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("saved new.json:", [s["symbol"] for s in out["stocks"]], "missing:", out["missing"])


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
