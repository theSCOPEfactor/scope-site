"""
Scope details job (GitHub Actions ma chale che) -> details.json

  - Nifty index constituents (kaya stocks kaya index ma che) + industry   (niftyindices.com CSV)
  - Fundamentals (Yahoo Finance via yfinance)
  - Latest news headlines (Google News RSS)

Je data na male te khali rakhay che; juno data jaato nathi (merge thay che), etle aadho run pan chale.
Optional env: MAX_DETAILS (default 600), SKIP_NEWS=1
"""
import os, re, io, csv, json, time, datetime as dt
from urllib.parse import quote
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "text/csv,*/*"}
BASES = ["https://www.niftyindices.com/IndexConstituent/", "https://archives.nseindia.com/content/indices/"]

# label -> csv file name
INDEX_FILES = {
    "Nifty 50": "ind_nifty50list.csv", "Nifty Next 50": "ind_niftynext50list.csv", "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv", "Nifty 500": "ind_nifty500list.csv",
    "Nifty Midcap 50": "ind_niftymidcap50list.csv", "Nifty Midcap 100": "ind_niftymidcap100list.csv",
    "Nifty Midcap 150": "ind_niftymidcap150list.csv", "Nifty Smallcap 50": "ind_niftysmallcap50list.csv",
    "Nifty Smallcap 100": "ind_niftysmallcap100list.csv", "Nifty Smallcap 250": "ind_niftysmallcap250list.csv",
    "Nifty Bank": "ind_niftybanklist.csv", "Nifty IT": "ind_niftyitlist.csv", "Nifty Auto": "ind_niftyautolist.csv",
    "Nifty FMCG": "ind_niftyfmcglist.csv", "Nifty Pharma": "ind_niftypharmalist.csv", "Nifty Metal": "ind_niftymetallist.csv",
    "Nifty Realty": "ind_niftyrealtylist.csv", "Nifty Energy": "ind_niftyenergylist.csv", "Nifty Media": "ind_niftymedialist.csv",
    "Nifty PSU Bank": "ind_niftypsubanklist.csv", "Nifty Private Bank": "ind_nifty_privatebanklist.csv",
    "Nifty Financial Services": "ind_niftyfinancelist.csv", "Nifty Healthcare": "ind_niftyhealthcarelist.csv",
    "Nifty Consumer Durables": "ind_niftyconsumerdurableslist.csv", "Nifty Oil & Gas": "ind_niftyoilgaslist.csv",
}
ALIASES = {"Nifty Financial Services": ["Nifty Fin Service", "Nifty Financial Services 25/50"],
           "Nifty Oil & Gas": ["Nifty Oil and Gas"], "Nifty Private Bank": ["Nifty Pvt Bank"]}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("&", "and"))


def get_csv(fn):
    for b in BASES:
        try:
            r = requests.get(b + fn, headers=UA, timeout=30)
            if r.ok and "Symbol" in r.text[:400]:
                return list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
        except Exception as e:
            print("  csv fail", b + fn, e)
    return None


def num(x, mult=1.0, nd=2):
    try:
        v = float(x) * mult
        return round(v, nd) if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def fundamentals(sym):
    import yfinance as yf
    info = yf.Ticker(sym + ".NS").info or {}
    if not info or not (info.get("marketCap") or info.get("trailingPE") or info.get("longName")):
        return None
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    dy = None
    if info.get("dividendRate") and price:
        dy = round(info["dividendRate"] / price * 100, 2)
    return {
        "name": info.get("longName") or info.get("shortName"),
        "mcap_cr": num(info.get("marketCap"), 1 / 1e7, 0),
        "pe": num(info.get("trailingPE")), "fwd_pe": num(info.get("forwardPE")), "pb": num(info.get("priceToBook")),
        "roe": num(info.get("returnOnEquity"), 100, 1), "de": num(info.get("debtToEquity"), 1, 1),
        "div_yield": dy, "eps": num(info.get("trailingEps")),
        "rev_growth": num(info.get("revenueGrowth"), 100, 1), "earn_growth": num(info.get("earningsGrowth"), 100, 1),
        "margin": num(info.get("profitMargins"), 100, 1), "insiders": num(info.get("heldPercentInsiders"), 100, 1),
        "y_sector": info.get("sector"), "y_industry": info.get("industry"),
        "about": (info.get("longBusinessSummary") or "")[:420],
    }


def news(name, sym):
    import feedparser
    q = quote(f'"{name or sym}" share')
    d = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")
    out = []
    for e in d.entries[:6]:
        title, src = e.get("title", ""), ""
        if e.get("source"):
            src = e["source"].get("title", "")
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        date = ""
        if e.get("published_parsed"):
            date = time.strftime("%Y-%m-%d", e.published_parsed)
        out.append({"t": title, "u": e.get("link", ""), "s": src, "d": date})
    return out


def main():
    old = {}
    if os.path.exists("details.json"):
        try:
            old = json.load(open("details.json"))
        except Exception:
            old = {}
    stocks = old.get("stocks", {})
    indices = old.get("indices", {})
    names = {}

    print("Index constituents ...")
    for label, fn in INDEX_FILES.items():
        rows = get_csv(fn)
        if not rows:
            print("  missing:", label); continue
        syms = [r["Symbol"].strip().upper() for r in rows if r.get("Symbol")]
        entry = {"label": label, "symbols": syms}
        for k in [label] + ALIASES.get(label, []):
            indices[norm(k)] = entry
        for r in rows:
            s = (r.get("Symbol") or "").strip().upper()
            if not s:
                continue
            names[s] = r.get("Company Name") or s
            d = stocks.setdefault(s, {})
            if r.get("Industry"):
                d["industry"] = r["Industry"].strip()
        print(f"  {label}: {len(syms)}")
        time.sleep(0.5)

    todo = sorted(stocks.keys())
    mx = int(os.environ.get("MAX_DETAILS", "600"))
    todo = todo[:mx]
    print("Fundamentals + news for", len(todo), "stocks")
    fails_in_row = 0
    for i, s in enumerate(todo, 1):
        d = stocks[s]
        try:
            f = fundamentals(s)
            if f:
                d.update({k: v for k, v in f.items() if v not in (None, "")})
                fails_in_row = 0
            else:
                fails_in_row += 1
        except Exception as e:
            fails_in_row += 1
            if fails_in_row in (1, 10):
                print("  yfinance problem:", s, str(e)[:120])
            time.sleep(2)
        if not os.environ.get("SKIP_NEWS"):
            try:
                n = news(d.get("name") or names.get(s), s)
                if n:
                    d["news"] = n
            except Exception as e:
                print("  news problem:", s, str(e)[:100])
        d.setdefault("name", names.get(s, s))
        if fails_in_row >= 30:
            print("Too many failures in a row (Yahoo may be limiting us). Saving what we have.")
            break
        time.sleep(0.35)
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)

    ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    out = {"updated": ist.strftime("%Y-%m-%d %H:%M"), "indices": indices, "stocks": stocks}
    with open("details.json", "w") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False)
    print("Saved details.json:", len(stocks), "stocks,", len({id(v) for v in indices.values()}), "indices")


if __name__ == "__main__":
    main()
