/* Research Daily — local workspace
   No framework. State lives in one object; render functions read from it. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const S = {
  run: null,          // full run payload
  papers: [],         // shortlist
  filtered: [],
  current: null,      // selected paper
  docMode: 'paper',
  sideTab: 'sources',
  refsCache: {},
  saveTimer: null,
  dirty: false,
};

const N_STEPS = [0, 50, 100, 500, 1000, 10000, 100000];
const N_LABELS = ['any', '50+', '100+', '500+', '1k+', '10k+', '100k+'];

const CHECKS = [
  'I opened the actual paper, not just this abstract',
  'I know what the control group received',
  'I know the follow-up duration',
  'I read the limitations section',
  'Effect size is meaningful, not just statistically significant',
  'Funding source and conflicts checked',
  'Every claim in my script maps to a line in the PDF',
  'If preprint: I say "not peer reviewed" on camera',
];

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ── boot ─────────────────────────────────────────────── */

init();

async function init() {
  const theme = localStorage.getItem('theme');
  if (theme) document.documentElement.dataset.theme = theme;

  wireTopbar();
  wireFilters();
  wireDocTabs();
  wireSideTabs();
  wirePaperTools();
  wireScriptTools();
  wireVerdicts();
  wireModals();
  wireResize();
  wireKeys();

  await loadRuns();
}

/* ── data ─────────────────────────────────────────────── */

async function api(path, opts) {
  const r = await fetch(path, opts);
  return r.json();
}

async function loadRuns(preferred) {
  const { runs = [] } = await api('/api/runs');
  const sel = $('#runSelect');
  sel.innerHTML = runs.map(r =>
    `<option value="${r.date}">${r.date} · ${r.count}</option>`).join('');

  if (!runs.length) {
    $('#list').innerHTML = `<div class="empty">No runs yet.<br>Hit <b>Fetch new</b>.</div>`;
    return;
  }
  const target = preferred && runs.some(r => r.date === preferred) ? preferred : runs[0].date;
  sel.value = target;
  await loadRun(target);
}

async function loadRun(date) {
  const data = await api('/api/run?date=' + encodeURIComponent(date));
  if (data.error) return;
  S.run = data;
  S.papers = data.shortlist || [];
  const st = data.stats || {};
  $('#runStats').textContent =
    `${st.total ?? '?'} scanned → ${S.papers.length} shortlisted`;
  applyFilters();
  if (S.filtered.length) select(S.filtered[0]);
}

/* ── filters + list ───────────────────────────────────── */

function wireFilters() {
  ['#search', '#fVerdict', '#fSort', '#fSource', '#fDesign',
   '#fNoPreprint', '#fTier', '#fMinN'].forEach(sel => {
    $(sel).addEventListener('input', applyFilters);
  });

  $('#fMinN').addEventListener('input', e => {
    $('#fMinNVal').textContent = N_LABELS[+e.target.value];
  });

  $('#btnReset').addEventListener('click', () => {
    $('#search').value = '';
    ['#fVerdict', '#fSource', '#fDesign'].forEach(s => $(s).value = '');
    $('#fSort').value = 'score';
    $('#fNoPreprint').checked = $('#fTier').checked = false;
    $('#fMinN').value = 0;
    $('#fMinNVal').textContent = 'any';
    applyFilters();
  });
}

function tierOf(p) {
  const m = (p.reasons || []).join(' ').match(/journal tier \+(\d)/);
  return m ? +m[1] : 0;
}

function applyFilters() {
  const q = $('#search').value.trim().toLowerCase();
  const verdict = $('#fVerdict').value;
  const source = $('#fSource').value;
  const design = $('#fDesign').value;
  const noPre = $('#fNoPreprint').checked;
  const tierOnly = $('#fTier').checked;
  const minN = N_STEPS[+$('#fMinN').value];
  const sort = $('#fSort').value;

  S.filtered = S.papers.filter(p => {
    if (q) {
      const hay = `${p.title} ${p.journal} ${p.abstract} ${p.authors || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (verdict === 'unset' && p.verdict) return false;
    if (verdict && verdict !== 'unset' && p.verdict !== verdict) return false;
    if (source && p.source !== source) return false;
    if (design && !`${p.title} ${p.abstract}`.toLowerCase().includes(design)) return false;
    if (noPre && p.is_preprint) return false;
    if (tierOnly && tierOf(p) < 4) return false;
    if (minN && (p.sample_size || 0) < minN) return false;
    return true;
  });

  const cmp = {
    score: (a, b) => b.score - a.score,
    date:  (a, b) => String(b.date).localeCompare(String(a.date)),
    n:     (a, b) => (b.sample_size || 0) - (a.sample_size || 0),
    cites: (a, b) => (b.citations || 0) - (a.citations || 0),
    title: (a, b) => a.title.localeCompare(b.title),
  }[sort];
  S.filtered.sort(cmp);

  renderList();
}

function renderList() {
  $('#listCount').textContent = `${S.filtered.length} of ${S.papers.length}`;
  const list = $('#list');

  if (!S.filtered.length) {
    list.innerHTML = `<div class="empty">Nothing matches these filters.</div>`;
    return;
  }

  list.innerHTML = S.filtered.map(p => {
    const tier = tierOf(p);
    const pills = [
      p.is_preprint ? `<span class="pill warn">preprint</span>` : '',
      tier >= 4 ? `<span class="pill tier">tier ${tier}</span>` : '',
      p.sample_size ? `<span class="pill">n≈${p.sample_size.toLocaleString()}</span>` : '',
      p.verdict ? `<span class="pill ${p.verdict}">${p.verdict}</span>` : '',
      p.has_notes ? `<span class="pill">notes</span>` : '',
    ].join('');

    return `
      <div class="card ${p.uid === S.current?.uid ? 'sel' : ''} ${p.verdict === 'kill' ? 'killed' : ''}"
           data-uid="${esc(p.uid)}">
        <div class="card-top">
          <span class="score">${p.score}</span>
          <h4>${esc(p.title)}</h4>
        </div>
        <div class="card-sub">
          <span>${esc(p.journal || p.source)}</span>
          <span>·</span>
          <span>${esc(p.date)}</span>
        </div>
        <div class="card-sub">${pills}</div>
      </div>`;
  }).join('');

  $$('.card', list).forEach(el => el.addEventListener('click', () => {
    const p = S.papers.find(x => x.uid === el.dataset.uid);
    if (p) select(p);
  }));
}

/* ── selection + paper render ─────────────────────────── */

function select(p) {
  flushSave();
  S.current = p;
  renderList();
  renderPaper();
  renderScript();
  renderSide();
  $('#docMeta').textContent = `${p.journal || p.source} · ${p.date}`;
  updateVerdictButtons();

  // Full text XML exists only for the PMC open-access subset.
  const ft = $('#btnFulltext');
  ft.disabled = !p.pmcid;
  ft.textContent = p.pmcid ? 'Load full text' : 'Abstract only (paywalled)';

  $('.docscroll').scrollTop = 0;
}

function paperBaseHTML(p, sections) {
  const flags = [];
  if (p.is_preprint) flags.push('PREPRINT — not peer reviewed. Say so on camera.');
  if (p.sample_size && p.sample_size < 100)
    flags.push(`Small sample (n≈${p.sample_size.toLocaleString()}) — be careful generalising.`);

  const meta = [
    p.journal, p.date,
    p.sample_size ? `n≈${p.sample_size.toLocaleString()}` : '',
    p.citations ? `${p.citations} citations` : '',
    p.doi ? `doi:${p.doi}` : '',
  ].filter(Boolean).map(x => `<span>${esc(x)}</span>`).join('');

  const body = sections
    ? sections.map(s => `
        ${s.heading ? `<h2>${esc(s.heading)}</h2>` : ''}
        ${s.paragraphs.map(t => `<p>${esc(t)}</p>`).join('')}`).join('')
    : `<h2>Abstract</h2>${
        splitAbstract(p.abstract).map(([h, t]) =>
          `${h ? `<h3>${esc(h)}</h3>` : ''}<p>${esc(t)}</p>`).join('')}`;

  return `
    <div class="paperhead">
      <h1>${esc(p.title)}</h1>
      ${p.authors ? `<p class="byline">${esc(p.authors)}</p>` : ''}
      <div class="meta">${meta}</div>
    </div>
    ${flags.map(f => `<div class="flagbox">⚠ ${esc(f)}</div>`).join('')}
    ${body}`;
}

/* Structured abstracts arrive as one blob: "BackgroundFoo...MethodsBar..." */
function splitAbstract(text) {
  // Longest first — 'Background and aims' must win over 'Background'.
  const heads = ['Background and aims', 'Main outcomes and measures',
    'Materials and methods', 'Trial registration', 'Study design',
    'Background', 'Importance', 'Objectives', 'Objective', 'Purpose',
    'Rationale', 'Introduction', 'Context', 'Aims', 'Aim',
    'Methods', 'Method', 'Design', 'Setting', 'Participants', 'Patients',
    'Intervention', 'Interventions', 'Measurements', 'Outcomes',
    'Results', 'Findings', 'Discussion', 'Limitations',
    'Conclusions', 'Conclusion', 'Interpretation', 'Implications',
    'Registration', 'Funding', 'Significance'];
  const re = new RegExp(`(${heads.join('|')})(?=[A-Z(])`, 'g');

  const out = [];
  let last = 0, lastHead = '', m;
  while ((m = re.exec(text)) !== null) {
    const chunk = text.slice(last, m.index).trim();
    if (chunk) out.push([lastHead, chunk]);
    lastHead = m[1];
    last = m.index + m[0].length;
  }
  const tail = text.slice(last).trim();
  if (tail) out.push([lastHead, tail]);
  return out.length ? out : [['', text]];
}

function renderPaper(sections) {
  const p = S.current;
  const doc = $('#paperDoc');
  if (!p) { doc.innerHTML = `<div class="empty">Pick a paper.</div>`; return; }

  if (!sections && p.saved?.reader_html) doc.innerHTML = p.saved.reader_html;
  else doc.innerHTML = paperBaseHTML(p, sections);

  renderHighlights();
}

/* ── highlighting ─────────────────────────────────────── */

function wirePaperTools() {
  $$('.swatch').forEach(b =>
    b.addEventListener('mousedown', e => e.preventDefault()));
  $$('.swatch').forEach(b =>
    b.addEventListener('click', () => highlight(b.dataset.hl)));

  $('#fontBig').addEventListener('change', e => {
    $('#paperDoc').classList.toggle('big', e.target.checked);
    $('#scriptDoc').classList.toggle('big', e.target.checked);
  });

  $('#btnFulltext').addEventListener('click', loadFullText);
}

async function loadFullText() {
  const p = S.current;
  if (!p?.pmcid) return;

  const hasWork = $$('#paperDoc mark').length > 0;
  if (hasWork && !confirm('Loading full text replaces this view and discards its highlights. Continue?'))
    return;

  const btn = $('#btnFulltext');
  btn.disabled = true;
  btn.textContent = 'Loading…';

  const { sections } = await api(`/api/fulltext?pmcid=${encodeURIComponent(p.pmcid)}`);

  btn.disabled = false;
  if (!sections) {
    btn.textContent = 'No open full text';
    setTimeout(() => (btn.textContent = 'Load full text'), 2200);
    return;
  }
  btn.textContent = 'Full text ✓';
  renderPaper(sections);
  saveSoon();
}

function textNodesInRange(range) {
  const root = range.commonAncestorContainer;
  const scope = root.nodeType === 3 ? root.parentNode : root;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) {
    if (range.intersectsNode(n) && n.nodeValue.trim()) nodes.push(n);
  }
  return nodes;
}

function highlight(color) {
  const sel = window.getSelection();
  if (!sel.rangeCount || sel.isCollapsed) return;
  const range = sel.getRangeAt(0);
  const doc = $('#paperDoc');
  if (!doc.contains(range.commonAncestorContainer)) return;

  if (color === 'none') {
    // Unwrap every mark the selection touches.
    $$('mark', doc).forEach(m => {
      if (range.intersectsNode(m)) {
        const parent = m.parentNode;
        while (m.firstChild) parent.insertBefore(m.firstChild, m);
        parent.removeChild(m);
      }
    });
  } else {
    // Wrap each text node separately — surroundContents throws across elements.
    textNodesInRange(range).forEach(node => {
      const start = node === range.startContainer ? range.startOffset : 0;
      const end = node === range.endContainer ? range.endOffset : node.nodeValue.length;
      if (end <= start) return;

      let target = node;
      if (end < target.nodeValue.length) target.splitText(end);
      if (start > 0) target = target.splitText(start);

      if (target.parentNode.tagName === 'MARK') {
        target.parentNode.dataset.c = color;
        return;
      }
      const m = document.createElement('mark');
      m.dataset.c = color;
      target.parentNode.insertBefore(m, target);
      m.appendChild(target);
    });
  }

  sel.removeAllRanges();
  mergeMarks();
  doc.normalize();
  renderHighlights();
  saveSoon();
}

function mergeMarks() {
  $$('#paperDoc mark').forEach(m => {
    if (!m.textContent.trim()) { m.remove(); return; }
    const next = m.nextSibling;
    if (next && next.nodeType === 1 && next.tagName === 'MARK' &&
        next.dataset.c === m.dataset.c) {
      while (next.firstChild) m.appendChild(next.firstChild);
      next.remove();
    }
  });
}

function renderHighlights() {
  const marks = $$('#paperDoc mark');
  marks.forEach((m, i) => (m.dataset.i = i));
  $('#hlCount').textContent = marks.length || '';
  if (S.sideTab === 'highlights') renderSide();
}

function collectHighlights() {
  return $$('#paperDoc mark').map((m, i) => ({
    i, c: m.dataset.c || 'y', text: m.textContent.trim(),
  })).filter(h => h.text);
}

/* ── script editor ────────────────────────────────────── */

function wireDocTabs() {
  $$('.tab[data-doc]').forEach(t => t.addEventListener('click', () => {
    S.docMode = t.dataset.doc;
    $$('.tab[data-doc]').forEach(x => x.classList.toggle('active', x === t));
    const isPaper = S.docMode === 'paper';
    $('#paperDoc').classList.toggle('hidden', !isPaper);
    $('#scriptDoc').classList.toggle('hidden', isPaper);
    $('#paperTools').classList.toggle('hidden', !isPaper);
    $('#scriptTools').classList.toggle('hidden', isPaper);
    if (!isPaper) $('#scriptDoc').focus();
  }));
}

function renderScript() {
  const p = S.current;
  const doc = $('#scriptDoc');
  doc.innerHTML = p?.saved?.script ||
    `<h2>الفكرة الأساسية</h2><p><br></p>
     <h2>ماذا وجدت الدراسة</h2><p><br></p>
     <h2>كيف اختبروها</h2><p><br></p>
     <h2>ما الذي لا تعنيه هذه النتيجة</h2><p><br></p>`;
  doc.dir = p?.saved?.rtl === false ? 'ltr' : 'rtl';
  updateWordCount();
}

function wireScriptTools() {
  $$('[data-cmd]').forEach(b => {
    b.addEventListener('mousedown', e => e.preventDefault());
    b.addEventListener('click', () => {
      document.execCommand(b.dataset.cmd, false, null);
      saveSoon();
    });
  });

  $$('[data-block]').forEach(b => {
    b.addEventListener('mousedown', e => e.preventDefault());
    b.addEventListener('click', () => {
      document.execCommand('formatBlock', false, b.dataset.block);
      saveSoon();
    });
  });

  $('#btnRtl').addEventListener('click', () => {
    const doc = $('#scriptDoc');
    doc.dir = doc.dir === 'rtl' ? 'ltr' : 'rtl';
    saveSoon();
  });

  $('#btnPullQuotes').addEventListener('click', () => {
    const hls = collectHighlights();
    if (!hls.length) return alert('No highlights yet. Highlight lines in the Paper tab first.');
    const html = hls.map(h => `<blockquote>${esc(h.text)}</blockquote>`).join('');
    $('#scriptDoc').insertAdjacentHTML('beforeend', html);
    saveSoon();
    updateWordCount();
  });

  $('#scriptDoc').addEventListener('input', () => { saveSoon(); updateWordCount(); });
  $('#paperDoc').addEventListener('input', saveSoon);
}

function updateWordCount() {
  const words = ($('#scriptDoc').innerText.trim().match(/\S+/g) || []).length;
  const secs = Math.round(words / 2.3);   // ~140 spoken words/min
  $('#wordCount').textContent =
    `${words} words · ~${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}

/* ── side panel ───────────────────────────────────────── */

function wireSideTabs() {
  $$('.tab[data-side]').forEach(t => t.addEventListener('click', () => {
    S.sideTab = t.dataset.side;
    $$('.tab[data-side]').forEach(x => x.classList.toggle('active', x === t));
    $$('.sideview').forEach(v => v.classList.toggle('hidden', v.dataset.view !== S.sideTab));
    renderSide();
  }));
}

function renderSide() {
  const p = S.current;
  if (!p) return;
  const view = $(`.sideview[data-view="${S.sideTab}"]`);
  if (!view) return;

  if (S.sideTab === 'sources') return renderSources(view, p);
  if (S.sideTab === 'highlights') return renderHighlightList(view);
  if (S.sideTab === 'scoring') return renderScoring(view, p);
  if (S.sideTab === 'checks') return renderChecks(view, p);
}

function renderSources(view, p) {
  const links = [
    p.doi && [`https://doi.org/${p.doi}`, 'Publisher (DOI)', p.doi],
    p.url && [p.url, p.source === 'arxiv' ? 'arXiv' : 'Europe PMC', ''],
    p.pmid && [`https://pubmed.ncbi.nlm.nih.gov/${p.pmid}/`, 'PubMed', p.pmid],
    p.pmcid && [`https://www.ncbi.nlm.nih.gov/pmc/articles/${p.pmcid}/`, 'PMC full text', p.pmcid],
    [`https://scholar.google.com/scholar?q=${encodeURIComponent(p.title)}`, 'Google Scholar', ''],
  ].filter(Boolean);

  view.innerHTML = `
    <div class="srcblock">
      <h5>Record</h5>
      <dl class="kv">
        <dt>Journal</dt><dd>${esc(p.journal || '—')}</dd>
        <dt>Published</dt><dd>${esc(p.date)}</dd>
        <dt>Authors</dt><dd>${esc(p.authors || '—')}</dd>
        <dt>Sample</dt><dd>${p.sample_size ? 'n≈' + p.sample_size.toLocaleString() : '—'}</dd>
        <dt>Citations</dt><dd>${p.citations ?? 0}</dd>
        <dt>Access</dt><dd>${p.open_access ? 'open access' : 'paywalled / unknown'}</dd>
      </dl>
    </div>

    <div class="srcblock">
      <h5>Go to source</h5>
      <div class="linklist">
        ${links.map(([href, label, note]) =>
          `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}<span>${esc(note)}</span></a>`).join('')}
      </div>
    </div>

    <div class="srcblock">
      <h5>References cited</h5>
      <div id="refbox">
        ${p.epmc_src && p.epmc_id
          ? `<button class="btn ghost small" id="btnRefs">Load references</button>`
          : `<span class="muted small">Not available for this source.</span>`}
      </div>
    </div>`;

  const btn = $('#btnRefs', view);
  if (btn) btn.addEventListener('click', () => loadRefs(p));

  const cached = S.refsCache[p.uid];
  if (cached) paintRefs(cached);
}

async function loadRefs(p) {
  const box = $('#refbox');
  box.innerHTML = `<span class="muted small">Loading…</span>`;
  const { references = [] } = await api(
    `/api/references?src=${encodeURIComponent(p.epmc_src)}&id=${encodeURIComponent(p.epmc_id)}`);
  S.refsCache[p.uid] = references;
  paintRefs(references);
}

function paintRefs(refs) {
  const box = $('#refbox');
  if (!box) return;
  if (!refs.length) {
    box.innerHTML = `<span class="muted small">No references indexed.</span>`;
    return;
  }
  box.innerHTML = refs.map(r => `
    <div class="ref">
      <b>${esc(r.title || 'untitled')}</b><br>
      ${esc([r.authors, r.journal, r.year].filter(Boolean).join(' · '))}
      ${r.doi ? ` — <a href="https://doi.org/${esc(r.doi)}" target="_blank" rel="noopener">doi</a>` : ''}
    </div>`).join('');
}

function renderHighlightList(view) {
  const hls = collectHighlights();
  if (!hls.length) {
    view.innerHTML = `<div class="muted small">No highlights yet.<br><br>
      Select text in the Paper tab and pick a colour:<br>
      yellow = key finding · green = method · purple = limitation · red = caution.</div>`;
    return;
  }
  view.innerHTML = hls.map(h =>
    `<div class="hlitem" data-c="${esc(h.c)}" data-i="${h.i}">${esc(h.text)}</div>`).join('');

  $$('.hlitem', view).forEach(el => el.addEventListener('click', () => {
    if (S.docMode !== 'paper') $('.tab[data-doc="paper"]').click();
    const m = $(`#paperDoc mark[data-i="${el.dataset.i}"]`);
    if (!m) return;
    m.scrollIntoView({ behavior: 'smooth', block: 'center' });
    m.classList.add('focus');
    setTimeout(() => m.classList.remove('focus'), 1200);
  }));
}

function renderScoring(view, p) {
  view.innerHTML = `
    <div class="srcblock">
      <h5>Score ${p.score}</h5>
      ${(p.reasons || []).map(r => `<div class="reasonline">${esc(r)}</div>`).join('')
        || `<span class="muted small">No reasons recorded.</span>`}
    </div>
    <div class="srcblock">
      <h5>Read this as</h5>
      <p class="muted small">A ranking of <em>abstracts</em>, not of truth. High score means
      the abstract advertises a strong design in a strong venue. It says nothing about
      whether the paper holds up.</p>
    </div>`;
}

function renderChecks(view, p) {
  const saved = p.saved?.notes?.checks || {};
  view.innerHTML = `
    <div class="srcblock">
      <h5>Before you shoot</h5>
      ${CHECKS.map((c, i) => `
        <label class="check ${saved[i] ? 'done' : ''}">
          <input type="checkbox" data-check="${i}" ${saved[i] ? 'checked' : ''}>
          <span>${esc(c)}</span>
        </label>`).join('')}
    </div>`;

  $$('[data-check]', view).forEach(cb => cb.addEventListener('change', () => {
    const notes = S.current.saved?.notes || {};
    notes.checks = notes.checks || {};
    notes.checks[cb.dataset.check] = cb.checked;
    S.current.saved = { ...(S.current.saved || {}), notes };
    cb.closest('.check').classList.toggle('done', cb.checked);
    saveSoon();
  }));
}

/* ── verdicts + saving ────────────────────────────────── */

function wireVerdicts() {
  $$('.btn.verdict').forEach(b => b.addEventListener('click', () => {
    if (!S.current) return;
    const v = b.dataset.verdict;
    S.current.verdict = S.current.verdict === v ? '' : v;
    updateVerdictButtons();
    renderList();
    saveSoon(true);
  }));
}

function updateVerdictButtons() {
  $$('.btn.verdict').forEach(b =>
    b.classList.toggle('on', S.current?.verdict === b.dataset.verdict));
}

function saveSoon(now) {
  S.dirty = true;
  $('#saveState').textContent = 'saving…';
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(flushSave, now ? 0 : 700);
}

async function flushSave() {
  clearTimeout(S.saveTimer);
  const p = S.current;
  // Without this, merely opening a paper would persist the blank script
  // template and flag every visited paper as having notes.
  if (!p || !S.dirty) return;
  S.dirty = false;

  const payload = {
    uid: p.uid,
    title: p.title,
    verdict: p.verdict || '',
    script: $('#scriptDoc').innerHTML,
    rtl: $('#scriptDoc').dir !== 'ltr',
    reader_html: $('#paperDoc').innerHTML,
    highlights: collectHighlights(),
    notes: p.saved?.notes || {},
  };
  // We only reach here on a real edit, so reaching here means work exists.
  p.saved = { ...(p.saved || {}), ...payload };
  p.has_notes = true;

  try {
    await api('/api/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    $('#saveState').textContent = 'saved';
  } catch {
    $('#saveState').textContent = 'save failed';
  }
}

/* ── topbar, modals ───────────────────────────────────── */

function wireTopbar() {
  $('#runSelect').addEventListener('change', e => loadRun(e.target.value));

  $('#btnTheme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('theme', next);
  });

  $('#btnFetch').addEventListener('click', () => $('#fetchModal').classList.remove('hidden'));

  $('#btnConfig').addEventListener('click', async () => {
    const { config } = await api('/api/config');
    $('#configText').value = JSON.stringify(config, null, 2);
    $('#configMsg').textContent = '';
    $('#configModal').classList.remove('hidden');
  });
}

function wireModals() {
  $$('[data-close]').forEach(b => b.addEventListener('click', () =>
    b.closest('.modal').classList.add('hidden')));

  $$('.modal').forEach(m => m.addEventListener('mousedown', e => {
    if (e.target === m) m.classList.add('hidden');
  }));

  $('#btnRunFetch').addEventListener('click', runFetch);

  $('#btnSaveConfig').addEventListener('click', async () => {
    let parsed;
    try {
      parsed = JSON.parse($('#configText').value);
    } catch (e) {
      $('#configMsg').textContent = 'Invalid JSON: ' + e.message;
      return;
    }
    const r = await api('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: parsed }),
    });
    $('#configMsg').textContent = r.ok ? 'Saved.' : (r.error || 'Failed.');
  });
}

async function runFetch() {
  const btn = $('#btnRunFetch');
  btn.disabled = true;
  $('#fetchLog').textContent = 'starting…\n';

  await api('/api/fetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      days: +$('#mDays').value,
      top: +$('#mTop').value,
      topic: $('#mTopic').value,
    }),
  });

  const poll = setInterval(async () => {
    const { active, log } = await api('/api/fetch/status');
    const done = log.find(l => l.startsWith('__done__'));
    $('#fetchLog').textContent = log.filter(l => !l.startsWith('__done__')).join('\n');
    $('#fetchLog').scrollTop = $('#fetchLog').scrollHeight;

    if (!active && done) {
      clearInterval(poll);
      btn.disabled = false;
      $('#fetchLog').textContent += '\n\n' + done.replace('__done__', 'finished');
      await loadRuns();
    }
  }, 1200);
}

/* ── resize + keys ────────────────────────────────────── */

function wireResize() {
  const layout = $('#layout');
  let active = null, startX = 0, startW = 0;

  $$('.gutter').forEach(g => g.addEventListener('mousedown', e => {
    active = g.dataset.resize;
    startX = e.clientX;
    startW = (active === 'list' ? $('#paneList') : $('#paneSide')).offsetWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  }));

  window.addEventListener('mousemove', e => {
    if (!active) return;
    const delta = active === 'list' ? e.clientX - startX : startX - e.clientX;
    const w = Math.max(220, Math.min(620, startW + delta));
    const cols = getComputedStyle(layout).gridTemplateColumns.split(' ');
    if (active === 'list') cols[0] = w + 'px'; else cols[4] = w + 'px';
    layout.style.gridTemplateColumns = cols.join(' ');
  });

  window.addEventListener('mouseup', () => {
    active = null;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
}

function wireKeys() {
  document.addEventListener('keydown', e => {
    const editing = e.target.isContentEditable ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName);

    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      flushSave();
      return;
    }
    if (e.key === 'Escape') {
      $$('.modal').forEach(m => m.classList.add('hidden'));
      return;
    }
    if (editing) return;

    const i = S.filtered.findIndex(p => p.uid === S.current?.uid);
    if (e.key === 'j' && i < S.filtered.length - 1) select(S.filtered[i + 1]);
    if (e.key === 'k' && i > 0) select(S.filtered[i - 1]);
    if (['1', '2', '3'].includes(e.key)) {
      $(`.btn.verdict[data-verdict="${{ 1: 'shoot', 2: 'hold', 3: 'kill' }[e.key]}"]`).click();
    }
  });
}

window.addEventListener('beforeunload', flushSave);
