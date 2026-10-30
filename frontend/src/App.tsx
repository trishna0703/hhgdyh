import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, Brush } from 'recharts'

type Point = {
  startTime: string
  actual: number | null
  metricActual: number | null
  forecast: number | null
  forecastPublishedAt: string | null
  laterForecast: number | null
  laterForecastPublishedAt: string | null
}
type Data = {
  points: Point[]
  metrics: { mae: number | null; rmse: number | null; bias: number | null; observations: number }
  coverage: { expectedIntervals: number; actualIntervals: number; matchedIntervals: number; percent: number }
  cutoff: string
  timezone: string
  fetchedAt: string
}

const api = import.meta.env.VITE_API_URL || ''
const fmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 })
const timeFmt = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' })
const num = (value: number | null | undefined) => value == null ? '—' : `${fmt.format(value)} MW`

function App() {
  const [from, setFrom] = useState('2026-01-01')
  const [to, setTo] = useState('2026-01-31')
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const [cutoff, setCutoff] = useState('')
  const [demand, setDemand] = useState<Map<string, number>>(new Map())
  const [demandError, setDemandError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    fetch(`${api}/api/forecast?start=${from}&end=${to}${cutoff ? `&cutoff=${cutoff}` : ''}`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error((await response.json()).detail || 'Could not load forecast data.')
        return response.json() as Promise<Data>
      })
      .then(setData)
      .catch(reason => { if (reason.name !== 'AbortError') setError(reason.message || 'Could not load forecast data.') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [from, to, cutoff, reload])

  const chart = useMemo(() => (data?.points ?? []).map(point => ({
    ...point,
    label: timeFmt.format(new Date(point.startTime)),
  })), [data])
  const averages = useMemo(() => {
    const matched = chart.filter(p => p.metricActual != null && p.forecast != null)
    const actuals = matched.map(p => p.metricActual!)
    const forecasts = matched.map(p => p.forecast!)
    return {
      actual: actuals.length ? actuals.reduce((a, b) => a + b, 0) / actuals.length : null,
      forecast: forecasts.length ? forecasts.reduce((a, b) => a + b, 0) / forecasts.length : null,
    }
  }, [chart])
  const procurement = useMemo(() => {
    const values = chart.flatMap(p => {
      const requested = demand.get(new Date(p.startTime).toISOString())
      return requested != null && p.forecast != null ? [Math.max(0, requested - p.forecast)] : []
    })
    return { average: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null, matched: values.length }
  }, [chart, demand])

  async function loadDemand(file?: File) {
    if (!file) return
    try {
      const rows: unknown = JSON.parse(await file.text())
      if (!Array.isArray(rows)) throw new Error('Expected a JSON array of hourly demand records.')
      const parsed = new Map<string, number>()
      for (const row of rows) {
        if (!row || typeof row.startTime !== 'string' || !Number.isFinite(Date.parse(row.startTime)) || !row.startTime.match(/(Z|[+-]\d{2}:\d{2})$/) || typeof row.demandMW !== 'number' || !Number.isFinite(row.demandMW) || row.demandMW < 0) throw new Error('Each row needs a timezone-aware startTime and non-negative numeric demandMW.')
        const key = new Date(row.startTime).toISOString()
        if (parsed.has(key)) throw new Error(`Duplicate demand timestamp: ${row.startTime}`)
        parsed.set(key, row.demandMW)
      }
      setDemand(parsed)
      setDemandError('')
    } catch (reason) { setDemandError(reason instanceof Error ? reason.message : 'Could not read demand file.') }
  }

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#" aria-label="Windwise home"><span className="brand-mark"><i /><i /><i /></span><span>windwise</span></a>
      <div className="header-right"><span className="period-label">HISTORICAL ANALYSIS</span><span className="period-value">JANUARY 2026</span><span className="avatar">UK</span></div>
    </header>

    <section className="intro">
      <div><div className="eyebrow"><span className="live-dot" /> NATIONAL WIND · GREAT BRITAIN</div><h1>Forecast performance</h1><p>Choose January purchase decision dates; charted delivery can extend through 1 February.</p></div>
      <div className="range-picker">
        <label>DECISION FROM<input aria-label="Decision from date" type="date" min="2026-01-01" max="2026-01-31" value={from} onChange={e => { setFrom(e.target.value); if (e.target.value > to) setTo(e.target.value) }} /></label>
        <span className="range-arrow">→</span>
        <label>TO<input aria-label="Decision to date" type="date" min={from} max="2026-01-31" value={to} onChange={e => setTo(e.target.value)} /></label>
        <label>CUTOFF (UK)<input aria-label="Market cutoff UK time" type="time" value={cutoff || data?.cutoff || '15:30'} onChange={e => setCutoff(e.target.value)} /></label>
      </div>
    </section>

    {loading && <div className="state-card"><span className="spinner" /> Loading January 2026 data from Elexon…</div>}
    {error && <div className="state-card error-state"><strong>Data unavailable</strong><span>{error}</span><button onClick={() => setReload(value => value + 1)}>Retry</button></div>}
    {!loading && !error && data && <>
      <section className="metrics-grid" aria-label="Summary metrics">
        <Metric title="AVERAGE ACTUAL" value={num(averages.actual)} subtitle="Hourly average of two FUELHH readings" icon="↗" />
        <Metric title="AVERAGE DAY-AHEAD" value={num(averages.forecast)} subtitle={`Latest by ${data.cutoff} UK time, previous day`} icon="◷" />
        <Metric title="MEAN ABSOLUTE ERROR" value={num(data.metrics.mae)} subtitle={`${fmt.format(data.metrics.observations)} matched intervals`} icon="±" accent />
        <Metric title="ROOT MEAN SQUARED ERROR" value={num(data.metrics.rmse)} subtitle="Penalises larger misses more" icon="⌁" />
        <Metric title="AVERAGE ADDITIONAL BUY" value={num(procurement.average)} subtitle={`${procurement.matched} hours with demand and wind forecast`} icon="＋" />
      </section>

      <section className="panel demand-panel">
        <div className="panel-heading"><div><h2>Demand forecast input</h2><p>Load your hourly demand forecast to estimate the residual buy beyond wind.</p></div><label className="demand-upload">Choose JSON<input aria-label="Upload demand forecast JSON" type="file" accept="application/json,.json" onChange={e => loadDemand(e.target.files?.[0])} /></label></div>
        <p className="demand-note">Expected format: [{`{"startTime":"2026-01-02T00:00:00Z","demandMW":12345}`}]. Demand data stays in this browser. Additional buy = max(0, demand − cutoff wind forecast); this is a simple energy balance, not a complete procurement plan.</p>
        {demandError && <p className="demand-error" role="alert">{demandError}</p>}
      </section>

      <section className="panel chart-panel">
        <div className="panel-heading"><div><h2>Generation over time</h2><p>Actual generation versus forecast available at the day-ahead cutoff</p></div><div className="unit-pill">MW <span>·</span> 30-minute actuals</div></div>
        <div className="legend"><span><i className="legend-dot actual" />Actual generation</span><span><i className="legend-dot cutoff" />Decision forecast</span><span><i className="legend-dot later" />Later published forecast</span></div>
        {chart.length ? <div className="chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart} margin={{ top: 14, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid stroke="#edf0ec" vertical={false} />
          <XAxis dataKey="label" minTickGap={55} tick={{ fill: '#8b9590', fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis width={62} tickFormatter={v => `${Math.round(v / 1000)}k`} tick={{ fill: '#8b9590', fontSize: 11 }} tickLine={false} axisLine={false} />
          <Tooltip content={<ChartTooltip />} />
          <Line type="monotone" dataKey="actual" name="Actual generation" stroke="#233d32" strokeWidth={2.2} dot={false} connectNulls={false} activeDot={{ r: 4 }} />
          <Line type="monotone" dataKey="forecast" name="Decision forecast" stroke="#238366" strokeWidth={2.2} dot={false} connectNulls={false} activeDot={{ r: 4 }} />
          <Line type="monotone" dataKey="laterForecast" name="Later published forecast" stroke="#c4a968" strokeWidth={1.5} strokeDasharray="4 5" dot={false} connectNulls={false} activeDot={{ r: 3 }} />
          <Brush dataKey="label" height={22} stroke="#c8d6ce" fill="#f7f9f7" travellerWidth={8} />
        </LineChart></ResponsiveContainer></div> : <div className="empty-state">No records were returned for this date range.</div>}
        <div className="chart-foot"><span><b className="footer-dot" /> Cutoff forecast uses the latest publication at or before {data.cutoff} Europe/London on the previous local day.</span><span>Forecast points remain hourly where Elexon publishes hourly values.</span></div>
      </section>

      <section className="bottom-grid">
        <div className="panel accuracy-panel"><div className="panel-heading"><div><h2>Accuracy at decision time</h2><p>Only matched actual and cutoff-eligible forecasts are scored.</p></div><span className="accuracy-icon">⌁</span></div>
          <div className="accuracy-stats"><div><span>FORECAST BIAS</span><strong className={data.metrics.bias != null && data.metrics.bias < 0 ? 'negative' : ''}>{data.metrics.bias == null ? '—' : `${data.metrics.bias > 0 ? '+' : ''}${fmt.format(data.metrics.bias)} MW`}</strong><small>{data.metrics.bias == null ? 'No matched values' : data.metrics.bias > 0 ? 'Forecasts higher than actuals' : data.metrics.bias < 0 ? 'Forecasts lower than actuals' : 'Forecasts match actuals on average'}</small></div><div><span>OBSERVATIONS</span><strong>{fmt.format(data.metrics.observations)}</strong><small>Hourly intervals matched</small></div></div>
          <div className="metric-explainer"><span className="info">i</span><p><b>MAE</b> is the typical absolute miss. <b>RMSE</b> gives larger misses more weight. <b>Bias</b> shows the average direction of error.</p></div>
        </div>
        <div className="panel coverage-panel"><div className="panel-heading"><div><h2>Data coverage</h2><p>January interval completeness</p></div><span className="coverage-badge">{data.coverage.percent}%</span></div>
          <div className="coverage-track"><i style={{ width: `${Math.min(data.coverage.percent, 100)}%` }} /></div>
          <div className="coverage-counts"><span><b>{fmt.format(data.coverage.matchedIntervals)}</b> matched</span><span><b>{fmt.format(data.coverage.expectedIntervals)}</b> expected</span></div>
          <div className="freshness"><span className="fresh-icon">↻</span><div><b>Source checked</b><small>{new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(data.fetchedAt))}</small></div><span className="source-tag">ELEXON API</span></div>
        </div>
      </section>
      <footer className="disclaimer">For forecast evaluation only · Not for automated or financial trading decisions</footer>
    </>}
  </main>
}

function Metric({ title, value, subtitle, icon, accent = false }: { title: string; value: string; subtitle: string; icon: string; accent?: boolean }) {
  return <article className={`metric-card${accent ? ' accent-card' : ''}`}><div className="metric-top"><span>{title}</span><i>{icon}</i></div><strong>{value}</strong><small>{subtitle}</small></article>
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  const point = payload?.[0]?.payload
  if (!active || !point) return null
  return <div className="tooltip"><b>{timeFmt.format(new Date(point.startTime))} UK</b>
    <span><i className="legend-dot actual" />Actual <strong>{num(point.actual)}</strong></span>
    {point.metricActual != null && point.metricActual !== point.actual && <small>Scored actual (two half-hours avg): {num(point.metricActual)}</small>}
    <span><i className="legend-dot cutoff" />Decision forecast <strong>{num(point.forecast)}</strong></span>
    {point.forecastPublishedAt && <small>Published {timeFmt.format(new Date(point.forecastPublishedAt))}</small>}
    <span><i className="legend-dot later" />Later forecast <strong>{num(point.laterForecast)}</strong></span>
    {point.laterForecastPublishedAt && <small>Published {timeFmt.format(new Date(point.laterForecastPublishedAt))}</small>}
  </div>
}

export default App
