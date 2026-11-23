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
| 2 | Motor de señales + backtest | pendiente |
| 3 | Dashboard web (React) | pendiente |
| 4 | Cartera, aportes y alertas | pendiente |

## Instalación

Requiere **Python 3.11 o superior**.

```bash
cd analisis
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt
```

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
- `risk_profile`, `signals` y `backtest`: se usan en las fases siguientes.

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
python -m app.cli fx                        # dólar
python -m app.cli refresh                   # actualiza toda la watchlist y la guarda en data/analisis.db
```

### API

```bash
uvicorn app.main:app --reload               # http://127.0.0.1:8000/docs
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
| POST | `/api/refresh` | Fuerza una actualización inmediata |

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
- **Horario de Santiago**: la librería de calendarios usa cierre a las 17:00 entre noviembre y febrero y a las 16:00
  el resto del año. Si en la práctica cierra antes, solo se consulta de más esa hora.
