"""
Scope Option Volume Spike job  (GitHub Actions ma market hours ma har ~10 min)

NIFTY / BANKNIFTY / SENSEX na nazdik na expiry na option strikes (ATM +- N + round levels: Nifty 500, BN/Sensex 1000)
nu volume + OI vanche che. Pachhi navi 10 min ma volume bija strikes karta ghanu vadhe to alert text banave che.
Output: optvol.json (alerts) + optvol_state.json (pachhli snapshot). Telegram moklvanu kaam website (tg_cron) kare che.

FAKAT MAHITI: volume spike = moti activity. Direction nathi (buy ke write, hedge pan hoi shake). Salah nathi.

Secrets: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD, ANGEL_TOTP_SECRET, TG_BOT_TOKEN, TG_CHAT_ID (alert tarat Telegram ma)
Env (optional): OV_MULT (6), OV_ROUND_MULT (5), OV_MIN_LAKH (100), OV_TOP (3), OV_WINDOW (10), OV_COOLDOWN_MIN (45), OV_FORCE (1 = time check chhodo)
"""
import os, sys, json, time, html, statistics, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
OUT = os.path.join(ROOT, "optvol.json")
STATE = os.path.join(ROOT, "optvol_state.json")

MULT = float(os.environ.get("OV_MULT", "6"))              # spike = median na kitla guna
RMULT = float(os.environ.get("OV_ROUND_MULT", "5"))      # round level par thodu halku
MIN_LAKH = float(os.environ.get("OV_MIN_LAKH", "100"))    # navi 10 min no minimum turnover (Rs lakh)
WINDOW = int(os.environ.get("OV_WINDOW", "10"))           # ATM +- N strikes
COOLDOWN = int(os.environ.get("OV_COOLDOWN_MIN", "45"))
TOP = int(os.environ.get("OV_TOP", "3"))                  # ek run ma fakat top N (turnover pramane) ek j message ma
KEEP = 30
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# name, option exchange, spot (exchange, token), strike step, round level
CFG = [
    {"name": "NIFTY", "exch": "NFO", "spot": ("NSE", "99926000"), "step": 50, "round": 500},
    {"name": "BANKNIFTY", "exch": "NFO", "spot": ("NSE", "99926009"), "step": 100, "round": 1000},
    {"name": "SENSEX", "exch": "BFO", "spot": ("BSE", "99919000"), "step": 100, "round": 1000},
]


def num(x):
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def in_market(now):
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return 9 * 60 + 20 <= m <= 15 * 60 + 30


def parse_expiry(s):
    try:
        return dt.datetime.strptime(str(s).title(), "%d%b%Y").date()
    except Exception:
        return None


def pick_options(master, cfg, today):
    """nazdik na expiry na badha strikes -> {strike: {"CE": token, "PE": token}}, expiry"""
    rows = [r for r in master if r.get("exch_seg") == cfg["exch"] and r.get("instrumenttype") == "OPTIDX"
            and str(r.get("name")) == cfg["name"]]
    exps = sorted({e for e in (parse_expiry(r.get("expiry")) for r in rows) if e and e >= today})
    if not exps:
        return {}, None
    ex = exps[0]
    out = {}
    for r in rows:
        if parse_expiry(r.get("expiry")) != ex:
            continue
        sym = str(r.get("symbol", ""))
        side = "CE" if sym.endswith("CE") else "PE" if sym.endswith("PE") else None
        k = num(r.get("strike"))
        if not side or not k:
            continue
        out.setdefault(round(k / 100.0, 2), {})[side] = {"token": str(r["token"]), "symbol": sym}
    return out, ex


def watch_strikes(chain, spot, cfg):
    step, R = cfg["step"], cfg["round"]
    atm = round(spot / step) * step
    ks = {k for k in chain if abs(k - atm) <= WINDOW * step}
    for k in chain:                                            # round levels (+-3R) hamesha jovo
        if k % R == 0 and abs(k - spot) <= 3 * R:
            ks.add(k)
    return sorted(ks), atm


def quote_batch(quote_once, api, exch, tokens):
    out = {}
    for i in range(0, len(tokens), 50):
        out.update(quote_once(api, exch, tokens[i:i + 50]))
        time.sleep(1.05)
    return out


def oi_tag(doi, pct):
    """Fakat varnan (salah nathi): OI ane bhav na pharak thi shu thayu hovanu lage."""
    if doi is None or pct is None:
        return "OI/bhav no pharak malyo nathi"
    if doi >= 3 and pct > 0.5:
        return "navi position, kharidi jevu"
    if doi >= 3 and pct < -0.5:
        return "navi position, writing jevu"
    if doi <= -3 and pct > 0.5:
        return "short covering jevu"
    if doi <= -3 and pct < -0.5:
        return "long band thai rahya"
    return "OI same, volume j vadhyu"


def lakh(x):
    return f"{x / 1e5:,.1f} lakh"


def analyse(cfg, spot, chain, strikes, atm, quotes, prev, now_iso, same_day, alerted, now_ts):
    """quotes: {token: quote}; prev: {token: [vol, oi, ltp]} (aaj no pachhlo snapshot). -> (alerts, new_snap)"""
    R = cfg["round"]
    rows, snap = [], {}
    for k in strikes:
        for side in ("CE", "PE"):
            t = (chain.get(k) or {}).get(side)
            if not t:
                continue
            q = quotes.get(t["token"]) or {}
            ltp, vol, oi = num(q.get("ltp")), num(q.get("tradeVolume")), num(q.get("opnInterest"))
            if ltp is None or vol is None:
                continue
            p = prev.get(t["token"]) if same_day else None
            dvol = (vol - p[0]) if p and vol >= p[0] else None
            doi = ((oi - p[1]) / p[1] * 100.0) if p and oi is not None and p[1] else None
            pct = ((ltp / p[2] - 1) * 100.0) if p and p[2] else None
            snap[t["token"]] = [vol, oi or 0, ltp]
            rows.append({"k": k, "side": side, "tok": t["token"], "sym": t["symbol"], "ltp": ltp, "vol": vol,
                         "oi": oi, "dvol": dvol, "doi": doi, "pct": pct, "round": (k % R == 0)})
    alerts = []
    for side in ("CE", "PE"):
        grp = [r for r in rows if r["side"] == side]
        base = [r["dvol"] for r in grp if r["dvol"] and r["dvol"] > 0]
        if len(base) < 5:
            continue
        med = statistics.median(base)
        if med <= 0:
            continue
        for r in grp:
            d = r["dvol"]
            if not d or d <= 0:
                continue
            ratio = d / med
            turnover = d * r["ltp"]
            need = RMULT if r["round"] else MULT
            if ratio < need or turnover < MIN_LAKH * 1e5:
                continue
            key = f"{cfg['name']}|{r['k']:g}|{side}"
            if now_ts - alerted.get(key, 0) < COOLDOWN * 60:
                continue
            alerted[key] = now_ts
            near = round(spot / R) * R
            txt = (f"OPTION VOLUME SPIKE | {cfg['name']} {r['k']:g} {side}" + ("  (round level)" if r["round"] else "") + "\n"
                   f"Navi ~10 min ma volume {ratio:.1f}x (bija {side} strikes na median karta), turnover ~Rs {lakh(turnover)}\n"
                   f"LTP {r['ltp']:,.2f}" + (f" ({r['pct']:+.1f}%)" if r["pct"] is not None else "")
                   + (f" | OI {r['doi']:+.1f}%" if r["doi"] is not None else "")
                   + f" | Spot {spot:,.1f} (ATM {atm:g}, nazdik round level {near:g})\n"
                   "Mahiti: moti activity. Direction nathi (buy ke write, hedge pan hoi shake). Salah nathi, nirnay tamaro.")
            alerts.append({"id": f"{key}|{now_iso}", "ts": now_iso, "index": cfg["name"], "strike": r["k"], "side": side,
                           "ratio": round(ratio, 1), "text": txt, "turn": turnover, "dist": r["k"] - spot, "round": r["round"],
                           "ltp": r["ltp"], "pct": r["pct"], "doi": r["doi"], "tag": oi_tag(r["doi"], r["pct"])})
    return alerts, snap


def send_tg(text):
    """Spike male tyare j Telegram ma moklo (GitHub Actions ne internet khulo che). True = gayu."""
    tok, chat = os.environ.get("TG_BOT_TOKEN", "").strip(), os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not chat:
        return False
    try:
        import requests
    except Exception:
        return False
    body, pre = [], None
    for k, ln in enumerate((text or "").split("\n")):
        if ln.strip() == "```":
            if pre is None:
                pre = []
            else:
                body.append("<pre>" + "\n".join(pre) + "</pre>")
                pre = None
        elif pre is not None:
            pre.append(html.escape(ln))
        else:
            body.append("<b>" + html.escape(ln) + "</b>" if k == 0 else html.escape(ln))
    if pre is not None:
        body.append("<pre>" + "\n".join(pre) + "</pre>")
    body = "\n".join(body)
    plain = (text or "").replace("```\n", "").replace("\n```", "")
    for i in range(4):
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              data={"chat_id": chat, "text": body, "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=20)
            if r.status_code == 200:
                return True
            if r.status_code == 400:      # HTML vandho: saada text
                r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": plain}, timeout=20)
                if r.status_code == 200:
                    return True
            print("tg send", r.status_code, r.text[:120], flush=True)
        except Exception as e:
            print("tg send exc", str(e).replace(tok, "***")[:120], flush=True)
        time.sleep(2 * (i + 1))
    return False


SHORT = {"NIFTY": "N", "BANKNIFTY": "BN", "SENSEX": "SX"}


def digest(top, total):
    n = len(top)
    head = "OPTION VOLUME SPIKE" + (f" | top {n} of {total}" if total > n else "")
    rows = [f"{'#':<1} {'Strike':<13} {'x':>4} {'Rs cr':>6} {'Spot':>6} {'OI%':>5}"]
    for i, a in enumerate(top, 1):
        name = f"{SHORT.get(a['index'], a['index'])} {a['strike']:g} {a['side']}" + ("*" if a.get("round") else "")
        oi = "-" if a.get("doi") is None else f"{a['doi']:+.0f}"
        rows.append(f"{i:<1} {name:<13} {a['ratio']:>4.1f} {a['turn'] / 1e7:>6.1f} {a['dist']:>+6.0f} {oi:>5}")
    out = [head, "```"] + rows + ["```"]
    for i, a in enumerate(top, 1):
        pc = f", LTP {a['ltp']:,.1f} ({a['pct']:+.1f}%)" if a.get("pct") is not None else f", LTP {a['ltp']:,.1f}"
        out.append(f"{i}) {a['tag']}{pc}")
    out.append("N=Nifty, BN=BankNifty, SX=Sensex, *=round level. x = volume kitla guna, Spot = spot thi pts.")
    out.append("Mahiti: moti activity. Direction nathi (hedge pan hoi shake). Salah nathi, nirnay tamaro.")
    return "\n".join(out)


def main():
    now = dt.datetime.now(IST)
    if os.environ.get("OV_FORCE") != "1" and not in_market(now):
        print("market hours bahar - skip", now.isoformat(timespec="minutes"))
        return
    import scope_daily
    from scope_live import quote_once
    api = scope_daily.login()
    master = scope_daily.get_master()
    today = now.date()
    st = load_json(STATE, {})
    same_day = st.get("date") == today.isoformat()
    prev_all = st.get("snap", {}) if same_day else {}
    alerted = st.get("alerted", {}) if same_day else {}
    out = load_json(OUT, {"alerts": []})
    now_iso = now.isoformat(timespec="seconds")
    now_ts = time.time()
    new_alerts, new_snap, status = [], {}, {}
    for cfg in CFG:
        try:
            chain, ex = pick_options(master, cfg, today)
            sq = quote_once(api, cfg["spot"][0], [cfg["spot"][1]]).get(cfg["spot"][1]) or {}
            spot = num(sq.get("ltp"))
            if not chain or not spot:
                status[cfg["name"]] = "no chain/spot"
                continue
            ks, atm = watch_strikes(chain, spot, cfg)
            toks = [t["token"] for k in ks for t in (chain[k].get("CE"), chain[k].get("PE")) if t]
            quotes = quote_batch(quote_once, api, cfg["exch"], toks)
            al, snap = analyse(cfg, spot, chain, ks, atm, quotes, prev_all, now_iso, same_day, alerted, now_ts)
            new_alerts += al
            new_snap.update(snap)
            status[cfg["name"]] = f"expiry {ex}, {len(ks)} strikes, {len(quotes)} quotes, {len(al)} alert"
        except Exception as e:
            status[cfg["name"]] = "error " + str(e)[:100]
        print(cfg["name"], status[cfg["name"]], flush=True)
    for k, v in prev_all.items():                      # je strike aa vakhate nathi aavya, tenu juno snapshot rakho
        new_snap.setdefault(k, v)
    if new_alerts:                                     # ek run na badha spike -> ek j message (top TOP, turnover pramane)
        ranked = sorted(new_alerts, key=lambda a: -a.get("turn", 0))
        ok = send_tg(digest(ranked[:TOP], len(ranked)))
        for a in new_alerts:                           # site pachhi pharvar na moke
            a["sent"] = ok
            print("alert", a["index"], a["strike"], a["side"], "telegram:", ok, flush=True)
    alerts = (out.get("alerts") or []) + new_alerts
    save_json(OUT, {"updated": now_iso, "alerts": alerts[-KEEP:], "status": status})
    save_json(STATE, {"date": today.isoformat(), "snap": new_snap, "alerted": alerted, "ts": now_iso})
    print("done", len(new_alerts), "new alerts", flush=True)


if __name__ == "__main__":
    main()
