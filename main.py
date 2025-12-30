#!/usr/bin/env python3
# Ejecutar: python main.py
# GitHub Actions (cron 00:00 UTC)

import requests
from datetime import datetime, timezone
import os
import math
import sys

# -----------------------
# CONFIG (ajustá si querés)
# -----------------------
SHEETBEST_URL = os.getenv("SHEETBEST_URL")  # REQUIRED
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # OPTIONAL
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # OPTIONAL

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
COINGECKO_SIMPLE = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"

STABLE_ALIASES = [
    "usdt", "tether", "usd-coin", "usdc", "dai", "busd",
    "binance-usd", "tusd", "true-usd", "frax"
]

# thresholds / params
TH_DEFENSIVO = 30
TH_TRANS_BAJISTA = 45
TH_NEUTRO = 55
TH_TRANS_ALCISTA = 70
PERSIST_MIN = 3
HIST_LIMIT = 60
MODEL_VERSION = "v2.2-macro-matrix"

# -----------------------
# Helpers
# -----------------------
def parse_float(x):
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ".").replace("%", ""))
    except:
        return None

def safe_get(url, timeout=12):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r

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

# -----------------------
# Coingecko
# -----------------------
def obtener_datos_globales():
    r = safe_get(COINGECKO_GLOBAL)
    return r.json()["data"]

def obtener_btc_24h_change():
    try:
        r = safe_get(COINGECKO_SIMPLE)
        j = r.json()
        return parse_float(j.get("bitcoin", {}).get("usd_24h_change"))
    except Exception:
        return None

def calcular_dominancia_stable(market_pct):
    total = 0.0
    if not isinstance(market_pct, dict):
        return 0.0
    for a in STABLE_ALIASES:
        v = market_pct.get(a)
        if v is None:
            v = market_pct.get(a.upper())
        if v is not None:
            try:
                total += float(v)
            except:
                pass
    return total

# -----------------------
# Sheet.best helpers
# -----------------------
def leer_historico(limit=HIST_LIMIT):
    if not SHEETBEST_URL:
        print("ERROR: SHEETBEST_URL no configurada.")
        return []
    try:
        r = safe_get(SHEETBEST_URL)
        data = r.json()
        rows = data[-limit:]
        hist = []
        for row in rows:
            hist.append({
                "fecha": row.get("fecha"),
                "dominancia_stable": parse_float(row.get("dominancia_stable")),
                "variacion_24h": parse_float(row.get("variacion_24h")),
                "aceleracion": parse_float(row.get("aceleracion")),
                "score_diario": parse_float(row.get("score_diario") or row.get("score_macro")),
                "regimen_macro": row.get("regimen_macro") or row.get("regimen"),
                "accion_sugerida": row.get("accion_sugerida") or row.get("accion") or ""
            })
        return hist
    except Exception as e:
        print("Error leyendo Sheet.best:", e)
        return []

def post_to_sheet(payload):
    if not SHEETBEST_URL:
        raise RuntimeError("SHEETBEST_URL no configurada")
    r = requests.post(SHEETBEST_URL, json=payload, timeout=15)
    r.raise_for_status()
    return r

# -----------------------
# Matriz de decisiones (acción y exposición)
# -----------------------
def decidir_accion(presion_def, regimen_macro, score_semanal, persist_baj, persist_alc):
    """
    Retorna:
      - accion_sugerida (str)
      - exposicion_recomendada (str) -> e.g. "0-10%", "20-30%", etc
      - sesgo_operativo (str) -> "BAJISTA", "NEUTRO", "ALCISTA"
      - comentario_operativo (str)
    """
    # defaults
    accion = "OBSERVAR"
    exposicion = "30-40%"
    sesgo = "NEUTRO"
    comentario = "No hay acción clara. Mantener vigilancia."

    # reglas:
    if presion_def >= 71 or (presion_def >= 55 and persist_baj >= PERSIST_MIN):
        accion = "VENDER PARCIAL" if presion_def < 85 else "VENDER"
        exposicion = "0-20%" if presion_def >= 85 else "10-30%"
        sesgo = "BAJISTA"
        comentario = "Alta presión defensiva. Reducir exposición y proteger capital."
        if regimen_macro == "DEFENSIVO":
            accion = "VENDER"
            exposicion = "0-10%"
            comentario = "Régimen defensivo confirmado; priorizar preservación."
    elif presion_def >= 55:
        accion = "REDUCIR"
        exposicion = "20-35%"
        sesgo = "BAJISTA"
        comentario = "Presión significativa; evitar aumentar posiciones."
    elif presion_def >= 45:
        accion = "OBservar"
        exposicion = "30-40%"
        sesgo = "NEUTRO"
        comentario = "Indecisión; esperar confirmación semanal."
    elif presion_def >= 30:
        accion = "COMPRAR PARCIAL"
        exposicion = "40-60%"
        sesgo = "ALCISTA"
        comentario = "Oportunidad gradual; usar sizing y stops."
    else:
        accion = "COMPRAR"
        exposicion = "60-80%"
        sesgo = "ALCISTA"
        comentario = "Baja presión defensiva; entorno favorable para acumulación."

    # ajustar si persistencia alcista fuerte
    if persist_alc >= PERSIST_MIN and presion_def < 45:
        accion = "COMPRAR"
        exposicion = "60-80%"
        sesgo = "ALCISTA"
        comentario = "Confirmación por persistencia alcista; aumentar exposición gradual."

    # transferencia de riesgo si score_semanal es contradictorio
    if score_semanal < TH_DEFENSIVO and accion.startswith("COMPR"):
        # penalizar compras si semanal sigue defensivo
        accion = "COMPRAR PARCIAL"
        exposicion = "30-40%"
        comentario += " Nota: score semanal bajo, ser cauteloso."

    return accion, exposicion, sesgo, comentario

# -----------------------
# Telegram: envío de mensajes
# -----------------------
def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram no configurado; omitiendo envío.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print("Error enviando Telegram:", e)
        return False

# -----------------------
# Interpretación anticipada (texto corto para notificación)
# -----------------------
def resumen_para_telegram(payload):
    lines = []
    lines.append(f"📊 <b>Macro v2.2 - {payload.get('fecha')}</b>")
    lines.append(f"Regimen: {payload.get('regimen_macro')} | Score semanal: {payload.get('score_semanal')}")
    lines.append(f"Domin. stable: {payload.get('dominancia_stable')}% | Δ24h: {payload.get('variacion_24h')}% | Acel: {payload.get('aceleracion')}%")
    lines.append(f"Presion defensiva: {payload.get('presion_defensiva')} | Prob bajista 7d: {payload.get('probabilidad_bajista_7d')}%")
    lines.append(f"Acción sugerida: <b>{payload.get('accion_sugerida')}</b> | Exposición: {payload.get('exposicion_recomendada')}")
    if payload.get('alerta'):
        lines.append(f"🚨 ALERTA: {payload.get('alerta')}")
    lines.append(f"Modelo: {payload.get('version_modelo')}")
    return "\n".join(lines)

# -----------------------
# Main: análisis + publicación + matrix + telegram
# -----------------------
def analizar_y_guardar():
    # 1) datos
    try:
        cg = obtener_datos_globales()
    except Exception as e:
        print("Error CoinGecko:", e)
        return

    total_mcap = parse_float(cg.get("total_market_cap", {}).get("usd"))
    market_pct = cg.get("market_cap_percentage", {}) or {}
    dom_btc = parse_float(market_pct.get("btc"))
    dom_stable = calcular_dominancia_stable(market_pct)

    btc_change_24h = obtener_btc_24h_change()

    # 2) historico
    historico = leer_historico()

    # 3) variacion y aceleracion
    prev_dom = historico[-1]["dominancia_stable"] if historico and historico[-1].get("dominancia_stable") is not None else None
    variacion = ((dom_stable - prev_dom) / prev_dom * 100) if prev_dom else 0.0

    prev_var = historico[-1].get("variacion_24h") if len(historico) >= 1 else None
    aceleracion = (variacion - (prev_var or 0.0))

    # 4) trending
    dom_series = [r["dominancia_stable"] for r in historico if r.get("dominancia_stable") is not None]
    dom_series.append(dom_stable)
    sma7 = sma(dom_series, 7)
    sma21 = sma(dom_series, 21)
    pendiente_7d = dom_stable - dom_series[-7] if len(dom_series) >= 7 else 0.0

    # 5) z-scores (normalizacion)
    hist_vars = [r.get("variacion_24h") for r in historico]
    hist_accs = [r.get("aceleracion") for r in historico]
    z_var = zscore(variacion, hist_vars)
    z_acc = zscore(aceleracion, hist_accs)

    # 6) score base & persistencia
    # (score_base simplificado — el v2.2 ya usa presion como driver de decisión)
    score_base = 50
    if dom_stable > 12:
        score_base += 20
    elif dom_stable < 9:
        score_base -= 20
    if dom_btc and dom_btc > 46:
        score_base += 5
    elif dom_btc and dom_btc < 42:
        score_base -= 5

    scores_hist = [r.get("score_diario") for r in historico if r.get("score_diario") is not None]
    scores_hist = [float(s) for s in scores_hist]
    scores_hist.append(float(score_base))
    last5 = scores_hist[-5:]
    persist_baj = sum(1 for s in last5 if s < TH_DEFENSIVO)
    persist_alc = sum(1 for s in last5 if s > TH_TRANS_ALCISTA)
    weekly_score = int(sum(last5) / len(last5)) if last5 else int(score_base)

    # 7) divergence flag (mejorado)
    divergence = False
    if btc_change_24h is not None:
        # divergencia si precio sube >0.7% y stable sube z>1.0 (anormal)
        if btc_change_24h > 0.7 and z_var > 1.0:
            divergence = True
        if btc_change_24h > 0 and variacion > 1.5:
            divergence = True

    # 8) presion defensiva + probabilidades
    # normalize pendiente_7d to simple positive/negative measure (already numeric)
    presion_def = 50
    # z_var rules (same as v2.2 but con zscore)
    if z_var > 1.5:
        presion_def += 30
    elif z_var > 0.8:
        presion_def += 20
    elif z_var < -1.5:
        presion_def -= 15
    elif z_var < -0.8:
        presion_def -= 5
    # z_acc
    if z_acc > 1.2:
        presion_def += 15
    elif z_acc < -1.2:
        presion_def -= 10
    # pendiente
    if pendiente_7d > 0:
        presion_def += 10
    elif pendiente_7d < 0:
        presion_def -= 5
    # divergence
    if divergence:
        presion_def += 15
    # anti-rebote
    regimen_guess = "NEUTRO"
    if weekly_score < TH_DEFENSIVO and persist_baj >= PERSIST_MIN:
        regimen_guess = "DEFENSIVO"
    elif weekly_score < TH_TRANS_BAJISTA:
        regimen_guess = "TRANSICION BAJISTA"
    elif weekly_score <= TH_NEUTRO:
        regimen_guess = "NEUTRO"
    elif weekly_score <= TH_TRANS_ALCISTA:
        regimen_guess = "TRANSICION ALCISTA"
    else:
        regimen_guess = "RISK-ON"
    if regimen_guess in ["DEFENSIVO", "TRANSICION BAJISTA"] and z_var < 0 and z_acc < 0:
        presion_def += 10

    presion_def = int(max(0, min(100, round(presion_def))))

    # map presion -> probabilidades (simple)
    if presion_def <= 30:
        prob_baj = int(round(10 + presion_def * 0.8))
    elif presion_def <= 50:
        prob_baj = int(round(35 + (presion_def - 30) * 1.2))
    elif presion_def <= 70:
        prob_baj = int(round(60 + (presion_def - 50) * 1.0))
    else:
        prob_baj = int(round(81 + (presion_def - 70) * 1.9))
    prob_baj = max(0, min(100, prob_baj))
    prob_alc = max(0, min(100, 100 - prob_baj))

    # 9) matriz de decision
    accion, exposicion, sesgo, comentario = decidir_accion(presion_def, regimen_guess, weekly_score, persist_baj, persist_alc)

    # 10) construimos payload con todas las columnas (incluye acción)
    fecha_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "fecha": fecha_iso,
        "total_market_cap": int(round(total_mcap)) if (total_mcap := parse_float(cg.get("total_market_cap", {}).get("usd"))) is not None else "",
        "dominancia_btc": round(dom_btc, 2) if dom_btc is not None else "",
        "dominancia_stable": round(dom_stable, 2) if dom_stable is not None else "",
        "variacion_24h": round(variacion, 2),
        "aceleracion": round(aceleracion, 2),
        "pendiente_7d": round(pendiente_7d, 2),
        "sma_7": round(sma7, 2) if sma7 is not None else "",
        "sma_21": round(sma21, 2) if sma21 is not None else "",
        "score_diario": int(score_base),
        "score_semanal": int(weekly_score),
        "persistencia_bajista": int(persist_baj),
        "persistencia_alcista": int(persist_alc),
        "estado_dinamico": "NEUTRO" if not (variacion and aceleracion) else ("CAMBIO FUERTE" if variacion>0 and aceleracion>0 else "PANICO" if variacion<0 and aceleracion<0 else "FRENO DE CAIDA" if variacion<0 and aceleracion>0 else "AGOTAMIENTO"),
        "regimen_macro": regimen_guess,
        "interpretacion": comentario,
        "divergencia_precio_flujo": "TRUE" if divergence else "FALSE",
        "presion_defensiva": int(presion_def),
        "probabilidad_bajista_7d": int(prob_baj),
        "probabilidad_alcista_7d": int(prob_alc),
        "interpretacion_anticipada": comentario,
        "accion_sugerida": accion,
        "exposicion_recomendada": exposicion,
        "sesgo_operativo": sesgo,
        "comentario_operativo": comentario,
        "alerta": "",
        "comentario_manual": "",
        "version_modelo": MODEL_VERSION
    }

    # 11) Chequeo duplicados y decisión de postear
    try:
        if SHEETBEST_URL:
            r_check = safe_get(SHEETBEST_URL)
            data_check = r_check.json()
            last = data_check[-5:] if len(data_check) >= 5 else data_check
            # buscar si hoy ya existe
            today_found = any(str(row.get("fecha","")).startswith(fecha_iso) for row in last)
            last_row_action = last[-1].get("accion_sugerida") if last else ""
            # si ya existe registro de hoy -> no crear duplicado
            if today_found:
                print("Registro para hoy ya existe. No se postea una nueva fila (evitando duplicados).")
                # si la accion cambió respecto último row -> enviar notificación (no postear)
                if last_row_action != accion:
                    print("Acción cambió respecto al último registro:", last_row_action, "->", accion)
                    payload_msg = resumen_para_telegram(payload)
                    send_telegram_message(payload_msg)
                else:
                    print("Acción no cambió respecto al último registro. No se envía Telegram.")
            else:
                # Postear nuevo registro
                try:
                    rpost = post_to_sheet(payload)
                    print("Registro cargado en Sheet.best (OK).")
                    # enviar Telegram si accion es relevante (vender o alerta)
                    if presion_def >= 71 or accion in ["VENDER","VENDER PARCIAL","REDUCIR"]:
                        payload_msg = resumen_para_telegram(payload)
                        send_telegram_message(payload_msg)
                except Exception as e:
                    print("Error posteando a Sheet.best:", e)
        else:
            print("SHEETBEST_URL no configurada. Imprimiendo payload para revisión:")
            print(payload)

    except Exception as e:
        print("Error en comprobación/posteo:", e)

    # 12) resumen consola
    print("\n--- Resumen ---")
    print("fecha:", payload["fecha"])
    print("regimen:", payload["regimen_macro"])
    print("presion_def:", payload["presion_defensiva"])
    print("accion_sugerida:", payload["accion_sugerida"], "| exposicion:", payload["exposicion_recomendada"])
    print("prob_bajista_7d:", payload["probabilidad_bajista_7d"])
    print("---------------\n")

if __name__ == "__main__":
    analizar_y_guardar()
