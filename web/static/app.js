import { html, render, useState, useEffect, useMemo, useRef, useCallback } from '/static/vendor/preact-htm.module.js';

// ---------------------------------------------------------------- api
async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}
const api = {
  get: (p) => request('GET', p),
  post: (p, b) => request('POST', p, b),
  put: (p, b) => request('PUT', p, b),
};

function useFetch(path, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const reload = useCallback(() => {
    setState((s) => ({ ...s, loading: true }));
    api.get(path).then(
      (data) => setState({ data, error: null, loading: false }),
      (error) => setState({ data: null, error, loading: false }),
    );
  }, [path]);
  useEffect(reload, [path, ...deps]);
  return { ...state, reload };
}

// ---------------------------------------------------------------- routing
function useRoute() {
  const parse = () => (location.hash.replace(/^#\/?/, '') || 'library').split('/');
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const onHash = () => { setRoute(parse()); window.scrollTo(0, 0); };
    addEventListener('hashchange', onHash);
    return () => removeEventListener('hashchange', onHash);
  }, []);
  return route;
}
const go = (path) => { location.hash = '#/' + path; };

// ---------------------------------------------------------------- live events
function useLive() {
  const [jobs, setJobs] = useState([]);
  const [logs, setLogs] = useState([]);
  const [tick, setTick] = useState(0); // cambia cuando algún job modifica la biblioteca
  useEffect(() => {
    let ws, retry, closed = false;
    api.get('/api/logs').then(setLogs).catch(() => {});
    const connect = () => {
      ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'hello') setJobs(msg.jobs);
        else if (msg.type === 'job') setJobs((js) => js.map((j) => (j.name === msg.job.name ? msg.job : j)));
        else if (msg.type === 'log') setLogs((ls) => [...ls.slice(-999), msg]);
        else if (msg.type === 'series_updated' || msg.type === 'job_finished') setTick((t) => t + 1);
      };
      ws.onclose = () => { if (!closed) retry = setTimeout(connect, 2000); };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws && ws.close(); };
  }, []);
  return { jobs, logs, tick };
}

// ---------------------------------------------------------------- helpers
const Icon = ({ d, size = 18 }) => html`<svg class="icon" viewBox="0 0 24 24" width=${size} height=${size}>${d.split('|').map((p) => html`<path d=${p} />`)}</svg>`;
const ICONS = {
  library: 'M4 5h4v14H4z|M10 5h4v14h-4z|M16.5 5.5l3.5-1 3 13.5-3.5 1z',
  wanted: 'M12 8v4l3 2|M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  system: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z|M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z|M21 21l-4.3-4.3',
  back: 'M15 18l-6-6 6-6',
  play: 'M6 4l14 8-14 8z',
  stop: 'M6 6h12v12H6z',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z|M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  eyeoff: 'M17.9 17.9A10.7 10.7 0 0 1 12 20c-7 0-11-8-11-8a19.8 19.8 0 0 1 5.1-5.9|M9.9 4.2A10 10 0 0 1 12 4c7 0 11 8 11 8a19.6 19.6 0 0 1-2.2 3.2|M1 1l22 22',
  refresh: 'M21 12a9 9 0 1 1-2.6-6.4L21 8|M21 3v5h-5',
};

const qualityLabel = (h) => (!h ? null : h >= 2000 ? '4K' : h >= 1000 ? '1080p' : h >= 700 ? '720p' : 'SD');
const displayName = (it) => it.tmdb_name || it.name;
const fmtDate = (s) => (s ? new Date(s.replace(' ', 'T')).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }) : '—');
const PALETTE = ['#3b4a6b', '#4b3b6b', '#6b3b55', '#3b6b62', '#6b5a3b', '#3b5c6b', '#58406b'];
const phColor = (id) => PALETTE[id % PALETTE.length];

function Poster({ item }) {
  const [failed, setFailed] = useState(false);
  const q = qualityLabel(item.max_resolution);
  const showImg = item.has_cover && !failed;
  return html`
    <div class="poster" style=${showImg ? '' : `background: linear-gradient(160deg, ${phColor(item.id)}, #161922)`}>
      ${showImg
        ? html`<img loading="lazy" decoding="async" src=${`/api/series/${item.id}/cover`} alt="" onError=${() => setFailed(true)} />`
        : html`<div class="ph">${displayName(item)}</div>`}
      <div class="badges">
        <span>${q && html`<span class=${'badge' + (q === '4K' ? ' q4k' : '')}>${q}</span>`}</span>
        <span>
          ${item.needs_review ? html`<span class="badge violet">Revisar</span>`
            : item.missing_count > 0 ? html`<span class="badge warn">−${item.missing_count}</span>` : null}
        </span>
      </div>
      ${item.content_mode === 'episodic' && item.local_count + item.missing_count > 0 && html`
        <div class="bar"><span style=${`width:${(100 * item.local_count) / (item.local_count + item.missing_count)}%;background:${item.missing_count ? 'var(--warn)' : 'var(--ok)'}`}></span></div>`}
    </div>`;
}

// ---------------------------------------------------------------- library
const FILTERS = [
  { id: 'all', label: 'Todo', test: () => true },
  { id: 'missing', label: 'Con faltantes', test: (i) => i.missing_count > 0 },
  { id: 'review', label: 'Revisar match', test: (i) => i.needs_review },
  { id: 'nocover', label: 'Sin fuente', test: (i) => !i.providers.length },
  { id: 'unmonitored', label: 'No vigilados', test: (i) => !i.monitored },
];
const SORTS = {
  name: { label: 'Nombre', fn: (a, b) => displayName(a).localeCompare(displayName(b), 'es') },
  year: { label: 'Año', fn: (a, b) => (b.year || 0) - (a.year || 0) },
  missing: { label: 'Faltantes', fn: (a, b) => b.missing_count - a.missing_count },
  updated: { label: 'Actualizado', fn: (a, b) => (b.updated_at || '').localeCompare(a.updated_at || '') },
};
const PAGE = 120;

function Library({ category, query, tick, categories }) {
  const { data, loading, error, reload } = useFetch('/api/library', [tick]);
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState(() => localStorage.getItem('sort') || 'name');
  const [shown, setShown] = useState(PAGE);
  const sentinel = useRef(null);

  const base = useMemo(() => (data || []).filter((i) => category === 'all' || i.content_category === category), [data, category]);
  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    const f = FILTERS.find((x) => x.id === filter);
    return base
      .filter((i) => f.test(i) && (!q || displayName(i).toLowerCase().includes(q) || (i.name || '').toLowerCase().includes(q)))
      .sort(SORTS[sort].fn);
  }, [base, filter, sort, query]);

  useEffect(() => setShown(PAGE), [category, filter, sort, query]);
  useEffect(() => {
    if (!sentinel.current) return;
    const io = new IntersectionObserver((e) => e[0].isIntersecting && setShown((n) => n + PAGE), { rootMargin: '800px' });
    io.observe(sentinel.current);
    return () => io.disconnect();
  }, [sentinel.current, items]);

  if (loading && !data) return html`<div class="loading"><div class="spinner"></div></div>`;
  if (error) return html`<div class="empty">Error cargando la biblioteca: ${error.message} <br/><br/><button class="btn" onClick=${reload}>Reintentar</button></div>`;

  return html`
    <div class="chips">
      ${[{ id: 'all', label: 'Todas', count: (data || []).length }, ...categories.filter((c) => c.count)].map((c) => html`
        <a class=${'chip' + (category === c.id ? ' on' : '')} href=${'#/library/' + c.id}>${c.label} <span class="n">${c.count}</span></a>`)}
    </div>
    <div class="toolbar">
      ${FILTERS.map((f) => html`
        <button class=${'chip' + (filter === f.id ? ' on' : '')} onClick=${() => setFilter(f.id)}>
          ${f.label} <span class="n">${base.filter(f.test).length}</span>
        </button>`)}
      <span style="flex:1"></span>
      <span class="muted">${items.length} elementos</span>
      <select class="select" value=${sort} onChange=${(e) => { setSort(e.target.value); localStorage.setItem('sort', e.target.value); }}>
        ${Object.entries(SORTS).map(([k, s]) => html`<option value=${k}>${s.label}</option>`)}
      </select>
    </div>
    ${items.length === 0
      ? html`<div class="empty">Nada que mostrar con estos filtros.</div>`
      : html`
        <div class="grid">
          ${items.slice(0, shown).map((it) => html`
            <a class=${'card' + (it.monitored ? '' : ' unmonitored')} href=${'#/series/' + it.id} key=${it.id}>
              <${Poster} item=${it} />
              <div class="title" title=${displayName(it)}>${displayName(it)}</div>
              <div class="sub"><span>${it.year || '—'}</span><span>·</span><span>${it.category_label}</span></div>
            </a>`)}
        </div>
        ${shown < items.length && html`<div ref=${sentinel} style="height:1px"></div>`}`}`;
}

// ---------------------------------------------------------------- detail
function SeriesDetail({ id, tick, toast }) {
  const { data: it, loading, error, reload } = useFetch(`/api/series/${id}`, [tick]);
  const [busy, setBusy] = useState(false);
  if (loading && !it) return html`<div class="loading"><div class="spinner"></div></div>`;
  if (error) return html`<div class="empty">${error.message}</div>`;

  const local = it.seasons.reduce((n, s) => n + s.local.length, 0);
  const missing = it.seasons.reduce((n, s) => n + s.missing.length, 0);
  const best = Math.max(0, ...it.seasons.flatMap((s) => s.local.map((e) => e.resolution_height || 0)));
  const cover = it.cover_url ? `/api/series/${it.id}/cover` : null;

  const toggleMonitored = async () => {
    setBusy(true);
    try {
      await api.put(`/api/series/${it.id}/monitored`, { monitored: !it.monitored });
      toast(it.monitored ? 'Ya no se vigila' : 'Vigilando');
      reload();
    } catch (e) { toast('Error: ' + e.message); }
    setBusy(false);
  };

  return html`
    <div class="hero">
      ${cover && html`<div class="hero-bg" style=${`background-image:url(${cover})`}></div>`}
      <div><${Poster} item=${{ ...it, has_cover: !!cover, missing_count: 0, local_count: 0, max_resolution: best }} /></div>
      <div>
        <div class="faint">${it.category_label}</div>
        <h2>${displayName(it)} ${it.year && html`<span class="faint" style="font-weight:400">(${it.year})</span>`}</h2>
        <div class="meta">
          ${it.tmdb_id ? html`<a href=${`https://www.themoviedb.org/${it.content_mode === 'episodic' ? 'tv' : 'movie'}/${it.tmdb_id}`} target="_blank" rel="noopener"><b>TMDB</b> ${it.tmdb_id}</a>` : html`<span>Sin TMDB</span>`}
          ${it.confidence_score != null && html`<span>Confianza <b>${Math.round(it.confidence_score * 100)}%</b></span>`}
          ${it.needs_review ? html`<span class="pill" style="background:rgba(167,139,250,.15);color:var(--violet)">Revisar match</span>` : null}
          ${!it.monitored && html`<span class="pill">No vigilado</span>`}
          ${it.search_disabled ? html`<span class="pill bad">Búsqueda desactivada</span>` : null}
        </div>
        <div class="path">${it.folder_path}</div>
        ${it.content_mode === 'episodic' && html`
          <div class="stats">
            <div class="stat"><div class="k">En disco</div><div class="v">${local}</div></div>
            <div class="stat"><div class="k">Faltan</div><div class="v" style=${missing ? 'color:var(--warn)' : ''}>${missing}</div></div>
            <div class="stat"><div class="k">Mejor calidad</div><div class="v">${qualityLabel(best) || '—'}</div></div>
            <div class="stat"><div class="k">Fuentes</div><div class="v">${it.offers.length + (it.hispashare_url && !it.offers.some((o) => o.provider === 'hispashare') ? 1 : 0)}</div></div>
          </div>`}
        <div class="actions" style=${it.content_mode === 'episodic' ? '' : 'margin-top:18px'}>
          <button class="btn" disabled=${busy} onClick=${toggleMonitored}>
            <${Icon} d=${it.monitored ? ICONS.eyeoff : ICONS.eye} /> ${it.monitored ? 'Dejar de vigilar' : 'Vigilar'}
          </button>
          ${it.hispashare_url && html`<a class="btn" href=${it.hispashare_url} target="_blank" rel="noopener">Abrir en Hispashare</a>`}
        </div>
      </div>
    </div>

    ${it.seasons.length > 0 && html`
      <div class="section">
        <h3>Temporadas
          <span class="legend">
            <span><span class="ep">1</span>En disco</span>
            <span><span class="ep q4k">1</span>En 4K</span>
            <span><span class="ep missing">1</span>Falta</span>
          </span>
        </h3>
        <div class="panel">
          ${it.seasons.map((s) => {
            const total = s.local.length + s.missing.length;
            const eps = [
              ...s.local.map((e) => ({ ...e, ok: true })),
              ...s.missing.map((e) => ({ ...e, ok: false })),
            ].sort((a, b) => a.episode - b.episode);
            return html`
              <div class="season">
                <div class="season-head">
                  ${s.season === 0 ? 'Especiales' : `Temporada ${s.season}`}
                  <span class="muted">${s.local.length}/${total}</span>
                  <div class="progress"><span style=${`width:${(100 * s.local.length) / total}%`}></span></div>
                </div>
                <div class="eps">
                  ${eps.map((e) => html`
                    <span class=${'ep' + (!e.ok ? ' missing' : e.resolution_height >= 2000 ? ' q4k' : '')}
                      title=${e.ok ? `${e.filename}${e.resolution_height ? ' · ' + qualityLabel(e.resolution_height) : ''}` : `Falta${e.name ? ': ' + e.name : ''}${e.air_date ? ' · ' + e.air_date : ''}`}>
                      ${e.episode}
                    </span>`)}
                </div>
              </div>`;
          })}
        </div>
      </div>`}

    ${it.offers.length > 0 && html`
      <div class="section">
        <h3>Fuentes conocidas</h3>
        <div class="panel">
          <table class="list">
            <thead><tr><th>Proveedor</th><th>Calidad</th><th>Estado</th><th></th></tr></thead>
            <tbody>
              ${it.offers.map((o) => html`
                <tr>
                  <td><b>${o.provider}</b></td>
                  <td>${qualityLabel(o.resolution) || '?'} ${o.codec ? html`<span class="faint">· ${o.codec}</span>` : null} ${o.hdr !== 'unknown' ? html`<span class="faint">· ${o.hdr}</span>` : null}</td>
                  <td>${o.available ? html`<span class="pill ok">Disponible</span>` : html`<span class="pill bad">No disponible</span>`}</td>
                  <td style="text-align:right">${o.source_url && html`<a class="btn small" href=${o.source_url} target="_blank" rel="noopener">Abrir</a>`}</td>
                </tr>`)}
            </tbody>
          </table>
        </div>
      </div>`}

    ${it.history.length > 0 && html`
      <div class="section">
        <h3>Historial</h3>
        <div class="panel">
          <table class="list">
            <tbody>
              ${it.history.map((h) => html`
                <tr><td class="faint mono" style="width:150px">${fmtDate(h.created_at)}</td><td>${h.message}</td><td class="faint mono">${h.decision_type}</td></tr>`)}
            </tbody>
          </table>
        </div>
      </div>`}`;
}

// ---------------------------------------------------------------- wanted
function Wanted({ query, tick }) {
  const { data, loading } = useFetch('/api/wanted', [tick]);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data || []).filter((r) => !q || r.series_name.toLowerCase().includes(q));
  }, [data, query]);
  if (loading && !data) return html`<div class="loading"><div class="spinner"></div></div>`;
  if (!rows.length) return html`<div class="empty">No falta nada. 🎉</div>`;
  return html`
    <div class="toolbar"><span class="muted">
      ${rows.length} episodios faltantes, más recientes primero${rows.length > 500 ? ' · mostrando 500 (usa el buscador para filtrar)' : ''}
    </span></div>
    <div class="panel">
      <table class="list">
        <thead><tr><th>Serie</th><th>Episodio</th><th>Título</th><th>Emisión</th></tr></thead>
        <tbody>
          ${rows.slice(0, 500).map((r) => html`
            <tr class="click" onClick=${() => go('series/' + r.series_id)}>
              <td><b>${r.series_name}</b></td>
              <td class="mono">${String(r.season).padStart(2, '0')}x${String(r.episode).padStart(2, '0')}</td>
              <td class="dim">${r.name || '—'}</td>
              <td class="faint">${r.air_date || '—'}</td>
            </tr>`)}
        </tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------- activity
function JobCard({ job, toast }) {
  const [busy, setBusy] = useState(false);
  const act = async (verb) => {
    setBusy(true);
    try { await api.post(`/api/jobs/${job.name}/${verb}`); } catch (e) { toast(e.message); }
    setBusy(false);
  };
  return html`
    <div class="panel job">
      <div class="job-head">
        <span class=${'dot' + (job.running ? ' on' : '')}></span>
        <h4>${job.label}</h4>
        <span style="flex:1"></span>
        ${job.running
          ? html`<button class="btn small danger" disabled=${busy || job.stopping} onClick=${() => act('stop')}><${Icon} d=${ICONS.stop} size=${14} /> ${job.stopping ? 'Deteniendo…' : 'Detener'}</button>`
          : html`<button class="btn small primary" disabled=${busy} onClick=${() => act('start')}><${Icon} d=${ICONS.play} size=${14} /> Iniciar</button>`}
      </div>
      <div class="msg">${job.message || (job.running ? '…' : 'Detenido')}</div>
      <div class="bigbar"><span style=${`width:${job.running ? job.percent : 0}%`}></span></div>
      <div class="foot">
        <span>${job.running ? `${job.percent}%` : job.finished_at ? `Última ejecución: ${fmtDate(job.finished_at)}` : 'Sin ejecutar'}</span>
        ${job.last_error && html`<span style="color:var(--bad)" title=${job.last_error}>Con errores</span>`}
      </div>
    </div>`;
}

function Activity({ live, toast }) {
  const box = useRef(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [live.logs]);
  return html`
    <div class="jobs">${live.jobs.map((j) => html`<${JobCard} job=${j} toast=${toast} key=${j.name} />`)}</div>
    <div class="section">
      <h3>Registro en directo</h3>
      <div class="console" ref=${box} onScroll=${(e) => { const el = e.target; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
        ${live.logs.length === 0 && html`<div class="faint">Sin actividad todavía. Inicia una tarea para ver aquí su progreso.</div>`}
        ${live.logs.map((l) => html`<div class="line"><span class="t">${(l.time || '').slice(11, 19)}</span>${l.message}</div>`)}
      </div>
    </div>`;
}

// ---------------------------------------------------------------- system
function System() {
  const { data } = useFetch('/api/status');
  if (!data) return html`<div class="loading"><div class="spinner"></div></div>`;
  return html`
    <div class="section" style="margin-top:0">
      <h3>Proveedores</h3>
      <div class="panel">
        <table class="list">
          <thead><tr><th>Proveedor</th><th>Entrega</th><th>Estado</th></tr></thead>
          <tbody>
            ${data.providers.map((p) => html`
              <tr><td><b>${p.label}</b></td><td class="dim">${p.protocol}</td>
              <td>${p.enabled ? html`<span class="pill ok">Activo</span>` : html`<span class="pill">Inactivo</span>`}</td></tr>`)}
          </tbody>
        </table>
      </div>
    </div>
    <div class="section">
      <h3>Bibliotecas</h3>
      <div class="panel">
        <table class="list">
          <thead><tr><th>Biblioteca</th><th>Ruta</th><th>Estado</th></tr></thead>
          <tbody>
            ${data.library_roots.map((r) => html`
              <tr><td><b>${r.label}</b></td><td class="mono dim">${r.path}</td>
              <td>${r.accessible ? html`<span class="pill ok">Accesible</span>` : html`<span class="pill bad">No accesible</span>`}</td></tr>`)}
          </tbody>
        </table>
      </div>
    </div>
    <div class="section">
      <h3>Clientes de descarga</h3>
      <div class="panel">
        <table class="list">
          <thead><tr><th>Cliente</th><th>Destino</th><th>Estado</th></tr></thead>
          <tbody>
            ${data.download_clients.map((c) => html`
              <tr><td><b>${c.label}</b></td><td class="mono dim">${c.target || '—'}</td>
              <td>${c.configured ? html`<span class="pill ok">Configurado</span>` : html`<span class="pill bad">Sin configurar</span>`}</td></tr>`)}
          </tbody>
        </table>
      </div>
    </div>
    <div class="section">
      <h3>General</h3>
      <div class="panel">
        <table class="list"><tbody>
          <tr><td>TMDB</td><td>${data.tmdb_configured ? html`<span class="pill ok">API key configurada</span>` : html`<span class="pill bad">Falta API key</span>`}</td></tr>
          <tr><td>Intervalo del ciclo automático</td><td>${data.check_interval_hours} h</td></tr>
          <tr><td>Rutas de biblioteca</td><td>
            ${data.library_paths.ok
              ? html`<span class="pill ok">Coinciden</span>`
              : html`<span class="pill bad">No coinciden — ciclo bloqueado</span>`}
            <span class="faint" style="margin-left:10px">${data.library_paths.in_configured_roots} de ${data.library_paths.total} elementos dentro de las rutas configuradas</span>
          </td></tr>
        </tbody></table>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- shell
const NAV = [
  { id: 'library', label: 'Biblioteca', icon: ICONS.library },
  { id: 'wanted', label: 'Buscados', icon: ICONS.wanted },
  { id: 'activity', label: 'Actividad', icon: ICONS.activity },
  { id: 'system', label: 'Sistema', icon: ICONS.system },
];

function App() {
  const route = useRoute();
  const live = useLive();
  const [query, setQuery] = useState('');
  const [msg, setMsg] = useState(null);
  const { data: categories } = useFetch('/api/categories', [live.tick]);
  const toast = useCallback((m) => { setMsg(m); setTimeout(() => setMsg(null), 2600); }, []);

  const [page, arg] = route;
  const running = live.jobs.find((j) => j.running);
  const titles = { library: 'Biblioteca', wanted: 'Buscados', activity: 'Actividad', system: 'Sistema', series: '' };
  const searchable = page === 'library' || page === 'wanted';

  let body;
  if (page === 'series') body = html`<${SeriesDetail} id=${arg} tick=${live.tick} toast=${toast} />`;
  else if (page === 'wanted') body = html`<${Wanted} query=${query} tick=${live.tick} />`;
  else if (page === 'activity') body = html`<${Activity} live=${live} toast=${toast} />`;
  else if (page === 'system') body = html`<${System} />`;
  else body = html`<${Library} category=${arg || 'all'} query=${query} tick=${live.tick} categories=${categories || []} />`;

  return html`
    <div class="shell">
      <aside class="sidebar">
        <div class="brand"><span class="brand-mark"><${Icon} d=${ICONS.library} /></span> Biblioteca</div>
        <nav class="nav">
          ${NAV.map((n) => html`
            <a href=${'#/' + n.id} class=${page === n.id || (n.id === 'library' && page === 'series') ? 'active' : ''}>
              <${Icon} d=${n.icon} /> ${n.label}
              ${n.id === 'activity' && running && html`<span class="count">${running.percent}%</span>`}
            </a>`)}
        </nav>
        <div class="sidebar-foot">${running ? `● ${running.label}` : 'Inactivo'}</div>
      </aside>
      <main class="main">
        <header class="topbar">
          ${page === 'series'
            ? html`<button class="btn small" onClick=${() => history.back()}><${Icon} d=${ICONS.back} size=${16} /> Volver</button>`
            : html`<h1>${titles[page] || 'Biblioteca'}</h1>`}
          <span class="spacer"></span>
          ${running && html`
            <a class="topjob" href="#/activity" title=${running.message}>
              <span class="dot on"></span><span class="txt">${running.message}</span>
              <div class="bigbar"><span style=${`width:${running.percent}%`}></span></div>
            </a>`}
          ${searchable && html`
            <label class="search"><${Icon} d=${ICONS.search} size=${16} />
              <input placeholder="Buscar…" value=${query} onInput=${(e) => setQuery(e.target.value)} />
            </label>`}
        </header>
        <div class="content">${body}</div>
      </main>
      ${msg && html`<div class="toast">${msg}</div>`}
    </div>`;
}

render(html`<${App} />`, document.getElementById('app'));
