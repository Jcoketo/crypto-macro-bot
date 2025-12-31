import requests
import os
from datetime import datetime

# =========================
# CONFIG
# =========================
SHEETBEST_URL = os.getenv("SHEETBEST_URL")            
TELEGRAM_BOT_TOKEN = os.getenv("TENDENCIAS_TOKEN")       
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

# =========================
# TELEGRAM
# =========================
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload, timeout=10)


# =========================
# HELPERS
# =========================
def safe_float(v):
    try:
        if v is None:
            return None

        if isinstance(v, str):
            v = v.strip()

            # elimina %
            v = v.replace("%", "")

            # formato europeo: 3.060.676.807.950,00
            if "," in v and "." in v:
                v = v.replace(".", "").replace(",", ".")

            # solo coma decimal: 57,42
            elif "," in v:
                v = v.replace(",", ".")

        return float(v)

    except Exception:
        return None


def signed(v):
    if v is None:
        return ""
    return f"+{round(v,2)}" if v > 0 else f"{round(v,2)}"


# =========================
# DATA FETCH
# =========================
def fetch_last_sheet_row():
    r = requests.get(SHEETBEST_URL, timeout=15)
    r.raise_for_status()
    rows = r.json()
    return rows[-1] if rows else None


def fetch_coingecko():
    r = requests.get(COINGECKO_GLOBAL, timeout=15)
    r.raise_for_status()
    return r.json()["data"]


# =========================
# MAIN LOGIC
# =========================
def run_macro_snapshot():

    last = fetch_last_sheet_row()
    cg = fetch_coingecko()

    # --- ACTUAL DATA ---
    market_cap = safe_float(cg["total_market_cap"]["usd"])
    dom_btc = safe_float(cg["market_cap_percentage"]["btc"])

    stable_keys = [
        "usdt", "tether",
        "usdc", "usd-coin",
        "dai", "busd", "frax", "tusd"
    ]

    dom_stable = 0.0
    for k in stable_keys:
        v = cg["market_cap_percentage"].get(k)
        if v:
            dom_stable += float(v)

    # --- PREVIOUS DATA ---
    prev_mcap = safe_float(last.get("total_market_cap"))
    prev_dom_btc = safe_float(last.get("dominancia_btc"))
    prev_dom_stable = safe_float(last.get("dominancia_stable"))

    # --- VARIATIONS ---
    var_mcap = ((market_cap - prev_mcap) / prev_mcap * 100) if prev_mcap else 0
    var_dom_btc = dom_btc - prev_dom_btc if prev_dom_btc else 0
    var_dom_stable = dom_stable - prev_dom_stable if prev_dom_stable else 0

    # =========================
    # MESSAGE BUILD
    # =========================
    lines = []
    lines.append("📊 <b>MACRO SNAPSHOT (4H)</b>")
    lines.append("")

    # --- MARKET CAP ---
    lines.append(f"<b>Market Cap en USD:</b> {int(market_cap)}")
    lines.append(f"Variación del Market Cap: {signed(var_mcap)}%")

    if var_mcap > 0:
        lines.append("🟢 Ingresando Dinero")
    lines.append("")

    # --- BTC DOM ---
    lines.append(f"<b>Dominación de BTC:</b> {round(dom_btc,2)}%")
    lines.append(f"Variación Dom. BTC: {signed(var_dom_btc)}%")

    if var_dom_btc > 0:
        lines.append("🟢 Ingresa dinero en BTC")
    lines.append("")

    # --- STABLE DOM ---
    if dom_stable > 9:
        dom_stable_txt = f"🚨 <span style='color:red'><b>{round(dom_stable,2)}%</b></span>"
    else:
        dom_stable_txt = f"{round(dom_stable,2)}%"

    lines.append(f"<b>Dominación de Stables:</b> {dom_stable_txt}")
    lines.append(f"Variación Cap. Stable: {signed(var_dom_stable)}%")

    if var_dom_stable > 0:
        lines.append("🟡 Ingresa Dinero en Stable Coin")
        if var_dom_stable > 1:
            lines.append("⚠️ Posible riesgo")
   
    # =========================
    # DEBUG – DATOS LEÍDOS
    # =========================
    lines.append("")
    lines.append("🧪 <b>DEBUG – DATOS LEÍDOS</b>")

    # --- Sheet ---
    lines.append("📄 <b>Desde Google Sheet:</b>")
    lines.append(f"Prev Market Cap: {prev_mcap}")
    lines.append(f"Prev Dom BTC: {prev_dom_btc}")
    lines.append(f"Prev Dom Stable: {prev_dom_stable}")

    # --- CoinGecko ---
    lines.append("")
    lines.append("🌐 <b>Desde CoinGecko:</b>")
    lines.append(f"Market Cap actual: {market_cap}")
    lines.append(f"Dom BTC actual: {dom_btc}")
    lines.append(f"Dom Stable actual: {round(dom_stable,2)}")

    # --- Variaciones calculadas ---
    lines.append("")
    lines.append("📐 <b>Variaciones calculadas:</b>")
    lines.append(f"Var Market Cap: {var_mcap}")
    lines.append(f"Var Dom BTC: {var_dom_btc}")
    lines.append(f"Var Dom Stable: {var_dom_stable}")

    ################ FIN DE PRUEBA
    ##############################
    
    
    message = "\n".join(lines)

    send_telegram(message)


# =========================
# ENTRYPOINT
# =========================
if __name__ == "__main__":
    run_macro_snapshot()
