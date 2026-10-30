# Windwise

A small analytics dashboard for reviewing Great Britain wind forecasts against actual generation for January 2026.

Windwise evaluates the forecast that was available before a configurable day-ahead purchasing cutoff, rather than selecting a later forecast retrospectively. It is an analysis demo, not a trading system.

## Data and assumptions

The backend reads two public Elexon BMRS Insights streaming datasets from `https://data.elexon.co.uk/bmrs/api/v1/datasets`:

- [FUELHH](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream), filtered to `fuelType=WIND`.
- [WINDFOR](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream).

The implementation calls each dataset's `/stream` endpoint with `publishDateTimeFrom` and `publishDateTimeTo`. FUELHH requests also pass `fuelType=WIND`. These parameters bound publication time, not delivery `startTime`.

For the January 2026 decision window, the code requests FUELHH publications from `2026-01-01T00:00:00Z` to `2026-02-02T00:00:00Z`, and WINDFOR publications from `2025-12-31T00:00:00Z` to `2026-02-02T00:00:00Z`. Those bounds include the records needed for delivery on 2 January–1 February and forecasts published before January 1. The response schemas were inspected using the API documentation and live January 2026 responses.

Both datasets contain `publishTime`, `startTime`, and `generation`. FUELHH also contains `fuelType`, `settlementDate`, and `settlementPeriod`.

The observed FUELHH rows are half-hourly, while WINDFOR rows are hourly. Each hourly forecast is scored against the mean of its two corresponding half-hour FUELHH readings. If either reading is missing, that forecast is excluded from scoring. Chart actuals retain their original half-hour values.

Null and unmatched records are excluded from the accuracy metrics. All generation values and error metrics are expressed in MW.

The stream responses are parsed as JSON arrays. The backend retries HTTP 429/5xx, URL/timeouts, and invalid or undecodable responses for up to three attempts, then returns an API error. It keeps fetched source rows in a Python process memory cache for 15 minutes by default (`CACHE_SECONDS=900`). There is no database or durable Elexon data file. On Vercel, each running backend instance has its own volatile cache; instances do not share it, and a new instance can fetch again before another instance's cache expires. Forecast selection and metrics are recalculated for each `/api/forecast` request from the rows in that instance's cache.

## Day-ahead cutoff

`DAY_AHEAD_CUTOFF` defaults to `15:30` in `Europe/London`, using EPEX's GB 30-minute day-ahead auction as the reference product.

- EPEX's [2025 trading timeline](https://www.epexspot.com/sites/default/files/2025-02/EPEX%20SPOT%20Trading%20Brochure%202025%20February.pdf) lists the GB half-hourly auction at 15:30 GMT. Its GB hourly auction closes at 09:20 GMT.
- Nord Pool's [GB half-hour auction information](https://support.nordpoolgroup.com/support/solutions/articles/8000077433-about-the-gb-half-hour-auction) lists a 15:30 CET gate closure (14:30 UK local time). Its [GB product specifications](https://www.nordpoolgroup.com/4ab576/globalassets/download-center/rules-and-regulations/product-specifications-gb-market.pdf), effective 10 July 2023, list the half-hour auction at 14:30 GMT/BST.
- [Nord Pool's N2EX documentation](https://support.nordpoolgroup.com/support/solutions/articles/8000088463-about-the-n2ex-day-ahead-auction) lists 09:50 GMT / 10:50 CET for N2EX. For UK-local configuration, that is 09:50 in winter and 10:50 during British Summer Time.
- Nord Pool's [GB market rules effective 1 January 2026](https://www.nordpoolgroup.com/48e283/globalassets/download-center/rules-and-regulations/day-ahead-market-regulations-gb-market.pdf) distinguish the GB hourly and half-hourly auctions.

There is no single cutoff shared by every GB trading venue or purchasing process. The 15:30 default is an EPEX GB half-hour auction proxy based on the linked EPEX 2025 trading timeline; it is not a claim about a utility's contractual purchasing deadline. Nord Pool has separate GB hourly and half-hourly auctions with separate gate closures. Gate closure can also be moved by an operational notice. Always check the current product timetable, any operational notice, and the buyer's internal decision deadline before using the selected cutoff.

Set `DAY_AHEAD_CUTOFF` to the relevant venue's cutoff (`HH:MM`, `Europe/London` local time) when it differs. The dashboard's time control can also override the default for an analysis session. For the January 2026 analysis dates, UK local time is GMT.

## Decision-date and forecast selection logic

The selected dates represent January 2026 purchase decision dates.

For each delivery interval, the backend:

1. Converts `startTime` to its `Europe/London` local date.
2. Subtracts one local calendar day to determine the purchase decision date.
3. Includes the interval if that decision date falls within the selected date range.
4. Selects the latest forecast whose `publishTime` is at or before (inclusive) the configured cutoff on that decision date.

For each delivery timestamp, the newest forecast published after the cutoff is shown separately and is not used to calculate the primary accuracy metrics. FUELHH duplicate `startTime` rows are reduced to the latest published row. Timestamps are compared as UTC instants after parsing their offsets.

Therefore, selecting decision dates from 1–31 January covers delivery from 2 January through 1 February.

The expected score denominator is 24 hourly forecasts per selected decision date.

## Accuracy metrics

- **MAE:** Mean Absolute Error. The average absolute difference between forecast and actual generation, representing the typical forecast miss.
- **RMSE:** Root Mean Squared Error. Gives larger forecast misses more weight than MAE.
- **Bias:** Average forecast minus actual generation. A positive value indicates overforecasting; a negative value indicates underforecasting.
- **Coverage:** Scored hourly matches divided by the 24 expected hours per selected decision date.

## Illustrative additional-buy calculation

A demand forecast can be loaded from a local JSON file in the dashboard:

```json
[
  {
    "startTime": "2026-01-02T00:00:00Z",
    "demandMW": 12345
  }
]
```

Rows require timezone-aware ISO `startTime` values and finite, non-negative numeric `demandMW`; duplicate timestamps are rejected. Timestamps are normalized to UTC and must exactly match a selected hourly wind forecast. File content stays in browser memory only: it is not uploaded, persisted to browser storage, or written to the backend.

For matched hours, the dashboard displays an illustrative additional-buy quantity:

```text
max(0, demandMW - cutoff wind forecast MW)
```

This is only a residual energy-balance illustration. Demand, other generation, reserves, network constraints, losses, and existing commercial positions are outside the Elexon-only scope. The result should not be interpreted as an actual procurement recommendation.

## Run locally

Requirements:

- Python 3.11+
- Node.js 20+

Start the backend:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn main:app --app-dir backend --reload
```

In another terminal, start the frontend:

```sh
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

Vite proxies `/api` requests to the local FastAPI server.

Set `DAY_AHEAD_CUTOFF=HH:MM` before starting the backend to change the default decision cutoff. `CACHE_SECONDS` controls the per-process in-memory upstream cache duration; the default is 900 seconds. The dashboard cutoff control overrides the backend default per request.

## Docker

The project includes separate frontend and backend Dockerfiles and a Docker Compose configuration.

Build and start both services:

```sh
docker compose up --build
```

Open [http://localhost:8080](http://localhost:8080). The API is also exposed on port 8000.

To override the cutoff, create a `.env` file in the project root:

```env
DAY_AHEAD_CUTOFF=09:50
```

This example uses the N2EX hourly cutoff. Choose the appropriate UK local time for the venue and product being analyzed.

Stop the services with:

```sh
docker compose down
```

Docker Compose provides a reproducible local two-service deployment. It is separate from the Vercel deployment.

## Tests

Run the backend tests:

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/pytest -q backend
```

Tests cover:

- Decision-date boundaries
- Cutoff-based forecast selection
- Hourly actual aggregation from half-hourly readings
- Separate handling of forecasts published after cutoff
- Exclusion of unmatched records
- Accuracy metric calculations
- Wind-only actual generation

## Project structure

```text
backend/
  main.py                 FastAPI, Elexon ingestion, selection, metrics
  test_main.py            Essential analysis tests
  requirements.txt        Python runtime and test dependencies
  .dockerignore           Files excluded from backend Docker builds
  Dockerfile              Python API container
  Dockerfile.vercel       Python API container for Vercel Services

frontend/
  src/
    App.tsx               Dashboard, chart, cutoff control, demand input
    style.css             Dashboard styling
    main.tsx              React application entry point
    vite-env.d.ts         Vite environment types
  index.html              Vite HTML shell
  package.json            Frontend scripts and dependencies
  package-lock.json       Locked npm dependency versions
  vite.config.ts          Vite dev server and local API proxy
  tsconfig*.json          TypeScript project configurations
  .dockerignore           Files excluded from frontend Docker builds
  Dockerfile              Static frontend container
  Dockerfile.vercel       Static frontend container for Vercel Services
  nginx.conf              Local Docker frontend and `/api` proxy
  nginx.vercel.conf       Vercel frontend static-file server

.gitignore                Local dependencies, build outputs, and secrets exclusions
docker-compose.yml        Local two-service setup
vercel.json               Vercel Services and path rewrites
```

## Deployment

`vercel.json` declares two Vercel Services, each built with its `Dockerfile.vercel`: the frontend and the FastAPI backend. The `/api/(.*)` rewrite routes API requests to the backend; all other paths route to the frontend. This setup requires the Vercel project to use the Services framework setting ([Vercel Services guide](https://vercel.com/kb/guide/vercel-services)). Both services share one Vercel URL; they are separate from the local Docker Compose stack. The frontend uses same-origin `/api` by default; if `VITE_API_URL` is set at build time, it overrides that path.

**Vercel deployment URL:** [Windwise](https://windwise-r8oes63vc-trishna-kashyaps-projects.vercel.app/)

GitHub Repository - https://github.com/trishna0703/hhgdyh.git


## AI disclosure

Built with assistance from OpenAI Codex. Data schemas and market-timing references were checked against the Elexon, EPEX, and Nord Pool sources linked above.
