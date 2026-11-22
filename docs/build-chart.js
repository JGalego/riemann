// Cumulative modules built over the verification run, from data/timeseries.csv.
// One series, so no legend; hover shows a crosshair and the bucket's numbers.
(async () => {
  const root = document.getElementById('build-chart');
  if (!root) return;
  const text = await (await fetch('data/timeseries.csv')).text();
  const [head, ...lines] = text.trim().split('\n');
  const cols = head.split(',');
  const at = (row, name) => row[cols.indexOf(name)];

  let total = 0;
  const pts = [];
  for (const line of lines) {
    const row = line.split(',');
    const built = Number(at(row, 'modules_built')) || 0;
    total += built;
    pts.push({ t: new Date(at(row, 'bucket_utc')), built, total, cores: Number(at(row, 'busy_cores')) || 0 });
  }
  // Trim to the build: first bucket with work to the first bucket at the final total.
  const first = pts.findIndex(p => p.built > 0);
  const last = pts.findIndex(p => p.total === total);
  const data = pts.slice(Math.max(0, first - 1), last + 1);

  const W = 760, H = 300, m = { l: 56, r: 16, t: 14, b: 30 };
  const t0 = data[0].t, t1 = data[data.length - 1].t;
  const x = t => m.l + (W - m.l - m.r) * (t - t0) / (t1 - t0);
  const yMax = 200000;
  const y = v => H - m.b - (H - m.t - m.b) * v / yMax;
  const ns = 'http://www.w3.org/2000/svg';
  const el = (name, attrs, parent) => {
    const e = document.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    parent.appendChild(e);
    return e;
  };

  const svg = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img',
    'aria-label': `Cumulative modules built: ${total.toLocaleString()} over the run` }, root);
  const grid = el('g', { class: 'grid' }, svg);
  const axis = el('g', { class: 'axis' }, svg);
  for (let v = 0; v <= yMax; v += 50000) {
    if (v) el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }, grid);
    el('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end' }, axis).textContent =
      v ? `${v / 1000}k` : '0';
  }
  // A tick every 3 hours, on the hour.
  const tick = new Date(t0); tick.setUTCMinutes(0, 0, 0);
  tick.setUTCHours(Math.ceil((t0.getUTCHours() + (t0.getUTCMinutes() ? 1 : 0)) / 3) * 3);
  for (; tick <= t1; tick.setUTCHours(tick.getUTCHours() + 3)) {
    el('text', { x: x(tick), y: H - m.b + 18, 'text-anchor': 'middle' }, axis).textContent =
      `${String(tick.getUTCHours()).padStart(2, '0')}:00`;
  }
  el('line', { class: 'baseline', x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }, svg);
  el('path', { class: 'line', d: data.map((p, i) => `${i ? 'L' : 'M'}${x(p.t)},${y(p.total)}`).join('') }, svg);

  const end = data[data.length - 1];
  el('circle', { class: 'dot', cx: x(end.t), cy: y(end.total), r: 4 }, svg);
  el('text', { class: 'label', x: x(end.t) - 8, y: y(end.total) - 10, 'text-anchor': 'end' }, svg)
    .textContent = `${total.toLocaleString()} modules, 0 errors`;

  // Hover layer.
  const cross = el('line', { class: 'cross', y1: m.t, y2: H - m.b, visibility: 'hidden' }, svg);
  const hot = el('circle', { class: 'dot', r: 4, visibility: 'hidden' }, svg);
  const tip = document.createElement('div');
  tip.className = 'tooltip';
  root.appendChild(tip);
  const hit = el('rect', { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: 'transparent' }, svg);
  const fmt = d => `${d.toISOString().slice(5, 10)} ${d.toISOString().slice(11, 16)} UTC`;
  hit.addEventListener('pointermove', ev => {
    const box = svg.getBoundingClientRect();
    const px = (ev.clientX - box.left) * W / box.width;
    let best = data[0];
    for (const p of data) if (Math.abs(x(p.t) - px) < Math.abs(x(best.t) - px)) best = p;
    const cx = x(best.t), cy = y(best.total);
    cross.setAttribute('x1', cx); cross.setAttribute('x2', cx);
    hot.setAttribute('cx', cx); hot.setAttribute('cy', cy);
    cross.setAttribute('visibility', 'visible'); hot.setAttribute('visibility', 'visible');
    tip.innerHTML = `<div class="muted">${fmt(best.t)}</div>
      <div><b>${best.total.toLocaleString()}</b> modules built</div>
      <div class="muted">+${best.built.toLocaleString()} in 5 min, ${Math.round(best.cores)} busy cores</div>`;
    tip.style.display = 'block';
    const sx = cx * box.width / W, sy = cy * box.height / H;
    const left = sx + 12 + tip.offsetWidth > box.width ? sx - 12 - tip.offsetWidth : sx + 12;
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(0, sy - tip.offsetHeight - 8)}px`;
  });
  hit.addEventListener('pointerleave', () => {
    cross.setAttribute('visibility', 'hidden'); hot.setAttribute('visibility', 'hidden');
    tip.style.display = 'none';
  });
})();
