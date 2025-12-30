#!/usr/bin/env python3
# GitHub Actions (cron diario)

import requests
from datetime import datetime, timezone
import os
import math
import sys

# -----------------------
# CONFIG 
# -----------------------
TH_DEFENSIVO = 30
TH_TRANS_BAJISTA = 45
TH_NEUTRO = 55
TH_TRANS_ALCISTA = 70

PERSIST_MIN = 3
SMA_CORTA = 7
SMA_LARGA = 21
HIST_LIMIT = 60

SHEETBEST_URL = os.getenv("SHEETBEST_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
COINGECKO_SIMPLE = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"

STABLE_ALIASES = ["usdt", "tether", "usd-coin", "usdc", "dai", "busd", "binance-usd", "tusd", "true-usd", "frax"]
MODEL_VERSION = "v2.1-anticipation"

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
    s = s.replace("%", "").replace(",", ".")
    try:
        return float(s)
    except:
        try:
            return float(s.replace(" ", ""))
        except:
            return None

def safe_get(url, timeout=12):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r

# -----------------------
# Data sources
# -----------------------
def obtener_datos_globales():
    try:
        r = safe_get(COINGECKO_GLOBAL)
        return r.json()["data"]
    except Exception as e:
        print("Error obteniendo CoinGecko global:", e)
        raise

def obtener_btc_24h_change():
    try:
        r = safe_get(COINGECKO_SIMPLE)
        j = r.json()
        # estructura: { "bitcoin": { "usd": 47000, "usd_24h_change": 1.234 } }
        b = j.get("bitcoin", {})
        change = b.get("usd_24h_change")
        return parse_float(change)
    except Exception as e:
        print("Warning: no se pudo obtener btc 24h change:", e)
        return None

def calcular_dominancia_stable_de_market_pct(market_pct):
    total = 0.0
    if not isinstance(market_pct, dict):
        return 0.0
    for alias in STABLE_ALIASES:
        v = market_pct.get(alias)
        if v is None:
            v = market_pct.get(alias.upper())
        if v is not None:
            try:
                total += float(v)
            except:
                pass
    return total

# -----------------------
# Sheet.best reading
# -----------------------
def leer_historico(limit=HIST_LIMIT):
    if not SHEETBEST_URL:
        print("ERROR: SHEETBEST_URL no definida.")
        return []
    try:
        r = safe_get(SHEETBEST_URL)
        data = r.json()
        if not isinstance(data, list):
            print("Respuesta Sheet.best no es lista JSON:", data)
            return []
        rows = data[-limit:]
        historico = []
        for row in rows:
            historico.append({
                "fecha": row.get("fecha"),
                "total_market_cap": parse_float(row.get("total_market_cap")),
                "dominancia_btc": parse_float(row.get("dominancia_btc") or row.get("dominancia_bitcoin")),
                "dominancia_stable": parse_float(row.get("dominancia_stable") or row.get("dominancia_stablecoins")),
                "variacion_24h": parse_float(row.get("variacion_24h") or row.get("variacion_stablecoins_pct")),
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
# Estadísticos
# -----------------------
def sma(lista, n):
    vals = [v for v in lista if v is not None]
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n

# -----------------------
# Score y patrón adelantado
# -----------------------
def calcular_score_base(dom_stable, variacion_pct, aceleracion, dom_btc):
    score = 50
    if dom_stable is None:
        dom_stable = 0
    if dom_stable > 12:
        score += 20
    elif dom_stable < 9:
        score -= 20
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
    if aceleracion is None:
        aceleracion = 0
    if aceleracion >= 1.5:
        score += 10
    elif aceleracion <= -1.5:
        score -= 10
    if dom_btc is None:
        dom_btc = 0
    if dom_btc > 46:
        score += 5
    elif dom_btc < 42:
        score -= 5
    return max(0, min(100, int(round(score))))

def calcular_presion_defensiva(dom_stable_change, aceleracion, pendiente_7d, divergence_flag, persistencia_bajista):
    """
    Ponderaciones (ajustables):
      - dom_stable_change (velocidad) -> 25
      - aceleracion -> 20
      - pendiente_7d -> 20
      - divergence_flag (0/1) -> 20
      - persistencia_bajista (0-5 cap) -> 15 (normalizada)
    Resultado en 0-100, donde mayor = más presión defensiva (riesgo de baja).
    """
    # normalizaciones sencillas (estas pueden afinarse con backtest)
    # dom_stable_change en puntos porcentuales (ej. +1.5 -> 1.5)
    x_dom = max(min(dom_stable_change, 10), -10)  # cap
    # convertimos a 0-100 contribution (asumimos 0..5pp relevante)
    dom_score = (max(x_dom, 0) / 5.0) * 25  # si x_dom=5 => 25 pts

    x_acc = max(min(aceleracion, 5), -5)
    acc_score = (max(x_acc, 0) / 3.0) * 20  # si accel=3 => 20 pts

    x_pend = max(min(pendiente_7d, 10), -10)
    pend_score = (max(x_pend, 0) / 7.0) * 20  # si pend=7 => 20 pts

    div_score = 20 if divergence_flag else 0

    persist_norm = max(min(persistencia_bajista, 5), 0)
    persist_score = (persist_norm / 5.0) * 15

    raw = dom_score + acc_score + pend_score + div_score + persist_score
    presion = int(round(max(0, min(100, raw))))
    return presion

def riesgo_probabilidades_from_presion(presion):
    """
    Mapear presión defensiva a probabilidades:
    - presion 0-30 -> baja probabilidad bajista
    - 31-50 -> moderada
    - 51-70 -> alta
    - 71-100 -> muy alta
    Devuelve prob_bajista_7d (%) y prob_alcista_7d (%)
    """
    if presion <= 30:
        p_baj = 10 + presion * 0.8  # 10..34
    elif presion <= 50:
        p_baj = 35 + (presion - 30) * 1.2  # 35..59
    elif presion <= 70:
        p_baj = 60 + (presion - 50) * 1.0  # 60..80
    else:
        p_baj = 81 + (presion - 70) * 1.9  # 81..100 approx

    p_baj = max(0, min(100, p_baj))
    # prob_alcista inversa con piso
    p_alc = max(0, min(100, 100 - p_baj))
    return int(round(p_baj)), int(round(p_alc))

# -----------------------
# Motor de interpretacion anticipada
# -----------------------
def interpretar_anticipado(presion, prob_baj, prob_alc, divergence_flag, regimen_macro):
    lines = []
    if divergence_flag:
        lines.append("Divergencia precio/flujo detectada: precio no confirma flujo (señal adelantada de riesgo).")
    if presion >= 71:
        lines.append(f"ALERTA anticipada: presión defensiva alta ({presion}). Prob. bajista 7d ≈ {prob_baj}%. Considerar reducción de exposición.")
    elif presion >= 51:
        lines.append(f"Precaución: presión defensiva significativa ({presion}). Prob. bajista 7d ≈ {prob_baj}%. Evitar entradas agresivas.")
    elif presion >= 31:
        lines.append(f"Atención: presión moderada ({presion}). Prob. bajista 7d ≈ {prob_baj}%. Monitorizar persistencia.")
    else:
        lines.append(f"Baja presión defensiva ({presion}). Prob. bajista 7d ≈ {prob_baj}%.")
    # añadir contexto del régimen macro ya calculado
    lines.append(f"Regimen macro actual: {regimen_macro}.")
    # síntesis final
    if prob_baj > 70:
        lines.append("Estrategia sugerida: priorizar capital preservación y evitar aumentar riesgo hasta confirmación semanal.")
    elif prob_alc > 70:
        lines.append("Estrategia sugerida: entorno favorable para acumulación progresiva con gestión de riesgo.")
    else:
        lines.append("Estrategia sugerida: mantener vigilancia; usar confirmación semanal para decisiones mayores.")
    return " ".join(lines)

# -----------------------
# Main: análisis + publicación
# -----------------------
def analizar_y_guardar():
    # 1) obtener datos
    try:
        cg = obtener_datos_globales()
    except Exception as e:
        print("Fallo al obtener global data:", e)
        return

    total_mcap = parse_float(cg.get("total_market_cap", {}).get("usd"))
    market_pct = cg.get("market_cap_percentage", {}) or {}
    dominancia_btc = parse_float(market_pct.get("btc"))
    dominancia_stable = calcular_dominancia_stable_de_market_pct(market_pct)

    btc_price_change_24h = obtener_btc_24h_change()  # en %
    # si no está disponible, se usa None

    # 2) leer historico
    historico = leer_historico(limit=HIST_LIMIT)

    # 3) calcular variacion y aceleracion
    variacion_pct = 0.0
    if historico and len(historico) >= 1:
        prev_dom = historico[-1].get("dominancia_stable")
        if prev_dom and prev_dom != 0:
            variacion_pct = ((dominancia_stable - prev_dom) / prev_dom) * 100
    aceleracion = 0.0
    if historico and len(historico) >= 2:
        prev_var = historico[-1].get("variacion_24h") or 0.0
        try:
            aceleracion = variacion_pct - float(prev_var)
        except:
            aceleracion = variacion_pct

    # 4) SMA y pendiente 7d
    doms = [r.get("dominancia_stable") for r in historico if r.get("dominancia_stable") is not None]
    doms_for_sma = doms + [dominancia_stable]
    sma7 = sma(doms_for_sma, SMA_CORTA)
    sma21 = sma(doms_for_sma, SMA_LARGA)
    pendiente_7d = 0.0
    if len(doms_for_sma) >= 7:
        try:
            pendiente_7d = dominancia_stable - doms_for_sma[-7]
        except:
            pendiente_7d = 0.0

    # 5) score base y persistencias
    score_diario = calcular_score_base(dominancia_stable, variacion_pct, aceleracion, dominancia_btc)
    scores_hist = [r.get("score_diario") for r in historico if r.get("score_diario") is not None]
    scores_hist = [float(x) for x in scores_hist]
    scores_hist.append(float(score_diario))
    last5 = scores_hist[-5:] if len(scores_hist) >= 1 else [score_diario]
    persistencia_bajista = sum(1 for s in last5 if s < TH_DEFENSIVO)
    persistencia_alcista = sum(1 for s in last5 if s > TH_TRANS_ALCISTA)

    weekly_score = calcular_weekly_score_con_registros(historico, score_diario)

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

    # 6) Divergencia precio/flujo
    divergence_flag = False
    # definimos umbral de "divergencia relevante": precio sube >0.5% mientras stable sube >+0.5pp (o precio sube y stables suben)
    if btc_price_change_24h is not None:
        # criterio: signo opuesto entre precio y flujo: precio up (>+0.5) AND stable up (>+0.5) -> distribution (bearish divergence)
        if btc_price_change_24h > 0.5 and variacion_pct > 0.5:
            divergence_flag = True
        # también si precio modestamente sube y stable sube mucho
        if btc_price_change_24h > 0 and variacion_pct > 1.5:
            divergence_flag = True
        # pauta inversa (confirmación risk-on)
        # (we leave divergence_flag False if not matching above)
    else:
        divergence_flag = False

    # 7) presion defensiva y probabilidades
    presion_def = calcular_presion_defensiva(variacion_pct, aceleracion, pendiente_7d, divergence_flag, persistencia_bajista)
    prob_baj, prob_alc = riesgo_probabilidades_from_presion(presion_def)

    # 8) interpretacion anticipada
    interpretacion_anticipada = interpretar_anticipado(presion_def, prob_baj, prob_alc, divergence_flag, regimen_macro)

    # 9) construir payload
    fecha_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "fecha": fecha_iso,
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
        "estado_dinamico": "NEUTRO" if not payload else "",  # placeholder updated below
        "regimen_macro": regimen_macro,
        "interpretacion": interpretacion_anticipada,
        "divergencia_precio_flujo": "TRUE" if divergence_flag else "FALSE",
        "presion_defensiva": int(presion_def),
        "prob_bajista_7d": int(prob_baj),
        "prob_alcista_7d": int(prob_alc),
        "interpretacion_anticipada": interpretacion_anticipada,
        "alerta": "",
        "comentario_manual": "",
        "version_modelo": MODEL_VERSION
    }

    # fix estado_dinamico properly (recompute text)
    estado_dinamico = "NEUTRO"
    if variacion_pct > 0 and aceleracion > 0:
        estado_dinamico = "CAMBIO FUERTE"
    elif variacion_pct < 0 and aceleracion > 0:
        estado_dinamico = "FRENO DE CAIDA"
    elif variacion_pct < 0 and aceleracion < 0:
        estado_dinamico = "PANICO"
    elif variacion_pct > 0 and aceleracion < 0:
        estado_dinamico = "AGOTAMIENTO"
    payload["estado_dinamico"] = estado_dinamico

    # 10) alertas (si corresponde)
    alerta_text = ""
    send_alert = False
    if score_diario >= 81:
        alerta_text = f"ALERTA: Score {score_diario} (RISK-OFF). {interpretacion_anticipada}"
        send_alert = True
    elif score_diario <= 20:
        alerta_text = f"ALERTA: Score {score_diario} (RISK-ON FUERTE). {interpretacion_anticipada}"
        send_alert = True
    if historico and len(historico) >= 1:
        last_reg = historico[-1].get("regimen_macro") or historico[-1].get("regimen")
        if last_reg and last_reg != regimen_macro:
            alerta_text = f"CAMBIO DE RÉGIMEN: {last_reg} → {regimen_macro}. Score: {score_diario}"
            send_alert = True
    if weekly_score is not None and weekly_score >= 81:
        alerta_text = f"ALERTA SEMANAL: weekly_score {weekly_score} indica stress sostenido."
        send_alert = True

    if send_alert:
        payload["alerta"] = alerta_text

    # 11) evitar duplicados: si ya existe registro con fecha_iso en últimas filas => skip posting
    try:
        if SHEETBEST_URL:
            r_check = safe_get(SHEETBEST_URL)
            data_check = r_check.json()
            last = data_check[-5:] if len(data_check) >= 5 else data_check
            today_found = any(str(row.get("fecha","")).startswith(fecha_iso) for row in last)
            if today_found:
                print("Registro para la fecha ya existe. No se añade duplicado. (fecha:", fecha_iso, ")")
                # aún así podemos actualizar alerta localmente; por ahora omitimos POST
                # si preferís actualizar la fila, habría que usar otra API o commitear CSV
            else:
                # POST
                rpost = requests.post(SHEETBEST_URL, json=payload, timeout=15)
                try:
                    rpost.raise_for_status()
                    print("✅ Registro subido a Sheet.best")
                except Exception as e:
                    print("Error subiendo a Sheet.best:", e)
                    try:
                        print("Respuesta:", rpost.text)
                    except:
                        pass
        else:
            print("SHEETBEST_URL no configurado. Imprimiendo payload para revisión:")
            print(payload)
    except Exception as e:
        print("Error comprobando duplicado o subiendo:", e)

    # 12) Telegram si corresponde
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

    # 13) resumir en consola
    print("\n--- Resumen ---")
    keys = ["fecha","dominancia_stable","variacion_24h","aceleracion","presion_defensiva","prob_bajista_7d","prob_alcista_7d","score_diario","score_semanal","regimen_macro"]
    for k in keys:
        print(f"{k}: {payload.get(k)}")
    print("interpretacion_anticipada:", payload.get("interpretacion_anticipada"))
    print("---------------\n")

if __name__ == "__main__":
    analizar_y_guardar()
