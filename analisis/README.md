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
| 4 | Cartera, aportes y alertas | ✅ |

## App de escritorio para Windows (sin instalar Python)

GitHub Actions compila un `.exe` en cada cambio de `analisis/` (workflow
[`analisis-windows.yml`](../.github/workflows/analisis-windows.yml)):

1. En GitHub: pestaña **Actions** → «App de análisis · Windows» → la ejecución más reciente en verde → artefacto
   **AnalisisInversiones-windows** (un .zip de unos 70 MB).
2. Descomprímelo donde quieras (por ejemplo, `Documentos\AnalisisInversiones`).
3. Abre `AnalisisInversiones.exe`. Como no está firmado, Windows puede mostrar «Windows protegió su PC»:
   **Más información → Ejecutar de todas formas** (solo la primera vez).

Se abre en una ventana propia (WebView2, ya incluido en Windows 10 y 11). Si falla, abre el navegador.
- Los datos viven en `%LOCALAPPDATA%\AnalisisInversiones`: `config.yaml`, `.env`, `data\analisis.db` y `logs\app.log`.
  Para actualizar el programa basta con reemplazar la carpeta; tu cartera no se toca.
- Si abres el programa dos veces, la segunda vez se muestra la misma instancia, no otra sobre la misma base de datos.
- `AnalisisInversiones.exe --headless` deja solo el servidor en http://127.0.0.1:8765, para usarlo desde el navegador.

Para compilarlo tú mismo en Windows: `pip install -r backend/requirements.txt -r desktop/requirements.txt`, compilar
el dashboard (`npm run build`) y `pyinstaller --noconfirm desktop/AnalisisInversiones.spec` → `dist\AnalisisInversiones\`.

## App Android (APK)

El mismo dashboard, pero **todo se calcula en el teléfono**: no necesita el PC ni un servidor. Consulta Yahoo Finance
y mindicador.cl directamente desde el teléfono y guarda tu cartera solo en el teléfono.
El motor de análisis está portado a TypeScript (`frontend/src/core`) y una batería de pruebas exige que dé
exactamente lo mismo que el de Python (`scripts/export_fixtures.py` → `golden.test.ts`).

**Instalar**
1. En GitHub: **Actions** → «App de análisis · Android» → la ejecución más reciente en verde → artefacto
   **AnalisisInversiones-android** (un .zip con `AnalisisInversiones.apk`).
2. Pasa el .apk al teléfono (por ejemplo, descargándolo desde el navegador del teléfono con tu sesión de GitHub).
3. Ábrelo. Android pedirá permitir «instalar apps de fuentes desconocidas» para esa app (navegador o archivos).
4. La primera vez, acepta las notificaciones: así llegan las alertas importantes.

**Firma estable (recomendado antes de usarla en serio).** Android solo instala una versión nueva encima de la anterior
si ambas están firmadas con la misma clave. Sin configurar nada, GitHub compila un APK de *depuración* con una clave
distinta cada vez: para actualizar habría que desinstalar, y eso **borra la cartera guardada en el teléfono**.
Para evitarlo, crea una clave una sola vez y guárdala como secretos del repositorio (nunca en el código: el repo es público):

```bash
keytool -genkeypair -v -keystore analisis.keystore -alias analisis -keyalg RSA -keysize 2048 -validity 10000
base64 -w0 analisis.keystore      # Windows (PowerShell): [Convert]::ToBase64String([IO.File]::ReadAllBytes("analisis.keystore"))
```

En GitHub: **Settings → Secrets and variables → Actions → New repository secret**, cuatro secretos:
`ANDROID_KEYSTORE_BASE64` (el texto base64), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS` (`analisis`) y
`ANDROID_KEY_PASSWORD`. Desde ahí el workflow compila la versión *release* firmada. Guarda `analisis.keystore` y sus
claves en un lugar seguro: si se pierden, la siguiente versión no podrá instalarse encima.
(`keytool` viene con Java o Android Studio.)

**Respaldo.** En Cartera → *Respaldo* → «Copiar operaciones (CSV)»: copia tus operaciones en el formato de la PWA.
Pégalas en una nota o correo. Para restaurarlas (o pasarlas del PC al teléfono y viceversa), pégalas en
«Importar» → «…o pega aquí el contenido del CSV».

**Diferencias con la versión de escritorio**
- Fuente de datos: Yahoo (sin API key). Si Yahoo limita las consultas (error 429), la app pausa de 5 a 30 min y lo
  indica en cada precio, en vez de insistir.
- Alertas: como notificación del teléfono, evaluadas cada vez que la app se actualiza (abierta o recién vuelta a
  primer plano). No hay Telegram ni email en el teléfono, y Android no permite revisar precios cada minuto con la app
  cerrada.
- La configuración usa los valores de `config.yaml` (30/70, −20 % / −25 %, umbrales ±30, aporte 500.000 CLP);
  el monto del aporte se cambia en la pantalla de reparto.

**Compilarlo tú mismo**: Node 20+, JDK 21 y el SDK de Android (Android Studio), luego
`cd frontend && npm ci && npm run android:sync && cd android && ./gradlew assembleDebug`.
Para probar el modo móvil en el navegador: `npm run build:mobile && npm run preview:mobile` (http://127.0.0.1:4174).

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
- `risk_profile`: distribución objetivo (30 % Chile / 70 % internacional), umbrales de alerta −20 % y −25 %,
  aporte mensual por defecto y si tu broker permite fracciones de acción.
- `alerts`: qué alertas se generan y desde qué gravedad se envían por Telegram o email.

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
| GET | `/api/search?q=oro` | Busca símbolos por nombre o código; dice el mercado y si tu perfil permite comprarlo |
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
| GET / POST | `/api/transactions` | Operaciones registradas / registrar una |
| DELETE | `/api/transactions/{id}` | Borrar una operación |
| POST | `/api/transactions/import` `{"csv": "...", "commit": false}` | Vista previa (o importación) del CSV de la PWA |
| GET | `/api/portfolio` | Posiciones, totales en CLP/USD, distribución vs objetivo |
| POST | `/api/contribution` `{"amount_clp": 500000}` | Reparto sugerido del aporte |
| GET | `/api/alerts` | Alertas, no leídas y canales configurados |
| POST | `/api/alerts/{id}/read`, `/api/alerts/read-all`, `/api/alerts/test` | Marcar leídas / probar notificaciones |
| GET | `/api/profile` | Perfil de riesgo vigente |

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
- **Buscador de símbolos** sobre la watchlist: escribe un nombre o código («nvidia», «oro», «s&p 500», «cobre»,
  «samsung», «FALABELLA.SN»). Primero muestra un catálogo propio con nombres en español y después los resultados de
  Yahoo. Cada resultado indica su tipo (acción, ETF, índice, futuro, cripto) y su mercado. **Populares** abre el
  catálogo por categorías: ETF de índices, oro y materias primas, índices, grandes empresas de EE.UU., Chile, otras
  bolsas y cripto.
  - **Solo análisis**: se puede seguir y ver su señal, pero tu perfil (`risk_profile`) no permite comprarlo: índices
    (`^GSPC`: usa SPY, VOO o IVV), futuros (`GC=F`: para oro usa GLD o IAU), tipos de cambio, ETF apalancados o
    inversos (TQQQ, SQQQ…) y acciones en monedas que la cartera aún no convierte (solo CLP y USD; por ejemplo,
    Samsung en Corea `005930.KS`, pero sí `SMSN.IL` en Londres, en dólares). La cartera rechaza esas compras y el
    reparto del aporte las excluye.
  - Horarios: futuros y divisas operan casi 24 h de lunes a viernes, las criptomonedas 24/7, y en otras bolsas se
    muestra la hora del último precio sin decir «abierto» o «cerrado».
- **Watchlist** agrupada por mercado (Chile, EE.UU., futuros y divisas, cripto, otras bolsas): precio, variación del día, **frescura del dato con su hora**
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

## Cartera

En la pestaña **Cartera**:

- **Registro de operaciones**: fecha, compra/venta, símbolo, cantidad, precio por acción y comisión en la moneda
  del activo, dólar opcional y nota. Se aceptan números en formato chileno («48.500», «650,5»).
  - Si el símbolo no está en la watchlist, se valida con el proveedor y se agrega (para tener precio y señal).
  - No se puede vender más de lo que tienes, registrar fechas futuras ni borrar una compra que respalda una venta posterior.
  - El dólar (CLP por USD) de cada operación se toma de la serie diaria de esa fecha. También puedes ingresarlo a mano;
    si no hay dato de esa fecha se usa el de hoy, marcado con `*`.
- **Importar desde la PWA**: en la PWA, Historial → Exportar CSV, y aquí se sube el archivo.
  - Muestra una vista previa con filas listas, duplicadas y con error, y solo guarda al confirmar.
  - Los activos en CLP sin sufijo reciben `.SN` (SQM-B → SQM-B.SN).
  - Detecta duplicados por fecha, símbolo, tipo, cantidad y precio, así que se puede volver a importar sin repetir.
- **Resumen**: valor total en CLP y USD, ganancia no realizada y realizada, variación de hoy y costo.
  La **distribución Chile / Internacional** se compara con el objetivo.
- **Posiciones**: cantidad, costo promedio, precio con su hora, valor en CLP y ganancia en CLP. En las posiciones en
  USD, la ganancia se separa en **efecto precio** y **efecto dólar**. También muestra la caída desde tu costo
  promedio, el estado (✓ OK / ⚠ Revisar / ⛔ Vender) y el peso en la cartera.

Cálculos (ver [`backend/app/portfolio.py`](backend/app/portfolio.py)):
- Costo promedio ponderado con comisiones.
- Ganancia realizada = ingreso neto − costo promedio vendido.
- El costo en CLP usa el dólar de cada compra; el valor actual, el de hoy.
- La caída para las alertas se mide en la moneda del activo (precio actual / costo promedio − 1). Así mide al activo y no al dólar.

### Reparto del aporte mensual

Ingresas un monto en CLP (por defecto `risk_profile.monthly_contribution_clp`) y la app sugiere qué comprar:

1. **Entre mercados**: calcula cuánto le falta a Chile y a internacional para llegar al 30/70 *después* del aporte, y
   reparte en proporción a esos faltantes. Si estás muy cargado a un lado, todo va al otro.
2. **Dentro de cada mercado**: entre lo que tienes o vigilas. COMPRAR pesa el doble que MANTENER. Se excluyen:
   - los símbolos con señal VENDER o sin historia suficiente;
   - **las posiciones bajo el umbral de venta**, porque las reglas de riesgo mandan sobre la señal técnica y no se
     promedia a la baja algo que tu perfil dice considerar vender.

   Las que están en revisión reciben aporte, pero marcadas con ⚠.
3. **Acciones enteras** en Chile (fracciones en EE.UU. si `fractional_shares.US: true`). Lo que sobra compra una acción
   más de lo que quedó más lejos de su monto, y el resto se informa como «sin invertir».

Nunca sugiere vender para rebalancear y no incluye comisiones del broker.

## Alertas

Se evalúan solas al final de cada ciclo de actualización y se ven en la pestaña **Alertas**: un contador en la
pestaña y un aviso arriba si hay alguna crítica sin leer.

| Alerta | Gravedad | Cuándo |
|---|---|---|
| ⛔ Venta | crítica | Una posición cae −25 % o más desde tu costo promedio |
| ⚠ Revisión | atención | Cae entre −20 % y −25 % |
| Cambio de señal | atención (a COMPRAR o VENDER) / info (a MANTENER) | La señal de hoy difiere de la del día anterior; si el mercado está abierto se marca provisional |
| Datos | info | Con el mercado abierto, un precio lleva más de 30 min sin llegar |

- Las de caída y datos se crean **una vez** mientras la condición se cumple. Se marcan «resueltas» si el precio se
  recupera, y si vuelve a caer se crea una nueva.
- Las de señal se generan una por símbolo, día y etiqueta.
- **Telegram y email** son opcionales. Para usarlos, completa las variables de `.env` (instrucciones en
  [`.env.example`](.env.example)) y reinicia. Por defecto solo se envían las de gravedad «atención» o mayor; se
  ajusta en `alerts.notify_min_severity`. El botón **Enviar prueba** verifica la configuración.

## Copiloto CFD (XTB xStation)

Pestaña **CFD** del dashboard (navegador y app de Windows; la app Android no la trae). Es un módulo **aparte** de la
cartera: aquí se analizan CFD apalancados con largos y cortos, pero la app **nunca envía órdenes**: arma un ticket y
tú operas a mano en xStation. Herramienta educativa, no es asesoría financiera.

**Activos:** GOLD, SILVER, US500, US100, US30 y US2000. **Temporalidades:** 1 min, 5 min, 15 min, 1 hora y 4 horas
(más 1 día como contexto).

**Datos y su retraso.** Por defecto, Yahoo: futuros continuos (GC=F, SI=F, ES=F, NQ=F, YM=F, RTY=F), casi 24 horas,
con **~10 minutos de retraso** (se muestra en cada activo). En 1 y 5 minutos eso pesa: confirma siempre el precio en
xStation. Con `TWELVEDATA_API_KEY` en `.env`, el oro y la plata vienen de Twelve Data al contado (XAU/USD, XAG/USD)
casi en tiempo real en 1, 5 y 15 min. El plan gratis alcanza para unas horas al día; después la app sigue con Yahoo.
Las velas de 4 horas se arman con las de 1 hora, alineadas a la sesión de CME (abre 17:00 hora de Chicago).

**Puntaje (−100 a +100).** Solo con velas cerradas:
- **Por temporalidad:**
  - tendencia: precio vs EMA200, EMA20 vs EMA50, EMA50 vs EMA200;
  - momento: RSI14 entre 55 y 70 alcista y entre 30 y 45 bajista (sobre 70 o bajo 30 resta: no perseguir);
  - MACD 12/26/9 y su histograma.
- **Combinación:** la temporalidad elegida pesa 50 %, la siguiente mayor 30 % y la otra 20 %.
- **Filtro intermercado** (en 1 hora o más):
  - clima risk-on/risk-off según los 4 índices frente al oro y la plata: ±10 a los índices, +10 a los metales en risk-off;
  - amplitud: si el Russell 2000 no acompaña al S&P 500, −5 al alza de los índices;
  - si el oro y la plata se confirman entre sí, ±5.
- **Veredictos:** COMPRA FUERTE (≥ 70), COMPRA (≥ 30), NEUTRAL, VENTA (≤ −30) y VENTA FUERTE (≤ −70).
- La tarjeta «Por qué este puntaje» muestra cada regla, si está a favor (✓) o en contra (✗), y sus puntos.

**Calibrar con xStation.** El CFD de XTB no cotiza igual que el feed (futuro o contado), pero la diferencia es casi
constante en el día.
1. Escribe el precio que ves en xStation (el BID, el de los gráficos).
2. La app guarda `desfase = precio_xtb − precio_feed`. Lo calcula contra la vela de 1 minuto del feed de **esa misma
   hora**, no contra el último precio del feed, que tiene 10 minutos de retraso. Por eso queda «pendiente» unos minutos.
3. Desde ahí, el gráfico, la entrada, el SL y el TP salen en precios de xStation.

Recalibra cada día y tras el vencimiento de un futuro, cuando el desfase salta. Cada fuente tiene su propio desfase.

**Ticket listo para XTB.**
- Entrada = precio actual; SL = entrada ∓ k × ATR14 (1,5 por defecto); TP = entrada ± k × ATR14 (3,0 por defecto).
- Lotes = riesgo en USD / (distancia del SL en puntos × valor del punto), redondeado **hacia abajo** a 0,01. Nunca
  arriesga más de lo pedido.
- Si ni 0,01 lotes cabe en tu riesgo, el ticket lo dice y muestra cuánto arriesgaría el lote mínimo.
- También muestra el riesgo real, el objetivo, la relación R:R y un margen aproximado (ESMA: 5 % índices principales
  y oro, 10 % plata y Russell 2000).
- Si xStation ya se movió, conserva las **distancias** en puntos.

| CFD | USD por punto (1 lote) | | CFD | USD por punto (1 lote) |
|---|---|---|---|---|
| GOLD | 100 (100 oz) | | US100 | 20 |
| SILVER | 5000 (5000 oz) | | US30 | 5 |
| US500 | 50 | | US2000 | 200 |

Valores de la tabla de especificaciones de XTB. Se corrigen en la pestaña, en «Contrato por activo», o en
`config.yaml` → `cfd.assets`. Ahí también se agrega el spread en puntos, que se suma al riesgo.

**Alertas por Telegram.** Se activan en la pestaña, apagadas por defecto. Cada 60 s, con el mercado abierto, se
revisan los 6 activos en tu temporalidad. Llega un aviso cuando el puntaje **entra** en ±70 (umbral ajustable), con el
ticket. Hay un solo aviso por activo, vela y dirección; si la señal sigue fuerte, no se repite. Los avisos quedan
también en la pestaña Alertas. Para recibirlos en el teléfono, configura `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

**API:**
- `GET /api/cfd/overview?tf=5m` (escáner)
- `GET /api/cfd/assets/GOLD?tf=1m` (detalle, velas y reglas)
- `PUT /api/cfd/settings`
- `POST /api/cfd/calibrate` (`{"asset": "GOLD", "xtb_price": 2650.3}` o `{"asset": "GOLD", "manual_offset": -18.4}`)
- `DELETE /api/cfd/calibrate/GOLD`
- `POST /api/cfd/scan`

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
│       └── components/    Watchlist, SignalCard, PriceChart, ScoreChart, Metrics, BacktestPanel,
│                          PortfolioView, ContributionCard, TransactionsCard, AlertsView
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
    │   ├── portfolio.py   posiciones, valorización CLP/USD, reparto de aportes (funciones puras)
    │   ├── portfolio_service.py  operaciones en SQLite, importación del CSV de la PWA
    │   ├── alerts.py      reglas de alerta, Telegram y email
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
