import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, Clock3, Database, Droplets, Flag, Globe2, History, Info, Layers3, LoaderCircle, Map as MapIcon, MapPin, Mountain, PanelRightClose, RefreshCw, Search, TrendingUp, TriangleAlert, X } from 'lucide-react';
import MapView from './MapView';
import RainChart from './RainChart';
import { SourceCard } from './Evidence';
import { api, query } from './api';
import { date, nepalDay, number, RISK_COLORS, RISK_LABELS, RISK_LEVELS, time } from './format';
import type { Cell, EventRecord, Forecast, LocationResult, Manifest, Place, Source, Status } from './types';

function Mark({ small = false }: { small?: boolean }) {
  return <div className={small ? 'brand-mark small' : 'brand-mark'}><svg viewBox="0 0 40 40" fill="none" aria-hidden="true"><path d="m5 29 12-18 8 12 4-7 8 13H5Z" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round"/><path d="m13 17 4 5 4-4" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"/></svg></div>;
}

function SearchBox({ onSelect }: { onSelect: (place: Place) => void }) {
  const [value, setValue] = useState('');
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [active, setActive] = useState(-1);
  const wrapper = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (value.trim().length < 2) { setResults([]); setLoading(false); return; }
    const abort = new AbortController();
    setLoading(true); setError(false); setActive(-1);
    const id = window.setTimeout(() => {
      api<{ results: Place[] }>(`/search?${query({ q: value })}`, abort.signal)
        .then(data => setResults(data.results))
        .catch(e => { if (e.name !== 'AbortError') { setError(true); setResults([]); } })
        .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    }, 250);
    return () => { window.clearTimeout(id); abort.abort(); };
  }, [value]);
  useEffect(() => {
    const handler = (e: MouseEvent) => { if (!wrapper.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);
  const select = (place: Place) => { onSelect(place); setValue(place.name); setOpen(false); };
  return <div className="search-wrap" ref={wrapper}>
    <div className="search-box"><Search size={17} /><input aria-label="Search places or coordinates" role="combobox" aria-expanded={open && value.trim().length >= 2} aria-controls="place-results" aria-activedescendant={active >= 0 ? `place-${active}` : undefined} placeholder="Search places or coordinates…" value={value} onFocus={() => setOpen(true)} onChange={e => { setValue(e.target.value); setOpen(true); }} onKeyDown={e => {
      if (e.key === 'Escape') setOpen(false);
      if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(a => Math.min(results.length - 1, a + 1)); }
      if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(0, a - 1)); }
      if (e.key === 'Enter' && results[active >= 0 ? active : 0]) select(results[active >= 0 ? active : 0]);
    }} />{loading ? <LoaderCircle className="spin" size={15} /> : value ? <button aria-label="Clear search" onClick={() => { setValue(''); setResults([]); }}><X size={14} /></button> : <kbd>⌕</kbd>}</div>
    {open && value.trim().length >= 2 && <div id="place-results" role="listbox" className="search-results">
      {results.map((place, i) => <button role="option" aria-selected={i === active} id={`place-${i}`} className={i === active ? 'selected' : ''} key={`${place.name}-${place.lat}-${place.lon}`} onClick={() => select(place)}><MapPin size={16}/><span><strong>{place.name}</strong><small>{place.kind} · {number(place.lat, 3)}° N, {number(place.lon, 3)}° E</small></span><ArrowUpRight size={15}/></button>)}
      {!results.length && <p>{loading ? 'Finding locations…' : error ? 'Search is temporarily unavailable.' : 'No location found. Try a city or lat, lon.'}</p>}
    </div>}
  </div>;
}

function Metric({ label, value, unit, icon, caption }: { label: string; value: number | null | undefined; unit: string; icon: React.ReactNode; caption?: string }) {
  return <div className="environment-metric"><span className="metric-icon">{icon}</span><span className="metric-copy"><span className="metric-label">{label}</span><strong>{number(value, value != null && value < 10 && value > 0 ? 1 : 0)} <small>{unit}</small></strong>{caption && <span className="metric-caption">{caption}</span>}</span></div>;
}

function CellPanel({ cell, location, manifest, loading, onClose }: { cell: Cell; location: LocationResult | null; manifest: Manifest | null; loading: boolean; onClose: () => void }) {
  const p = cell.properties;
  const percentile = manifest?.score_type === 'relative_hazard_percentile';
  const probability = !percentile && p.probability != null;
  const rainfall = location?.rainfall?.length ? location.rainfall : p.rainfall_series || [];
  const observationEnd = typeof p.input_sources?.rainfall_observed_through === 'string' ? p.input_sources.rainfall_observed_through : manifest?.data_cutoff;
  return <aside className="detail-panel" aria-label="Selected location">
    <div className="panel-title"><span className="eyebrow"><MapPin size={13}/> Location details</span><button className="icon-button" aria-label="Close location details" onClick={onClose}><PanelRightClose size={18}/></button></div>
    <div className="location-heading"><h2>{p.place_name || `Cell ${p.cell_id}`}</h2><p className="coordinates">{number(p.lat, 3)}° N <span>/</span> {number(p.lon, 3)}° E <span className="coordinate-cross">⌖</span></p></div>
    <div className={`risk-card risk-${p.risk_level || 'unavailable'}`}>
      <div className="risk-card-top"><span>Model assessment</span><span className="risk-pill"><i style={{ background: RISK_COLORS[p.risk_level] || RISK_COLORS.unavailable }}/>{RISK_LABELS[p.risk_level] || 'Unavailable'}</span></div>
      <div className="score-value">{number(probability ? p.probability! * 100 : p.score != null ? p.score * 100 : null, 1)}<span>{probability ? '%' : '/ 100'}</span></div>
      <p>{percentile ? 'Relative hazard percentile' : probability ? 'Recorded-event probability' : 'Relative hazard score'}</p>
      <div className="score-track"><span style={{ width: `${Math.max(0, Math.min(100, (probability ? p.probability || 0 : p.score || 0) * 100))}%` }}/></div>
      <small>{percentile ? 'Ranked against 2024 calibration data.' : probability ? 'For a reported landslide in this cell and target day.' : 'Relative model output.'}</small>
    </div>
    <div className="detail-section environment-section"><div className="section-heading"><h3>Environmental conditions</h3><Droplets size={15}/></div>
      <div className="environment-grid">
        <Metric label="Rainfall · 24h" value={p.rain_24h_mm} unit="mm" icon={<Droplets size={17}/>} />
        <Metric label="Rainfall · 72h" value={p.rain_72h_mm} unit="mm" icon={<History size={17}/>} />
        <Metric label="Median elevation" value={p.elevation_m} unit="m" icon={<Mountain size={17}/>} />
        <Metric label="Terrain slope" value={p.slope_deg} unit="°" icon={<TrendingUp size={17}/>} />
      </div>
      {(p.forecast_rain_24h_mm != null || p.soil_moisture != null) && <div className="extra-readings">{p.forecast_rain_24h_mm != null && <p><span>GFS weather context · 24h</span><strong>{number(p.forecast_rain_24h_mm, 1)} mm</strong></p>}{p.soil_moisture != null && <p><span>Soil moisture estimate</span><strong>{number(p.soil_moisture, 3)} m³/m³</strong></p>}</div>}
    </div>
    <div className="detail-section"><div className="section-heading"><h3>Antecedent rainfall</h3><span className="subtle-label">{rainfall.length ? `${rainfall.length} observations` : 'Source unavailable'}</span></div><RainChart data={rainfall}/>{p.rain_7d_mm != null && <p className="rain-total">7-day accumulation <strong>{number(p.rain_7d_mm, 1)} mm</strong></p>}</div>
    <div className="detail-section provenance-section"><div className="section-heading"><h3>Observation times</h3><Clock3 size={15}/></div><p className="provenance-line"><span>{manifest?.mode === 'historical' ? 'Input age at simulated issue' : 'Input age at issue'}</span><strong>{p.input_age_hours == null ? 'Unavailable' : `${number(p.input_age_hours, 2)} h`}</strong></p>{observationEnd && <p className="provenance-line"><span>Rainfall observed through</span><strong>{date(observationEnd, { year: undefined })} · {time(observationEnd)} NPT</strong></p>}</div>
    {loading && <div className="panel-loading"><LoaderCircle size={14} className="spin"/> Loading location history…</div>}
  </aside>;
}

function OverviewPanel({ manifest, forecast, onPick }: { manifest: Manifest | null; forecast: Forecast | null; onPick: (lat: number, lon: number) => void }) {
  const cells = forecast?.features || [];
  const featured = [...cells].filter(cell => cell.properties.score != null).sort((a, b) => b.properties.score! - a.properties.score!).slice(0, 3);
  const total = manifest?.cell_count || cells.length;
  return <aside className="detail-panel overview-panel" aria-label="National overview">
    <div className="panel-title"><span className="eyebrow"><Globe2 size={13}/> National overview</span></div>
    <div className="overview-intro"><p>Select a cell to view rainfall and model estimates.</p></div>
    <div className="coverage-card"><div><span className="eyebrow">Modeled coverage</span><strong>{total ? number(total) : '—'} <span>grid cells</span></strong></div><Layers3 size={27}/><small>{manifest ? `${number(manifest.grid_resolution_degrees, 2)}° grid resolution` : 'Waiting for a published run'}</small></div>
    {manifest && <div className="detail-section"><div className="section-heading"><h3>National distribution</h3><span className="subtle-label">Cells</span></div><div className="distribution-bar">{RISK_LEVELS.map(level => <span title={`${RISK_LABELS[level]}: ${manifest.summary?.[level] || 0}`} key={level} style={{ background: RISK_COLORS[level], width: `${total ? (manifest.summary?.[level] || 0) / total * 100 : 0}%` }}/>)}</div><div className="distribution-list">{RISK_LEVELS.map(level => <div key={level}><span><i style={{ background: RISK_COLORS[level] }}/>{RISK_LABELS[level]}</span><strong>{number(manifest.summary?.[level] || 0)}</strong></div>)}</div></div>}
    {featured.length > 0 && <div className="detail-section"><div className="section-heading"><h3>Higher-score cells</h3><ArrowUpRight size={15}/></div><div className="featured-cells">{featured.map(cell => <button key={cell.properties.cell_id} onClick={() => onPick(cell.properties.lat, cell.properties.lon)}><MapPin size={16}/><span><strong>{cell.properties.place_name || `Cell ${cell.properties.cell_id}`}</strong><small>{number(cell.properties.lat, 2)}° N, {number(cell.properties.lon, 2)}° E</small></span><span className="cell-dot" style={{ background: RISK_COLORS[cell.properties.risk_level] }}/><ChevronRight size={14}/></button>)}</div></div>}
  </aside>;
}

function Drawer({ kind, sources, sourcesError, onClose }: { kind: 'sources' | 'about'; sources: Source[]; sourcesError: string | null; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'Tab') {
        const focusable = dialog.current?.querySelectorAll<HTMLElement>('button, a[href], input, select, [tabindex="0"]');
        if (!focusable?.length) return;
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, [onClose]);
  const title = kind === 'sources' ? 'Data sources' : 'About this project';
  return <div className="drawer-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><div className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" tabIndex={-1} ref={dialog}>
    <div className="drawer-header"><Mark small/><span className="eyebrow">Landslide Watch</span><button className="icon-button" aria-label="Close panel" onClick={onClose}><X size={21}/></button></div>
    <div className="drawer-heading"><h2 id="drawer-title">{title}</h2>
      {kind === 'sources' ? <>
        <p>There is so much data about rainfall, terrain and past disasters, but it is scattered across different places. I wanted to explore whether bringing it together could help us better understand where landslides might happen.</p>
        <p>These are the sources I used to build and test the model.</p>
      </> : <p>I’m Ishika Bohara, a high school student at The Times Secondary School in Kathmandu, exploring how machine learning can help us understand landslide risk. I wanted to build something that connects what I am learning with a problem that matters to people. I’ve connected the project to near-real-time satellite rainfall data, with automatic checks for new observations every 30 minutes.</p>}
    </div>
    {kind === 'sources' && <>{sourcesError && <div className="inline-error">{sourcesError}</div>}{!sources.length && !sourcesError && <div className="empty-metadata"><Database size={28}/><p>No source metadata has been published yet.</p></div>}{sources.map((source, i) => <SourceCard source={source} index={i} key={String(source.id || source.name || i)}/>)}</>}
    {kind === 'about' && <>
      <section className="drawer-section"><h3>Why landslides?</h3><p className="long-copy">Nepal stretches from the low plains of the Terai to the steep slopes of the Himalayas. Its active tectonic setting exposes it to earthquakes, while heavy monsoon rains can bring floods and destabilize hillsides. These hazards can affect homes, roads and the connections between communities.</p><p className="long-copy motivation-paragraph">That vulnerability gives this project its purpose. I wanted to understand whether the rainfall and terrain data we already collect could help us recognize landslide risk earlier. Landslides became the focus of my attempt to turn that question into something I could build, test and share.</p></section>
      <section className="drawer-section"><h3>What I hope to learn</h3><p className="long-copy">I want to find out whether recent rainfall helps explain landslide patterns beyond what the landscape alone can tell us. Building this map has given me a way to work with real data, compare models and share what I learn.</p><p className="long-copy motivation-paragraph">I hope to keep improving this project and contribute to work that can help communities better understand landslide risk.</p></section>
      <p className="long-copy project-note">This is my student research project, not an official warning service.</p>
    </>}
  </div></div>;
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [runs, setRuns] = useState<Manifest[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [eventsTotal, setEventsTotal] = useState<number | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [eventRetry, setEventRetry] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [forecastLoading, setForecastLoading] = useState(false);
  const [gridReady, setGridReady] = useState(false);
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [forecastError, setForecastError] = useState<string | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [cell, setCell] = useState<Cell | null>(null);
  const [location, setLocation] = useState<LocationResult | null>(null);
  const [locationLoading, setLocationLoading] = useState(false);
  const [focus, setFocus] = useState<{ lat: number; lon: number; nonce: number } | null>(null);
  const [showEvents, setShowEvents] = useState(false);
  const [showGrid, setShowGrid] = useState(true);
  const [showHazard, setShowHazard] = useState(true);
  const [drawer, setDrawer] = useState<'sources' | 'about' | null>(null);
  const [autoLatest, setAutoLatest] = useState(true);
  const [refreshToken, setRefreshToken] = useState(0);
  const [mobileDetails, setMobileDetails] = useState(false);
  const [timelineCount, setTimelineCount] = useState(() => window.innerWidth <= 520 ? 3 : window.innerWidth <= 860 ? 4 : 5);
  useEffect(() => {
    const resize = () => setTimelineCount(window.innerWidth <= 520 ? 3 : window.innerWidth <= 860 ? 4 : 5);
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const locationRequest = useRef<AbortController | null>(null);
  const selectedPosition = useRef<{ lat: number; lon: number } | null>(null);
  const latestRunId = useRef<string | null>(null);
  const autoLatestRef = useRef(autoLatest);
  autoLatestRef.current = autoLatest;

  const loadMetadata = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true);
    // Start the map as soon as its run ID arrives. Optional drawer metadata must
    // not hold up predictions if one of those independent requests is slow.
    await Promise.allSettled([
      api<Status>('/status').then(next => {
        setStatus(next); setServiceError(null);
        const id = next.latest_run?.run_id || null;
        if (autoLatestRef.current) setRunId(id);
        latestRunId.current = id;
        if (manual) setRefreshToken(value => value + 1);
      }).catch(error => {
        setServiceError(error instanceof Error ? error.message : 'Unable to reach the data service.');
      }).finally(() => { setLoading(false); setRefreshing(false); }),
      api<{ runs: Manifest[] }>('/runs').then(data => setRuns([...data.runs].sort((a, b) => a.valid_from.localeCompare(b.valid_from)))),
      api<{ sources: Source[] }>('/sources').then(data => {
        setSources(data.sources); setSourcesError(null);
      }).catch(() => setSourcesError('Source metadata is currently unavailable.')),

    ]);
  }, []);

  useEffect(() => { void loadMetadata(); }, [loadMetadata]);
  useEffect(() => {
    const id = window.setInterval(async () => {
      try {
        const next = await api<Status>('/status');
        setStatus(next); setServiceError(null);
        if ((next.latest_run?.run_id || null) !== latestRunId.current) void loadMetadata();
      } catch { setServiceError('The data service is unreachable. Showing the last loaded run.'); }
    }, 60000);
    return () => window.clearInterval(id);
  }, [loadMetadata]);

  // Colors, dates, reports and click inspection refer to the same loaded run,
  // including while a different archive date is still being downloaded.
  const displayedRunId = forecast?.manifest?.run_id || runId;
  const pickLocation = useCallback(async (lat: number, lon: number) => {
    locationRequest.current?.abort();
    const abort = new AbortController(); locationRequest.current = abort;
    selectedPosition.current = { lat, lon };
    setLocationLoading(true); setLocationError(null); setLocation(null);
    try {
      const result = await api<LocationResult>(`/location?${query({ lat, lon, run_id: displayedRunId || undefined })}`, abort.signal);
      if (!abort.signal.aborted) {
        setLocation(result); setCell(result.cell); setMobileDetails(Boolean(result.cell));
        if (!result.cell) setLocationError('This point is outside the mapped grid. Select a colored cell to explore its data.');
      }
    } catch (error) {
      if (!abort.signal.aborted) { setCell(null); setLocationError(error instanceof Error ? error.message : 'Location data is unavailable.'); }
    } finally { if (!abort.signal.aborted) setLocationLoading(false); }
  }, [displayedRunId]);

  useEffect(() => {
    if (!runId) { setForecast(null); setCell(null); setLocation(null); return; }
    const abort = new AbortController();
    const loadedRun = forecast?.manifest?.run_id;
    setForecastLoading(true); setForecastError(null);
    api<Forecast>(`/forecast?${query({ run_id: runId })}`, abort.signal)
      .then(result => {
        // Run IDs are immutable. An unchanged refresh must not rebuild the
        // map's worker source or erase the currently selected location.
        if (!abort.signal.aborted && result.manifest?.run_id !== loadedRun) {
          setCell(null); setLocation(null); setForecast(result);
        }
      })
      .catch(error => { if (!abort.signal.aborted) setForecastError(error.message); })
      .finally(() => { if (!abort.signal.aborted) setForecastLoading(false); });
    return () => { abort.abort(); locationRequest.current?.abort(); };
  }, [runId, refreshToken]);

  useEffect(() => {
    if (forecast && selectedPosition.current) void pickLocation(selectedPosition.current.lat, selectedPosition.current.lon);
  }, [forecast, pickLocation]);

  const manifest = forecast?.manifest || runs.find(run => run.run_id === runId) || (status?.latest_run?.run_id === runId ? status.latest_run : null);
  const awaitingForecast = !!runId && !forecast && !forecastError;
  const activeIndex = runs.findIndex(run => run.run_id === runId);
  const timelineStart = Math.max(0, Math.min(activeIndex - Math.floor(timelineCount / 2), runs.length - timelineCount));
  const visibleRuns = runs.slice(timelineStart, timelineStart + timelineCount);
  const eventStart = manifest ? nepalDay(manifest.valid_from) : null;
  const eventEnd = manifest ? nepalDay(new Date(new Date(manifest.valid_to).getTime() - 1).toISOString()) : null;
  useEffect(() => {
    setEvents([]); setEventsTotal(null); setEventsError(null);
    if (!showEvents || !eventStart || !eventEnd) { setEventsLoading(false); return; }
    const abort = new AbortController();
    const cap = 5000;
    setEventsLoading(true);
    async function loadEvents() {
      const loaded: EventRecord[] = [];
      let total: number | null = null;
      try {
        while (loaded.length < cap) {
          const page = await api<{ events: EventRecord[]; total: number; offset: number; limit: number }>(`/events?${query({ start: eventStart!, end: eventEnd!, bbox: '80,26,89,31', limit: Math.min(1000, cap - loaded.length), offset: loaded.length })}`, abort.signal);
          if (abort.signal.aborted) return;
          total = page.total;
          loaded.push(...page.events);
          if (!page.events.length && loaded.length < total) throw new Error('The catalogue returned an incomplete page.');
          if (!page.events.length || loaded.length >= total) break;
        }
        if (!abort.signal.aborted) { setEvents(loaded); setEventsTotal(total); }
      } catch (error) {
        if (!abort.signal.aborted) {
          setEvents(loaded); setEventsTotal(total);
          setEventsError(error instanceof Error ? error.message : 'Reports could not be loaded.');
        }
      } finally { if (!abort.signal.aborted) setEventsLoading(false); }
    }
    void loadEvents();
    return () => abort.abort();
  }, [showEvents, eventStart, eventEnd, refreshToken, eventRetry]);
  const chooseRun = (id: string) => { setAutoLatest(false); setRunId(id); };
  const pickAndFocus = (lat: number, lon: number) => { setFocus({ lat, lon, nonce: Date.now() }); void pickLocation(lat, lon); };
  const closeDrawer = useCallback(() => setDrawer(null), []);
  const closeLocation = () => { locationRequest.current?.abort(); selectedPosition.current = null; setCell(null); setLocation(null); setLocationLoading(false); setMobileDetails(false); };

  return <div className="app-shell">
    <aside className="sidebar">
      <a href="#" className="brand" aria-label="Nepal Landslide Watch home" onClick={e => { e.preventDefault(); closeLocation(); setDrawer(null); }}><Mark/><span>Nepal<span>Landslide Watch<span className="brand-period">.</span></span></span></a>
      <nav className="navigation" aria-label="Main navigation">
        <button className={!drawer ? 'nav-item active' : 'nav-item'} onClick={() => setDrawer(null)}><MapIcon size={19}/><span>Explore map</span><span className="nav-active-dot"/></button>
        <button className={drawer === 'sources' ? 'nav-item active' : 'nav-item'} onClick={() => setDrawer('sources')}><Database size={18}/><span>Data sources</span></button>
        <button className={drawer === 'about' ? 'nav-item active' : 'nav-item'} onClick={() => setDrawer('about')}><Info size={18}/><span>About this project</span></button>
      </nav>
    </aside>
    <main className="workspace">
      <header className="main-header"><div className="mobile-brand"><Mark small/></div><div className="header-title"><h1>Landslide monitoring</h1></div></header>
      <div className="workspace-toolbar"><SearchBox onSelect={place => pickAndFocus(place.lat, place.lon)}/><div className="toolbar-end"><label className={`event-toggle ${showEvents ? 'enabled' : ''}`}><input type="checkbox" checked={showEvents} onChange={e => setShowEvents(e.target.checked)}/><span className="switch"/><Flag size={14}/><span>Reported events</span></label><span className="toolbar-divider"/><button className="refresh-button" onClick={() => void loadMetadata(true)} disabled={refreshing} title="Refresh published data"><RefreshCw size={15} className={refreshing ? 'spin' : ''}/><span>Refresh</span></button></div></div>
      <div className="explorer-body">
        <section className="map-pane" aria-label="Landslide map">
          <MapView forecast={forecast} selected={cell} events={events} showEvents={showEvents} showGrid={showGrid} showHazard={showHazard} focus={focus} onPick={(lat, lon) => void pickLocation(lat, lon)} onToggleGrid={() => setShowGrid(value => !value)} onToggleHazard={() => setShowHazard(value => !value)} onForecastReadyChange={setGridReady}/>
          <div className="map-run-card"><div className="run-date">{manifest ? date(manifest.valid_from) : 'Select a date'}<span className="window-tag">24h window</span></div><p>{manifest ? `${time(manifest.valid_from)} → ${time(manifest.valid_to)} NPT · ${date(manifest.valid_to)}` : 'Select a published run.'}</p></div>
          {showEvents && <div className={`event-notice ${eventsError ? 'event-error' : ''}`} role="status">{eventsLoading ? <LoaderCircle size={13} className="spin"/> : eventsError ? <TriangleAlert size={13}/> : <span className="event-marker"/>}<span>{eventsLoading ? 'Loading target-day reports…' : eventsError ? `${events.length ? `${number(events.length)} reports loaded; ` : ''}Reports unavailable` : !manifest ? 'Choose a published target day' : eventsTotal === 0 ? `No located reports · ${date(manifest.valid_from)}` : `${number(events.length)}${eventsTotal != null && events.length < eventsTotal ? ` of ${number(eventsTotal)}` : ''} located reports · ${date(manifest.valid_from)}`}{!eventsLoading && !eventsError && eventsTotal != null && events.length < eventsTotal && <small>Display capped at 5,000 reports.</small>}</span>{eventsError && <button title={eventsError} onClick={() => setEventRetry(value => value + 1)}>Retry <RefreshCw size={11}/></button>}</div>}
          {!gridReady && !forecastError && !serviceError && (loading || forecastLoading || awaitingForecast || !!forecast) && <div className="map-loading" role="status"><LoaderCircle size={20} className="spin"/><span>{forecast ? 'Drawing the colored grid…' : 'Loading the colored grid…'}</span></div>}
          {!forecast && !loading && !awaitingForecast && !forecastLoading && <div className="map-message"><div className="map-message-icon"><Database size={27}/></div><h2>{serviceError ? 'Data connection unavailable' : forecastError ? 'This run could not be loaded' : 'No published runs'}</h2><p>{serviceError || forecastError || 'No model run has been published yet. Once the real-data pipeline completes, its estimates will appear here.'}</p><button onClick={() => void loadMetadata(true)}><RefreshCw size={14}/> Try again</button></div>}
          {forecast && (forecastLoading || refreshing || forecastError || serviceError) && !locationLoading && !locationError && <div className="location-toast" role="status">{forecastError || serviceError ? <Info size={15}/> : <LoaderCircle size={15} className="spin"/>}<span>{forecastError || serviceError ? 'Update unavailable.' : 'Loading the selected run…'} Showing {manifest ? date(manifest.valid_from) : 'the last loaded grid'}.</span></div>}
          {(locationError || locationLoading) && <div className="location-toast" role="status">{locationLoading ? <LoaderCircle size={15} className="spin"/> : <Info size={16}/>}<span>{locationLoading ? 'Inspecting this location…' : locationError}</span>{!locationLoading && <button aria-label="Dismiss location message" onClick={() => setLocationError(null)}><X size={14}/></button>}</div>}
          <div className="map-bottom"><div className="map-legend"><div><span className="legend-title">{manifest?.score_type === 'relative_hazard_percentile' ? 'RELATIVE HAZARD PERCENTILE' : manifest?.score_type === 'recorded_event_probability' ? 'RECORDED-EVENT HAZARD' : 'RELATIVE HAZARD'}</span></div><div className="legend-gradient">{RISK_LEVELS.map(level => <span key={level} style={{ background: RISK_COLORS[level] }}/>)}</div><div className="legend-labels">{RISK_LEVELS.map(level => <span key={level}>{RISK_LABELS[level]}</span>)}</div><p><span className="unavailable-dot"/>Grey = unavailable <span>·</span> Click a cell to inspect</p></div></div>
          <button className="mobile-detail-toggle" onClick={() => setMobileDetails(value => !value)}><Layers3 size={16}/>{cell ? 'Location details' : 'National overview'}<ChevronRight size={16}/></button>
        </section>
        <div className={`detail-wrap ${mobileDetails ? 'mobile-open' : ''}`}>{mobileDetails && <button className="mobile-panel-close" aria-label="Close details" onClick={() => setMobileDetails(false)}><X size={18}/></button>}{cell ? <CellPanel cell={cell} location={location} manifest={manifest} loading={locationLoading} onClose={closeLocation}/> : <OverviewPanel manifest={manifest} forecast={forecast} onPick={pickAndFocus}/>}</div>
      </div>
      <section className="timeline" aria-label="Date selection"><div className="timeline-label"><CalendarDays size={19}/><div><strong>Date</strong></div></div><div className="timeline-track"><button className="timeline-arrow" aria-label="Previous run" disabled={activeIndex <= 0} onClick={() => activeIndex > 0 && chooseRun(runs[activeIndex - 1].run_id)}><ChevronLeft size={18}/></button><div className="timeline-dates">{visibleRuns.length ? visibleRuns.map(run => <button key={run.run_id} className={`timeline-date ${run.run_id === runId ? 'selected' : ''}`} onClick={() => chooseRun(run.run_id)}><span>{date(run.valid_from, { weekday: 'short', day: undefined, month: undefined, year: undefined })}</span><strong>{date(run.valid_from, { year: undefined })}</strong><i/></button>) : <span className="empty-timeline">Available dates will appear here.</span>}</div><button className="timeline-arrow" aria-label="Next run" disabled={activeIndex < 0 || activeIndex >= runs.length - 1} onClick={() => activeIndex >= 0 && activeIndex < runs.length - 1 && chooseRun(runs[activeIndex + 1].run_id)}><ChevronRight size={18}/></button></div><div className="timeline-actions"><label className="run-select"><CalendarDays size={14}/><select aria-label="Select a date" value={runId || ''} onChange={e => chooseRun(e.target.value)} disabled={!runs.length}>{!runs.length && <option value="">Select date</option>}{[...runs].reverse().map(run => <option key={run.run_id} value={run.run_id}>{date(run.valid_from)}</option>)}</select><ChevronDown size={13}/></label><button className={`latest-button ${autoLatest ? 'following' : ''}`} onClick={() => { setAutoLatest(true); setRunId(status?.latest_run?.run_id || null); }} disabled={!status?.latest_run}>{autoLatest ? <Check size={13}/> : <History size={13}/>}Latest run</button></div></section>
      <footer className="status-footer project-footer"><span>© 2026 Ishika Bohara</span></footer>
    </main>
    {drawer && <Drawer kind={drawer} sources={sources} sourcesError={sourcesError} onClose={closeDrawer}/>}
  </div>;
}
