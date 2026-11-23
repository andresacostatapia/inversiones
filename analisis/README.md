# Análisis de acciones y ETF

App local que monitorea una watchlist de acciones chilenas (`.SN`) e internacionales, calcula indicadores
técnicos y (desde la fase 2) propone señales COMPRAR / MANTENER / VENDER con su explicación.

> ⚠️ **Herramienta educativa, no es asesoría financiera.** La app solo sugiere: no ejecuta órdenes
> ni se conecta a ningún broker.

Es independiente de la PWA que está en la raíz del repositorio: no comparte código ni datos con ella.

## Estado

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Datos (proveedores intercambiables, dólar, SQLite, actualización periódica) + indicadores | ✅ |
| 2 | Motor de señales + backtest | ✅ |
| 3 | Dashboard web (React) | ✅ |
| 4 | Cartera, aportes y alertas | pendiente |

## Instalación

Requiere **Python 3.11 o superior** y, para el dashboard, **Node.js 20 o superior**.

```bash
cd analisis
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt

cd frontend
npm install
npm run build                      # deja el dashboard compilado en frontend/dist
```

Después, para usar la app:

```bash
cd analisis/backend
uvicorn app.main:app               # abre http://127.0.0.1:8000
```

El servidor sirve el dashboard y la API en el mismo puerto, y solo escucha en tu computador (127.0.0.1).

## Configuración

### `config.yaml`

Todo lo editable está en [`config.yaml`](config.yaml):

- `watchlist`: símbolos iniciales. Solo se usa la primera vez; después la watchlist vive en la base de datos y
  se edita desde la API (o la UI en la fase 3).
- `data.poll_seconds`: cada cuánto se piden precios con el mercado abierto (60 s por defecto). Con el mercado
  cerrado, `closed_poll_seconds` (1 hora).
- `data.providers`: orden de proveedores por mercado (`CL` = Bolsa de Santiago, `US` = EE.UU.). Se usa el primero
  que tenga API key y responda; si falla, se pasa al siguiente.
- `data.rate_limits`: límite de consultas por proveedor. Ajústalo si tienes un plan pagado.
- `signals`: umbrales de COMPRAR/VENDER, peso de cada estrategia y efecto del volumen.
- `backtest`: comisión y deslizamiento por operación.
- `risk_profile`: se usa en la fase 4.

Símbolos: los de Santiago llevan el sufijo `.SN` (`SQM-B.SN`, `CHILE.SN`, `FALABELLA.SN`); los de EE.UU. van tal cual
(`SPY`, `VOO`, `EEM`, `VWO`).

### API keys (opcional)

Sin claves, la app funciona solo con **yfinance** (gratis, con retraso). Para usar otros proveedores:

```bash
cp .env.example .env     # y completa las claves que tengas
```

| Proveedor | Variable | Plan gratis | Para qué sirve aquí |
|---|---|---|---|
| yfinance | — | Sin límite formal (≈60/min es prudente) | EE.UU., Santiago y dólar; precio e historia |
| [Finnhub](https://finnhub.io) | `FINNHUB_API_KEY` | 60/min | Precio **en tiempo real** de EE.UU. (sin historia) |
| [Twelve Data](https://twelvedata.com) | `TWELVEDATA_API_KEY` | 8/min, 800/día | Precio e historia de EE.UU. |
| [Alpha Vantage](https://www.alphavantage.co) | `ALPHAVANTAGE_API_KEY` | 25/día | Respaldo de historia de EE.UU. (100 días) |

Ninguno de los tres con API key cubre la Bolsa de Santiago en su plan gratis, por eso Chile usa yfinance.
El archivo `.env` está en `.gitignore`: las claves nunca se suben al repositorio.

### Dólar

Se pide primero el **dólar observado** a [mindicador.cl](https://mindicador.cl) (Banco Central, un valor por día hábil)
y, si no responde, el USD/CLP de mercado de Yahoo (`CLP=X`). La fuente usada siempre se informa.

## Uso

Todos los comandos se corren desde `analisis/backend` con el entorno virtual activado.

### Línea de comandos

```bash
python -m app.cli status                    # mercados abiertos/cerrados y proveedores configurados
python -m app.cli quote SQM-B.SN CHILE.SN SPY
python -m app.cli indicators VOO            # indicadores y métricas (descarga 2 años)
python -m app.cli signal SQM-B.SN SPY       # señal con puntaje, confianza y razones
python -m app.cli backtest SPY --years 10   # cada estrategia y la combinada vs buy & hold
python -m app.cli backtest SPY --commission 0.1 --slippage 0.05
python -m app.cli fx                        # dólar
python -m app.cli refresh                   # actualiza toda la watchlist y la guarda en data/analisis.db
```

### API

```bash
uvicorn app.main:app --reload               # dashboard en http://127.0.0.1:8000, API en /docs
```

Al iniciar, la app descarga 10 años de historia diaria de cada símbolo (unos segundos) y luego actualiza
los precios cada 60 s mientras su mercado esté abierto (los feriados se respetan).

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/api/status` | Aviso legal, mercados, salud y consumo de cada proveedor |
| GET | `/api/watchlist` | Precios de la watchlist con su etiqueta de frescura |
| POST | `/api/watchlist` `{"symbol": "FALABELLA.SN"}` | Agrega un símbolo (valida que tenga datos; 404 si no) |
| DELETE | `/api/watchlist/{symbol}` | Lo quita |
| GET | `/api/quotes/{symbol}` | Precio de un símbolo |
| GET | `/api/instruments/{symbol}/history?period=1y` | Velas diarias + todos los indicadores (`1m` … `10y`, `max`) |
| GET | `/api/instruments/{symbol}/indicators` | Último valor de cada indicador + métricas básicas |
| GET | `/api/fx` | Dólar |
| POST | `/api/refresh` | Fuerza una actualización inmediata (precios, historia y señales) |
| GET | `/api/strategies` | Estrategias disponibles, pesos y umbrales |
| GET | `/api/signals` | Señal actual de toda la watchlist |
| GET | `/api/signals/{symbol}` | Señal con detalle por estrategia, razones, confianza, señal anterior |
| GET | `/api/signals/{symbol}/history?period=1y` | Puntaje diario de cada estrategia y del combinado |
| POST | `/api/backtest` | Backtest (ver abajo) |

### Frescura del dato

Cada precio incluye `freshness.label`, que dice siempre qué tipo de dato es y de qué hora:

| `kind` | Ejemplo |
|---|---|
| `tiempo_real` | Tiempo real · 11:32 (solo proveedores que lo garantizan, p. ej. Finnhub, y con menos de 2 min) |
| `retraso` | Con retraso · dato de 11:15 (hace 17 min) |
| `desactualizado` | Mercado abierto pero el dato tiene más de 30 min (configurable) |
| `cierre` | Mercado cerrado · último precio 16:01 |
| `diario` | Dato diario oficial del 23/11 (dólar observado) |

Las horas se muestran en la zona de `general.timezone` (Santiago por defecto).

## Dashboard

En `http://127.0.0.1:8000` (después de `npm run build`):

- **Aviso fijo** arriba: «Herramienta educativa, no es asesoría financiera».
- **Barra superior**: estado de la Bolsa de Santiago y de EE.UU. (abierta hasta / abre a), dólar, hora del último
  ciclo del servidor, botón **Actualizar** y tema claro/oscuro/sistema.
- **Watchlist** agrupada en Chile / Internacional: precio, variación del día, **frescura del dato con su hora**
  (Tiempo real / Retraso / Cerrado / Desactualizado), señal, puntaje y confianza, y aviso si la señal cambió desde
  el día anterior. Se agregan símbolos escribiéndolos (se validan contra el proveedor) y se quitan con ×.
- **Detalle** del símbolo seleccionado (la URL `#SQM-B.SN` lo deja enlazable):
  - Precio grande con su fuente y etiqueta de frescura completa («Con retraso · dato de 11:15 (hace 17 min)»).
  - **Señal**: etiqueta, puntaje, confianza, resumen, qué hacer si la tienes o no, avisos (provisional,
    volatilidad alta, huecos de datos), stop de referencia y una barra por estrategia (azul = compra,
    rojo = venta, con los umbrales marcados) con sus razones.
  - **Gráfico** de velas con SMA 20/50/200 y Bollinger, y paneles de volumen, RSI (líneas 30/70) y MACD;
    cada uno se activa o desactiva. Al pasar el cursor se leen todos los valores de ese día.
    Periodos de 3 meses a 10 años.
  - **Evolución de la señal combinada** con las líneas de COMPRAR y VENDER.
  - **Métricas**: retornos, volatilidad, máximos y mínimos de 52 semanas, distancia a la SMA200, RSI, ATR,
    volumen relativo.
  - **Backtest**: años y comisión; tabla contra buy & hold y curvas de «valor de 100 invertidos».
- Se actualiza solo cada 30–60 s (y deja de consultar cuando la pestaña está oculta). Funciona en el celular.

Colores: azul/rojo para la dirección de la señal y verde/rojo para la variación de precio, siempre acompañados
de icono, signo o texto (nunca solo el color). La paleta de las series se validó para daltonismo en
modo claro y oscuro.

### Desarrollo del dashboard

```bash
# terminal 1
cd analisis/backend && uvicorn app.main:app --reload
# terminal 2
cd analisis/frontend && npm run dev    # http://127.0.0.1:5173, recarga al guardar; /api va al puerto 8000
npm test                               # tests del formato de números
```

## Indicadores

En [`backend/app/indicators.py`](backend/app/indicators.py), escritos en pandas, sin pandas-ta ni ta-lib:

- **SMA y EMA** de 20, 50 y 200 días. La EMA se inicializa con la SMA, como en TradingView y StockCharts.
- **RSI(14)** con suavizado de Wilder. El test lo compara con los valores publicados por StockCharts.
- **MACD** (12, 26, 9).
- **Bandas de Bollinger** (20, 2σ), con %B y ancho de banda.
- **ATR(14)**, en valor absoluto y como % del precio.
- **Volumen relativo**: volumen del día dividido por el promedio de los 20 días anteriores.
- **Métricas**: retorno a 1, 3, 6 y 12 meses, volatilidad anual, máximo y mínimo de 52 semanas, distancia a la SMA200.

Mientras el mercado está abierto, la vela del día se arma con el precio en vivo, así que los indicadores del día
son **provisionales hasta el cierre**: `/indicators` lo indica con `"provisional": true`. Con el mercado abierto,
el volumen del día aún está incompleto.

## Motor de señales

Cada estrategia es un módulo en [`backend/app/strategies/`](backend/app/strategies) que da un puntaje de
**−100 (vender) a +100 (comprar)** para cada día, más sus razones en español.

| Estrategia | Lógica | Nota |
|---|---|---|
| **Tendencia** | Precio vs SMA200 (±35, satura en ±5 %), SMA50 vs SMA200 (cruce dorado/de la muerte, ±35), pendiente de la EMA20 en 5 días (±30, satura en ±2 %) | Necesita 200 días |
| **RSI** | Lineal por tramos: RSI 20 → +100, 30 → +60, 40 → +20, 50 → 0, 60 → −20, 70 → −60, 80 → −100 | |
| **MACD** | Sobre/bajo su señal (±40) + cruce en los últimos 3 días (±40) + signo del MACD (±20) | |
| **Bollinger** | (0,5 − %B) × 160: banda inferior → +80, superior → −80. **Se reduce a la mitad** si va contra la tendencia (comprar una caída bajo la SMA200, vender una subida sobre ella) | |

**Señal combinada** = promedio ponderado de las estrategias con datos (pesos en `config.yaml`; peso 0 la desactiva),
multiplicado por el **factor de volumen**:
- Volumen ≥ 1,5x el promedio a favor del movimiento: la confirma (× 1,2).
- Volumen alto en contra: la debilita (× 0,8).
- Volumen < 0,7x: la debilita (× 0,9).
- El volumen no vota por sí solo, y con el mercado abierto no se usa porque el volumen del día está incompleto.

Con puntaje ≥ +30 la señal es **COMPRAR**, con ≤ −30 es **VENDER**, y en medio **MANTENER**. Los umbrales están en `signals.thresholds`.
Si tienen datos menos de la mitad del peso (por ejemplo, con menos de 20 días de historia), no hay señal.

Como no se permiten ventas en corto, cada señal se traduce en una acción según si tienes el activo o no:

| Señal | Si lo tienes | Si no lo tienes |
|---|---|---|
| COMPRAR | Puedes mantener o aumentar | Considerar comprar |
| MANTENER | Mantener | Esperar una mejor entrada |
| VENDER | Considerar vender o reducir | Evitar comprar por ahora |

La **confianza (0–100 %)** combina tres cosas:
- **Acuerdo (65 %):** el peso de las estrategias que apuntan en la dirección de la señal. Las neutrales cuentan la mitad. Para MANTENER, el peso de las que también están en zona neutral.
- **Fuerza (35 %):** cuánto pasa el puntaje del umbral. Para MANTENER, qué tan cerca está de 0.
- **Calidad de los datos:** reduce la confianza si faltan estrategias por historia corta, si el precio está desactualizado (× 0,85) o si el ATR supera 1,5 veces su mediana de un año (× 0,8).

Cada señal incluye además:
- un resumen, por ejemplo «Cruce MACD alcista hoy + Precio bajo la banda inferior de Bollinger»;
- todas las razones y los avisos;
- un **stop de referencia** informativo (cierre − 2×ATR);
- la **señal del día anterior**, para saber si cambió.

Con el mercado abierto, la señal es **provisional** hasta el cierre.
Las señales se recalculan en cada ciclo de actualización y se guardan, una por símbolo y día, en la tabla `signals`.

Con los umbrales de ±30, en los 10 años de historia de la watchlist inicial la señal combinada fue MANTENER
alrededor del 90 % de los días, COMPRAR ~5 % y VENDER ~3 %. Es selectiva a propósito, pensando en un horizonte de 6–12 meses.

## Backtest

```bash
curl -X POST localhost:8000/api/backtest -H 'content-type: application/json' \
     -d '{"symbol": "SPY", "years": 10, "commission_pct": 0.5}'
```

Campos opcionales: `strategies` (subconjunto), `commission_pct`, `slippage_pct`, `buy_threshold`,
`sell_threshold` y `weights`. Por defecto se usa lo de `config.yaml`.

Reglas, en [`backend/app/backtest.py`](backend/app/backtest.py):
- **Sin mirar el futuro:** la señal se calcula al cierre del día t y se ejecuta en la **apertura del día t+1**.
- Solo largo, 100 % dentro o fuera. Entra con puntaje ≥ umbral de compra, sale con ≤ umbral de venta y en medio
  mantiene lo que tenga.
- Comisión + deslizamiento en cada compra y en cada venta (0,5 % por defecto). Una posición abierta al final
  se valoriza a mercado.
- **Precios ajustados por dividendos** cuando el proveedor los entrega, así se compara el retorno total.
- Buy & hold compra en la misma apertura en que empieza la estrategia. Todas empiezan el primer día con
  200 días de historia, así que con 10 años de datos se evalúan unos 9,2.

Entrega para cada estrategia y para la combinada:
- retorno total y anualizado;
- **drawdown máximo**;
- volatilidad y Sharpe;
- número de operaciones, **win rate**, retorno medio por operación y % del tiempo invertido;
- la diferencia contra buy & hold y una frase de conclusión;
- la curva de capital semanal y la lista de operaciones.

> Un backtest favorable puede deberse a **sobreajuste**. No ajustes pesos y umbrales hasta que el pasado se vea
> bien: el próximo año no va a repetir los últimos diez.

Ejemplo real, SPY, 2016–2026, comisión de 0,5 %. Buy & hold ganó +281 % con una caída máxima de −34 %. La tendencia
sola ganó +155 % con −22 %. El MACD solo perdió dinero: hace más de 100 operaciones y las comisiones se lo comen.

## Tests

```bash
cd analisis/backend
pytest
```

Los tests corren sin red ni API keys: usan datos sintéticos, un proveedor falso y respuestas HTTP simuladas (respx).

## Estructura

```
analisis/
├── config.yaml            perfil, watchlist inicial, proveedores, límites
├── .env.example           API keys (copiar a .env)
├── data/                  base SQLite (se crea sola, no se sube a git)
├── frontend/              dashboard React + Vite + lightweight-charts
│   └── src/
│       ├── App.tsx        estructura, barra superior, polling
│       ├── api.ts         cliente de la API
│       └── components/    Watchlist, SignalCard, PriceChart, ScoreChart, Metrics, BacktestPanel
└── backend/
    ├── app/
    │   ├── main.py        FastAPI + actualización periódica
    │   ├── cli.py         comandos de prueba
    │   ├── config.py      lee config.yaml y .env
    │   ├── db.py          tablas SQLite
    │   ├── market_data.py precios, historia y dólar → SQLite
    │   ├── market_hours.py horarios con feriados (XSGO, XNYS) y etiqueta de frescura
    │   ├── scheduler.py   qué actualizar y cuándo
    │   ├── indicators.py
    │   ├── strategies/    trend, rsi, macd, bollinger (una por módulo)
    │   ├── signals.py     combinación, confianza, razones
    │   ├── signal_service.py  señales desde SQLite + backtest
    │   ├── backtest.py
    │   ├── api/routes.py
    │   └── providers/     base, yfinance, finnhub, twelvedata, alphavantage, fx, router
    └── tests/
```

## Manejo de errores

- **Un proveedor falla** (red, 5xx, límite): se pasa al siguiente de la lista. Tras 3 fallas seguidas, ese proveedor
  descansa de 1 a 30 min, con pausas que van creciendo. Un 429 lo pone en pausa de inmediato.
- **Límites propios**: antes de llamar se revisa el cupo por minuto o día. Si no queda, se usa el siguiente proveedor
  sin castigar al primero.
- **Símbolo inexistente**: al agregarlo, la API responde 404 con una pista (por ejemplo, el sufijo `.SN`).
  No cuenta como falla del proveedor.
- **Falla al actualizar**: se conserva el último precio bueno y se marca `status: "error"` con el motivo.
- **Velas de relleno de Yahoo**: a veces Yahoo "rellena" semanas sin datos reales repitiendo el último precio con
  volumen 0 (visto en 2026 en SQM-B.SN, FALABELLA.SN y CHILE.SN, de julio a octubre). Esas filas se descartan al
  descargar y al iniciar, porque aplanan las medias y falsean el backtest. Si queda un hueco de más de 10 días en el
  último año, la señal lo avisa.
- **Horario de Santiago**: la librería de calendarios usa cierre a las 17:00 entre noviembre y febrero y a las 16:00
  el resto del año. Si en la práctica cierra antes, solo se consulta de más esa hora.
