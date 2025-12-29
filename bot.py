# bot.py
import requests
from datetime import datetime, timezone
import os
import math

# ================= CONFIG =================
SHEETBEST_URL = os.getenv("SHEETBEST_URL")  # secret en GitHub
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # opcional
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # opcional
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
# ==========================================

# Aliases para stablecoins que podrían aparecer en market_cap_percentage
STABLE_ALIASES = [
    ["usdt", "tether"],       # Tether / USDT
    ["usdc", "usd-coin"],     # USDC
    ["dai"],                  # DAI
    ["busd", "binance-usd"],  # BUSD
    ["tusd", "true-usd"],     # TUSD
    ["frax"]                  # FRAX
]

def safe_request_get(url, **kwargs):
    r = requests.get(url, timeout=15, **kwargs)
    r.raise_for_status()
    return r

def get_global_data():
    r = safe_request_get(COINGECKO_GLOBAL)
    return r.json()["data"]

def get_sheet_rows():
    if not SHEETBEST_URL:
        raise RuntimeError("SHEETBEST_URL no configurada")
    r = safe_request_get(SHEETBEST_URL)
    return r.json()

def stable_value_from_market_caps(market_caps):
    """
    market_caps: dict from CoinGecko global['market_cap_percentage']
    sum values for stable aliases, tolerant to different key names.
    """
    total = 0.0
    if not isinstance(market_caps, dict):
        return 0.0
    for aliases in STABLE_ALIASES:
        found = False
        for a in aliases:
            if a in market_caps:
                try:
                    total += float(market_caps[a])
                except:
                    pass
                found = True
                break
        # if none found, try symbol uppercase maybe
        if not found:
            for a in aliases:
                key_upper = a.upper()
                if key_upper in market_caps:
                    try:
                        total += float(market_caps[key_upper])
                    except:
                        pass
                    break
    return total

# ----- Score logic -----
def calcular_score(dom_stable, variacion, aceleracion, dom_btc):
    score = 50

    # Dominancia stable (≈ 40% del peso)
    if dom_stable > 12:
        score += 20
    elif dom_stable < 9:
        score -= 20

    # Variación 24h (≈ 30%)
    if variacion >= 5:
        score += 15
    elif variacion <= -5:
        score -= 15
    elif variacion >= 3:
        score += 8
    elif variacion <= -3:
        score -= 8

    # Aceleración (≈ 20%)
    if aceleracion >= 1.5:
        score += 10
    elif aceleracion <= -1.5:
        score -= 10

    # Dominancia BTC (≈ 10%)
    if dom_btc > 46:
        score += 5
    elif dom_btc < 42:
        score -= 5

    score = max(0, min(100, round(score)))
    return score

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

def interpretar_por_score(score):
    if score >= 81:
        return "Flujo defensivo extremo hacia stablecoins. Mercado en stress, priorizar preservación de capital."
    if score >= 61:
        return "Aumento relevante de stablecoins con sesgo defensivo. Riesgo de correcciones."
    if score >= 41:
        return "Mercado equilibrado, fase de espera y consolidación."
    if score >= 21:
        return "Salida gradual de stablecoins. Mejora en apetito por riesgo, entradas selectivas posibles."
    return "Salida fuerte de stablecoins. Entorno claramente risk-on."

# ----- Telegram -----
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
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

# ----- Weekly score helper -----
def calcular_weekly_score_con_registros(registros, current_dom, current_var, current_acc, current_btc):
    """
    registros: lista de filas previas (orden cronológico)
    Armamos una ventana de hasta 6 previos + current para tener 7 valores (o lo que haya).
    Calculamos promedios y devolvemos score semanal (int) o None.
    """
    valores_dom = []
    valores_var = []
    valores_acc = []
    valores_btc = []

    # tomamos hasta 6 últimos registros previos
    prev_list = registros[-6:] if len(registros) >= 6 else registros[:]
    for r in prev_list:
        try:
            valores_dom.append(float(r.get("dominancia_stablecoins", 0)))
            valores_var.append(float(r.get("variacion_stablecoins_pct", 0)))
            valores_acc.append(float(r.get("aceleracion_stablecoins", 0)))
            valores_btc.append(float(r.get("dominancia_bitcoin", 0)))
        except:
            continue

    # agregamos el valor actual
    valores_dom.append(float(current_dom))
    valores_var.append(float(current_var))
    valores_acc.append(float(current_acc))
    valores_btc.append(float(current_btc))

    if not valores_dom:
        return None

    avg_dom = sum(valores_dom) / len(valores_dom)
    avg_var = sum(valores_var) / len(valores_var)
    avg_acc = sum(valores_acc) / len(valores_acc)
    avg_btc = sum(valores_btc) / len(valores_btc)

    return calcular_score(avg_dom, avg_var, avg_acc, avg_btc)

# ----- MAIN -----
def main():
    fecha_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        datos = get_global_data()
    except Exception as e:
        print("Error obteniendo datos CoinGecko:", e)
        return

    total_mcap = datos.get("total_market_cap", {}).get("usd", 0)
    market_caps_pct = datos.get("market_cap_percentage", {})

    dom_btc = market_caps_pct.get("btc", 0)
    dom_stable = stable_value_from_market_caps(market_caps_pct)

    # obtener registros previos desde sheet.best
    try:
        registros = get_sheet_rows()  # lista de dict
    except Exception as e:
        print("Error obteniendo registros de Sheet.best:", e)
        registros = []

    # variación vs último registro
    variacion_pct = 0.0
    if registros and len(registros) >= 1:
        try:
            prev_dom = float(registros[-1].get("dominancia_stablecoins", 0))
            if prev_dom != 0:
                variacion_pct = ((dom_stable - prev_dom) / prev_dom) * 100
            else:
                variacion_pct = 0.0
        except Exception:
            variacion_pct = 0.0

    # aceleración = variacion_actual - variacion_previa
    aceleracion = 0.0
    if registros and len(registros) >= 2:
        try:
            prev_var = float(registros[-1].get("variacion_stablecoins_pct", 0))
            aceleracion = variacion_pct - prev_var
        except:
            aceleracion = 0.0

    # score diario
    score_macro = calcular_score(dom_stable, variacion_pct, aceleracion, dom_btc)
    regimen = obtener_regimen(score_macro)
    interpretacion = interpretar_por_score(score_macro)

    # weekly score (promedia hasta 6 previos + hoy)
    weekly_score = calcular_weekly_score_con_registros(registros, dom_stable, variacion_pct, aceleracion, dom_btc)
    weekly_score_val = int(weekly_score) if weekly_score is not None else ""

    # alerta logic (simple)
    alerta_text = ""
    send_alert = False

    if score_macro >= 81:
        alerta_text = f"ALERTA: Score {score_macro} (RISK-OFF). {interpretacion}"
        send_alert = True
    elif score_macro <= 20:
        alerta_text = f"ALERTA: Score {score_macro} (RISK-ON FUERTE). {interpretacion}"
        send_alert = True

    # cambio de regimen respecto a último
    if registros and len(registros) >= 1:
        last_regimen = registros[-1].get("regimen", "")
        if last_regimen and last_regimen != regimen:
            alerta_text = f"CAMBIO DE RÉGIMEN: {last_regimen} → {regimen}. Score: {score_macro}"
            send_alert = True

    # alerta semanal extrema
    if weekly_score is not None and weekly_score >= 81:
        alerta_text = f"ALERTA SEMANAL: weekly_score {int(weekly_score)} indica stress sostenido."
        send_alert = True

    payload = {
        "fecha": fecha_iso,
        "dominancia_stablecoins": round(dom_stable, 2),
        "variacion_stablecoins_pct": round(variacion_pct, 2),
        "aceleracion_stablecoins": round(aceleracion, 2),
        "dominancia_bitcoin": round(dom_btc, 2),
        "capitalizacion_total": int(round(total_mcap, 0)),
        "score_macro": int(score_macro),
        "regimen": regimen,
        "interpretacion": interpretacion,
        "weekly_score": weekly_score_val,
        "alerta": alerta_text if send_alert else ""
    }

    # enviar a sheet.best
    try:
        r = requests.post(SHEETBEST_URL, json=payload, timeout=15)
        r.raise_for_status()
        print("✅ Registro subido a Sheet.best")
    except Exception as e:
        print("Error subiendo a Sheet.best:", e)
        try:
            print("Respuesta:", r.text)
        except:
            pass

    # enviar alerta por Telegram si corresponde
    if send_alert and alerta_text:
        ok = send_telegram(alerta_text)
        if ok:
            print("✅ Alerta enviada por Telegram")
        else:
            print("⚠️ Falló envío de Telegram (revisar TELEGRAM_TOKEN/CHAT_ID)")

if __name__ == "__main__":
    main()
