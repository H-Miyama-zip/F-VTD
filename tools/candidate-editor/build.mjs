import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const inputPath = path.resolve(process.argv[2]);
const out = path.dirname(inputPath);
const input = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const outputPath = path.join(out,'追加候補_作業台帳.xlsx');
// layout_input contains the actual pending edits and the original baseline.
// Reconstruct this known workbook without the slow large-workbook importer.
console.log('台帳を作成します。');
const wb = Workbook.create();
const literal = value => typeof value === 'string' && value.startsWith('=') ? `'${value}` : value;
const cols = ['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q'];
// Canonical input indices: name, link, decision, reading, status, candidates,
// evidence, URL, reason, memo, listing, YouTube ID, identity, external, type, date, key.
const order = [0,16,4,1,3,2,6,5,8,7,10,12,13,15,14,11,9];
const widths = [25,19,12,20,21,24,50,38,30,32,22,30,44,26,22,23,44];
const headers=order.map(i=>i===16?'YouTubeチャンネル':input.headers[i]);
const decisions=['未判断','採用','保留','不採用'];
const sheets = [
  ['main', '主一覧', '名前・読みを確認し、収録判断を選びます。未確認の行を先に並べています。'],
  ['external', '別管理の候補', '個別IDが未確定の候補・別名義など。確認済みにする時は本人確認の根拠も記入します。'],
  ['new', '新規追加', '空き行に入力します。固定YouTube IDがあれば主一覧へ、空欄なら別管理候補へ登録します。'],
];
const baseFont = {name:'Arial',size:11,color:'#243B53'};
async function addYoutubeLinks() {
  for (const [kind,name] of sheets) {
    const sheet=wb.worksheets.getItem(name);
    const rowCount=input.rows[kind].length;
    const end=rowCount+4;
    sheet.getRange('B4').values=[['YouTubeチャンネル']];
    sheet.getRange('B4').format={fill:'#243B53',font:{...baseFont,bold:true,color:'#FFFFFF'},
      wrapText:true,horizontalAlignment:'center',verticalAlignment:'center'};
    sheet.getRange(`B5:B${end}`).format={fill:'#EAF2FF',font:{...baseFont,bold:true,color:'#1565C0'},
      numberFormat:'General',verticalAlignment:'center',horizontalAlignment:'center',wrapText:true};
    sheet.getRange(`B5:B${end}`).formulas=Array.from({length:rowCount},(_,i)=>{
      const r=i+5;
      return [`=IF(AND(LEN(L${r})=24,LEFT(L${r},2)="UC"),HYPERLINK("https://www.youtube.com/channel/"&L${r},"チャンネルを開く"),"")`];
    });
    console.log((await wb.inspect({kind:'table',sheetId:name,range:'A4:F7',include:'values,formulas',tableMaxRows:4,tableMaxCols:6,tableMaxCellChars:60,maxChars:2000})).ndjson);
  }
}
async function validateYoutubeLinks() {
  // Exercise the same live formula on the actual new-entry sheet, then restore.
  const sheet=wb.worksheets.getItem('新規追加');
  const saved=sheet.getRange('L5').values;
  const formula=sheet.getRange('B5').formulas;
  const checks=[];
  for(const [id,label] of [['',''],['UC'+'z'.repeat(22),'チャンネルを開く'],['not-a-channel','']]) {
    sheet.getRange('L5').values=[[id]];
    wb.recalculate();
    const result=sheet.getRange('B5').values[0][0];
    // The runtime resolves the arguments but delegates HYPERLINK evaluation to
    // native Excel. Check the resolved URI in its diagnostic until then.
    const nativePending=label && typeof result==='string' && result.startsWith('HYPERLINK is not implemented.') &&
      result.includes(`linkLocation=https://www.youtube.com/channel/${id}, friendlyName=${label}`);
    if ((result ?? '')!==label && !nativePending) throw new Error(`YouTube link did not react to ID: ${id}: ${result}`);
    checks.push({id,result:label,nativeEvaluationPending:Boolean(nativePending)});
  }
  sheet.getRange('L5').values=saved;
  if(JSON.stringify(sheet.getRange('B5').formulas)!==JSON.stringify(formula)) throw new Error('Link formula changed');
  console.log(JSON.stringify({youtubeLinkInputChecks:checks}));
}
async function renderYoutubeLinks() {
  for(const [kind,name] of sheets) {
    const sheet=wb.worksheets.getItem(name);
    const end=input.rows[kind].length+4;
    const range=sheet.getRange(`B5:B${end}`);
    const formulas=range.formulas;
    const labels=sheet.getRange(`L5:L${end}`).values.map(([id])=>[
      typeof id==='string' && id.length===24 && id.startsWith('UC') ? 'チャンネルを開く' : ''
    ]);
    // Preview native Excel's friendly labels; retain the live formulas in the file.
    range.values=labels;
    await fs.writeFile(path.join(out,`preview_work_${name}.png`),new Uint8Array(await (await wb.render({sheetName:name,range:'A1:H8',scale:1.1,format:'png'})).arrayBuffer()));
    await fs.writeFile(path.join(out,`preview_details_${name}.png`),new Uint8Array(await (await wb.render({sheetName:name,range:'I4:Q7',scale:1,format:'png'})).arrayBuffer()));
    range.formulas=formulas;
  }
}
function fitRows(sheet, rows, colWidths, minimum=44) {
  const lastRow=rows.length+4;
  const lastCol=cols[colWidths.length-1];
  sheet.getRange(`A5:${lastCol}${lastRow}`).format.verticalAlignment='top';
  sheet.getRange(`A5:${lastCol}${lastRow}`).format.wrapText=true;
  const widthOf = s => [...String(s??'')].reduce((n,c)=>n+(c.codePointAt(0)>255?2:1),0);
  const heights=rows.map(r=>Math.min(409,Math.max(minimum,12+14*Math.max(...r.map((v,i)=>
    String(v??'').split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil(widthOf(line)/Math.max(1,colWidths[i]-2))),0))))));
  // Apply equal consecutive heights together, preserving compact short rows.
  let start=0;
  for(let i=1;i<=heights.length;i++) {
    if(i===heights.length || heights[i]!==heights[start]) {
      sheet.getRange(`A${start+5}:${lastCol}${i+4}`).format.rowHeight=heights[start];
      start=i;
    }
  }
}
async function previewsAndExport() {
  await addYoutubeLinks();
  await validateYoutubeLinks();
  await renderYoutubeLinks();
  wb.recalculate();
  console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:2200})).ndjson);
  for (const [sheetName,range,file] of [
    ['使い方','A1:B25','preview_guide.png'],
  ]) await fs.writeFile(path.join(out,file),new Uint8Array(await (await wb.render({sheetName,range,scale:1.3,format:'png'})).arrayBuffer()));
  const temp=path.join(out,'layout_export.tmp.xlsx');
  await (await SpreadsheetFile.exportXlsx(wb)).save(temp);
  await fs.rename(temp,outputPath);
  console.log(JSON.stringify({output:outputPath,counts:input.counts}));
}
function base(sheet, title, note, end, last='P') {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${last}${end}`).format.font = baseFont;
  sheet.getRange('A2').values = [[title]];
  sheet.getRange('A2').format.font = {...baseFont,size:16,bold:true};
  sheet.getRange('A2').format.rowHeight = 28;
  sheet.getRange('A3').values = [[note]];
  sheet.getRange('A3').format.font = {...baseFont,size:10,color:'#526579'};
  sheet.getRange('A3').format.rowHeight = 23;
}
for (const [kind, name, note] of sheets) {
  console.log(`${name}: ${input.rows[kind].length}行の配置を調整します。`);
  const sheet = wb.worksheets.add(name);
  sheet.tabColor = kind === 'main' ? '#243B53' : kind === 'external' ? '#627D98' : '#C79C32';
  const canonicalData = input.rows[kind].map(row => row.map((v,i) => {
    if (i === 11 && v) {
      if(input.serial_dates) {
        if(Number.isFinite(Number(v))) return Number(v);
        const existingInstant=Date.parse(v);
        if(Number.isFinite(existingInstant)) return new Date(existingInstant);
      }
      const instant = Date.parse(v);
      if (Number.isFinite(instant)) {
        // Excel has no timezone. Source date-only values must keep their date.
        return new Date(instant + (/^\d{4}-\d{2}-\d{2}$/.test(v) ? 0 : 9 * 3600000));
      }
    }
    return literal(i===4 && v==='対象外' ? '不採用' : v);
  }));
  const data=canonicalData.map(row=>order.map(i=>i===16?'':row[i]));
  const end = data.length + 4;
  base(sheet, `追加候補 ${name}`, 'チャンネルを開き、読み・根拠を確認して収録判断を選びます。黄色の欄が入力欄です。', end,'Q');
  sheet.getRange(`A4:Q${end}`).values = [headers, ...data];
  const table = sheet.tables.add(`A4:Q${end}`, true, `Candidate${kind}Table`);
  console.log(`${name}: 入力内容と列の配置が完了しました。`);
  table.style = 'TableStyleLight9';
  sheet.getRange('A4:Q4').format = {fill:'#243B53',font:{...baseFont,bold:true,color:'#FFFFFF'},
    rowHeight:34,wrapText:true,horizontalAlignment:'center',verticalAlignment:'center'};
  sheet.getRange(`A5:Q${end}`).format = {font:baseFont,rowHeight:44,wrapText:true,verticalAlignment:'center',numberFormat:'@',fill:'#F1F5F9'};
  sheet.getRange(`A5:A${end}`).format.fill = '#FFFBEF';
  sheet.getRange(`C5:J${end}`).format.fill = '#FFFBEF';
  if (kind !== 'main') sheet.getRange(`L5:O${end}`).format.fill = '#FFFBEF';
  for (let i=0;i<cols.length;i++) sheet.getRange(`${cols[i]}:${cols[i]}`).format.columnWidth = widths[i];
  sheet.getRange(`P5:P${end}`).format.numberFormat = 'yyyy-mm-dd hh:mm';
  sheet.getRange(`E5:E${end}`).dataValidation = {rule:{type:'list',values:input.statuses}};
  sheet.getRange(`C5:C${end}`).dataValidation = {rule:{type:'list',values:decisions}};
  if (kind !== 'main') sheet.getRange(`N5:N${end}`).dataValidation = {rule:{type:'list',values:input.external_decisions}};
  for (const [label,fill,color] of [['確認済み','#E8F4EC','#276749'],['未確認','#F1F5F9','#526579'],['候補','#FFF3CD','#805B10']]) {
    sheet.getRange(`E5:E${end}`).conditionalFormats.add('cellIs',{operator:'equal',formula:`"${label}"`,format:{fill,font:{color}}});
  }
  for(const [label,fill,color] of [['採用','#E8F4EC','#276749'],['保留','#FFF3CD','#805B10'],['不採用','#FBECEC','#9B2C2C']])
    sheet.getRange(`C5:C${end}`).conditionalFormats.add('cellIs',{operator:'equal',formula:`"${label}"`,format:{fill,font:{color,bold:true}}});
  sheet.getRange(`C5:C${end}`).format.horizontalAlignment='center';
  sheet.getRange(`C5:C${end}`).format.font={...baseFont,bold:true};
  sheet.getRange('C4').format.fill='#365F46';
  sheet.freezePanes.freezeRows(4);
  sheet.freezePanes.freezeColumns(3);
  fitRows(sheet,data,widths);
  sheet.getRange(`C5:C${end}`).format.verticalAlignment='center';
  console.log(`${name}: 表示と入力欄の設定が完了しました。`);
}
const logs = wb.worksheets.add('調査記録');
base(logs,'元の調査記録','識別IDで主一覧・別管理候補と対応します。原文は保持され、編集メモは一覧の入力欄で管理します。',input.logs.length+4,'G');
logs.getRange(`A4:G${input.logs.length+4}`).values = [
  ['名前','識別ID','元の注記','元の保留理由','元の本人確認の根拠','提供された本人キー','元資料の記録ID'],
  ...input.logs.map(r=>r.map(literal))
];
logs.tables.add(`A4:G${input.logs.length+4}`,true,'CandidateResearchHistoryTable').style='TableStyleLight9';
logs.getRange('A4:G4').format={fill:'#526579',font:{...baseFont,bold:true,color:'#FFFFFF'},rowHeight:32,horizontalAlignment:'center'};
logs.getRange(`A5:G${input.logs.length+4}`).format={font:baseFont,rowHeight:32,wrapText:false,numberFormat:'@'};
for (const [col,width] of [['A',24],['B',48],['C',60],['D',65],['E',55],['F',55],['G',48]]) logs.getRange(`${col}:${col}`).format.columnWidth=width;
logs.freezePanes.freezeRows(4);
logs.freezePanes.freezeColumns(1);
fitRows(logs,input.logs,[24,48,60,65,55,55,48],56);

const guide = wb.worksheets.add('使い方');
guide.tabColor='#98A6AD';
base(guide,'追加候補台帳の使い方',`書き出し ${input.created_at.slice(0,16).replace('T',' ')}（日本時間）`,25,'D');
const notes = [
  ['入力欄','薄い黄色の欄を編集します。灰色の識別ID・掲載状況・日時は参照用です。'],
  ['件数',`主一覧 ${input.counts.main.toLocaleString()}件、別管理候補 ${input.counts.external.toLocaleString()}件。新規追加は100行。`],
  ['未確認を探す','確認状態のフィルターで「未確認」「候補が複数」などを選びます。'],
  ['読みを確認する','読み・確認状態・根拠URL・根拠の要点を入力します。確認済みの読みはひらがな・長音で記入。'],
  ['候補と確定の区別','確認済み以外の読みは未確定です。根拠が足りない読みは読み候補として記録します。'],
  ['別管理候補の確認','本人確認の根拠を入力し、確認状態と別管理の扱いを両方「確認済み」にします。'],
  ['収録判断','未判断・採用・保留・不採用からプルダウンで選びます。読みの確認状態と別に管理します。'],
  ['採用の条件','確認済みの読みと根拠URL・要点が必要です。採用は収録予定で、辞書への登録は別作業です。'],
  ['編集メモと判断理由','新しいメモを入力します。元の調査記録を上書きせず、編集履歴を保存します。'],
  ['新規追加','新規追加シートに入力します。YouTube IDはUCから始まる24文字の固定IDを使用します。'],
  ['同じIDがある場合','新規追加せず既存行を編集します。名前だけで同一人物と判断して結合しません。'],
  ['変更の確認','Excelを保存して閉じ、差分確認を実行します。確認用HTMLはブラウザ編集アプリとは別の画面です。'],
  ['DBへの反映','候補DBだけに保存します。辞書追加はCodexへ別途依頼してください。バックアップと履歴も保存します。'],
  ['反映後','最新台帳を新しく書き出します。書き出し時に既存のExcelは置き換えず履歴として残します。'],
  ['長い記録','セルを選択し、数式バーを広げて原文を確認できます。調査記録シートに元の原文があります。'],
  ['行の削除','既存行を削除せず、収録判断を不採用にしてください。識別IDは変更しません。'],
  ['並べ替え','フィルターは保護したまま使えます。並べ替えをExcelが制限する場合は校閲からシート保護を解除。'],
  ['掲載状況','書き出し時点のmaster.tsvとの一致です。Web公開・IMEへの取り込みとは別です。同名でも本人を確認します。'],
  ['調査履歴の保持','調査記録は読み取り用です。元資料の詳細・履歴・保留表はDB側にも保持しています。'],
  ['チャンネルを開く','名前の右隣の青いリンクをクリックします。YouTube IDを変更するとリンク先も更新します。'],
  ['作業順と固定列','名前・チャンネル・収録判断を固定。読み・確認状態・読み候補の次に根拠とメモ、右側に参照情報があります。'],
];
guide.getRange(`A5:B${notes.length+4}`).values=notes;
guide.getRange(`A5:A${notes.length+4}`).format={font:{...baseFont,bold:true},verticalAlignment:'center'};
guide.getRange(`B5:B${notes.length+4}`).format={font:baseFont,wrapText:true,verticalAlignment:'center'};
guide.getRange(`A5:B${notes.length+4}`).format.rowHeight=42;
guide.getRange('A:A').format.columnWidth=24;
guide.getRange('B:B').format.columnWidth=92;
guide.getRange('C:D').format.columnWidth=4;

const metadata = wb.worksheets.add('_編集情報');
metadata.getRange(`A1:B${input.metadata.length}`).values=input.metadata;
metadata.showGridLines=false;
// The public API has no documented protection setter. Native protection and
// veryHidden metadata are added by the stdlib OOXML pass after export.
console.log(wb.help('worksheet.protection',{include:'index,notes',maxChars:1400}).ndjson);

await previewsAndExport();
