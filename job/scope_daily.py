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
import threading
from concurrent.futures import ThreadPoolExecutor
from SmartApi import SmartConnect

SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
BINS = 100            # volume profile rows
VALUE_AREA = 0.70     # 70% value area
LOOKBACK_DAYS = 360   # ONE_HOUR candle ni limit 400 divas che
SLEEP = 0.45          # Angel rate limit (~3/sec) thi nichu rahevu
WORKERS = int(os.environ.get("WORKERS", "3"))          # ek sathe ketla requests
MIN_GAP = float(os.environ.get("MIN_GAP", "0.40"))     # be request vache ochhu ketlo samay (badha workers mali ne)
_gate = threading.Lock()
_next_ok = [0.0]


def _throttle():
    """badha threads mali ne ~2.5 request/sec thi vadhu nahi (Angel limit ~3/sec)"""
    with _gate:
        now = time.monotonic()
        wait = _next_ok[0] - now
        _next_ok[0] = max(now, _next_ok[0]) + MIN_GAP
    if wait > 0:
        time.sleep(wait)


_MASTER = {}


def get_master():
    if "rows" not in _MASTER:
        _MASTER["rows"] = requests.get(SCRIP_URL, timeout=180).json()
    return _MASTER["rows"]


IND_URLS = ["https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
            "https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv",
            "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
            "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"]


def load_industry():
    """NSE industry (Nifty Total Market / Nifty 500 list). Na malе to khali dict, baki badhu chale."""
    import io
    hdr = {"User-Agent": "Mozilla/5.0"}
    for url in IND_URLS:
        try:
            r = requests.get(url, headers=hdr, timeout=40)
            if not r.ok:
                continue
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            m = {str(a).strip(): str(b).strip() for a, b in zip(df["Symbol"], df["Industry"])}
            if len(m) > 100:
                print("Industry list:", url, len(m)); return m
        except Exception as e:
            print("industry fail", url, e)
    print("Industry list not available")
    return load_manual_industry()


def load_manual_industry():
    """Fallback: industry_map.csv (symbol,industry) in repo root, if the NSE lists cannot be downloaded."""
    import csv
    m = {}
    if os.path.exists("industry_map.csv"):
        with open("industry_map.csv", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sym = (row.get("symbol") or row.get("Symbol") or "").strip()
                ind = (row.get("industry") or row.get("Industry") or "").strip()
                if sym and ind:
                    m[sym] = ind
        print("industry_map.csv:", len(m))
    return m


VIX = {}


def load_universe():
    rows = get_master()
    for r in rows:                                   # India VIX (index list ma nathi levayo, alag rakhyo)
        if r.get("exch_seg") == "NSE" and "INDIA VIX" in str(r.get("symbol", "")).upper().replace("  ", " "):
            VIX["token"] = r["token"]
            break
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
    # timeout=30 -> Angel server jawab na ape to kayam latki na rahe
    # (jo "unexpected keyword argument timeout" error aave to ", timeout=30" kadhi nakho)
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"], timeout=30)
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    res = api.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PASSWORD"], totp)
    if not res.get("status"):
        print("Login fail:", res); sys.exit(1)
    return api


def fetch_hourly(api, token, retries=4, interval="ONE_HOUR", days=LOOKBACK_DAYS, exch="NSE"):
    now = dt.datetime.now()
    frm = now - dt.timedelta(days=days)
    params = {"exchange": exch, "symboltoken": token, "interval": interval,
              "fromdate": frm.strftime("%Y-%m-%d 09:15"), "todate": now.strftime("%Y-%m-%d %H:%M")}
    for i in range(retries):
        try:
            _throttle()
            res = api.getCandleData(params)
            if isinstance(res, dict):
                data = res.get("data")
                if data:
                    df = pd.DataFrame(data, columns=["ts", "o", "h", "l", "c", "v"])
                    df["ts"] = pd.to_datetime(df["ts"], utc=False).dt.tz_localize(None)
                    return df
                msg = str(res.get("message", "")).lower()
                if res.get("errorcode") == "AB1010" or "rate" in msg or "access denied" in msg:
                    time.sleep(3 * (i + 1))      # rate limit -> thodi vaar ruki ne fari try
                    continue
                return None                      # kharekhar data nathi
            time.sleep(2 * (i + 1))
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


TV_ROWS = 24        # tamara TradingView profile (Rows 24): edges 24934.8 / 25047.0 / POC 25031.7 barabar 10.2-pt rows
TV_TICK = 0.05      # index future no mintick


def frvp_tv(d, rows=TV_ROWS, va=VALUE_AREA, tick=TV_TICK, min_n=30):
    """SCOPE PRO (Pine) jevo j profile: rows tick-aligned (f_vaCalc/main profile ni jem), volume dar candle na
    high-low ma row sathe overlap mujab, POC = row nu mid, Value Area 1-1 row sarkave (tie -> POC ni nazdik wali),
    ane jo next row umerta target (70%) vatay to tya j aatki jay. d = 1-min candles (ts,o,h,l,c,v)."""
    if d is None or len(d) < min_n:
        return None
    r_hi, r_lo = float(d["h"].max()), float(d["l"].min())
    if r_hi <= r_lo:
        return None
    tot = max(1, int(round((r_hi - r_lo) / tick)))
    raw = tot / rows
    t_dn, t_up = max(1, int(np.floor(raw))), max(1, int(np.ceil(raw)))
    rw_dn, rw_up = int(np.ceil(tot / t_dn)), int(np.ceil(tot / t_up))
    tpr = t_dn if abs(rw_dn - rows) <= abs(rw_up - rows) else t_up
    lows, highs, px, left = [], [], r_lo, tot
    while left > 0:
        tt = min(tpr, left)
        lows.append(px); px += tt * tick; highs.append(px); left -= tt
    lows, highs = np.array(lows), np.array(highs)
    n = len(lows)
    vol = np.zeros(n)
    for l, h, v in zip(d["l"].values, d["h"].values, d["v"].values.astype(float)):
        br = h - l
        ov = np.minimum(h, highs) - np.maximum(l, lows)
        m = ov > 0
        if m.any():
            vol[m] += v * (ov[m] / br if br > 0 else 1.0)
    mx = vol.max()
    if mx <= 0:
        return None
    poc = int(np.argmax(vol))
    tgt = vol.sum() * va
    acc, lo_i, hi_i = mx, poc, poc
    for _ in range(n):
        if acc >= tgt:
            break
        up_v = vol[hi_i + 1] if hi_i < n - 1 else -1.0
        dn_v = vol[lo_i - 1] if lo_i > 0 else -1.0
        if up_v < 0 and dn_v < 0:
            break
        if up_v < 0:
            go_up = False
        elif dn_v < 0:
            go_up = True
        elif up_v != dn_v:
            go_up = up_v > dn_v
        else:
            go_up = (hi_i + 1 - poc) <= (poc - (lo_i - 1))
        nx = up_v if go_up else dn_v
        if acc + nx > tgt:
            break
        if go_up:
            hi_i += 1; acc += up_v
        else:
            lo_i -= 1; acc += dn_v
    return {"hi": round(r_hi, 2), "lo": round(r_lo, 2), "point": round(float((lows[poc] + highs[poc]) / 2), 2),
            "vhigh": round(float(highs[hi_i]), 2), "vlow": round(float(lows[lo_i]), 2), "rows": n}


def session_frvps(m1, n=3, min_bars=330):
    """1-min candles -> {date: frvp_tv} chhella n PURA sessions (9:15-15:30; adhura/Muhurat skip)."""
    if m1 is None or len(m1) == 0:
        return {}
    m1 = m1.sort_values("ts").drop_duplicates("ts")
    t = m1["ts"].dt.time
    m1 = m1[(t >= dt.time(9, 15)) & (t <= dt.time(15, 30))].copy()
    m1["date"] = m1["ts"].dt.date
    out = {}
    for day, g in m1.groupby("date"):
        if len(g) < min_bars or g["ts"].dt.time.max() < dt.time(15, 25) or g["ts"].dt.time.min() > dt.time(9, 20):
            continue
        r = frvp_tv(g)
        if r:
            tv_ = float(g["v"].sum())                      # diagnostic: TradingView sathe volume sarkhavva mate
            hv = g.groupby(g["ts"].dt.hour)["v"].sum()
            r["diag"] = {"bars": int(len(g)), "zero": int((g["v"] <= 0).sum()), "tot": int(tv_),
                         "vh": {str(int(h)): round(float(v) / tv_ * 100, 1) for h, v in hv.items()} if tv_ > 0 else {},
                         "last": round(float(g["v"].iloc[-1]), 0), "first": round(float(g["v"].iloc[0]), 0)}
            out[str(day)] = r
    return {k: out[k] for k in sorted(out)[-n:]}


def fib_levels(vlow, vhigh):
    r = vhigh - vlow
    f = lambda x: round(vlow + r * x, 2)
    return {"breakdown": f(0), "buy_reversal": f(0.25), "sell_reversal": f(0.75),
            "breakout": f(1), "target1": f(1.272)}


def _norm(x):
    return "".join(ch for ch in str(x).lower() if ch.isalnum())


# index (site naam, normalised) -> (futures "name" in master, futures exchange)
FUT_INDICES = {"nifty50": ("NIFTY", "NFO"), "niftybank": ("BANKNIFTY", "NFO"),
               "niftyfinservice": ("FINNIFTY", "NFO"), "niftymidselect": ("MIDCPNIFTY", "NFO"),
               "niftynext50": ("NIFTYNXT50", "NFO"), "sensex": ("SENSEX", "BFO"), "bankex": ("BANKEX", "BFO")}


def pick_future(master, label, today=None):
    """Index (site naam) -> nazdik no FUTIDX contract {symbol, token, expiry, exch, name} athva None."""
    m = FUT_INDICES.get(_norm(label))
    if not m:
        return None
    name, exch = m
    today = today or dt.date.today()
    best = None
    for r in master:
        if r.get("exch_seg") != exch or r.get("name") != name or r.get("instrumenttype") != "FUTIDX":
            continue
        try:
            e = dt.datetime.strptime(str(r["expiry"]).title(), "%d%b%Y").date()
        except Exception:
            continue
        if e < today:
            continue
        if best is None or e < best[0]:
            best = (e, r)
    if not best:
        return None
    r = best[1]
    return {"symbol": r["symbol"], "token": r["token"], "expiry": str(r["expiry"]).title(), "exch": exch, "name": name}


def day_levels(m5, bins=40, min_bars=6):
    """5-min candles -> chhela session na levels {date, open, hi, lo, point, vhigh, vlow} (athva None)."""
    if m5 is None or len(m5) == 0:
        return None
    m5 = m5.sort_values("ts").drop_duplicates("ts")
    t = m5["ts"].dt.time
    m5 = m5[(t >= dt.time(9, 15)) & (t <= dt.time(15, 30))]
    if len(m5) == 0:
        return None
    last = m5["ts"].dt.date.max()
    d = m5[m5["ts"].dt.date == last]
    if len(d) < min_bars:
        return None
    p = None
    if float(d["v"].sum()) > 0:                              # asli volume hoy (future) -> SCOPE PRO jevo 24-row profile
        p = frvp_tv(d, min_n=min_bars)
    if not p:
        p = volume_profile(d, bins=bins)                     # index (volume nathi) -> time based
    if not p:
        return None
    out = {"date": str(last), "open": round(float(d["o"].iloc[0]), 2),
           "hi": round(float(d["h"].max()), 2), "lo": round(float(d["l"].min()), 2)}
    out.update({k: round(v, 2) for k, v in p.items() if k in ("point", "vhigh", "vlow")})
    return out


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


def pct1(x):
    """0.1234 -> 12.3 (percent), nan -> None"""
    return None if x is None or np.isnan(x) else round(float(x) * 100, 1)


def ret(c, days):
    if len(c) <= days:
        return np.nan
    return c.iloc[-1] / c.iloc[-1 - days] - 1


def ohlc_blocks(daily):
    """to_daily() ma thi: chhelli session nu O/H/L/C (+prev close, volume) ane 52 week nu O/H/L/C (tarikh sathe). Extra API call nathi."""
    try:
        n = len(daily)
        if n < 1:
            return None
        r = lambda v: round(float(v), 2)
        last = daily.iloc[-1]
        d = {"date": str(daily.index[-1]), "o": r(last["o"]), "h": r(last["h"]), "l": r(last["l"]), "c": r(last["c"]),
             "v": int(last["v"]) if last["v"] == last["v"] else 0}
        if n > 1:
            d["pc"] = r(daily["c"].iloc[-2])
        w = daily.tail(252)
        wk = {"from": str(w.index[0]), "o": r(w["o"].iloc[0]), "h": r(w["h"].max()), "hd": str(w["h"].idxmax()),
              "l": r(w["l"].min()), "ld": str(w["l"].idxmin()), "c": r(w["c"].iloc[-1]), "days": int(len(w))}
        return {"d": d, "w": wk}
    except Exception as e:
        print("ohlc fail", e)
        return None


def index_row(api, u):
    """Ek index: 52W levels (1h) + chhela session na levels (5 min). u["exch"] default NSE."""
    ex = u.get("exch", "NSE")
    h1 = fetch_hourly(api, u["token"], exch=ex); time.sleep(SLEEP)
    if h1 is None or len(h1) < 5:
        return None
    daily = to_daily(h1)
    c = daily["c"]
    price = float(c.iloc[-1]); prev = float(c.iloc[-2]) if len(c) > 1 else price
    row = {"symbol": u["symbol"], "price": round(price, 2), "chg": round((price / prev - 1) * 100, 2), "ohlc": ohlc_blocks(daily)}
    prof = volume_profile(h1)
    if prof:
        row["w52"] = {k: round(v, 2) for k, v in prof.items()}
        row["w52"]["hi"] = round(float(h1["h"].max()), 2); row["w52"]["lo"] = round(float(h1["l"].min()), 2)
    m5 = fetch_hourly(api, u["token"], interval="FIVE_MINUTE", days=5, exch=ex); time.sleep(SLEEP)
    dl = day_levels(m5, min_bars=5)
    if dl:
        row["day"] = dl
    return row


def vix_row(api):
    """India VIX: aajno bhav, badlav, 1 varsh ma percentile ane chhela 30 divas (nano chart mate)."""
    if not VIX.get("token"):
        return None
    h1 = fetch_hourly(api, VIX["token"]); time.sleep(SLEEP)
    if h1 is None or len(h1) < 20:
        return None
    c = to_daily(h1)["c"].dropna()
    if len(c) < 20:
        return None
    price = float(c.iloc[-1]); prev = float(c.iloc[-2])
    return {"price": round(price, 2), "chg": round(price - prev, 2),
            "pct": round(float((c <= price).mean()) * 100), "lo": round(float(c.min()), 2), "hi": round(float(c.max()), 2),
            "n": int(len(c)), "series": [round(float(x), 2) for x in c.iloc[-30:]]}


def future_row(api, label, fut):
    """Index na futures contract: price + 52W (contract ni available history) + chhela session na levels."""
    h1 = fetch_hourly(api, fut["token"], exch=fut["exch"]); time.sleep(SLEEP)
    if h1 is None or len(h1) < 5:
        return None
    daily = to_daily(h1)
    c = daily["c"]
    price = float(c.iloc[-1]); prev = float(c.iloc[-2]) if len(c) > 1 else price
    row = {"index": label, "contract": fut["symbol"], "expiry": fut["expiry"], "token": fut["token"], "exch": fut["exch"],
           "price": round(price, 2), "chg": round((price / prev - 1) * 100, 2), "since": str(daily.index[0]), "days": int(len(daily)),
           "ohlc": ohlc_blocks(daily)}
    prof = volume_profile(h1)
    if prof:
        row["w52"] = {k: round(v, 2) for k, v in prof.items()}
        row["w52"]["hi"] = round(float(h1["h"].max()), 2); row["w52"]["lo"] = round(float(h1["l"].min()), 2)
    m5 = fetch_hourly(api, fut["token"], interval="FIVE_MINUTE", days=5, exch=fut["exch"]); time.sleep(SLEEP)
    dl = day_levels(m5, min_bars=5)
    if dl:
        row["day"] = dl
    return row


def main():
    uni, idx = load_universe()
    mx = int(os.environ.get("MAX_STOCKS", "0"))
    if mx:
        uni = uni[:mx]
    print("Universe:", len(uni))
    api = login()
    industry = load_industry()
    bn, b2, b50, b200 = None, None, None, None      # market breadth (date -> counts)
    indices = []
    if not any(_norm(u["symbol"]) == "sensex" for u in idx):
        idx = idx + [{"symbol": "SENSEX", "token": "99919000", "exch": "BSE"}]
    for u in idx[:61]:
        try:
            r = index_row(api, u)
            if r:
                indices.append(r)
        except Exception as e:
            print("index fail", u["symbol"], e)
    print("Indices:", len(indices))
    vix = None
    try:
        vix = vix_row(api)
        print("VIX:", vix and vix["price"])
    except Exception as e:
        print("vix fail", e)
    futures = []
    try:
        master = get_master()
        for ix in indices:
            fut = pick_future(master, ix["symbol"])
            if not fut:
                continue
            try:
                fr = future_row(api, ix["symbol"], fut)
                if fr:
                    futures.append(fr)
            except Exception as e:
                print("future fail", ix["symbol"], e)
    except Exception as e:
        print("futures skipped", e)
    print("Futures:", [f["contract"] for f in futures])
    t0 = time.time()
    rows, fails = [], 0
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    for i, (u, h1) in enumerate(zip(uni, pool.map(lambda x: fetch_hourly(api, x["token"]), uni)), 1):
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
        valid = s200.notna()                                 # breadth counters (Market health)
        parts = [valid.astype(int), (stage == 2).astype(int), ((c > s50) & s50.notna()).astype(int), ((c > s200) & valid).astype(int)]
        if bn is None:
            bn, b2, b50, b200 = parts
        else:
            bn, b2, b50, b200 = [a.add(b, fill_value=0) for a, b in zip((bn, b2, b50, b200), parts)]
        nS2 = days_in_stage2(stage)
        prior = int(stage.values[-nS2 - 1]) if 0 < nS2 < len(stage) else None
        vx = float(daily["v"].tail(5).max() / daily["v"].tail(50).mean()) if daily["v"].tail(50).mean() > 0 else None
        base_bo = bool(0 < nS2 <= 10 and prior == 1 and vx is not None and vx >= 1.5)
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
            "stage": int(stage.iloc[-1]), "days_s2": nS2,
            "industry": industry.get(u["symbol"]), "prior_stage": prior,
            "vol_x": None if vx is None else round(vx, 2), "base_bo": base_bo,
            "rs_raw": None if np.isnan(rs_raw) else float(rs_raw),
            "avg_vol": int(daily["v"].tail(20).mean()),
            "new": bool(len(daily) < 200),      # navi listing: Stage/RS mate pura divas nathi
            "bars": int(len(daily)),
            "ohlc": ohlc_blocks(daily),
            "r1w": pct1(ret(c, 5)), "r1m": pct1(ret(c, 21)), "r3m": pct1(r3), "r6m": pct1(r6), "r1y": pct1(r12),
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
            print(f"{i}/{len(uni)} done, ok={len(rows)} fail={fails}, {int((time.time() - t0) / 60)} min", flush=True)

    # Stocks na "Today" (chhela session) levels: top-N liquid stocks (5-min candle, 1 request / stock)
    topn = int(os.environ.get("DAY_TOP", "400"))
    tokmap = {u["symbol"]: u["token"] for u in uni}
    cand = sorted([r for r in rows if r.get("point") is not None],
                  key=lambda r: -(r["price"] * (r.get("avg_vol") or 0)))[:topn]
    nd = 0
    m5s = pool.map(lambda r: fetch_hourly(api, tokmap[r["symbol"]], interval="FIVE_MINUTE", days=5), cand)
    for r, m5 in zip(cand, m5s):
        try:
            dl = day_levels(m5)
            if dl:
                r["day"] = dl; nd += 1
        except Exception as e:
            print("day fail", r["symbol"], e)
    print("Stock day levels:", nd, "of", len(cand))

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

    breadth = []
    if bn is not None:
        for d in list(bn.index)[-150:]:
            n = float(bn.get(d, 0))
            if n > 0 and n >= 0.5 * float(bn.max()):
                breadth.append({"d": str(d), "n": int(n), "s2": round(float(b2.get(d, 0)) / n * 100, 1),
                                "a50": round(float(b50.get(d, 0)) / n * 100, 1),
                                "a200": round(float(b200.get(d, 0)) / n * 100, 1)})
    print("Industry matched:", sum(1 for r in rows if r.get("industry")), "of", len(rows))

    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)      # show India time
    out = {"updated": ist.strftime("%Y-%m-%d %H:%M"), "count": len(rows), "indices": indices, "futures": futures, "breadth": breadth, "vix": vix, "stocks": rows}
    with open("data.json", "w") as f:
        json.dump(clean(out), f, separators=(",", ":"), allow_nan=False)
    print("Saved data.json:", len(rows), "stocks, failed:", fails)


if __name__ == "__main__":
    main()