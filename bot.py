import requests
from datetime import datetime
import os

SHEETBEST_URL = os.getenv("SHEETBEST_URL")
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

STABLES = [
    "tether",
    "usd-coin",
    "dai",
    "true-usd",
    "frax"
]

# -----------------------------
# Obtención de datos
# -----------------------------

def obtener_datos_globales():
    r = requests.get(COINGECKO_GLOBAL, timeout=15)
    r.raise_for_status()
    return r.json()["data"]

def obtener_registros_previos():
    r = requests.get(SHEETBEST_URL, timeout=15)
    r.raise_for_status()
    return r.json()

# -----------------------------
# Lógica macro
# -----------------------------

def calcular_score(dom_stable, variacion, aceleracion, dom_btc):
    score = 50  # punto neutral

    # 1️⃣ Dominancia stable (40%)
    if dom_stable > 12:
        score += 20
    elif dom_stable < 9:
        score -= 20

    # 2️⃣ Variación 24h (30%)
    if variacion >= 5:
        score += 15
    elif variacion <= -5:
        score -= 15
    elif variacion >= 3:
        score += 8
    elif variacion <= -3:
        score -= 8

    # 3️⃣ Aceleración (20%)
    if aceleracion >= 1.5:
        score += 10
    elif aceleracion <= -1.5:
        score -= 10

    # 4️⃣ Dominancia BTC (10%)
    if dom_btc > 46:
        score += 5
    elif dom_btc < 42:
        score -= 5

    return max(0, min(100, round(score)))

def obtener_regimen(score):
    if score >= 81:
        return "RISK-OFF / STRESS"
    if score >= 61:
        return "DEFENSIVO"
    if score >= 41:
        return "NEUTRO"
    if score >= 21:
        return "RISK-ON"
    return "RISK-ON FUERTE"

def interpretar(score, variacion, aceleracion):
    if score >= 81:
        return (
            "Flujo defensivo extremo hacia stablecoins. "
            "Mercado en stress, preservación de capital prioritaria."
        )

    if score >= 61:
        return (
            "Aumento relevante de stablecoins con sesgo defensivo. "
            "Riesgo elevado de correcciones."
        )

    if score >= 41:
        return (
            "Mercado equilibrado, sin dominancia clara de flujos. "
            "Fase de espera y consolidación."
        )

    if score >= 21:
        return (
            "Salida gradual de stablecoins. "
            "Mejora en el apetito por riesgo, entradas selectivas posibles."
        )

    return (
        "Salida fuerte y acelerada de stablecoins. "
        "Entorno claramente risk-on, favorable para activos de riesgo."
    )

# -----------------------------
# Main
# -----------------------------

def main():
    fecha = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    datos_globales = obtener_datos_globales()
    registros = obtener_registros_previos()

    capitalizacion_total = datos_globales["total_market_cap"]["usd"]
    porcentajes = datos_globales["market_cap_percentage"]

    dominancia_bitcoin = porcentajes.get("btc", 0)

    dominancia_stable = sum(
        porcentajes.get(stable, 0) for stable in STABLES
    )

    # Variación y aceleración
    if len(registros) >= 1:
        prev_dom = float(registros[-1]["dominancia_stablecoins"])
        variacion_pct = ((dominancia_stable - prev_dom) / prev_dom) * 100
    else:
        variacion_pct = 0.0

    if len(registros) >= 2:
        prev_var = float(registros[-1]["variacion_stablecoins_pct"])
        aceleracion = variacion_pct - prev_var
    else:
        aceleracion = 0.0

    score_macro = calcular_score(
        dominancia_stable,
        variacion_pct,
        aceleracion,
        dominancia_bitcoin
    )

    regimen = obtener_regimen(score_macro)
    interpretacion = interpretar(score_macro, variacion_pct, aceleracion)

    payload = {
        "fecha": fecha,
        "dominancia_stablecoins": round(dominancia_stable, 2),
        "variacion_stablecoins_pct": round(variacion_pct, 2),
        "aceleracion_stablecoins": round(aceleracion, 2),
        "dominancia_bitcoin": round(dominancia_bitcoin, 2),
        "capitalizacion_total": round(capitalizacion_total, 0),
        "score_macro": score_macro,
        "regimen": regimen,
        "interpretacion": interpretacion
    }

    r = requests.post(SHEETBEST_URL, json=payload, timeout=15)
    r.raise_for_status()

    print("Análisis macro diario cargado correctamente")

if __name__ == "__main__":
    main()
