// Offline interaction tests. No browser or live dictionary is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag='div') {
    this.tag=tag; this.children=[]; this.listeners={}; this.attributes={}; this.dataset={};
    this.value=''; this.style={}; this.classList={add(){},remove(){}};
  }
  append(...nodes) { for(const node of nodes) node.parentElement=this; this.children.push(...nodes); }
  replaceChildren(...nodes) { for(const child of this.children) child.parentElement=undefined; this.children=[];this.append(...nodes); }
  remove() { if(this.parentElement) this.parentElement.children=this.parentElement.children.filter(child=>child!==this); this.parentElement=undefined; }
  get lastElementChild() { return this.children.at(-1); }
  contains(node) { return node===this||this.children.some(child=>child.contains(node)); }
  setAttribute(key,value) { this.attributes[key]=value; }
  addEventListener(event,fn) { (this.listeners[event] ||= []).push(fn); }
  async fire(event,details={}) {
    const value={target:this,preventDefault(){},...details};
    for(let node=this;node;node=node.parentElement) for(const fn of node.listeners[event]||[]) await fn(value);
  }
  querySelectorAll(selector) {
    const descendants=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);
    return descendants.filter(child=>selector==='*'||child.tag===selector||
      (selector==='[aria-current="true"]'&&child.attributes['aria-current']==='true')||
      (selector==='[data-field="reading"]'&&child.dataset.field==='reading')||
      (selector==='[data-field="word"]'&&child.dataset.field==='word'));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  showModal() { this.open=true; }
  close() { this.open=false; }
  focus() {}
  scrollIntoView() {}
}

const html=fs.readFileSync(path.join(__dirname,'app/index.html'),'utf8');
const ids=new Map([...html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)].map(match=>[match[2],new Element(match[1])]));
ids.get('edit-form').append(ids.get('edit-name'),ids.get('edit-reading'),ids.get('alias-rows'));
const decisions=['採用','保留','不採用'].map(decision=>Object.assign(new Element('button'),{dataset:{decision}}));
const storage=new Map();
const scheduled=new Map(); let timerNumber=0;
const context=vm.createContext({
  document:{getElementById(id){assert.ok(ids.has(id),`missing element: ${id}`);return ids.get(id);},
    createElement:tag=>new Element(tag), querySelectorAll:()=>decisions, querySelector:()=>null,addEventListener(){}},
  window:{addEventListener(){}},
  localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
  setTimeout:(fn,delay)=>{const id=++timerNumber;scheduled.set(id,{fn,delay});return id;},clearTimeout:id=>scheduled.delete(id),fetch(){throw Error('Unexpected network access');},console,
});
const source=fs.readFileSync(path.join(__dirname,'app/app.js'),'utf8');
const startup=source.lastIndexOf('(async()=>{try {sessionToken=');
assert.ok(startup>0);
vm.runInContext(source.slice(0,startup),context);
const run=script=>vm.runInContext(script,context);
const json=expression=>JSON.parse(run(`JSON.stringify(${expression})`));
const record=(key,name,decision='未判断')=>({key,name,reading:'よみ',decision,memo:'',evidence_summary:'根拠',
  source_url:'https://example.com',youtube_channel_id:''});
const fixtures=[record('main:a','候補1','保留'),record('main:b','候補2','採用'),record('main:c','候補3')];
const state={batch_id:'ui-test',revision:1,date:'2026-10-10',records:fixtures,dictionary_count:1,history:[],complete:null};

(async()=>{
  run(`state=${JSON.stringify(state)}; selected='main:a'; requests=[];`);
  ids.get('filter').value='all';
  run(`catalogRecords=${JSON.stringify(fixtures)}; catalogKind='held'; renderCatalog();`);
  // Check one row, search for another, and select the visible result.
  const first=ids.get('catalog-list').children[0].children[0];
  first.checked=true; await first.fire('change');
  ids.get('catalog-search').value='候補2'; run('renderCatalog()');
  await ids.get('catalog-select-all').fire('click');
  assert.deepEqual(json('[...catalogSelected]'),['main:a','main:b']);
  ids.get('catalog-search').value=''; run('renderCatalog()');
  assert.equal(ids.get('catalog-list').children[0].children[0].checked,true);
  assert.equal(ids.get('catalog-list').children[1].children[0].checked,true);
  assert.equal(ids.get('catalog-selected').textContent,'2件選択中');
  // The explicit bulk button makes one request and opens the chosen subset.
  run(`api=async(route,body)=>{requests.push({route,body});return {state,selected:body.keys[0],selected_keys:body.keys};};`);
  ids.get('catalog-dialog').showModal();
  await ids.get('recall-selected').fire('click');
  assert.deepEqual(json('requests[0].body.keys'),['main:a','main:b']);
  assert.equal(json('requests.length'),1);
  assert.equal(ids.get('catalog-dialog').open,false);
  assert.equal(ids.get('filter').value,'picked');
  assert.deepEqual(json('visible().map(r=>r.key)'),['main:a','main:b']);
  // Choosing a decision advances to the next picked row, even if already adopted.
  run(`api=async(route,body)=>({revision:state.revision+1,saved_at:'now',state:{...state,
    records:state.records.some(r=>r.key==='main:d')?state.records:[...state.records,${JSON.stringify(record('main:d','補充候補'))}]}});`);
  await run("decide('採用')");
  assert.equal(json('selected'),'main:b');
  assert.deepEqual(json('visible().map(r=>r.key)'),['main:a','main:b']);
  await run("decide('保留')");
  assert.equal(json('selected'),'main:b');
  assert.equal(json('visible()[1].decision'),'保留');
  run('reviewKeys=[]; selected="main:c"; restoreReview(); render();');
  assert.equal(json('selected'),'main:b');
  assert.deepEqual(json('reviewKeys'),['main:a','main:b']);
  assert.equal(ids.get('position').textContent,'2 / 2');
  await ids.get('leave-review').fire('click');
  assert.equal(json('visible().length'),4);
  // Local adoptions and DB adoptions appear once; local edits take precedence.
  run(`state=${JSON.stringify(state)}; state.records[0].decision='採用';
    state.records[0].registration={name:'編集した候補',reading:'へんしゅう',aliases:[]};
    api=async()=>({records:[${JSON.stringify(fixtures[0])},${JSON.stringify(record('main:e','DB採用'))}]});`);
  await run("openCatalog('approved')");
  assert.deepEqual(json('catalogRecords.map(r=>r.key)'),['main:a','main:b','main:e']);
  assert.equal(json('catalogRecords[0].name'),'編集した候補');
  await ids.get('catalog-select-all').fire('click');
  await ids.get('catalog-clear').fire('click');
  assert.equal(json('catalogSelected.size'),0);
  assert.equal(ids.get('recall-selected').disabled,true);
  // Inline fields and one unused alias are present without opening a dialog.
  run(`state=${JSON.stringify(state)}; editKey=undefined;editDirty=false;selected='main:c';
    requests=[];api=async(route,body)=>{requests.push({route,body});return {revision:state.revision+1,saved_at:'now'};};`);
  ids.get('filter').value='all'; run('render()');
  assert.equal(ids.get('edit-name').value,'候補3');
  assert.equal(ids.get('alias-rows').children.length,1);
  assert.equal(ids.has('edit-dialog'),false);
  ids.get('edit-name').value='修正した名前'; await ids.get('edit-name').fire('input');
  ids.get('edit-reading').value='しゅうせい'; await ids.get('edit-reading').fire('input');
  const aliasRow=ids.get('alias-rows').children[0];
  assert.equal(aliasRow.querySelector('[data-field="word"]').value,'修正した名前');
  aliasRow.querySelector('[data-field="reading"]').value='したのなまえ';
  await aliasRow.querySelector('[data-field="reading"]').fire('input');
  const timer=[...scheduled].find(([,job])=>job.delay===450);
  assert.ok(timer); scheduled.delete(timer[0]); await timer[1].fn();
  assert.deepEqual(json('current().registration'),{name:'修正した名前',reading:'しゅうせい',aliases:[{reading:'したのなまえ',word:'修正した名前'}]});
  assert.equal(json('editDirty'),false);
  assert.equal(ids.get('edit-status').textContent,'保存済み');
  assert.equal(json('requests[0].route'),'save');
  assert.equal(json('current().decision'),'未判断');
  // Change the alias output to the given name; navigation flushes it before leaving.
  aliasRow.querySelector('[data-field="word"]').value='下の名前';
  await aliasRow.querySelector('[data-field="word"]').fire('input');
  await run("select('main:a')");
  assert.equal(json("state.records.find(r=>r.key==='main:c').registration.aliases[0].word"),'下の名前');
  assert.equal(ids.get('edit-name').value,'候補1');
  // An unfinished reading is kept on screen, blocks navigation, and survives reload.
  ids.get('edit-reading').value=''; await ids.get('edit-reading').fire('input');
  const beforeRequests=json('requests.length');
  await assert.rejects(run("select('main:b')"),/ひらがな/);
  assert.equal(json('selected'),'main:a');
  assert.equal(ids.get('edit-reading').value,'');
  assert.equal(json('requests.length'),beforeRequests);
  assert.equal(ids.get('edit-errors').hidden,false);
  run('editDirty=false;editKey=undefined;selected="main:b";render();restoreInlineEdit();');
  assert.equal(json('selected'),'main:a');
  assert.equal(ids.get('edit-reading').value,'');
  assert.equal(json('editDirty'),true);
  ids.get('filter').value='採用'; await ids.get('filter').fire('change');
  assert.equal(ids.get('filter').value,'all');
  assert.equal(json('selected'),'main:a');
  await ids.get('revert-edit').fire('click');
  assert.equal(ids.get('edit-reading').value,'よみ');
  assert.equal(json('editDirty'),false);
  // Unused rows never become dictionary conversions; IME composition is not saved mid-word.
  assert.equal(json('validInlineForm(rawEdit()).aliases.length'),0);
  await ids.get('edit-form').fire('compositionstart');
  ids.get('edit-reading').value='romaji'; await ids.get('edit-reading').fire('input');
  assert.equal(json('editComposing'),true);
  await run('commitEdit()');
  assert.equal(json('requests.length'),beforeRequests);
  ids.get('edit-reading').value='かな'; await ids.get('edit-form').fire('compositionend');
  await run('flush()');
  assert.equal(json('current().registration.reading'),'かな');
  assert.equal(json('editDirty'),false);
  // A slow save must not overwrite newer text entered during the request.
  run('api=async(route,body)=>{requests.push({route,body});return new Promise(resolve=>{resolveSlowSave=resolve;});};');
  ids.get('edit-name').value='保存中の名前'; await ids.get('edit-name').fire('input');
  const slowSave=run('commitEdit()');
  await new Promise(resolve=>setImmediate(resolve));
  ids.get('edit-name').value='続けて入力した名前'; await ids.get('edit-name').fire('input');
  run('resolveSlowSave({revision:state.revision+1,saved_at:"now"});'); await slowSave;
  assert.equal(ids.get('edit-name').value,'続けて入力した名前');
  assert.equal(json('editDirty'),true);
  run('api=async(route,body)=>{requests.push({route,body});return {revision:state.revision+1,saved_at:"now"};};');
  await run('flush()');
  assert.equal(json('current().registration.name'),'続けて入力した名前');
  assert.equal(json('editDirty'),false);
  assert.equal(ids.get('save-status').textContent,'自動保存済み');
  // Finalizing a browser review saves candidates; every visible action says so.
  run(`state=${JSON.stringify(state)}; selected='main:b'; editKey=undefined; editDirty=false;
    dirty=false; saving=Promise.resolve(); requests=[];
    api=async(route,body)=>{
      requests.push({route,body});
      if(route==='preview') return {token:'reviewed',conversions:[{reading:'よみ',word:'候補2'}],errors:[],
        counts:{'採用':1,'保留':1,'不採用':0,'未判断':1}};
      if(route==='apply') return {result:{target:'candidate_db',saved_decisions:2,added_words:0},
        state:{...state,complete:{target:'candidate_db',saved_decisions:2,added_words:0}}};
      if(route==='next') return {...state,batch_id:'next',complete:null,records:state.records.filter(r=>r.decision==='未判断')};
      throw Error('Unexpected action: '+route);
    }; render();`);
  await run('openPreview()');
  assert.equal(ids.get('apply').textContent,'候補DBに保存する');
  assert.match(ids.get('preview-description').textContent,/候補DBに保存/);
  await run('apply()');
  assert.match(ids.get('success').textContent,/2件を候補DBに保存しました/);
  assert.equal(json('state.dictionary_count'),1);
  assert.deepEqual(json('requests.map(r=>r.route)'),['preview','apply','next']);
  assert.equal(ids.has('copy-publish'),false);
  assert.equal(ids.has('copy-request'),true);
  assert.ok(!html.includes('このPCの辞書・配布ファイル・サイトを更新'));
  console.log('OK: candidate DB completion, bulk selection, sequential review, inline autosave, aliases, navigation guards, draft recovery, IME composition');
})().catch(error=>{console.error(error);process.exitCode=1;});
