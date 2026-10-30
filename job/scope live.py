"""
Scope Live job  (GitHub Actions ma har ~15 min market hours ma chale che - scope_new.yml ni sathe)

  - badha stocks no live price (LTP) + chg%   (getMarketData, 50 stocks / request)
  - indices (spot) no live price + aaj na divas na levels (5-min candle)
  - index futures (nazdik no contract) no live price + aaj na levels
Output: live.json   (website e vanche che; data.json karta navu hoy tyare j vaparay)

Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
"""
import os, sys, json, time, datetime as dt

MAIN = {"nifty50", "niftybank", "sensex"}               # live job ma fakat aa indices (spot + future)
DEADLINE_SEC = int(os.environ.get("LIVE_DEADLINE", "330"))      # ~5.5 min pachhi je male te save kari ne puru
BATCH = 50
SLEEP_Q = 1.05

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


def quote_once(api, exch, tokens):
    """FULL quote (1 retry) -> {token: row}."""
    for attempt in range(2):
        try:
            res = api.getMarketData("FULL", {exch: [str(t) for t in tokens]})
            if isinstance(res, dict) and res.get("status") and res.get("data"):
                return {str(q.get("symbolToken")): q for q in res["data"].get("fetched", [])}
            print("quote retry", exch, (res or {}).get("message") if isinstance(res, dict) else res, flush=True)
        except Exception as e:
            print("quote error", exch, str(e)[:120], flush=True)
        time.sleep(2)
    return {}


def build(data, master, quotes_fn, candles_fn, today=None, deadline=None, sleep_q=0, with_stocks=True, only=None):
    """quotes_fn(exch, tokens)->{token: quote}, candles_fn(exch, token)->5-min df.
    Pehla indices+futures (nana), pachhi stocks (vadhu traded pehla). deadline (time.time()) vati jaay to je male te aape."""
    from scope_daily import pick_future, day_levels, _norm
    today = today or dt.date.today()
    out = {"stocks": {}, "indices": []}

    # ---- indices spot (+ futures) - pehla. Quotes batch ma (1-2 call), candles fakat futures vala index mate
    spot_tok = {}
    for r in master:
        if r.get("exch_seg") == "NSE" and (r.get("instrumenttype") == "AMXIDX" or str(r.get("token", "")).startswith("99926")):
            spot_tok.setdefault(str(r.get("symbol")), (str(r["token"]), "NSE"))
    spot_tok.setdefault("SENSEX", ("99919000", "BSE"))
    items = []                       # (name, (token, exch), fut-or-None)
    for ix in data.get("indices", []):
        name = ix["symbol"]
        if only and _norm(name) not in only:
            continue
        st = spot_tok.get(name) or next((v for k, v in spot_tok.items() if _norm(k) == _norm(name)), None)
        if st:
            items.append((name, st, pick_future(master, name, today)))
    by_ex = {}
    for name, st, fut in items:
        by_ex.setdefault(st[1], []).append(st[0])
        if fut:
            by_ex.setdefault(fut["exch"], []).append(str(fut["token"]))
    Q = {}
    for ex, toks in by_ex.items():
        for i in range(0, len(toks), BATCH):
            for t, row in quotes_fn(ex, toks[i:i + BATCH]).items():
                Q[(ex, t)] = row
            if sleep_q:
                time.sleep(sleep_q)
    print("index quotes:", len(Q), "items:", len(items), flush=True)
    for name, st, fut in items:
        row = {"symbol": name}
        sq = Q.get((st[1], st[0]))
        v = parse_quote(sq) if sq else None
        if v:
            row["price"], row["chg"] = v[0], v[1]
        f = None
        if fut:
            fq = Q.get((fut["exch"], str(fut["token"])))
            fv = parse_quote(fq) if fq else None
            f = {"contract": fut["symbol"], "expiry": fut["expiry"]}
            if fv:
                f["price"], f["chg"] = fv[0], fv[1]
                oi = num((fq or {}).get("opnInterest"))
                if oi:
                    f["oi"] = int(oi)
            row["fut"] = f
        if fut and not (deadline and time.time() > deadline):      # 5-min candles: fakat futures vala index
            for tgt, ex, tk, lab in ((row, st[1], st[0], "index"), (f, fut["exch"], fut["token"], "future")):
                try:
                    dl = day_levels(candles_fn(ex, tk), min_bars=5)
                    if dl:
                        tgt["day"] = dl
                except Exception as e:
                    print(lab, "candles fail", name, str(e)[:100], flush=True)
        if "price" in row or "fut" in row:
            out["indices"].append(row)
    print("indices done:", len(out["indices"]), flush=True)

    if not with_stocks:
        return out

    # ---- stocks: symbol -> NSE EQ token, vadhu traded pehla
    eq = {}
    for r in master:
        if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).endswith("-EQ"):
            eq.setdefault(r["name"], r["token"])
    rows = [s for s in data.get("stocks", []) if s["symbol"] in eq]
    rows.sort(key=lambda s: -((s.get("price") or 0) * (s.get("avg_vol") or 0)))
    tok2sym = {eq[s["symbol"]]: s["symbol"] for s in rows}
    toks = [eq[s["symbol"]] for s in rows]
    nb = (len(toks) + BATCH - 1) // BATCH
    for bi in range(nb):
        if deadline and time.time() > deadline:
            print(f"deadline: {bi}/{nb} batches done", flush=True)
            break
        q = quotes_fn("NSE", toks[bi * BATCH:(bi + 1) * BATCH])
        for t, row in q.items():
            v = parse_quote(row)
            if v and t in tok2sym:
                out["stocks"][tok2sym[t]] = v
        if bi % 10 == 0:
            print(f"stocks batch {bi + 1}/{nb}, got {len(out['stocks'])}", flush=True)
        if sleep_q:
            time.sleep(sleep_q)
    return out


def main():
    import requests
    import scope_daily as sd
    master = sd.get_master()
    data = {}
    try:
        with open(os.path.join(ROOT, "data.json")) as f:
            data = json.load(f)
    except Exception as e:
        print("data.json nathi:", e); sys.exit(0)
    print("master:", len(master), "stocks in data.json:", len(data.get("stocks", [])), flush=True)
    api = sd.login()
    print("login ok", flush=True)

    def quotes_fn(exch, tokens):
        return quote_once(api, exch, tokens)

    def candles_fn(exch, token):
        df = sd.fetch_hourly(api, token, retries=2, interval="FIVE_MINUTE", days=5, exch=exch)
        time.sleep(sd.SLEEP)
        return df

    with_stocks = os.environ.get("LIVE_STOCKS", "0") == "1"       # default: fakat main indices (halku, ~40 sec)
    out = build(data, master, quotes_fn, candles_fn, deadline=time.time() + DEADLINE_SEC, sleep_q=SLEEP_Q,
                with_stocks=with_stocks, only=None if with_stocks else MAIN)
    if not out["indices"] and len(out["stocks"]) < 50:
        print("quotes aavya nathi - live.json badlyu nahi"); return
    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    out["updated"] = ist.strftime("%Y-%m-%d %H:%M")
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False)
    print("saved live.json:", len(out["stocks"]), "stocks,", len(out["indices"]), "indices")


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
