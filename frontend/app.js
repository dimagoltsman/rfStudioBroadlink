'use strict';

/* ---------- tiny helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hex24 = (n) => Number(n).toString(16).padStart(6, '0');

const api = {
  async call(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : res.statusText);
    }
    return data;
  },
  get: (p) => api.call('GET', p),
  post: (p, b) => api.call('POST', p, b ?? {}),
  put: (p, b) => api.call('PUT', p, b),
  patch: (p, b) => api.call('PATCH', p, b),
  del: (p) => api.call('DELETE', p),
};

const state = {
  tab: 'remotes',
  devices: [], codes: [], remotes: [], protocols: [], deviceTypes: [],
  activeDevice: localStorage.getItem('rfw.device') || '',
  learn: null, learnTimer: null, learnSweep: true, learnFreq: '433.92',
  sessionCaptures: [], captureCount: 0,
  selectedCodes: new Set(), compare: null, openAnalyses: {},
};

function toast(msg, kind = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'err' ? 7000 : 3500);
}
const fail = (e) => toast(e.message || String(e), 'err');

async function guarded(fn) {
  try { return await fn(); } catch (e) { fail(e); return undefined; }
}

/* ---------- data ---------- */
async function loadAll() {
  [state.devices, state.codes, state.remotes, state.protocols, state.deviceTypes] = await Promise.all([
    api.get('/api/devices'), api.get('/api/codes'), api.get('/api/remotes'), api.get('/api/protocols'), api.get('/api/device-types'),
  ]);
  if (!state.devices.some((d) => d.id === state.activeDevice)) {
    state.activeDevice = state.devices[0]?.id || '';
  }
  renderHeader();
}
const deviceName = (id) => state.devices.find((d) => d.id === id)?.name || '—';
const codeName = (id) => state.codes.find((c) => c.id === id)?.name || '—';
const protocol = (name) => state.protocols.find((p) => p.name === name);
const activeDevice = () => state.devices.find((d) => d.id === state.activeDevice);

/* ---------- virtual remote helpers ---------- */
const MAIN_COMMANDS = { open: '▲ Open', stop: '■ Stop', close: '▼ Close' };
const paramDisplay = (p, value) => (value === undefined ? '—' : p.kind === 'hex' ? hex24(value) : String(value));
function paramsSummary(proto, params) {
  return (proto?.params || []).map((p) => `${esc(p.label.toLowerCase())} <code>${esc(paramDisplay(p, params[p.key]))}</code>`).join(' · ');
}
/* open/stop/close get the big buttons when the remote has them; otherwise its own commands do (e.g. a one-button garage remote). */
function remoteCommands(c, proto) {
  const all = c.mode === 'virtual' ? Object.entries(proto?.commands || {}) : Object.keys(c.codes).map((k) => [k, k]);
  const main = all.filter(([k]) => k in MAIN_COMMANDS).map(([k]) => [k, MAIN_COMMANDS[k]]);
  const extra = all.filter(([k]) => !(k in MAIN_COMMANDS));
  return main.length ? { main, extra } : { main: extra, extra: [] };
}

function renderHeader() {
  const sel = $('#active-device');
  sel.innerHTML = state.devices.length
    ? state.devices.map((d) => `<option value="${d.id}" ${d.id === state.activeDevice ? 'selected' : ''}>${esc(d.name)}</option>`).join('')
    : '<option value="">no radios yet</option>';
  $('#device-dot').className = `dot ${state.activeDevice ? 'ok' : ''}`;
}

/* ---------- modal ---------- */
function openModal(html) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-bg"><div class="modal">${html}</div></div>`;
  root.querySelector('.modal-bg').addEventListener('click', (e) => { if (e.target.classList.contains('modal-bg')) closeModal(); });
  return root.querySelector('.modal');
}
const closeModal = () => { $('#modal-root').innerHTML = ''; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

/* ---------- oscilloscope ---------- */
function drawScope(canvas, pulses, zoom = 1) {
  const wrap = canvas.parentElement;
  const W = Math.max(wrap.clientWidth || 600, 300) * zoom;
  const H = 140;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = `${W}px`;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.fillStyle = '#0a0c0f'; ctx.fillRect(0, 0, W, H);
  const total = pulses.reduce((a, b) => a + b, 0) || 1;
  const px = W / total;
  ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.lineWidth = 1;
  for (let t = 0; t < total; t += 5000) { ctx.beginPath(); ctx.moveTo(t * px, 0); ctx.lineTo(t * px, H); ctx.stroke(); }
  ctx.fillStyle = 'rgba(138,144,153,.7)'; ctx.font = '10px JetBrains Mono, monospace';
  ctx.fillText(`${(total / 1000).toFixed(1)} ms · ${pulses.length} pulses`, 6, H - 6);
  const hi = 28, lo = 104;
  ctx.strokeStyle = '#f2a93b'; ctx.lineWidth = 1.4; ctx.shadowColor = 'rgba(242,169,59,.6)'; ctx.shadowBlur = 6;
  ctx.beginPath();
  let x = 0; ctx.moveTo(0, lo);
  pulses.forEach((p, i) => {
    const level = i % 2 === 0 ? hi : lo;
    ctx.lineTo(x, level); x += p * px; ctx.lineTo(x, level);
  });
  ctx.lineTo(x, lo); ctx.stroke();
}
function scopeHtml(id) {
  return `<div class="scope"><canvas id="scope-${id}"></canvas></div>
    <div class="scope-tools">zoom <input type="range" min="1" max="12" step="1" value="1" data-scope="${id}"> <span class="muted">drag to scroll when zoomed</span></div>`;
}
function mountScope(id, pulses) {
  const canvas = $(`#scope-${id}`);
  if (!canvas) return;
  drawScope(canvas, pulses, 1);
  $(`[data-scope="${id}"]`)?.addEventListener('input', (e) => drawScope(canvas, pulses, Number(e.target.value)));
}

/* ---------- bit rendering ---------- */
function bitsHtml(bits, diffPositions = new Set()) {
  if (!bits) return '<span class="muted">—</span>';
  let out = '';
  for (let i = 0; i < bits.length; i += 4) {
    out += '<span class="nib">';
    for (let j = i; j < Math.min(i + 4, bits.length); j++) {
      out += `<span class="${diffPositions.has(j) ? 'd' : ''}">${bits[j]}</span>`;
    }
    out += '</span>';
  }
  return `<div class="bits">${out}</div>`;
}
function analysisHtml(a, id) {
  const unique = a.unique_frames.map((u) => `<div><span class="badge">${u.count}×</span> <code>${esc(u.hex)}</code> <span class="muted small">${u.length} bits</span></div>`).join('');
  const guess = { aok: 'teal', dooya: 'teal', secplus1: 'teal', unknown: '' }[a.guess] ?? '';
  const decoded = a.decoded
    ? `<dt>Decoded</dt><dd>${Object.entries(a.decoded).map(([k, v]) => `${esc(k)} <code>${esc(v)}</code>`).join(' · ')}
        <div class="muted small" style="margin-top:4px">Virtual remote: copy id1 / id0 / button into its fields (keep your own remote id), then pair it.</div></dd>`
    : '';
  return `${scopeHtml(id)}
    <dl class="kv" style="margin-top:14px">
      <dt>Frames</dt><dd>${a.frames.length} decoded · ${a.unique_frames.length} distinct</dd>
      <dt>Timing</dt><dd class="mono small">short ${a.short_us} µs · long ${a.long_us} µs · sync ${a.sync_us.length ? a.sync_us[0] + ' µs' : 'none'} · ${(a.duration_us / 1000).toFixed(1)} ms total</dd>
      <dt>Encoding</dt><dd><span class="badge amber">${esc(a.encoding)}</span></dd>
      <dt>Protocol</dt><dd><span class="badge ${guess}">${esc(a.guess)}</span></dd>
      ${decoded}
      <dt>Consensus</dt><dd>${bitsHtml(a.consensus)}</dd>
      ${a.unique_frames.length ? `<dt>Distinct</dt><dd>${unique}</dd>` : ''}
      ${a.warnings.length ? `<dt>Notes</dt><dd class="muted">${a.warnings.map(esc).join('<br>')}</dd>` : ''}
    </dl>`;
}
function compareHtml(cmp) {
  if (cmp.verdict === 'need_two') return '<div class="notice">Select at least two captures.</div>';
  if (cmp.verdict === 'length_mismatch') return `<div class="notice"><h4>Different lengths</h4>${esc(cmp.message)}</div>`;
  const diff = new Set(cmp.positions);
  const verdict = cmp.verdict === 'identical'
    ? `<div class="status-line ok">All ${cmp.length} bits identical across captures → <b>fixed code</b>. Replay will work.</div>`
    : `<div class="status-line err">${cmp.positions.length} of ${cmp.length} bits change between captures (positions ${cmp.positions.join(', ')}) → the remote sends a <b>counter / rolling code</b>. Blind replay will not work; the changing bits mark the counter field, the stable bits are the remote identity.</div>`;
  const rows = cmp.rows.map(([name, bits]) => `<tr><td class="muted small" style="white-space:nowrap">${esc(name)}</td><td>${bitsHtml(bits, diff)}</td></tr>`).join('');
  return `${verdict}<table style="margin-top:12px">${rows}</table>`;
}

/* ---------- views ---------- */
const views = {
  remotes() {
    const cards = state.remotes.map((c) => {
      const proto = c.mode === 'virtual' ? protocol(c.protocol) : null;
      const { main, extra } = remoteCommands(c, proto);
      const button = ([k, label], big) => `<button class="${big ? `big ${esc(k)}` : ''}" data-action="remote-cmd" data-id="${c.id}" data-cmd="${esc(k)}">${esc(label)}</button>`;
      const meta = c.mode === 'virtual'
        ? `${esc(proto?.label || c.protocol)} · ${paramsSummary(proto, c.params)} · ×${c.repeat}`
        : `${Object.keys(c.codes).length} learned codes`;
      return `<div class="panel remote">
        <div class="remote-head"><div><h4>${esc(c.name)}</h4><div class="meta">${esc(deviceName(c.device_id))} · ${meta}</div></div>
          <span class="badge ${c.mode === 'virtual' ? 'amber' : 'teal'}">${c.mode}</span></div>
        <div class="controls">${main.map((cmd) => button(cmd, true)).join('')}</div>
        <div class="row" style="justify-content:space-between">
          <div class="extra">${extra.map((cmd) => button(cmd, false)).join('')}</div>
          <div class="row">
            ${c.mode === 'virtual' ? `<button class="ghost" data-action="pair" data-id="${c.id}">Pairing guide</button>` : ''}
            <button class="ghost" data-action="edit-remote" data-id="${c.id}">Edit</button>
            <button class="ghost danger icon" data-action="del-remote" data-id="${c.id}" title="Delete">✕</button>
          </div>
        </div></div>`;
    }).join('');
    return `<div class="section-head"><div><h2>Remotes</h2><p>Anything with an RF remote: curtain motors, garage openers, fans, outlets. Either replay codes learned from the original remote, or let the workbench synthesise a virtual remote and pair it to the receiver.</p></div>
      <button class="primary" data-action="new-remote">+ New remote</button></div>
      ${state.remotes.length ? `<div class="grid">${cards}</div>` : '<div class="empty">No remotes yet. Learn a code from an original remote, or create a virtual one.</div>'}`;
  },

  learn() {
    const dev = activeDevice();
    const l = state.learn;
    const active = l && ['starting', 'sweeping', 'waiting'].includes(l.phase);
    const step = (key, title, text, idx) => {
      const order = ['sweeping', 'waiting', 'done'];
      const cur = l ? order.indexOf(l.phase === 'starting' ? 'sweeping' : l.phase) : -1;
      const cls = !l ? '' : idx < cur || l.phase === 'done' ? 'done' : idx === cur ? 'live' : '';
      return `<div class="step ${cls}"><b>${key}</b>${title}<div class="muted small">${text}</div></div>`;
    };
    const result = l?.analysis ? `<div class="panel"><h3>Capture</h3>${analysisHtml(l.analysis, 'learn')}
        <div class="form-grid" style="margin-top:16px">
          <label class="field">Name<input id="code-name" value="${esc(suggestName())}"></label>
          <label class="field">Notes<input id="code-notes" placeholder="e.g. UP, channel 2, 3rd press"></label>
        </div>
        <div class="row end" style="margin-top:12px">
          <button data-action="test-capture">Test send</button>
          <button data-action="save-capture" data-again="1">Save &amp; capture again</button>
          <button class="primary" data-action="save-capture">Save code</button>
        </div></div>` : '';
    const session = state.sessionCaptures.length ? `<div class="panel"><h3>This session</h3>
        <div class="row" style="justify-content:space-between"><div class="muted small">${state.sessionCaptures.map((id) => esc(codeName(id))).join(' · ')}</div>
        <button data-action="compare-session" ${state.sessionCaptures.length < 2 ? 'disabled' : ''}>Compare captures</button></div>
        <div id="session-compare" style="margin-top:12px">${state.compare ? compareHtml(state.compare) : ''}</div></div>` : '';
    return `<div class="section-head"><div><h2>Learn</h2><p>Capture a button from the original remote through the Broadlink. Capture the same button several times and compare: if bits change between presses the receiver expects a rolling code and replay will not work.</p></div></div>
      <div class="panel"><h3>Radio</h3>
        ${dev ? `<div class="row"><span class="badge ${dev.rf ? 'teal' : 'red'}">${dev.rf ? 'RF capable' : 'no RF'}</span> <b>${esc(dev.name)}</b> <span class="muted small">${esc(dev.model)} · ${esc(dev.host)}</span></div>` : '<div class="status-line err">No active radio. Add one under Radios.</div>'}
        <div class="row" style="margin-top:14px">
          <label class="check"><input type="checkbox" id="learn-sweep" ${state.learnSweep ? 'checked' : ''} ${active ? 'disabled' : ''}> Sweep for frequency first (recommended)</label>
          <label class="field" style="flex-direction:row;align-items:center;gap:8px">MHz <input id="learn-freq" class="mono" style="width:100px" value="${esc(state.learnFreq)}" ${state.learnSweep || active ? 'disabled' : ''}></label>
          <span style="flex:1"></span>
          ${active ? '<button class="danger" data-action="learn-cancel">Cancel</button>' : `<button class="primary" data-action="learn-start" ${dev && dev.rf ? '' : 'disabled'}>Start learning</button>`}
        </div>
        <div class="stepper">
          ${step('01 · sweep', 'Hold the button', 'Press and hold the remote button ~30 cm from the Broadlink until the frequency locks.', 0)}
          ${step('02 · capture', 'Press again', 'Release, wait a second, then press the same button briefly.', 1)}
          ${step('03 · decode', 'Inspect & save', 'The waveform and decoded bits appear below.', 2)}
        </div>
        ${l ? `<div class="status-line ${l.phase === 'error' ? 'err' : l.phase === 'done' ? 'ok' : ''}">${esc(l.message)}${l.frequency ? ` · ${l.frequency.toFixed(2)} MHz` : ''}</div>` : ''}
      </div>
      ${result}${session}`;
  },

  codes() {
    const rows = state.codes.map((c) => {
      const sel = state.selectedCodes.has(c.id);
      const a = state.openAnalyses[c.id];
      return `<tr class="${sel ? 'selected' : ''}">
        <td><input type="checkbox" data-action="toggle-code" data-id="${c.id}" ${sel ? 'checked' : ''}></td>
        <td><b>${esc(c.name)}</b>${c.notes ? `<div class="muted small">${esc(c.notes)}</div>` : ''}</td>
        <td><span class="badge ${c.guess !== 'unknown' ? 'teal' : ''}">${esc(c.guess || '?')}</span></td>
        <td class="mono small">${c.bits ? c.bits.length + ' b' : '—'}</td>
        <td class="small muted">${c.frequency ? c.frequency.toFixed(2) + ' MHz' : '—'}</td>
        <td class="row end" style="flex-wrap:nowrap">
          <button data-action="send-code" data-id="${c.id}">Send</button>
          <button class="ghost" data-action="analyze-code" data-id="${c.id}">${a ? 'Hide' : 'Analyze'}</button>
          <button class="ghost danger icon" data-action="del-code" data-id="${c.id}">✕</button></td></tr>
        ${a ? `<tr><td colspan="6">${analysisHtml(a, c.id)}</td></tr>` : ''}`;
    }).join('');
    return `<div class="section-head"><div><h2>Codes</h2><p>Learned Broadlink packets. Send them to the active radio, inspect the decoded frame, or select several captures of the same button and compare them bit by bit.</p></div>
      <button data-action="compare-codes" ${state.selectedCodes.size < 2 ? 'disabled' : ''}>Compare ${state.selectedCodes.size || ''} selected</button></div>
      ${state.compare ? `<div class="panel"><h3>Comparison</h3>${compareHtml(state.compare)}</div>` : ''}
      <div class="panel">${state.codes.length ? `<table><thead><tr><th></th><th>Name</th><th>Guess</th><th>Len</th><th>Freq</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">Nothing learned yet.</div>'}</div>`;
  },

  devices() {
    const rows = state.devices.map((d) => `<tr class="${d.id === state.activeDevice ? 'selected' : ''}">
      <td><b>${esc(d.name)}</b><div class="muted small">${esc(d.manufacturer || '')} ${esc(d.model)}</div></td>
      <td class="mono small">${esc(d.host)}<br><span class="muted">${esc(d.mac)}</span></td>
      <td><span class="badge ${d.rf ? 'teal' : 'red'}">${d.rf ? 'IR + RF' : 'IR only'}</span></td>
      <td class="row end" style="flex-wrap:nowrap">
        ${d.id === state.activeDevice ? '<span class="badge teal">active</span>' : `<button data-action="use-device" data-id="${d.id}">Use</button>`}
        <button class="ghost" data-action="rename-device" data-id="${d.id}">Rename</button>
        <button class="ghost danger icon" data-action="del-device" data-id="${d.id}">✕</button></td></tr>`).join('');
    return `<div class="section-head"><div><h2>Radios</h2><p>Broadlink RM pro / RM4 pro units on your LAN. Discovery needs host networking; adding by IP works everywhere.</p></div></div>
      <div class="panel"><h3>Find</h3><div class="row">
        <button class="primary" data-action="discover">Discover on LAN</button>
        <span class="muted small">or</span>
        <input id="add-host" class="mono" placeholder="192.168.1.42" style="width:180px">
        <button data-action="add-device">Add by IP</button>
        <span class="muted small">The device must already be on Wi-Fi (set up once in the Broadlink app).</span>
      </div>
      <details style="margin-top:14px"><summary class="muted small" style="cursor:pointer">Manual add (Docker Desktop / NAT: discovery and add-by-IP cannot work, this can)</summary>
        <div class="row" style="margin-top:10px">
          <input id="manual-host" class="mono" placeholder="192.168.1.104" style="width:150px">
          <input id="manual-mac" class="mono" placeholder="a0:43:b0:12:34:56" style="width:170px">
          <select id="manual-type">${state.deviceTypes.map((t) => `<option value="${t.devtype}">${esc(t.model)} (${t.devtype.toString(16)})</option>`).join('')}</select>
          <button data-action="add-manual">Add &amp; verify</button>
        </div>
        <p class="muted small">IP and MAC are shown in the Broadlink app under device info, or run <code>python3 -c "import broadlink; print([(d.model, d.host, d.mac.hex(':'), hex(d.devtype)) for d in broadlink.discover(timeout=5)])"</code> on a machine on the LAN.</p>
      </details></div>
      <div class="panel">${state.devices.length ? `<table><thead><tr><th>Device</th><th>Address</th><th>Radio</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">No radios yet.</div>'}</div>`;
  },

  protocols() {
    const cards = state.protocols.map((p) => `<div class="panel"><h3>${esc(p.label)}</h3><p class="small" style="margin-top:0">${esc(p.description)}</p>
      <dl class="kv"><dt>Radio</dt><dd>${p.frequency_mhz} MHz</dd>
      <dt>Fields</dt><dd>${p.params.map((x) => `<code>${esc(x.key)}</code> <span class="muted small">${esc(x.help)}</span>`).join('<br>')}</dd>
      <dt>Commands</dt><dd>${Object.values(p.commands).map(esc).join(' · ')}</dd>
      <dt>Repeat</dt><dd>${p.default_repeat}× per press</dd></dl>
      <h3 style="margin-top:16px">Pairing</h3><ol class="steps small">${p.pairing_steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol></div>`).join('');
    return `<div class="section-head"><div><h2>Protocols</h2><p>Frame encoders available for virtual remotes. A virtual remote has its own random identity; the receiver learns it through its normal “add a remote” procedure, so no capture of the original is needed.</p></div></div>
      <div class="grid">${cards}</div>${alphaNotice(true)}`;
  },
};

function alphaNotice(full = false) {
  return `<div class="notice" style="margin-top:18px"><h4>Alpha WSRE303 / WSER motors (433.92 MHz)</h4>
    Verified with real captures: Alpha sends a 65-bit Manchester frame (400 µs half-bits, 52 ms per frame, 6.4 ms gaps) and
    <b>every press changes about half of the bits with no fixed serial field</b>, i.e. the payload is encrypted. Replay cannot work,
    and a virtual remote would need the cipher and key, which are not public. For Alpha motors use the Alpha Neo hub or a
    button-presser on a spare Alpha remote; this workbench still fully applies to Dooya, A-OK and any fixed-code motor.
    ${full ? `<br><br><b>Alpha programming grammar (from the WSER cheat sheet):</b> every command is <i>press UP+DOWN together for 1 s → press STOP N× → press the final button</i>; the motor jiggles to confirm.
    <br>· Assign first remote: power the motor (or tap its RED/WHITE button), within 10 s do UP+DOWN then STOP 1×.
    <br>· Copy a channel to a new remote: on the old remote UP+DOWN then STOP 8× (motor jiggles); on the new remote UP+DOWN then STOP 1×.
    <br>· Delete all remotes: UP+DOWN then STOP 6× then UP. Delete a channel: STOP 7× then UP.` : ''}</div>`;
}

let captureSeq = 0;
function suggestName() { return `Capture ${state.captureCount + 1}`; }

/* ---------- remote form ---------- */
function remoteForm(c) {
  const isEdit = Boolean(c);
  const data = c || { name: '', device_id: state.activeDevice, mode: 'learned', codes: {}, protocol: state.protocols[0]?.name, params: {}, repeat: null };
  const codeOptions = (sel) => `<option value="">—</option>${state.codes.map((x) => `<option value="${x.id}" ${x.id === sel ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}`;
  const actionRows = isEdit ? Object.entries(data.codes) : Object.keys(MAIN_COMMANDS).map((k) => [k, '']);
  const modal = openModal(`<h3>${isEdit ? 'Edit' : 'New'} remote</h3>
    <form id="remote-form">
      <div class="form-grid">
        <label class="field">Name<input name="name" required value="${esc(data.name)}"></label>
        <label class="field">Radio<select name="device_id">${state.devices.map((d) => `<option value="${d.id}" ${d.id === data.device_id ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select></label>
      </div>
      <div class="row" style="margin:14px 0">
        <label class="check"><input type="radio" name="mode" value="learned" ${data.mode === 'learned' ? 'checked' : ''}> Learned codes (replay)</label>
        <label class="check"><input type="radio" name="mode" value="virtual" ${data.mode === 'virtual' ? 'checked' : ''}> Virtual remote (synthesised)</label>
      </div>
      <div id="mode-learned" ${data.mode !== 'learned' ? 'hidden' : ''}>
        <p class="muted small" style="margin-top:0">Bind action names to learned codes. open / stop / close become the big buttons; any other name shows as a small one.</p>
        <div id="extra-actions">${actionRows.map(([k, v]) => extraRow(k, v, codeOptions)).join('')}</div>
        <button type="button" class="ghost small" data-action="add-extra" style="margin-top:8px">+ action</button>
      </div>
      <div id="mode-virtual" ${data.mode !== 'virtual' ? 'hidden' : ''}>
        <div class="form-grid">
          <label class="field">Protocol<select name="protocol">${state.protocols.map((p) => `<option value="${p.name}" ${p.name === data.protocol ? 'selected' : ''}>${esc(p.label)}</option>`).join('')}</select></label>
          <label class="field">Repeat<input name="repeat" type="number" min="1" max="50" value="${data.repeat ?? ''}" placeholder="protocol default"></label>
        </div>
        <div id="param-fields" class="form-grid" style="margin-top:12px"></div>
        <p class="muted small" id="proto-desc"></p>
      </div>
      <div class="modal-foot"><button type="button" class="ghost" data-action="close-modal">Cancel</button><button class="primary" type="submit">${isEdit ? 'Save' : 'Create'}</button></div>
    </form>`);

  const form = $('#remote-form', modal);
  const renderParams = () => {
    const p = protocol(form.protocol.value);
    $('#proto-desc', modal).textContent = p?.description || '';
    $('#param-fields', modal).innerHTML = (p?.params || []).map((x) => {
      const cur = data.protocol === p.name ? data.params[x.key] : undefined;
      const value = cur !== undefined ? paramDisplay(x, cur) : x.randomize ? '' : (x.default ?? x.minimum);
      return `<label class="field">${esc(x.label)}
        <div class="row" style="flex-wrap:nowrap"><input class="mono" name="param_${x.key}" value="${esc(value)}" required ${x.kind === 'int' ? `type="number" min="${x.minimum}" max="${x.maximum}"` : 'pattern="[0-9a-fA-F]{1,6}"'} style="flex:1">
        ${x.randomize ? `<button type="button" class="ghost icon" data-action="random-param" data-key="${x.key}" title="Randomise">⟳</button>` : ''}</div>
        <span class="muted" style="text-transform:none;letter-spacing:0">${esc(x.help)}</span></label>`;
    }).join('');
    $$('[name^="param_"]', modal).forEach((inp) => { if (!inp.value) randomise(inp.name.slice(6)); });
  };
  const randomise = async (key) => {
    const params = await api.post(`/api/protocols/${form.protocol.value}/random`);
    const inp = form[`param_${key}`];
    const spec = protocol(form.protocol.value).params.find((x) => x.key === key);
    inp.value = paramDisplay(spec, params[key]);
  };
  form.addEventListener('change', (e) => {
    if (e.target.name === 'mode') {
      $('#mode-learned', modal).hidden = e.target.value !== 'learned';
      $('#mode-virtual', modal).hidden = e.target.value !== 'virtual';
    }
    if (e.target.name === 'protocol') renderParams();
  });
  modal.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    if (el.dataset.action === 'close-modal') closeModal();
    if (el.dataset.action === 'random-param') guarded(() => randomise(el.dataset.key));
    if (el.dataset.action === 'add-extra') $('#extra-actions', modal).insertAdjacentHTML('beforeend', extraRow('', '', codeOptions));
    if (el.dataset.action === 'remove-extra') el.closest('.extra-row').remove();
  });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    guarded(async () => {
      const mode = form.mode.value;
      const body = { name: form.name.value, device_id: form.device_id.value, mode, codes: {}, params: {} };
      if (mode === 'learned') {
        $$('.extra-row', modal).forEach((row) => {
          const k = $('[name=extra_key]', row).value.trim();
          if (k) body.codes[k] = $('[name=extra_code]', row).value;
        });
      } else {
        body.protocol = form.protocol.value;
        body.repeat = form.repeat.value ? Number(form.repeat.value) : null;
        for (const x of protocol(body.protocol).params) body.params[x.key] = form[`param_${x.key}`].value;
      }
      if (isEdit) await api.put(`/api/remotes/${c.id}`, body); else await api.post('/api/remotes', body);
      closeModal();
      toast(isEdit ? 'Remote saved' : 'Remote created');
      await refresh();
    });
  });
  renderParams();
}
function extraRow(key, codeId, codeOptions) {
  return `<div class="row extra-row" style="margin-bottom:8px"><input name="extra_key" placeholder="action, e.g. open" value="${esc(key)}" style="width:200px">
    <select name="extra_code">${codeOptions(codeId)}</select><button type="button" class="ghost icon" data-action="remove-extra">✕</button></div>`;
}

function pairingGuide(c) {
  const p = protocol(c.protocol);
  const primary = 'program' in p.commands ? 'program' : Object.keys(p.commands)[0];
  const modal = openModal(`<h3>Pair “${esc(c.name)}” — ${esc(p.label)}</h3>
    <p class="small muted">Virtual remote: ${paramsSummary(p, c.params)}. The receiver must learn this identity once; afterwards the buttons work like a real remote.</p>
    <ol class="steps">${p.pairing_steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
    <div class="row" style="margin-top:16px">${Object.entries(p.commands).map(([k, label]) => `<button data-action="pair-cmd" data-cmd="${esc(k)}" class="${k === primary ? 'primary' : ''}">${esc(label)}</button>`).join('')}</div>
    <div class="modal-foot"><button class="ghost" data-action="close-modal">Close</button></div>`);
  modal.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    if (el.dataset.action === 'close-modal') closeModal();
    if (el.dataset.action === 'pair-cmd') sendRemote(c.id, el.dataset.cmd);
  });
}

/* ---------- actions ---------- */
async function sendRemote(id, cmd) {
  await guarded(async () => {
    const res = await api.post(`/api/remotes/${id}/commands/${cmd}`);
    toast(`Sent ${cmd}`);
    const remote = state.remotes.find((c) => c.id === id);
    if (res.params && remote && JSON.stringify(remote.params) !== JSON.stringify(res.params)) {
      remote.params = res.params;  // rolling-code remotes: show the advanced counter
      if (state.tab === 'remotes') render();
    }
  });
}

const actions = {
  'new-remote': () => remoteForm(null),
  'edit-remote': (el) => remoteForm(state.remotes.find((c) => c.id === el.dataset.id)),
  'del-remote': (el) => confirm('Delete this remote?') && guarded(async () => { await api.del(`/api/remotes/${el.dataset.id}`); await refresh(); }),
  'remote-cmd': (el) => sendRemote(el.dataset.id, el.dataset.cmd),
  pair: (el) => pairingGuide(state.remotes.find((c) => c.id === el.dataset.id)),

  'learn-start': () => guarded(async () => {
    state.learnSweep = $('#learn-sweep').checked;
    state.learnFreq = $('#learn-freq').value;
    state.compare = null;
    const body = { device_id: state.activeDevice, sweep: state.learnSweep, frequency: state.learnSweep ? null : Number(state.learnFreq) || null };
    state.learn = await api.post('/api/learn/start', body);
    render();
    pollLearn();
  }),
  'learn-cancel': () => guarded(async () => { await api.post('/api/learn/cancel'); }),
  'test-capture': () => guarded(async () => {
    await api.post('/api/send/raw', { device_id: state.activeDevice, pulses: packetPulses(), repeat: 0 });
    toast('Sent capture to the radio');
  }),
  'save-capture': (el) => guarded(async () => {
    const code = await api.post('/api/codes', {
      name: $('#code-name').value || suggestName(), notes: $('#code-notes').value,
      packet_hex: state.learn.packet_hex, device_id: state.activeDevice, frequency: state.learn.frequency,
    });
    state.captureCount += 1;
    state.sessionCaptures.push(code.id);
    toast(`Saved “${code.name}”`);
    await loadAll();
    if (el.dataset.again) { state.learn = null; actions['learn-start'](); } else render();
  }),
  'compare-session': () => guarded(async () => {
    state.compare = await api.post('/api/codes/compare', { code_ids: state.sessionCaptures });
    render();
  }),

  'toggle-code': (el) => { el.checked ? state.selectedCodes.add(el.dataset.id) : state.selectedCodes.delete(el.dataset.id); render(); },
  'compare-codes': () => guarded(async () => {
    state.compare = await api.post('/api/codes/compare', { code_ids: [...state.selectedCodes] });
    render();
  }),
  'send-code': (el) => guarded(async () => {
    if (!state.activeDevice) throw new Error('Pick an active radio first');
    await api.post(`/api/codes/${el.dataset.id}/send`, { device_id: state.activeDevice });
    toast(`Sent “${codeName(el.dataset.id)}”`);
  }),
  'analyze-code': (el) => guarded(async () => {
    const id = el.dataset.id;
    if (state.openAnalyses[id]) delete state.openAnalyses[id];
    else state.openAnalyses[id] = await api.get(`/api/codes/${id}/analysis`);
    render();
  }),
  'del-code': (el) => confirm('Delete this code?') && guarded(async () => {
    await api.del(`/api/codes/${el.dataset.id}`);
    state.selectedCodes.delete(el.dataset.id);
    state.sessionCaptures = state.sessionCaptures.filter((x) => x !== el.dataset.id);
    await refresh();
  }),

  discover: (el) => guarded(async () => {
    el.disabled = true; el.textContent = 'Scanning…';
    try {
      const found = await api.post('/api/devices/discover', { timeout: 5 });
      toast(found.length ? `Found ${found.length} device(s)` : 'Nothing answered the broadcast');
      await refresh();
    } finally { el.disabled = false; el.textContent = 'Discover on LAN'; }
  }),
  'add-device': () => guarded(async () => {
    const host = $('#add-host').value.trim();
    if (!host) throw new Error('Enter the IP address of the Broadlink');
    const d = await api.post('/api/devices', { host });
    state.activeDevice = d.id;
    localStorage.setItem('rfw.device', d.id);
    toast(`Added ${d.model}`);
    await refresh();
  }),
  'add-manual': () => guarded(async () => {
    const host = $('#manual-host').value.trim(), mac = $('#manual-mac').value.trim();
    if (!host || !mac) throw new Error('Enter IP and MAC');
    const d = await api.post('/api/devices/manual', { host, mac, devtype: Number($('#manual-type').value) });
    state.activeDevice = d.id;
    localStorage.setItem('rfw.device', d.id);
    toast(`Added and authenticated ${d.model}`);
    await refresh();
  }),
  'use-device': (el) => { state.activeDevice = el.dataset.id; localStorage.setItem('rfw.device', el.dataset.id); renderHeader(); render(); },
  'rename-device': (el) => {
    const name = prompt('Name', deviceName(el.dataset.id));
    if (name) guarded(async () => { await api.patch(`/api/devices/${el.dataset.id}`, { name }); await refresh(); });
  },
  'del-device': (el) => confirm('Forget this radio?') && guarded(async () => { await api.del(`/api/devices/${el.dataset.id}`); await refresh(); }),
};

function packetPulses() {
  const a = state.learn?.analysis;
  if (!a) throw new Error('Nothing captured');
  return packetToPulses(state.learn.packet_hex);
}
function packetToPulses(hexStr) {
  const bytes = Uint8Array.from(hexStr.match(/../g).map((h) => parseInt(h, 16)));
  const len = bytes[2] | (bytes[3] << 8);
  const out = [];
  for (let i = 4; i < Math.min(4 + len, bytes.length);) {
    let t = bytes[i++];
    if (t === 0) { if (i + 1 >= bytes.length) break; t = (bytes[i] << 8) | bytes[i + 1]; i += 2; }
    out.push(Math.round(t * 8192 / 269));
  }
  return out;
}

async function pollLearn() {
  clearTimeout(state.learnTimer);
  const s = await guarded(() => api.get('/api/learn/status'));
  if (!s) return;
  const changed = JSON.stringify({ ...s, analysis: undefined }) !== JSON.stringify({ ...(state.learn || {}), analysis: undefined });
  state.learn = s;
  if (changed && state.tab === 'learn') render();
  if (['starting', 'sweeping', 'waiting'].includes(s.phase)) state.learnTimer = setTimeout(pollLearn, 600);
}

/* ---------- render loop ---------- */
function render() {
  $('#main').innerHTML = views[state.tab]();
  $$('#rail button').forEach((b) => b.classList.toggle('active', b.dataset.tab === state.tab));
  if (state.tab === 'learn' && state.learn?.analysis) mountScope('learn', packetToPulses(state.learn.packet_hex));
  if (state.tab === 'codes') {
    for (const [id] of Object.entries(state.openAnalyses)) {
      const code = state.codes.find((c) => c.id === id);
      if (code) mountScope(id, packetToPulses(code.packet_hex));
    }
  }
}
async function refresh() { await loadAll(); render(); }

$('#main').addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (el && el.type !== 'checkbox' && actions[el.dataset.action]) actions[el.dataset.action](el, e);
});
$('#main').addEventListener('change', (e) => {
  const el = e.target.closest('[data-action]');
  if (el && el.type === 'checkbox' && actions[el.dataset.action]) actions[el.dataset.action](el, e);
  if (e.target.id === 'learn-sweep') { state.learnSweep = e.target.checked; $('#learn-freq').disabled = state.learnSweep; }
});
$('#rail').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-tab]');
  if (!b) return;
  state.tab = b.dataset.tab;
  if (state.tab !== 'codes') state.compare = state.tab === 'learn' ? state.compare : null;
  render();
});
$('#active-device').addEventListener('change', (e) => {
  state.activeDevice = e.target.value; localStorage.setItem('rfw.device', e.target.value); renderHeader(); render();
});

guarded(async () => {
  await refresh();
  const s = await api.get('/api/learn/status');
  if (s.phase !== 'idle') { state.learn = s; if (['starting', 'sweeping', 'waiting'].includes(s.phase)) pollLearn(); }
});
