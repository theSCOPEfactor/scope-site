"""
Scope Live job  (GitHub Actions ma har ~15 min market hours ma chale che - scope_new.yml ni sathe)

  - badha stocks no live price (LTP) + chg%   (getMarketData, 50 stocks / request)
  - indices (spot) no live price + aaj na divas na levels (5-min candle)
  - index futures (nazdik no contract) no live price + aaj na levels
Output: live.json   (website e vanche che; data.json karta navu hoy tyare j vaparay)

Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
"""
import os, sys, json, time, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "live.json")


def num(x):
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def parse_quote(q):
    """Angel FULL quote row -> [price, chg%, volume] athva None."""
    p = num(q.get("ltp"))
    if not p or p <= 0:
        return None
    prev = num(q.get("close"))
    chg = num(q.get("percentChange"))
    if prev and prev > 0:
        chg = (p / prev - 1) * 100            # prev close thi pote j gano (vadhu bharosapatra)
    return [round(p, 2), round(chg, 2) if chg is not None else None, int(num(q.get("tradeVolume")) or 0)]


def build(data, master, quotes_fn, candles_fn, today=None):
    """Pure-ish: quotes_fn(exch, tokens)->{token: quote}, candles_fn(exch, token)->5-min df. Returns live dict."""
    from scope_daily import pick_future, day_levels, _norm
    today = today or dt.date.today()
    out = {"stocks": {}, "indices": []}

    # stocks: symbol -> NSE EQ token
    eq = {}
    for r in master:
        if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).endswith("-EQ"):
            eq.setdefault(r["name"], r["token"])
    want = [s["symbol"] for s in data.get("stocks", []) if s["symbol"] in eq]
    tok2sym = {eq[s]: s for s in want}
    q = quotes_fn("NSE", [eq[s] for s in want])
    for t, row in q.items():
        v = parse_quote(row)
        if v and t in tok2sym:
            out["stocks"][tok2sym[t]] = v

    # indices spot (+ futures)
    spot_tok = {}
    for r in master:
        if r.get("exch_seg") == "NSE" and (r.get("instrumenttype") == "AMXIDX" or str(r.get("token", "")).startswith("99926")):
            spot_tok.setdefault(str(r.get("symbol")), (r["token"], "NSE"))
    spot_tok.setdefault("SENSEX", ("99919000", "BSE"))
    for ix in data.get("indices", []):
        name = ix["symbol"]
        st = spot_tok.get(name) or next((v for k, v in spot_tok.items() if _norm(k) == _norm(name)), None)
        if not st:
            continue
        row = {"symbol": name}
        sq = quotes_fn(st[1], [st[0]]).get(str(st[0]))
        v = parse_quote(sq) if sq else None
        if v:
            row["price"], row["chg"] = v[0], v[1]
        try:
            dl = day_levels(candles_fn(st[1], st[0]), min_bars=5)
            if dl:
                row["day"] = dl
        except Exception as e:
            print("index candles fail", name, e)
        fut = pick_future(master, name, today)
        if fut:
            fq = quotes_fn(fut["exch"], [fut["token"]]).get(str(fut["token"]))
            fv = parse_quote(fq) if fq else None
            f = {"contract": fut["symbol"], "expiry": fut["expiry"]}
            if fv:
                f["price"], f["chg"] = fv[0], fv[1]
                oi = num((fq or {}).get("opnInterest"))
                if oi:
                    f["oi"] = int(oi)
            try:
                fd = day_levels(candles_fn(fut["exch"], fut["token"]), min_bars=5)
                if fd:
                    f["day"] = fd
            except Exception as e:
                print("future candles fail", name, e)
            row["fut"] = f
        if "price" in row or "fut" in row:
            out["indices"].append(row)
    return out


def main():
    import requests
    import scope_daily as sd
    from scope_options import quote
    master = sd.get_master()
    data = {}
    try:
        with open(os.path.join(ROOT, "data.json")) as f:
            data = json.load(f)
    except Exception as e:
        print("data.json nathi:", e); sys.exit(0)
    api = sd.login()

    def quotes_fn(exch, tokens):
        return quote(api, exch, tokens)

    def candles_fn(exch, token):
        df = sd.fetch_hourly(api, token, interval="FIVE_MINUTE", days=5, exch=exch)
        time.sleep(sd.SLEEP)
        return df

    out = build(data, master, quotes_fn, candles_fn)
    if len(out["stocks"]) < 50 and not out["indices"]:
        print("quotes aavya nathi - live.json badlyu nahi"); return
    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    out["updated"] = ist.strftime("%Y-%m-%d %H:%M")
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False)
    print("saved live.json:", len(out["stocks"]), "stocks,", len(out["indices"]), "indices")


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
