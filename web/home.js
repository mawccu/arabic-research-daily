/* Research Daily — home screen.
   Generates runs and lists them as cards that open the workspace. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const H = { runs: [], menuFor: null, polling: null };

/* Static build (GitHub Pages) has no Python backend — demo-data.js supplies
   one baked run and generating is disabled. */
const DEMO = typeof window !== 'undefined' && !!window.DEMO_DATA;
const WORKSPACE = DEMO ? 'workspace.html' : '/workspace';

init();

async function init() {
  const theme = localStorage.getItem('theme');
  if (theme) document.documentElement.dataset.theme = theme;

  $('#btnTheme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
  });

  $('#btnGenerate').addEventListener('click', generate);
  $('#btnLatest').addEventListener('click', () => {
    if (H.runs[0]) location.href = `${WORKSPACE}?date=${encodeURIComponent(H.runs[0].date)}`;
  });

  $('#gDays').addEventListener('input', e => {
    const d = Math.max(1, +e.target.value || 1);
    $('#genHint').textContent = `scan the last ${d} day${d === 1 ? '' : 's'}`;
  });

  wireConfig();
  wireMenu();

  if (DEMO) enterDemoMode();
  await loadRuns();
}

async function api(path, opts) {
  if (DEMO) return demoApi(path);
  const r = await fetch(path, opts);
  return r.json();
}

function demoApi(path) {
  const D = window.DEMO_DATA;
  if (path.startsWith('/api/runs')) {
    const saved = JSON.parse(localStorage.getItem('demoState') || '{}');
    return { runs: [{
      date: D.date,
      count: D.shortlist.length,
      stats: D.stats,
      top: D.shortlist[0]?.title || '',
      decided: D.shortlist.filter(p => saved[p.uid]?.verdict).length,
    }] };
  }
  if (path.startsWith('/api/config')) return { config: D.config, ok: true };
  return {};
}

function enterDemoMode() {
  const gen = $('#btnGenerate');
  gen.disabled = true;
  gen.title = 'Generating needs the local app';
  $('#genOpts').classList.add('hidden');

  const bar = document.createElement('div');
  bar.className = 'demobar';
  bar.innerHTML = `<b>Static demo.</b> Real papers and real scoring, but generating new
    runs needs the local app. <a href="https://github.com/mawccu/arabic-research-daily"
    target="_blank" rel="noopener">Run it properly →</a>`;
  $('.topbar').insertAdjacentElement('afterend', bar);
}

/* ── runs ─────────────────────────────────────────────── */

async function loadRuns() {
  const { runs = [] } = await api('/api/runs');
  H.runs = runs;

  $('#runsCount').textContent = runs.length
    ? `${runs.length} run${runs.length === 1 ? '' : 's'}` : '';
  $('#latestHint').textContent = runs.length ? runs[0].date : 'no runs yet';
  $('#btnLatest').disabled = !runs.length;

  const grid = $('#runGrid');
  if (!runs.length) {
    grid.innerHTML = `<div class="emptycard">
      <p>No runs yet.</p>
      <p class="muted small">Hit <b>Generate a research</b> — it takes about two minutes.</p>
    </div>`;
    return;
  }

  grid.innerHTML = runs.map(r => {
    const s = r.stats || {};
    return `
      <article class="runcard" data-date="${esc(r.date)}">
        <button class="runcard-more" data-more="${esc(r.date)}" title="Options">⋯</button>
        <div class="runcard-ico">✦</div>
        <h3>${esc(r.label || r.date)}</h3>
        <p class="muted small">${r.count} shortlisted${
          s.total ? ` · ${Number(s.total).toLocaleString()} scanned` : ''}</p>
        ${r.top ? `<p class="runcard-top">${esc(r.top)}</p>` : ''}
        <div class="runcard-foot">
          <span class="muted small">${esc(r.date)}</span>
          ${r.decided ? `<span class="pill">${r.decided} decided</span>` : ''}
        </div>
      </article>`;
  }).join('');

  $$('.runcard', grid).forEach(card => {
    card.addEventListener('click', e => {
      if (e.target.closest('[data-more]')) return;
      location.href = `${WORKSPACE}?date=${encodeURIComponent(card.dataset.date)}`;
    });
  });

  $$('[data-more]', grid).forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    openMenu(b, b.dataset.more);
  }));
}

/* ── generate ─────────────────────────────────────────── */

async function generate() {
  const btn = $('#btnGenerate');
  const log = $('#genLog');
  btn.disabled = true;
  btn.classList.add('busy');
  log.classList.remove('hidden');
  log.textContent = 'starting…\n';

  await api('/api/fetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      days: +$('#gDays').value,
      top: +$('#gTop').value,
      topic: $('#gTopic').value,
    }),
  });

  clearInterval(H.polling);
  H.polling = setInterval(async () => {
    const { active, log: lines = [] } = await api('/api/fetch/status');
    const done = lines.find(l => l.startsWith('__done__'));
    log.textContent = lines.filter(l => !l.startsWith('__done__')).join('\n');
    log.scrollTop = log.scrollHeight;

    if (!active && done) {
      clearInterval(H.polling);
      btn.disabled = false;
      btn.classList.remove('busy');
      log.textContent += '\n\n' + done.replace('__done__', 'finished');
      await loadRuns();
    }
  }, 1200);
}

/* ── card menu ────────────────────────────────────────── */

function wireMenu() {
  document.addEventListener('click', () => $('#cardMenu').classList.add('hidden'));

  $$('#cardMenu button').forEach(b => b.addEventListener('click', async e => {
    e.stopPropagation();
    const date = H.menuFor;
    $('#cardMenu').classList.add('hidden');
    if (!date) return;

    switch (b.dataset.act) {
      case 'open':
        location.href = `${WORKSPACE}?date=${encodeURIComponent(date)}`;
        break;
      case 'rename': {
        const run = H.runs.find(r => r.date === date);
        const label = prompt('Title for this run', run?.label || date);
        if (label === null) return;
        await api('/api/run/label', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date, label }),
        });
        await loadRuns();
        break;
      }
      case 'export':
        window.open(`/out/${encodeURIComponent(date)}.json`, '_blank');
        break;
      case 'markdown':
        window.open(`/out/${encodeURIComponent(date)}.md`, '_blank');
        break;
      case 'delete':
        if (!confirm(`Delete the ${date} run?\n\nThe shortlist files are removed. Your highlights and scripts are kept.`))
          return;
        await api('/api/run/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date }),
        });
        await loadRuns();
        break;
    }
  }));
}

function openMenu(anchor, date) {
  H.menuFor = date;
  const menu = $('#cardMenu');
  const r = anchor.getBoundingClientRect();
  menu.classList.remove('hidden');
  menu.style.top = `${r.bottom + window.scrollY + 6}px`;
  menu.style.left = `${Math.min(r.left, window.innerWidth - menu.offsetWidth - 12)}px`;

  if (DEMO) $$('#cardMenu button').forEach(b => {
    b.disabled = ['rename', 'delete'].includes(b.dataset.act);
  });
}

/* ── config ───────────────────────────────────────────── */

function wireConfig() {
  $('#btnConfig').addEventListener('click', async () => {
    const { config } = await api('/api/config');
    $('#configText').value = JSON.stringify(config, null, 2);
    $('#configMsg').textContent = DEMO ? 'Read-only on the demo page.' : '';
    $('#btnSaveConfig').disabled = DEMO;
    $('#configModal').classList.remove('hidden');
  });

  $$('[data-close]').forEach(b => b.addEventListener('click', () =>
    b.closest('.modal').classList.add('hidden')));

  $('#configModal').addEventListener('mousedown', e => {
    if (e.target.id === 'configModal') e.target.classList.add('hidden');
  });

  $('#btnSaveConfig').addEventListener('click', async () => {
    let parsed;
    try {
      parsed = JSON.parse($('#configText').value);
    } catch (err) {
      $('#configMsg').textContent = 'Invalid JSON: ' + err.message;
      return;
    }
    const r = await api('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: parsed }),
    });
    $('#configMsg').textContent = r.ok ? 'Saved.' : (r.error || 'Failed.');
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $$('.modal').forEach(m => m.classList.add('hidden'));
  });
}
