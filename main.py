#!/usr/bin/env python3
# Bot macro cripto v2.2-macro
# Diseñado para correr 1 vez por día (00:00 UTC) vía GitHub Actions

import requests
from datetime import datetime, timezone
import os
import math

# =======================
# CONFIGURACIÓN
# =======================

SHEETBEST_URL = os.getenv("SHEETBEST_URL")

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

STABLE_ALIASES = [
    "usdt", "tether", "usd-coin", "usdc", "dai", "busd",
    "binance-usd", "tusd", "true-usd", "frax"
]

# Umbrales macro
TH_DEFENSIVO = 30
TH_TRANS_BAJISTA = 45
TH_NEUTRO = 55
TH_TRANS_ALCISTA = 70

PERSIST_MIN = 3
HIST_LIMIT = 60

MODEL_VERSION = "v2.2-macro"

# =======================
# HELPERS
# =======================

def parse_float(x):
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ".").replace("%", ""))
    except:
        return None

def zscore(actual, historico, window=14):
    vals = [v for v in historico if v is not None]
    if len(vals) < window:
        return 0.0
    sub = vals[-window:]
    mean = sum(sub) / len(sub)
    var = sum((x - mean)**2 for x in sub) / len(sub)
    std = math.sqrt(var) if var > 0 else 0
    if std == 0:
        return 0.0
    return (actual - mean) / std

def sma(values, n):
    vals = [v for v in values if v is not None]
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n

# =======================
# COINGECKO
# =======================

def obtener_datos_globales():
    r = requests.get(COINGECKO_GLOBAL, timeout=15)
    r.raise_for_status()
    return r.json()["data"]

def calcular_dominancia_stable(market_pct):
    total = 0.0
    for a in STABLE_ALIASES:
        if a in market_pct:
            total += float(market_pct[a])
    return total

# =======================
# HISTÓRICO
# =======================

def leer_historico():
    if not SHEETBEST_URL:
        return []
    r = requests.get(SHEETBEST_URL, timeout=15)
    r.raise_for_status()
    data = r.json()
    rows = data[-HIST_LIMIT:]
    hist = []
    for r in rows:
        hist.append({
            "dominancia_stable": parse_float(r.get("dominancia_stable")),
            "variacion_24h": parse_float(r.get("variacion_24h")),
            "aceleracion": parse_float(r.get("aceleracion")),
            "score_diario": parse_float(r.get("score_diario")),
            "regimen_macro": r.get("regimen_macro")
        })
    return hist

# =======================
# SCORE BASE
# =======================

def calcular_score_base(dom_stable, dom_btc):
    score = 50
    if dom_stable > 12:
        score += 20
    elif dom_stable < 9:
        score -= 20

    if dom_btc > 46:
        score += 5
    elif dom_btc < 42:
        score -= 5

    return max(0, min(100, score))

# =======================
# PRESIÓN DEFENSIVA (ANTICIPACIÓN)
# =======================

def calcular_presion_defensiva(z_var, z_acc, pendiente_7d, divergence, regimen):
    presion = 50

    # Flujo estable anormal
    if z_var > 1.5:
        presion += 30
    elif z_var > 0.8:
        presion += 20
    elif z_var < -1.5:
        presion -= 15
    elif z_var < -0.8:
        presion -= 5

    # Aceleración
    if z_acc > 1.2:
        presion += 15
    elif z_acc < -1.2:
        presion -= 10

    # Tendencia estructural
    if pendiente_7d > 0:
        presion += 10
    elif pendiente_7d < 0:
        presion -= 5

    # Divergencia precio/flujo
    if divergence:
        presion += 15

    # Filtro anti-rebote (clave 2026)
    if regimen in ["DEFENSIVO", "TRANSICION BAJISTA"]:
        if z_var < 0 and z_acc < 0:
            presion += 10

    return max(0, min(100, int(round(presion))))

# =======================
# MAIN
# =======================

def main():
    cg = obtener_datos_globales()

    total_mcap = parse_float(cg["total_market_cap"]["usd"])
    market_pct = cg["market_cap_percentage"]

    dom_btc = parse_float(market_pct.get("btc"))
    dom_stable = calcular_dominancia_stable(market_pct)

    historico = leer_historico()

    # Variación 24h
    prev_dom = historico[-1]["dominancia_stable"] if historico else None
    variacion = ((dom_stable - prev_dom) / prev_dom * 100) if prev_dom else 0.0

    # Aceleración
    prev_var = historico[-1]["variacion_24h"] if len(historico) > 1 else 0.0
    aceleracion = variacion - (prev_var or 0)

    # Tendencias
    dom_series = [r["dominancia_stable"] for r in historico if r["dominancia_stable"]]
    dom_series.append(dom_stable)

    sma7 = sma(dom_series, 7)
    sma21 = sma(dom_series, 21)
    pendiente_7d = dom_stable - dom_series[-7] if len(dom_series) >= 7 else 0

    # Normalización
    z_var = zscore(variacion, [r["variacion_24h"] for r in historico])
    z_acc = zscore(aceleracion, [r["aceleracion"] for r in historico])

    # Score base
    score_base = calcular_score_base(dom_stable, dom_btc)

    # Persistencia
    scores = [r["score_diario"] for r in historico if r["score_diario"] is not None]
    scores.append(score_base)
    last5 = scores[-5:]

    persist_baj = sum(1 for s in last5 if s < TH_DEFENSIVO)
    persist_alc = sum(1 for s in last5 if s > TH_TRANS_ALCISTA)

    # Régimen macro
    weekly_score = int(sum(last5) / len(last5))

    if weekly_score < TH_DEFENSIVO and persist_baj >= PERSIST_MIN:
        regimen = "DEFENSIVO"
    elif weekly_score < TH_TRANS_BAJISTA:
        regimen = "TRANSICION BAJISTA"
    elif weekly_score <= TH_NEUTRO:
        regimen = "NEUTRO"
    elif weekly_score <= TH_TRANS_ALCISTA:
        regimen = "TRANSICION ALCISTA"
    else:
        regimen = "RISK-ON"

    # Divergencia (placeholder conservador)
    divergence = z_var > 1.0

    # Presión defensiva
    presion_def = calcular_presion_defensiva(
        z_var, z_acc, pendiente_7d, divergence, regimen
    )

    # Probabilidades
    prob_baj = min(100, presion_def + (10 if regimen in ["DEFENSIVO", "TRANSICION BAJISTA"] else 0))
    prob_alc = max(0, 100 - prob_baj)

    # Interpretación anticipada
    interpretacion = (
        "Alta presión defensiva anticipada. "
        "Históricamente este patrón precede fases bajistas o alta volatilidad."
        if prob_baj > 65 else
        "Mercado en transición. Confirmación pendiente."
        if prob_baj > 45 else
        "Presión defensiva baja. Contexto favorable con cautela."
    )

    payload = {
        "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_market_cap": int(total_mcap),
        "dominancia_btc": round(dom_btc, 2),
        "dominancia_stable": round(dom_stable, 2),
        "variacion_24h": round(variacion, 2),
        "aceleracion": round(aceleracion, 2),
        "pendiente_7d": round(pendiente_7d, 2),
        "sma_7": round(sma7, 2) if sma7 else "",
        "sma_21": round(sma21, 2) if sma21 else "",
        "score_diario": score_base,
        "score_semanal": weekly_score,
        "persistencia_bajista": persist_baj,
        "persistencia_alcista": persist_alc,
        "presion_defensiva": presion_def,
        "probabilidad_bajista_7d": prob_baj,
        "probabilidad_alcista_7d": prob_alc,
        "regimen_macro": regimen,
        "interpretacion": interpretacion,
        "version_modelo": MODEL_VERSION
    }

    requests.post(SHEETBEST_URL, json=payload, timeout=15)

    print("✅ v2.2 ejecutado")
    print(regimen, "| Presión:", presion_def, "| Prob bajista:", prob_baj)

if __name__ == "__main__":
    main()
