// Platform-independent sync rules. No file paths or SDK cache UUIDs are durable IDs.
// Use only for reads: a timed-out write could still complete and overwrite newer state.
export async function readWithTimeout(label, action, milliseconds=15000) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(action), new Promise((_,reject) => {
      timer = setTimeout(() => reject(new Error(label+' did not respond within '+Math.round(milliseconds/1000)+' seconds. Reopen the plugin and retry; your saved connection and queued changes have not been cleared.')),milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

export function parsePairingFile(raw) {
  if (typeof raw !== 'string' || raw.length > 65536) throw new Error('Invalid pairing file. Download it from planner settings.');
  let value;
  try { value = JSON.parse(raw); } catch { throw new Error('This is not a planner pairing JSON file.'); }
  if (!value || value.schema !== 1 || typeof value.origin !== 'string' ||
      typeof value.bookId !== 'string' || typeof value.token !== 'string' || typeof value.caPem !== 'string') {
    throw new Error('Pairing file is incomplete. Download a new file from planner settings.');
  }
  const config = {origin:value.origin.trim().replace(/\/$/,''), bookId:value.bookId.trim(), token:value.token.trim(), caPem:value.caPem.trim()};
  if (!/^https:\/\/(?:[a-z0-9.-]+|\[[0-9a-f:]+\])(?::\d{1,5})?$/i.test(config.origin) ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(config.bookId) ||
      !/^[a-z0-9_-]{43}$/i.test(config.token)) throw new Error('Invalid planner connection details.');
  if (!/^-----BEGIN CERTIFICATE-----\s+[a-z0-9+/=\s]+-----END CERTIFICATE-----$/i.test(config.caPem)) {
    throw new Error('Pairing needs the public CA certificate, never a private key.');
  }
  return config;
}

export function metadata(element) {
  try { const value = JSON.parse(element.userData || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch { return {}; }
}

export function pageMap(pages, bookId) {
  const map = new Map();
  for (const [index, elements] of pages.entries()) {
    const anchors = elements.map(metadata).filter(m => m.owner === 'supernote-planner' && m.kind === 'page-anchor' && m.bookId === bookId);
    if (anchors.length !== 1 || map.has(anchors[0].pageId)) throw new Error('Missing or duplicate page anchor. Reconcile the notebook before syncing.');
    map.set(anchors[0].pageId, index);
  }
  return map;
}

export function bounds(points, size) {
  if (!points.length) return null;
  let left=Infinity,top=Infinity,right=-Infinity,bottom=-Infinity;
  for (const p of points) { left=Math.min(left,p.x); top=Math.min(top,p.y); right=Math.max(right,p.x); bottom=Math.max(bottom,p.y); }
  return [left/size.width,top/size.height,right/size.width,bottom/size.height];
}

export function inside(box, rect) {
  return box && box[0] >= rect[0] && box[1] >= rect[1] && box[2] <= rect[0] + rect[2] && box[3] <= rect[1] + rect[3];
}

export function crossedOut(points, rect, size) {
  const box = bounds(points, size);
  if (!box || points.length < 2) return false;
  // Deliberate, nearly horizontal line across most of exactly one rendered row.
  // An X or ordinary letters aren't silently treated as a cancellation.
  const first = points[0], last = points[points.length-1];
  const mid = rect[1] + rect[3] / 2;
  return box[0] >= rect[0] - .01 && box[2] <= rect[0] + rect[2] + .01 &&
    Math.abs((first.y + last.y) / (2 * size.height) - mid) < rect[3] * .35 &&
    Math.abs(last.x - first.x) / size.width > rect[2] * .7 && box[3] - box[1] < rect[3] * .55;
}

export function checked(points, rect, size) {
  const box = bounds(points,size);
  if (!box || points.length < 3) return false;
  const checkbox = [rect[0],rect[1],.035,rect[3]];
  if (!inside(box,checkbox)) return false;
  const bottom = points.reduce((best,p,n) => p.y > points[best].y ? n : best,0);
  return bottom > 0 && bottom < points.length-1 && points[bottom].y - points[0].y > size.height*.002 &&
    points[bottom].y - points[points.length-1].y > size.height*.004 && points[points.length-1].x > points[bottom].x;
}

export function newStrokeGroups(strokes, region, size, seen) {
  const groups = new Map();
  for (const stroke of strokes) {
    if (seen[stroke.fingerprint]) continue;
    const box = bounds(stroke.points, size);
    if (!inside(box, region.capture)) continue;
    const row = Math.min(2, Math.floor(((box[1] + box[3]) / 2 - region.capture[1]) / (region.capture[3] / 3)));
    if (!groups.has(row)) groups.set(row, []);
    groups.get(row).push(stroke);
  }
  return [...groups.values()];
}

export function displayRows(page, items, {previous = [], ink = []} = {}) {
  const rows = [];
  rows.overflow = 0;
  for (const region of page.regions || []) {
    const entries = items.filter(i => i.day === region.date && !i.deleted).sort((a, b) => (a.start_time || '').localeCompare(b.start_time || '') || a.title.localeCompare(b.title));
    // Keep the original row pitch: existing annotations/cancellation marks must
    // not attach to a different event when the display area expands.
    const pitch = region.digital[3] / 2;
    const next = (page.regions || []).filter(r => Number.isFinite(r.y) && r.y > region.digital[1]).map(r => r.y - .012);
    const ownBottom = Number.isFinite(region.y) && Number.isFinite(region.h)
      ? region.y + region.h - .008 : region.digital[1] + region.digital[3];
    const bottom = Math.min(ownBottom, .89, ...next);
    const capacity = Math.max(0, Math.min(32, Math.floor((bottom - region.digital[1] + 1e-9) / pitch)));
    const slots = Array(capacity).fill(null);
    const blocked = slots.map((_,n) => ink.some(box => box && box[2] > region.digital[0] && box[0] < region.digital[0]+region.digital[2] && box[3] > region.digital[1]+n*pitch && box[1] < region.digital[1]+(n+1)*pitch));
    for (const prior of previous) {
      const item = entries.find(i => i.id === prior.itemId);
      if (!item || !prior.rect || Math.abs(prior.rect[0]-region.digital[0]) > .001) continue;
      const n = Math.round((prior.rect[1]-region.digital[1])/pitch);
      if (n >= 0 && n < capacity && !slots[n] && !slots.some(s => s?.id === item.id) &&
          Math.abs(prior.rect[1] - (region.digital[1]+n*pitch)) < .001) slots[n] = item;
    }
    for (const item of entries.filter(i => !slots.some(s => s?.id === i.id))) {
      const n = slots.findIndex((s,index) => s === null && !blocked[index]);
      if (n >= 0) slots[n] = item;
    }
    const visible = slots.filter(Boolean).length;
    const overflow = entries.length-visible;
    rows.overflow += overflow;
    const last = slots.reduce((found,item,n) => item ? n : found,-1);
    for (const [n, item] of slots.entries()) {
      if (!item) continue;
      const rect = [...region.digital];
      rect[1] += n * pitch;
      rect[3] = pitch;
      const prefix = item.kind === 'task' ? (item.completed ? '[x]' : '[ ]') : (item.start_time ? item.start_time.slice(0, 5) : 'All day');
      const title = Array.from(item.title).slice(0,32).join('') + (Array.from(item.title).length > 32 ? '…' : '');
      rows.push({item, rect, text: `${prefix} ${title}${n === last && overflow ? ` (+${overflow} in app)` : ''}`});
    }
  }
  return rows;
}

export function acknowledge(state, response) {
  const ids = new Set(response.operations.map(op => op.id));
  const bindings = {...state.bindings};
  for (const receipt of response.operations) {
    if (bindings[receipt.id] && receipt.item_id) bindings[receipt.id] = {...bindings[receipt.id],itemId:receipt.item_id,version:receipt.version};
  }
  for (const binding of Object.values(bindings)) {
    const item = response.items.find(i => i.id === binding.itemId);
    if (item) { binding.version = item.version; binding.kind = item.kind; }
  }
  return {...state, outbox: state.outbox.filter(op => !ids.has(op.id)), serverRevision: response.revision, items: response.items,
    bindings, reviews: response.operations.filter(op => op.status === 'review')};
}
