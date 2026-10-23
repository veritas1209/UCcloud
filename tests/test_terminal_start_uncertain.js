const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/terminal.js','utf8');
const id = 'c6e69a7b-41bf-4bd4-bbac-1eed7aa26205';
const flush = () => new Promise(resolve => setImmediate(resolve));

async function check(mode) {
  const nodes = new Map();
  const node = name => {
    if (!nodes.has(name)) {
      const classes = new Set(['hidden']);
      nodes.set(name,{value:'',textContent:'',disabled:false,listeners:{},
        classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
        addEventListener(type,callback){this.listeners[type]=callback;},
        querySelectorAll(){return [];},focus(){}});
    }
    return nodes.get(name);
  };
  const timers = [];
  const actions = [];
  let now = 1700000000000;
  let vmStatus = 'SHUTOFF';
  const context = {
    URL,URLSearchParams,TextDecoder,
    Date:class extends Date {static now(){return now;}},
    document:{getElementById:node},
    location:{search:`?vm=${id}&start=1`},
    window:{addEventListener(){}},
    Terminal:class{constructor(){this.options={};}loadAddon(){}onData(){}onSelectionChange(){}clearSelection(){}attachCustomKeyEventHandler(){}},
    FitAddon:{FitAddon:class{}},WebLinksAddon:{WebLinksAddon:class{}},
    ResizeObserver:class{observe(){}},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},
    fetch:async path=>{
      if(path==='/api/me')return{ok:true,json:async()=>({csrf_token:'test-csrf'})};
      if(path==='/api/instances')return{ok:true,json:async()=>({instances:[{
        id,name:'stopped-vm',vm_username:'student',status:vmStatus,
        addresses:vmStatus==='ACTIVE'?[{address:'10.30.23.177',version:4}]:[],
      }]})};
      if(path===`/api/instances/${id}/action`){
        actions.push(path);
        if(actions.length===1 && mode==='network')throw new Error('network unavailable');
        return actions.length===1?{ok:false,status:502}:{ok:true,status:202};
      }
      throw new Error(`Unexpected fetch: ${path}`);
    },
  };
  vm.createContext(context);
  vm.runInContext(source,context);
  await flush();
  assert.equal(actions.length,1);
  assert.match(node('status').textContent,/시작 요청 결과를 확인하지 못했습니다/);
  assert.equal(timers.length,1);

  timers.shift()();
  await flush();
  assert.equal(actions.length,1,'polling must not repeat an uncertain start request');
  assert.match(node('status').textContent,/자동으로 확인합니다/);

  now+=301000;
  timers.shift()();
  await flush();
  assert.equal(actions.length,1);
  assert.equal(node('retry-start').classList.contains('hidden'),false);
  assert.match(node('status').textContent,/다시 켜기를 누르세요/);
  assert.equal(timers.length,0);

  node('retry-start').listeners.click();
  await flush();
  assert.equal(actions.length,2,'one explicit retry sends one new start request');
  assert.equal(node('retry-start').classList.contains('hidden'),true);
  vmStatus='ACTIVE';
  timers.shift()();
  await flush();
  assert.equal(actions.length,2);
  assert.equal(node('connect-form').classList.contains('hidden'),false);
}

(async()=>{
  await check('http-502');
  await check('network');
  console.log('Uncertain VM start and one-click retry checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
