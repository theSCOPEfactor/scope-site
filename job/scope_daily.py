"""
Scope daily job  (GitHub Actions ma chale che)

Badha NSE EQ stocks mate Angel One thi 1-hour candles (1 request / stock) lai ne:
  - 52-week volume profile levels: Point (POC), VHIGH, VLOW
  - Fib levels (VLOW=0, VHIGH=1 par based)
  - 50/150/200 DMA, Stage (1/2/3/4), Days in Stage 2
  - RS rating (1-99) = 3/6/9/12 mahina na return nu weighted percentile rank
Output: data.json  (repo ma commit thay che, website e vanche che)

Secrets (GitHub repo -> Settings -> Secrets -> Actions):
  ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
Optional env: MAX_STOCKS (test mate, e.g. 20)
"""
import os, sys, json, time, datetime as dt
import numpy as np
import pandas as pd
import requests
import pyotp
from SmartApi import SmartConnect

SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
BINS = 100            # volume profile rows
VALUE_AREA = 0.70     # 70% value area
LOOKBACK_DAYS = 360   # ONE_HOUR candle ni limit 400 divas che
SLEEP = 0.45          # Angel rate limit (~3/sec) thi nichu rahevu


def load_universe():
    rows = requests.get(SCRIP_URL, timeout=120).json()
    out = []
    for r in rows:
        if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).endswith("-EQ"):
            out.append({"symbol": r["name"], "token": r["token"], "tsym": r["symbol"],
                        "company": r.get("name")})
    # duplicate hatavo
    seen, uniq = set(), []
    for o in out:
        if o["symbol"] not in seen:
            seen.add(o["symbol"]); uniq.append(o)
    # NSE indices (Nifty 50, Nifty Bank, ...)
    idx, seen_i = [], set()
    for r in rows:
        if r.get("exch_seg") != "NSE":
            continue
        nm = str(r.get("symbol", ""))
        if (r.get("instrumenttype") == "AMXIDX" or str(r.get("token", "")).startswith("99926")) \
                and "VIX" not in nm.upper() and nm not in seen_i:
            seen_i.add(nm); idx.append({"symbol": nm, "token": r["token"]})
    return uniq, idx


def login():
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    res = api.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PASSWORD"], totp)
    if not res.get("status"):
        print("Login fail:", res); sys.exit(1)
    return api


def fetch_hourly(api, token, retries=3, interval="ONE_HOUR", days=LOOKBACK_DAYS):
    now = dt.datetime.now()
    frm = now - dt.timedelta(days=days)
    params = {"exchange": "NSE", "symboltoken": token, "interval": interval,
              "fromdate": frm.strftime("%Y-%m-%d 09:15"), "todate": now.strftime("%Y-%m-%d %H:%M")}
    for i in range(retries):
        try:
            res = api.getCandleData(params)
            data = res.get("data") if isinstance(res, dict) else None
            if data:
                df = pd.DataFrame(data, columns=["ts", "o", "h", "l", "c", "v"])
                df["ts"] = pd.to_datetime(df["ts"], utc=False).dt.tz_localize(None)
                return df
            return None
        except Exception as e:
            time.sleep(2 * (i + 1))
    return None


def volume_profile(h1, bins=BINS):
    """candles thi profile. Dar candle nu volume tena high-low range ma vehchay che.
    Index ma volume hotu nathi -> dar candle ne barabar vajan (time based)."""
    BINS = bins
    lo, hi = h1["l"].min(), h1["h"].max()
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, BINS + 1)
    vol = np.zeros(BINS)
    L = h1["l"].values; H = h1["h"].values; V = h1["v"].values.astype(float)
    if V.sum() <= 0:
        V = np.ones(len(V))
    for l, h, v in zip(L, H, V):
        if v <= 0:
            continue
        i0 = min(int((l - lo) / (hi - lo) * BINS), BINS - 1)
        i1 = min(int((h - lo) / (hi - lo) * BINS), BINS - 1)
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    poc = int(vol.argmax())
    total = vol.sum()
    lo_i = hi_i = poc
    acc = vol[poc]
    while acc < VALUE_AREA * total and (lo_i > 0 or hi_i < BINS - 1):
        up = vol[hi_i + 1] if hi_i < BINS - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    mid = lambda i: float((edges[i] + edges[i + 1]) / 2)
    return {"point": mid(poc), "vhigh": float(edges[hi_i + 1]), "vlow": float(edges[lo_i])}


def fib_levels(vlow, vhigh):
    r = vhigh - vlow
    f = lambda x: round(vlow + r * x, 2)
    return {"breakdown": f(0), "buy_reversal": f(0.25), "sell_reversal": f(0.75),
            "breakout": f(1), "target1": f(1.272)}


def to_daily(h1):
    d = h1.copy()
    d["date"] = d["ts"].dt.date
    g = d.groupby("date").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"),
                              c=("c", "last"), v=("v", "sum"))
    return g


def stage_series(daily):
    c = daily["c"]
    s50, s150, s200 = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    hi52 = daily["h"].rolling(250, min_periods=1).max()
    lo52 = daily["l"].rolling(250, min_periods=1).min()
    rising200 = s200 > s200.shift(20)
    s2 = ((c > s150) & (c > s200) & (s150 > s200) & rising200 &
          (s50 > s150) & (c > s50) & (c >= 0.75 * hi52) & (c >= 1.30 * lo52))
    s4 = (c < s200) & (~rising200) & (s50 < s200)
    s3 = (~s2) & (~s4) & (c < s50) & (c > s200 * 0.95) & (s200.notna())
    stage = pd.Series(1, index=daily.index)
    stage[s3] = 3
    stage[s4] = 4
    stage[s2] = 2
    stage[s200.isna()] = 0   # data pura nathi
    return stage, s50, s150, s200, hi52, lo52


def days_in_stage2(stage):
    n = 0
    for v in stage.values[::-1]:
        if v == 2:
            n += 1
        else:
            break
    return n


def ret(c, days):
    if len(c) <= days:
        return np.nan
    return c.iloc[-1] / c.iloc[-1 - days] - 1


def index_row(api, u):
    """Ek index: 52W levels (1h) + aaj na divas na levels (5 min)."""
    h1 = fetch_hourly(api, u["token"]); time.sleep(SLEEP)
    if h1 is None or len(h1) < 5:
        return None
    daily = to_daily(h1)
    c = daily["c"]
    price = float(c.iloc[-1]); prev = float(c.iloc[-2]) if len(c) > 1 else price
    row = {"symbol": u["symbol"], "price": round(price, 2), "chg": round((price / prev - 1) * 100, 2)}
    prof = volume_profile(h1)
    if prof:
        row["w52"] = {k: round(v, 2) for k, v in prof.items()}
        row["w52"]["hi"] = round(float(h1["h"].max()), 2); row["w52"]["lo"] = round(float(h1["l"].min()), 2)
    m5 = fetch_hourly(api, u["token"], interval="FIVE_MINUTE", days=4); time.sleep(SLEEP)
    if m5 is not None and len(m5) >= 5:
        last = m5["ts"].dt.date.max()
        d = m5[m5["ts"].dt.date == last]
        dp = volume_profile(d, bins=40) if len(d) >= 5 else None
        if dp:
            row["day"] = {k: round(v, 2) for k, v in dp.items()}
            row["day"]["hi"] = round(float(d["h"].max()), 2); row["day"]["lo"] = round(float(d["l"].min()), 2)
            row["day"]["date"] = str(last)
    return row


def main():
    uni, idx = load_universe()
    mx = int(os.environ.get("MAX_STOCKS", "0"))
    if mx:
        uni = uni[:mx]
    print("Universe:", len(uni))
    api = login()
    indices = []
    for u in idx[:60]:
        try:
            r = index_row(api, u)
            if r:
                indices.append(r)
        except Exception as e:
            print("index fail", u["symbol"], e)
    print("Indices:", len(indices))
    rows, fails = [], 0
    for i, u in enumerate(uni, 1):
        h1 = fetch_hourly(api, u["token"])
        time.sleep(SLEEP)
        if h1 is None or len(h1) < 5:      # navi IPO pan chalse (ochha candles)
            fails += 1
            continue
        daily = to_daily(h1)
        if len(daily) < 1:
            continue
        prof = volume_profile(h1)
        stage, s50, s150, s200, hi52, lo52 = stage_series(daily)
        c = daily["c"]
        price = float(c.iloc[-1]); prev = float(c.iloc[-2]) if len(c) > 1 else price
        r3, r6, r9, r12 = ret(c, 63), ret(c, 126), ret(c, 189), ret(c, 250)
        rs_raw = np.nansum([0.4 * r3, 0.2 * r6, 0.2 * r9, 0.2 * r12]) if not np.isnan(r3) else np.nan
        row = {
            "symbol": u["symbol"], "price": round(price, 2),
            "chg": round((price / prev - 1) * 100, 2),
            "sma50": None if np.isnan(s50.iloc[-1]) else round(float(s50.iloc[-1]), 2),
            "sma150": None if np.isnan(s150.iloc[-1]) else round(float(s150.iloc[-1]), 2),
            "sma200": None if np.isnan(s200.iloc[-1]) else round(float(s200.iloc[-1]), 2),
            "hi52": round(float(hi52.iloc[-1]), 2), "lo52": round(float(lo52.iloc[-1]), 2),
            "from_hi": round((price / float(hi52.iloc[-1]) - 1) * 100, 1),
            "listed_days": int(len(daily)),
            "stage": int(stage.iloc[-1]), "days_s2": days_in_stage2(stage),
            "rs_raw": None if np.isnan(rs_raw) else float(rs_raw),
            "avg_vol": int(daily["v"].tail(20).mean()),
            "new": bool(len(daily) < 200),      # navi listing: Stage/RS mate pura divas nathi
            "bars": int(len(daily)),
        }
        if prof:
            row.update({k: round(v, 2) for k, v in prof.items()})
            row["fib"] = fib_levels(prof["vlow"], prof["vhigh"])
            if price > row["fib"]["breakout"]:
                row["zone"] = "Above VHIGH"
            elif price < row["fib"]["breakdown"]:
                row["zone"] = "Below VLOW"
            elif price >= row["fib"]["sell_reversal"]:
                row["zone"] = "Near VHIGH (sell zone)"
            elif price <= row["fib"]["buy_reversal"]:
                row["zone"] = "Near VLOW (buy zone)"
            else:
                row["zone"] = "Inside range"
        rows.append(row)
        if i % 100 == 0:
            print(f"{i}/{len(uni)} done, ok={len(rows)} fail={fails}", flush=True)

    # RS percentile (1-99)
    s = pd.Series({r["symbol"]: r["rs_raw"] for r in rows if r["rs_raw"] is not None})
    pct = (s.rank(pct=True) * 98 + 1).round(0)
    for r in rows:
        r["rs"] = int(pct.get(r["symbol"], 0)) if r["symbol"] in pct else None
        r.pop("rs_raw", None)

    def clean(o):                      # NaN is not valid JSON (browsers reject it) -> null
        if isinstance(o, float):
            return o if np.isfinite(o) else None
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o

    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)      # show India time
    out = {"updated": ist.strftime("%Y-%m-%d %H:%M"), "count": len(rows), "indices": indices, "stocks": rows}
    with open("data.json", "w") as f:
        json.dump(clean(out), f, separators=(",", ":"), allow_nan=False)
    print("Saved data.json:", len(rows), "stocks, failed:", fails)


if __name__ == "__main__":
    main()
