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
  calendar: 'M3 6h18v15H3z|M3 10h18|M8 3v4|M16 3v4',
  sparkle: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z|M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z',
  sliders: 'M4 21v-7|M4 10V3|M12 21v-9|M12 8V3|M20 21v-5|M20 12V3|M1 14h6|M9 8h6|M17 16h6',
};

const qualityLabel = (h) => (!h ? null : h >= 2000 ? '4K' : h >= 1000 ? '1080p' : h >= 700 ? '720p' : 'SD');
const displayName = (it) => it.tmdb_name || it.name;
const hdrLabel = (h) => ({ dolby_vision: 'Dolby Vision', hdr10: 'HDR10' })[h] || null;
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
          ${item.placeholder ? html`<span class="badge violet">En camino</span>`
            : item.needs_review ? html`<span class="badge violet">Revisar</span>`
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
  { id: 'placeholder', label: 'En camino', test: (i) => i.placeholder },
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
  const [editUrl, setEditUrl] = useState(null); // null = no se está editando
  if (loading && !it) return html`<div class="loading"><div class="spinner"></div></div>`;
  if (error) return html`<div class="empty">${error.message}</div>`;

  const local = it.seasons.reduce((n, s) => n + s.local.length, 0);
  const missing = it.seasons.reduce((n, s) => n + s.missing.length, 0);
  const best = Math.max(it.file_quality?.resolution_height || 0, ...it.seasons.flatMap((s) => s.local.map((e) => e.resolution_height || 0)));
  const cover = it.has_cover ? `/api/series/${it.id}/cover` : null;
  const bestOffer = it.offers.filter((o) => o.available).sort((a, b) => (b.resolution || 0) - (a.resolution || 0))[0];
  const toggleMonitored = async () => {
    setBusy(true);
    try {
      await api.put(`/api/series/${it.id}/monitored`, { monitored: !it.monitored });
      toast(it.monitored ? 'Ya no se vigila' : 'Vigilando');
      reload();
    } catch (e) { toast('Error: ' + e.message); }
    setBusy(false);
  };

  const saveUrl = async () => {
    setBusy(true);
    try {
      await api.put(`/api/series/${it.id}/hispashare_url`, { url: editUrl.trim() || null });
      toast(editUrl.trim() ? 'Enlace de Hispashare guardado' : 'Enlace de Hispashare quitado');
      setEditUrl(null);
      reload();
    } catch (e) { toast('Error: ' + e.message); }
    setBusy(false);
  };
  const tmdb = it.tmdb || {};
  const heroBg = tmdb.backdrop_url || cover;

  return html`
    <div class="hero">
      ${heroBg && html`<div class="hero-bg" style=${`background-image:url(${heroBg})${tmdb.backdrop_url ? ';filter:blur(2px) saturate(1.1);opacity:.22' : ''}`}></div>`}
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
          ${tmdb.vote_average ? html`<span>TMDB <b>${tmdb.vote_average.toFixed(1)}</b> <span class="faint">(${tmdb.vote_count} votos)</span></span>` : null}
          ${tmdb.imdb_id && html`<a href=${`https://www.imdb.com/title/${tmdb.imdb_id}/`} target="_blank" rel="noopener"><b>IMDb</b> ${tmdb.imdb_id}</a>`}
        </div>
        ${tmdb.overview && html`<p class="overview">${tmdb.overview}</p>`}
        <div class="path">${it.folder_path}</div>
        ${it.content_mode === 'episodic' && html`
          <div class="stats">
            <div class="stat"><div class="k">En disco</div><div class="v">${local}</div></div>
            <div class="stat"><div class="k">Faltan</div><div class="v" style=${missing ? 'color:var(--warn)' : ''}>${missing}</div></div>
            <div class="stat"><div class="k">Mejor calidad</div><div class="v">${qualityLabel(best) || '—'}</div></div>
            <div class="stat"><div class="k">Fuentes</div><div class="v">${it.offers.length + (it.hispashare_url && !it.offers.some((o) => o.provider === 'hispashare') ? 1 : 0)}</div></div>
          </div>`}
        ${it.content_mode !== 'episodic' && html`
          <div class="stats">
            <div class="stat"><div class="k">Calidad del archivo</div>
              <div class="v">${it.file_quality ? qualityLabel(it.file_quality.resolution_height) : '—'}</div>
              ${it.file_quality && html`<div class="faint" style="font-size:12px">${[it.file_quality.codec, hdrLabel(it.file_quality.hdr)].filter(Boolean).join(' · ') || 'códec desconocido'}</div>`}
            </div>
            <div class="stat"><div class="k">Mejor fuente</div>
              <div class="v">${bestOffer ? qualityLabel(bestOffer.resolution) || '?' : '—'}</div>
              ${bestOffer && html`<div class="faint" style="font-size:12px">${bestOffer.provider}</div>`}
            </div>
            <div class="stat"><div class="k">Fuentes</div><div class="v">${it.offers.length}</div></div>
          </div>`}
        <div class="actions" style=${it.content_mode === 'episodic' ? '' : 'margin-top:18px'}>
          <button class="btn" disabled=${busy} onClick=${toggleMonitored}>
            <${Icon} d=${it.monitored ? ICONS.eyeoff : ICONS.eye} /> ${it.monitored ? 'Dejar de vigilar' : 'Vigilar'}
          </button>
          ${it.hispashare_url && html`<a class="btn" href=${it.hispashare_url} target="_blank" rel="noopener">Abrir en Hispashare</a>`}
          ${editUrl === null && html`<button class="btn" onClick=${() => setEditUrl(it.hispashare_url || '')}>${it.hispashare_url ? 'Cambiar enlace de Hispashare' : 'Añadir enlace de Hispashare'}</button>`}
        </div>
        ${editUrl !== null && html`
          <div class="inline-edit">
            <input class="input mono" autofocus value=${editUrl} onInput=${(e) => setEditUrl(e.target.value)}
              placeholder="https://www.hispashare.org/?view=title&id=…" onKeyDown=${(e) => e.key === 'Enter' && saveUrl()} />
            <button class="btn primary" disabled=${busy} onClick=${saveUrl}>Guardar</button>
            <button class="btn" disabled=${busy} onClick=${() => setEditUrl(null)}>Cancelar</button>
          </div>
          <div class="faint" style="font-size:12px;margin-top:6px">Déjalo vacío y guarda para quitar el enlace.</div>`}
      </div>
    </div>

    ${it.seasons.length > 0 && html`
      <div class="section">
        <h3>Temporadas
          <span class="legend">
            <span><span class="ep">1</span>En disco</span>
            <span><span class="ep q4k">1</span>En 4K</span>
            <span><span class="ep missing">1</span>Falta</span>
            <span><span class="ep unmeasured">1</span>Calidad sin analizar</span>
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
                    <span class=${'ep' + (!e.ok ? ' missing' : e.resolution_height >= 2000 ? ' q4k' : '') + (e.ok && !e.measured ? ' unmeasured' : '')}
                      title=${e.ok ? `${e.filename} · ${e.measured ? [qualityLabel(e.resolution_height), e.codec, hdrLabel(e.hdr)].filter(Boolean).join(' · ') : 'calidad sin analizar'}` : `Falta${e.name ? ': ' + e.name : ''}${e.air_date ? ' · ' + e.air_date : ''}`}>
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

    ${it.grabs.length > 0 && html`
      <div class="section">
        <h3>Enviado a descargar</h3>
        <div class="panel">
          <table class="list">
            <thead><tr><th>Fecha</th><th>Episodio</th><th>Proveedor</th><th>Archivo</th></tr></thead>
            <tbody>
              ${it.grabs.map((g) => html`
                <tr>
                  <td class="faint mono" style="width:150px">${fmtDate(g.created_at)}</td>
                  <td class="mono">${g.season != null ? `${String(g.season).padStart(2, '0')}x${String(g.episode).padStart(2, '0')}` : '—'}</td>
                  <td><b>${g.provider}</b></td>
                  <td class="dim" title=${g.destination}>${g.release_title}</td>
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

// ---------------------------------------------------------------- calendar
const CAL_STATUS = {
  have: { label: 'En disco', cls: 'ok' },
  grabbed: { label: 'Descargando', cls: 'accent' },
  missing: { label: 'Falta', cls: 'bad' },
  upcoming: { label: 'Próximo', cls: '' },
};
const dayLabel = (iso, today) => {
  if (iso === today) return 'Hoy';
  const d = new Date(iso + 'T00:00:00');
  const diff = Math.round((d - new Date(today + 'T00:00:00')) / 86400000);
  const name = d.toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long' });
  return diff === 1 ? `Mañana · ${name}` : diff === -1 ? `Ayer · ${name}` : name.charAt(0).toUpperCase() + name.slice(1);
};

function Calendar({ live, toast }) {
  const [offset, setOffset] = useState(0); // semanas
  const range = useMemo(() => {
    const base = new Date();
    const start = new Date(base); start.setDate(base.getDate() - 7 + offset * 28);
    const end = new Date(start); end.setDate(start.getDate() + 37);
    const iso = (d) => d.toISOString().slice(0, 10);
    return { start: iso(start), end: iso(end) };
  }, [offset]);
  const { data, loading } = useFetch(`/api/calendar?start=${range.start}&end=${range.end}`, [live.tick]);
  const running = live.jobs.find((j) => j.name === 'agenda' && j.running);
  const days = useMemo(() => {
    const map = new Map();
    for (const e of data?.entries || []) { if (!map.has(e.date)) map.set(e.date, []); map.get(e.date).push(e); }
    return [...map.entries()];
  }, [data]);
  const refresh = async () => { try { await api.post('/api/jobs/agenda/start'); toast('Actualizando calendario…'); } catch (e) { toast(e.message); } };

  return html`
    <div class="toolbar">
      <button class="btn small" onClick=${() => setOffset(offset - 1)}>← Anterior</button>
      <button class="btn small" disabled=${offset === 0} onClick=${() => setOffset(0)}>Hoy</button>
      <button class="btn small" onClick=${() => setOffset(offset + 1)}>Siguiente →</button>
      <span class="muted">${range.start} — ${range.end}</span>
      <span style="flex:1"></span>
      <button class="btn" disabled=${!!running} onClick=${refresh}><${Icon} d=${ICONS.refresh} size=${15} /> ${running ? running.message : 'Actualizar calendario'}</button>
    </div>
    <p class="dim" style="margin:-4px 0 18px">Los episodios solo se buscan en las fuentes una vez emitidos. Las películas son los estrenos de Novedades.</p>
    ${loading && !data ? html`<div class="loading"><div class="spinner"></div></div>`
      : !days.length ? html`<div class="empty">Nada en estas fechas. Si acabas de empezar, pulsa "Actualizar calendario".</div>`
      : html`<div class="agenda">
        ${days.map(([date, entries]) => html`
          <div class=${'agenda-day' + (date === data.today ? ' today' : date < data.today ? ' past' : '')}>
            <div class="agenda-date">${dayLabel(date, data.today)}</div>
            <div class="panel">
              ${entries.map((e) => html`
                <div class=${'agenda-row' + (e.series_id ? ' click' : '')} onClick=${() => e.series_id && go('series/' + e.series_id)}>
                  <img class="agenda-thumb" loading="lazy" src=${e.poster_url || (e.series_id ? `/api/series/${e.series_id}/cover` : '')} onError=${(ev) => { ev.target.style.visibility = 'hidden'; }} alt="" />
                  <div class="agenda-text">
                    <div class="agenda-title">${e.title}</div>
                    <div class="faint">${e.kind === 'movie' ? '🎬 ' : ''}${e.subtitle} · ${e.category_label}</div>
                  </div>
                  <span class=${'pill ' + CAL_STATUS[e.status].cls}>${CAL_STATUS[e.status].label}</span>
                </div>`)}
            </div>
          </div>`)}
      </div>`}`;
}

// ---------------------------------------------------------------- discovery
const DISCOVERY_TABS = [
  { id: 'approved', label: 'Buscando', hint: 'Cumplen tus notas: se descargan en cuanto aparezcan en alguna fuente, con la mejor calidad.' },
  { id: 'waiting', label: 'En espera', hint: 'Aún sin estrenar o con pocos votos para fiarse de la nota. Se revisan en cada ciclo.' },
  { id: 'rejected', label: 'Descartadas', hint: 'No llegan a las notas mínimas (o las descartaste tú). Puedes añadirlas igualmente.' },
  { id: 'in_library', label: 'Ya en la biblioteca', hint: 'Novedades que ya tienes en disco.' },
];

function Rating({ label, value, votes }) {
  if (value == null) return html`<span class="faint">${label} —</span>`;
  const color = value >= 7 ? 'var(--ok)' : value >= 6 ? 'var(--accent)' : 'var(--bad)';
  return html`<span><b style=${`color:${color}`}>${label} ${Number(value).toFixed(1)}</b> <span class="faint">(${votes ?? 0})</span></span>`;
}

function Discovery({ live, toast }) {
  const [tab, setTab] = useState('approved');
  const { data, loading, reload } = useFetch(`/api/discovery?status=${tab}`, [live.tick]);
  const [busyId, setBusyId] = useState(null);
  const running = live.jobs.find((j) => j.name === 'discovery' && j.running);
  const counts = data?.counts || {};
  const tabCount = (id) => ({ approved: counts.approved, waiting: counts.waiting, in_library: counts.in_library,
    rejected: (counts.rejected || 0) + (counts.expired || 0) + (counts.dismissed || 0) })[id] || 0;

  const act = async (id, verb) => {
    setBusyId(id);
    try { await api.post(`/api/discovery/${id}/${verb}`); toast(verb === 'approve' ? 'Añadida: se buscará en todas las fuentes' : 'Descartada'); reload(); }
    catch (e) { toast('Error: ' + e.message); }
    setBusyId(null);
  };
  const runNow = async () => {
    try { await api.post('/api/jobs/discovery/start'); toast('Buscando novedades…'); } catch (e) { toast(e.message); }
  };

  return html`
    <div class="toolbar">
      ${DISCOVERY_TABS.map((t) => html`
        <button class=${'chip' + (tab === t.id ? ' on' : '')} onClick=${() => setTab(t.id)}>${t.label} <span class="n">${tabCount(t.id)}</span></button>`)}
      <span style="flex:1"></span>
      <button class="btn primary" disabled=${!!running} onClick=${runNow}><${Icon} d=${ICONS.refresh} size=${15} /> ${running ? running.message : 'Buscar novedades ahora'}</button>
    </div>
    <p class="dim" style="margin:-4px 0 18px">${DISCOVERY_TABS.find((t) => t.id === tab).hint}</p>
    ${loading && !data ? html`<div class="loading"><div class="spinner"></div></div>`
      : !data.items.length ? html`<div class="empty">Nada por aquí todavía.${tab === 'approved' ? ' Pulsa "Buscar novedades ahora" o espera al siguiente ciclo.' : ''}</div>`
      : html`
        <div class="grid">
          ${data.items.map((w) => html`
            <div class="card" key=${w.id} onClick=${() => w.series_id && go('series/' + w.series_id)}>
              <div class="poster" style=${w.poster_url ? '' : `background: linear-gradient(160deg, ${phColor(w.tmdb_id)}, #161922)`}>
                ${w.poster_url ? html`<img loading="lazy" src=${w.poster_url} alt="" />` : html`<div class="ph">${w.title}</div>`}
                <div class="badges"><span class="badge mute">${w.media_type === 'tv' ? 'Serie' : 'Película'}</span><span></span></div>
              </div>
              <div class="title" title=${w.title}>${w.title}</div>
              <div class="sub"><span>${w.release_date || 'sin fecha'}</span><span>·</span><span>${w.category_label}</span></div>
              <div class="sub" style="gap:10px;margin-top:2px"><${Rating} label="TMDB" value=${w.tmdb_rating} votes=${w.tmdb_votes} /><${Rating} label="IMDb" value=${w.imdb_rating} votes=${w.imdb_votes} /></div>
              <div class="faint" style="font-size:11.5px;margin-top:3px;min-height:16px">${w.reason || ''}</div>
              <div style="display:flex;gap:6px;margin-top:6px" onClick=${(e) => e.stopPropagation()}>
                ${tab !== 'approved' && tab !== 'in_library' && html`<button class="btn small" disabled=${busyId === w.id} onClick=${() => act(w.id, 'approve')}>Añadir</button>`}
                ${tab !== 'rejected' && tab !== 'in_library' && html`<button class="btn small danger" disabled=${busyId === w.id} onClick=${() => act(w.id, 'dismiss')}>Descartar</button>`}
              </div>
            </div>`)}
        </div>`}`;
}

function DiscoverySettingsPanel({ toast }) {
  const { data, reload } = useFetch('/api/settings/discovery');
  const [form, setForm] = useState(null);
  useEffect(() => { if (data) setForm({ ...data }); }, [data]);
  if (!form) return null;
  const num = (k) => (e) => setForm({ ...form, [k]: e.target.value === '' ? '' : Number(e.target.value) });
  const toggleCat = (id) => setForm({ ...form, categories: form.categories.includes(id) ? form.categories.filter((c) => c !== id) : [...form.categories, id] });
  const dirty = JSON.stringify({ ...form, all_categories: 0 }) !== JSON.stringify({ ...data, all_categories: 0 });
  const save = async () => {
    try { const { all_categories, ...body } = form; await api.put('/api/settings/discovery', body); toast('Ajustes de novedades guardados'); reload(); }
    catch (e) { toast('Error: ' + e.message); }
  };
  return html`
    <div class="section">
      <h3>Novedades (TMDB + IMDb)</h3>
      <div class="panel form">
        <label class="check"><input type="checkbox" checked=${form.enabled} onChange=${(e) => setForm({ ...form, enabled: e.target.checked })} /> Buscar y descargar novedades automáticamente</label>
        <div class="form-row">
          <label class="field"><span>Nota mínima TMDB</span><input class="input" type="number" step="0.1" min="0" max="10" value=${form.min_tmdb} onInput=${num('min_tmdb')} /></label>
          <label class="field"><span>Votos mínimos TMDB</span><input class="input" type="number" min="0" value=${form.min_tmdb_votes} onInput=${num('min_tmdb_votes')} /></label>
          <label class="field"><span>Nota mínima IMDb</span><input class="input" type="number" step="0.1" min="0" max="10" value=${form.min_imdb} onInput=${num('min_imdb')} /></label>
          <label class="field"><span>Votos mínimos IMDb</span><input class="input" type="number" min="0" value=${form.min_imdb_votes} onInput=${num('min_imdb_votes')} /></label>
        </div>
        <div class="form-row">
          <label class="field"><span>Estrenos de los últimos (días)</span><input class="input" type="number" min="1" value=${form.days_back} onInput=${num('days_back')} /></label>
          <label class="field"><span>Próximos estrenos (días)</span><input class="input" type="number" min="0" value=${form.days_ahead} onInput=${num('days_ahead')} /></label>
          <label class="field"><span>Decidir con una sola nota tras (días)</span><input class="input" type="number" min="0" value=${form.imdb_grace_days} onInput=${num('imdb_grace_days')} /></label>
        </div>
        <div class="field"><span>Tipos que se siguen</span>
          <div class="chips" style="margin:0">
            ${form.all_categories.map((c) => html`<button class=${'chip' + (form.categories.includes(c.id) ? ' on' : '')} onClick=${() => toggleCat(c.id)}>${c.label}</button>`)}
          </div>
        </div>
        <small class="faint">Con pocos votos la nota no es fiable: hasta reunir los votos mínimos la novedad queda "en espera".</small>
      </div>
      <div class="actions" style="margin-top:14px"><button class="btn primary" disabled=${!dirty} onClick=${save}>Guardar</button></div>
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
          <tr><td>Calidad analizada con MediaInfo</td><td>
            ${!data.mediainfo_available ? html`<span class="pill bad">MediaInfo no disponible</span>` : html`
              <span class="mono">${data.quality_coverage.episodes.measured}/${data.quality_coverage.episodes.total}</span> episodios ·
              <span class="mono">${data.quality_coverage.items.measured}/${data.quality_coverage.items.total}</span> películas y documentales
              ${(data.quality_coverage.episodes.unreadable + data.quality_coverage.items.unreadable) > 0 && html`<span class="faint"> · ${data.quality_coverage.episodes.unreadable + data.quality_coverage.items.unreadable} sin poder leer (se reintentan en 7 días)</span>`}
              <button class="btn small" style="margin-left:10px" onClick=${() => api.post('/api/jobs/quality_scan/start').then(() => go('activity'), (e) => alert(e.message))}>Analizar ahora</button>`}
          </td></tr>
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

// ---------------------------------------------------------------- settings
function Settings({ toast }) {
  return html`<${GeneralSettings} toast=${toast} /><${QualitySettings} toast=${toast} /><${DiscoverySettingsPanel} toast=${toast} /><${ImportSettingsPanel} toast=${toast} />`;
}

function GeneralSettings({ toast }) {
  const { data, reload } = useFetch('/api/settings/general');
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (data) setForm({ ...data }); }, [data]);
  if (!form) return html`<div class="loading"><div class="spinner"></div></div>`;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const dirty = Object.keys(form).some((k) => form[k] !== data[k]);
  const save = async () => {
    setSaving(true);
    try {
      const changed = Object.fromEntries(Object.entries(form).filter(([k, v]) => v !== data[k]));
      await api.put('/api/settings/general', changed);
      toast('Ajustes guardados');
      reload();
    } catch (e) { toast('Error: ' + e.message); }
    setSaving(false);
  };
  return html`
    <div class="section" style="margin-top:0">
      <h3>Claves de API</h3>
      <div class="panel form">
        <label class="field"><span>TMDB API key</span>
          <input class="input mono" value=${form.tmdb_api_key} onInput=${set('tmdb_api_key')} placeholder="Pega aquí tu clave de themoviedb.org" />
          <small class="faint">Carátulas, fechas de estreno, notas y episodios. Se comprueba al guardar.</small>
        </label>
        <label class="field"><span>IMDb (clave de OMDb)</span>
          <input class="input mono" value=${form.omdb_api_key} onInput=${set('omdb_api_key')} placeholder="Opcional: clave gratuita de omdbapi.com" />
          <small class="faint">IMDb no tiene API pública. Sin clave, las notas de IMDb se toman de sus datos oficiales que publica cada día.</small>
        </label>
      </div>
    </div>
    <div class="section">
      <h3>Fuentes y descargas</h3>
      <div class="panel form">
        <label class="check"><input type="checkbox" checked=${form.autostart_scheduler} onChange=${set('autostart_scheduler')} /> Iniciar el ciclo automático al arrancar la web</label>
        <label class="check"><input type="checkbox" checked=${form.wolfmax_enabled} onChange=${set('wolfmax_enabled')} /> Wolfmax4k</label>
        <label class="check"><input type="checkbox" checked=${form.dontorrent_enabled} onChange=${set('dontorrent_enabled')} /> DonTorrent</label>
        <label class="field"><span>Dirección de DonTorrent</span>
          <input class="input mono" value=${form.dontorrent_url} onInput=${set('dontorrent_url')} />
          <small class="faint">Cambia de dominio a menudo: actualízala aquí cuando ocurra.</small>
        </label>
        <label class="field"><span>Carpeta vigilada por el cliente torrent</span>
          <input class="input mono" value=${form.torrent_watch_dir} onInput=${set('torrent_watch_dir')} />
        </label>
      </div>
      <div class="actions" style="margin-top:14px">
        <button class="btn primary" disabled=${!dirty || saving} onClick=${save}>${saving ? 'Comprobando…' : 'Guardar'}</button>
      </div>
    </div>`;
}

function ImportSettingsPanel({ toast }) {
  const { data, reload } = useFetch('/api/settings/import');
  const [form, setForm] = useState(null);
  useEffect(() => { if (data) setForm({ ...data, source_text: (data.source_dirs || []).join('\n') }); }, [data]);
  if (!form) return null;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const body = () => ({
    enabled: form.enabled, mode: form.mode, recycle_dir: form.recycle_dir.trim(),
    min_age_minutes: Number(form.min_age_minutes),
    source_dirs: form.source_text.split('\n').map((d) => d.trim()).filter(Boolean),
  });
  const dirty = data && JSON.stringify(body()) !== JSON.stringify({
    enabled: data.enabled, mode: data.mode, recycle_dir: data.recycle_dir, min_age_minutes: data.min_age_minutes,
    source_dirs: data.source_dirs });
  const save = async () => {
    try { await api.put('/api/settings/import', body()); toast('Ajustes de importación guardados'); reload(); }
    catch (e) { toast('Error: ' + e.message); }
  };
  return html`
    <div class="section">
      <h3>Importación a la biblioteca</h3>
      <p class="dim" style="margin:-4px 0 14px; max-width:760px">
        Cuando una descarga termina, la coloca en su serie o película con el nombre de tu biblioteca
        (<span class="mono">Serie/Season 01/Serie S01E05 - Título</span>, <span class="mono">Película (Año)</span>).
        Solo toca lo que la app pidió o necesita. Déjalo desactivado si las descargas pasan antes por otro programa.
      </p>
      <div class="panel form">
        <label class="check"><input type="checkbox" checked=${form.enabled} onChange=${set('enabled')} /> Importar automáticamente lo descargado</label>
        <label class="field"><span>Carpetas donde terminan las descargas (una por línea)</span>
          <textarea class="input mono" rows="3" value=${form.source_text} onInput=${set('source_text')} placeholder="F:/temp transmission"></textarea>
          <small class="faint">La de Transmission y, si quieres, la carpeta "Incoming" de aMule. La carpeta vigilada de torrents se ignora.</small>
        </label>
        <div class="form-row">
          <label class="field"><span>Al importar</span>
            <select class="select" value=${form.mode} onChange=${set('mode')}>
              <option value="copy">Copiar (el original sigue compartiéndose)</option>
              <option value="move">Mover (libera espacio, deja de compartirse)</option>
            </select>
          </label>
          <label class="field"><span>Descarga terminada si no cambia en (minutos)</span>
            <input class="input" type="number" min="1" value=${form.min_age_minutes} onInput=${set('min_age_minutes')} />
          </label>
        </div>
        <label class="field"><span>Carpeta para los archivos sustituidos por una mejora</span>
          <input class="input mono" value=${form.recycle_dir} onInput=${set('recycle_dir')} placeholder="F:/temp transmission/_reemplazados" />
          <small class="faint">El archivo de peor calidad nunca se borra: se mueve aquí. Sin carpeta, las mejoras no se importan.</small>
        </label>
      </div>
      <div class="actions" style="margin-top:14px">
        <button class="btn primary" disabled=${!dirty} onClick=${save}>Guardar</button>
        <button class="btn" onClick=${() => api.post('/api/jobs/importer/start').then(() => go('activity'), (e) => toast(e.message))}>Importar ahora</button>
      </div>
    </div>`;
}

function QualitySettings({ toast }) {
  const { data, reload } = useFetch('/api/settings/quality');
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (data) setDraft({ tiers: data.tiers, minimum: data.minimum }); }, [data]);
  if (!draft) return html`<div class="loading"><div class="spinner"></div></div>`;

  const move = (i, delta) => {
    const tiers = [...draft.tiers];
    const j = i + delta;
    if (j < 0 || j >= tiers.length) return;
    [tiers[i], tiers[j]] = [tiers[j], tiers[i]];
    setDraft({ ...draft, tiers });
  };
  const minIndex = draft.tiers.findIndex((t) => t.id === draft.minimum);
  const dirty = data && (draft.minimum !== data.minimum || draft.tiers.some((t, i) => t.id !== data.tiers[i].id));
  const save = async () => {
    setSaving(true);
    try {
      await api.put('/api/settings/quality', { order: draft.tiers.map((t) => t.id), minimum: draft.minimum });
      toast('Perfil de calidad guardado');
      reload();
    } catch (e) { toast('Error: ' + e.message); }
    setSaving(false);
  };

  return html`
    <div class="section">
      <h3>Perfil de calidad</h3>
      <p class="dim" style="margin:-4px 0 14px; max-width:720px">
        Da igual la fuente: siempre se descarga la mejor calidad según este orden (arriba = mejor),
        y si aparece una versión de un nivel superior a la que tienes, se mejora.
        Lo que quede por debajo de la calidad mínima no se descarga.
      </p>
      <div class="panel">
        <table class="list">
          <thead><tr><th style="width:60px">#</th><th>Calidad</th><th>Mínima</th><th style="text-align:right">Orden</th></tr></thead>
          <tbody>
            ${draft.tiers.map((t, i) => html`
              <tr style=${i > minIndex ? 'opacity:.45' : ''}>
                <td class="faint mono">${i + 1}</td>
                <td><b>${t.label}</b> ${i > minIndex ? html`<span class="pill" style="margin-left:8px">No se descarga</span>` : null}</td>
                <td><input type="radio" name="minimum" checked=${draft.minimum === t.id} onChange=${() => setDraft({ ...draft, minimum: t.id })} /></td>
                <td style="text-align:right; white-space:nowrap">
                  <button class="btn small" disabled=${i === 0} onClick=${() => move(i, -1)} title="Subir">▲</button>
                  <button class="btn small" disabled=${i === draft.tiers.length - 1} onClick=${() => move(i, 1)} title="Bajar" style="margin-left:6px">▼</button>
                </td>
              </tr>`)}
          </tbody>
        </table>
      </div>
      <div class="actions" style="margin-top:14px">
        <button class="btn primary" disabled=${!dirty || saving} onClick=${save}>Guardar</button>
        <button class="btn" disabled=${!dirty || saving} onClick=${() => setDraft({ tiers: data.tiers, minimum: data.minimum })}>Descartar cambios</button>
      </div>
    </div>`;
}

// ---------------------------------------------------------------- shell
const NAV = [
  { id: 'library', label: 'Biblioteca', icon: ICONS.library },
  { id: 'discovery', label: 'Novedades', icon: ICONS.sparkle },
  { id: 'calendar', label: 'Calendario', icon: ICONS.calendar },
  { id: 'wanted', label: 'Buscados', icon: ICONS.wanted },
  { id: 'activity', label: 'Actividad', icon: ICONS.activity },
  { id: 'settings', label: 'Ajustes', icon: ICONS.sliders },
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
  const titles = { library: 'Biblioteca', calendar: 'Calendario', discovery: 'Novedades', wanted: 'Buscados', activity: 'Actividad', settings: 'Ajustes', system: 'Sistema', series: '' };
  const searchable = page === 'library' || page === 'wanted';

  let body;
  if (page === 'series') body = html`<${SeriesDetail} id=${arg} tick=${live.tick} toast=${toast} />`;
  else if (page === 'wanted') body = html`<${Wanted} query=${query} tick=${live.tick} />`;
  else if (page === 'activity') body = html`<${Activity} live=${live} toast=${toast} />`;
  else if (page === 'system') body = html`<${System} />`;
  else if (page === 'calendar') body = html`<${Calendar} live=${live} toast=${toast} />`;
  else if (page === 'discovery') body = html`<${Discovery} live=${live} toast=${toast} />`;
  else if (page === 'settings') body = html`<${Settings} toast=${toast} />`;
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
