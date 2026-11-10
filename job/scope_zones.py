"""
Scope Zones -> Telegram

Tame (workflow ma) Low / High / POC aapo, aa job options.json mathi
Call OI strike, Put OI strike, Max pain, PCR jate vanche, Buy / Sell / No-trade zones banave
ane Telegram par moklе.

Env (workflow input / secrets):
  TG_BOT_TOKEN, TG_CHAT_ID           (repo secrets)
  INDEX   : "Nifty 50" | "Nifty Bank" | "Sensex"   (default Nifty 50)
  LOW, HIGH, POC : tamara levels (POC optional)
  Jo LOW/HIGH khali hoy to Telegram ma chhello msg vanche: 'NIFTY 22406 22545 22480'
  (naam pachhi Low High POC)
"""
import os, re, json, sys, requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT = os.environ.get("TG_CHAT_ID", "")
import sys
sys.path.insert(0, ROOT)
from zones import ALIAS, analyse, levels, build, parse_msg


def from_telegram():
    r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", timeout=30).json()
    for u in reversed(r.get("result", [])):
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(CHAT):
            continue
        got = parse_msg(m.get("text"))
        if got:
            return got
    return None


def send(text):
    if not (TOKEN and CHAT):
        print(text); print("\n(TG_BOT_TOKEN / TG_CHAT_ID nathi - fakt print karyu)"); return
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT, "text": text}, timeout=30)
    print("telegram:", r.status_code, r.text[:200])


def main():
    name = os.environ.get("INDEX") or "Nifty 50"
    low, high, poc = (os.environ.get(k, "").strip() for k in ("LOW", "HIGH", "POC"))
    if low and high:
        low, high = sorted([float(low), float(high)]); poc = float(poc) if poc else None
    else:
        got = from_telegram() if TOKEN and CHAT else None
        if not got:
            send("Low/High nathi malya. Workflow ma Low, High aapo athva bot ne moklo: NIFTY 22406 22545 22480"); sys.exit(0)
        name, low, high, poc = got
    opt = json.load(open(os.path.join(ROOT, "options.json")))
    ix = opt["indices"].get(name)
    if not ix:
        send(f"options.json ma {name} nathi."); sys.exit(0)
    A = analyse(ix)
    send(build(name, A, levels(low, high, poc), poc, W=walls(ix)) + f"\n(Option data: {opt.get('updated')})")


if __name__ == "__main__":
    main()
