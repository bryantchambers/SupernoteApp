import test from 'node:test';
import assert from 'node:assert/strict';
import {pageMap, crossedOut, checked, newStrokeGroups, displayRows, acknowledge, parsePairingFile, readWithTimeout} from './core.mjs';

test('a timed-out read cannot continue into a later write when its result arrives',async()=>{
  let resolveRead,wrote=false;
  const pending=new Promise(resolve=>{resolveRead=resolve;});
  const action=async()=>{await readWithTimeout('Pairing file read',()=>pending,10);wrote=true;};
  await assert.rejects(action(),/Pairing file read did not respond/);
  resolveRead('late data');
  await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal(wrote,false);
  assert.equal(await readWithTimeout('Quick read',()=>Promise.resolve('ok'),100),'ok');
});

test('pairing import accepts only HTTPS planner credentials and a public certificate', () => {
  const value = {schema:1,origin:'https://planner.example:8443',bookId:'12345678-1234-1234-1234-123456789abc',
    token:'a'.repeat(43),caPem:'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----',outbox:['untrusted']};
  const result = parsePairingFile(JSON.stringify(value));
  assert.equal(result.origin,value.origin);
  assert.equal(result.bookId,value.bookId);
  assert.equal(result.token,value.token);
  assert.equal(result.outbox,undefined);
  for (const change of [{origin:'http://planner.example'}, {origin:'https://user@planner.example'},
      {origin:'https://planner.example/path'}, {bookId:'bad'}, {token:'bad'},
      {schema:2}, {caPem:'-----BEGIN PRIVATE KEY-----\nYWJj\n-----END PRIVATE KEY-----'}]) {
    assert.throws(() => parsePairingFile(JSON.stringify({...value,...change})));
  }
  assert.throws(() => parsePairingFile('not json'));
  assert.throws(() => parsePairingFile('x'.repeat(65537)));
});

const anchor = pageId => ({userData: JSON.stringify({owner: 'supernote-planner', kind: 'page-anchor', bookId: 'book', pageId})});
test('page reorder uses anchors, duplicate/missing anchors stop sync', () => {
  assert.equal(pageMap([[anchor('b')], [anchor('a')]], 'book').get('a'), 1);
  assert.throws(() => pageMap([[anchor('a')], [anchor('a')]], 'book'));
  assert.throws(() => pageMap([[]], 'book'));
});
test('only a deliberate horizontal stroke cancels a rendered event', () => {
  const size = {width:1000, height:1000}, rect = [.1,.2,.8,.04];
  assert.equal(crossedOut([{x:110,y:220},{x:850,y:220}],rect,size), true);
  assert.equal(crossedOut([{x:110,y:200},{x:850,y:240}],rect,size), false);
  assert.equal(crossedOut([{x:110,y:220},{x:130,y:220}],rect,size), false);
});
test('already submitted strokes and free notes do not create new entries', () => {
  const strokes = [{fingerprint:'a',points:[{x:100,y:300},{x:200,y:310}]},{fingerprint:'b',points:[{x:100,y:800},{x:200,y:810}]}];
  const region = {capture:[.05,.25,.9,.1]};
  assert.equal(newStrokeGroups(strokes,region,{width:1000,height:1000},{}).length,1);
  assert.equal(newStrokeGroups(strokes,region,{width:1000,height:1000},{a:true}).length,0);
});
test('calendar overflow stays out of handwriting area and reports overflow', () => {
  const page = {regions:[{date:'2027-01-01',digital:[.1,.2,.8,.06]}]};
  const items = Array.from({length:4}, (_,i) => ({id:String(i),day:'2027-01-01',title:'Event '+i,kind:'event'}));
  const rows = displayRows(page,items);
  assert.equal(rows.length,2);
  assert.match(rows[1].text,/\+2 in app/);
  assert.equal(rows[1].rect[1]+rows[1].rect[3],.26);
});
test('acknowledge only persisted operations; retain unsent work', () => {
  const state = {outbox:[{id:'a'},{id:'b'}]};
  const next = acknowledge(state,{operations:[{id:'a',status:'review'}],revision:3,items:[]});
  assert.deepEqual(next.outbox,[{id:'b'}]);
  assert.equal(next.reviews.length,1);
});
test('task completion needs a check inside the generated checkbox', () => {
  assert.equal(checked([{x:102,y:215},{x:112,y:225},{x:128,y:208}],[.1,.2,.8,.04],{width:1000,height:1000}),true);
  assert.equal(checked([{x:402,y:215},{x:412,y:225},{x:428,y:208}],[.1,.2,.8,.04],{width:1000,height:1000}),false);
});
test('calendar refresh never places another entry beneath an old ink mark', () => {
  const page = {regions:[{date:'2027-01-01',digital:[.1,.2,.8,.06]}]};
  const items = [{id:'new',day:'2027-01-01',title:'New event',kind:'event'}];
  const rows = displayRows(page,items,{ink:[[.1,.21,.9,.22]]});
  assert.equal(rows[0].rect[1],.23);
  const blocked = displayRows(page,items,{ink:[[.1,.2,.9,.26]]});
  assert.equal(blocked.length,0);
  assert.equal(blocked.overflow,1);
});
test('source handwriting keeps its item identity and receives current versions', () => {
  const state = {outbox:[{id:'source'}],bindings:{source:{pageId:'page',rect:[.1,.2,.4,.02]}}};
  const next = acknowledge(state,{operations:[{id:'source',item_id:'item',version:1}],revision:4,items:[{id:'item',version:2,kind:'event'}]});
  assert.equal(next.bindings.source.itemId,'item');
  assert.equal(next.bindings.source.version,2);
});
