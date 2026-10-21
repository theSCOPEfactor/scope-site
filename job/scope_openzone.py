"""
Scope Open-zone job  (GitHub Actions ma chale che)

Index (Nifty 50, Nifty Bank, Sensex) mate:
  - Dar divas na candles (15 min, ~3 varsh) thi te divas no volume-profile: VLOW(0), VHIGH(1), Point
  - Bija divas no OPEN te levels ma kya padyo (fib ratio) ane open pachhi su thayu
  - Zone-wise stats -> openzone.json   (website /openzone page vanche che)

Fakat OPEN ganay che, candle kya close thai e nahi (close fakat "divas nu result" tarike).

Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
Optional env: YEARS (default 3)
"""
import os, sys, json, time, datetime as dt
import numpy as np
import pandas as pd

BINS = 40
VALUE_AREA = 0.70
CHUNK_DAYS = 190          # FIFTEEN_MINUTE ni limit 200 divas / request
SLEEP = 0.5

# (naam, exchange, token)
INDICES = [("Nifty 50", "NSE", "99926000"),
           ("Nifty Bank", "NSE", "99926009"),
           ("Sensex", "BSE", "99919000")]

# Ladder (tamara chart na levels)
LADDER = [-3.618, -2.618, -2, -1.618, -1.272, -0.618, -0.236, 0, 0.236, 0.382, 0.5, 0.618,
          0.786, 0.886, 1, 1.272, 1.414, 1.618, 2, 2.272, 2.414, 2.618, 3.618]
# Open kya zone ma padyo (edges ratio ma)
ZEDGES = [-1.272, -0.618, -0.236, 0, 0.236, 0.5, 0.786, 1, 1.272, 1.618]


def zone_of(r):
    i = 0
    while i < len(ZEDGES) and r >= ZEDGES[i]:
        i += 1
    return i            # 0 .. len(ZEDGES)


def zone_label(i):
    if i == 0:
        return f"Below {ZEDGES[0]}"
    if i == len(ZEDGES):
        return f"Above {ZEDGES[-1]}"
    return f"{ZEDGES[i-1]} to {ZEDGES[i]}"


def profile(d, bins=BINS):
    """Ek divas na candles -> point / vhigh / vlow (time-weighted, index ma volume nathi)."""
    lo, hi = float(d["l"].min()), float(d["h"].max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for l, h in zip(d["l"].values, d["h"].values):
        i0 = min(int((l - lo) / (hi - lo) * bins), bins - 1)
        i1 = min(int((h - lo) / (hi - lo) * bins), bins - 1)
        vol[i0:i1 + 1] += 1.0 / (i1 - i0 + 1)
    poc = int(vol.argmax()); total = vol.sum()
    lo_i = hi_i = poc; acc = vol[poc]
    while acc < VALUE_AREA * total and (lo_i > 0 or hi_i < bins - 1):
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    return {"point": float((edges[poc] + edges[poc + 1]) / 2),
            "vhigh": float(edges[hi_i + 1]), "vlow": float(edges[lo_i])}


def analyze(m15):
    """m15: DataFrame ts,o,h,l,c (intraday). Return (days list, latest levels dict)."""
    m15 = m15.sort_values("ts").drop_duplicates("ts")
    t = m15["ts"].dt.time
    m15 = m15[(t >= dt.time(9, 15)) & (t <= dt.time(15, 30))].copy()
    m15["date"] = m15["ts"].dt.date
    groups = {d: g.reset_index(drop=True) for d, g in m15.groupby("date") if len(g) >= 10}
    dates = sorted(groups)
    days, levels = [], {}
    for k, d in enumerate(dates):
        g = groups[d]
        prof = profile(g)
        if prof:
            levels[str(d)] = {a: round(b, 2) for a, b in prof.items()}
        if k == 0:
            continue
        pg = groups[dates[k - 1]]
        pr = levels.get(str(dates[k - 1]))
        if not pr or pr["vhigh"] <= pr["vlow"]:
            continue
        rng = pr["vhigh"] - pr["vlow"]
        op = float(g["o"].iloc[0])
        prev_close = float(pg["c"].iloc[-1])
        r = (op - pr["vlow"]) / rng
        up_l = [x for x in LADDER if x > r]
        dn_l = [x for x in LADDER if x <= r]
        nxt_up = up_l[0] if up_l else None
        nxt_dn = dn_l[-1] if dn_l else None
        pu = pr["vlow"] + rng * nxt_up if nxt_up is not None else None
        pd_ = pr["vlow"] + rng * nxt_dn if nxt_dn is not None else None
        first = "none"
        for h, l in zip(g["h"].values, g["l"].values):
            hu = pu is not None and h >= pu
            hd = pd_ is not None and l <= pd_
            if hu and hd:
                first = "both"; break
            if hu:
                first = "up"; break
            if hd:
                first = "dn"; break
        hi, lo, cl = float(g["h"].max()), float(g["l"].min()), float(g["c"].iloc[-1])
        days.append({
            "date": str(d), "open": round(op, 2), "r": round(r, 3), "zone": zone_of(r),
            "gap": round((op / prev_close - 1) * 100, 2),
            "up": round((hi / op - 1) * 100, 2), "dn": round((lo / op - 1) * 100, 2),
            "cl": round((cl / op - 1) * 100, 2), "first": first,
            "up_lvl": nxt_up, "dn_lvl": nxt_dn,
            # nazik na level thi pachhi pachhu aavyu? (touch pachhi close te level ni pachhal)
            "rev_up": (first == "up" and cl < pu), "rev_dn": (first == "dn" and cl > pd_),
            "lv": {"vlow": pr["vlow"], "vhigh": pr["vhigh"]},
        })
    return days, levels


def summarize(days):
    zones = []
    for i in range(len(ZEDGES) + 1):
        z = [d for d in days if d["zone"] == i]
        n = len(z)
        row = {"zone": i, "label": zone_label(i), "n": n}
        if n:
            f = lambda k: round(float(np.mean([d[k] for d in z])), 2)
            row.update(avg_cl=f("cl"), avg_up=f("up"), avg_dn=f("dn"),
                       up_days=round(100 * np.mean([d["cl"] > 0 for d in z])),
                       first_up=round(100 * np.mean([d["first"] == "up" for d in z])),
                       first_dn=round(100 * np.mean([d["first"] == "dn" for d in z])),
                       first_both=round(100 * np.mean([d["first"] == "both" for d in z])),
                       first_none=round(100 * np.mean([d["first"] == "none" for d in z])))
            fu = [d for d in z if d["first"] == "up"]; fd = [d for d in z if d["first"] == "dn"]
            row["rev_up"] = round(100 * np.mean([d["rev_up"] for d in fu])) if len(fu) >= 3 else None
            row["rev_dn"] = round(100 * np.mean([d["rev_dn"] for d in fd])) if len(fd) >= 3 else None
            row["n_up"], row["n_dn"] = len(fu), len(fd)
            row["recent"] = [{k: d[k] for k in ("date", "open", "r", "gap", "up", "dn", "cl", "first")}
                             for d in z[-8:]][::-1]
        zones.append(row)
    base = {"n": len(days)}
    if days:
        base.update(avg_cl=round(float(np.mean([d["cl"] for d in days])), 2),
                    up_days=round(100 * np.mean([d["cl"] > 0 for d in days])),
                    avg_up=round(float(np.mean([d["up"] for d in days])), 2),
                    avg_dn=round(float(np.mean([d["dn"] for d in days])), 2))
    return zones, base


# ---------------- Angel One ----------------
def login():
    from SmartApi import SmartConnect
    import pyotp
    api = SmartConnect(api_key=os.environ["ANGEL_API_KEY"], timeout=30)
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
    res = api.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PASSWORD"], totp)
    if not res.get("status"):
        print("Login fail:", res); sys.exit(1)
    return api


def fetch_chunk(api, exch, token, frm, to, retries=4):
    params = {"exchange": exch, "symboltoken": token, "interval": "FIFTEEN_MINUTE",
              "fromdate": frm.strftime("%Y-%m-%d 09:15"), "todate": to.strftime("%Y-%m-%d %H:%M")}
    for i in range(retries):
        try:
            res = api.getCandleData(params)
            if isinstance(res, dict) and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["ts", "o", "h", "l", "c", "v"])
                df["ts"] = pd.to_datetime(df["ts"], utc=False).dt.tz_localize(None)
                return df
            if isinstance(res, dict):
                msg = str(res.get("message", "")).lower()
                if res.get("errorcode") == "AB1010" or "rate" in msg or "access denied" in msg:
                    time.sleep(3 * (i + 1)); continue
                if res.get("status") and not res.get("data"):
                    return None
        except Exception as e:
            print("  err", e); time.sleep(2 * (i + 1))
    return None


def fetch_all(api, exch, token, years):
    end = dt.datetime.now()
    start = end - dt.timedelta(days=int(365 * years))
    parts, cur = [], start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=CHUNK_DAYS), end)
        df = fetch_chunk(api, exch, token, cur, nxt); time.sleep(SLEEP)
        if df is not None:
            parts.append(df)
        cur = nxt + dt.timedelta(days=1)
    if not parts:
        return None
    return pd.concat(parts).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def main():
    years = float(os.environ.get("YEARS", "3"))
    api = login()
    out = {"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "ladder": LADDER, "edges": ZEDGES, "indices": {}}
    for name, exch, token in INDICES:
        print("->", name)
        m15 = fetch_all(api, exch, token, years)
        if m15 is None or len(m15) < 200:
            print("  data nathi:", name); continue
        days, levels = analyze(m15)
        zones, base = summarize(days)
        last = sorted(levels)[-6:]
        out["indices"][name] = {"zones": zones, "base": base,
                                "levels": {d: levels[d] for d in last},
                                "from": days[0]["date"] if days else None,
                                "to": days[-1]["date"] if days else None}
        print("  days:", len(days), "latest levels:", {d: levels[d] for d in last[-2:]})
    with open("openzone.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("saved openzone.json", list(out["indices"]))


if __name__ == "__main__":
    main()
