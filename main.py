#!/usr/bin/env python3
# main.py - v2.4+stops - Macro + Patrones + Escenarios + Matriz de decisión + Telegram + Sheet.best
# Incluye backtest avanzado con STOP LOSS y TAKE PROFIT estructurales.
# Ejecutar: python main.py
# Backtest: python main.py --backtest

import requests
from datetime import datetime, timezone, timedelta
import os
import math
import statistics
import csv
import sys

# -----------------------
# CONFIG / SECRETS (set via GitHub Actions secrets)
# -----------------------
SHEETBEST_URL = os.getenv("SHEETBEST_URL")            # REQUIRED
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")          # OPTIONAL
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # OPTIONAL

# Optional external endpoints (user-provided)
ETF_FLOWS_URL = os.getenv("ETF_FLOWS_URL")            # OPTIONAL
ONCHAIN_FLOWS_URL = os.getenv("ONCHAIN_FLOWS_URL")    # OPTIONAL

# CoinGecko endpoints
CG_GLOBAL = "https://api.coingecko.com/api/v3/global"
CG_SIMPLE_BTC = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
CG_BTC_MARKET_CHART = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days={days}"

# Version
MODEL_VERSION = "v2.4-stops"

# Parameters
HIST_LIMIT = 120
Z_WINDOW = 14
PERSIST_MIN = 3

# Thresholds (tunable)
TH_DEFENSIVO = 30
TH_TRANS_BAJISTA = 45
TH_NEUTRO = 55
TH_TRANS_ALCISTA = 70

# Stop/TP parameters (you can adjust)
MAX_DRAWDOWN_STOP = -0.18   # stop if drawdown reaches -18% from entry
TP_PROFIT_PCT = 0.25        # take profit if +25%
TP_PRESION_DELTA = 20       # take profit if presion_def rises +20 pts vs entry
STOP_PRESION = 75           # stop if presion_def >= 75

# -----------------------
# HELPERS
# -----------------------
def safe_get(url, timeout=15, params=None, headers=None):
    r = requests.get(url, timeout=timeout, params=params, headers=headers)
    r.raise_for_status()
    return r

def parse_float(x):
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ".").replace("%", ""))
    except:
        return None

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def zscore(actual, hist_list, window=Z_WINDOW):
    vals = [v for v in hist_list if v is not None]
    if len(vals) < window:
        return 0.0
    sub = vals[-window:]
    mean = sum(sub)/len(sub)
    var = sum((x-mean)**2 for x in sub)/len(sub)
    std = math.sqrt(var) if var > 0 else 0
    if std == 0:
        return 0.0
    return (actual - mean)/std

def sma(values, n):
    vals = [v for v in values if v is not None]
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n

# -----------------------
# SHEET.BEST IO
# -----------------------
def leer_historico(limit=HIST_LIMIT):
    if not SHEETBEST_URL:
        print("ERROR: SHEETBEST_URL no configurada.")
        return []
    try:
        r = safe_get(SHEETBEST_URL)
        data = r.json()
        if not isinstance(data, list):
            print("Sheet.best devolvió no-lista:", type(data))
            return []
        rows = data[-limit:]
        hist = []
        for row in rows:
            hist.append({
                "fecha": row.get("fecha"),
                "total_market_cap": parse_float(row.get("total_market_cap")),
                "dominancia_btc": parse_float(row.get("dominancia_btc")),
                "dominancia_usdt": parse_float(row.get("dominancia_usdt") or row.get("usdt_d")),
                "dominancia_usdc": parse_float(row.get("dominancia_usdc") or row.get("usdc_d")),
                "dominancia_stable": parse_float(row.get("dominancia_stable")),
                "variacion_24h": parse_float(row.get("variacion_24h")),
                "aceleracion": parse_float(row.get("aceleracion")),
                "score_diario": parse_float(row.get("score_diario")),
                "score_semanal": parse_float(row.get("score_semanal")),
                "presion_defensiva": parse_float(row.get("presion_defensiva")),
                "accion_sugerida": row.get("accion_sugerida") or row.get("accion") or "",
                "exposicion_recomendada": row.get("exposicion_recomendada") or ""
            })
        return hist
    except Exception as e:
        print("Error leyendo Sheet.best:", e)
        return []

def post_to_sheet(payload):
    if not SHEETBEST_URL:
        raise RuntimeError("SHEETBEST_URL no configurada")
    r = requests.post(SHEETBEST_URL, json=payload, timeout=20)
    r.raise_for_status()
    return r

# -----------------------
# Optional external fetchers
# -----------------------
def fetch_external_series(url):
    if not url:
        return {}
    try:
        r = safe_get(url)
        j = r.json()
        out = {}
        if isinstance(j, list):
            for item in j:
                d = item.get("date") or item.get("fecha")
                if not d:
                    continue
                out[d] = item
        elif isinstance(j, dict):
            for k,v in j.items():
                out[k] = v
        return out
    except Exception as e:
        print(f"Warning: fetch_external_series failed for {url}: {e}")
        return {}

# -----------------------
# CoinGecko helpers
# -----------------------
def fetch_global_safe():
    try:
        r = safe_get(CG_GLOBAL)
        return r.json().get("data", {})
    except Exception as e:
        print("fetch_global failed:", e)
        return {}

def fetch_btc_market_chart_days(days=120):
    try:
        r = safe_get(CG_BTC_MARKET_CHART.format(days=days))
        return r.json()
    except Exception as e:
        print("fetch_btc_market_chart failed:", e)
        return None

def fetch_btc_24h_change():
    try:
        r = safe_get(CG_SIMPLE_BTC)
        return parse_float(r.json().get("bitcoin", {}).get("usd_24h_change"))
    except Exception as e:
        print("Warning: fetch_btc_24h_change failed:", e)
        return None

# -----------------------
# Scoring / patterns / matrix (v2.4 logic)
# -----------------------
def score_patterns_to_probabilities(features):
    presion = features.get("presion_def", 50)
    divergence = features.get("divergence", False)
    dom_stable_pend = features.get("dom_stable_pend", 0)
    dom_btc_pend = features.get("dom_btc_pend", 0)
    volume_spike = features.get("volume_spike", False)
    structure = features.get("market_structure", "sideways")

    bull = 0.0; neutral = 0.0; bear = 0.0
    if presion >= 75: bear += 50
    elif presion >= 55: bear += 30
    elif presion >= 45: bear += 10
    else: bull += 10

    if divergence: bear += 25

    if dom_stable_pend > 0.3: bear += 20
    elif dom_stable_pend > 0.1: bear += 10
    elif dom_stable_pend < -0.3: bull += 25
    elif dom_stable_pend < -0.1: bull += 10

    if dom_btc_pend > 0.3: bear += 10
    elif dom_btc_pend < -0.3: bull += 5

    if structure == "uptrend": bull += 15
    elif structure == "downtrend": bear += 15
    else: neutral += 10

    if volume_spike: bear += 10

    raw = {"bull": bull, "neutral": neutral, "bear": bear}
    total = sum(raw.values())
    if total == 0:
        return {"bull":33, "neutral":34, "bear":33}
    probs = {k: int(round(v/total*100)) for k,v in raw.items()}
    s = sum(probs.values())
    if s != 100:
        diff = 100 - s
        kmax = max(probs, key=probs.get)
        probs[kmax] += diff
    return probs

def decidir_accion_matrix(presion_def, probs, regime_guess, weekly_score, persist_baj, persist_alc):
    prob_bear = probs.get("bear",33)
    prob_bull = probs.get("bull",33)
    if prob_bear >= 70 or presion_def >= 80:
        return "VENDER", "0-15%", "BAJISTA", "Alta probabilidad bajista; priorizar preservación."
    if prob_bear >= 55 or presion_def >= 60:
        return "VENDER PARCIAL", "10-30%", "BAJISTA", "Reducir exposición."
    if prob_bull >= 65 and presion_def <= 35 and persist_alc >= PERSIST_MIN:
        return "COMPRAR", "60-80%", "ALCISTA", "Alta probabilidad alcista; acumulación gradual."
    if prob_bull >= 50 and presion_def <= 45:
        return "COMPRAR PARCIAL", "40-60%", "ALCISTA", "Entrada parcial."
    return "OBSERVAR", "30-40%", "NEUTRO", "Contexto mixto; esperar confirmación."

# -----------------------
# Exposure helpers used by backtest
# -----------------------
def parse_exposure(exposicion_str):
    if not exposicion_str:
        return None
    s = str(exposicion_str).replace("%","").strip()
    if "-" in s:
        try:
            a,b = s.split("-")
            a = float(a); b = float(b)
            return (a+b)/2.0/100.0
        except:
            return None
    else:
        try:
            v = float(s)
            return v/100.0
        except:
            return None

def action_default_exposure(action):
    mapping = {
        "VENDER": 0.05,
        "VENDER PARCIAL": 0.2,
        "REDUCIR": 0.25,
        "OBSERVAR": 0.35,
        "COMPRAR PARCIAL": 0.5,
        "COMPRAR": 0.7
    }
    return mapping.get(action.upper(), 0.35)

def compute_drawdown(series):
    peak = -1e9
    maxdd = 0.0
    for v in series:
        if v > peak:
            peak = v
        dd = (peak - v)/peak if peak>0 else 0
        if dd > maxdd:
            maxdd = dd
    return maxdd

# -----------------------
# NEW BACKTEST: run_backtest_with_stops
# -----------------------
def run_backtest_with_stops(historico_rows, price_by_date, out_csv="backtest_results_with_stops.csv"):
    """
    Backtest that simulates position entries according to accion_sugerida and exponesion,
    and manages position with STOP LOSS and TAKE PROFIT rules (structural / regime-based).
    """
    if not historico_rows:
        print("No hay historial para backtest.")
        return None

    capital = 1.0
    in_position = False
    entry_price = None
    entry_capital = None
    entry_presion = None
    entry_exposure = 0.0

    results = []
    capitals = []

    for i in range(len(historico_rows) - 1):
        row = historico_rows[i]
        next_row = historico_rows[i + 1]

        date = row.get("fecha")
        next_date = next_row.get("fecha")

        price_today = price_by_date.get(date)
        price_next = price_by_date.get(next_date)

        if price_today is None or price_next is None:
            # skip days with missing prices
            continue

        presion = parse_float(row.get("presion_defensiva")) or 50
        dom_stable = parse_float(row.get("dominancia_stable")) or 0
        accel = parse_float(row.get("aceleracion")) or 0
        escenario = row.get("escenario_probable") or ""
        accion = (row.get("accion_sugerida") or "").upper()
        exposicion_str = row.get("exposicion_recomendada") or ""

        # ENTRY: if not in position and action requests buy
        if not in_position and accion in ("COMPRAR", "COMPRAR PARCIAL"):
            exposure = parse_exposure(exposicion_str) or action_default_exposure(accion)
            in_position = True
            entry_price = price_today
            entry_capital = capital
            entry_presion = presion
            entry_exposure = exposure

        stop_loss = False
        take_profit = False

        if in_position:
            # compute drawdown/profit relative to entry price
            if entry_price and entry_price > 0:
                ret_since_entry = price_today / entry_price - 1.0
            else:
                ret_since_entry = 0.0

            # STOP LOSS rules
            if presion >= STOP_PRESION:
                stop_loss = True
            if ret_since_entry <= MAX_DRAWDOWN_STOP:
                stop_loss = True
            # structure-based: divergence + negative momentum (simplified)
            if escenario == "BAJISTA" and presion >= 55:
                stop_loss = True

            # TAKE PROFIT rules
            if ret_since_entry >= TP_PROFIT_PCT:
                take_profit = True
            if presion >= (entry_presion + TP_PRESION_DELTA):
                take_profit = True
            if escenario in ("NEUTRO", "BAJISTA") and ret_since_entry > 0:
                # lock partial profits if scenario degraded
                take_profit = True

            # Execution of stop/take: update exposure and position status
            if stop_loss:
                # harsh exit: reduce to minimal exposure
                entry_exposure = 0.05
                in_position = False
            elif take_profit:
                # sell partial: leave reduced exposure
                entry_exposure = min(entry_exposure, 0.30)
                in_position = False

        # If not in_position, exposure is zero or minimal depending on last action
        exposure_today = entry_exposure if in_position else (parse_exposure(exposicion_str) or action_default_exposure(accion) if accion in ("COMPRAR", "COMPRAR PARCIAL") else 0.0)

        # DAILY RETURN (assume not-exposed capital in stable => 0% return)
        daily_return = exposure_today * (price_next / price_today - 1.0)
        capital = capital * (1 + daily_return)

        capitals.append(capital)

        results.append({
            "fecha": next_date,
            "accion": accion,
            "exposure": round(exposure_today, 4),
            "price_today": price_today,
            "price_next": price_next,
            "daily_return": daily_return,
            "capital": capital,
            "stop_loss": stop_loss,
            "take_profit": take_profit
        })

    if not results:
        print("No hubo pasos simulados (precios faltantes).")
        return None

    max_dd = compute_drawdown([r["capital"] for r in results])

    # write csv
    try:
        with open(out_csv, "w", newline="") as f:
            fieldnames = ["fecha","accion","exposure","price_today","price_next","daily_return","capital","stop_loss","take_profit"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in results:
                w.writerow(r)
        print(f"Backtest CSV escrito: {out_csv}")
    except Exception as e:
        print("No se pudo escribir CSV:", e)

    return {
        "capital_start": 1.0,
        "capital_end": results[-1]["capital"],
        "total_return": results[-1]["capital"] - 1.0,
        "max_drawdown": max_dd,
        "steps": len(results)
    }

# -----------------------
# Telegram util
# -----------------------
def send_telegram(text):
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
        print("Error Telegram:", e)
        return False

def telegram_summary(payload, last_action=None, last_escenario=None):
    lines = []
    lines.append(f"📊 <b>Macro v2.4 - {payload.get('fecha')}</b>")
    lines.append(f"Escenario probable: <b>{payload.get('escenario_probable')}</b> (bull {payload.get('prob_bull_pct')}% / neutral {payload.get('prob_neutral_pct')}% / bear {payload.get('prob_bear_pct')}%)")
    lines.append(f"Presion defensiva: {payload.get('presion_defensiva')} | Domin. stable: {payload.get('dominancia_stable')}%")
    lines.append(f"Acción sugerida: <b>{payload.get('accion_sugerida')}</b> | Exposición: {payload.get('exposicion_recomendada')}")
    if last_action and last_action != payload.get('accion_sugerida'):
        lines.append(f"⚠️ Cambio acción: {last_action} → {payload.get('accion_sugerida')}")
    if last_escenario and last_escenario != payload.get('escenario_probable'):
        lines.append(f"⚠️ Cambio escenario: {last_escenario} → {payload.get('escenario_probable')}")
    if payload.get('condiciones_activacion'):
        lines.append("Condiciones: " + payload.get('condiciones_activacion'))
    if payload.get('señales_invalidacion'):
        lines.append("Invalidación: " + payload.get('señales_invalidacion'))
    lines.append(f"Modelo: {payload.get('version_modelo')}")
    return "\n".join(lines)

# -----------------------
# MAIN run (v2.4 logic + call backtest_with_stops when requested)
# -----------------------
def main(run_backtest_flag=False):
    # 1) fetch core data
    cg = fetch_global_safe()
    if not cg:
        print("No hay datos CoinGecko; abortando")
        return

    total_mcap = parse_float(cg.get("total_market_cap", {}).get("usd"))
    market_pct = cg.get("market_cap_percentage", {}) or {}

    dom_btc = parse_float(market_pct.get("btc"))
    dom_usdt = parse_float(market_pct.get("usdt") or market_pct.get("tether"))
    dom_usdc = parse_float(market_pct.get("usd-coin") or market_pct.get("usdc"))

    # total stables best-effort
    stable_keys = ["tether","usdt","usd-coin","usdc","dai","busd","binance-usd","frax","tusd","true-usd"]
    dom_stable = 0.0
    for k in stable_keys:
        v = market_pct.get(k) or market_pct.get(k.upper())
        if v is not None:
            try: dom_stable += float(v)
            except: pass
    dom_stable = round(dom_stable, 4)

    # 2) fetch BTC chart and price change
    btc_chart = fetch_btc_market_chart_days(days=HIST_LIMIT+10)
    btc_prices = []; btc_vols = []
    if btc_chart:
        prices = btc_chart.get("prices", [])
        vols = btc_chart.get("total_volumes", [])
        btc_prices = [p[1] for p in prices]
        btc_vols = [v[1] for v in vols]
    btc_24h = fetch_btc_24h_change()

    # 3) history from sheet
    historico = leer_historico(limit=HIST_LIMIT)

    # 4) external series (optional)
    etf_series = fetch_external_series(ETF_FLOWS_URL) if ETF_FLOWS_URL else {}
    onchain_series = fetch_external_series(ONCHAIN_FLOWS_URL) if ONCHAIN_FLOWS_URL else {}

    # 5) compute metrics
    hist_vars = [r.get("variacion_24h") for r in historico if r.get("variacion_24h") is not None]
    hist_accs = [r.get("aceleracion") for r in historico if r.get("aceleracion") is not None]
    hist_dom_stable = [r.get("dominancia_stable") for r in historico if r.get("dominancia_stable") is not None]
    hist_dom_btc = [r.get("dominancia_btc") for r in historico if r.get("dominancia_btc") is not None]
    hist_scores = [r.get("score_diario") for r in historico if r.get("score_diario") is not None]

    prev_dom = historico[-1].get("dominancia_stable") if historico and historico[-1].get("dominancia_stable") is not None else None
    variacion_24h = ((dom_stable - prev_dom)/prev_dom*100) if prev_dom else 0.0
    prev_var = historico[-1].get("variacion_24h") if historico and historico[-1].get("variacion_24h") is not None else 0.0
    aceleracion = variacion_24h - (prev_var or 0.0)

    dom_series = hist_dom_stable + [dom_stable]
    sma7 = sma(dom_series, 7)
    sma21 = sma(dom_series, 21)
    pendiente_7d = dom_series[-1] - dom_series[-7] if len(dom_series) >= 7 else 0.0

    z_var = zscore(variacion_24h, hist_vars) if hist_vars else 0.0
    z_acc = zscore(aceleracion, hist_accs) if hist_accs else 0.0

    # 6) detect patterns
    divergence = False
    if btc_24h is not None:
        if btc_24h > 0.7 and (z_var > 1.0 or variacion_24h > 1.0):
            divergence = True

    volume_spike = False
    if btc_vols and len(btc_vols) >= 8:
        avg7 = sum(btc_vols[-8:-1])/7
        if btc_vols[-1] > avg7 * 1.8:
            volume_spike = True

    # 7) presion defensiva
    presion_def = 50
    if z_var > 1.5: presion_def += 30
    elif z_var > 0.8: presion_def += 20
    elif z_var < -1.5: presion_def -= 15
    elif z_var < -0.8: presion_def -= 5
    if z_acc > 1.2: presion_def += 15
    elif z_acc < -1.2: presion_def -= 10
    if pendiente_7d > 0.2: presion_def += 10
    elif pendiente_7d < -0.2: presion_def -= 5
    if divergence: presion_def += 15

    weekly_score = int(round(statistics.mean(hist_scores[-6:]))) if hist_scores else 50
    regime_guess = "NEUTRO"
    if weekly_score < TH_DEFENSIVO and sum(1 for s in hist_scores[-5:] if s < TH_DEFENSIVO) >= PERSIST_MIN:
        regime_guess = "DEFENSIVO"
    elif weekly_score < TH_TRANS_BAJISTA:
        regime_guess = "TRANSICION BAJISTA"
    elif weekly_score <= TH_NEUTRO:
        regime_guess = "NEUTRO"
    elif weekly_score <= TH_TRANS_ALCISTA:
        regime_guess = "TRANSICION ALCISTA"
    else:
        regime_guess = "RISK-ON"

    if regime_guess in ["DEFENSIVO","TRANSICION BAJISTA"] and z_var < 0 and z_acc < 0:
        presion_def += 10
    presion_def = int(max(0, min(100, round(presion_def))))

    features = {
        "presion_def": presion_def,
        "divergence": divergence,
        "dom_stable_pend": pendiente_7d,
        "dom_btc_pend": (hist_dom_btc[-1] - hist_dom_btc[-7]) if len(hist_dom_btc) >= 7 else 0,
        "volume_spike": volume_spike,
        "market_structure": "uptrend" if (btc_prices and len(btc_prices)>3 and btc_prices[-1]>btc_prices[-4]) else "sideways"
    }
    probs = score_patterns_to_probabilities(features)

    # 8) decide action
    persist_baj = sum(1 for s in hist_scores[-5:] if s < TH_DEFENSIVO)
    persist_alc = sum(1 for s in hist_scores[-5:] if s > TH_TRANS_ALCISTA)
    accion, exposicion, sesgo, comentario = decidir_accion_matrix(presion_def, probs, regime_guess, weekly_score, persist_baj, persist_alc)

    # 9) conditions & invalidation text
    condiciones = []
    invalidacion = []
    if probs["bull"] >= probs["bear"] and probs["bull"] >= probs["neutral"]:
        condiciones.append("USDT.D en caída sostenida; BTC.D baja; estructura alcista en BTC; volumen confirma acumulación.")
        invalidacion.append("USDT.D rompe al alza o presion_def > 60.")
    if probs["bear"] >= probs["bull"] and probs["bear"] >= probs["neutral"]:
        condiciones.append("Aumento dominancia stable + divergencia precio/flujo + volumen en caídas.")
        invalidacion.append("USDT.D cae sostenidamente y dominancia BTC se reduce.")
    if probs["neutral"] >= probs["bull"] and probs["neutral"] >= probs["bear"]:
        condiciones.append("Dominancias estables; mercado en rango.")
        invalidacion.append("Ruptura con volumen.")

    # 10) build payload
    fecha = now_iso()
    payload = {
        "fecha": fecha,
        "total_market_cap": int(round(total_mcap)) if total_mcap else "",
        "dominancia_btc": round(dom_btc,2) if dom_btc is not None else "",
        "dominancia_usdt": round(dom_usdt,3) if dom_usdt is not None else "",
        "dominancia_usdc": round(dom_usdc,3) if dom_usdc is not None else "",
        "dominancia_stable": round(dom_stable,3),
        "variacion_24h": round(variacion_24h,3),
        "aceleracion": round(aceleracion,3),
        "pendiente_7d": round(pendiente_7d,4),
        "sma_7": round(sma7,3) if sma7 else "",
        "sma_21": round(sma21,3) if sma21 else "",
        "score_diario": int(round((100-presion_def)/2 + 50)),
        "score_semanal": int(weekly_score),
        "presion_defensiva": int(presion_def),
        "divergencia_precio_flujo": "TRUE" if divergence else "FALSE",
        "prob_bull_pct": int(probs["bull"]),
        "prob_neutral_pct": int(probs["neutral"]),
        "prob_bear_pct": int(probs["bear"]),
        "escenario_probable": ("ALCISTA" if probs["bull"]>probs["bear"] and probs["bull"]>probs["neutral"] else ("BAJISTA" if probs["bear"]>probs["bull"] and probs["bear"]>probs["neutral"] else "NEUTRO")),
        "condiciones_activacion": "; ".join(condiciones),
        "señales_invalidacion": "; ".join(invalidacion),
        "estructura_mercado": features["market_structure"],
        "volumen_spike": "TRUE" if volume_spike else "FALSE",
        "accion_sugerida": accion,
        "exposicion_recomendada": exposicion,
        "sesgo_operativo": sesgo,
        "comentario_operativo": comentario,
        "version_modelo": MODEL_VERSION
    }

    # 11) Post / avoid duplicates / telegram
    try:
        if not SHEETBEST_URL:
            print("SHEETBEST_URL no configurada. Payload:")
            print(payload)
        else:
            r_check = safe_get(SHEETBEST_URL)
            sheet_rows = r_check.json()
            last_rows = sheet_rows[-5:] if len(sheet_rows) >= 5 else sheet_rows
            today_found = any(str(row.get("fecha","")).startswith(fecha) for row in last_rows)
            last_row = last_rows[-1] if last_rows else None
            last_action = (last_row.get("accion_sugerida") if last_row else "") or (last_row.get("accion") if last_row else "")
            last_escenario = last_row.get("escenario_probable") if last_row else ""

            action_changed = (last_action != payload["accion_sugerida"])
            scenario_changed = (last_escenario != payload["escenario_probable"])

            if today_found:
                print("Ya existe registro para hoy. No duplicamos.")
                if action_changed or scenario_changed:
                    send_telegram(telegram_summary(payload, last_action=last_action, last_escenario=last_escenario))
                else:
                    print("Sin cambios relevantes.")
            else:
                post_to_sheet(payload)
                print("Registro subido.")
                if presion_def >= 70 or payload["escenario_probable"] == "BAJISTA" or accion.startswith("VENDER"):
                    send_telegram(telegram_summary(payload, last_action=last_action, last_escenario=last_escenario))
    except Exception as e:
        print("Error chequeo/posteo:", e)
        print(payload)

    # 12) optionally run backtest if requested
    if run_backtest_flag:
        print("Ejecutando backtest con stops (flag activo)...")
        # build date->price map from btc_chart
        date_price_map = {}
        if btc_chart:
            for p in btc_chart.get("prices", []):
                ts = int(p[0])//1000
                dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                date_price_map[dt] = p[1]
        bt_summary = run_backtest_with_stops(historico, date_price_map, out_csv="backtest_with_stops.csv")
        print("Backtest summary:", bt_summary)

    # 13) console summary
    print("=== Resumen v2.4 (stops) ===")
    print("fecha:", fecha)
    print("dominancia_stable:", payload["dominancia_stable"])
    print("presion_defensiva:", payload["presion_defensiva"])
    print("escenario_probable:", payload["escenario_probable"], f"(bull {payload['prob_bull_pct']}% neutral {payload['prob_neutral_pct']}% bear {payload['prob_bear_pct']}%)")
    print("accion_sugerida:", payload["accion_sugerida"], "| exposicion:", payload["exposicion_recomendada"])
    print("====================")

# -----------------------
# Entrypoint CLI
# -----------------------
if __name__ == "__main__":
    run_backtest_flag = False
    if len(sys.argv) > 1 and sys.argv[1] in ("--backtest","backtest"):
        run_backtest_flag = True
    if os.getenv("RUN_BACKTEST", "").lower() in ("1","true","yes"):
        run_backtest_flag = True
    main(run_backtest_flag=run_backtest_flag)
