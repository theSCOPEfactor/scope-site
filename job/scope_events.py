"""Scope events job -> events.json
Aajna important stocks: results (board meeting), corporate actions (dividend/bonus/split), exchange announcements
(orders, acquisitions, outcome of board meeting ...) ane news (Google News) jema important keyword hoy.
Je source na male te chhodi didho che (ek fail thay to bija chalu rahe). Env: NEWS_TOP (default 120)"""
import os, re, json, time, datetime as dt
from urllib.parse import quote
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}

POS = ["wins", "bags", "secures", "order", "surge", "jump", "rally", "soar", "upgrade", "record", "approval", "approved", "profit rises",
       "profit jumps", "net profit up", "beats", "buyback", "bonus", "dividend", "acquire", "acquisition", "stake buy", "target price raised",
       "strong", "gains", "rises", "launch", "contract", "partnership", "expansion"]
NEG = ["falls", "slump", "plunge", "tumble", "downgrade", "penalty", "probe", "fraud", "raid", "loss", "net loss", "resign", "default", "cuts",
       "miss", "warning", "bans?\\b", "fire\\b", "recall", "sebi order", "investigation", "delist", "block deal sell", "weak", "decline", "drops"]
IMPORTANT = ["result", "earnings", "q1", "q2", "q3", "q4", "profit", "order", "contract", "acqui", "merger", "demerger", "stake", "upgrade", "downgrade",
             "target price", "buyback", "dividend", "bonus", "split", "penalty", "fraud", "probe", "raid", "fire\\b", "bans?\\b", "usfda", "approval", "block deal",
             "rating", "fund raise", "qip", "ipo", "listing", "resigns", "appoint", "default", "guidance", "capex", "partnership", "wins", "bags", "secures"]


def tone(text):
    t = text.lower()
    p = sum(1 for w in POS if re.search(r"\b" + w, t))
    n = sum(1 for w in NEG if re.search(r"\b" + w, t))
    return "pos" if p > n else "neg" if n > p else "neu"


def important(text):
    t = text.lower()
    return any(re.search(r"\b" + w, t) for w in IMPORTANT)


def ist_now():
    return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)


def nse_session():
    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://www.nseindia.com", timeout=20)
        s.get("https://www.nseindia.com/companies-listing/corporate-filings-event-calendar", timeout=20)
    except Exception as e:
        print("NSE cookie fail:", e)
    return s


def nse_json(s, path, params):
    try:
        r = s.get("https://www.nseindia.com/api/" + path, params=params, timeout=30)
        if r.ok:
            return r.json()
        print("NSE", path, "->", r.status_code)
    except Exception as e:
        print("NSE", path, "fail:", str(e)[:120])
    return None


def first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def fmt(d):
    return d.strftime("%d-%m-%Y")


def parse_dt(s):
    s = str(s or "").strip()
    for f in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            continue
    m = re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{4})", s)
    if m:
        return dt.datetime.strptime(m.group(1), "%d-%b-%Y")
    return None


def nse_events(items, today):
    s = nse_session()
    # 1) board meetings / results
    cal = nse_json(s, "event-calendar", {"index": "equities", "from_date": fmt(today), "to_date": fmt(today + dt.timedelta(days=7))})
    n = 0
    for r in (cal if isinstance(cal, list) else (cal or {}).get("data", [])) or []:
        sym = first(r, "symbol")
        purpose = first(r, "purpose", "bm_desc", "desc")
        if not sym:
            continue
        d = parse_dt(first(r, "date", "bm_date"))
        res = "result" in purpose.lower()
        items.append({"symbol": sym, "kind": "result" if res else "meeting", "tone": "neu",
                      "title": ("Results due" if res else "Board meeting") + (": " + purpose[:160] if purpose else ""),
                      "date": d.strftime("%Y-%m-%d") if d else str(today), "src": "NSE"})
        n += 1
    print("event-calendar:", n)
    # 2) corporate actions (dividend / bonus / split) ex-date next 7 days
    ca = nse_json(s, "corporates-corporateActions", {"index": "equities", "from_date": fmt(today), "to_date": fmt(today + dt.timedelta(days=7))})
    n = 0
    for r in (ca if isinstance(ca, list) else (ca or {}).get("data", [])) or []:
        sym, subj = first(r, "symbol"), first(r, "subject", "purpose")
        if not sym or not subj:
            continue
        d = parse_dt(first(r, "exDate", "exdate", "recDate"))
        items.append({"symbol": sym, "kind": "action", "tone": "pos" if re.search(r"dividend|bonus", subj, re.I) else "neu",
                      "title": subj[:180] + (" (ex-date " + d.strftime("%d %b") + ")" if d else ""),
                      "date": d.strftime("%Y-%m-%d") if d else str(today), "src": "NSE"})
        n += 1
    print("corporate actions:", n)
    # 3) aajna announcements (orders, acquisitions, outcome of board meeting, results declared)
    an = nse_json(s, "corporate-announcements", {"index": "equities", "from_date": fmt(today), "to_date": fmt(today)})
    n = 0
    for r in (an if isinstance(an, list) else (an or {}).get("data", [])) or []:
        sym = first(r, "symbol")
        desc, txt = first(r, "desc", "subject"), first(r, "attchmntText", "details")
        full = desc + " " + txt
        if not sym or not important(full):
            continue
        if re.search(r"trading window|loss of share|duplicate|intimation of .*schedule|analyst|investor meet|newspaper", full, re.I):
            continue
        res = bool(re.search(r"financial result|outcome of board|results", full, re.I))
        items.append({"symbol": sym, "kind": "result" if res else "announcement", "tone": tone(full),
                      "title": (desc + (": " + txt if txt else ""))[:220], "date": str(today), "src": "NSE announcement"})
        n += 1
    print("announcements kept:", n)


def google_news(items, symbols, names, today):
    import feedparser
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=30)
    kept = 0
    for i, sym in enumerate(symbols, 1):
        nm = names.get(sym) or sym
        q = quote(f'"{nm}" share when:1d')
        try:
            d = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")
        except Exception as e:
            print("  news fail", sym, str(e)[:80])
            continue
        got = 0
        for e in d.entries[:8]:
            title = re.sub(r"\s+-\s+[^-]+$", "", e.get("title", "")).strip()
            src = (e.get("source") or {}).get("title", "")
            pub = e.get("published_parsed")
            if pub and dt.datetime(*pub[:6]) < cutoff:
                continue
            if not title or not important(title):
                continue
            items.append({"symbol": sym, "kind": "news", "tone": tone(title), "title": title[:200], "date": str(today),
                          "src": src or "Google News", "url": e.get("link", "")})
            kept += 1; got += 1
            if got >= 2:
                break
        time.sleep(0.25)
        if i % 40 == 0:
            print(f"  news {i}/{len(symbols)}")
    print("news kept:", kept)


def main():
    today = ist_now().date()
    items = []
    try:
        nse_events(items, today)
    except Exception as e:
        print("NSE part failed:", e)
    syms, names = [], {}
    try:
        data = json.load(open("data.json"))
        top = sorted([r for r in data["stocks"] if r.get("price") and r.get("avg_vol")], key=lambda r: -(r["price"] * r["avg_vol"]))
        syms = [r["symbol"] for r in top[:int(os.environ.get("NEWS_TOP", "120"))]]
    except Exception as e:
        print("data.json not read:", e)
    try:
        det = json.load(open("details.json")).get("stocks", {})
        names = {k: v.get("name") for k, v in det.items() if v.get("name")}
    except Exception:
        pass
    if syms and not os.environ.get("SKIP_NEWS"):
        try:
            google_news(items, syms, names, today)
        except Exception as e:
            print("news part failed:", e)
    # same symbol+title duplicate kadho; order: result, action, announcement, news
    seen, out = set(), []
    order = {"result": 0, "announcement": 1, "action": 2, "meeting": 3, "news": 4}
    for it in sorted(items, key=lambda x: (order.get(x["kind"], 9), x["symbol"])):
        k = (it["symbol"], it["title"][:60].lower())
        if k in seen:
            continue
        seen.add(k); out.append(it)
    out = out[:500]
    if not out and os.path.exists("events.json"):
        print("nothing fetched: old events.json rakhyu")
        return
    with open("events.json", "w") as f:
        json.dump({"updated": ist_now().strftime("%Y-%m-%d %H:%M"), "items": out}, f, separators=(",", ":"), ensure_ascii=False)
    print("Saved events.json:", len(out), "items")


if __name__ == "__main__":
    main()
