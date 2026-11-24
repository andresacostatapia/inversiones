# CDF Plus · Copiloto de Trading CFD para XTB xStation

Aplicación web local (Streamlit) de **apoyo a la toma de decisiones** para operar CFDs en
**xStation de XTB** de forma semiautomática: la app analiza el mercado, propone la operación
y calcula el lotaje. **La orden la ejecutas tú manualmente** en xStation.

Monitorea en paralelo 6 activos:

| XTB (xStation) | Activo | Twelve Data (plan gratuito) | yfinance (fallback) |
|---|---|---|---|
| `GOLD`   | Oro spot      | `XAU/USD`                | `GC=F`  |
| `SILVER` | Plata spot    | `XAG/USD`                | `SI=F`  |
| `US500`  | S&P 500       | `SPY` × 10 (o `SPX`)     | `ES=F`  |
| `US100`  | Nasdaq 100    | `QQQ` × 41 (o `NDX`)     | `NQ=F`  |
| `US30`   | Dow Jones 30  | `DIA` × 100 (o `DJI`)    | `YM=F`  |
| `US2000` | Russell 2000  | `IWM` × 10.1 (o `RUT`)   | `RTY=F` |

---

## 1. Instalación

Requisitos: Python 3.11 o superior.

```bash
cd cdf-plus
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configuración (opcional)

```bash
cp .env.example .env      # en Windows: copy .env.example .env
```

| Variable | Para qué sirve |
|---|---|
| `TWELVE_DATA_API_KEY` | Datos de Twelve Data. **Sin ella la app usa yfinance automáticamente.** |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alertas por Telegram cuando un score supera el umbral. |

## 3. Ejecución

```bash
streamlit run app.py
```

Se abre en `http://localhost:8501`.

---

## 4. Flujo de uso recomendado

1. **Barra lateral**: indica tu capital (USD), % de riesgo por operación y los multiplicadores
   de ATR (por defecto SL = 1.5×ATR y TP = 3.0×ATR → ratio 1:2).
2. **Verifica las especificaciones de contrato** (barra lateral → *Especificaciones de contrato*):
   USD por punto con 1.0 lote. Valores por defecto: GOLD 100, SILVER 5 000, US500 50, US100 20,
   US30 5, US2000 50. XTB puede cambiarlos según tu entidad: compruébalos en xStation
   (clic derecho en el instrumento → *Información del instrumento*). **Un valor incorrecto
   produce un lotaje incorrecto.**
3. **Calibra el offset** de cada activo: escribe el precio que ves ahora en xStation y pulsa
   *Calibrar*. La app guarda `offset = precio_xtb − precio_feed` y traduce Entrada, SL y TP al
   precio exacto de xStation. Los offsets se guardan en `.state/offsets.json`.
4. Revisa el **Escáner de mercado** (precio calibrado, variación, sesgo 15M/1H/4H, ATR, score).
5. Abre la **vista detallada** de un activo: gráfico con EMAs y niveles, desglose del score y el
   **Ticket listo para XTB** con botones de copiado para Volumen, Entrada, SL y TP.
6. Introduce la orden manualmente en xStation.

> **Consejo de calibración:** calibra con el mercado tranquilo y vuelve a hacerlo tras el
> rollover de futuros o si cambias de fuente de datos (las calibraciones se guardan por
> proveedor + símbolo y se ignoran si cambias de feed). yfinance puede ir con unos minutos de
> retraso en futuros: calibra en un momento sin movimientos bruscos.

---

## 5. Cómo funciona

### Arquitectura

```
cdf-plus/
├── app.py            # Interfaz Streamlit (escáner, detalle, ticket, gráficos Plotly)
├── config.py         # Activos, símbolos, escalas, contratos XTB, pesos y umbrales
├── data_fetcher.py   # OHLCV 15m/1h/4h · Twelve Data con fallback a yfinance · caché TTL
├── indicators.py     # EMA/RSI/MACD/ATR vectoriales · filtro intermercado · scoring
├── risk_engine.py    # Calibrador de offset XTB · lotaje dinámico · ticket
├── notifier.py       # Alertas Telegram con anti-duplicados por vela
├── .streamlit/config.toml   # Tema oscuro tipo terminal
├── requirements.txt
└── .env.example
```

### Datos y límites de peticiones

- **Twelve Data**: se descarga una sola serie de 15 min por activo (5 000 velas) y se
  remuestrea localmente a 1h y 4h → **6 créditos por actualización**, dentro del límite gratuito
  de 8/min. Caché de 5 min (≈ 72 créditos/hora). Un limitador interno respeta las 8 peticiones
  por minuto. En el plan gratuito los índices se sustituyen por ETFs (SPY, QQQ, DIA, IWM)
  multiplicados por un factor de escala; el offset de calibración absorbe la diferencia residual.
  Con un plan de pago puedes usar `SPX`, `NDX`, `DJI`, `RUT` cambiando `twelve_symbol` y
  `twelve_scale = 1.0` en `config.py`.
- **yfinance**: 15m y 1h directos; 4h remuestreado desde 1h. Caché de 2 min.
- Si Twelve Data falla para un activo (límite, símbolo no incluido en el plan, red), **ese
  activo se reintenta automáticamente con yfinance** y se muestra un aviso.
- Fuera de horario de mercado los activos se marcan como *Cerrado/atrasado* y las señales usan
  la última sesión disponible (no se envían alertas con datos atrasados).

### Motor de scoring (−100 a +100)

Cada temporalidad recibe un sesgo −100..+100 con 6 reglas:

| Regla | Peso |
|---|---|
| Precio vs EMA 200 (tendencia estructural) | 25 |
| Alineación EMA 20 / 50 / 200 | 25 |
| MACD vs línea de señal | 15 |
| Aceleración del histograma MACD | 10 |
| RSI(14) en zona de momentum (50–70 / 30–50; los extremos puntúan poco y avisan) | 15 |
| Precio vs EMA 20 | 10 |

El score técnico combina: **1H completo (60 %) + sesgo 4H (15 %) + sesgo 15M (10 %) +
alineación multi-temporalidad (15 %)**.

### Filtro intermercado (Risk-On / Risk-Off y amplitud)

- Tendencia de fondo de cada activo = media del sesgo 1H y 4H.
- **Índices** = media de US500, US100, US30, US2000. **Metales** = media de GOLD y SILVER.
- Régimen: *RISK-ON* (índices ≫ metales), *RISK-OFF* (metales ≫ índices), *Liquidez*
  (todo sube), *Desapalancamiento* (todo baja) o *Mixto*.
- **Amplitud**: cuántos índices acompañan la dirección del bloque y si el Russell 2000
  (small caps) confirma a los grandes índices.
- El score técnico se multiplica por un factor de convicción (0.5–1.2): se refuerza cuando el
  régimen y la amplitud confirman y se penaliza ante divergencias (p. ej. comprar índices en
  Risk-Off, índices divididos, Russell sin confirmar, oro y plata en direcciones opuestas).

Veredicto: `≥ 60` COMPRA FUERTE · `≥ 25` COMPRA · entre −25 y 25 NEUTRAL · `≤ −25` VENTA ·
`≤ −60` VENTA FUERTE (umbrales editables en `config.py`).

### Gestión de riesgo

```
riesgo_USD   = capital × %riesgo
distancia_SL = SL_mult × ATR        (en puntos del índice/metal)
lotes        = riesgo_USD / (distancia_SL × USD_por_punto_por_lote)
```

El lotaje se **redondea hacia abajo a 0.01** para no superar nunca el riesgo máximo. Si el
riesgo no alcanza para 0.01 lotes, el ticket muestra 0 lotes y cuánto arriesgarías con el
mínimo. También se estima el margen (5 % índices y oro, 10 % plata; editable en `config.py`).

### Alertas Telegram

Con `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en `.env`, activa *Enviar alertas* en la barra
lateral. Se envía un mensaje cuando un activo alcanza `score ≥ +umbral` o `≤ −umbral` (70 por
defecto), con el ticket calibrado. Cada combinación activo + dirección + vela de 1H se envía una
sola vez (`.state/alerts_sent.json`). Las alertas se evalúan cuando la app se refresca: activa
*Auto-actualización* y deja la pestaña abierta.

---

## 6. Solución de problemas

| Problema | Solución |
|---|---|
| "Twelve Data [429]" | Límite de créditos alcanzado: la app usa yfinance para ese activo. Espera un minuto. |
| "Twelve Data [404/400]" en un índice | Ese símbolo no está en tu plan: deja el ETF por defecto. |
| Activo "Cerrado/atrasado" | Mercado cerrado o feed retrasado; la señal usa la última sesión. |
| Lotaje 0.00 | El riesgo en USD no cubre 0.01 lotes con ese stop: sube capital/% o reduce el multiplicador ATR. |
| Precios distintos a xStation | Calibra el offset del activo. |

---

> **Aviso:** CDF Plus es una herramienta de apoyo a la decisión y no constituye asesoramiento
> financiero. Los CFDs son instrumentos complejos con un alto riesgo de perder dinero
> rápidamente debido al apalancamiento. Verifica siempre precio, lotaje y especificaciones en
> xStation antes de ejecutar cualquier orden.
