import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

async function engine({permission=2,count=2,expected=3,pairing=null,pairingRead=null,pending=[],fastTimeouts=false,awake=false}={}) {
  const calls=[];
  let state={origin:'https://planner.example:8443',bookId:'12345678-1234-1234-1234-123456789abc',token:'a'.repeat(43),caPem:'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----',appliedRevision:0,outbox:pending};
  const response={success:true,result:null};
  const sdk={
    PluginManager:{getPluginDirPath:async()=>'/private/planner',requestPermission:async()=>permission},
    NativePluginManager:{hasPermission:async()=>permission},
    PluginNoteAPI:{saveCurrentNote:async()=>{calls.push('save-note');return {...response,result:true};}},
    PluginCommAPI:{getCurrentFilePath:async()=>({...response,result:'/storage/emulated/0/Note/Planner/Test.note'})},
    PluginFileAPI:{getNoteTotalPageNum:async()=>({...response,result:count}),insertElements:async()=>{calls.push('insert');}},
    Element:{TYPE_TEXT:500,TYPE_STROKE:0},TextBox:class{},RattaFileSelector:{},FileUtils:{},
  };
  const native={NativeModules:{PlannerStorage:{
    setKeepAwake:async()=>{if(awake instanceof Error)throw awake;return awake;},
    readPairingFromDocument:async()=>{calls.push('read-pairing');return pairingRead ? pairingRead() : JSON.stringify(pairing || {schema:1,...state});},
    load:async()=>JSON.stringify(state),
    save:async(_dir,value)=>{calls.push('save-state');state=JSON.parse(value);},
    request:async(_dir,_origin,_endpoint,_token,body)=>{calls.push(['request',JSON.parse(body)]);return JSON.stringify({manifest:{pages:Array(expected).fill({})},items:[],revision:0});},
  }}};
  const context=vm.createContext({console,setTimeout:fastTimeouts ? (fn,ms)=>setTimeout(fn,Math.min(ms,10)) : setTimeout,clearTimeout});
  function mock(values) {return new vm.SyntheticModule(Object.keys(values),function(){for(const [key,value] of Object.entries(values))this.setExport(key,value);},{context});}
  const core=new vm.SourceTextModule(await readFile(new URL('./core.mjs',import.meta.url),'utf8'),{context});
  await core.link(()=>{throw new Error('Unexpected core import');});
  const module=new vm.SourceTextModule(await readFile(new URL('./engine.js',import.meta.url),'utf8'),{context});
  await module.link(name=>name==='react-native'?mock(native):name==='sn-plugin-lib'?mock(sdk):core);
  await module.evaluate();
  return {api:module.namespace,calls,get state(){return state;}};
}

test('notebook diagnosis reports page mismatch without saving or changing notebook elements',async()=>{
  const {api,calls}=await engine();const stages=[];
  const result=await api.inspectNotebook(value=>stages.push(value));
  assert.match(result,/Pages: 2; planner expects 3/);
  assert.match(result,/Initialization has not completed/);
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'request');
  assert.equal(calls[0][1].operations.length,0);
  assert.ok(stages.includes('Counting notebook pages'));
});

test('initialization rejects a mismatched notebook before inserting anchors',async()=>{
  const {api,calls}=await engine();const stages=[];
  await assert.rejects(api.initialize(value=>stages.push(value)),/Notebook has 2 pages; this planner needs 3/);
  assert.ok(stages.includes('Checking notebook page count'));
  assert.ok(!calls.includes('insert'));
  assert.ok(!calls.includes('save-state'));
});

test('denied device permission stops before contacting server or modifying notebook',async()=>{
  const {api,calls}=await engine({permission:0});
  await assert.rejects(api.initialize(),/Allow FILE:READ/);
  assert.equal(calls.length,0);
});

test('pairing reads the fixed Document file without a picker and retains existing queued changes',async()=>{
  const f=await engine({pending:[{id:'queued-change'}]});const stages=[];
  const config=await f.api.importPairing(value=>stages.push(value));
  assert.equal(config.origin,'https://planner.example:8443');
  assert.equal(f.state.outbox[0].id,'queued-change');
  assert.deepEqual(f.calls,['read-pairing'],'An identical pairing does not rewrite existing state');
  assert.ok(stages.includes('Reading Document/supernote-planner-pairing.json'));
  assert.ok(stages.includes('Validating the pairing file'));
});

test('absent foreground window or rejected wake guard never blocks pairing or clears pending changes',async()=>{
  for(const awake of [false,new Error('No foreground planner window')]) {
    const f=await engine({awake,pending:[{id:'keep'}]});
    assert.equal(await f.api.setKeepAwake(true),false);
    await f.api.importPairing();
    assert.equal(f.state.outbox[0].id,'keep');
    assert.deepEqual(f.calls,['read-pairing']);
  }
});

test('invalid or missing pairing file never replaces an existing connection',async()=>{
  for(const options of [{pairing:{schema:1}}, {pairingRead:()=>{throw new Error('Pairing file not found');}}]) {
    const f=await engine({...options,pending:[{id:'keep'}]});
    await assert.rejects(f.api.importPairing());
    assert.equal(f.state.token,'a'.repeat(43));
    assert.equal(f.state.outbox[0].id,'keep');
    assert.ok(!f.calls.includes('save-state'));
  }
});

test('pairing for another planner cannot discard unsent changes',async()=>{
  const f=await engine({pending:[{id:'keep'}],pairing:{schema:1,origin:'https://other.example',bookId:'99999999-1234-1234-1234-123456789abc',token:'b'.repeat(43),caPem:'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----'}});
  await assert.rejects(f.api.importPairing(),/Sync pending changes/);
  assert.equal(f.state.outbox[0].id,'keep');
  assert.ok(!f.calls.includes('save-state'));
});

test('stalled pairing read times out and a late result cannot replace the saved connection',async()=>{
  let resolveRead;
  const stalled=new Promise(resolve=>{resolveRead=resolve;});
  const f=await engine({pending:[{id:'keep'}],fastTimeouts:true,pairingRead:()=>stalled});
  const stages=[];
  await assert.rejects(f.api.importPairing(value=>stages.push(value)),/Reading the pairing file from Document did not respond/);
  resolveRead(JSON.stringify({schema:1,origin:'https://other.example',bookId:'99999999-1234-1234-1234-123456789abc',token:'b'.repeat(43),caPem:'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----'}));
  await new Promise(resolve=>setTimeout(resolve,15));
  assert.equal(f.state.origin,'https://planner.example:8443');
  assert.equal(f.state.outbox[0].id,'keep');
  assert.ok(!f.calls.includes('save-state'));
  assert.ok(stages.includes('Reading Document/supernote-planner-pairing.json'));
});
