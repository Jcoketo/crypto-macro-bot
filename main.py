#!/usr/bin/env python3
# main.py - v2.4-stops-decision + Telegram siempre (envía resumen en cada run)
# Ejecutar: python main.py
# Backtest (opcional): python main.py --backtest

import requests
from datetime import datetime, timezone
import os
import math
import statistics
import csv
import sys

# -----------------------
# CONFIG / SECRETS
# -----------------------
SHEETBEST_URL = os.getenv("SHEETBEST_URL")            # REQUIRED
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")          # OPTIONAL
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # OPTIONAL

ETF_FLOWS_URL = os.getenv("ETF_FLOWS_URL")            # OPTIONAL
ONCHAIN_FLOWS_URL = os.getenv("ONCHAIN_FLOWS_URL")    # OPTIONAL

CG_GLOBAL = "https://api.coingecko.com/api/v3/global"
CG_SIMPLE_BTC = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
CG_BTC_MARKET_CHART = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days={days}"

MODEL_VERSION = "v2.4-stops-decision"

HIST_LIMIT = 120
Z_WINDOW = 14
PERSIST_MIN = 3

TH_DEFENSIVO = 30
TH_TRANS_BAJISTA = 45
TH_NEUTRO = 55
TH_TRANS_ALCISTA = 70

MAX_DRAWDOWN_STOP = -0.18
TP_PROFIT_PCT = 0.25
TP_PRESION_DELTA = 20
STOP_PRESION = 75

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
# External fetcher (optional)
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
# Scoring & matrix (unchanged core)
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
# Exposure helpers
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
# BACKTEST with stops (kept for research, optional)
# -----------------------
def run_backtest_with_stops(historico_rows, price_by_date, out_csv="backtest_results_with_stops.csv"):
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
            continue

        presion = parse_float(row.get("presion_defensiva")) or 50
        escenario = row.get("escenario_probable") or ""
        accion = (row.get("accion_sugerida") or "").upper()
        exposicion_str = row.get("exposicion_recomendada") or ""

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
            ret_since_entry = price_today / entry_price - 1.0 if entry_price else 0.0

            if presion >= STOP_PRESION:
                stop_loss = True
            if ret_since_entry <= MAX_DRAWDOWN_STOP:
                stop_loss = True
            if escenario == "BAJISTA" and presion >= 55:
                stop_loss = True

            if ret_since_entry >= TP_PROFIT_PCT:
                take_profit = True
            if presion >= (entry_presion + TP_PRESION_DELTA):
                take_profit = True
            if escenario in ("NEUTRO", "BAJISTA") and ret_since_entry > 0:
                take_profit = True

            if stop_loss:
                entry_exposure = 0.05
                in_position = False
            elif take_profit:
                entry_exposure = min(entry_exposure, 0.30)
                in_position = False

        exposure_today = entry_exposure if in_position else (parse_exposure(exposicion_str) or action_default_exposure(accion) if accion in ("COMPRAR", "COMPRAR PARCIAL") else 0.0)

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
# --- NEW: motor decisional anticipado (SIEMPRE se ejecuta)
# -----------------------
def motor_decision_anticipada(historico, actual):
    """
    historico: lista de filas previas (orden cronológico ascendente)
    actual: dict con llaves mínimas:
      'presion_defensiva', 'aceleracion', 'dominancia_stable', 'dominancia_btc', 'score_semanal'
    Devuelve dict con keys para agregar al payload.
    """
    presion = int(actual.get("presion_defensiva") or 50)
    accel = float(actual.get("aceleracion") or 0.0)
    dom_stable = float(actual.get("dominancia_stable") or 0.0)
    dom_btc = float(actual.get("dominancia_btc") or 0.0)
    weekly = int(actual.get("score_semanal") or 50)

    # base probabilidades (heurístico, combinatorio)
    prob_bull = 0; prob_neutral = 0; prob_bear = 0
    # presion domina
    if presion >= 75:
        prob_bear += 50
    elif presion >= 60:
        prob_bear += 30
    elif presion >= 45:
        prob_bear += 10
    else:
        prob_bull += 10

    # aceleracion & dirección
    if accel > 1.5:
        prob_bear += 20
    elif accel < -1.0:
        prob_bull += 15
    elif abs(accel) < 0.3:
        prob_neutral += 10

    # dominancia trend recent (pendiente simple)
    dom_series = [r.get("dominancia_stable") for r in historico if r.get("dominancia_stable") is not None]
    if dom_series and len(dom_series) >= 3:
        pend = (dom_stable - dom_series[-3]) / 1.0
        if pend > 0.3:
            prob_bear += 15
        elif pend < -0.3:
            prob_bull += 15

    # BTC dominance tilt
    dom_btc_series = [r.get("dominancia_btc") for r in historico if r.get("dominancia_btc") is not None]
    if dom_btc_series and len(dom_btc_series) >= 3:
        btc_pend = dom_btc - dom_btc_series[-3]
        if btc_pend > 0.5:
            prob_bear += 8
        elif btc_pend < -0.5:
            prob_bull += 5

    # weekly score influence (smoothing)
    if weekly <= TH_DEFENSIVO:
        prob_bear += 10
    elif weekly >= TH_TRANS_ALCISTA:
        prob_bull += 8
    else:
        prob_neutral += 5

    # normalize to percentages
    raw = {"bull": prob_bull, "neutral": prob_neutral, "bear": prob_bear}
    s = sum(raw.values())
    if s == 0:
        probs = {"bull":33, "neutral":34, "bear":33}
    else:
        probs = {k: int(round(v/s*100)) for k,v in raw.items()}
        # adjust rounding
        diff = 100 - sum(probs.values())
        if diff != 0:
            kmax = max(probs, key=probs.get)
            probs[kmax] += diff

    # escenario probable
    if probs["bull"] > probs["bear"] and probs["bull"] > probs["neutral"]:
        escenario = "ALCISTA"
    elif probs["bear"] > probs["bull"] and probs["bear"] > probs["neutral"]:
        escenario = "BAJISTA"
    else:
        escenario = "NEUTRO"

    # accion sugerida (simple mapping)
    accion = "OBSERVAR"
    expos = "30-40%"
    sesgo = "NEUTRO"
    if probs["bear"] >= 70 or presion >= 80:
        accion = "VENDER"
        expos = "0-15%"
        sesgo = "BAJISTA"
    elif probs["bear"] >= 55 or presion >= 60:
        accion = "VENDER PARCIAL"
        expos = "10-30%"
        sesgo = "BAJISTA"
    elif probs["bull"] >= 65 and presion <= 35:
        accion = "COMPRAR"
        expos = "60-80%"
        sesgo = "ALCISTA"
    elif probs["bull"] >= 50 and presion <= 45:
        accion = "COMPRAR PARCIAL"
        expos = "40-60%"
        sesgo = "ALCISTA"

    # stops & TPs (probables) - boolean flags
    stop_loss = False
    take_profit = False

    # stop: regime invalidation OR drawdown heuristics
    if presion >= STOP_PRESION:
        stop_loss = True
    # acceleration spike combined with high presion
    if accel > 1.2 and presion > 60:
        stop_loss = True
    # take profit: presion sobe relativo al historico inmediato
    last_pres = historico[-1].get("presion_defensiva") if historico and historico[-1].get("presion_defensiva") is not None else presion
    if presion - (last_pres or presion) >= TP_PRESION_DELTA:
        take_profit = True

    comentario = f"Escenario {escenario}. Probabilidades BULL {probs['bull']}% | NEUT {probs['neutral']}% | BEAR {probs['bear']}%. "
    comentario += "Stop loss probable. " if stop_loss else ""
    comentario += "Take profit probable. " if take_profit else ""
    comentario += "Revisar condiciones y persistencia."

    return {
        "escenario_probable": escenario,
        "prob_bull_pct": int(probs["bull"]),
        "prob_neutral_pct": int(probs["neutral"]),
        "prob_bear_pct": int(probs["bear"]),
        "accion_sugerida": accion,
        "exposicion_recomendada": expos,
        "sesgo_operativo": sesgo,
        "stop_loss_probable": "SI" if stop_loss else "NO",
        "take_profit_probable": "SI" if take_profit else "NO",
        "comentario_estrategico": comentario
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

    # Título
    lines.append(f"📰 <b>NOTICIAS PARA {payload.get('fecha')}</b>")
    lines.append("")

    lines.append(
        f"Escenario probable: <b>{payload.get('escenario_probable')}</b> "
        f"(bull {payload.get('prob_bull_pct')}% / "
        f"neutral {payload.get('prob_neutral_pct')}% / "
        f"bear {payload.get('prob_bear_pct')}%)"
    )

    # Línea 1: presión defensiva
    lines.append(f"Presión defensiva: {payload.get('presion_defensiva')}")

    # Línea 2: dominancia stable + variación 24h
    var_24h = payload.get("variacion_24h")
    sign = "+" if isinstance(var_24h, (int, float)) and var_24h > 0 else ""

    dom_stable = payload.get("dominancia_stable")

    # Semáforo de dominancia stable
    if isinstance(dom_stable, (int, float)):
        if dom_stable < 8:
            dom_text = f"<span style='color:green'><b>{dom_stable}%</b></span>"
        elif dom_stable <= 9:
            dom_text = f"🟡 <b>{dom_stable}%</b>"
        else:
            dom_text = f"🚨 <span style='color:red'><b>{dom_stable}%</b></span>"
    else:
        dom_text = f"{dom_stable}%"

    lines.append(
        f"Domin. stable: {dom_text} | "
        f"Var. 24hs: {sign}{var_24h}%"
    )

    # Acción y exposición
    lines.append(
        f"Acción sugerida: <b>{payload.get('accion_sugerida')}</b> | "
        f"Exposición: {payload.get('exposicion_recomendada')}"
    )

    # Stops
    lines.append(
        f"Stop probable: {payload.get('stop_loss_probable')} | "
        f"TP probable: {payload.get('take_profit_probable')}"
    )

    # Cambios de acción
    if last_action and last_action != payload.get('accion_sugerida'):
        lines.append(
            f"⚠️ Cambio acción: {last_action} → {payload.get('accion_sugerida')}"
        )

    # Cambios de escenario
    if last_escenario and last_escenario != payload.get('escenario_probable'):
        lines.append(
            f"⚠️ Cambio escenario: {last_escenario} → {payload.get('escenario_probable')}"
        )

    # Comentario operativo
    if payload.get('comentario_operativo'):
        lines.append(payload.get('comentario_operativo'))

    return "\n".join(lines)


# -----------------------
# MAIN orchestration
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

    stable_keys = ["tether","usdt","usd-coin","usdc","dai","busd","binance-usd","frax","tusd","true-usd"]
    dom_stable = 0.0
    for k in stable_keys:
        v = market_pct.get(k) or market_pct.get(k.upper())
        if v is not None:
            try: dom_stable += float(v)
            except: pass
    dom_stable = round(dom_stable, 4)

    # fetch BTC series & 24h change
    btc_chart = fetch_btc_market_chart_days(days=HIST_LIMIT+10)
    btc_prices = []; btc_vols = []
    if btc_chart:
        prices = btc_chart.get("prices", [])
        vols = btc_chart.get("total_volumes", [])
        btc_prices = [p[1] for p in prices]
        btc_vols = [v[1] for v in vols]
    btc_24h = fetch_btc_24h_change()

    # read history
    historico = leer_historico(limit=HIST_LIMIT)

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

    # detect patterns
    divergence = False
    if btc_24h is not None:
        if btc_24h > 0.7 and (z_var > 1.0 or variacion_24h > 1.0):
            divergence = True

    volume_spike = False
    if btc_vols and len(btc_vols) >= 8:
        avg7 = sum(btc_vols[-8:-1])/7
        if btc_vols[-1] > avg7 * 1.8:
            volume_spike = True

    # presion defensiva
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

    # decide action base
    persist_baj = sum(1 for s in hist_scores[-5:] if s < TH_DEFENSIVO)
    persist_alc = sum(1 for s in hist_scores[-5:] if s > TH_TRANS_ALCISTA)
    accion_base, expos_base, sesgo_base, comentario_base = decidir_accion_matrix(presion_def, probs, regime_guess, weekly_score, persist_baj, persist_alc)

    # ---------- RUN motor decisional OBLIGATORIO ----------
    actual_context = {
        "presion_defensiva": presion_def,
        "aceleracion": aceleracion,
        "dominancia_stable": dom_stable,
        "dominancia_btc": dom_btc,
        "score_semanal": weekly_score
    }
    decision = motor_decision_anticipada(historico, actual_context)
    # decision contains keys: escenario_probable, prob_bull_pct, prob_neutral_pct, prob_bear_pct,
    # accion_sugerida, exposicion_recomendada, sesgo_operativo, stop_loss_probable, take_profit_probable, comentario_estrategico

    # Build payload merging base + decision (decision overrides base where appropriate)
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
        "prob_bull_pct": int(decision.get("prob_bull_pct")),
        "prob_neutral_pct": int(decision.get("prob_neutral_pct")),
        "prob_bear_pct": int(decision.get("prob_bear_pct")),
        "escenario_probable": decision.get("escenario_probable"),
        "condiciones_activacion": "; ".join([c for c in [", ".join([str(k) for k in []])] if c]) ,  # placeholder
        "señales_invalidacion": "; ".join([c for c in []]),
        "estructura_mercado": features["market_structure"],
        "volumen_spike": "TRUE" if volume_spike else "FALSE",
        # from decision
        "accion_sugerida": decision.get("accion_sugerida"),
        "exposicion_recomendada": decision.get("exposicion_recomendada"),
        "sesgo_operativo": decision.get("sesgo_operativo"),
        "comentario_operativo": decision.get("comentario_estrategico"),
        "stop_loss_probable": decision.get("stop_loss_probable"),
        "take_profit_probable": decision.get("take_profit_probable"),
        "version_modelo": MODEL_VERSION
    }

    # --- ENVÍO TELEGRAM SIEMPRE (inmediatamente después de construir `payload`) ---
    last_action_ctx = None
    last_escenario_ctx = None
    try:
        if SHEETBEST_URL:
            r_tmp = safe_get(SHEETBEST_URL)
            rows_tmp = r_tmp.json()
            last_row_tmp = rows_tmp[-1] if rows_tmp else None
            if last_row_tmp:
                last_action_ctx = (last_row_tmp.get("accion_sugerida") or last_row_tmp.get("accion") or "").strip()
                last_escenario_ctx = (last_row_tmp.get("escenario_probable") or "").strip()
    except Exception as e:
        print("Warning: no se pudo leer último registro para contexto de Telegram:", e)

    try:
        msg = telegram_summary(payload, last_action=last_action_ctx, last_escenario=last_escenario_ctx)
        sent = send_telegram(msg)
        if sent:
            print("✅ Telegram: notificación enviada (run).")
        else:
            print("ℹ️ Telegram: no configurado o envío omitido.")
    except Exception as e:
        print("Error enviando Telegram (no crítico):", e)
    # --- FIN ENVÍO TELEGRAM SIEMPRE ---

    # 11) Post / avoid duplicates / telegram (post-send)
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
                if presion_def >= 70 or payload["escenario_probable"] == "BAJISTA" or payload["accion_sugerida"].startswith("VENDER"):
                    send_telegram(telegram_summary(payload, last_action=last_action, last_escenario=last_escenario))
    except Exception as e:
        print("Error chequeo/posteo:", e)
        print(payload)

    # Optional backtest run (for research)
    if run_backtest_flag:
        print("Ejecutando backtest con stops (flag activo)...")
        date_price_map = {}
        if btc_chart:
            for p in btc_chart.get("prices", []):
                ts = int(p[0])//1000
                dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                date_price_map[dt] = p[1]
        bt_summary = run_backtest_with_stops(historico, date_price_map, out_csv="backtest_with_stops.csv")
        print("Backtest summary:", bt_summary)

    # final console summary
    print("=== Resumen v2.4 ===")
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
