import requests
import datetime

def get_global_data():
    url = "https://api.coingecko.com/api/v3/global"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()["data"]

def analyze(data):
    stable_dom = data["stablecoin_percentage"]
    btc_dom = data["market_cap_percentage"]["btc"]
    total_cap = data["total_market_cap"]["usd"]

    analysis = []

    if stable_dom > 10:
        analysis.append("🛡️ Mercado defensivo: alta dominancia de stablecoins")
    else:
        analysis.append("🟢 Mercado risk-on: baja dominancia de stablecoins")

    if btc_dom > 45:
        analysis.append("🔥 BTC dominante (fase liderazgo BTC)")
    else:
        analysis.append("⚠️ BTC débil vs altcoins")

    analysis.append(f"📊 Stable Dominance: {stable_dom:.2f}%")
    analysis.append(f"📊 BTC Dominance: {btc_dom:.2f}%")
    analysis.append(f"💰 Total Market Cap: ${total_cap:,.0f}")

    return analysis

def main():
    data = get_global_data()
    result = analyze(data)

    today = datetime.date.today().isoformat()

    print(f"\n📅 Análisis macro cripto diario — {today}\n")
    for line in result:
        print(line)

if __name__ == "__main__":
    main()
