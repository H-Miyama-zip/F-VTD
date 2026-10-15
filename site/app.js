const $=id=>document.getElementById(id);let config,manifest,pending,worker,ready=false,current='',page=1,requestId=0,history=[],save=true;
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
const safeUrl=(s,base=location.href)=>{const u=new URL(s,base);if(!['https:','http:'].includes(u.protocol))throw Error('Invalid URL');return u.href};
async function getJson(url){const r=await fetch(url,{cache:'no-cache',signal:AbortSignal.timeout(20000)});if(!r.ok)throw Error('取得失敗');return r.json()}
try{save=localStorage.getItem('fvtd-save')!=='false';const h=JSON.parse(localStorage.getItem('fvtd-history')||'[]');history=save&&Array.isArray(h)?h.filter(x=>typeof x==='string').slice(0,20):[]}catch{$('storage-note').textContent='このブラウザでは履歴を保存できません。'}
$('save-history').checked=save;
function persist(){try{localStorage.setItem('fvtd-save',String(save));localStorage.setItem('fvtd-history',JSON.stringify(history))}catch{$('storage-note').textContent='履歴はこの画面を閉じるまでのみ保持されます。'}}
function renderHistory(){$('history').replaceChildren();if(!history.length){$('history').append(el('span',save?'検索すると、ここに履歴が表示されます。':'履歴の保存はオフです。','hint'));return}for(const q of history){const chip=el('span',undefined,'chip');const b=el('button',q);b.onclick=()=>{$('query').value=q;search(q)};const rm=el('button','×','remove');rm.setAttribute('aria-label',q+'を履歴から削除');rm.onclick=()=>{history=history.filter(x=>x!==q);persist();renderHistory()};chip.append(b,rm);$('history').append(chip)}}
$('save-history').onchange=e=>{save=e.target.checked;if(!save)history=[];persist();renderHistory()};$('clear-history').onclick=()=>{history=[];persist();renderHistory()};renderHistory();
function card(r){const n=el('article',undefined,'result-card');n.append(el('strong',r.word),el('div',r.reading,'reading'));for(const x of r.excluded||[])n.append(el('div',x.ime+'向け辞書には含まれません：'+x.reason,'warning'));return n}
function requestBox(q){const p=config.formPrefill,u=new URL(safeUrl(config.formUrl));u.searchParams.set('usp','pp_url');u.searchParams.set('entry.'+p.type,p.newType);u.searchParams.set('entry.'+(/^[ぁ-ゖー]+$/.test(q)?p.reading:p.name),q);const box=el('div',undefined,'request-box'),a=el('a','「'+q+'」の収録を依頼する ↗');a.href=u.href;a.target='_blank';a.rel='noopener';box.append(el('p','「'+q+'」に完全一致する登録語はありません。未収録のVTuberであれば、フォームから収録を依頼できます（入力した語がフォームに記入された状態で開きます）。'),a);return box}
function search(q,p=1,remember=true){if(!q.length||!ready)return;current=q;page=p;if(remember&&save){history=[q,...history.filter(x=>x!==q)].slice(0,20);persist();renderHistory()}$('status').textContent='検索しています…';worker.postMessage({type:'search',q,page:p,id:++requestId})}
$('search-form').onsubmit=e=>{e.preventDefault();search($('query').value)};
function showResults(r){if(r.id!==requestId)return;$('exact').replaceChildren();$('candidates').replaceChildren();$('status').textContent=r.exact.length?'完全一致する登録語があります。':'完全一致する登録語はありません。';if(!r.exact.length&&config.formUrl&&config.formPrefill)$('exact').append(requestBox(r.q));if(r.exact.length){const box=el('div',undefined,'exact-box');box.append(el('h3','✓ 完全一致 · '+r.exact.length+'件'));for(const row of r.exact)box.append(card(row));$('exact').append(box)}$('result-count').textContent='部分一致 '+r.total.toLocaleString()+'件';for(const row of r.items)$('candidates').append(card(row));if(!r.items.length&&!r.exact.length)$('candidates').append(el('p','別の名前や読み、短い文字列で検索してみてください。','hint'));renderPagination(r)}
// Keep pagination controls alive; only move focus if the focused control becomes disabled.
const prevPage=el('button','前へ'),nextPage=el('button','次へ'),pageInfo=el('span');
pageInfo.setAttribute('role','status');pageInfo.setAttribute('aria-live','polite');pageInfo.tabIndex=-1;
prevPage.onclick=()=>search(current,page-1,false);nextPage.onclick=()=>search(current,page+1,false);
$('pagination').append(prevPage,pageInfo,nextPage);$('pagination').hidden=true;
function renderPagination(r){
  const active=document.activeElement, pages=Math.ceil(r.total/20);
  $('pagination').hidden=r.total<=20;prevPage.disabled=r.page===1;nextPage.disabled=r.page>=pages;
  pageInfo.textContent=r.total>20?r.page+' / '+pages+'ページ · '+((r.page-1)*20+1)+'〜'+Math.min(r.page*20,r.total)+'件':'';
  if((active===prevPage||active===nextPage)&&(active.disabled||r.total<=20)){
    (r.total<=20?$('result-heading'):(active===nextPage?prevPage:nextPage)).focus();
  }
}
function youtubeChannelUrl(value){
  if(typeof value!=='string')return null;
  try{
    const u=new URL(value);
    if(!['https:','http:'].includes(u.protocol)||!['youtube.com','www.youtube.com','m.youtube.com'].includes(u.hostname)||u.username||u.password||u.port)return null;
    const channel=u.pathname.match(/^\/(?:channel\/[^/]+|c\/[^/]+|user\/[^/]+|@[^/]+)(?=\/|$)/);
    return channel?'https://www.youtube.com'+channel[0]:null;
  }catch{return null}
}
const historyGroups=[['added','追加した名前'],['corrected','変更した名前'],['removed','削除した名前'],['undone','変更を取り消した名前'],['annotations','記録を更新した名前']];
function historyNames(items,group,records,legacy){
  const names=new Map();
  for(const item of items){
    const change=group==='annotations'?records.get(item.related_ids[0]):item;
    const row=legacy?item:change?.after||change?.before;
    if(!row&&group!=='annotations')throw Error('History row missing');
    if(row&&(typeof row.word!=='string'||typeof row.reading!=='string'))throw Error('History row invalid');
    const key=row?.word||'\0'+item.id;
    if(!names.has(key))names.set(key,{word:row?.word,readings:new Set(),previousNames:new Set(),youtube:null});
    const name=names.get(key);
    if(row)name.readings.add(row.reading);
    if(!legacy&&change?.before&&change.before.word!==row.word)name.previousNames.add(change.before.word);
    const urls=[row?.source_url,...(change?.evidence?.urls||[]),change?.before?.source_url];
    for(const url of urls)name.youtube||=youtubeChannelUrl(url);
  }
  const sorted=[...names.values()];
  for(const name of sorted)name.readings=[...name.readings].sort((a,b)=>b.length-a.length||a.localeCompare(b,'ja'));
  return sorted.sort((a,b)=>(a.readings[0]||a.word||'').localeCompare(b.readings[0]||b.word||'','ja')||(a.word||'').localeCompare(b.word||'','ja'));
}
function historyNameList(names){
  const list=el('ul',undefined,'change-name-list');
  for(const name of names){
    const item=el('li');item.append(el('strong',name.word||'名前不明','change-name'));
    if(name.previousNames.size)item.append(el('span','変更前：'+[...name.previousNames].join(' / '),'change-previous-name'));
    if(name.readings.length)item.append(el('span',name.readings.join(' / '),'change-readings'));
    if(name.youtube){
      const link=el('a','YouTube ↗','change-youtube');link.href=name.youtube;link.target='_blank';link.rel='noopener';
      link.setAttribute('aria-label',name.word+'のYouTubeチャンネルを開く');item.append(link);
    }
    list.append(item);
  }
  return list;
}
function appendHistoryNames(body,change,records,legacy=false){
  for(const[group,label]of historyGroups){
    const items=change[group]||[];
    if(!items.length)continue;
    const names=historyNames(items,group,records,legacy),section=el('section',undefined,'change-name-group');
    section.append(el('h3',label+'（'+names.length+'件）'),historyNameList(names.slice(0,12)));
    if(names.length>12){
      const more=el('details',undefined,'change-more');
      more.append(el('summary','残り'+(names.length-12)+'件の名前を見る'),historyNameList(names.slice(12)));
      section.append(more);
    }
    body.append(section);
  }
}
// Build every history node before replacing the validated view.
function prepareManifestView(m){
  if(!m||typeof m.version!=='string'||typeof m.data!=='string'||typeof m.zip!=='string'||
     !Number.isInteger(m.count)||!Array.isArray(m.changes)||!Number.isFinite(Date.parse(m.updatedAt)))throw Error('Manifest invalid');
  const downloadUrl=safeUrl(m.zip,config.manifestUrl),ledgerFormat=m.ledgerChanges!==undefined;
  const changes=ledgerFormat?m.ledgerChanges:m.changes,historyNodes=[];
  if(!Array.isArray(changes))throw Error('History invalid');
  const records=new Map();
  for(const change of changes)for(const[group]of historyGroups)for(const item of change[group]||[])if(item.id)records.set(item.id,item);
  const dateLabel={legacy_added_on:'旧記録の追加日',applied_on:'適用日',upstream_date:'原版の日付'}[m.dateMeaning]||'日付';
  const publication=m.publicationStatus===undefined?'旧形式（公開確認情報なし）':
    m.publicationStatus==='confirmed'?'公開確認済み（'+m.publishedAt+'）':'公開確認記録なし';
  const versionText=m.version+' · '+m.count.toLocaleString()+'語 · '+dateLabel+' '+m.updatedAt.slice(0,10)+' · '+publication;
  for(const c of changes.slice(0,5)){
    const box=el('article',undefined,'change'),body=el('div',undefined,'change-body');
    box.append(el('time',c.date||'日付不明'));
    if(!ledgerFormat && !('scope' in c)){
      if(!Array.isArray(c.added)||!Array.isArray(c.removed))throw Error('Legacy history invalid');
      appendHistoryNames(body,c,records,true);
      box.append(body);historyNodes.push(box);continue;
    }
    if(!['reconstructed','applied'].includes(c.scope)||
       !['added','corrected','removed','undone','annotations'].every(group=>Array.isArray(c[group])))throw Error('Ledger history invalid');
    appendHistoryNames(body,c,records);
    box.append(body);historyNodes.push(box);
  }
  return {entryCount:m.count.toLocaleString()+'語',downloadUrl,downloadName:'F-VTD-'+m.version+'.zip',
    downloadText:config.demo?'↓ 確認用ZIPをダウンロード':'↓ すべての辞書をダウンロード',versionText,historyNodes};
}
function applyManifestView(view){
  $('entry-count').textContent=view.entryCount;
  $('download').href=view.downloadUrl;$('download').download=view.downloadName;
  $('download').removeAttribute('aria-disabled');$('download').textContent=view.downloadText;
  $('download-top').href=view.downloadUrl;$('download-top').download=view.downloadName;
  $('version').textContent=view.versionText;$('changelog').replaceChildren(...view.historyNodes);
}
function renderManifest(m){applyManifestView(prepareManifestView(m))}
async function load(m){
  const view=prepareManifestView(m),rows=await getJson(safeUrl(m.data,config.manifestUrl));
  if(!Array.isArray(rows)||rows.length!==m.count||rows.some(r=>typeof r.word!=='string'||typeof r.reading!=='string'||(r.excluded&&!Array.isArray(r.excluded))))throw Error('データ形式不正');
  rows.sort((a,b)=>a.reading.localeCompare(b.reading,'ja')||a.word.localeCompare(b.word,'ja'));
  const next=new Worker('/search-worker.js');
  try{
    await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(Error('検索準備タイムアウト')),20000);
      const finish=error=>{clearTimeout(timeout);error?reject(error):resolve()};
      next.onerror=()=>finish(Error('検索準備失敗'));
      next.onmessage=e=>{if(e.data.type==='ready')finish()};
      try{next.postMessage({type:'load',rows})}catch(error){finish(error)}
    });
    applyManifestView(view);
  }catch(error){next.terminate();throw error}
  // Only retire the previous search snapshot after the replacement is ready.
  worker?.terminate();worker=next;worker.onmessage=e=>showResults(e.data);manifest=m;ready=true;
  $('search-button').disabled=false;$('retry').hidden=true;
  $('status').textContent='名前や読みを入力して、収録状況を確認できます。';
  if(current)search(current,1,false);
}
function editionSnapshot(m){const value=JSON.parse(JSON.stringify(m));delete value.publicationStatus;delete value.publishedAt;for(const update of value.ledgerChanges||value.changes){delete update.publishedIn;delete update.publicationStatus;for(const group of ['added','corrected','removed','undone','annotations'])for(const item of update[group]||[])delete item.publishedIn}return JSON.stringify(value)}
async function check(){if(document.hidden||!manifest)return;try{const m=await getJson(config.manifestUrl);if(m.version!==manifest.version){pending=m;$('update-banner').hidden=false}else if(JSON.stringify(m)!==JSON.stringify(manifest)){if(editionSnapshot(m)!==editionSnapshot(manifest))throw Error('Same-version edition changed');renderManifest(m);manifest=m}}catch{/* Keep last validated snapshot. */}}
$('apply-update').onclick=async()=>{if(!pending)return;$('apply-update').disabled=true;try{await load(pending);pending=null;$('update-banner').hidden=true}catch{$('status').textContent='更新を取得できませんでした。現在の版を引き続き利用できます。'}finally{$('apply-update').disabled=false}};
async function init(){$('search-button').disabled=true;try{config=await getJson('/config.json');config.manifestUrl=safeUrl(config.manifestUrl);if(!config.demo){document.querySelector('.demo').hidden=true;document.querySelector('.download-note').textContent='ZIP内の案内・利用条件をご確認ください。'}for(const[id,url,text]of [['repo-link',config.githubUrl,'GitHub ↗'],['past-link',config.githubUrl?config.githubUrl+'/blob/main/data/ledger.jsonl':'','過去の変更をGitHubで見る ↗']])if(url){const a=el('a',text);a.href=safeUrl(url);a.rel='noopener';$(id).replaceChildren(a)}if(config.issuesUrl||config.formUrl){$('contact-links').replaceChildren();for(const[url,text]of [[config.issuesUrl,'GitHub Issues ↗'],[config.formUrl,'Googleフォーム ↗']])if(url){const a=el('a',text);a.href=safeUrl(url);$('contact-links').append(a)}}await load(await getJson(config.manifestUrl))}catch{$('status').textContent='辞書を読み込めませんでした。未収録の判定は行っていません。再読み込みをお試しください。';$('retry').hidden=false}}
$('retry').onclick=init;setInterval(check,300000);document.addEventListener('visibilitychange',check);
for(const[name,url]of [['Microsoft IME','https://support.microsoft.com/ja-jp/windows/hardware/input-devices/microsoft-japanese-ime'],['Google日本語入力','https://www.google.co.jp/ime/'],['Mozc','https://github.com/google/mozc'],['ATOK','https://atok.com/useful/'],['macOSテキスト置換（plist）','https://support.apple.com/ja-jp/guide/mac-help/mchl2a7bd795/mac']]){const a=el('a',name+' ↗');a.href=url;a.target='_blank';a.rel='noopener';$('help-links').append(a)}
init();
