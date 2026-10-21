import React, {useEffect, useState, useRef} from 'react';
import {AppState, BackHandler, ScrollView, Text, TextInput, Button, View} from 'react-native';
import {NativePluginManager, NativeUIUtils} from 'sn-plugin-lib';
import {load, save, configure, initialize, sync, importPairing, recoverPairing, inspectNotebook, requestStop, setKeepAwake} from './engine';

export default function App() {
  const [config,setConfig] = useState({origin:'',bookId:'',token:'',caPem:''});
  const [message,setMessage] = useState('Connect this notebook to Daily Planner in your existing app.');
  const [working,setWorking] = useState(false);
  const [ready,setReady] = useState(false);
  const [manual,setManual] = useState(false);
  const [awakeUnavailable,setAwakeUnavailable] = useState(false);
  const [recoveryAvailable,setRecoveryAvailable] = useState(false);
  const [confirmRecovery,setConfirmRecovery] = useState(false);
  const refreshTimer = useRef(null);
  const running = useRef(false);
  const closeAfterAction = useRef(false);
  async function close() {
    if (running.current) {
      closeAfterAction.current = true;
      requestStop();
      setMessage('Stopping after the current operation is saved, then returning to your notebook.');
      return;
    }
    try { await setKeepAwake(false); } catch {}
    try { await NativePluginManager.closePluginView(); }
    catch (e) { setMessage('Could not close the plugin: '+(e?.message || String(e))); }
  }
  useEffect(() => {
    const back = BackHandler.addEventListener('hardwareBackPress',() => { close(); return true; });
    const state = AppState.addEventListener('change',value => {
      if (value !== 'active' && running.current) requestStop();
    });
    return () => { back.remove(); state.remove(); requestStop(); setKeepAwake(false).catch(() => {}); };
  },[]);
  function refresh() {
    if (refreshTimer.current !== null) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      try { NativePluginManager.invalidatePluginView(); } catch {}
    },400);
  }
  useEffect(() => { load().then(s => {
    setConfig({origin:s.origin || '',bookId:s.bookId || '',token:s.token || '',caPem:s.caPem || ''});
    if (s.lastAction?.message) setMessage('Previous result:\n'+s.lastAction.message);
    else if (s.origin) setMessage('Connection already saved. You can check or initialize the notebook without importing again.');
  }).catch(e => { setMessage(e.message); setRecoveryAvailable(/AEADBadTagException/.test(e.message || '')); }).finally(() => setReady(true)); },[]);
  useEffect(refresh,[message,working,manual,config.origin,ready,awakeUnavailable,recoveryAvailable,confirmRecovery]);
  useEffect(() => () => { if (refreshTimer.current !== null) clearTimeout(refreshTimer.current); },[]);
  async function run(label, action) {
    if (running.current || !ready) return;
    running.current = true;
    setWorking(true);
    let stage = label, changed = Date.now(), result, success = false;
    const report = value => { stage = value; changed = Date.now(); setMessage(value); };
    report(label);
    const waiting = setInterval(() => {
      if (Date.now()-changed >= 15000) setMessage(stage+'\nStill waiting for the tablet ('+Math.round((Date.now()-changed)/1000)+' seconds). Keep this notebook open.');
    },5000);
    try { setAwakeUnavailable(!(await setKeepAwake(true))); result = await action(report) || 'Connection saved.'; success = true; }
    catch(e) { result = label+' stopped at: '+stage+'\n'+(e?.message || String(e)); if (/AEADBadTagException/.test(e?.message || '')) setRecoveryAvailable(true); }
    finally { clearInterval(waiting); }
    setMessage(result);
    // Persist the result only after the engine has finished, never alongside its outbox writes.
    try { const state = await load(); await save({...state,lastAction:{message:result,success,at:Date.now()}}); } catch {}
    try { await setKeepAwake(false); } catch {}
    setWorking(false);
    running.current = false;
    refresh();
    if (closeAfterAction.current) { closeAfterAction.current = false; await close(); return; }
    try { NativeUIUtils.showRattaDialog(result,'','OK',success).catch(() => {}); } catch {}
  }
  return <View style={{flex:1,backgroundColor:'white'}}>
    <View style={{padding:20,borderBottomColor:'black',borderBottomWidth:2}}>
      <Text style={{fontSize:24,fontWeight:'700',marginBottom:10}}>Daily Planner · 0.1.6</Text>
      <Button title={working ? 'Stop and return to notebook' : 'Return to notebook'} onPress={close}/>
      <Text style={{fontSize:18,color:'black'}} accessibilityLiveRegion="polite">{message}</Text>
      {working ? <Text style={{fontSize:16,marginTop:8}}>Working — keep this notebook open.</Text> : null}
    </View>
    <ScrollView style={{flex:1}} contentContainerStyle={{padding:24}} onScroll={refresh} scrollEventThrottle={250}>
    {awakeUnavailable ? <Text style={{fontSize:18,marginBottom:16}}>Automatic sleep prevention is unavailable in this plugin host. Keep the tablet awake during sync or temporarily increase its auto-sleep interval.</Text> : null}
    {recoveryAvailable ? <View style={{borderColor:'black',borderWidth:2,padding:16,marginBottom:20}}>
      <Text style={{fontSize:18,marginBottom:12}}>Saved state failed its encryption check. Importing again cannot repair it. Recovery keeps the unreadable file and old key, checks the pairing file against this notebook, and creates a separate encrypted connection. Unsynced changes inside the unreadable file cannot be restored automatically. Notebook ink and server entries are kept. Handwriting will be scanned again and sent for review, so check for duplicates.</Text>
      <Button title={confirmRecovery ? 'Recover using pairing file; keep old state' : 'Review connection recovery'} disabled={working || !ready} onPress={() => {
        if (!confirmRecovery) { setConfirmRecovery(true); return; }
        run('Recover connection',async report => { const saved = await recoverPairing(report); setConfig(saved); setRecoveryAvailable(false); setConfirmRecovery(false); return 'Connection recovered. Unreadable state and old key were kept. Run Sync Planner, then review captured entries in the app. No reinitialization is needed.'; });
      }}/>
    </View> : null}
    <Text style={{fontSize:18,marginBottom:16}}>Copy supernote-planner-pairing.json into the tablet's Document folder. Import reads that file directly; there is no file chooser. If your app address is already shown below, you can use the existing pairing.</Text>
    <Button title="Import pairing file from Document" disabled={working || !ready} onPress={() => run('Import pairing file',async report => { const saved = await importPairing(report); setConfig(saved); return 'Connection imported and saved. Remove the pairing file from shared storage after setup.'; })}/>
    {config.origin ? <Text style={{fontSize:16,marginTop:16}}>App: {config.origin}</Text> : null}
    <View style={{height:20}}/><Button title={manual ? 'Hide manual setup' : 'Manual setup'} disabled={working || !ready} onPress={() => setManual(!manual)}/>
    {manual ? <View style={{marginTop:20}}>{[['origin','App HTTPS address'],['bookId','Planner ID'],['token','Device credential'],['caPem','Public CA certificate (PEM)']].map(([field,label]) => <View key={field} style={{marginBottom:18}}><Text>{label}</Text><TextInput autoCapitalize="none" autoCorrect={false} secureTextEntry={field==='token'} multiline={field==='caPem'} value={config[field]} onChangeText={value => setConfig({...config,[field]:value})} style={{borderColor:'#777',borderWidth:1,padding:12,fontSize:18,marginTop:5}} /></View>)}<Button title="Save connection" disabled={working || !ready} onPress={() => run('Save connection',() => configure(config))}/></View> : null}
    <View style={{height:20}}/><Button title="Check notebook (read only)" disabled={working || !ready} onPress={() => run('Check notebook',inspectNotebook)}/><View style={{height:20}}/><Button title="Initialize imported notebook" disabled={working || !ready} onPress={() => run('Initialize notebook',initialize)}/><View style={{height:20}}/><Button title="Sync Planner" disabled={working || !ready} onPress={() => run('Sync planner',sync)}/><View style={{height:40}}/>
  </ScrollView></View>;
}
