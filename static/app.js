/* Musica frontend. No framework, no build: one state object, one render per panel,
   and a single EventSource that replaces the queue snapshot whenever the backend moves. */

'use strict';

const state = {
  settings: null,
  drives: [],
  driveInfo: null,
  health: null,
  preview: null,          // {items, playlist_title, is_playlist, destination, ...}
  selected: new Set(),    // item ids chosen in the preview
  hideOwned: false,
  queue: null,            // snapshot from /api/events
  logs: [],
  destination: '',
  browserPath: null,
};

const els = {};
let events = null;

/* ── tiny helpers ─────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function duration(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) return '—';
  const total = Math.round(Number(seconds));
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hours
    ? `${hours}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
    : `${mins}:${String(secs).padStart(2, '0')}`;
}

function bytes(value) {
  if (!value) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const size = value / 1024 ** exponent;
  return `${size >= 100 ? Math.round(size) : size.toFixed(1)} ${units[exponent]}`;
}

function clock(at) {
  return new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function icon(name, extra = '') {
  return `<svg class="${extra}"><use href="#${name}"/></svg>`;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = { detail: text }; }
  if (!response.ok) {
    throw new Error((payload && payload.detail) || `Request failed (${response.status})`);
  }
  return payload;
}

function toast(message, kind = '') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  const glyph = kind === 'bad' ? 'i-warn' : kind === 'good' ? 'i-check' : 'i-bolt';
  node.innerHTML = `${icon(glyph)}<p>${esc(message)}</p>`;
  els.toasts.append(node);
  const life = kind === 'bad' ? 7000 : 3200;
  setTimeout(() => node.remove(), life);
  node.addEventListener('click', () => node.remove());
}

/* ── mode ─────────────────────────────────────────────────────────────── */

// /spotify is this same page. Only the copy and accent change: the backend recognises a
// Spotify link in /api/resolve, so every control below works the same in both modes.
const SPOTIFY_MODE = location.pathname.startsWith('/spotify');

function applySpotifyMode() {
  document.body.classList.add('mode-spotify');
  document.title = 'Musica · Spotify';
  els.modeYt.classList.remove('active');
  els.modeYt.removeAttribute('aria-current');
  els.modeSp.classList.add('active');
  els.modeSp.setAttribute('aria-current', 'page');
  els.heroTitle.innerHTML = 'Your Spotify playlists, <em>as files.</em>';
  els.heroSub.textContent = 'Paste a playlist, album or song link. Songs already on the drive are skipped, so re-syncing a playlist only fetches the new ones.';
  els.query.placeholder = 'https://open.spotify.com/playlist/…';
  els.query.setAttribute('aria-label', 'Spotify link');
  els.suggest.innerHTML = `
    <button type="button" data-fill="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M">Today's Top Hits</button>
    <button type="button" data-fill="https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy">an album</button>
    <button type="button" data-fill="https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp">a single song</button>`;
}

/* ── boot ─────────────────────────────────────────────────────────────── */

async function boot() {
  [
    'queryForm', 'query', 'findBtn', 'suggest', 'driveSelect', 'browseBtn', 'refreshDrives',
    'destPath', 'formatSelect', 'qualitySelect', 'previewPanel', 'previewTitle', 'previewMeta',
    'previewList', 'selectAllBtn', 'selectNoneBtn', 'hideOwned', 'skipNote', 'downloadBtn',
    'queuePanel', 'queueMeta', 'queueList', 'overallBar', 'overall', 'queueHeadline', 'queueOrb',
    'queuePct', 'queueChips', 'pauseBtn', 'cancelBtn', 'clearBtn',
    'logbox', 'logList', 'emptyState', 'versions', 'conn', 'connLabel',
    'modeYt', 'modeSp', 'heroTitle', 'heroSub',
    'settingsBtn', 'settingsClose', 'settingsDrawer', 'scrim', 'browserModal', 'browserClose',
    'browserCancel', 'browserPick', 'browserUp', 'browserPath', 'browserList', 'toasts',
    'setNaming', 'setSubfolders', 'setSearchResults', 'setConcurrency', 'setArchiveOnDrive',
    'setSkipName', 'setStage', 'setCookiefile', 'healthList',
    'setCarMode', 'setNumberFiles', 'setCarGenre',
    'formatBtn', 'formatModal', 'formatClose', 'formatCancel', 'formatGo',
    'formatType', 'formatLabel', 'formatConfirm', 'formatCommand', 'formatWarning',
  ].forEach((id) => { els[id] = $(id); });

  if (SPOTIFY_MODE) applySpotifyMode();
  bindUi();

  try {
    const [health, config] = await Promise.all([
      request('/api/health'),
      request('/api/settings'),
    ]);
    state.health = health;
    state.settings = config.settings;
    applySettingsToForm();
    renderHealth();
  } catch (error) {
    toast(`Could not reach the Musica backend: ${error.message}`, 'bad');
  }

  await refreshDrives();
  listenEvents();
}

/* ── live connection ──────────────────────────────────────────────────── */

function listenEvents() {
  events = new EventSource('/api/events');

  events.addEventListener('open', () => {
    els.conn.classList.add('live');
    els.conn.classList.remove('dead');
    els.connLabel.textContent = 'live';
  });

  events.addEventListener('snapshot', (message) => {
    const frame = JSON.parse(message.data);
    if (frame.event === 'log') {
      state.logs.push(frame.payload);
      state.logs = state.logs.slice(-200);
      renderLogs();
      return;
    }
    state.queue = frame.payload;
    renderQueue();
  });

  events.addEventListener('error', () => {
    els.conn.classList.remove('live');
    els.conn.classList.add('dead');
    els.connLabel.textContent = 'reconnecting';
  });
}

/* ── drives and destination ───────────────────────────────────────────── */

async function refreshDrives() {
  try {
    const info = await request('/api/drives');
    state.driveInfo = info;
    state.drives = info.drives;
    if (!state.destination) {
      state.destination = state.settings?.destination || info.fallback;
    }
    // The remembered drive was unplugged or renamed (e.g. reformatted as MUSICA):
    // move to a drive that is actually here rather than fail at download time.
    const onDrive = (path) => state.drives.some((drive) => path === drive.path || path.startsWith(`${drive.path}/`));
    const gone = /^(\/Volumes|\/media|\/run\/media|\/mnt)\//.test(state.destination) && !onDrive(state.destination);
    if (gone) {
      const missing = state.destination;
      const replacement = state.drives.find((drive) => drive.kind === 'removable')
        || state.drives.find((drive) => drive.kind === 'external');
      state.destination = replacement ? replacement.path : info.fallback;
      toast(`${missing} is not connected, so saving to ${state.destination} instead.`);
      saveSettings({ destination: state.destination });
    }
    renderDestination();
  } catch (error) {
    toast(error.message, 'bad');
  }
}

function isKnownDestination(path) {
  return state.drives.some((drive) => drive.path === path);
}

function renderDestination() {
  const select = els.driveSelect;
  const previous = select.value;
  select.innerHTML = '';

  state.drives.forEach((drive) => {
    const option = new Option(
      `${drive.label}  ·  ${drive.free_human} free${drive.kind === 'removable' ? '' : `  (${drive.kind})`}`,
      drive.path,
    );
    option.dataset.free = drive.free;
    option.dataset.total = drive.total;
    select.append(option);
  });

  // A remembered folder that is not a detected drive still has to be selectable.
  if (state.destination && !isKnownDestination(state.destination)) {
    select.append(new Option(`Custom folder  ·  ${state.destination}`, state.destination));
  }
  if (!state.drives.length) {
    select.append(new Option(`Music folder  ·  ${state.driveInfo?.fallback || ''}`, state.driveInfo?.fallback || ''));
  }

  const wanted = previous && [...select.options].some((o) => o.value === previous) ? previous : state.destination;
  if ([...select.options].some((o) => o.value === wanted)) select.value = wanted;

  const chosen = select.value || state.destination;
  const drive = state.drives.find((item) => item.path === chosen);
  // Erasing a disk is only ever offered for a real removable stick, never a folder.
  els.formatBtn.hidden = !(drive && ['removable', 'external'].includes(drive.kind));
  els.formatBtn.dataset.device = drive ? drive.device || '' : '';
  const parts = [];
  if (drive) {
    parts.push(`<span class="free">${esc(drive.path)}</span>`);
    parts.push(`${bytes(drive.free)} free of ${bytes(drive.total)}`);
    if (drive.fstype) parts.push(drive.fstype.toUpperCase());
    // Most head units will only mount a FAT-based stick; NTFS/ext4 silently shows up empty.
    if (state.settings?.car_mode && drive.fstype && !['fat32', 'vfat', 'msdos', 'exfat'].includes(drive.fstype)) {
      parts.push('<span class="missing">car players usually cannot read this format — reformat the stick to FAT32 or exFAT</span>');
    }
  } else {
    parts.push(esc(chosen || 'no folder chosen'));
  }
  if (!state.drives.length) {
    parts.push('<span class="missing">no removable drive detected — plug it in and refresh</span>');
  }
  els.destPath.innerHTML = parts.join('  ·  ');
  state.destination = chosen;
}

/* ── resolve ──────────────────────────────────────────────────────────── */

async function find(rawQuery) {
  const query = rawQuery.trim();
  if (!query) return;
  els.queryForm.classList.add('busy');
  els.findBtn.disabled = true;
  els.findBtn.querySelector('span').textContent = /spotify\.com/.test(query) ? 'Reading playlist…' : 'Reading…';

  try {
    const result = await request('/api/resolve', { method: 'POST', body: JSON.stringify({ query }) });
    state.preview = result;
    // Default to everything we do not already own.
    state.selected = new Set(
      result.items.filter((item) => !item.in_archive && !item.name_duplicate).map((item) => item.url || item.id),
    );
    if (!result.items.length) toast('That returned nothing playable.', 'bad');
    renderPreview();
    els.emptyState.hidden = true;
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    els.queryForm.classList.remove('busy');
    els.findBtn.disabled = false;
    els.findBtn.querySelector('span').textContent = 'Find';
  }
}

function ownedReason(item) {
  if (item.in_archive) return 'In this drive\'s archive (same video)';
  if (item.name_duplicate) return `Same song already on the drive — ${item.duplicate_display}`;
  return '';
}

function ownedCount() {
  return state.preview ? state.preview.items.filter((item) => item.in_archive || item.name_duplicate).length : 0;
}

function visibleItems() {
  if (!state.preview) return [];
  return state.hideOwned
    ? state.preview.items.filter((item) => !item.in_archive && !item.name_duplicate)
    : state.preview.items;
}

function itemKey(item) {
  return item.url || item.id;
}

function renderPreview() {
  const preview = state.preview;
  if (!preview) return;
  els.previewPanel.hidden = false;

  const items = visibleItems();
  const heading = preview.is_playlist
    ? preview.playlist_title || 'Playlist'
    : preview.kind === 'search'
      ? `Search: ${preview.query}`
      : (items[0]?.title || 'Link');
  els.previewTitle.textContent = heading;

  const owned = ownedCount();
  els.previewMeta.textContent =
    `${preview.items.length} track${preview.items.length === 1 ? '' : 's'}` +
    (preview.is_playlist ? ' in this playlist' : '') +
    (owned ? ` · ${owned} already on the drive` : '') +
    (preview.scanned_files ? ` · ${preview.scanned_files} files scanned on the target` : '');

  els.previewList.innerHTML = items.map((item) => {
    const key = itemKey(item);
    const ownedText = ownedReason(item);
    const checked = state.selected.has(key) ? 'checked' : '';
    return `
      <li class="track selectable ${ownedText ? 'is-owned' : ''}" data-key="${esc(key)}">
        <input type="checkbox" class="pick" ${checked} aria-label="Select ${esc(item.title)}">
        <div class="cover ${item.thumbnail ? '' : 'none'}"
             style="${item.thumbnail ? `background-image:url('${esc(item.thumbnail)}')` : ''}">
          <span class="dur">${duration(item.duration)}</span>
        </div>
        <div class="body">
          <div class="title">${esc(item.title)}</div>
          <div class="subtitle">${esc(item.artist || item.uploader || 'unknown artist')}
            <span class="sep">·</span>${esc(item.album || item.playlist || 'single track')}</div>
          ${ownedText ? `<div class="note">${icon('i-warn')}<span>${esc(ownedText)}</span></div>` : ''}
        </div>
        <div class="right">
          ${ownedText ? '<span class="badge owned">On drive</span>' : ''}
        </div>
      </li>`;
  }).join('') || '<li class="track"><div class="body"><div class="subtitle">Nothing to show.</div></div></li>';

  const selected = items.filter((item) => state.selected.has(itemKey(item)));
  const fresh = selected.filter((item) => !ownedReason(item));
  // Picking only owned tracks means "put it there again" - a deliberate force re-download.
  state.forceBatch = selected.length > 0 && fresh.length === 0;

  els.downloadBtn.querySelector('span').textContent = fresh.length
    ? `Download ${fresh.length}`
    : state.forceBatch ? 'Re-download anyway' : 'Download';
  els.downloadBtn.disabled = selected.length === 0;
  els.skipNote.innerHTML = selected.length
    ? state.forceBatch
      ? `<strong>${selected.length}</strong> selected, all already on the drive · they will be fetched again`
      : `<strong>${selected.length}</strong> selected` +
        (selected.length - fresh.length
          ? ` · ${selected.length - fresh.length} already there will be skipped`
          : ' · anything already on the drive is skipped automatically')
    : 'Select at least one track.';
}

/* ── download ─────────────────────────────────────────────────────────── */

async function download() {
  if (!state.settings) { toast('Still loading settings - give it a moment.', 'bad'); return; }
  const items = state.preview?.items.filter((item) => state.selected.has(itemKey(item))) || [];
  if (!items.length) { toast('Nothing selected.', 'bad'); return; }
  if (!state.destination) { toast('Choose a destination first.', 'bad'); return; }

  els.downloadBtn.disabled = true;
  try {
    const result = await request('/api/download', {
      method: 'POST',
      body: JSON.stringify({
        destination: state.destination,
        items,
        label: state.preview.playlist_title || state.preview.query || '',
        force: Boolean(state.forceBatch),
        settings: {
          format: state.settings.format,
          quality: state.settings.quality,
          naming: state.settings.naming,
          playlist_subfolders: state.settings.playlist_subfolders,
          skip_name_duplicates: state.settings.skip_name_duplicates,
          archive_on_drive: state.settings.archive_on_drive,
          stage_locally: state.settings.stage_locally,
          concurrency: Number(state.settings.concurrency),
        },
      }),
    });
    toast(
      result.queued
        ? `${result.queued} track${result.queued === 1 ? '' : 's'} queued`
          + (result.skipped ? ` · ${result.skipped} already on the drive` : '')
        : 'Everything you picked is already on the drive.',
      result.queued ? 'good' : '',
    );
    els.queuePanel.hidden = false;
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    els.downloadBtn.disabled = false;
  }
}

/* ── queue rendering ──────────────────────────────────────────────────── */

const STAGE_LABEL = {
  Connecting: 'Connecting',
  Downloading: 'Downloading',
  'Converting to audio': 'Converting',
  Saved: 'Saved',
  Failed: 'Failed',
};

function trackRow(track) {
  const status = track.status;
  const running = status === 'running';
  const classes = ['track', `is-${status}`].join(' ');
  const owned = status === 'skipped';

  let right = '';
  if (running) {
    const convert = track.stage === 'Converting to audio';
    const connecting = track.stage === 'Connecting';
    const pct = Math.round(track.percent || 0);
    const detail = convert
      ? 'making the audio file'
      : [track.speed ? `${bytes(track.speed)}/s` : '', track.eta ? `${duration(track.eta)} left` : ''].filter(Boolean).join(' · ');
    right = `
      <div class="right-stack">
        <span class="badge running">${convert ? 'Converting' : connecting ? 'Connecting' : `Downloading ${pct}%`}</span>
        <div class="bar ${convert || connecting ? 'convert' : ''}"><i style="width:${convert ? 100 : pct}%"></i></div>
        ${detail ? `<div class="rate"><span>${esc(detail)}</span></div>` : ''}
      </div>`;
  } else if (status === 'done') {
    right = `<div class="right-stack"><span class="badge done">Saved</span>
      <div class="rate"><span>${bytes(track.size)}</span></div></div>`;
  } else if (owned) {
    right = `<span class="badge owned">${esc(shortSkip(track))}</span>`;
  } else if (status === 'error') {
    right = `<div class="right-stack"><span class="badge error">Failed</span>
      <button class="mini-link" data-retry="${esc(track.key)}">Retry</button></div>`;
  } else if (status === 'cancelled') {
    right = '<span class="badge">Cancelled</span>';
  } else {
    right = `<span class="badge pending">${STAGE_LABEL[track.stage] || 'Waiting'}</span>`;
  }

  const note = status === 'error'
    ? `<div class="note bad">${icon('i-warn')}<span>${esc(track.error || '').slice(0, 220)}</span></div>`
    : status === 'done'
      ? `<div class="note ok"><code>${esc(track.relative || track.path || '')}</code></div>`
      : owned && track.stage
        ? `<div class="note">${icon('i-warn')}<span>${esc(track.stage)}</span></div>`
        : '';

  return `
    <li class="${classes}" data-key="${esc(track.key)}">
      <span class="st ${status}">${icon(statusIcon(status))}</span>
      <div class="cover ${track.thumbnail ? '' : 'none'}"
           style="${track.thumbnail ? `background-image:url('${esc(track.thumbnail)}')` : ''}">
        <span class="dur">${duration(track.duration)}</span>
      </div>
      <div class="body">
        <div class="title">${esc(track.title)}</div>
        <div class="subtitle">${esc(track.artist || 'unknown artist')}
          <span class="sep">·</span>${esc(track.playlist || 'single track')}</div>
        ${note}
      </div>
      <div class="right">${right}</div>
    </li>`;
}

function statusIcon(status) {
  return { done: 'i-check', error: 'i-warn', skipped: 'i-check', cancelled: 'i-close', running: 'i-download', pending: 'i-pause' }[status] || 'i-disc';
}

function shortSkip(track) {
  const reason = track.skip_reason || 'skipped';
  return { 'already-downloaded': 'Already on drive', 'same-song': 'Same song on drive', 'already-queued': 'Already queued', 'duplicate-in-selection': 'Listed twice' }[reason] || reason;
}

// One sentence that says what the queue is doing right now, plus how it should look.
function queueHeadline(snapshot, counts, processed, total) {
  const running = counts.running || 0;
  const pending = counts.pending || 0;
  const active = running + pending;
  const current = (snapshot.jobs || []).flatMap((job) => job.tracks).find((track) => track.status === 'running');
  if (snapshot.paused && active) {
    return { title: 'Paused', sub: `${active} track${active === 1 ? '' : 's'} waiting · press Resume to continue`, orb: 'paused', icon: 'i-pause' };
  }
  if (active) {
    return {
      title: `Downloading ${Math.min(processed + 1, total)} of ${total}`,
      sub: current ? `Now: ${current.title}` : 'Starting the next track…',
      orb: 'running',
      icon: 'i-disc',
    };
  }
  if (counts.error) {
    return { title: `Finished with ${counts.error} failed`, sub: 'Hit Retry on a failed track below', orb: 'bad', icon: 'i-warn' };
  }
  return { title: 'All done', sub: `${counts.done || 0} saved to your drive`, orb: 'done', icon: 'i-check' };
}

function renderQueue() {
  const snapshot = state.queue;
  if (!snapshot) return;
  const jobs = snapshot.jobs || [];
  els.queuePanel.hidden = jobs.length === 0;
  if (!jobs.length) { document.title = 'Musica'; return; }
  els.emptyState.hidden = true;

  const counts = snapshot.counts || {};
  const totalTrack = jobs.reduce((sum, job) => sum + job.total, 0);
  const processed = jobs.reduce((sum, job) => sum + job.processed, 0);
  const size = jobs.reduce((sum, job) => sum + (job.bytes_done || 0), 0);

  els.pauseBtn.querySelector('span').textContent = snapshot.paused ? 'Resume' : 'Pause';
  els.pauseBtn.querySelector('use').setAttribute('href', snapshot.paused ? '#i-play' : '#i-pause');

  const head = queueHeadline(snapshot, counts, processed, totalTrack);
  const busy = head.orb === 'running';
  els.queueHeadline.textContent = head.title;
  els.queueMeta.textContent = head.sub + (size ? ` · ${bytes(size)} written` : '');
  els.queueOrb.className = `status-orb ${head.orb}`;
  els.queueOrb.querySelector('use').setAttribute('href', `#${head.icon}`);
  els.pauseBtn.hidden = !busy && head.orb !== 'paused';
  els.cancelBtn.hidden = !busy && head.orb !== 'paused';

  const percent = totalTrack ? (processed / totalTrack) * 100 : 0;
  els.overallBar.style.width = `${percent.toFixed(1)}%`;
  els.overall.classList.toggle('working', busy);
  els.queuePct.innerHTML = `${Math.round(percent)}<small>%</small>`;
  document.title = busy ? `(${Math.round(percent)}%) Downloading · Musica` : head.orb === 'done' ? '✓ Done · Musica' : 'Musica';

  els.queueChips.innerHTML = [
    ['done', 'saved'],
    ['running', 'downloading'],
    ['pending', 'waiting'],
    ['skipped', 'already had'],
    ['error', 'failed'],
  ].filter(([key]) => counts[key]).map(([key, label]) => (
    `<span class="chip c-${key}"><b>${counts[key]}</b>${label}</span>`
  )).join('');

  els.queueList.innerHTML = jobs.map((job) => `
    <li class="job-head">
      <div>
        <strong>${esc(job.label || 'Download')}</strong>
        <span class="job-dest">${esc(job.destination)}</span>
        <span class="job-status ${job.status}">${esc(job.status)}</span>
      </div>
      <em>${job.counts.done}/${job.total}</em>
    </li>
    ${job.tracks.map(trackRow).join('')}
  `).join('');

  bindRetryButtons();
}

function bindRetryButtons() {
  els.queueList.querySelectorAll('[data-retry]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await request('/api/queue/retry', {
          method: 'POST',
          body: JSON.stringify({ keys: [button.dataset.retry], force: true }),
        });
        toast('Retrying that track, ignoring the duplicate rules.');
      } catch (error) {
        toast(error.message, 'bad');
      }
    });
  });
}

function renderLogs() {
  els.logList.innerHTML = state.logs.slice(-80).reverse().map((entry) => `
    <li class="${esc(entry.level)}"><time>${clock(entry.at)}</time><span>${esc(entry.text)}</span></li>
  `).join('');
}

/* ── settings ─────────────────────────────────────────────────────────── */

function applySettingsToForm() {
  const settings = state.settings;
  if (!settings) return;
  els.formatSelect.value = settings.format;
  els.qualitySelect.value = settings.quality;
  els.qualitySelect.disabled = ['flac', 'wav'].includes(settings.format);
  els.setNaming.value = settings.naming;
  els.setSubfolders.checked = !!settings.playlist_subfolders;
  els.setSearchResults.value = String(settings.search_results);
  els.setConcurrency.value = String(settings.concurrency);
  els.setArchiveOnDrive.checked = !!settings.archive_on_drive;
  els.setSkipName.checked = !!settings.skip_name_duplicates;
  els.setStage.checked = !!settings.stage_locally;
  els.setCookiefile.value = settings.cookiefile || '';
  els.setCarMode.checked = !!settings.car_mode;
  els.setNumberFiles.checked = !!settings.number_files;
  els.setCarGenre.value = settings.car_genre || 'Music';
  els.setNumberFiles.disabled = !settings.car_mode;
  state.destination = settings.destination || state.destination;
}

async function saveSettings(patch) {
  if (!state.settings) return;
  Object.assign(state.settings, patch);
  applySettingsToForm();
  try {
    const saved = await request('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({ settings: patch }),
    });
    state.settings = saved.settings || saved;
    applySettingsToForm();
    if (patch.destination !== undefined) {
      state.destination = patch.destination;
      renderDestination();
    }
    if (patch.format !== undefined) {
      const s = state.settings;
      toast(`${String(s.format).toUpperCase()} · ${s.quality} will be used from now on`, 'good');
    }
  } catch (error) {
    toast(error.message, 'bad');
  }
}

/* ── format the stick for the car ─────────────────────────────────────── */

async function openFormat() {
  const path = state.destination;
  if (!path) return;
  els.formatModal.hidden = false;
  els.formatConfirm.value = '';
  els.formatGo.disabled = true;
  els.formatLabel.value = '';
  els.formatWarning.textContent = 'Checking what would run…';
  els.formatCommand.textContent = '…';
  try {
    const plan = await request('/api/drives/plan', {
      method: 'POST',
      body: JSON.stringify({ path, fstype: els.formatType.value, label: els.formatLabel.value }),
    });
    els.formatWarning.textContent = plan.warning;
    els.formatCommand.textContent = plan.command;
  } catch (error) {
    els.formatWarning.textContent = error.message;
    els.formatCommand.textContent = 'not available from this app';
  }
}

function closeFormat() {
  els.formatModal.hidden = true;
}

async function runFormat() {
  if (els.formatConfirm.value.trim() !== 'FORMAT') return;
  els.formatGo.disabled = true;
  els.formatGo.textContent = 'Formatting…';
  try {
    const done = await request('/api/drives/format', {
      method: 'POST',
      body: JSON.stringify({
        path: state.destination,
        fstype: els.formatType.value,
        label: els.formatLabel.value,
        confirm: els.formatConfirm.value.trim(),
      }),
    });
    state.drives = done.drives.drives;
    state.driveInfo = done.drives;
    closeFormat();
    els.formatGo.textContent = 'Erase and format';
    renderDestination();
    toast('The stick is formatted for the car. Re-copy anything you had on it.', 'good');
  } catch (error) {
    els.formatGo.disabled = false;
    els.formatGo.textContent = 'Erase and format';
    toast(error.message, 'bad');
  }
}

function renderHealth() {
  const health = state.health;
  if (!health) return;
  els.versions.innerHTML = [
    `yt-dlp ${esc(health.ytdlp)}`,
    health.ffmpeg.ok ? `ffmpeg ${esc(health.ffmpeg.version)}` : 'ffmpeg MISSING',
    `python ${esc(health.python)}`,
    health.os,
  ].map((item) => `<span class="${item.includes('MISSING') ? 'bad' : ''}">${item}</span>`).join('');

  els.healthList.innerHTML = [
    ['yt-dlp', health.ytdlp],
    ['ffmpeg', health.ffmpeg.ok ? health.ffmpeg.version || 'found' : 'not found'],
    ['JS runtime', health.js_runtime || 'none - YouTube may refuse'],
    ['Python', health.python],
    ['System', health.os],
    ['Musica', health.version],
  ].map(([label, value]) => `
    <div><span>${esc(label)}</span><b class="${value.startsWith('not') || value.startsWith('none') ? 'bad' : ''}">${esc(value)}</b></div>
  `).join('');
}

/* ── folder browser ───────────────────────────────────────────────────── */

async function openBrowser(path) {
  els.browserModal.hidden = false;
  els.scrim.hidden = false;
  try {
    const listing = await request('/api/folders', {
      method: 'POST',
      body: JSON.stringify({ path: path || state.destination || '' }),
    });
    state.browserPath = listing.path;
    els.browserPath.textContent = listing.path + (listing.error ? `  (${listing.error})` : '');
    els.browserList.innerHTML = (listing.dirs || []).map((dir) => `
      <li><button data-path="${esc(dir.path)}">${icon('i-folder')}<span>${esc(dir.name)}</span></button></li>
    `).join('') || '<li><button disabled>Nothing else in here.</button></li>';
    els.browserList.querySelectorAll('button[data-path]').forEach((button) => {
      button.addEventListener('click', () => openBrowser(button.dataset.path));
    });
  } catch (error) {
    toast(error.message, 'bad');
    closeBrowser();
  }
}

function closeBrowser() {
  els.browserModal.hidden = true;
  if (els.settingsDrawer.hidden) els.scrim.hidden = true;
}

/* ── wiring ───────────────────────────────────────────────────────────── */

function bindUi() {
  els.queryForm.addEventListener('submit', (event) => {
    event.preventDefault();
    find(els.query.value);
  });

  els.suggest.addEventListener('click', (event) => {
    const button = event.target.closest('[data-fill]');
    if (!button) return;
    els.query.value = button.dataset.fill;
    els.query.focus();
    const end = els.query.value.length;
    els.query.setSelectionRange(end, end);
  });

  els.driveSelect.addEventListener('change', async () => {
    state.destination = els.driveSelect.value;
    renderDestination();
    await saveSettings({ destination: state.destination });
    if (state.preview) refreshOwnership();
  });

  els.refreshDrives.addEventListener('click', async () => {
    els.refreshDrives.querySelector('svg').style.animation = 'spin 0.7s linear';
    await refreshDrives();
    setTimeout(() => { els.refreshDrives.querySelector('svg').style.animation = ''; }, 700);
    toast(state.drives.length ? `Found ${state.drives.length} drive(s)` : 'Still no removable drive detected');
  });

  els.browseBtn.addEventListener('click', () => openBrowser(state.destination));
  els.formatBtn.addEventListener('click', openFormat);
  els.formatClose.addEventListener('click', closeFormat);
  els.formatCancel.addEventListener('click', closeFormat);
  els.formatGo.addEventListener('click', runFormat);
  els.formatConfirm.addEventListener('input', () => {
    els.formatGo.disabled = els.formatConfirm.value.trim() !== 'FORMAT';
  });
  els.formatType.addEventListener('change', openFormat);
  els.browserClose.addEventListener('click', closeBrowser);
  els.browserCancel.addEventListener('click', closeBrowser);
  els.browserUp.addEventListener('click', () => {
    const parent = state.browserPath.replace(/[/\\][^/\\]*[/\\]?$/, '');
    openBrowser(parent || state.browserPath);
  });
  els.browserPick.addEventListener('click', async () => {
    if (!state.browserPath) return;
    state.destination = state.browserPath;
    closeBrowser();
    await saveSettings({ destination: state.destination });
    renderDestination();
    if (state.preview) refreshOwnership();
  });

  els.formatSelect.addEventListener('change', () => saveSettings({ format: els.formatSelect.value }));
  els.qualitySelect.addEventListener('change', () => saveSettings({ quality: els.qualitySelect.value }));

  els.previewList.addEventListener('change', (event) => {
    const box = event.target.closest('.pick');
    if (!box) return;
    const key = box.closest('.track').dataset.key;
    if (box.checked) state.selected.add(key); else state.selected.delete(key);
    renderPreview();
  });
  // The whole row is the hit target, not just the 20px checkbox.
  els.previewList.addEventListener('click', (event) => {
    if (event.target.closest('.pick')) return;
    const box = event.target.closest('.track')?.querySelector('.pick');
    if (!box) return;
    box.checked = !box.checked;
    box.dispatchEvent(new Event('change', { bubbles: true }));
  });

  els.selectAllBtn.addEventListener('click', () => {
    state.selected = new Set(visibleItems().map(itemKey));
    renderPreview();
  });
  els.selectNoneBtn.addEventListener('click', () => {
    state.selected.clear();
    renderPreview();
  });
  els.hideOwned.addEventListener('change', () => {
    state.hideOwned = els.hideOwned.checked;
    renderPreview();
  });

  els.downloadBtn.addEventListener('click', download);

  els.pauseBtn.addEventListener('click', async () => {
    await request('/api/queue/pause', {
      method: 'POST',
      body: JSON.stringify({ paused: !state.queue.paused }),
    });
  });
  els.cancelBtn.addEventListener('click', async () => {
    await request('/api/queue/cancel', { method: 'POST' });
    toast('Cancelling: tracks that had not started were dropped.');
  });
  els.clearBtn.addEventListener('click', () => request('/api/queue/clear', { method: 'POST' }));

  els.settingsBtn.addEventListener('click', () => openDrawer(true));
  els.settingsClose.addEventListener('click', () => openDrawer(false));
  els.scrim.addEventListener('click', () => { openDrawer(false); closeBrowser(); });

  [
    ['setNaming', 'naming', 'value'],
    ['setSubfolders', 'playlist_subfolders', 'checked'],
    ['setSearchResults', 'search_results', 'value'],
    ['setConcurrency', 'concurrency', 'value'],
    ['setArchiveOnDrive', 'archive_on_drive', 'checked'],
    ['setSkipName', 'skip_name_duplicates', 'checked'],
    ['setStage', 'stage_locally', 'checked'],
    ['setCookiefile', 'cookiefile', 'value'],
    ['setCarMode', 'car_mode', 'checked'],
    ['setNumberFiles', 'number_files', 'checked'],
    ['setCarGenre', 'car_genre', 'value'],
  ].forEach(([id, key, property]) => {
    els[id].addEventListener('change', () => {
      const value = els[id][property];
      saveSettings({ [key]: key.endsWith('results') || key === 'concurrency' ? Number(value) : value });
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { openDrawer(false); closeBrowser(); closeFormat(); }
    if (event.key === '/' && document.activeElement !== els.query) {
      event.preventDefault();
      els.query.focus();
    }
  });
}

function openDrawer(open) {
  els.settingsDrawer.hidden = !open;
  els.scrim.hidden = !open && els.browserModal.hidden;
}

// Changing the destination changes who is "already owned". Ask the backend again and
// copy only the ownership flags across, so the user's checkboxes survive.
async function refreshOwnership() {
  if (!state.preview) return;
  const handle = state.preview.query || state.preview.url;
  if (!handle) return;
  try {
    const result = await request('/api/resolve', { method: 'POST', body: JSON.stringify({ query: handle }) });
    const byKey = new Map(result.items.map((item) => [itemKey(item), item]));
    state.preview.items = state.preview.items.map((item) => {
      const fresh = byKey.get(itemKey(item));
      if (!fresh) return item;
      return {
        ...item,
        in_archive: fresh.in_archive,
        name_duplicate: fresh.name_duplicate,
        duplicate_display: fresh.duplicate_display,
      };
    });
    state.preview.scanned_files = result.scanned_files;
    renderPreview();
  } catch {
    // The server still applies both skip rules at download time, so this is cosmetic.
  }
}

document.addEventListener('DOMContentLoaded', boot);
