import {NativeModules} from 'react-native';
import {PluginManager, NativePluginManager, PluginNoteAPI, PluginFileAPI, PluginCommAPI, Element, TextBox} from 'sn-plugin-lib';
import {metadata, pageMap, bounds, crossedOut, checked, newStrokeGroups, displayRows, acknowledge, parsePairingFile, readWithTimeout} from './core.mjs';

const storage = NativeModules.PlannerStorage;
export async function setKeepAwake(enabled) {
  // Plugins can run in a headless host: window control is optional, never a sync prerequisite.
  try { return storage?.setKeepAwake ? (await storage.setKeepAwake(enabled)) === true : false; }
  catch { return false; }
}
const check = response => {
  if (!response?.success) throw new Error(response?.error?.message || 'The device API could not complete this action.');
  return response.result;
};
let busy = false;
let stopRequested = false;
export function requestStop() { if (busy) stopRequested = true; }
function checkpoint() {
  if (stopRequested) throw new Error('Paused safely. Reopen the notebook and run Sync Planner to resume. Saved changes and unfinished preview uploads are retained.');
}
function guardedReport(report) { return value => { checkpoint(); report(value); }; }
const OWNER = 'supernote-planner';
const activeElements = new Set();

function track(elements) {
  for (const element of elements) {
    if (typeof element?.uuid !== 'string' || !element.uuid) throw new Error('The device returned an element without a cache identity.');
    activeElements.add(element.uuid);
  }
  return elements;
}
function release(elements) {
  for (const element of elements) {
    if (!activeElements.has(element.uuid)) continue;
    PluginCommAPI.recycleElement(element.uuid);
    activeElements.delete(element.uuid);
  }
}
function releaseAll() {
  for (const uuid of [...activeElements]) {
    try { PluginCommAPI.recycleElement(uuid); } catch {}
    activeElements.delete(uuid);
  }
}
async function elementsAt(page,path) {
  const elements = check(await PluginFileAPI.getElements(page,path));
  if (!Array.isArray(elements)) throw new Error('The device returned an invalid page element list.');
  return track(elements);
}
function changed(response) {
  if (check(response) !== true) throw new Error('The device did not confirm the notebook change. Sync again before proceeding.');
}

async function permissions(report = () => {}) {
  // Let the host prompt using its supported permission workflow. A denied permission
  // causes the associated file/network operation to fail without changing the queue.
  for (const name of ['FILE:READ','FILE:WRITE','INTERNET']) {
    report('Checking permission: '+name);
    const permission = 'plugin.permission.'+name;
    const existing = await readWithTimeout('Checking '+name+' permission',() => NativePluginManager.hasPermission(permission));
    if (existing !== 1 && existing !== 2) report('Allow '+name+' in the tablet permission dialog');
    const granted = existing === 1 || existing === 2 ? existing : await PluginManager.requestPermission(permission);
    if (granted !== 1 && granted !== 2) throw new Error('Allow '+name+' in the planner plugin permissions before continuing.');
  }
}

async function directory() {
  if (!storage) throw new Error('The PlannerStorage native bridge is missing. Build the full plugin package.');
  const response = await readWithTimeout('Reading plugin storage location',() => PluginManager.getPluginDirPath());
  return typeof response === 'string' ? response : check(response);
}
export async function load() {
  const dir = await directory();
  return JSON.parse(await readWithTimeout('Reading saved planner connection',() => storage.load(dir)));
}
export async function save(state) {
  await storage.save(await directory(), JSON.stringify(state));
}
export async function configure(config, report = () => {}) {
  config = {...config,origin:config.origin.trim(),bookId:config.bookId.trim(),token:config.token.trim()};
  if (!/^https:\/\/[^/]+$/.test(config.origin)) throw new Error('Use the HTTPS origin of your existing app, without a trailing slash.');
  report('Reading the existing connection and queued changes');
  const old = await load();
  if (old.bookId && old.bookId !== config.bookId && old.outbox?.length) throw new Error('Sync pending changes before switching planners.');
  const base = old.bookId && old.bookId !== config.bookId ? {} : old;
  if (['origin','bookId','token','caPem'].every(key => old[key] === config[key])) return;
  report('Saving encrypted planner connection');
  await save({...base, ...config, outbox:base.outbox || [], seen:base.seen || {}, bindings:base.bindings || {}, appliedRevision:base.appliedRevision || 0});
}
export async function importPairing(report = () => {}) {
  if (busy) throw new Error('Wait for the current planner action to finish.');
  busy = true;
  try {
    if (!storage?.readPairingFromDocument) throw new Error('Install planner plugin 0.1.4 or newer for direct pairing import.');
    await permissions(report);
    report('Reading Document/supernote-planner-pairing.json');
    const raw = await readWithTimeout('Reading the pairing file from Document',() => storage.readPairingFromDocument());
    report('Validating the pairing file');
    const config = parsePairingFile(raw);
    await configure(config,report);
    return config;
  } finally { busy = false; }
}
export async function recoverPairing(report = () => {}) {
  if (busy) throw new Error('Wait for the current planner action to finish.');
  busy = true;
  stopRequested = false;
  report = guardedReport(report);
  try {
    if (!storage?.recover) throw new Error('Install the full planner plugin 0.1.6 package and restart the tablet for recovery.');
    // Never turn recovery into an ordinary connection reset.
    report('Checking the saved-state authentication failure');
    try { await load(); throw new Error('Existing state is readable. Use Sync Planner instead of recovery.'); }
    catch (e) { if (!/AEADBadTagException/.test(e?.message || '')) throw e; }
    await permissions(report);
    report('Reading Document/supernote-planner-pairing.json for recovery');
    const config = parsePairingFile(await readWithTimeout('Reading the recovery pairing file',() => storage.readPairingFromDocument()));
    report('Checking the recovery connection with the server');
    const response = await api(config,'/planner/api/v1/sync/',{schema:1,book_id:config.bookId,operations:[],acknowledged_revision:0});
    report('Checking the open notebook for recovery');
    const path = check(await PluginCommAPI.getCurrentFilePath());
    if (typeof path !== 'string' || !/\/Note\/Planner\/.*\.note$/.test(path)) throw new Error('Open the original planner notebook inside Note/Planner before recovery.');
    const count = check(await PluginFileAPI.getNoteTotalPageNum(path));
    if (!response.manifest?.pages?.length || count !== response.manifest.pages.length) throw new Error('The pairing file and open notebook have different page counts. No recovery was written.');
    const pages = [];
    for (let n=0;n<count;n++) {
      report(`Verifying recovery page ${n+1} of ${count}`);
      const elements = await elementsAt(n,path);
      pages.push(elements.map(e => ({userData:e.userData})));
      release(elements);
    }
    const map = pageMap(pages,config.bookId);
    if (response.manifest.pages.some(p => map.get(p.id) === undefined) || map.size !== count) throw new Error('Notebook page identities do not match this pairing file. No recovery was written.');
    report('Saving a new connection while preserving unreadable state');
    await storage.recover(await directory(),JSON.stringify({...config,notePath:path,manifest:response.manifest,items:response.items,
      recovered:true,outbox:[],seen:{},bindings:{},appliedRevision:0,
      pendingPreviews:response.manifest.pages.filter(p => p.kind === 'week').map(p => p.id)}));
    return config;
  } finally { releaseAll(); busy = false; }
}
async function api(state, endpoint, body, pngPath = '') {
  const result = await storage.request(await directory(), state.origin, endpoint, state.token,
    JSON.stringify(body), state.caPem || '', pngPath, state.previewPage || '', state.serverRevision || 0);
  return JSON.parse(result);
}

async function textElement(text, rect, page, size, userData) {
  if (!Number.isInteger(size.width) || !Number.isInteger(size.height) || size.width < 1 || size.height < 1) throw new Error('The device returned an invalid page size.');
  const element = track([check(await PluginCommAPI.createElement(Element.TYPE_TEXT))])[0];
  element.pageNum = page;
  element.layerNum = 0;
  element.userData = JSON.stringify(userData);
  const left = Math.max(0,Math.round(rect[0]*size.width)), top = Math.max(0,Math.round(rect[1]*size.height));
  const right = Math.min(size.width,Math.round((rect[0]+rect[2])*size.width));
  const bottom = Math.min(size.height,Math.round((rect[1]+rect[3])*size.height));
  if (right <= left || bottom <= top) throw new Error('Planner text does not fit this page.');
  element.textBox = Object.assign(new TextBox(), {fontSize:Math.max(1,Math.round(size.height*.014)),textContentFull:text,
    textRect:{left,top,right,bottom},textEditable:0});
  return element;
}

async function current(report = () => {}) {
  report('Saving the open notebook');
  changed(await PluginNoteAPI.saveCurrentNote());
  report('Reading the open notebook location');
  const path = check(await PluginCommAPI.getCurrentFilePath());
  if (!path.endsWith('.note') || !/\/Note\/Planner\//.test(path)) throw new Error('Open the notebook inside Note/Planner first.');
  return path;
}

export async function inspectNotebook(report = () => {}) {
  if (busy) throw new Error('Wait for the current planner action to finish.');
  busy = true;
  try {
    await permissions(report);
    report('Reading saved connection');
    const state = await load();
    if (!state.bookId || !state.token) throw new Error('Import the pairing file first.');
    report('Reading the open notebook location');
    const path = check(await PluginCommAPI.getCurrentFilePath());
    if (typeof path !== 'string' || !path.endsWith('.note')) throw new Error('Open the .note planner notebook, not the PDF.');
    report('Counting notebook pages');
    const count = check(await PluginFileAPI.getNoteTotalPageNum(path));
    report('Checking planner connection');
    const response = await api(state,'/planner/api/v1/sync/',{schema:1,book_id:state.bookId,operations:[],acknowledged_revision:state.appliedRevision || 0});
    const correctFolder = /\/Note\/Planner\//.test(path);
    return `Notebook: ${path}\nPages: ${count}; planner expects ${response.manifest.pages.length}.\n${correctFolder ? 'Folder: correct.' : 'Move this notebook into Note/Planner before initializing.'}\n${state.manifest ? 'Initialization is saved.' : 'Initialization has not completed.'}\n${state.outbox?.length || 0} changes waiting to send.`;
  } finally { busy = false; }
}

// One-time initialization requires the untouched complete PDF page order.
export async function initialize(report = () => {}) {
  if (busy) throw new Error('Planner sync is already running.');
  busy = true;
  stopRequested = false;
  report = guardedReport(report);
  try {
    await permissions(report);
    report('Reading saved connection');
    const state = await load();
    if (!state.bookId || !state.token) throw new Error('Save your connection details first.');
    report('Connecting to the planner server');
    const response = await api(state, '/planner/api/v1/sync/', {schema:1,book_id:state.bookId,operations:[],acknowledged_revision:0});
    const path = await current(report);
    report('Checking notebook page count');
    const count = check(await PluginFileAPI.getNoteTotalPageNum(path));
    if (count !== response.manifest.pages.length) throw new Error(`Notebook has ${count} pages; this planner needs ${response.manifest.pages.length}. Import all PDF pages in order. Your handwriting has not been changed.`);
    for (const page of response.manifest.pages) {
      checkpoint();
      if (page.index % 5 === 0 || page.index === count-1) report(`Initializing page ${page.index+1} of ${count}`);
      const elements = await elementsAt(page.index,path);
      const anchors = elements.filter(e => metadata(e).owner === OWNER && metadata(e).kind === 'page-anchor');
      if (anchors.length) {
        if (anchors.length !== 1 || metadata(anchors[0]).bookId !== state.bookId || metadata(anchors[0]).pageId !== page.id) throw new Error('Unexpected page anchor; initialization stopped.');
        release(elements);
        continue;
      }
      const size = check(await PluginFileAPI.getPageSize(path,page.index));
      const element = await textElement(page.id,[.06,.98,.8,.018],page.index,size,{owner:OWNER,kind:'page-anchor',bookId:state.bookId,pageId:page.id});
      changed(await PluginFileAPI.insertElements(path,page.index,[element]));
      release([element,...elements]);
    }
    state.notePath = path;
    state.manifest = response.manifest;
    state.items = response.items;
    report('Saving notebook initialization');
    await save(state);
    report('Initialization saved; refreshing notebook');
    changed(await PluginCommAPI.reloadFile());
    return 'Notebook initialized. Run Sync Planner to validate the round trip.';
  } finally { releaseAll(); busy = false; }
}

export async function sync(report = () => {}) {
  if (busy) throw new Error('Planner sync is already running.');
  busy = true;
  stopRequested = false;
  report = guardedReport(report);
  try {
    await permissions(report);
    report('Reading saved planner state');
    let state = await load();
    state.bindings = state.bindings || {};
    if (!state.manifest) throw new Error('Initialize the imported planner notebook first.');
    const path = await current(report);
    if (path !== state.notePath) throw new Error('This is a different notebook. Reconnect it explicitly before syncing.');
    const count = check(await PluginFileAPI.getNoteTotalPageNum(path));
    if (count !== state.manifest.pages.length) throw new Error('Notebook page count changed. Reconcile pages before syncing.');
    const pages = [];
    // Keep only lightweight metadata between pages; SDK point caches are reclaimed.
    for (let n=0;n<count;n++) {
      checkpoint();
      if (n % 10 === 0) report(`Checking page identities: ${n+1} of ${count}`);
      const elements = await elementsAt(n,path);
      pages.push(elements.map(e => ({userData:e.userData})));
      release(elements);
    }
    const map = pageMap(pages,state.bookId);
    const previewPages = new Set(state.pendingPreviews || []);
    const inkByPage = new Map();
    for (const page of state.manifest.pages) {
      const index = map.get(page.id);
      if (index === undefined) throw new Error('A planner page is missing.');
      if (page.kind !== 'week') continue;
      report(`Reading handwriting on page ${index+1} of ${count}`);
      const size = check(await PluginFileAPI.getPageSize(path,index));
      const elements = await elementsAt(index,path);
      const strokes = [];
      let pagePointCount = 0;
      for (const element of elements.filter(e => e.type === Element.TYPE_STROKE && !metadata(e).owner)) {
        const count = await element.stroke.points.size();
        if (!Number.isInteger(count) || count < 0) throw new Error('The device could not read a handwriting stroke. The notebook is unchanged; reopen it and retry.');
        // Bound memory use; oversized content is left untouched for explicit review.
        if (count > 50000) throw new Error('A stroke is too large for safe capture. Review this page manually.');
        pagePointCount += count;
        if (pagePointCount > 200000) throw new Error('This page contains too much handwriting for safe sync. Review this page manually.');
        if (count === 0) continue;
        const points = await element.stroke.points.getRange(0,count);
        if (points.length !== count || points.some(p => !Number.isFinite(p.x) || !Number.isFinite(p.y))) throw new Error('Incomplete handwriting points received. No calendar changes have been sent.');
        const fingerprint = await storage.hash(JSON.stringify({page:page.id,pen:element.stroke.penType,points}));
        strokes.push({element,points,fingerprint});
      }
      for (const binding of Object.values(state.bindings)) {
        if (binding.pageId === page.id && binding.fingerprints && !strokes.some(s => binding.fingerprints.includes(s.fingerprint))) binding.retired = true;
      }
      // Detect cancellation on original handwriting before considering new capture ink.
      for (const stroke of strokes.filter(s => !state.seen[s.fingerprint])) {
        const matches = Object.entries(state.bindings).filter(([,binding]) => binding.pageId === page.id && !binding.cancelled && !binding.retired &&
          (crossedOut(stroke.points,binding.rect,size) || (binding.kind === 'task' && checked(stroke.points,binding.rect,size))));
        if (matches.length !== 1) continue;
        const [sourceId,binding] = matches[0];
        const operation = {id:await storage.randomId(),action:crossedOut(stroke.points,binding.rect,size)?'cancel':'complete',version:binding.version || 1,
          ...(binding.itemId ? {item_id:binding.itemId} : {item_operation:sourceId})};
        state.outbox.push(operation);
        state.seen[stroke.fingerprint] = operation.id;
        binding.cancelled = true;
        previewPages.add(page.id);
        state.pendingPreviews = [...previewPages];
        await save(state);
      }
      for (const region of page.regions) {
        checkpoint();
        for (const group of newStrokeGroups(strokes,region,size,state.seen || {})) {
          report(`Recognizing handwriting on page ${index+1} of ${count}`);
          const text = check(await PluginCommAPI.recognizeElements(group.map(s => s.element),size));
          const box = bounds(group.reduce((all,s) => all.concat(s.points),[]),size);
          const rect = [box[0],box[1],Math.max(.035,box[2]-box[0]),Math.max(.014,box[3]-box[1])];
          const uncertain = state.recovered === true || group.some(s => crossedOut(s.points,rect,size));
          const operation = {id:await storage.randomId(),action:'capture',day:region.date,text,uncertain,source_region:{page_id:page.id,rect}};
          state.bindings[operation.id] = {pageId:page.id,rect,version:1,fingerprints:group.map(s => s.fingerprint),kind:/^(□|☐|\[ \])/.test(text)?'task':'event'};
          state.outbox.push(operation);
          previewPages.add(page.id);
          for (const stroke of group) state.seen[stroke.fingerprint] = operation.id;
          state.pendingPreviews = [...previewPages];
          await save(state); // Persist before making any network request.
        }
      }
      inkByPage.set(page.id,strokes.map(s => bounds(s.points,size)));
      const owned = elements.filter(e => metadata(e).owner === OWNER && metadata(e).kind === 'item' && metadata(e).bookId === state.bookId);
      for (const stroke of strokes.filter(s => !state.seen[s.fingerprint])) {
        const matches = owned.filter(e => crossedOut(stroke.points,metadata(e).rect,size) || (metadata(e).itemKind === 'task' && checked(stroke.points,metadata(e).rect,size)));
        if (matches.length !== 1) continue;
        const row = metadata(matches[0]);
        const operation = {id:await storage.randomId(),action:crossedOut(stroke.points,row.rect,size)?'cancel':'complete',item_id:row.itemId,version:row.version};
        state.outbox.push(operation);
        state.seen[stroke.fingerprint] = operation.id;
        state.pendingPreviews = [...previewPages];
        await save(state);
        previewPages.add(page.id);
      }
      // Free notes/erasures also refresh the paper preview, without calendar operations.
      const digest = await storage.hash(JSON.stringify(strokes.map(s => s.fingerprint).sort()));
      if (state.pageDigests?.[page.id] !== digest && (strokes.length || state.pageDigests?.[page.id] !== undefined)) previewPages.add(page.id);
      state.pageDigests = {...state.pageDigests,[page.id]:digest};
      state.pendingPreviews = [...previewPages];
      await save(state);
      release(elements);
    }
    // Chunking prevents oversized requests; server receipts make network retries safe.
    state.pendingPreviews = [...previewPages];
    await save(state);
    let response;
    do {
      report(`Sending ${Math.min(100,state.outbox.length)} changes to the planner`);
      response = await api(state,'/planner/api/v1/sync/',{schema:1,book_id:state.bookId,operations:state.outbox.slice(0,100),acknowledged_revision:state.appliedRevision || 0});
      state = acknowledge(state,response);
      await save(state);
    } while (state.outbox.length);
    let overflow = 0;
    const hitboxes = {};
    for (const page of state.manifest.pages.filter(p => p.kind === 'week')) {
      const index = map.get(page.id);
      report(`Updating calendar text on page ${index+1} of ${count}`);
      const size = check(await PluginFileAPI.getPageSize(path,index));
      const currentElements = await elementsAt(index,path);
      const owned = currentElements.filter(e => metadata(e).owner === OWNER && metadata(e).kind === 'item' && metadata(e).bookId === state.bookId);
      const rows = displayRows(page,state.items,{previous:owned.map(metadata),ink:inkByPage.get(page.id) || []});
      overflow += rows.overflow;
      hitboxes[page.id] = rows.map(row => ({item_id:row.item.id,rect:row.rect}));
      for (const binding of Object.values(state.bindings)) {
        if (binding.pageId === page.id && !binding.retired && state.items.some(i => i.id === binding.itemId && !i.deleted)) hitboxes[page.id].push({item_id:binding.itemId,rect:binding.rect});
      }
      const existingRows = owned.map(e => `${metadata(e).itemId}:${metadata(e).version}`).sort().join('|');
      const newRows = rows.map(row => `${row.item.id}:${row.item.version}`).sort().join('|');
      if (existingRows === newRows) {
        release(currentElements);
        continue;
      }
      previewPages.add(page.id);
      state.pendingPreviews = [...previewPages];
      await save(state);
      const incoming = [];
      for (const row of rows) incoming.push(await textElement(row.text,row.rect,index,size,{owner:OWNER,kind:'item',bookId:state.bookId,pageId:page.id,itemId:row.item.id,itemKind:row.item.kind,version:row.item.version,rect:row.rect}));
      // File-level mutation is safe only after flushing and before a host reload.
      // Deleting by native numInPage never touches unowned strokes or links.
      if (owned.length) changed(await PluginFileAPI.deleteElements(path,index,owned.map(e => e.numInPage)));
      if (incoming.length) changed(await PluginFileAPI.insertElements(path,index,incoming));
      release([...incoming,...currentElements]);
    }
    report('Refreshing the notebook after calendar updates');
    changed(await PluginCommAPI.reloadFile());
    state.appliedRevision = response.revision;
    state.pendingPreviews = [...previewPages];
    await save(state);
    for (const id of previewPages) {
      report(`Uploading handwritten preview: ${previewPages.size-state.pendingPreviews.length+1} of ${previewPages.size}`);
      const pngPath = (await directory()) + '/preview.png';
      changed(await PluginFileAPI.generateNotePng({notePath:path,page:map.get(id),times:1,pngPath,type:1}));
      await api({...state,previewPage:id},'/planner/api/v1/preview/',{regions:hitboxes[id] || []},pngPath);
      state.pendingPreviews = state.pendingPreviews.filter(p => p !== id);
      await save(state);
    }
    // Acknowledge only after every managed page was applied successfully.
    report('Confirming completed sync with the server');
    const confirmed = await api(state,'/planner/api/v1/sync/',{schema:1,book_id:state.bookId,operations:[],acknowledged_revision:state.appliedRevision});
    return `${confirmed.review_count ?? state.reviews?.length ?? 0} changes need review. ${overflow ? overflow + ' entries are available in the app; erase old cancellation marks to free blocked rows. ' : ''}Planner synced.${confirmed.validated === false ? ' Google writes await device acceptance.' : ''}`;
  } finally { releaseAll(); busy = false; }
}
