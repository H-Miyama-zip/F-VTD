'use strict';
const $ = id => document.getElementById(id);
const elements = [...document.querySelectorAll('[data-decision]')];
let state, sessionToken, selected, preview, busy = false, saving = Promise.resolve(), memoTimer, dirty = false, stopped = false;
let completionMessage = '';
let editKey, editDirty = false, catalogKind, catalogRecords = [];
let editTimer, editComposing = false, rawEditBase;
let renderedFilter='未判断';
let catalogSelected = new Set(), reviewKeys = [];
const draftKey = 'f-vtd-daily-draft';
const reviewKey = 'f-vtd-daily-review-selection';
const inlineDraftKey = 'f-vtd-daily-inline-edit';
const decisionLabels = {'未判断':'未確認','採用':'追加予定','保留':'あとで確認','不採用':'追加しない'};
const decisionLabel = decision => decisionLabels[decision];

async function api(route, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-App-Token':sessionToken},body:JSON.stringify(body)};
  const response = await fetch('/api/' + route, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作を完了できませんでした。');
  return result;
}
const version = () => ({batch_id:state.batch_id, revision:state.revision});
const entries = () => state.records.map(r => ({key:r.key, decision:r.decision, memo:r.memo, ...(r.registration ? {registration:r.registration} : {})}));
const registration = r => r.registration || {name:r.name, reading:r.reading, aliases:[]};
const canonical = rows => JSON.stringify([...rows].sort((a,b)=>a.key.localeCompare(b.key)));
const current = () => state.records.find(r => r.key === selected);
const visible = () => $('filter').value === 'picked'
  ? reviewKeys.map(key=>state.records.find(r=>r.key===key)).filter(Boolean)
  : state.records.filter(r => $('filter').value === 'all' || r.decision === $('filter').value);
function rememberReview() {
  try {localStorage.setItem(reviewKey,JSON.stringify({batch_id:state.batch_id,keys:reviewKeys,selected,active:$('filter').value==='picked'}));} catch {}
}
function restoreReview() {
  reviewKeys=[];
  try {
    const saved=JSON.parse(localStorage.getItem(reviewKey));
    if(saved?.batch_id===state.batch_id && Array.isArray(saved.keys)) {
      reviewKeys=[...new Set(saved.keys)].filter(key=>state.records.some(r=>r.key===key));
      if(saved.active && reviewKeys.length) {$('filter').value='picked'; selected=reviewKeys.includes(saved.selected)?saved.selected:reviewKeys[0];}
    } else if($('filter').value==='picked') $('filter').value='未判断';
  } catch {if($('filter').value==='picked') $('filter').value='未判断';}
}

function error(error) {
  $('error-text').textContent = error.message || String(error);
  $('error').hidden = false;
  $('save-status').textContent = dirty ? '保存できていません' : '操作を確認してください';
  $('save-status').classList.add('failed');
}
function clearError() { $('error').hidden = true; $('save-status').classList.remove('failed'); }
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(()=>{$('toast').hidden=true;},3000); }
function persistDraft() { try { localStorage.setItem(draftKey, JSON.stringify({...version(), entries:entries()})); } catch {} }
function removeDraft() { try { localStorage.removeItem(draftKey); } catch {} }
function savedAt() { $('save-status').textContent = editDirty?'名前・読みは入力中':'自動保存済み'; $('save-status').classList.remove('failed'); }

function save() {
  const snapshot = entries();
  dirty = true;
  persistDraft();
  $('save-status').textContent = '保存中…';
  preview = null;
  const job = saving.catch(()=>{}).then(async () => {
    const result = await api('save', {...version(), entries:snapshot});
    state.revision = result.revision;
    state.saved_at = result.saved_at;
    dirty = canonical(entries()) !== canonical(snapshot);
    if(result.state) {
      const local = new Map(state.records.map(r=>[r.key,r]));
      state = {...result.state, records:result.state.records.map(r=>local.has(r.key)?{...r,decision:local.get(r.key).decision,memo:local.get(r.key).memo,...(local.get(r.key).registration?{registration:local.get(r.key).registration}:{})}:r)};
    }
    if (!dirty) { removeDraft(); savedAt(); } else persistDraft();
    if(editKey) {
      const entry=snapshot.find(r=>r.key===editKey), record=state.records.find(r=>r.key===editKey);
      if(entry&&record) rawEditBase=JSON.stringify(entry.registration||{name:record.name,reading:record.reading,aliases:[]});
      if(editDirty) persistInlineEdit();
    }
    return result;
  });
  saving = job;
  job.catch(error);
  return job;
}
async function flush() {
  clearTimeout(editTimer); editTimer=undefined;
  if(!await commitEdit()) throw new Error($('edit-errors').textContent);
  if(editDirty) throw new Error('入力が変わりました。文字を確定し、保存が済んでからもう一度操作してください。');
  if (memoTimer) { clearTimeout(memoTimer); memoTimer = undefined; save(); }
  await saving;
  if (dirty) throw new Error('まだ保存できていない入力があります。通信が戻ってから再度操作してください。');
}
function controls() {
  const complete = Boolean(state?.complete);
  const judged = state ? state.records.filter(r => r.decision !== '未判断').length : 0;
  for (const button of elements) button.disabled = busy || complete;
  $('reset-decision').disabled = busy || complete || !current() || current().decision === '未判断';
  $('memo').disabled = busy || complete;
  $('filter').disabled = busy;
  if(state) {
    const records=visible(), index=records.findIndex(r=>r.key===selected);
    $('previous').disabled=busy||index<=0;
    $('next-candidate').disabled=busy||index<0||index>=records.length-1;
  }
  $('preview').disabled = busy || judged === 0 || dirty || editDirty;
  $('new-batch').disabled = busy;
  $('apply').disabled = busy || !preview?.token || Boolean(preview?.errors?.length);
  $('close-preview').disabled = busy;
  $('back-to-review').disabled = busy;
  for(const id of ['show-pending','show-held','close-catalog','catalog-select-all','catalog-clear','leave-review']) $(id).disabled=busy;
  $('recall-selected').disabled=busy||catalogSelected.size===0;
  $('catalog-search').disabled=busy;
  for(const input of $('catalog-list').querySelectorAll('input')) input.disabled=busy;
  $('add-alias').disabled=busy||complete||!current();
  $('revert-edit').disabled=busy||complete||!editDirty;
  for(const button of $('catalog-list').querySelectorAll('button')) button.disabled=busy;
  for(const input of $('edit-form').querySelectorAll('input')) input.disabled=busy||complete;
  for(const button of $('alias-rows').querySelectorAll('button')) button.disabled=busy||complete;
}
function make(tag, className, text) { const node = document.createElement(tag); if(className) node.className=className; if(text!==undefined) node.textContent=text; return node; }

function renderList() {
  renderedFilter=$('filter').value;
  $('picked-option').hidden=!reviewKeys.length;
  const picked=$('filter').value==='picked';
  $('review-scope').hidden=!picked;
  $('review-scope-text').textContent=`呼び出した${visible().length}件を確認中`;
  $('list-footer').textContent=picked?'呼び出した候補だけを表示しています。前後ボタンで順に見直せます。':'候補はランダムに補充します。確認済みの語は「すべて」から見られます。';
  const list = $('candidate-list'); list.replaceChildren();
  const classes = {'未判断':'','採用':'adopt','保留':'hold','不採用':'reject'};
  for (const record of visible()) {
    const form = registration(record);
    const button = make('button','candidate-item' + (record.key===selected?' active':''));
    button.type='button'; button.setAttribute('aria-current',record.key===selected?'true':'false');
    button.setAttribute('aria-label',`${form.name}、${form.reading}、${decisionLabel(record.decision)}`);
    const number = String((picked?visible():state.records).indexOf(record)+1).padStart(2,'0');
    button.append(make('span','candidate-number',number));
    const text = make('span','candidate-text'); text.append(make('span','candidate-name',form.name),make('span','candidate-reading',form.reading));
    button.append(text,make('span','candidate-status '+classes[record.decision],decisionLabel(record.decision)));
    button.addEventListener('click',()=>select(record.key).catch(error)); list.append(button);
  }
  $('list-count').textContent = visible().length;
  list.querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest'});
}
function renderSummary() {
  const counts = Object.fromEntries(['未判断','採用','保留','不採用'].map(label=>[label,state.records.filter(r=>r.decision===label).length]));
  $('dictionary-count').textContent = state.dictionary_count.toLocaleString('ja-JP');
  $('judged-count').textContent = state.records.length - counts['未判断'];
  $('goal-denominator').textContent = '（目安10件）';
  $('progress-bar').style.width = Math.min(100,(state.records.length-counts['未判断'])/10*100)+'%';
  $('adopt-count').textContent = counts['採用']; $('hold-count').textContent = counts['保留']; $('reject-count').textContent = counts['不採用']; $('pending-count').textContent = counts['未判断'];
  $('queue-label').textContent = '追加予定';
  const adopted = $('adopt-list'); adopted.replaceChildren();
  if(!counts['採用']) adopted.append(make('div','empty-adopt','まだ選んでいません'));
  for(const record of state.records.filter(r=>r.decision==='採用')) {
    const form=registration(record), row = make('button','adopt-entry'); row.type='button';
    row.append(make('span','','✓'),make('strong','',form.name + (form.aliases.length ? `（別の変換 ${form.aliases.length}件）` : '')));
    row.disabled=busy; row.addEventListener('click',async()=>{try {await flush(); $('filter').value='採用'; await select(record.key);} catch(e) {error(e);}}); adopted.append(row);
  }
  $('preview').hidden = Boolean(state.complete); $('new-batch').hidden = !state.complete || (state.pool_exhausted && !counts['未判断']);
  $('preview').textContent = counts['採用'] ? `追加予定の${counts['採用']}件を確認` : counts['保留'] + counts['不採用'] ? '選択内容を確認して完了' : '追加内容を確認';
  $('summary-note').textContent = state.complete ? '候補の保存は完了しました。辞書への追加は、Codexへの依頼で進めます。' : state.pool_exhausted ? '新しい候補はすべて表示済みです。残りを確認し、候補DBに保存できます。' : '確認するたびに、未確認の候補をランダムに補充します。区切りのよいところで候補DBに保存できます。';
  $('success').hidden = !state.complete && !completionMessage;
  $('success').textContent = completionMessage || (state.complete?.target === 'candidate_db' ? `${state.complete.saved_decisions}件を候補DBに保存しました。` : '過去の作業は完了しています。');
  const history = $('history'); history.replaceChildren();
  if (!state.history.length) history.append(make('p','muted','候補DBへの保存をここに記録します。'));
  for(const item of state.history) { const row=make('div','history-row'); row.append(make('span','',item.date.slice(5).replace('-',' / ')),make('strong','',item.target==='candidate_db'?`候補保存 ${item.saved}件`:`過去の辞書追加 ${item.added}語`)); history.append(row); }
  controls();
}
function renderDetail() {
  const record = current();
  $('review-empty').hidden = Boolean(record); $('review-body').hidden = !record;
  if(!record) return;
  const records=visible();
  $('position').textContent = `${records.findIndex(r=>r.key===selected)+1} / ${records.length}`;
  $('decision-help-text').textContent=$('filter').value==='picked'?'選ぶと保存して、呼び出した次の候補へ進みます。':'選ぶと保存して、次の未確認へ進みます。';
  const form=registration(record);
  $('review-name').textContent = form.name+'の名前と読み';
  if(editKey!==record.key) fillEdit(record);
  $('current-decision').textContent = decisionLabel(record.decision);
  $('evidence-text').textContent = record.evidence_summary;
  const source = $('source-link'); source.href = record.source_url;
  source.title = record.source_url;
  const channel = $('channel-link'); const id=record.youtube_channel_id;
  channel.hidden = !/^UC[A-Za-z0-9_-]{22}$/.test(id);
  if(!channel.hidden) channel.href = 'https://www.youtube.com/channel/' + id;
  if($('memo').value!==record.memo) $('memo').value = record.memo;
  for(const button of elements) button.setAttribute('aria-pressed',String(button.dataset.decision===record.decision));
  const index = visible().findIndex(r=>r.key===selected);
  $('previous').disabled = busy || index<=0; $('next-candidate').disabled = busy || index<0 || index>=visible().length-1;
  controls();
}
function render() { renderList(); renderSummary(); renderDetail(); }
async function select(key) {
  if(busy) return; busy=true; controls();
  try {await flush(); selected=key; rememberReview();}
  finally {busy=false; render();}
}
async function decide(decision) {
  if(busy || state.complete || !current()) return;
  busy=true; controls();
  try {
    clearError(); await flush();
    completionMessage = '';
    const record = current(); record.decision=decision;
    render();
    await save();
    if(decision!=='未判断') toast(`「${registration(record).name}」を「${decisionLabels[decision]}」にしました。`);
    const index=state.records.findIndex(r=>r.key===record.key);
    if(decision!=='未判断') {
      const sequence=visible(), position=sequence.findIndex(r=>r.key===record.key);
      const next=$('filter').value==='picked' ? sequence[position+1]
        : [...state.records.slice(index+1),...state.records.slice(0,index)].find(r=>r.decision==='未判断' && ($('filter').value==='all'||$('filter').value===r.decision));
      if(next) selected=next.key;
      else if(!visible().some(r=>r.key===selected)) selected=visible()[0]?.key;
      rememberReview();
    }
  } finally { busy=false; render(); }
}
async function load() {
  state = await api('state');
  editKey=undefined; editDirty=false;
  if(state.complete && (!state.pool_exhausted || state.records.some(r=>r.decision==='未判断'))) {
    try { state=await api('next',{...version(),count:10}); }
    catch(e) { error(e); }
  }
  selected = state.records.find(r=>r.decision==='未判断')?.key || state.records[0]?.key;
  restoreReview();
  const date = new Date(state.date+'T12:00:00+09:00');
  $('date').textContent = date.toLocaleDateString('ja-JP',{year:'numeric',month:'long',day:'numeric',weekday:'short'}) + 'の候補';
  savedAt(); render();
  let draft;
  try { draft = JSON.parse(localStorage.getItem(draftKey)); } catch {}
  if(draft && Array.isArray(draft.entries) && draft.batch_id===state.batch_id && !state.complete && canonical(draft.entries)!==canonical(entries())) {
    if(draft.revision!==state.revision) { error(new Error('このブラウザーに未保存の入力がありますが、別の画面で判断が更新されています。入力を上書きせず保管しています。')); return; }
    if(draft.entries.length!==state.records.length || draft.entries.some(e=>!state.records.some(r=>r.key===e.key))) return;
    for(const entry of draft.entries) { const record=state.records.find(r=>r.key===entry.key); record.decision=entry.decision; record.memo=entry.memo; if(entry.registration) record.registration=entry.registration; }
    editKey=undefined; render(); await save(); renderSummary(); toast('前回の未保存の入力を復元しました。');
  }
  restoreInlineEdit();
}
async function openPreview() {
  if(busy || state.complete) return;
  clearError(); busy=true; controls();
  try {
    await flush();
    preview=await api('preview',version());
    $('preview-rows').replaceChildren();
    for(const row of preview.conversions) { const tr=make('tr'); tr.append(make('td','',row.reading),make('td','',row.word)); $('preview-rows').append(tr); }
    if(!preview.conversions.length) { const tr=make('tr'); const td=make('td','','追加予定の変換はありません。「あとで確認」「追加しない」の選択を保存します。'); td.colSpan=2; tr.append(td); $('preview-rows').append(tr); }
    $('preview-description').textContent = `追加予定 ${preview.counts['採用']}件（変換 ${preview.conversions.length}語）、「あとで確認」${preview.counts['保留']}件、「追加しない」${preview.counts['不採用']}件を候補DBに保存します。未確認の${preview.counts['未判断']}件とメモは、保存後も引き継ぎます。`;
    $('preview-errors').hidden = !preview.errors.length; $('preview-errors').textContent = preview.errors.join('\n');
    $('apply').textContent = '候補DBに保存する';
    $('preview-dialog').showModal(); renderSummary();
  } finally { busy=false; controls(); }
}
async function apply() {
  if(busy || !preview?.token) return;
  clearError(); busy=true; controls(); $('apply').textContent='候補DBに保存しています…';
  try {
    const result=await api('apply',{...version(),token:preview.token});
    state=result.state; preview=null; removeDraft(); dirty=false;
    $('preview-dialog').close(); render(); savedAt();
    completionMessage = `${result.result.saved_decisions}件を候補DBに保存しました。`;
    try {
      state=await api('next',{...version(),count:10});
      selected=state.records.find(r=>r.decision==='未判断')?.key || state.records[0]?.key;
      $('filter').value='未判断';
      reviewKeys=[]; rememberReview();
      if(!state.complete) completionMessage += ' 次の候補を用意しました。続けて作業できます。';
    } catch(e) { completionMessage += ' 次の候補の準備に失敗しました。「次の候補を開く」から再試行できます。'; }
    render(); toast(completionMessage);
  } catch(e) {
    $('preview-errors').hidden=false; $('preview-errors').textContent=e.message;
    preview=null; $('apply').textContent='もう一度、保存内容を確認してください';
    throw e;
  } finally { busy=false; controls(); }
}
function addAlias(row={reading:'',word:$('edit-name').value,suggested:true}) {
  if($('alias-rows').children.length>=20) {toast('別の変換は20件まで追加できます。'); return;}
  const wrap=make('div','alias-row'); wrap.dataset.suggested=String(Boolean(row.suggested));
  for(const [field,label] of [['reading','入力する読み'],['word','変換される名前']]) {
    const title=make('label','',label), input=make('input'); input.dataset.field=field; input.value=row[field];
    input.maxLength=200; input.autocomplete='off'; input.placeholder=field==='reading'?'下の名前の読みなど':'変換される名前';
    if(field==='word') input.addEventListener('input',()=>{wrap.dataset.suggested='false';});
    title.append(input); wrap.append(title);
  }
  const remove=make('button','','削除'); remove.type='button'; remove.setAttribute('aria-label','この別の変換を削除');
  remove.addEventListener('click',()=>{
    if(busy||state.complete) return;
    wrap.remove(); if(!$('alias-rows').children.length) addAlias(); inlineChanged();
  }); wrap.append(remove); $('alias-rows').append(wrap);
}
function rawEdit() {
  return {name:$('edit-name').value,reading:$('edit-reading').value,
    aliases:[...$('alias-rows').children].map(row=>({reading:row.querySelector('[data-field="reading"]').value,
      word:row.querySelector('[data-field="word"]').value,suggested:row.dataset.suggested==='true'}))};
}
function persistInlineEdit() {
  try {localStorage.setItem(inlineDraftKey,JSON.stringify({batch_id:state.batch_id,key:editKey,base:rawEditBase,fields:rawEdit()}));} catch {}
}
function removeInlineDraft() {
  try {const draft=JSON.parse(localStorage.getItem(inlineDraftKey)); if(draft?.batch_id===state.batch_id&&draft.key===editKey) localStorage.removeItem(inlineDraftKey);} catch {}
}
function fillEdit(record, fields) {
  const form=fields||registration(record); editKey=record.key; rawEditBase=JSON.stringify(registration(record));
  $('edit-name').value=form.name; $('edit-reading').value=form.reading; $('alias-rows').replaceChildren();
  for(const row of form.aliases) addAlias(row);
  if(form.aliases.length<20&&!fields) addAlias();
  $('edit-errors').hidden=true; $('edit-status').textContent='入力は自動保存'; editDirty=Boolean(fields);
  showOriginal();
}
function showOriginal() {
  const record=state?.records.find(r=>r.key===editKey); if(!record) return;
  $('original-registration').textContent=`元の候補：${record.name}（${record.reading}）`;
  $('original-registration').hidden=$('edit-name').value===record.name&&$('edit-reading').value===record.reading;
}
function inlineChanged() {
  if(busy||state.complete||!current()) return;
  editDirty=true; preview=null; persistInlineEdit(); showOriginal();
  clearError(); $('save-status').textContent='名前・読みを入力中…';
  $('edit-errors').hidden=true; $('edit-status').textContent='入力中…'; clearTimeout(editTimer); controls();
  if(!editComposing) editTimer=setTimeout(()=>{editTimer=undefined; return commitEdit().catch(error);},450);
}
function validInlineForm(raw) {
  const word=value=>value.length>0&&value.length<=200&&!/[\x00-\x1f\x7f]/.test(value);
  const reading=value=>value.length<=200&&/^[ぁ-ゖー]+$/.test(value);
  const form={name:raw.name.trim(),reading:raw.reading.trim(),aliases:[]};
  if(!word(form.name)) throw new Error('変換される名前を200文字以内で入力してください。');
  if(!reading(form.reading)) throw new Error('入力する読みをひらがな・長音で入力してください。');
  for(const row of raw.aliases) {
    const alias={reading:row.reading.trim(),word:row.word.trim()};
    if(!alias.reading&&(!alias.word||row.suggested)) continue;
    if(!reading(alias.reading)||!word(alias.word)) throw new Error('下の名前・別の変換には、ひらがなの読みと変換される名前を両方入力してください。使わない欄は削除できます。');
    form.aliases.push(alias);
  }
  const pairs=[{reading:form.reading,word:form.name},...form.aliases].map(r=>JSON.stringify([r.reading,r.word]));
  if(new Set(pairs).size!==pairs.length) throw new Error('同じ読みと名前の変換が重複しています。');
  return form;
}
async function commitEdit() {
  if(!editDirty) return true;
  if(editComposing) return true;
  await saving;
  if(!editDirty) return true;
  const key=editKey, raw=rawEdit(), fingerprint=JSON.stringify(raw);
  let form;
  try {form=validInlineForm(raw);}
  catch(e) {$('edit-errors').textContent=e.message; $('edit-errors').hidden=false; $('edit-status').textContent='入力を確認してください'; controls(); return false;}
  const record=state.records.find(r=>r.key===key);
  if(!record||key!==selected) throw new Error('編集中の候補が変わりました。画面を再読み込みしてください。');
  $('edit-errors').hidden=true; $('edit-status').textContent='保存中…';
  if(JSON.stringify(registration(record))!==JSON.stringify(form)) {record.registration=form; await save();}
  else await saving;
  if(editKey===key&&JSON.stringify(rawEdit())===fingerprint) {
    editDirty=false; removeInlineDraft(); $('edit-status').textContent='保存済み';
    if(!dirty) savedAt();
  }
  renderList(); renderSummary(); return true;
}
function restoreInlineEdit() {
  let draft; try {draft=JSON.parse(localStorage.getItem(inlineDraftKey));} catch {}
  if(draft?.batch_id!==state.batch_id||state.complete) return;
  const record=state.records.find(r=>r.key===draft.key);
  if(!record||!draft.fields||!Array.isArray(draft.fields.aliases)) return;
  if(draft.base!==JSON.stringify(registration(record))) {error(new Error('このブラウザーに編集中の入力がありますが、候補の登録内容が更新されています。入力を上書きせず保管しています。')); return;}
  selected=record.key; if(!visible().some(r=>r.key===selected)) $('filter').value='all';
  fillEdit(record,draft.fields); rememberReview(); render();
  $('edit-status').textContent='入力途中の内容を復元しました';
  $('save-status').textContent='名前・読みは入力中';
}
$('add-alias').addEventListener('click',()=>{
  if(busy||state.complete) return;
  const empty=[...$('alias-rows').children].find(row=>row.dataset.suggested==='true'&&!row.querySelector('[data-field="reading"]').value.trim());
  if(empty) {empty.querySelector('input').focus(); return;}
  addAlias(); inlineChanged(); $('alias-rows').lastElementChild?.querySelector('input')?.focus();
});
$('edit-name').addEventListener('input',()=>{
  for(const row of $('alias-rows').children) if(row.dataset.suggested==='true') row.querySelector('[data-field="word"]').value=$('edit-name').value;
});
$('edit-form').addEventListener('input',inlineChanged);
$('edit-form').addEventListener('compositionstart',()=>{editComposing=true; clearTimeout(editTimer);});
$('edit-form').addEventListener('compositionend',()=>{editComposing=false; inlineChanged();});
$('edit-form').addEventListener('submit',event=>{event.preventDefault(); if(!busy) flush().catch(error);});
$('edit-form').addEventListener('focusout',event=>{
  if(!busy&&!editComposing&&!$('edit-form').contains(event.relatedTarget)) {clearTimeout(editTimer); editTimer=undefined; commitEdit().catch(error);}
});
$('revert-edit').addEventListener('click',()=>{
  if(busy||!editDirty) return;
  clearTimeout(editTimer); editTimer=undefined;
  const record=current(); record.registration=JSON.parse(rawEditBase); removeInlineDraft(); fillEdit(record); render();
  if(dirty) save().catch(error); else {clearError(); savedAt();}
});
function filteredCatalog() {
  const query=$('catalog-search').value.trim().toLocaleLowerCase('ja-JP');
  return catalogRecords.filter(r=>`${r.name} ${r.reading} ${r.memo||r.decision_reason||''}`.toLocaleLowerCase('ja-JP').includes(query));
}
function renderCatalogSelection() {
  $('catalog-selected').textContent=`${catalogSelected.size}件選択中`;
  $('recall-selected').textContent=catalogSelected.size?`選んだ${catalogSelected.size}件を呼び出す`:'選んだ候補を呼び出す';
  $('recall-selected').disabled=busy||!catalogSelected.size;
}
function renderCatalog() {
  const rows=filteredCatalog();
  const list=$('catalog-list'); list.replaceChildren(); $('catalog-count').textContent=`表示 ${rows.length}件 / 全 ${catalogRecords.length}件`;
  if(!rows.length) list.append(make('p','empty','この条件に合う候補はありません。'));
  for(const record of rows) {
    const row=make('label','catalog-item'), text=make('span','catalog-text');
    const checkbox=make('input','catalog-checkbox'); checkbox.type='checkbox'; checkbox.checked=catalogSelected.has(record.key); checkbox.disabled=busy;
    checkbox.setAttribute('aria-label',`${record.name}を選ぶ`);
    checkbox.addEventListener('change',()=>{if(checkbox.checked) catalogSelected.add(record.key); else catalogSelected.delete(record.key); renderCatalogSelection();});
    text.append(make('strong','',record.name),make('p','',record.reading||'読み未確認'));
    if(record.memo||record.decision_reason) text.append(make('p','',record.memo||record.decision_reason));
    const origin=record.local?`今の作業 · ${decisionLabels[record.decision]}`:catalogKind==='held'?'DBに保存した保留':'DBの採用済み';
    text.append(make('p','catalog-origin',origin));
    if(!record.local) text.append(make('p','',`${record.registered?'辞書に登録済み · 別の変換を追加できます':'辞書への追加を見直せます'}${record.ready?'':' · 元の台帳で確認が必要'}`));
    row.append(checkbox,text); list.append(row);
  }
  renderCatalogSelection();
}
async function openCatalog(kind) {
  if(busy) return; busy=true; controls();
  try {
    await flush(); catalogKind=kind;
    const local=!state.complete?state.records.filter(r=>r.decision===(kind==='held'?'保留':'採用')):[];
    const db=(await api('catalog?kind='+kind)).records, localKeys=new Set(local.map(r=>r.key));
    catalogRecords=[...local.map(r=>({...r,...registration(r),local:true})),...db.filter(r=>!localKeys.has(r.key))];
    catalogSelected=new Set();
    $('catalog-title').textContent=kind==='held'?'あとで確認をまとめて呼び出す':'追加予定をまとめて呼び出す';
    $('catalog-description').textContent=kind==='held'?'今の作業の保留と、DBに保存した過去の保留から選べます。チェックしてまとめて呼び出すと、その候補だけを順に確認できます。':'アプリの追加予定と、DBの採用済みから選べます。チェックしてまとめて呼び出すと、その候補だけを順に確認できます。作業中の判断・編集・メモは保持します。';
    $('catalog-search').value=''; $('catalog-errors').hidden=true; renderCatalog(); $('catalog-dialog').showModal();
  } finally {busy=false; controls();}
}
$('show-pending').addEventListener('click',()=>openCatalog('approved').catch(error));
$('show-held').addEventListener('click',()=>openCatalog('held').catch(error));
$('catalog-search').addEventListener('input',renderCatalog);
$('catalog-select-all').addEventListener('click',()=>{if(busy) return; for(const r of filteredCatalog()) catalogSelected.add(r.key); renderCatalog();});
$('catalog-clear').addEventListener('click',()=>{if(busy) return; catalogSelected.clear(); renderCatalog();});
$('recall-selected').addEventListener('click',async()=>{
  if(busy||!catalogSelected.size) return;
  busy=true; controls(); $('catalog-errors').hidden=true;
  try {
    await flush();
    const keys=catalogRecords.filter(r=>catalogSelected.has(r.key)).map(r=>r.key);
    const result=await api('recall',{...version(),kind:catalogKind,keys});
    state=result.state; selected=result.selected; reviewKeys=result.selected_keys; preview=null; savedAt();
    $('filter').value='picked'; rememberReview(); $('catalog-dialog').close();
    toast(`${reviewKeys.length}件を呼び出しました。前後ボタンで順に確認できます。`);
  } catch(e) {$('catalog-errors').textContent=e.message; $('catalog-errors').hidden=false;}
  finally {busy=false; render();}
});
$('close-catalog').addEventListener('click',()=>{if(!busy) $('catalog-dialog').close();});
$('catalog-dialog').addEventListener('cancel',event=>{if(busy) event.preventDefault();});
$('leave-review').addEventListener('click',()=>{if(busy) return; $('filter').value='all'; rememberReview(); render();});

for(const button of elements) button.addEventListener('click',()=>decide(button.dataset.decision).catch(error));
$('reset-decision').addEventListener('click',()=>decide('未判断').catch(error));
$('memo').addEventListener('input',()=>{
  if(!current()||busy||state.complete) return;
  current().memo=$('memo').value; dirty=true; preview=null; persistDraft();
  $('save-status').textContent='保存中…'; clearTimeout(memoTimer); controls();
  memoTimer=setTimeout(()=>{memoTimer=undefined; save().then(controls).catch(()=>{});},300);
});
$('memo').addEventListener('blur',()=>flush().then(controls).catch(error));
$('filter').addEventListener('change',async()=>{
  if(busy) return;
  const previousFilter=renderedFilter; busy=true; controls();
  try {await flush(); if(!visible().some(r=>r.key===selected)) selected=visible()[0]?.key; rememberReview();}
  catch(e) {$('filter').value=previousFilter; error(e);}
  finally {busy=false; render();}
});
$('previous').addEventListener('click',()=>{ const records=visible(); const i=records.findIndex(r=>r.key===selected); if(i>0) select(records[i-1].key).catch(error); });
$('next-candidate').addEventListener('click',()=>{ const records=visible(); const i=records.findIndex(r=>r.key===selected); if(i<records.length-1) select(records[i+1].key).catch(error); });
$('preview').addEventListener('click',()=>openPreview().catch(error));
$('apply').addEventListener('click',()=>apply().catch(error));
for(const id of ['close-preview','back-to-review']) $(id).addEventListener('click',()=>{if(!busy) $('preview-dialog').close();});
$('preview-dialog').addEventListener('cancel',event=>{if(busy) event.preventDefault();});
$('new-batch').addEventListener('click',async()=>{
  if(busy) return;
  busy=true; controls(); clearError();
  try { state=await api('next',{...version(),count:10}); selected=state.records[0]?.key; preview=null; completionMessage=''; $('filter').value='未判断'; await load(); toast(`次の候補を用意しました。`); }
  catch(e) {error(e);} finally {busy=false; render();}
});
$('copy-request').addEventListener('click',async()=>{try {await navigator.clipboard.writeText('候補DBに保存した追加予定を確認して、作業用ブランチで辞書に追加して。'); toast('依頼文をコピーしました。Codexに貼り付けてください。');} catch {toast('Codexに「候補DBの追加予定を確認して辞書に追加して」と送ってください。');}});
$('retry').addEventListener('click',async()=>{try {sessionToken=(await api('session')).token; if(dirty) {clearTimeout(memoTimer); memoTimer=undefined; await save();} await flush(); clearError(); await load();} catch(e) {error(e);}});
$('shutdown').addEventListener('click',async()=>{if(busy) return; try {await flush(); await api('shutdown',{}); stopped=true; busy=true; controls(); $('save-status').textContent='アプリを終了しました'; toast('アプリを終了しました。このタブを閉じられます。');} catch(e) {error(e);}});
document.addEventListener('keydown',event=>{
  if(!state || busy || document.querySelector('dialog[open]') || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('textarea,input,select,button')) return;
  if(['1','2','3'].includes(event.key)) {event.preventDefault(); decide({'1':'採用','2':'保留','3':'不採用'}[event.key]).catch(error);}
  if(event.key==='ArrowLeft') $('previous').click(); if(event.key==='ArrowRight') $('next-candidate').click();
});
window.addEventListener('beforeunload',event=>{if(!stopped && (dirty || busy || editDirty)) {event.preventDefault(); event.returnValue='';}});
(async()=>{try {sessionToken=(await api('session')).token; await load();} catch(e) {error(e);}})();
