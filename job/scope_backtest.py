"""
Scope backtest job (GitHub Actions, manually run) -> backtest.json

Question answered:  "When price is at VLOW / the magnet / VHIGH, what happened over the next 1, 3 and 5 days?"
No look-ahead: the levels on day D are built only from candles up to day D (last 250 trading days).
Compared with a baseline (all days). Uses the same Angel 1h data as scope_daily.py.

Optional env: MAX_BT (default 400 = most liquid stocks), STEP (evaluate every Nth day, default 2)
"""
import os, sys, json, time, datetime as dt
import numpy as np
import pandas as pd

BINS, VALUE_AREA, WINDOW, MIN_DAYS = 100, 0.70, 250, 60
HORIZONS = (1, 3, 5)
EVENTS = [("below_vlow", "Below VLOW"), ("at_vlow", "At VLOW (within 2% above)"), ("at_magnet", "At the magnet (within 1%)"),
          ("at_vhigh", "At VHIGH (within 2% below)"), ("above_vhigh", "Above VHIGH"), ("inside", "Elsewhere in the range")]


def to_daily(h1):
    d = h1.copy()
    d["date"] = d["ts"].dt.date
    return d.groupby("date").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum"))


def stage_series(daily):
    c = daily["c"]
    s50, s150, s200 = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    hi52 = daily["h"].rolling(250, min_periods=1).max()
    lo52 = daily["l"].rolling(250, min_periods=1).min()
    rising = s200 > s200.shift(20)
    s2 = ((c > s150) & (c > s200) & (s150 > s200) & rising & (s50 > s150) & (c > s50) & (c >= 0.75 * hi52) & (c >= 1.30 * lo52))
    s4 = (c < s200) & (~rising) & (s50 < s200)
    s3 = (~s2) & (~s4) & (c < s50) & (c > s200 * 0.95) & (s200.notna())
    st = pd.Series(1, index=daily.index)
    st[s3] = 3; st[s4] = 4; st[s2] = 2; st[s200.isna()] = 0
    return st


def profile(l, h, v):
    """vectorised volume profile -> (point, vhigh, vlow)"""
    lo, hi = l.min(), h.max()
    if not hi > lo:
        return None
    v = v.astype(float)
    if v.sum() <= 0:
        return None
    i0 = np.minimum(((l - lo) / (hi - lo) * BINS).astype(int), BINS - 1)
    i1 = np.minimum(((h - lo) / (hi - lo) * BINS).astype(int), BINS - 1)
    w = v / (i1 - i0 + 1)
    diff = np.zeros(BINS + 1)
    np.add.at(diff, i0, w)
    np.add.at(diff, i1 + 1, -w)
    vol = np.cumsum(diff)[:BINS]
    poc = int(vol.argmax()); total = vol.sum(); a = b = poc; acc = vol[poc]
    while acc < VALUE_AREA * total and (a > 0 or b < BINS - 1):
        up = vol[b + 1] if b < BINS - 1 else -1
        dn = vol[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1; acc += vol[b]
        else:
            a -= 1; acc += vol[a]
    edges = np.linspace(lo, hi, BINS + 1)
    return (edges[poc] + edges[poc + 1]) / 2, edges[b + 1], edges[a]


def analyze(h1, step=2):
    """-> list of samples {ev, st, r1, r3, r5} for one stock"""
    daily = to_daily(h1)
    n = len(daily)
    if n < MIN_DAYS + max(HORIZONS) + 1:
        return []
    stage = stage_series(daily).values
    close = daily["c"].values
    dates = np.array(daily.index)
    cdate = h1["ts"].dt.date.values
    # number of candles up to and including each day
    cnt = np.searchsorted(cdate, dates, side="right")
    L, H, V = h1["l"].values, h1["h"].values, h1["v"].values
    out = []
    for i in range(MIN_DAYS, n - max(HORIZONS), step):
        a = cnt[max(0, i - WINDOW + 1) - 1] if i - WINDOW + 1 > 0 else 0
        b = cnt[i]
        pr = profile(L[a:b], H[a:b], V[a:b])
        if not pr:
            continue
        pt, vh, vl = pr
        p = close[i]
        if p < vl:
            ev = "below_vlow"
        elif p <= vl * 1.02:
            ev = "at_vlow"
        elif abs(p / pt - 1) <= 0.01:
            ev = "at_magnet"
        elif p > vh:
            ev = "above_vhigh"
        elif p >= vh * 0.98:
            ev = "at_vhigh"
        else:
            ev = "inside"
        s = {"ev": ev, "st": int(stage[i])}
        for k in HORIZONS:
            s[f"r{k}"] = close[i + k] / p - 1
        out.append(s)
    return out


def stats(vals):
    a = np.array(vals, dtype=float)
    if len(a) == 0:
        return None
    return {"avg": round(float(a.mean() * 100), 2), "med": round(float(np.median(a) * 100), 2), "up": round(float((a > 0).mean() * 100), 1)}


def summarize(samples):
    out = []
    for group, flt in (("all", lambda s: True), ("stage2", lambda s: s["st"] == 2), ("other", lambda s: s["st"] in (1, 3, 4))):
        S = [s for s in samples if flt(s)]
        rows = [("all_days", "All days (baseline)", S)] + [(k, lbl, [s for s in S if s["ev"] == k]) for k, lbl in EVENTS]
        for k, lbl, X in rows:
            if len(X) < 1:
                continue
            out.append({"group": group, "ev": k, "label": lbl, "n": len(X),
                        **{f"r{h}": stats([s[f"r{h}"] for s in X]) for h in HORIZONS}})
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import scope_daily as sd            # Angel login + candle fetch (same as the daily job)
    data = json.load(open("data.json"))
    cand = [s for s in data["stocks"] if s.get("avg_vol", 0) >= 200000 and s.get("stage", 0) != 0]
    cand.sort(key=lambda s: -s["avg_vol"])
    cand = cand[: int(os.environ.get("MAX_BT", "400"))]
    step = int(os.environ.get("STEP", "2"))
    uni, _ = sd.load_universe()
    tok = {u["symbol"]: u["token"] for u in uni}
    api = sd.login()
    samples, by_stock, used = [], {}, 0
    for i, s in enumerate(cand, 1):
        t = tok.get(s["symbol"])
        if not t:
            continue
        h1 = sd.fetch_hourly(api, t); time.sleep(sd.SLEEP)
        if h1 is None or len(h1) < 400:
            continue
        smp = analyze(h1, step)
        if not smp:
            continue
        used += 1
        samples += smp
        rec = {}
        for key, ev in (("lo", "at_vlow"), ("mg", "at_magnet"), ("hi", "at_vhigh")):
            X = [x["r3"] for x in smp if x["ev"] == ev]
            if len(X) >= 3:
                st = stats(X)
                rec[key] = [len(X), st["avg"], st["up"]]
        if rec:
            by_stock[s["symbol"]] = rec
        if i % 50 == 0:
            print(f"{i}/{len(cand)} stocks, {len(samples)} samples", flush=True)
    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    out = {"updated": ist.strftime("%Y-%m-%d %H:%M"), "stocks_used": used, "samples": len(samples), "step": step,
           "rows": summarize(samples), "by_stock": by_stock}
    with open("backtest.json", "w") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False)
    print("Saved backtest.json:", used, "stocks,", len(samples), "samples")


if __name__ == "__main__":
    main()
