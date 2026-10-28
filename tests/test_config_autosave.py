"""Exercise auto-save timing and edits made while a save is in flight."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_auto_save_waits_and_preserves_newer_edits():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required')
    source = (Path(__file__).resolve().parent.parent / 'webui.py').read_text()
    start = source.index('// Auto-save complete configuration')
    end = source.index('/* ===== 配置搜索', start)
    code = source[start:end]
    bootstrap = r'''
const assert = require('assert');
let now=1000; Date.now=()=>now;
let timer=null; global.setInterval=(fn)=>{timer=fn}; global.setTimeout=()=>0;
global.document={activeElement:null,addEventListener(){}};
let nodes={cfgmsg:{textContent:''}}; function $(id){return nodes[id]||null;}
let CFG=[{name:'before',routes:[],notifier:{}}], GLB={port:8765};
let CFG_CLEAN=JSON.stringify(CFG), GLB_CLEAN=JSON.stringify(GLB), CFG_REVISION='rev1', CFG_PROXY_MODE=true;
let CFG_WATCH={dirty:false}, GLB_RANGES=[], INTL={}, INTLN={}, CITY={}, INTL_LOADED=true;
let SRX_RADAR_CFG={},GLB_STORE={},RESTART={}, CFG_AUTO_ERRORS=[];
let RUNTIME={}; let buildCount=0;
const location={href:'https://ticket.example:8888/'};
const window={toast:true};
function _canon(x){return x;} function cfgErr(s){CFG_AUTO_ERRORS.push(s);CFG_AUTO_ERROR=true;}
function _startupChanged(){return [];} function _restartArmed(){} function buildForm(){buildCount++;}
function toast(){} function renderRestartCenter(){} function load(){}
let resolveFetch=null, sentBody=null, requests=0;
function fetch(url,options){requests++;sentBody=JSON.parse(options.body);return new Promise(r=>{resolveFetch=r;});}
'''
    scenario = r'''
(async()=>{
 CFG[0].name='first'; timer();
 now+=2600; timer();
 assert.strictEqual(requests,1);
 assert.strictEqual(sentBody.users[0].name,'first');
 // While the response is pending, a new edit must remain on screen and dirty.
 CFG[0].name='second';
 resolveFetch({json:async()=>({ok:true,users:1,revision:'rev2'})});
 await CFG_SAVE_FLIGHT;
 assert.strictEqual(JSON.parse(CFG_CLEAN)[0].name,'first');
 assert.strictEqual(CFG[0].name,'second');
 assert.strictEqual(buildCount,0);
 assert.strictEqual(cfgCoreDirty(),true);
 timer();now+=2600;timer();
 assert.strictEqual(requests,2);
 assert.strictEqual(sentBody.users[0].name,'second');
 resolveFetch({json:async()=>({ok:true,users:1,revision:'rev2'})});
 await CFG_SAVE_FLIGHT;
 assert.strictEqual(cfgCoreDirty(),false);
 assert.deepStrictEqual(CFG_AUTO_ERRORS,[]);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run([node, '-e', bootstrap + code + scenario], check=True, timeout=10)
