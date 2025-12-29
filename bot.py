import requests
import datetime

# ================= CONFIG =================
SHEETBEST_URL = "https://api.sheetbest.com/sheets/e1fcb6ce-c588-40ea-891f-1cb9f91d1ffb"
STABLES = ["usdt", "usdc", "dai", "busd", "tusd", "frax"]
# =========================================

def get_global_data():
    r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
    r.raise_for_status()
    return r.json()["data"]

def calculate_stable_dominance(market_caps):
    return sum(market_caps.get(coin, 0) for coin in STABLES)

def get_last_row():
    r = requests.get(SHEETBEST_URL, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data[-1] if data else None

def analyze_and_send(data):
    market_caps = data["market_cap_percentage"]

    stable_dom = calculate_stable_dominance(market_caps)
    btc_dom = market_caps.get("btc", 0)
    total_cap = data["total_market_cap"]["usd"]

    last = get_last_row()
    change_pct = None

    if last and last.get("stable_dom"):
        prev = float(last["stable_dom"])
        change_pct = ((stable_dom - prev) / prev) * 100 if prev > 0 else 0

    # Régimen macro
    if stable_dom > 12:
        regime = "DEFENSIVO"
    elif stable_dom > 8:
        regime = "NEUTRO"
    else:
        regime = "RISK-ON"

    payload = {
        "date": datetime.date.today().isoformat(),
        "stable_dom": round(stable_dom, 2),
        "stable_dom_change_pct": round(change_pct, 2) if change_pct is not None else "",
        "btc_dom": round(btc_dom, 2),
        "total_market_cap": int(total_cap),
        "regime": regime
    }

    requests.post(SHEETBEST_URL, json=payload, timeout=10)

    return payload

def main():
    data = get_global_data()
    result = analyze_and_send(data)

    print("\n📅 Análisis macro cripto diario\n")
    for k, v in result.items():
        print(f"{k}: {v}")

if __name__ == "__main__":
    main()
