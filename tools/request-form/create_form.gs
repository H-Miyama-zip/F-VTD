/**
 * F-VTD 収録依頼フォームを作成する Google Apps Script。
 *
 * setup() を1回だけ実行すると、次のものが作られます。
 *   - Google フォーム（新規収録／読みの追加／誤りの訂正で分岐）
 *   - 回答用スプレッドシート（「辞書照合」「対応状況」「対応メモ」列つき）
 *   - 回答が届くたびに辞書と照合し、メールで知らせるトリガー
 *
 * 手順は同じフォルダーの README.md を参照してください。
 */

const CONFIG = {
  formTitle: 'F-VTD 収録依頼フォーム',
  repoUrl: 'https://github.com/H-Miyama-zip/F-VTD',
  siteUrl: 'https://f-vtd.tomaranaina.workers.dev',
  masterTsvUrl: 'https://raw.githubusercontent.com/H-Miyama-zip/F-VTD/main/data/master.tsv',
  statusOptions: ['未対応', '調査中', '収録済み', '見送り'],
};

// 質問の見出し。回答シートの列名にもなり、handleSubmit で値を取り出す鍵に使うため、すべて異なる文言にする。
const Q = {
  type: '依頼の種類',
  newName: '名前の表記',
  newReading: 'フルネームの読み',
  newShortReadings: '下の名前・愛称などの読み',
  newAffiliation: '所属・活動形態',
  addName: '読みを追加したい名前（辞書に収録済みの表記）',
  addReadings: '追加したい読み',
  fixName: '訂正したい名前（辞書に収録済みの表記）',
  fixWrong: '誤っている内容',
  fixCorrectName: '正しい名前の表記',
  fixCorrectReading: '正しい読み',
  sourceUrl: '確認元のURL',
  note: '補足',
  requester: '依頼者の立場',
  contact: '連絡先',
  agree: '確認事項',
};

const TYPE = {
  new: '新規収録（辞書に未収録のVTuberを追加してほしい）',
  add: '読みの追加（収録済みのVTuberに別の読みを追加してほしい）',
  fix: '誤りの訂正（名前の表記や読みの誤りを直してほしい）',
};

const COLUMN = { lookup: '辞書照合', status: '対応状況', memo: '対応メモ' };

const READING_PATTERN = '^[ぁ-ゖー]+$';
const READINGS_PATTERN = '^[ぁ-ゖー]+([、,， 　]+[ぁ-ゖー]+)*$';
const READING_HELP = 'ひらがなで入力してください（長音「ー」は使えます）。英数字を含む読みは「補足」欄にお書きください。';

function setup() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('formId')) {
    throw new Error('このプロジェクトでは作成済みです。作り直す場合は README.md の「作り直すとき」を参照してください。');
  }

  const form = buildForm_();
  const ss = SpreadsheetApp.create(CONFIG.formTitle + '（回答）');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
  const sheet = prepareSheet_(ss.getId());

  ScriptApp.newTrigger('handleSubmit').forSpreadsheet(ss).onFormSubmit().create();

  const email = Session.getEffectiveUser().getEmail();
  props.setProperties({ formId: form.getId(), spreadsheetId: ss.getId(), notifyEmail: email });

  const publishedUrl = form.getPublishedUrl();
  let shortUrl = publishedUrl;
  try {
    shortUrl = form.shortenFormUrl(publishedUrl);
  } catch (err) {
    // 短縮URLが取れなくても公開URLで運用できる。
  }

  Logger.log('回答用URL（README に載せるもの）: ' + shortUrl);
  Logger.log('フォームの編集画面: ' + form.getEditUrl());
  Logger.log('回答スプレッドシート: ' + ss.getUrl() + '（シート「' + sheet.getName() + '」）');
  Logger.log('通知メールの送信先: ' + email);
}

function buildForm_() {
  const form = FormApp.create(CONFIG.formTitle);
  form
    .setDescription([
      'F-VTD（VTuber変換辞書 ver1_2 を基にした非公式の改変版）への収録依頼フォームです。',
      '未収録のVTuberの追加、読みの追加、名前や読みの誤りの訂正を受け付けます。1回の送信につき1名分をお送りください。',
      '',
      '・原作者（とぷんろぷ氏）の窓口ではありません。F-VTD についてのご連絡を原作者へ送らないでください。',
      '・いただいた内容は、公式サイトなどで名前と読みを確認してから収録します。すべての依頼への対応や、反映の時期はお約束できません。',
      '・収録した場合、名前・読み・確認元URLを GitHub で公開します。依頼者の立場と連絡先は公開しません。',
      '',
      '収録状況の検索と辞書のダウンロード：' + CONFIG.siteUrl,
    ].join('\n'))
    .setConfirmationMessage('送信ありがとうございました。内容を確認のうえ、収録を検討します。反映された内容は ' + CONFIG.siteUrl + ' の「更新履歴」でご確認いただけます。')
    .setCollectEmail(false)
    .setAllowResponseEdits(false)
    .setProgressBar(true);

  const typeItem = form.addMultipleChoiceItem().setTitle(Q.type).setRequired(true);

  // 新規収録
  const newPage = form.addPageBreakItem()
    .setTitle('新規収録')
    .setHelpText('辞書に未収録のVTuberについてお書きください。収録済みかどうかは ' + CONFIG.siteUrl + ' で検索できます。');
  form.addTextItem().setTitle(Q.newName)
    .setHelpText('公式の表記どおりにお書きください。例：キズナアイ')
    .setRequired(true);
  form.addTextItem().setTitle(Q.newReading)
    .setHelpText('例：きずなあい　' + READING_HELP)
    .setValidation(textPattern_(READING_PATTERN))
    .setRequired(true);
  form.addTextItem().setTitle(Q.newShortReadings)
    .setHelpText('下の名前や、よく呼ばれる短い読みでも変換できるように登録します。例：あい　複数ある場合は「、」で区切ってください。' + READING_HELP)
    .setValidation(textPattern_(READINGS_PATTERN));
  form.addTextItem().setTitle(Q.newAffiliation)
    .setHelpText('事務所・グループ名、個人勢、自治体の公認VTuberなど。');

  // 読みの追加
  const addPage = form.addPageBreakItem()
    .setTitle('読みの追加')
    .setHelpText('辞書に収録済みのVTuberに、別の読みを追加します。');
  form.addTextItem().setTitle(Q.addName)
    .setRequired(true);
  form.addTextItem().setTitle(Q.addReadings)
    .setHelpText('複数ある場合は「、」で区切ってください。' + READING_HELP)
    .setValidation(textPattern_(READINGS_PATTERN))
    .setRequired(true);

  // 誤りの訂正
  const fixPage = form.addPageBreakItem()
    .setTitle('誤りの訂正')
    .setHelpText('辞書に収録済みの名前・読みの誤りを直します。');
  form.addTextItem().setTitle(Q.fixName)
    .setRequired(true);
  form.addParagraphTextItem().setTitle(Q.fixWrong)
    .setRequired(true);
  form.addTextItem().setTitle(Q.fixCorrectName)
    .setHelpText('名前の表記を直す場合のみ。');
  form.addTextItem().setTitle(Q.fixCorrectReading)
    .setHelpText('読みを直す場合のみ。' + READING_HELP)
    .setValidation(textPattern_(READING_PATTERN));

  // 共通
  const commonPage = form.addPageBreakItem()
    .setTitle('確認元と依頼者について');
  form.addParagraphTextItem().setTitle(Q.sourceUrl)
    .setHelpText('名前と読みを確認できるページのURL。公式サイト、本人のX・YouTube、運営元・自治体のお知らせなどを優先してください。複数ある場合は改行で区切ってください。')
    .setValidation(FormApp.createParagraphTextValidation()
      .requireTextContainsPattern('https?://')
      .setHelpText('http:// または https:// で始まるURLを1つ以上入力してください。')
      .build())
    .setRequired(true);
  form.addParagraphTextItem().setTitle(Q.note)
    .setHelpText('任意。読みの根拠（配信での名乗りなど）や、その他お伝えしたいことがあればお書きください。');
  form.addMultipleChoiceItem().setTitle(Q.requester)
    .setChoiceValues(['VTuber本人', '運営・事務所などの関係者', 'ファン・その他'])
    .setRequired(true);
  form.addTextItem().setTitle(Q.contact)
    .setHelpText('任意。確認のため連絡してよい場合のみ、XのIDやメールアドレスをお書きください。返信のため以外には使わず、公開もしません。');
  form.addCheckboxItem().setTitle(Q.agree)
    .setChoiceValues(['すべての依頼に対応するとは限らないこと、収録時に名前・読み・確認元URLが公開されることを了承します'])
    .setRequired(true);

  typeItem.setChoices([
    typeItem.createChoice(TYPE.new, newPage),
    typeItem.createChoice(TYPE.add, addPage),
    typeItem.createChoice(TYPE.fix, fixPage),
  ]);
  // 各依頼のページを終えたら、次の依頼のページを飛ばして共通ページへ進む。
  addPage.setGoToPage(commonPage);
  fixPage.setGoToPage(commonPage);
  commonPage.setGoToPage(FormApp.PageNavigationType.CONTINUE);

  return form;
}

function textPattern_(pattern) {
  return FormApp.createTextValidation()
    .requireTextMatchesPattern(pattern)
    .setHelpText(READING_HELP)
    .build();
}

function prepareSheet_(spreadsheetId) {
  SpreadsheetApp.flush();
  const ss = SpreadsheetApp.openById(spreadsheetId);
  const sheet = ss.getSheets().find((s) => s.getFormUrl());
  if (!sheet) throw new Error('回答シートが見つかりません。もう一度 setup() を実行する前に README.md の「作り直すとき」を確認してください。');

  ss.getSheets().filter((s) => s.getSheetId() !== sheet.getSheetId()).forEach((s) => ss.deleteSheet(s));

  const firstExtra = sheet.getLastColumn() + 1;
  const headers = [COLUMN.lookup, COLUMN.status, COLUMN.memo];
  sheet.getRange(1, firstExtra, 1, headers.length).setValues([headers]).setFontWeight('bold').setBackground('#fff2cc');
  sheet.getRange(2, firstExtra + 1, sheet.getMaxRows() - 1, 1).setDataValidation(
    SpreadsheetApp.newDataValidation().requireValueInList(CONFIG.statusOptions, true).build());
  sheet.setFrozenRows(1);
  return sheet;
}

/** 回答が届いたときに呼ばれる（setup() が作るトリガーから）。 */
function handleSubmit(e) {
  const values = e.namedValues;
  const get = (title) => ((values[title] || [''])[0] || '').trim();

  const type = get(Q.type);
  const name = get(Q.newName) || get(Q.addName) || get(Q.fixName);
  const readings = splitReadings_([get(Q.newReading), get(Q.newShortReadings), get(Q.addReadings), get(Q.fixCorrectReading)].join('、'));

  let lookup;
  try {
    lookup = lookupDictionary_(name, readings);
  } catch (err) {
    lookup = '照合失敗：' + err.message;
  }

  const sheet = e.range.getSheet();
  const header = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const lookupCol = header.indexOf(COLUMN.lookup) + 1;
  const statusCol = header.indexOf(COLUMN.status) + 1;
  if (lookupCol) sheet.getRange(e.range.getRow(), lookupCol).setValue(lookup);
  if (statusCol) sheet.getRange(e.range.getRow(), statusCol).setValue(CONFIG.statusOptions[0]);

  const to = PropertiesService.getScriptProperties().getProperty('notifyEmail');
  if (!to) return;

  const lines = Object.keys(Q)
    .map((key) => Q[key])
    .filter((title) => title !== Q.agree && get(title))
    .map((title) => '■ ' + title + '\n' + get(title));
  const body = [
    '新しい収録依頼が届きました。',
    '',
    '■ 辞書照合（自動）',
    lookup,
    '',
    lines.join('\n\n'),
    '',
    '回答シート：' + sheet.getParent().getUrl() + '#gid=' + sheet.getSheetId(),
  ].join('\n');
  MailApp.sendEmail(to, '[F-VTD] 収録依頼：' + type.split('（')[0] + '｜' + (name || '(名前なし)'), body);
}

function splitReadings_(text) {
  return text.split(/[、,，\s　]+/).map((r) => r.trim()).filter(Boolean);
}

function normalize_(text) {
  return String(text).normalize('NFKC').replace(/[\s　]+/g, '');
}

/** 公開中の master.tsv と照合し、名前・読みの登録状況を1つの文にまとめる。 */
function lookupDictionary_(name, readings) {
  const res = UrlFetchApp.fetch(CONFIG.masterTsvUrl, { muteHttpExceptions: true });
  if (res.getResponseCode() !== 200) throw new Error('master.tsv を取得できません（HTTP ' + res.getResponseCode() + '）');

  const rows = res.getContentText('UTF-8').split(/\r?\n/).slice(1)
    .map((line) => line.split('\t'))
    .filter((cols) => cols.length >= 2);

  const target = normalize_(name);
  const registered = rows.filter((cols) => normalize_(cols[1]) === target).map((cols) => cols[0]);

  const parts = [];
  if (!name) {
    parts.push('名前なし');
  } else if (registered.length) {
    parts.push('名前は収録済み（登録済みの読み：' + registered.join('、') + '）');
  } else {
    parts.push('名前は未収録');
  }

  const already = readings.filter((r) => registered.includes(r));
  if (already.length) parts.push('依頼の読みのうち登録済み：' + already.join('、'));

  const others = readings
    .map((r) => {
      const words = rows.filter((cols) => cols[0] === r && normalize_(cols[1]) !== target).map((cols) => cols[1]);
      return words.length ? r + '→' + words.slice(0, 5).join('／') : null;
    })
    .filter(Boolean);
  if (others.length) parts.push('同じ読みの別の登録：' + others.join('、'));

  return parts.join('。');
}
