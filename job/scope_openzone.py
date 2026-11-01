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

# Ladder = site na level (Stock page jevu): ratio VLOW=0, VHIGH=1 par based
LVNAME = {-0.618: "Deep reversal 2", -0.272: "Deep reversal 1", 0: "VLOW", 0.25: "Lower reversal",
          0.75: "Upper reversal", 1: "VHIGH", 1.272: "Extension"}
LADDER = sorted(LVNAME)
REACH = LADDER + [1.618, 2.0]


def zone_key(r, pr=None):
    """Open (ratio r) kya be level vachche. pr = Point no ratio (hoy to e pan level ganay)."""
    lv = [(x, n) for x, n in LVNAME.items()]
    if pr is not None:
        lv.append((pr, "Point"))
    lv.sort()
    lo = [(x, n) for x, n in lv if x <= r]
    up = [(x, n) for x, n in lv if x > r]
    return (lo[-1] if lo else None), (up[0] if up else None)


def touch(g, vlow, rng, lo, up, cl):
    """Open pachhi pehla kayo nazik level aavyo + te level thi pachha pharyo ke nahi."""
    pu = vlow + rng * up[0] if up else None
    pdn = vlow + rng * lo[0] if lo else None
    first = "none"
    for h, l in zip(g["h"].values, g["l"].values):
        hu = pu is not None and h >= pu
        hd = pdn is not None and l <= pdn
        if hu and hd:
            first = "both"; break
        if hu:
            first = "up"; break
        if hd:
            first = "dn"; break
    return first, bool(first == "up" and cl < pu), bool(first == "dn" and cl > pdn)


def path_of(g, pr, rng, op):
    """Divas no rasto: open pachhi kayo level kyare pehli vaar aavyo (upar / niche) ane te level thi kyare pachha pharyo (close)."""
    lv = [(pr["vlow"] + rng * x, n) for x, n in LVNAME.items()] + [(pr["point"], "Point")]
    ev = []
    for price, name in lv:
        if price > op:
            idx = next((i for i, h in enumerate(g["h"].values) if h >= price), None)
            if idx is None:
                continue
            ev.append((idx, 0, "up", name, price))
            j = next((i for i in range(idx, len(g)) if g["c"].values[i] < price), None)
            if j is not None:
                ev.append((j, 1, "rev_dn", name, price))      # upar thi pachha niche band (rejection)
        elif price < op:
            idx = next((i for i, l in enumerate(g["l"].values) if l <= price), None)
            if idx is None:
                continue
            ev.append((idx, 0, "dn", name, price))
            j = next((i for i in range(idx, len(g)) if g["c"].values[i] > price), None)
            if j is not None:
                ev.append((j, 1, "rev_up", name, price))      # niche thi pachha upar band
    ev.sort(key=lambda e: (e[0], e[1], e[4]))
    return [[g["ts"].iloc[i].strftime("%H:%M"), k, n, round(float(pr_), 2)] for i, _, k, n, pr_ in ev]


def label_of(lo, up):
    if lo is None:
        return f"Below {up[1]}"
    if up is None:
        return f"Above {lo[1]}"
    return f"{lo[1]} to {up[1]}"


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
    """m15: DataFrame ts,o,h,l,c (intraday). Return (days list, levels dict)."""
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
        pratio = (pr["point"] - pr["vlow"]) / rng
        hi, lo_, cl = float(g["h"].max()), float(g["l"].min()), float(g["c"].iloc[-1])
        rec = {"date": str(d), "open": round(op, 2), "r": round(r, 3),
               "gap": round((op / prev_close - 1) * 100, 2),
               "up": round((hi / op - 1) * 100, 2), "dn": round((lo_ / op - 1) * 100, 2),
               "cl": round((cl / op - 1) * 100, 2),
               "mx": round((hi - pr["vlow"]) / rng, 3), "mn": round((lo_ - pr["vlow"]) / rng, 3)}
        for tag, prr in (("p", pratio), ("n", None)):      # p = Point sathe, n = Point vagar
            lo, up = zone_key(r, prr)
            first, ru, rd = touch(g, pr["vlow"], rng, lo, up, cl)
            rec[tag] = {"key": label_of(lo, up), "first": first, "rev_up": ru, "rev_dn": rd}
        rec["path"] = path_of(g, pr, rng, op)
        days.append(rec)
    return days, levels


def summarize(days, tag):
    groups = {}
    for d in days:
        groups.setdefault(d[tag]["key"], []).append(d)
    zones = {}
    for key, z in groups.items():
        n = len(z)
        f = lambda k: round(float(np.mean([d[k] for d in z])), 2)
        fs = [d[tag]["first"] for d in z]
        row = {"label": key, "n": n, "avg_cl": f("cl"), "avg_up": f("up"), "avg_dn": f("dn"),
               "up_days": round(100 * np.mean([d["cl"] > 0 for d in z])),
               "first_up": round(100 * np.mean([x == "up" for x in fs])),
               "first_dn": round(100 * np.mean([x == "dn" for x in fs])),
               "first_both": round(100 * np.mean([x == "both" for x in fs])),
               "first_none": round(100 * np.mean([x == "none" for x in fs]))}
        fu = [d for d in z if d[tag]["first"] == "up"]; fd = [d for d in z if d[tag]["first"] == "dn"]
        row["rev_up"] = round(100 * np.mean([d[tag]["rev_up"] for d in fu])) if len(fu) >= 3 else None
        row["rev_dn"] = round(100 * np.mean([d[tag]["rev_dn"] for d in fd])) if len(fd) >= 3 else None
        row["n_up"], row["n_dn"] = len(fu), len(fd)
        # chance that the day's high / low reaches each level (ratio of VLOW=0 .. VHIGH=1), only when enough days
        if n >= 5:
            row["reach"] = {str(L): {"up": round(100 * np.mean([d["mx"] >= L for d in z])),
                                      "dn": round(100 * np.mean([d["mn"] <= L for d in z]))} for L in REACH}
        row["recent"] = [{"date": d["date"], "open": d["open"], "gap": d["gap"], "up": d["up"],
                          "dn": d["dn"], "cl": d["cl"], "first": d[tag]["first"], "path": d.get("path", [])} for d in z[-10:]][::-1]
        zones[key] = row
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
           "indices": {}}
    for name, exch, token in INDICES:
        print("->", name)
        m15 = fetch_all(api, exch, token, years)
        if m15 is None or len(m15) < 200:
            print("  data nathi:", name); continue
        days, levels = analyze(m15)
        zones, base = summarize(days, "p")
        zones_np, _ = summarize(days, "n")
        last = sorted(levels)[-6:]
        out["indices"][name] = {"zones": zones, "zones_np": zones_np, "base": base,
                                "levels": {d: levels[d] for d in last},
                                "from": days[0]["date"] if days else None,
                                "to": days[-1]["date"] if days else None}
        print("  days:", len(days), "latest levels:", {d: levels[d] for d in last[-2:]})
    with open("openzone.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("saved openzone.json", list(out["indices"]))


if __name__ == "__main__":
    main()
