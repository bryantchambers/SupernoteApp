import {AppRegistry, Image} from 'react-native';
import {PluginManager} from 'sn-plugin-lib';
import App from './App';

AppRegistry.registerComponent('SupernotePlanner', () => App);
PluginManager.init();
PluginManager.registerButton(1,['NOTE'],{id:701,name:'Sync Planner',icon:Image.resolveAssetSource(require('./assets/planner.png')).uri,showType:1});
