"""
Scope BSE-only stocks: jyo stock fakat BSE par listed che (jem National Stock Exchange of India, BSE code 544937),
tena live price ne new.json ma umere che. scope_new.py pachhi aa chalavvu (workflow ma te j thay che).

BSE_SYMBOLS = "NSE:544937,ABC:5xxxxx"   (naam:BSE scrip code, comma vade)
Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
"""
import os, sys, json, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
OUT = os.path.join(ROOT, "new.json")


def upsert(doc, sym, row, ts):
    """row = [price, chg, volume, (hi, lo)] -> new.json ma sym no row umero/badlo."""
    stocks = doc.setdefault("stocks", [])
    new = {"symbol": sym, "price": row[0], "chg": row[1], "chg_basis": "close", "stage": 0, "days_s2": 0,
           "rs_raw": None, "rs": None, "new": False, "bse": True, "live": True, "live_ts": ts}
    if len(row) >= 5:
        new["dhi"], new["dlo"] = row[3], row[4]
    for i, s in enumerate(stocks):
        if s.get("symbol") == sym:
            stocks[i] = dict(s, **new)
            return doc
    stocks.append(new)
    return doc


def main():
    spec = os.environ.get("BSE_SYMBOLS", "NSE:544937")
    pairs = [p.strip().split(":") for p in spec.split(",") if ":" in p]
    if not pairs:
        print("BSE_SYMBOLS khali"); return
    import scope_daily as sd
    from scope_live import quote_once, parse_quote
    api = sd.login()
    q = quote_once(api, "BSE", [c for _n, c in pairs])
    doc = {"updated": None, "stocks": []}
    try:
        with open(OUT) as f:
            doc = json.load(f)
    except Exception as e:
        print("new.json nathi, navu banavu:", e)
    ist = (dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    got = 0
    for name, code in pairs:
        row = parse_quote(q.get(str(code)) or {})
        if not row:
            print("BSE quote na malyo:", name, code); continue
        upsert(doc, name, row, ist); got += 1
        print("BSE", name, row, flush=True)
    if got:
        doc["updated"] = ist[:16]
        with open(OUT + ".tmp", "w") as f:
            json.dump(doc, f)
        os.replace(OUT + ".tmp", OUT)


if __name__ == "__main__":
    main()
