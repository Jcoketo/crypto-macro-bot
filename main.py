#!/usr/bin/env python3
# Ejecutar: python main.py
# Diseñado para correr en GitHub Actions cada 24h

import requests
from datetime import datetime, timezone
import os
import math

# -----------------------
# CONFIG 
# -----------------------
# Umbrales para regime (score semanal)
TH_DEFENSIVO = 30
TH_TRANS_BAJISTA = 45
TH_NEUTRO = 55
TH_TRANS_ALCISTA = 70

# Persistencia: mínimo de días consecutivos para confirmar régimen
PERSIST_MIN = 3

# SMA windows (días)
SMA_CORTA = 7
SMA_LARGA = 21

# Cuántas filas históricas traer (max)
HIST_LIMIT = 60

# SheetBest URL (leer desde secret / env)
SHEETBEST_URL = os.getenv("SHEETBEST_URL")  # obligatorio en Actions
# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# CoinGecko
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

# Aliases para stablecoins en market_cap_percentage
STABLE_ALIASES = ["usdt", "tether", "usd-coin", "usdc", "dai", "busd", "binance-usd", "tusd", "true-usd", "frax"]

# Version modelo
MODEL_VERSION = "v2.0-macro"

# -----------------------
# Helpers
# -----------------------
def parse_float(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s == "":
        return None
    # quitar % y reemplazar coma decimal
    s = s.replace("%", "").replace(",", ".")
    try:
        return float(s)
    except:
        try:
            # intentar quitar espacios
            return float(s.replace(" ", ""))
        except:
            return None

def safe_get(url, timeout=12):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r

# -----------------------
# Lectura CoinGecko
# -----------------------
def obtener_datos_globales():
    try:
        r = safe_get(COINGECKO_GLOBAL)
        data = r.json()["data"]
    except Exception as e:
        print("Error obteniendo CoinGecko global:", e)
        raise
    return data

def calcular_dominancia_stable_de_market_pct(market_pct):
    # market_pct es dict de CoinGecko: keys pueden ser 'btc','eth','usdt','usd-coin',...
    total = 0.0
    if not isinstance(market_pct, dict):
        return 0.0
    for alias in STABLE_ALIASES:
        v = market_pct.get(alias)
        if v is None:
            # CoinGecko a veces usa símbolos distintos (no común), intentamos mayúsculas
            v = market_pct.get(alias.upper())
        if v is not None:
            try:
                total += float(v)
            except:
                pass
    return total

# -----------------------
# Lectura histórico desde Sheet.best
# -----------------------
def leer_historico(limit=HIST_LIMIT):
    if not SHEETBEST_URL:
        print("ERROR: SHEETBEST_URL no definida (setear secret SHEETBEST_URL).")
        return []
    try:
        r = safe_get(SHEETBEST_URL)
        data = r.json()
        if not isinstance(data, list):
            print("Respuesta Sheet.best no es lista JSON:", data)
            return []
        # tomar últimas `limit` filas (asumimos orden cronológico ascendente)
        rows = data[-limit:]
        historico = []
        for row in rows:
            historico.append({
                "fecha": row.get("fecha"),
                "total_market_cap": parse_float(row.get("total_market_cap")),
                "dominancia_btc": parse_float(row.get("dominancia_btc") or row.get("dominancia_bitcoin") or row.get("dominancia_btc")),
                "dominancia_stable": parse_float(row.get("dominancia_stable") or row.get("dominancia_stablecoins") or row.get("dominancia_stablecoins")),
                "variacion_24h": parse_float(row.get("variacion_24h") or row.get("variacion_stablecoins_pct") or row.get("variacion_stablecoins_pct")),
                "aceleracion": parse_float(row.get("aceleracion") or row.get("aceleracion_stablecoins")),
                "sma_7": parse_float(row.get("sma_7")),
                "sma_21": parse_float(row.get("sma_21")),
                "score_diario": parse_float(row.get("score_diario") or row.get("score_macro")),
                "score_semanal": parse_float(row.get("score_semanal") or row.get("weekly_score")),
                "persistencia_bajista": int(parse_float(row.get("persistencia_bajista") or 0)) if row.get("persistencia_bajista") not in (None,"") else 0,
                "persistencia_alcista": int(parse_float(row.get("persistencia_alcista") or 0)) if row.get("persistencia_alcista") not in (None,"") else 0,
                "estado_dinamico": row.get("estado_dinamico"),
                "regimen_macro": row.get("regimen_macro") or row.get("regimen"),
                "interpretacion": row.get("interpretacion"),
                "alerta": row.get("alerta") or ""
            })
        return historico
    except Exception as e:
        print("Error leyendo Sheet.best:", e)
        return []

# -----------------------
# Cálculos estadísticos
# -----------------------
def sma(lista, n):
    vals = [v for v in lista if v is not None]
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n

def calcular_score(dom_stable, variacion_pct, aceleracion, dom_btc):
    # Repr. del score explicado: base 50, sumas/restas por factores
    score = 50
    # Dominancia stable (40% weight)
    if dom_stable is None:
        dom_stable = 0
    if dom_stable > 12:
        score += 20
    elif dom_stable < 9:
        score -= 20
    # Variación 24h (30% weight)
    if variacion_pct is None:
        variacion_pct = 0
    if variacion_pct >= 5:
        score += 15
    elif variacion_pct <= -5:
        score -= 15
    elif variacion_pct >= 3:
        score += 8
    elif variacion_pct <= -3:
        score -= 8
    # Aceleración (20% weight)
    if aceleracion is None:
        aceleracion = 0
    if aceleracion >= 1.5:
        score += 10
    elif aceleracion <= -1.5:
        score -= 10
    # Dominancia BTC (10%)
    if dom_btc is None:
        dom_btc = 0
    if dom_btc > 46:
        score += 5
    elif dom_btc < 42:
        score -= 5
    score = max(0, min(100, int(round(score))))
    return score

def calcular_weekly_score_con_registros(registros, current_score):
    # toma hasta 6 previos + current para window de 7
    if registros is None:
        registros = []
    prev_scores = [r.get("score_diario") for r in registros if r.get("score_diario") is not None]
    # asegurarse de que sean floats
    prev_scores = [float(x) for x in prev_scores]
    window = prev_scores[-6:] + [float(current_score)]
    if not window:
        return None
    return int(round(sum(window) / len(window)))

# -----------------------
# Lógica principal: análisis y payload
# -----------------------
def analizar_y_guardar():
    # 1) datos globales
    cg = obtener_datos_globales()
    total_mcap = parse_float(cg.get("total_market_cap", {}).get("usd"))
    market_pct = cg.get("market_cap_percentage", {})
    dominancia_btc = parse_float(market_pct.get("btc"))
    dominancia_stable = calcular_dominancia_stable_de_market_pct(market_pct)

    # 2) historico
    historico = leer_historico(limit=HIST_LIMIT)

    # 3) variación 24h vs último registro
    variacion_pct = 0.0
    if historico and len(historico) >= 1:
        prev_dom = historico[-1].get("dominancia_stable")
        if prev_dom and prev_dom != 0:
            variacion_pct = ((dominancia_stable - prev_dom) / prev_dom) * 100
        else:
            variacion_pct = 0.0

    # 4) aceleración (variacion actual - variacion previa)
    aceleracion = 0.0
    if historico and len(historico) >= 2:
        prev_var = historico[-1].get("variacion_24h") or 0.0
        try:
            aceleracion = variacion_pct - float(prev_var)
        except:
            aceleracion = variacion_pct

    # 5) SMA y pendiente 7d
    doms = [r.get("dominancia_stable") for r in historico if r.get("dominancia_stable") is not None]
    # incluir el valor de hoy al final para cálculos de SMA que incluyan hoy
    doms_for_sma = doms + [dominancia_stable]
    sma7 = sma(doms_for_sma, SMA_CORTA)
    sma21 = sma(doms_for_sma, SMA_LARGA)
    pendiente_7d = 0.0
    if len(doms_for_sma) >= 7:
        try:
            pendiente_7d = dominancia_stable - doms_for_sma[-7]
        except:
            pendiente_7d = 0.0

    # 6) score diario
    score_diario = calcular_score(dominancia_stable, variacion_pct, aceleracion, dominancia_btc)

    # 7) persistencia (últimos 5 registros de score)
    scores_hist = [r.get("score_diario") for r in historico if r.get("score_diario") is not None]
    scores_hist = [float(x) for x in scores_hist]
    scores_hist.append(float(score_diario))
    last5 = scores_hist[-5:] if len(scores_hist) >= 1 else [score_diario]
    persistencia_bajista = sum(1 for s in last5 if s < TH_DEFENSIVO)
    persistencia_alcista = sum(1 for s in last5 if s > TH_TRANS_ALCISTA)

    # 8) weekly score (promedio ventana 7: hasta 6 prev + hoy)
    weekly_score = calcular_weekly_score_con_registros(historico, score_diario)

    # 9) regimen macro (decisión) basado en weekly_score y persistencia
    regimen_macro = "NEUTRO"
    if weekly_score is None:
        weekly_score = score_diario
    if weekly_score < TH_DEFENSIVO and persistencia_bajista >= PERSIST_MIN:
        regimen_macro = "DEFENSIVO"
    elif weekly_score < TH_TRANS_BAJISTA:
        regimen_macro = "TRANSICION BAJISTA"
    elif weekly_score <= TH_NEUTRO:
        regimen_macro = "NEUTRO"
    elif weekly_score <= TH_TRANS_ALCISTA:
        regimen_macro = "TRANSICION ALCISTA"
    else:
        regimen_macro = "RISK-ON"

    # 10) estado dinamico
    estado_dinamico = "NEUTRO"
    if variacion_pct > 0 and aceleracion > 0:
        estado_dinamico = "CAMBIO FUERTE"
    elif variacion_pct < 0 and aceleracion > 0:
        estado_dinamico = "FRENO DE CAIDA"
    elif variacion_pct < 0 and aceleracion < 0:
        estado_dinamico = "PANICO"
    elif variacion_pct > 0 and aceleracion < 0:
        estado_dinamico = "AGOTAMIENTO"

    # 11) interpretacion textual más prudente (macro-aware)
    interpretacion = ""
    if regimen_macro == "DEFENSIVO":
        interpretacion = "Flujo defensivo dominante. Alta probabilidad de continuación bajista o rango prolongado; priorizar preservación de capital."
    elif regimen_macro == "TRANSICION BAJISTA":
        interpretacion = "Fase de transición bajista — rebotes posibles pero frágiles. Mantener cautela."
    elif regimen_macro == "NEUTRO":
        interpretacion = "Mercado equilibrado; falta confirmación direccional. Esperar confirmación semanal."
    elif regimen_macro == "TRANSICION ALCISTA":
        interpretacion = "Mejora incipiente en apetito por riesgo. Entradas selectivas con confirmación adicional."
    else:
        interpretacion = "Apetito por riesgo sostenido. Ambiente favorable para exposición pero vigilar aceleración."

    # 12) construir payload en el orden de columnas A..S (según te pasé antes)
    payload = {
        "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_market_cap": int(round(total_mcap)) if total_mcap is not None else "",
        "dominancia_btc": round(dominancia_btc, 2) if dominancia_btc is not None else "",
        "dominancia_stable": round(dominancia_stable, 2) if dominancia_stable is not None else "",
        "variacion_24h": round(variacion_pct, 2),
        "aceleracion": round(aceleracion, 2),
        "pendiente_7d": round(pendiente_7d, 2),
        "sma_7": round(sma7, 2) if sma7 is not None else "",
        "sma_21": round(sma21, 2) if sma21 is not None else "",
        "score_diario": int(score_diario),
        "score_semanal": int(weekly_score) if weekly_score is not None else "",
        "persistencia_bajista": int(persistencia_bajista),
        "persistencia_alcista": int(persistencia_alcista),
        "estado_dinamico": estado_dinamico,
        "regimen_macro": regimen_macro,
        "interpretacion": interpretacion,
        "alerta": "",  # se rellenará si enviamos alerta por Telegram
        "comentario_manual": "",
        "version_modelo": MODEL_VERSION
    }

    # 13) lógica de alertas (simple): enviar solo si cambio importante de régimen o score extremo
    alerta_text = ""
    send_alert = False
    # alertas por score extremo
    if score_diario >= 81:
        alerta_text = f"ALERTA: Score {score_diario} (RISK-OFF). {interpretacion}"
        send_alert = True
    elif score_diario <= 20:
        alerta_text = f"ALERTA: Score {score_diario} (RISK-ON FUERTE). {interpretacion}"
        send_alert = True
    # cambio de regimen vs último registro
    if historico and len(historico) >= 1:
        last_reg = historico[-1].get("regimen_macro") or historico[-1].get("regimen")
        if last_reg and last_reg != regimen_macro:
            alerta_text = f"CAMBIO DE RÉGIMEN: {last_reg} → {regimen_macro}. Score: {score_diario}"
            send_alert = True
    # alerta semanal extrema
    if weekly_score is not None and weekly_score >= 81:
        alerta_text = f"ALERTA SEMANAL: weekly_score {weekly_score} indica stress sostenido."
        send_alert = True

    if send_alert:
        payload["alerta"] = alerta_text

    # 14) POST a sheet.best
    try:
        if not SHEETBEST_URL:
            print("SHEETBEST_URL no configurado. No se puede subir.")
        else:
            r = requests.post(SHEETBEST_URL, json=payload, timeout=15)
            r.raise_for_status()
            print("✅ Registro subido a Sheet.best")
    except Exception as e:
        print("Error subiendo a Sheet.best:", e)
        try:
            print("Respuesta:", r.text)
        except:
            pass

    # 15) enviar Telegram si corresponde
    if send_alert and alerta_text:
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            try:
                tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                data = {"chat_id": TELEGRAM_CHAT_ID, "text": alerta_text, "parse_mode": "HTML"}
                rt = requests.post(tg_url, json=data, timeout=10)
                rt.raise_for_status()
                print("✅ Alerta enviada por Telegram")
            except Exception as e:
                print("Error enviando Telegram:", e)
        else:
            print("Telegram no configurado (omitido).")

    # 16) imprimir resumen local
    print("\n--- Resumen ---")
    for k in ["fecha","dominancia_stable","variacion_24h","aceleracion","score_diario","score_semanal","regimen_macro","interpretacion"]:
        print(f"{k}: {payload.get(k)}")
    print("---------------\n")

if __name__ == "__main__":
    analizar_y_guardar()
