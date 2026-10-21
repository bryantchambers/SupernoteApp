// Uses the installed, pinned SDK's real exports, validators and element accessors.
// Only the Android bridge and HTTP transport are simulated; no SDK symbols are invented.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {resolve,dirname} from 'node:path';
import {stripTypeScriptTypes} from 'node:module';
import vm from 'node:vm';
import {createHash} from 'node:crypto';

const sdkRoot=resolve(process.env.SN_PLUGIN_SDK_ROOT || 'node_modules/sn-plugin-lib');
assert.equal(JSON.parse(await readFile(resolve(sdkRoot,'package.json'),'utf8')).version,'0.2.3');
const clone=value=>JSON.parse(JSON.stringify(value));
const ok=result=>({success:true,result});

async function fixture({pageCount=3}={}) {
  const context=vm.createContext({console,setTimeout,clearTimeout});
  const cache=new Map(), modules=new Map(), calls=[];
  let sequence=0, failInsertPage=-1, loseNextReply=false, failPreviewAt=-1, saved={origin:'https://planner.example:8443',bookId:'12345678-1234-1234-1234-123456789abc',token:'a'.repeat(43),seen:{},bindings:{},outbox:[],appliedRevision:0};
  const previewAttempts=[];
  let readFailure=false,recoveryCount=0;
  const size={width:1403,height:1871}; // Odd dimensions expose fractional rectangle bugs.
  const region={date:'2027-01-05',digital:[.07,.183,.86,.054],capture:[.07,.242,.86,.076]};
  const manifest={schema:1,pages:Array.from({length:pageCount},(_,index)=>({id:'page-'+index,index,kind:index===0?'week':'notes',regions:index===0?[region]:[]}))};
  const points=[{x:200,y:490},{x:220,y:500},{x:240,y:490}];
  const originalInk={type:0,stroke:{penType:10},userData:'',_points:points};
  const originalLink={type:600,userData:'',link:{destPage:1}};
  const pages=manifest.pages.map((_,n)=>n===0?[clone(originalInk),clone(originalLink)]:[]);
  const items=[{id:'existing',day:region.date,kind:'event',title:'Existing meeting',start_time:'09:00',version:1}];
  const received=new Map();
  function cached(element){
    const copy=clone(element), uuid='cache-'+(++sequence);
    const source=copy._points || [];delete copy._points;
    cache.set(uuid,{points:source});return {...copy,uuid};
  }
  const api={
    getCurrentFilePath:async()=>ok('/storage/emulated/0/Note/Planner/Test.note'),
    saveCurrentNote:async()=>ok(true),reloadFile:async()=>ok(true),
    getNoteTotalPageNum:async()=>ok(pages.length),getPageSize:async()=>ok(size),
    getElements:async(page)=>ok(pages[page].map((e,n)=>cached({...e,numInPage:n+1}))),
    createElement:async(type)=>{assert.equal(type,500);return ok(cached({type,pageNum:0,layerNum:0,numInPage:1}));},
    insertElements:async(_path,page,elements)=>{
      if(page===failInsertPage){failInsertPage=-1;return ok(false);}
      for(const e of elements){assert.equal(e.type,500);assert.equal(e.textBox.textEditable,0);pages[page].push(clone(e));}
      calls.push('insert');return ok(true);
    },
    deleteElements:async(_path,page,nums)=>{
      for(const num of nums)assert.equal(JSON.parse(pages[page][num-1].userData).owner,'supernote-planner');
      pages[page]=pages[page].filter((_,n)=>!nums.includes(n+1));return ok(true);
    },
    recycleElement:uuid=>{assert.ok(cache.delete(uuid),'Only live native cache objects may be recycled');},
    opElementPointData:async(uuid,_type,operation,index)=>{
      assert.ok(cache.has(uuid),'Point reads must happen before cache release');
      const value=cache.get(uuid).points;
      if(operation===0)return value.length;
      if(operation===2)return value.slice(index[0],(index[1]??index[0])+1);
      throw new Error('Unexpected point operation');
    },
    recognizeElements:async(elements,actualSize)=>{assert.equal(actualSize.width,size.width);assert.ok(elements.every(e=>cache.has(e.uuid)));return ok('14:00 Dentist');},
    generateNotePng:async params=>{assert.equal(params.times,1);calls.push('preview');return ok(true);},
  };
  const storage={
    load:async()=>{if(readFailure)throw new Error('AEADBadTagException: state authentication failed');return JSON.stringify(saved);},
    save:async(_root,value)=>{saved=JSON.parse(value);return true;},
    readPairingFromDocument:async()=>JSON.stringify({schema:1,origin:saved.origin,bookId:saved.bookId,token:saved.token,caPem:'-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----'}),
    recover:async(_root,value)=>{assert.ok(readFailure);saved=JSON.parse(value);readFailure=false;recoveryCount++;return true;},
    hash:async value=>createHash('sha256').update(value).digest('hex'),randomId:async()=>String(++sequence),
    request:async(_root,_origin,endpoint,_token,body,_ca,_png,pageId)=>{
      const request=JSON.parse(body);
      if(endpoint.endsWith('/preview/')) {
        previewAttempts.push(pageId);
        if(previewAttempts.length===failPreviewAt){failPreviewAt=-1;throw new Error('Tablet slept during preview upload');}
        return JSON.stringify({digest:'preview'});
      }
      for(const op of request.operations){
        if(!received.has(op.id)){
          received.set(op.id,op);
          if(op.action==='capture')items.push({id:op.id,day:op.day,kind:'event',title:op.text,version:1});
        }
      }
      if(loseNextReply && request.operations.length){loseNextReply=false;throw new Error('Connection interrupted after server receipt');}
      return JSON.stringify({schema:1,book_id:'12345678-1234-1234-1234-123456789abc',revision:7,manifest,items,operations:request.operations.map(op=>({id:op.id,status:'review',item_id:op.id,version:1}))});
    },
  };
  const manager={getPluginDirPath:async()=>'/private/planner',hasPermission:async()=>2,requestPermission:async()=>2};
  const native={NativePluginAPI:api,NativePluginManager:manager,FileSelector:{},RTNFileUtils:{},NativeUIUtils:{}};
  function synthetic(id,values){
    if(!modules.has(id))modules.set(id,new vm.SyntheticModule(Object.keys(values),function(){for(const [k,v] of Object.entries(values))this.setExport(k,v);},{context,identifier:id}));
    return modules.get(id);
  }
  const rn={NativeModules:{PlannerStorage:storage},TurboModuleRegistry:{getEnforcing:name=>{
    assert.ok(name in native,'Known native module: '+name);return native[name];
  }},DeviceEventEmitter:{addListener:()=>({remove(){}})},AppRegistry:{},NativeEventEmitter:class{}};
  async function source(file){
    file=resolve(file);
    if(modules.has(file))return modules.get(file);
    let text=await readFile(file,'utf8');
    if(/\.tsx?$/.test(file))text=stripTypeScriptTypes(text,{mode:'transform',sourceUrl:file});
    const module=new vm.SourceTextModule(text,{context,identifier:file});modules.set(file,module);return module;
  }
  async function link(name,from){
    if(name==='react-native')return synthetic('react-native',rn);
    if(name==='react-native/Libraries/TurboModule/TurboModuleRegistry')return synthetic(name,{get:()=>null});
    if(name==='sn-plugin-lib/package.json')return synthetic(name,{default:{version:'0.2.3'}});
    if(name==='sn-plugin-lib')return source(resolve(sdkRoot,'src/index.tsx'));
    if(!name.startsWith('.'))throw new Error('Unexpected dependency: '+name);
    const base=resolve(dirname(from.identifier),name);
    const path=[base,base+'.ts',base+'.tsx',base+'.js',base+'.mjs'].find(existsSync);
    assert.ok(path,'Resolvable SDK source: '+name);return source(path);
  }
  const engine=await source(resolve(dirname(new URL(import.meta.url).pathname),'engine.js'));
  await engine.link(link);await engine.evaluate();
  return {engine:engine.namespace,pages,calls,cache,received,manifest,items,get saved(){return saved;},
    failStateRead(){readFailure=true;},get recoveryCount(){return recoveryCount;},
    failInsert(page){failInsertPage=page;},loseReply(){loseNextReply=true;},
    failPreview(number){failPreviewAt=number;},previewAttempts,originalInk,api};
}

test('pinned SDK initializes 180 pages using real export names, integer text boxes and plain native objects',async()=>{
  const f=await fixture({pageCount:180});
  await f.engine.initialize();
  assert.equal(f.saved.manifest.pages.length,180);
  assert.equal(f.cache.size,0,'All page and created-element caches are released');
  assert.deepEqual(f.pages[0][0],f.originalInk,'Original ink is unchanged');
  assert.equal(f.pages[0][1].type,600,'Existing PDF navigation is preserved');
  assert.ok(f.pages.every(page=>page.filter(e=>e.userData && JSON.parse(e.userData).kind==='page-anchor').length===1));
  await f.engine.initialize(); // Retry recognizes existing anchors; no duplicate or leaked objects.
  assert.equal(f.cache.size,0);
  assert.ok(f.pages.every(page=>page.filter(e=>e.userData && JSON.parse(e.userData).kind==='page-anchor').length===1));
});

test('real SDK validators and point accessors exercise capture, text rendering, preview, acknowledgement and retry',async()=>{
  const f=await fixture();
  await f.engine.initialize();await f.engine.sync();
  assert.equal(f.received.size,1);assert.equal(f.saved.outbox.length,0);
  assert.equal(f.saved.appliedRevision,7);assert.equal(f.cache.size,0);
  assert.deepEqual(f.pages[0][0],f.originalInk);
  assert.ok(f.calls.includes('preview'));
  assert.ok(f.pages[0].some(e=>e.textBox?.textContentFull?.includes('Existing meeting')));
  await f.engine.sync();
  assert.equal(f.received.size,1,'Repeated sync must not capture the same ink twice');
  assert.equal(f.cache.size,0);
  assert.deepEqual(f.pages[0][0],f.originalInk);
  f.items[0]={...f.items[0],title:'Rescheduled meeting',version:2};
  await f.engine.sync();
  assert.ok(f.pages[0].some(e=>e.textBox?.textContentFull?.includes('Rescheduled meeting')));
  assert.deepEqual(f.pages[0][0],f.originalInk,'Replacing managed text never deletes original ink');
  assert.equal(f.pages[0][1].type,600,'Replacing managed text never deletes PDF links');
  assert.equal(f.cache.size,0);
});

test('connection loss after server receipt retains outbox and retries without duplicate captures',async()=>{
  const f=await fixture();await f.engine.initialize();f.loseReply();
  await assert.rejects(f.engine.sync(),/Connection interrupted/);
  assert.equal(f.saved.outbox.length,1);assert.equal(f.received.size,1);assert.equal(f.cache.size,0);
  await f.engine.sync();
  assert.equal(f.saved.outbox.length,0);assert.equal(f.received.size,1);assert.equal(f.cache.size,0);
  assert.deepEqual(f.pages[0][0],f.originalInk);
});

test('invalid native stroke size stops safely before sending handwriting operations',async()=>{
  const f=await fixture();await f.engine.initialize();
  f.api.opElementPointData=async()=>-1;
  await assert.rejects(f.engine.sync(),/could not read a handwriting stroke/);
  assert.equal(f.received.size,0);assert.equal(f.cache.size,0);
  assert.deepEqual(f.pages[0][0],f.originalInk);
});

test('false native insertion result stops safely and partial initialization resumes without duplicate anchors',async()=>{
  const f=await fixture();f.failInsert(1);
  await assert.rejects(f.engine.initialize(),/did not confirm/);
  assert.equal(f.saved.manifest,undefined);assert.equal(f.cache.size,0);
  assert.deepEqual(f.pages[0][0],f.originalInk);
  await f.engine.initialize();
  assert.equal(f.cache.size,0);
  assert.ok(f.pages.every(page=>page.filter(e=>e.userData && JSON.parse(e.userData).kind==='page-anchor').length===1));
});

test('sleep on preview 44 retains the remaining queue and retry skips 43 completed uploads',async()=>{
  const f=await fixture({pageCount:50});
  for(const page of f.manifest.pages){page.kind='week';page.regions=f.manifest.pages[0].regions;}
  await f.engine.initialize();f.failPreview(44);
  await assert.rejects(f.engine.sync(),/Tablet slept/);
  assert.equal(f.saved.pendingPreviews.length,7);
  assert.equal(f.saved.pendingPreviews[0],'page-43');
  const before=f.previewAttempts.length;
  await f.engine.sync();
  assert.equal(f.previewAttempts.length-before,7);
  assert.equal(f.saved.pendingPreviews.length,0);
  assert.equal(f.received.size,1,'Captured ink is not duplicated on retry');
  assert.equal(f.cache.size,0);
  assert.deepEqual(f.pages[0][0],f.originalInk);
});

test('stop during preview work checkpoints completed upload and releases busy state for resumption',async()=>{
  const f=await fixture({pageCount:3});
  for(const page of f.manifest.pages){page.kind='week';page.regions=f.manifest.pages[0].regions;}
  await f.engine.initialize();
  const generate=f.api.generateNotePng;
  f.api.generateNotePng=async params=>{f.engine.requestStop();return generate(params);};
  await assert.rejects(f.engine.sync(),/Paused safely/);
  assert.equal(f.saved.pendingPreviews.length,2);
  assert.equal(f.cache.size,0);
  f.api.generateNotePng=generate;
  await f.engine.sync();
  assert.equal(f.saved.pendingPreviews.length,0);
  assert.equal(f.previewAttempts.length,3);
});

test('pausing during capture preserves preview intent and the original outbox entry',async()=>{
  const f=await fixture();await f.engine.initialize();
  const recognize=f.api.recognizeElements;
  f.api.recognizeElements=async(...args)=>{f.engine.requestStop();return recognize(...args);};
  await assert.rejects(f.engine.sync(),/Paused safely/);
  assert.equal(f.saved.outbox.length,1);
  assert.ok(f.saved.pendingPreviews.includes('page-0'));
  assert.equal(f.received.size,0);
  assert.equal(f.cache.size,0);
  f.api.recognizeElements=recognize;
  await f.engine.sync();
  assert.equal(f.saved.outbox.length,0);
  assert.equal(f.received.size,1);
  assert.deepEqual(f.pages[0][0],f.originalInk);
});

test('confirmed authentication recovery checks notebook identities, preserves ink and reviews re-scanned captures',async()=>{
  const f=await fixture();await f.engine.initialize();
  const before=clone(f.pages);f.failStateRead();
  const config=await f.engine.recoverPairing();
  assert.equal(config.bookId,f.saved.bookId);
  assert.equal(f.recoveryCount,1);
  assert.deepEqual(f.pages,before,'Recovery does not write notebook elements');
  assert.equal(f.saved.recovered,true);
  assert.equal(f.saved.pendingPreviews.length,1);
  assert.equal(f.cache.size,0);
  await f.engine.sync();
  assert.equal(f.received.size,1);
  assert.ok([...f.received.values()].every(op=>op.uncertain===true));
});

test('recovery refuses readable state and mismatched page identities before creating any new state',async()=>{
  const readable=await fixture();await readable.engine.initialize();
  await assert.rejects(readable.engine.recoverPairing(),/Existing state is readable/);
  assert.equal(readable.recoveryCount,0);
  const wrong=await fixture();await wrong.engine.initialize();wrong.failStateRead();
  const anchor=wrong.pages[0].find(e=>e.userData);
  const data=JSON.parse(anchor.userData);data.bookId='another-book';anchor.userData=JSON.stringify(data);
  const before=clone(wrong.pages);
  await assert.rejects(wrong.engine.recoverPairing(),/Missing or duplicate page anchor/);
  assert.equal(wrong.recoveryCount,0);
  assert.deepEqual(wrong.pages,before);
  assert.equal(wrong.cache.size,0);
});
