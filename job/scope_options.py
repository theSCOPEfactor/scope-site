"""
Scope Options job  (GitHub Actions ma har ~30 min market hours ma chale che)

Nifty 50, Nifty Bank, Sensex mate option chain (nazdik na expiries, ATM na aaju baaju na strikes):
  price (LTP), prev close, OI, volume, IV (Black-Scholes thi), delta
  OI badlav = aaj nu OI - kal na divas nu chhelu OI  (options_state.json ma saachvay che)
Output: options.json  (website /options page vanche che)

Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
Optional env: EXPIRIES (default 3), WINDOW (default 0.07 = spot ni +-7% strikes)
"""
import os, sys, json, math, time, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "options.json")
STATE = os.path.join(ROOT, "options_state.json")
N_EXP = int(os.environ.get("EXPIRIES", "3"))
WINDOW = float(os.environ.get("WINDOW", "0.07"))
RATE = 0.065
SLEEP = 1.1                  # market-data API: ~1 request / second
BATCH = 50

# (site naam, master "name", options exchange, spot exchange, spot token, strike-step jovu hoy to)
INDICES = [("Nifty 50", "NIFTY", "NFO", "NSE", "99926000"),
           ("Nifty Bank", "BANKNIFTY", "NFO", "NSE", "99926009"),
           ("Sensex", "SENSEX", "BFO", "BSE", "99919000")]


# ------------------------------------------------------------ maths (pure, testable)
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, r, sigma, call):
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if call:
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def implied_vol(price, S, K, T, r, call):
    """IV % (bisection). None = nikalvu shakya nathi (price intrinsic thi nichhu / T khub nano)."""
    if not price or price <= 0 or T < 1.0 / 365 / 24 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, (S - K * math.exp(-r * T)) if call else (K * math.exp(-r * T) - S))
    if price <= intrinsic + 1e-6:
        return None
    lo, hi = 0.01, 3.0
    if bs_price(S, K, T, r, hi, call) < price:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, r, mid, call) > price:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2 * 100, 2)


def bs_delta(S, K, T, r, iv_pct, call):
    if not iv_pct or T <= 0:
        return None
    s = iv_pct / 100
    d1 = (math.log(S / K) + (r + 0.5 * s * s) * T) / (s * math.sqrt(T))
    return round(_ncdf(d1) if call else _ncdf(d1) - 1, 3)


def parse_expiry(s):
    return dt.datetime.strptime(s.title(), "%d%b%Y")


# ------------------------------------------------------------ master + quotes
def pick_contracts(master, name, exch, spot, today, n_exp=N_EXP, window=WINDOW):
    """master rows -> {expiry: {strike: {'CE': row, 'PE': row}}} (nazdik na expiry, spot na window ma)"""
    opts = {}
    for r in master:
        if r.get("exch_seg") != exch or r.get("name") != name or r.get("instrumenttype") != "OPTIDX":
            continue
        sym = str(r.get("symbol", ""))
        typ = "CE" if sym.endswith("CE") else ("PE" if sym.endswith("PE") else None)
        try:
            exp = parse_expiry(r["expiry"])
            k = float(r["strike"]) / 100.0
        except Exception:
            continue
        if not typ or exp.date() < today:
            continue
        if abs(k / spot - 1) > window:
            continue
        opts.setdefault(r["expiry"].title(), {}).setdefault(k, {})[typ] = r
    exps = sorted(opts, key=parse_expiry)[:n_exp]
    return {e: opts[e] for e in exps}


def quote(api, exch, tokens):
    """FULL quote -> {token: row}. getMarketData na hoy to sidhu REST."""
    out = {}
    tokens = [str(t) for t in tokens]
    for i in range(0, len(tokens), BATCH):
        chunk = tokens[i:i + BATCH]
        res = None
        for attempt in range(3):
            try:
                if hasattr(api, "getMarketData"):
                    res = api.getMarketData("FULL", {exch: chunk})
                else:
                    import requests
                    res = requests.post("https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/",
                                        json={"mode": "FULL", "exchangeTokens": {exch: chunk}}, timeout=30,
                                        headers={"Authorization": "Bearer " + api.access_token,
                                                 "X-PrivateKey": api.api_key, "Content-Type": "application/json",
                                                 "Accept": "application/json", "X-UserType": "USER",
                                                 "X-SourceID": "WEB", "X-ClientLocalIP": "127.0.0.1",
                                                 "X-ClientPublicIP": "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00"}).json()
                if isinstance(res, dict) and res.get("status") and res.get("data"):
                    break
                print("quote retry", exch, (res or {}).get("message") if isinstance(res, dict) else res)
            except Exception as e:
                print("quote error", e)
            time.sleep(2 + attempt * 2)
        time.sleep(SLEEP)
        for q in ((res or {}).get("data") or {}).get("fetched", []) if isinstance(res, dict) else []:
            out[str(q.get("symbolToken"))] = q
    return out


def num(x, d=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def build_index(api, master, label, name, exch, sx, st, now, state_base):
    sq = quote(api, sx, [st]).get(st)
    spot = num((sq or {}).get("ltp"))
    if not spot:
        print("spot nathi:", label); return None, {}
    today = now.date()
    chains = pick_contracts(master, name, exch, spot, today)
    if not chains:
        print("contracts nathi:", label); return None, {}
    toks = [r["token"] for e in chains.values() for k in e.values() for r in k.values()]
    qs = quote(api, exch, toks)
    print(label, "spot", spot, "expiries", list(chains), "tokens", len(toks), "quotes", len(qs))
    res = {"name": label, "spot": round(spot, 2), "prev_close": num((sq or {}).get("close")),
           "expiries": [], "chains": {}}
    oi_now = {}
    for e, strikes in chains.items():
        exp_dt = parse_expiry(e).replace(hour=15, minute=30)
        T = max((exp_dt - now).total_seconds(), 0) / (365 * 86400)
        rows = []
        for k in sorted(strikes):
            row = {"strike": k}
            for typ in ("CE", "PE"):
                c = strikes[k].get(typ)
                q = qs.get(str(c["token"])) if c else None
                if not q:
                    row[typ.lower()] = None
                    continue
                ltp, oi = num(q.get("ltp"), 0), num(q.get("opnInterest"), 0)
                iv = implied_vol(ltp, spot, k, T, RATE, typ == "CE")
                oi_now[str(c["token"])] = oi
                base = state_base.get(str(c["token"]))
                row[typ.lower()] = {"ltp": round(ltp, 2), "pc": num(q.get("close")), "oi": int(oi),
                                    "doi": None if base is None else int(oi - base),
                                    "vol": int(num(q.get("tradeVolume"), 0)), "iv": iv,
                                    "delta": bs_delta(spot, k, T, RATE, iv, typ == "CE")}
            if row["ce"] or row["pe"]:
                rows.append(row)
        lot = next((int(r["lotsize"]) for k in strikes.values() for r in k.values() if r.get("lotsize")), None)
        res["expiries"].append(e)
        res["chains"][e] = {"expiry": e, "days": round(T * 365, 2), "lot": lot, "rows": rows}
    return res, oi_now


def update_state(state, oi_now, today_s):
    """options_state.json: last_* = aaj no chhelo snapshot; base = kal na divas no chhelo snapshot."""
    if state.get("last_date") and state["last_date"] != today_s:
        state["base"] = state.get("last_oi", {})
        state["base_date"] = state["last_date"]
    last = state.get("last_oi", {}) if state.get("last_date") == today_s else dict(state.get("last_oi", {}))
    last.update(oi_now)
    if len(last) > 30000:
        last = dict(list(last.items())[-30000:])
    state["last_oi"] = last
    state["last_date"] = today_s
    return state


def read_json(p, d):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return d


def main():
    import requests
    sys.path.insert(0, HERE)
    from scope_daily import login, SCRIP_URL
    now = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    master = requests.get(SCRIP_URL, timeout=180).json()
    api = login()
    state = read_json(STATE, {})
    today_s = now.date().isoformat()
    # base = kal nu chhelu OI (aaj no pehlo run aave tyare state ma kal no snapshot hoy che)
    probe = dict(state)
    probe = update_state(probe, {}, today_s)
    base = probe.get("base", {})
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "base_date": probe.get("base_date"), "indices": {}}
    all_oi = {}
    for label, name, exch, sx, st in INDICES:
        try:
            r, oi = build_index(api, master, label, name, exch, sx, st, now, base)
        except Exception as e:
            print("fail", label, e); r, oi = None, {}
        if r:
            out["indices"][label] = r
            all_oi.update(oi)
    if not out["indices"]:
        print("koi data nathi - options.json badalyu nathi"); sys.exit(0)
    state = update_state(state, all_oi, today_s)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    with open(STATE, "w") as f:
        json.dump(state, f, separators=(",", ":"))
    print("saved options.json:", {k: len(v["expiries"]) for k, v in out["indices"].items()})


if __name__ == "__main__":
    main()
