"""Scope zones: options.json na 4 aankda + tamara Low/High/POC -> Buy / Sell / No-trade zones (text).
Aa file Telegram webhook (app.py) ane GitHub job (job/scope_zones.py) bane vapare che."""
import re

ALIAS = {"NIFTY": "Nifty 50", "NIFTY50": "Nifty 50", "BANKNIFTY": "Nifty Bank", "BANK": "Nifty Bank",
         "SENSEX": "Sensex"}
RANGE = 12   # ATM +- 12 strikes (site jevu j)


def f0(n): return f"{n:,.0f}"
def f1(n): return f"{n:,.1f}"


def analyse(ix, expiry=None):
    exp = expiry or ix["expiries"][0]
    ch = ix["chains"][exp]; rows = ch["rows"]; spot = ix["spot"]
    a = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot))
    sh = rows[max(0, a - RANGE): a + RANGE + 1]
    oi = lambda r, t: (r.get(t) or {}).get("oi", 0)
    ceT = sum(oi(r, "ce") for r in sh); peT = sum(oi(r, "pe") for r in sh)
    mc = max(sh, key=lambda r: oi(r, "ce")); mp = max(sh, key=lambda r: oi(r, "pe"))
    best, mpain = None, None
    for K in rows:
        p = sum(oi(r, "ce") * max(0, K["strike"] - r["strike"]) + oi(r, "pe") * max(0, r["strike"] - K["strike"]) for r in rows)
        if best is None or p < best:
            best, mpain = p, K["strike"]
    return {"spot": spot, "expiry": exp, "days": ch.get("days"), "call": mc["strike"], "put": mp["strike"],
            "pain": mpain, "pcr": (peT / ceT) if ceT else None}


def levels(low, high):
    r = high - low
    return {"bd": low, "br": low + 0.25 * r, "sr": low + 0.75 * r, "bo": high, "t1": low + 1.272 * r}


def build(name, A, L, poc):
    spot, pcr = A["spot"], A["pcr"]
    lines = [f"{name} | Spot {f1(spot)} | Expiry {A['expiry']}",
             f"Call OI strike: {f0(A['call'])} | Put OI strike: {f0(A['put'])} | Max pain: {f0(A['pain'])} | PCR: {pcr:.2f}" if pcr else "",
             "", "Levels: Break down {} | Buy rev {} | Sell rev {} | Break out {} | Target {}".format(
                 f1(L["bd"]), f1(L["br"]), f1(L["sr"]), f1(L["bo"]), f1(L["t1"]))]
    if poc: lines.append(f"Point (POC): {f1(poc)}")
    lines.append("")
    # option chain su kahe che
    near = min(abs(A["call"] - spot), abs(A["put"] - spot))
    notes = []
    if near > spot * 0.015:
        notes.append(f"Call/Put wall bhav thi dur ({f0(A['call']-spot)} / {f0(spot-A['put'])} pts) - rukavat nathi.")
    else:
        notes.append("Wall bhav ni najik che - tya rukavat/support mali shake.")
    d = A["pain"] - spot
    if abs(d) > 40:
        notes.append(f"Max pain {'upar' if d>0 else 'niche'} {f0(abs(d))} pts - expiry taraf {'upar' if d>0 else 'niche'} kheenchvani halki jhok.")
    if pcr:
        notes.append("PCR " + (">1.2: put OI vadhu (supportive)" if pcr > 1.2 else "<0.8: call OI vadhu (upar ma dabaan)" if pcr < 0.8
                               else "0.8-1.2: lagbhag santulit (vadhare 0.9 thi niche = thodu sambhalvu)"))
    lines += ["Option chain: "] + ["- " + n for n in notes] + [""]
    up_bias = d > 40
    if spot > L["bo"]:
        zone = "Bhav Break out upar che."
    elif spot < L["bd"]:
        zone = "Bhav Break down niche che."
    elif L["sr"] <= spot <= L["bo"]:
        zone = "Bhav Sell rev ane Break out vachche che."
    elif spot < L["br"]:
        zone = "Bhav Break down ane Buy rev vachche che."
    else:
        zone = "Bhav Buy rev ane Sell rev vachche che."
    lines.append(zone)
    lines += [
        f"BUY: {f1(L['bo'])} upar candle close tike -> Target {f1(L['t1'])}" + (f", pachhi {f0(A['pain'])} (max pain)" if up_bias else "") + f". SL {f1(L['sr'])} niche.",
        f"SELL: {f1(L['sr'])} niche tuti ne tike -> Target {f1(L['br'])}, pachhi {f1(L['bd'])}. SL {f1(L['bo'])} upar." + (" Max pain upar che, etle sell ma satark / nano size." if up_bias else ""),
        f"NO TRADE: {f1(L['sr'])} thi {f1(L['bo'])} vachche.",
        "", "Aa aankda ni mari analysis che, salah nathi. Navo option-chain reading aave tyare pharithi chalavo.",
    ]
    return "\n".join(x for x in lines if x is not None)


def parse_msg(text):
    """'NIFTY 22406 22545 22480' -> (index, low, high, poc) athva None"""
    t = (text or "").upper().replace(",", " ")
    mm = re.match(r"\s*/?([A-Z0-9]+)\s+([\d.]+)\s+([\d.]+)(?:\s+([\d.]+))?", t)
    if not mm or mm.group(1) not in ALIAS:
        return None
    lo, hi = sorted([float(mm.group(2)), float(mm.group(3))])
    if hi <= lo:
        return None
    return ALIAS[mm.group(1)], lo, hi, float(mm.group(4)) if mm.group(4) else None


HELP = ("Aa rite moklo (naam pachhi Low High POC):\n"
        "NIFTY 22406 22545 22480\nBANKNIFTY 51000 51600 51300\nSENSEX 74000 74600 74300\n"
        "POC optional che.")


def reply_for(text, opt):
    got = parse_msg(text)
    if not got:
        return HELP
    name, low, high, poc = got
    ix = (opt or {}).get("indices", {}).get(name)
    if not ix or not ix.get("expiries"):
        return f"Option data ma {name} nathi. Options job chalavo (GitHub Actions)."
    A = analyse(ix)
    return build(name, A, levels(low, high), poc) + f"\n(Option data: {(opt or {}).get('updated')})"


# ---------------------------------------------------------------- auto messages (cron)
def zone_of(spot, L):
    if spot > L["bo"]:
        return "Bhav Break out upar che."
    if spot < L["bd"]:
        return "Bhav Break down niche che."
    if L["sr"] <= spot <= L["bo"]:
        return "Bhav Sell rev ane Break out vachche che (no-trade zone)."
    if spot < L["br"]:
        return "Bhav Break down ane Buy rev vachche che."
    return "Bhav Buy rev ane Sell rev vachche che."


def snapshot(A):
    return {"call": A["call"], "put": A["put"], "pain": A["pain"],
            "pcr": round(A["pcr"], 2) if A["pcr"] else None, "expiry": A["expiry"]}


def _band(p):
    return None if p is None else (0 if p < 0.8 else 2 if p > 1.2 else 1)


def changes(prev, A):
    out = []
    if prev.get("expiry") and prev["expiry"] != A["expiry"]:
        out.append(f"Expiry badlayi: {prev['expiry']} -> {A['expiry']}")
    if prev.get("call") != A["call"]:
        out.append(f"Call wall (resistance): {f0(prev['call'])} -> {f0(A['call'])}")
    if prev.get("put") != A["put"]:
        out.append(f"Put wall (support): {f0(prev['put'])} -> {f0(A['put'])}")
    if prev.get("pain") is not None and abs(A["pain"] - prev["pain"]) >= 0.0025 * A["spot"]:
        out.append(f"Max pain: {f0(prev['pain'])} -> {f0(A['pain'])}")
    if _band(prev.get("pcr")) != _band(A["pcr"]) and A["pcr"]:
        out.append(f"PCR: {prev.get('pcr')} -> {A['pcr']:.2f}")
    return out


def register_levels(state, text, opt, today):
    """Telegram msg ma thi levels jaanya pachhi aajna din mate yaad rakho (reply to alredy gayo che)"""
    got = parse_msg(text)
    if not got:
        return
    name, low, high, poc = got
    ix = (opt or {}).get("indices", {}).get(name)
    if not ix or not ix.get("expiries"):
        return
    state[name] = {"low": low, "high": high, "poc": poc, "date": today, "sent": True, "snap": snapshot(analyse(ix))}


def cron_step(state, opt, today):
    """Aajna levels vala index mate: pehli vaar full reading, pachhi fakt wall/max pain/PCR badlay tyare alert."""
    msgs = []
    tag = f"\n(Option data: {(opt or {}).get('updated')})"
    for name, st in list(state.items()):
        if name.startswith("_") or st.get("date") != today:
            continue
        ix = (opt or {}).get("indices", {}).get(name)
        if not ix or not ix.get("expiries"):
            continue
        A, L = analyse(ix), levels(st["low"], st["high"])
        if not st.get("sent") or not st.get("snap"):
            msgs.append(build(name, A, L, st.get("poc")) + tag)
            st["sent"], st["snap"] = True, snapshot(A)
            continue
        ch = changes(st["snap"], A)
        if ch:
            msgs.append(f"{name} | option chain badlayu | Spot {f1(A['spot'])}\n" + "\n".join("- " + c for c in ch)
                        + f"\n\n{zone_of(A['spot'], L)}\nPuro reading mate bot ne pharithi levels moklo." + tag)
            st["snap"] = snapshot(A)
    return msgs, state
