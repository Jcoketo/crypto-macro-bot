# Crypto Macro Bot — README y Manual operativo (v2.4)

## Workflow / Cron recomendado

El workflow de GitHub Actions debe ejecutar `main.py` una vez al día a las `00:00 UTC` (cron `0 0 * * *`). 
Esto te da un **snapshot diario** que incluye la sesión completa de EE. UU. y reacciones iniciales de Asia.

## Columnas que escribe el bot (asegurate que existan o que el sheet acepte columnas nuevas)

El bot escribe las columnas siguientes (nombres exactos recomendados):

* `fecha`
* `total_market_cap` Capitalización total cripto
* `dominancia_btc` Liderazgo BTC
* `dominancia_usdt`
* `dominancia_usdc`
* `dominancia_stable` Capital defensivo
* `variacion_24h` Capital defensivo
* `aceleracion` Cambio del cambio (momentum)
* `pendiente_7d` Dirección semanal
* `sma_7`, `sma_21` Media corta, Media larga
* `score_diario` Riesgo inmediato
* `score_semanal` Riesgo estructural
* `presion_defensiva`
* `divergencia_precio_flujo`
* `prob_bull_pct`
* `prob_neutral_pct`
* `prob_bear_pct`
* `escenario_probable`
* `condiciones_activacion`
* `señales_invalidacion`
* `estructura_mercado`
* `volumen_spike`
* `accion_sugerida` COMPRAR / VENDER / MANTENER
* `exposicion_recomendada`
* `sesgo_operativo`
* `comentario_operativo`
* `version_modelo`

## Resumen ejecutivo — Qué hace el bot

* Analiza dominancias (BTC, USDT, USDC, total stablecoins)
* Detecta divergencias precio/flujo y rupturas de dominancia
* Calcula `presion_defensiva` (0–100)
* Estima probabilidades para 3 escenarios: ALCISTA / NEUTRO / BAJISTA
* Sugiere `accion_sugerida` y `exposicion_recomendada` según una matriz de decisión
* Escribe todo en Sheet.best y notifica por Telegram cambios relevantes

## Lectura diaria (Checklist rápido)

1. Abrí la fila más reciente en el sheet.
2. Verificá `fecha` y `version_modelo`.
3. Mirá `escenario_probable` y las tres probabilidades (`prob_bull_pct`, `prob_neutral_pct`, `prob_bear_pct`).
4. Mirá `presion_defensiva`:
   * > 70: alto riesgo defensivo
   * 55–70: riesgo relevante
   * 30–45: moderado
   * <30: entorno favorable
5. Revisa `accion_sugerida` y `exposicion_recomendada`.
6. Verifica `condiciones_activacion` y `señales_invalidacion` para saber qué vigilar.

## Matriz de decisión (resumida)

| Prob/Bloque                         | Condición típica |          Acción sugerida | Exposición recomendada |
| ----------------------------------- | ---------------- | -----------------------: | ---------------------: |
| Prob_bear >= 70 o presion_def >= 80 | Riesgo muy alto  |                   VENDER |                  0–15% |
| Prob_bear 55–70 o presion_def 60–79 | Riesgo alto      | VENDER PARCIAL / REDUCIR |                 10–30% |
| Prob_bull >= 65 y presion_def <= 35 | Favorable        |                  COMPRAR |                 60–80% |
| Prob_bull 50–64                     | Moderado         |          COMPRAR PARCIAL |                 40–60% |
| Resto                               | Indecisión       |                 OBSERVAR |                 30–40% |

> Nota: si `score_semanal` < 30, penalizar compras (usar exposición más baja).

## Interpretación de columnas clave (qué mirar y por qué)

* `dominancia_stable`: termómetro de liquidez en espera. Subidas sostenidas → aumento del cash => riesgo.
* `variacion_24h` y `aceleracion`: indican rapidez y fuerza. Aceleración positiva en subida de stables = señal adelantada de fuga al cash.
* `pendiente_7d` y `sma_7/21`: confirman tendencias de mediano plazo en flujo.
* `divergencia_precio_flujo`: si TRUE, price action no refleja entradas/salidas de capital — suele preceder giros.
* `presion_defensiva`: síntesis numérica usada para asignar probabilidad y accionar la matriz.

## Reglas de activación e invalidación (ejemplos)

* **Activación Bajista:** presion_def > 70 + divergencia TRUE + volumen spike en caídas.
* **Invalidación Bajista:** presion_def cae por debajo de 45 por 3 días consecutivos y USDT.D rompe a la baja.
* **Activación Alcista:** dominancia_stable en caída sostenida (pendiente negativa) + BTC.D bajando + volumen en soportes.
* **Invalidación Alcista:** dominancia_stable sube y presion_def vuelve a >55.

## 🧮 **Matriz de Decisión Operativa**

| Regimen          | Score Semanal    |      Acción sugerida      |
| ---------------- | ---------------: | ------------------------: |
| DEFENSIVO        |       < 30       |  VENDER PARCIAL / VENDER  |
| TRANS. BAJISTA   |      30 - 45     |         OBSERVAR          |           
| NEUTRO           |      45 - 55     |         MANTENER          |             
| TRANS. ALCISTA   |      55 - 70     |     COMPRAR PARCIAL       |
| RISK-ON          |       > 70       |         COMPRAR           |         

## 🟥 1️⃣ STOP LOSS SUGERIDO (Stop Macro)
📌 Condición
Dominancia de stablecoins subiendo + score bajando durante 2 días consecutivos

🔴 ¿Qué está detectando?
Que el capital está migrando hacia modo defensivo, incluso si el precio aún no cayó con fuerza.
En otras palabras:
El dinero sale de activos de riesgo
Los rebotes pierden calidad
Aumenta la probabilidad de continuidad bajista

🔍 Señales que lo activan
Stablecoins empiezan a subir
El score de riesgo empieza a caer
El patrón se repite al menos 2 días seguidos

📊 Columnas que se analizan
  Columna	Interpretación
  dominancia_stable	¿sube día a día?
  variacion_24h	¿positiva 2 días seguidos?
  score_diario	¿desciende de forma continua?
  score_semanal	¿confirma debilidad estructural?
🧠 Ejemplo práctico
  Día	       Dominancia Stable	Score
  Lunes	            8.2%	        62
  Martes	          8.6%	        55
  Miércoles	        9.1%	        48

Aunque el precio todavía no haya caído fuerte, el sistema indica:

⚠️ Cambio de contexto
⚠️ Riesgo macro creciente
🟥 STOP MACRO SUGERIDO

❗ Qué NO significa
❌ Vender en pánico
❌ Cerrar todo de golpe

✅ Qué SÍ significa
Reducir exposición
Ajustar stops reales
Evitar nuevas compras

🟡 2️⃣ TAKE PROFIT PARCIAL (Protección de Ganancias)
📌 Condición
Score alto (>75) + aceleración negativa

🟢 ¿Qué está detectando?
Que el mercado sigue fuerte, pero el impulso comienza a agotarse.
No es una señal bajista todavía.
Es una señal de posible techo intermedio o consolidación.

📊 Columnas que se analizan
  Columna	Interpretación
  score_diario	Muy alto (>75)
  score_semanal	Optimismo sostenido
  aceleracion	Pasa a negativa
  pendiente_7d	Se aplana o cae
🧠 Ejemplo
Día	      Score	 Aceleración
Lunes	      72	    +1.4
Martes	    78	    +0.6
Miércoles	  81	    -0.3

Resultado:
🟡 Tomar ganancias parciales
🟡 Proteger beneficios
🟡 Prepararse para rango o corrección

Contextos típicos
Final de impulsos
Techos intermedios
Distribución lenta

🟥 3️⃣ SALIDA TOTAL (Cambio de Régimen)
📌 Condición
Cambio de régimen: RISK-ON → DEFENSIVO
Este es el evento más fuerte del sistema.

🔴 ¿Qué significa un cambio de régimen?
Que la estructura macro del mercado cambió, no solo el movimiento diario.
Señales típicas:
  Score semanal cae con fuerza
  Persistencia bajista confirmada
  Dominancia de stablecoins se acelera

👉 Ya no es ruido
👉 Es un nuevo entorno de mercado

📊 Columnas clave
  Columna	Señal
  regimen_macro	Cambio a DEFENSIVO
  score_semanal	< 30
  persistencia_bajista	≥ 3
  dominancia_stable	> 12%
  
🧠 Interpretación correcta
No es:
“mañana el precio se desploma”
Es:
“las probabilidades ahora favorecen un entorno bajista”
En este escenario:
Mantener exposición es ir contra el contexto
El sistema sugiere salida total o reducción fuerte

🧠 RESUMEN OPERATIVO RÁPIDO
Señal	Acción sugerida
Stable ↑ + Score ↓	Precaución
Stable ↑ 2 días	Riesgo real
Score alto + desacelera	Tomar ganancias
Cambio de régimen	Salir

## Ejemplo de uso diario

1. 21:00 ART / 00:00 UTC la Action postea la fila del día.
2. Abrís Sheet, mirás `escenario_probable` y `accion_sugerida`.
3. Si `accion_sugerida` es VENDER o VENDER PARCIAL, revisá stops y liquidez del mercado antes de ejecutar.
4. Si COMPRAR PARCIAL, definí tamaños escalonados y stops técnicos (por ejemplo, 1–2 ATR debajo del soporte).

## Cómo reaccionar a una alerta de Telegram

* Mensaje contiene: escenario, presion_defensiva, acción sugerida, condiciones de activación/invalidación.
* Validar en Sheet; revisar contexto de noticias (macro, regulación) y volumen antes de ejecutar.
