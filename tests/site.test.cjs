// Run the actual app in a small DOM/worker harness, without a browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../site/app.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));

function fixture() {
  return {version:'fixture-version', count:1, updatedAt:'2026-10-05T00:00:00+09:00',
    dateMeaning:'applied_on', data:'../files/fixture-version/search.json',
    zip:'../files/fixture-version/F-VTD-fixture-version.zip',
    hashes:{'search.json':'fixture-search-hash', zip:'fixture-zip-hash'},
    publicationStatus:'unconfirmed', publishedAt:null,
    changes:[{id:'fixture-update', date:'2026-10-05', appliedOn:'2026-10-05', scope:'applied',
      title:'架空更新', publishedIn:[], added:[], corrected:[], undone:[], annotations:[],
      removed:[{id:'fixture-change', before:{word:'架空甲', reading:'かこうこう',
        google_pos:'人名', microsoft_pos:'人名', atok_pos:'人名', origin:'upstream'}, after:null,
        reason:'架空の確認理由', checked_on:'2026-10-05', related_ids:[],
        evidence:{text:null, urls:['https://example.test/evidence']}}]}]};
}

class Element {
  constructor(){this.children=[];this.textContent='';this.hidden=false;this.disabled=false;}
  append(...values){this.children.push(...values);}
  replaceChildren(...values){this.children=values;}
  setAttribute(){}
  removeAttribute(){}
}
function textContent(node) {
  return typeof node === 'string' ? node : node.textContent + node.children.map(textContent).join(' ');
}

async function app(initial) {
  const nodes = new Map();
  const get = id => {if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  let current = clone(initial), workers = 0;
  class Worker {
    constructor(){workers++;}
    postMessage(){queueMicrotask(()=>this.onmessage({data:{type:'ready'}}));}
    terminate(){}
  }
  const context = vm.createContext({URL, AbortSignal, setTimeout, clearTimeout, Worker,
    setInterval(){}, location:{href:'https://example.test/'},
    localStorage:{getItem(){return null;},setItem(){}},
    document:{hidden:false, getElementById:get, createElement(){return new Element();},
      querySelector:get, addEventListener(){}},
    fetch:async url=>({ok:true,json:async()=>{
      if(url==='/config.json')return {manifestUrl:'https://example.test/data/latest.json',demo:true};
      if(String(url).endsWith('/search.json'))return [{word:'架空乙',reading:'かこうおつ',excluded:[]}];
      return clone(current);
    }}),
  });
  await vm.runInContext(source,context);
  return {get, context, workers:()=>workers, manifest:value=>{current=clone(value);},
    check:()=>vm.runInContext('check()',context)};
}

test('a malformed historical evidence link does not disable dictionary search', async()=>{
  const initial=fixture();
  initial.changes[0].removed[0].evidence.urls=['https://'];
  const screen=await app(initial);
  assert.equal(vm.runInContext('ready',screen.context),true);
  assert.equal(screen.get('search-button').disabled,false);
  assert.match(textContent(screen.get('changelog')),/リンク形式を確認してください/);
});

test('publication-only updates refresh the current screen without a new search worker', async()=>{
  const initial=fixture(), screen=await app(initial);
  assert.match(screen.get('version').textContent,/公開確認記録なし/);
  const confirmed=clone(initial);
  confirmed.publicationStatus='confirmed';
  confirmed.publishedAt='2026-10-05T12:00:00+09:00';
  confirmed.changes[0].publishedIn=[initial.version];
  confirmed.changes[0].publicationStatus='confirmed';
  confirmed.changes[0].removed[0].publishedIn=[initial.version];
  screen.manifest(confirmed);
  await screen.check();
  assert.match(screen.get('version').textContent,/公開確認済み/);
  assert.match(textContent(screen.get('changelog')),/公開確認版：fixture-version/);
  assert.equal(screen.workers(),1);
  assert.equal(screen.get('search-button').disabled,false);
  assert.equal(vm.runInContext('manifest.publicationStatus',screen.context),'confirmed');
});

test('a different edition remains pending until the user chooses to load it', async()=>{
  const initial=fixture(), screen=await app(initial);
  screen.manifest({...initial,version:'next-version'});
  await screen.check();
  assert.equal(screen.get('update-banner').hidden,false);
  assert.equal(vm.runInContext('manifest.version',screen.context),initial.version);
  assert.equal(screen.workers(),1);
});

test('same-version changes to dictionary URLs are not applied as metadata', async()=>{
  const initial=fixture(), screen=await app(initial);
  screen.manifest({...initial,publicationStatus:'confirmed',data:'../files/other/search.json'});
  await screen.check();
  assert.equal(vm.runInContext('manifest.publicationStatus',screen.context),'unconfirmed');
  assert.equal(screen.workers(),1);
  assert.match(screen.get('version').textContent,/公開確認記録なし/);
});

test('same-version history changes are not accepted as publication updates', async()=>{
  const initial=fixture(), screen=await app(initial), changed=clone(initial);
  changed.publicationStatus='confirmed';
  changed.changes[0].removed[0].reason='置き換わった理由';
  screen.manifest(changed);
  await screen.check();
  assert.equal(vm.runInContext('manifest.publicationStatus',screen.context),'unconfirmed');
  assert.doesNotMatch(textContent(screen.get('changelog')),/置き換わった理由/);
  assert.equal(screen.workers(),1);
});
