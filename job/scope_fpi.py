"""
Scope FPI job: NSDL nu Fortnightly Sector-wise FPI Investment data -> fpiflow.json
URL pattern: https://www.fpi.nsdl.co.in/web/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_Oct312026.html
Report har fortnight (15 tarikh ane mahina-end) pachhi aave che. Job chhella 4 report try kare che.
Kai pan tute to fpiflow.json ma 'error' lakhe che (workflow fail nathi thatu).
Output: fpiflow.json = {updated, periods:[{id,asof,net:{label: {sector: {eq,tot}}}}], error?}
"""
import os, re, io, sys, json, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "fpiflow.json")
BASE = "https://www.fpi.nsdl.co.in/web/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_{}.html"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def num(x):
    s = str(x).strip().replace(",", "").replace("−", "-")
    if not s or s.lower() in ("nan", "-", "--"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def period_ends(today, n=4):
    """Chhella n fortnight-end tarikho (15 ane mahina-end), navi pehla."""
    out, d = [], today
    while len(out) < n + 2 and (today - d).days < 150:
        for cand in (d.replace(day=15), (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)):
            if cand < today and cand not in out:
                out.append(cand)
        d = (d.replace(day=1) - dt.timedelta(days=1))
    return sorted(set(out), reverse=True)[:n]


def grid(html):
    """Sauthi moti <table> ne rowspan/colspan expand kari ne 2D list banave."""
    import lxml.html
    doc = lxml.html.fromstring(html)
    tabs = doc.xpath("//table")
    t = max(tabs, key=lambda x: len(x.xpath(".//tr")) * max([len(r.xpath("./th|./td")) for r in x.xpath(".//tr")] or [1]))
    g, pend = [], {}
    for tr in t.xpath(".//tr"):
        row, c = [], 0
        cells = tr.xpath("./th|./td")
        ci = 0
        while ci < len(cells) or c in pend:
            if c in pend:
                left, txt = pend[c]
                row.append(txt)
                if left > 1:
                    pend[c] = (left - 1, txt)
                else:
                    del pend[c]
                c += 1
                continue
            cell = cells[ci]; ci += 1
            txt = " ".join(cell.text_content().split())
            cs = int(cell.get("colspan") or 1); rs = int(cell.get("rowspan") or 1)
            for k in range(cs):
                row.append(txt)
                if rs > 1:
                    pend[c] = (rs - 1, txt)
                c += 1
        g.append(row)
    return g


def parse(html):
    """Table -> {label: {sector: {eq, tot}}} (INR Cr). Header ne flatten kari ne 'Net Investment' na groups shodhe."""
    g = grid(html)
    first = next(i for i, r in enumerate(g) if r and re.fullmatch(r"1(\.0)?", r[0].strip()))
    n = max(len(r) for r in g)
    g = [r + [""] * (n - len(r)) for r in g]
    flat = []
    for c in range(n):
        parts = []
        for r in range(first):
            v = g[r][c].strip()
            if v and (not parts or parts[-1] != v):
                parts.append(v)
        flat.append(" | ".join(parts))
    cols = {}
    for c, h in enumerate(flat):
        hl = h.lower()
        if "net investment" not in hl or "usd" in hl:
            continue
        m = re.search(r"net investment[^|]*", h, re.I)
        label = (m.group(0).strip() if m else h)
        kind = "tot" if hl.rstrip().endswith("total") else "eq" if hl.rstrip().endswith("equity") and "mutual" not in hl else None
        if kind:
            cols.setdefault(label, {}).setdefault(kind, c)
    out = {}
    for i in range(first, len(g)):
        name = g[i][1].strip()
        if not name or name.lower() == "grand total" or not re.fullmatch(r"\d+(\.0)?", g[i][0].strip()):
            continue
        for label, cc in cols.items():
            out.setdefault(label, {})[name] = {k: num(g[i][c]) for k, c in cc.items()}
    return out, flat


def main():
    import requests
    today = dt.date.today()
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT))
        except Exception:
            old = {}
    have = {p["id"]: p for p in old.get("periods", [])}
    errs = []
    for d in period_ends(today):
        pid = d.strftime("%b%d%Y")
        if pid in have and have[pid].get("net"):
            continue
        url = BASE.format(pid)
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code != 200:
                errs.append(f"{pid}: HTTP {r.status_code}")
                continue
            net, flat = parse(r.text)
            if not net:
                errs.append(f"{pid}: net investment columns not found; headers={flat[:40]}")
                continue
            have[pid] = {"id": pid, "asof": d.isoformat(), "net": net}
            print("ok", pid, {k: len(v) for k, v in net.items()})
        except Exception as e:
            errs.append(f"{pid}: {type(e).__name__}: {str(e)[:200]}")
    periods = sorted(have.values(), key=lambda p: p["asof"], reverse=True)[:6]
    res = {"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "periods": periods}
    if errs:
        res["error"] = errs[:6]
        print("\n".join(errs))
    if periods or not os.path.exists(OUT):
        json.dump(res, open(OUT, "w"), separators=(",", ":"))
    print("saved fpiflow.json periods:", len(periods))


if __name__ == "__main__":
    main()
