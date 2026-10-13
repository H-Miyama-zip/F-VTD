"""Japanese Excel front end for the existing candidate tables.

Standard-library XLSX reader, three-way field merge, reviewed plans and backups.
Workbook authoring is delegated to build.mjs (@oai/artifact-tool).
"""
from __future__ import annotations

import argparse
import base64
import copy
import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import uuid
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / 'outputs/vtuberpost_private'
DEFAULT_OUT = ROOT / 'outputs/01a11e9f-3575-7fc3-8eb3-55ed85c0a3c9'
BOOK_NAME = '追加候補_作業台帳.xlsx'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
JST = timezone(timedelta(hours=9))
STATUS = {'verified': '確認済み', 'candidate': '候補', 'unknown': '未確認',
          'conflict_existing_candidates': '候補が複数',
          'candidate_existing_fvtd_unsourced': '出典未確認の候補'}
DECISIONS = ['未判断', '採用', '保留', '対象外']
EXTERNAL = {'verified_external': '確認済みの別管理候補', 'unresolved': '未解決', 'excluded': '対象外'}
HEADERS = ['名前', '読み', '読み候補', '確認状態', '収録判断', '根拠URL', '根拠の要点',
           '編集メモ', '判断理由', '識別ID', '辞書掲載状況', '最終確認（日本時間）',
           'YouTube ID', '本人確認の根拠', '個人・団体の区分', '別管理の扱い']
SHEETS = {'main': '主一覧', 'external': '別管理の候補', 'new': '新規追加'}
FIELDS = ['name', 'reading', 'reading_candidates', 'reading_status', 'dictionary_decision',
          'source_url', 'evidence_summary', 'editor_note', 'decision_reason', 'key',
          'dictionary_presence', 'last_checked', 'youtube_channel_id', 'identity_evidence',
          'entity_classification', 'decision']
DB_EDITABLE = {'main': {'name', 'reading', 'reading_candidates', 'reading_status',
                        'source_url', 'evidence_summary'},
               'external': {'name', 'reading', 'reading_candidates', 'reading_status', 'source_url',
                            'evidence_summary', 'youtube_channel_id', 'identity_evidence',
                            'entity_classification', 'decision'}}
META_EDITABLE = {'dictionary_decision', 'editor_note', 'decision_reason'}
FILES = ['vtuberpost_readings.tsv', 'vtuberpost_readings.json', 'candidates_external.tsv',
         'candidates_external.json', 'candidate_db_index.json', 'vtuberpost_readings_report.json',
         'vtuberpost_readings_checkpoint.json', 'candidate_editor_state.json',
         'candidate_manual_overrides.json', 'candidate_editor_history.json']


def now():
    return datetime.now(JST).isoformat(timespec='seconds')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return digest(path.read_bytes()) if path.exists() else None


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else copy.deepcopy(default)


def tsv_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8-sig')), delimiter='\t'))


def tsv_bytes(rows, columns):
    buf = io.StringIO(newline='')
    w = csv.DictWriter(buf, fieldnames=columns, delimiter='\t', lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode('utf-8')


def load_db(db):
    """Capture and verify all files from the same read window."""
    raw = {n: (db / n).read_bytes() if (db / n).exists() else None for n in FILES}
    tables = {}
    for kind, prefix, key in [('main', 'vtuberpost_readings', 'youtube_channel_id'),
                              ('external', 'candidates_external', 'record_id')]:
        if raw[prefix + '.tsv'] is None or raw[prefix + '.json'] is None:
            raise ValueError(f'{prefix}: TSV/JSONの両方が必要です。')
        rows = tsv_rows(raw[prefix + '.tsv'])
        if rows != json.loads(raw[prefix + '.json']):
            raise ValueError(f'{prefix}: TSVとJSONが一致していません。先に整合性を確認してください。')
        if len(rows) != len({r[key] for r in rows}) or any(not r[key] for r in rows):
            raise ValueError(f'{prefix}: 識別IDが空欄または重複しています。')
        tables[kind] = rows
    hashes = {n: digest(b) if b is not None else None for n, b in raw.items()}
    if hashes != {n: file_hash(db / n) for n in FILES}:
        raise ValueError('読み取り中にDBが更新されました。もう一度実行してください。')
    state = json.loads(raw['candidate_editor_state.json']) if raw['candidate_editor_state.json'] else {'version': 1, 'records': {}, 'new_origins': {}}
    return tables, state, hashes, raw


def make_key(kind, row):
    return kind + ':' + row['youtube_channel_id' if kind == 'main' else 'record_id']


def editable_view(kind, row, state):
    k = make_key(kind, row)
    result = {f: row.get(f, '') for f in DB_EDITABLE[kind]}
    saved = state['records'].get(k, {})
    result.update({f: saved.get(f, '未判断' if f == 'dictionary_decision' else '') for f in META_EDITABLE})
    return result


def presence(row, pairs, names):
    if row.get('reading') and (row['name'], row['reading']) in pairs:
        return '同じ名前・読みの語あり'
    return '同じ名前の語あり' if row['name'] in names else '未掲載'


def display_text(value):
    """Expose XML-illegal source characters without changing the source DB."""
    if not isinstance(value, str):
        return value
    return ''.join(f'［制御文字 U+{ord(c):04X}］' if (ord(c) < 32 and c not in '\t\n\r')
                   or ord(c) in (0xFFFE, 0xFFFF) or 0xD800 <= ord(c) <= 0xDFFF else c for c in value)


def export_input(db, out):
    tables, state, hashes, _ = load_db(db)
    master_path = ROOT / 'data/master.tsv'
    master = tsv_rows(master_path.read_bytes()) if master_path.exists() else []
    pairs = {(r['word'], r['reading']) for r in master}
    names = {r['word'] for r in master}
    baseline = {'version': 1, 'export_id': str(uuid.uuid4()), 'exported_at': now(),
                'hashes': hashes, 'rows': {}, 'new_slots': []}
    sheet_rows = {'main': [], 'external': [], 'new': []}
    logs = []
    for kind, rows in tables.items():
        # Unconfirmed first; the Excel filter retains every record.
        ordered = sorted(rows, key=lambda r: (r['reading_status'] == 'verified', r['name'], make_key(kind, r)))
        for row in ordered:
            k = make_key(kind, row)
            view = editable_view(kind, row, state)
            original = copy.deepcopy(view)
            view.update(key=k, dictionary_presence=presence(row, pairs, names),
                        last_checked=row.get('last_checked', ''))
            for f in ('youtube_channel_id', 'identity_evidence', 'entity_classification', 'decision'):
                view.setdefault(f, row.get(f, '') if kind == 'external' or f == 'youtube_channel_id' else '')
            view['reading_status'] = STATUS.get(row['reading_status'], row['reading_status'])
            if kind == 'external':
                view['decision'] = EXTERNAL.get(row.get('decision', ''), row.get('decision', ''))
            if view['entity_classification'] == 'individual':
                view['entity_classification'] = '個人'
            view = {f: display_text(v) for f, v in view.items()}
            baseline['rows'][k] = {'kind': kind, 'cells': view, 'original': original}
            sheet_rows[kind].append([view.get(f, '') for f in FIELDS])
            logs.append([display_text(v) for v in [row['name'], k, row.get('notes', ''), row.get('hold_reason', ''),
                         row.get('identity_evidence', ''), row.get('supplied_identity_keys', ''),
                         row.get('import_details_id', '')]])
    for _ in range(100):
        slot = 'manual:' + str(uuid.uuid4())
        baseline['new_slots'].append(slot)
        sheet_rows['new'].append(['', '', '', '未確認', '未判断', '', '', '', '', slot, '', '', '', '', '個人', '未解決'])
    packed = base64.b64encode(zlib.compress(json.dumps(baseline, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 9)).decode('ascii')
    chunks = [packed[i:i + 28000] for i in range(0, len(packed), 28000)]
    input_data = {'created_at': now(), 'headers': HEADERS, 'rows': sheet_rows, 'logs': logs,
                  'metadata': [['candidate-editor-v1', baseline['export_id']], *[[c, ''] for c in chunks]],
                  'counts': {k: len(v) for k, v in tables.items()},
                  'statuses': list(STATUS.values()), 'decisions': DECISIONS,
                  'external_decisions': list(EXTERNAL.values())}
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'workbook_input.json'
    path.write_bytes(json_bytes(input_data))
    return path


def col_index(ref):
    n = 0
    for c in re.match(r'[A-Z]+', ref)[0]:
        n = n * 26 + ord(c) - 64
    return n - 1


def column_map(rows):
    headers = rows.get(4, {})
    result = {}
    for field, label in zip(FIELDS, HEADERS):
        found = [i for i, v in headers.items() if v == label]
        if len(found) != 1:
            raise ValueError(f'列「{label}」が見つからないか重複しています。')
        result[field] = found[0]
    links = [i for i, v in headers.items() if v == 'YouTubeチャンネル']
    if links:
        result['_link'] = links[0]
    return result


def read_xlsx(path, canonical=True):
    """Read actual cell types; reject formulas in human-input tables."""
    with zipfile.ZipFile(path) as z:
        if any(i.file_size > 150_000_000 for i in z.infolist()) or sum(i.file_size for i in z.infolist()) > 400_000_000:
            raise ValueError('台帳の展開サイズが大きすぎます。')
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            shared = [''.join(n.itertext()) if n.tag.endswith('}t') else ''.join(t.text or '' for t in n.findall('.//s:t', NS)) for n in root]
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        workbook = ET.fromstring(z.read('xl/workbook.xml'))
        result = {}
        for sheet in workbook.findall('s:sheets/s:sheet', NS):
            target = rels[sheet.attrib['{' + REL + '}id']]
            member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            root = ET.fromstring(z.read(member))
            values = {}
            for r in root.findall('s:sheetData/s:row', NS):
                number = int(r.attrib['r'])
                cells = {}
                for c in r.findall('s:c', NS):
                    idx = col_index(c.attrib['r'])
                    if c.find('s:f', NS) is not None:
                        value = {'formula': c.findtext('s:f', default='', namespaces=NS)}
                    elif c.attrib.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in c.findall('.//s:t', NS))
                    elif c.attrib.get('t') == 's':
                        value = shared[int(c.findtext('s:v', namespaces=NS))]
                    elif c.attrib.get('t') in ('e', 'b'):
                        value = {'invalid_type': c.attrib['t']}
                    else:
                        value = c.findtext('s:v', default='', namespaces=NS)
                    cells[idx] = value
                values[number] = cells
            name = sheet.attrib['name']
            if canonical and name in SHEETS.values():
                mapping = column_map(values)
                reordered = {}
                for number, cells in values.items():
                    if number < 4:
                        reordered[number] = cells
                        continue
                    row = {i: cells.get(mapping[f], '') for i, f in enumerate(FIELDS)}
                    if number > 4 and row[4] == '不採用':
                        row[4] = '対象外'  # Existing DB/editor-state convention.
                    if '_link' in mapping:
                        row[16] = cells.get(mapping['_link'], '')
                    reordered[number] = row
                values = reordered
            result[name] = values
        return result


def layout_input(workbook, out):
    """Use the actual workbook, including pending edits and its original baseline."""
    sheets = read_xlsx(workbook)
    baseline = baseline_from(sheets)
    data = {
        'created_at': baseline['exported_at'], 'headers': HEADERS,
        'rows': {kind: [[cells.get(i, '') for i in range(len(FIELDS))]
                        for rn, cells in sorted(sheets[name].items()) if rn >= 5]
                 for kind, name in SHEETS.items()},
        'logs': [[cells.get(i, '') for i in range(7)]
                 for rn, cells in sorted(sheets['調査記録'].items()) if rn >= 5],
        'metadata': [[cells.get(i, '') for i in range(2)]
                     for rn, cells in sorted(sheets['_編集情報'].items())],
        'counts': {kind: sum(r['kind'] == kind for r in baseline['rows'].values())
                   for kind in ('main', 'external')},
        'statuses': list(STATUS.values()), 'decisions': DECISIONS,
        'external_decisions': list(EXTERNAL.values()), 'serial_dates': True,
    }
    if any(not isinstance(v, str) for rows in data['rows'].values() for row in rows for v in row):
        raise ValueError('入力欄の数式やエラーを先に修正してください。')
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'workbook_layout_input.json'
    target.write_bytes(json_bytes(data))
    return target


def baseline_from(sheets):
    meta = sheets.get('_編集情報', {})
    if meta.get(1, {}).get(0) != 'candidate-editor-v1':
        raise ValueError('この台帳には編集用の元データがありません。最新台帳を書き出してください。')
    chunks = [meta[n].get(0, '') for n in sorted(meta) if n > 1]
    if any(not isinstance(c, str) for c in chunks):
        raise ValueError('編集情報が破損しています。')
    obj = json.loads(zlib.decompress(base64.b64decode(''.join(chunks))).decode('utf-8'))
    if obj.get('version') != 1 or obj.get('export_id') != meta[1].get(1):
        raise ValueError('編集情報のバージョンまたは台帳IDが一致しません。')
    return obj


def read_inputs(sheets, baseline):
    inputs, additions, errors = {}, [], []
    slots = set(baseline['new_slots'])
    seen_slots = set()
    for kind, sheet_name in SHEETS.items():
        rows = sheets.get(sheet_name)
        if rows is None:
            errors.append(f'シート「{sheet_name}」がありません。')
            continue
        if [rows.get(4, {}).get(i, '') for i in range(len(HEADERS))] != HEADERS:
            errors.append(f'{sheet_name}: 列名・列順が変更されています。')
            continue
        for rn, cells in sorted(rows.items()):
            if rn <= 4:
                continue
            raw = [cells.get(i, '') for i in range(len(FIELDS))]
            if not any(v != '' for v in raw):
                continue
            if any(not isinstance(v, str) for v in raw):
                errors.append(f'{sheet_name} {rn}行: 数式・エラー・真偽値を入力欄に使用できません。')
                continue
            view = dict(zip(FIELDS, raw))
            k = view['key']
            if kind == 'new':
                if k not in slots or k in seen_slots:
                    errors.append(f'{sheet_name} {rn}行: 新規行の識別IDが変更・重複しています。')
                    continue
                seen_slots.add(k)
                if not view['name'].strip():
                    meaningful = [v for f, v in view.items() if f not in {'key', 'reading_status', 'dictionary_decision', 'entity_classification', 'decision'} and v.strip()]
                    if meaningful or view['reading_status'] != '未確認' or view['dictionary_decision'] != '未判断' or view['decision'] != '未解決':
                        errors.append(f'{sheet_name} {rn}行: 名前を入力してください。')
                    continue
                additions.append((k, view, sheet_name, rn))
            else:
                original = baseline['rows'].get(k)
                if not original or original['kind'] != kind or k in inputs:
                    errors.append(f'{sheet_name} {rn}行: 識別IDが変更・重複しています。')
                    continue
                inputs[k] = (kind, view, sheet_name, rn)
                readonly = set(FIELDS) - DB_EDITABLE[kind] - META_EDITABLE
                for field in readonly:
                    # Dates are exported as typed serials, never merged from Excel.
                    if field == 'last_checked':
                        continue
                    if view[field] != original['cells'].get(field, ''):
                        errors.append(f'{sheet_name} {rn}行: 保護対象「{HEADERS[FIELDS.index(field)]}」が変更されています。')
    missing = set(baseline['rows']) - set(inputs)
    if missing:
        errors.append(f'元の行が{len(missing)}件なくなっています。削除せず収録判断を「対象外」にしてください。')
    return inputs, additions, errors


def decode(field, value):
    if field == 'reading_status':
        inverse = {v: k for k, v in STATUS.items()}
        if value not in inverse:
            raise ValueError('確認状態をプルダウンから選んでください。')
        return inverse[value]
    if field == 'decision':
        inverse = {v: k for k, v in EXTERNAL.items()}
        if value not in inverse:
            raise ValueError('別管理の扱いをプルダウンから選んでください。')
        return inverse[value]
    if field == 'entity_classification' and value == '個人':
        return 'individual'
    return value


def valid_url(value):
    return bool(re.fullmatch(r'https?://[^\s]+', value))


def validate(kind, view, changed, is_new=False):
    errors = []
    if not view.get('name', '').strip():
        errors.append('名前が空欄です。')
    for field, value in view.items():
        if (is_new or field in changed) and isinstance(value, str) and (len(value) > 32767 or any(ord(c) < 32 and c not in '\n\r\t' for c in value)):
            errors.append(f'{field}: 長すぎる値または制御文字があります。')
    if view.get('dictionary_decision') not in DECISIONS:
        errors.append('収録判断をプルダウンから選んでください。')
    status = view.get('reading_status')
    if status not in STATUS:
        errors.append('確認状態が不正です。')
    evidence_changed = is_new or bool(changed & {'name', 'reading', 'reading_status', 'source_url', 'evidence_summary', 'identity_evidence', 'youtube_channel_id', 'decision'})
    if status == 'verified' and evidence_changed:
        if not re.fullmatch(r'[ぁ-ゖー]+', view.get('reading', '')):
            errors.append('確認済みの読みは、空欄にせず、ひらがな・長音で入力してください。')
        if not valid_url(view.get('source_url', '')) or not view.get('evidence_summary', '').strip():
            errors.append('確認済みにするには根拠URLと根拠の要点が必要です。')
        if kind == 'external' and not view.get('identity_evidence', '').strip():
            errors.append('別管理候補の確認済みには、本人確認の根拠が必要です。')
    if 'source_url' in changed and view.get('source_url') and not valid_url(view['source_url']):
        errors.append('根拠URLはhttp://またはhttps://で始まるURLにしてください。')
    if ('youtube_channel_id' in changed or is_new) and view.get('youtube_channel_id'):
        if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', view['youtube_channel_id']):
            errors.append('YouTube IDはUCから始まる24文字の固定IDにしてください。')
    if view.get('dictionary_decision') == '採用':
        if status != 'verified' or not view.get('reading') or not valid_url(view.get('source_url', '')) or not view.get('evidence_summary', '').strip():
            errors.append('収録判断を採用にするには、読みが確認済みで根拠URL・要点が必要です。')
        if kind == 'external' and (view.get('decision') != 'verified_external' or not view.get('identity_evidence', '').strip()):
            errors.append('別管理候補の採用には、別管理の扱いと本人確認の根拠も必要です。')
    if kind == 'external':
        if view.get('decision') not in EXTERNAL:
            errors.append('別管理の扱いが不正です。')
        if (status == 'verified') != (view.get('decision') == 'verified_external'):
            errors.append('確認済みと「確認済みの別管理候補」を合わせて変更してください。')
        if view.get('decision') == 'excluded' and view.get('dictionary_decision') == '採用':
            errors.append('対象外の候補を採用にできません。')
        if status != 'verified' and view.get('reading') and evidence_changed:
            errors.append('別管理候補の未確定の読みは「読み候補」に入力し、「読み」は空欄にしてください。')
    return errors


def preview(db, workbook, out):
    tables, state, hashes, _ = load_db(db)
    sheets = read_xlsx(workbook)
    baseline = baseline_from(sheets)
    inputs, additions, errors = read_inputs(sheets, baseline)
    maps = {k: {make_key(k, r): r for r in rows} for k, rows in tables.items()}
    changes = []
    for k, (kind, cells, sheet, rn) in inputs.items():
        old_cells = baseline['rows'][k]['cells']
        current = maps[kind].get(k)
        if current is None:
            errors.append(f'{sheet} {rn}行: 最新DBで識別IDが見つかりません。')
            continue
        current_view = editable_view(kind, current, state)
        updates = {}
        for field in DB_EDITABLE[kind] | META_EDITABLE:
            # Compare Excel text first; preserve unchanged original strings exactly.
            if cells[field] == old_cells.get(field, ''):
                continue
            try:
                before = baseline['rows'][k]['original'].get(field, '')
                after = decode(field, cells[field])
            except ValueError as e:
                errors.append(f'{sheet} {rn}行: {e}')
                continue
            latest = current_view.get(field, '')
            if latest == after:
                continue  # Already applied, or the same update arrived elsewhere.
            if latest != before:
                errors.append(f'{sheet} {rn}行 {HEADERS[FIELDS.index(field)]}: DBとExcelの両方で変更されています（DB={latest!r} / Excel={after!r}）。')
                continue
            updates[field] = {'before': latest, 'after': after}
        if not updates:
            continue
        merged = {**current_view, **{f: v['after'] for f, v in updates.items()}}
        errors.extend(f'{sheet} {rn}行: {e}' for e in validate(kind, merged, set(updates)))
        changes.append({'kind': kind, 'key': k, 'name': merged['name'], 'operation': 'update',
                        'updates': updates, 'sheet': sheet, 'row': rn})
    for slot, cells, sheet, rn in additions:
        kind = 'main' if cells['youtube_channel_id'] else 'external'
        try:
            values = {f: decode(f, cells[f]) for f in DB_EDITABLE[kind] | META_EDITABLE}
        except ValueError as e:
            errors.append(f'{sheet} {rn}行: {e}')
            continue
        if kind == 'main':
            values['youtube_channel_id'] = cells['youtube_channel_id']
            k = 'main:' + cells['youtube_channel_id']
        else:
            k = 'external:' + slot
        if state.get('new_origins', {}).get(slot) == k:
            existing = maps[kind].get(k)
            if existing is not None and editable_view(kind, existing, state) == {f: values.get(f, '') for f in DB_EDITABLE[kind] | META_EDITABLE}:
                continue
            errors.append(f'{sheet} {rn}行: この新規行は反映済みです。最新台帳の既存行を編集してください。')
            continue
        if k in maps[kind] or any(c['key'] == k for c in changes):
            errors.append(f'{sheet} {rn}行: 同じ固定IDの候補が既にあります。既存行を編集してください。')
            continue
        errors.extend(f'{sheet} {rn}行: {e}' for e in validate(kind, values, set(values), True))
        changes.append({'kind': kind, 'key': k, 'name': values['name'], 'operation': 'add',
                        'values': values, 'origin_slot': slot, 'sheet': sheet, 'row': rn})
    plan = {'version': 1, 'plan_id': str(uuid.uuid4()), 'created_at': now(), 'db': str(db.resolve()),
            'workbook': str(workbook.resolve()), 'workbook_sha256': file_hash(workbook),
            'export_id': baseline['export_id'], 'hashes': hashes,
            'changes': changes, 'errors': errors, 'applicable': not errors}
    out.mkdir(parents=True, exist_ok=True)
    (out / '反映計画.json').write_bytes(json_bytes(plan))
    write_report(out / '変更内容.html', plan)
    return plan


def write_report(path, plan):
    esc = lambda v: html.escape(str(v))
    rows = []
    for c in plan['changes']:
        for field, values in (c.get('updates') or {f: {'before': '', 'after': v} for f, v in c.get('values', {}).items()}).items():
            label = HEADERS[FIELDS.index(field)] if field in FIELDS else field
            rows.append('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in [c['sheet'], c['row'], c['name'], label, values['before'], values['after']]) + '</tr>')
    errors = ''.join('<li>' + esc(e) + '</li>' for e in plan['errors'])
    status = '反映できます' if plan['applicable'] else '反映できません。下記の項目を修正してください。'
    path.write_text('<!doctype html><html lang="ja"><meta charset="utf-8"><title>追加候補の変更内容</title><style>body{font:16px system-ui; margin:32px;color:#1f2937}table{border-collapse:collapse;width:100%}th{background:#243b53;color:white;position:sticky;top:0}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd;white-space:pre-wrap;overflow-wrap:anywhere}td{max-width:480px}li{color:#b42318;margin:12px 0}</style><h1>追加候補の変更内容</h1><p>' + esc(plan['created_at']) + '</p><p>' + esc(status) + f' 変更対象 {len(plan["changes"]):,}件。</p><p>採用は収録予定の判断です。辞書への登録は別の作業で行います。</p><ul>' + errors + '</ul><table><thead><tr><th>シート</th><th>行</th><th>名前</th><th>項目</th><th>現在のDB</th><th>編集後</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></html>', encoding='utf-8')


def new_row(kind, key, values, stamp):
    if kind == 'main':
        row = dict(name='', youtube_channel_id=key.removeprefix('main:'), reading='', reading_candidates='',
                   candidate_count='0', reading_status='unknown', reading_source='', source_url='',
                   match_method='', review_required='true', notes='', last_checked=stamp, evidence_summary='')
    else:
        row = dict(record_id=key.removeprefix('external:'), name='', youtube_channel_id='', supplied_identity_keys='[]',
                   identity_evidence='', reading='', reading_candidates='', reading_status='unknown',
                   review_required='true', entity_classification='individual', decision='unresolved',
                   source_url='', evidence_summary='', hold_reason='', import_details_id='')
    row.update({f: v for f, v in values.items() if f in DB_EDITABLE[kind]})
    return row


def apply_plan(db, plan_path):
    plan = read_json(plan_path)
    if plan.get('version') != 1 or not plan.get('applicable') or plan.get('errors'):
        raise ValueError('反映可能な計画ではありません。先に差分確認を実行してください。')
    if Path(plan['db']).resolve() != db.resolve():
        raise ValueError('反映計画の対象DBが違います。')
    if file_hash(Path(plan['workbook'])) != plan['workbook_sha256']:
        raise ValueError('差分確認後にExcelが変更されました。差分確認をやり直してください。')
    # Recompute a fresh plan. A hand-edited plan must never bypass validation.
    verification_out = plan_path.parent / 'validation'
    fresh = preview(db, Path(plan['workbook']), verification_out)
    if not fresh['applicable'] or fresh['changes'] != plan['changes'] or fresh['hashes'] != plan['hashes']:
        raise ValueError('差分確認後にDBまたは計画が変更されました。差分確認をやり直してください。')
    if not plan['changes']:
        return {'changes': 0, 'message': '変更はありません。DBは更新していません。'}
    lock = db / '.candidate-editor.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('別の反映処理が実行中です。中断した処理がある場合はバックアップを確認してください。')
    os.close(fd)
    try:
        tables, state, hashes, raw = load_db(db)
        if hashes != plan['hashes']:
            raise ValueError('DBが更新されました。差分確認をやり直してください。')
        stamp = now()
        overrides = read_json(db / 'candidate_manual_overrides.json', {})
        history = read_json(db / 'candidate_editor_history.json', [])
        maps = {kind: {make_key(kind, r): r for r in rows} for kind, rows in tables.items()}
        touched_tables = set()
        for change in plan['changes']:
            kind, k = change['kind'], change['key']
            updates = {f: v['after'] for f, v in change.get('updates', {}).items()}
            if change['operation'] == 'add':
                updates = change['values']
                row = new_row(kind, k, updates, stamp)
                tables[kind].append(row)
                maps[kind][k] = row
                state.setdefault('new_origins', {})[change['origin_slot']] = k
                touched_tables.add(kind)
            else:
                row = maps[kind][k]
            before = copy.deepcopy(row)
            row.update({f: v for f, v in updates.items() if f in DB_EDITABLE[kind]})
            editorial = state['records'].setdefault(k, {})
            editorial.update({f: v for f, v in updates.items() if f in META_EDITABLE})
            editorial['updated_at'] = stamp
            facts = set(updates) & DB_EDITABLE[kind]
            if facts or change['operation'] == 'add':
                touched_tables.add(kind)
                row['review_required'] = 'false' if row['reading_status'] == 'verified' else 'true'
                if kind == 'main':
                    row['last_checked'] = stamp
                    if facts & {'name', 'reading', 'reading_status', 'source_url', 'evidence_summary'} or change['operation'] == 'add':
                        row['reading_source'] = 'human_excel_review'
                        row['match_method'] = 'human_review_by_stable_id'
                        overrides[row['youtube_channel_id']] = {'name': row['name'], 'reading': row['reading'],
                            'reading_status': row['reading_status'], 'source_url': row['source_url'],
                            'evidence_summary': row['evidence_summary'], 'reviewed_at': stamp, 'record_id': k}
                    if 'reading_candidates' in facts or change['operation'] == 'add':
                        row['candidate_count'] = str(len({v.strip() for v in re.split(r'\s*\|\s*|\s*/\s*', row['reading_candidates']) if v.strip()}))
            history.append({'plan_id': plan['plan_id'], 'applied_at': stamp, 'key': k, 'operation': change['operation'],
                            'changes': change, 'before': before if change['operation'] != 'add' else None,
                            'after': copy.deepcopy(row), 'editorial_after': copy.deepcopy(editorial)})
        output = {}
        for kind, prefix in [('main', 'vtuberpost_readings'), ('external', 'candidates_external')]:
            if kind in touched_tables:
                columns = list(tables[kind][0])
                output[prefix + '.json'] = json_bytes(tables[kind])
                output[prefix + '.tsv'] = tsv_bytes(tables[kind], columns)
                assert tsv_rows(output[prefix + '.tsv']) == json.loads(output[prefix + '.json'])
        state['updated_at'] = stamp
        output['candidate_editor_state.json'] = json_bytes(state)
        output['candidate_editor_history.json'] = json_bytes(history)
        if overrides != read_json(db / 'candidate_manual_overrides.json', {}):
            output['candidate_manual_overrides.json'] = json_bytes(overrides)
        index = read_json(db / 'candidate_db_index.json', {'version': 1, 'tables': {}})
        index['updated_at'] = stamp
        index['tables']['youtube_readings']['rows'] = len(tables['main'])
        index['tables']['external_candidates']['rows'] = len(tables['external'])
        index['tables']['editorial_state'] = {'json': 'candidate_editor_state.json', 'key': 'namespaced stable ID', 'rows': len(state['records'])}
        index['tables']['editorial_history'] = {'json': 'candidate_editor_history.json', 'rows': len(history)}
        if overrides:
            index['tables']['human_review_overrides'] = {'json': 'candidate_manual_overrides.json',
                                                        'key': 'youtube_channel_id', 'rows': len(overrides)}
        index['editorial_updated_at'] = stamp
        output['candidate_db_index.json'] = json_bytes(index)
        if 'main' in touched_tables:
            counts = dict(Counter(r['reading_status'] for r in tables['main']))
            report = read_json(db / 'vtuberpost_readings_report.json', {})
            report.update(updated_at=stamp, output_rows=len(tables['main']), status_counts=counts,
                          unique_youtube_channel_ids=len(tables['main']), tsv_json_consistent=True,
                          last_editor_plan=plan['plan_id'])
            for item in report.get('outputs', []):
                name = Path(item.get('path', '')).name
                if name in output:
                    item.update(rows=len(tables['main']), sha256=digest(output[name]))
            output['vtuberpost_readings_report.json'] = json_bytes(report)
            cp = read_json(db / 'vtuberpost_readings_checkpoint.json', {})
            cp.update(updated_at=stamp, records=len(tables['main']), last_editor_plan=plan['plan_id'])
            output['vtuberpost_readings_checkpoint.json'] = json_bytes(cp)
        backup = db / 'editor_backups' / (datetime.now(JST).strftime('%Y%m%d_%H%M%S') + '_' + plan['plan_id'][:8])
        backup.mkdir(parents=True)
        manifest = {'version': 1, 'plan_id': plan['plan_id'], 'created_at': stamp, 'files': {}, 'status': 'prepared'}
        for name, data in output.items():
            old = raw[name]
            manifest['files'][name] = {'before_sha256': hashes[name], 'after_sha256': digest(data)}
            if old is not None:
                (backup / name).write_bytes(old)
            (backup / ('after_' + name)).write_bytes(data)
        (backup / 'plan.json').write_bytes(json_bytes(plan))
        (backup / 'manifest.json').write_bytes(json_bytes(manifest))
        if {n: file_hash(db / n) for n in FILES} != hashes:
            raise ValueError('バックアップ作成中にDBが更新されました。差分確認をやり直してください。')
        replaced = []
        try:
            for name, data in output.items():
                temp = db / (name + '.editor-' + plan['plan_id'] + '.tmp')
                temp.write_bytes(data)
                if file_hash(db / name) != hashes[name]:
                    raise ValueError(f'反映中に{name}が更新されました。差分確認をやり直してください。')
                os.replace(temp, db / name)
                replaced.append(name)
            # Read all generated pairs back from disk, with the same consistency gate.
            load_db(db)
        except BaseException:
            for name in reversed(replaced):
                # Never silently restore over an unrelated concurrent modification.
                if file_hash(db / name) != digest(output[name]):
                    manifest['status'] = 'rollback_needs_review'
                    (backup / 'manifest.json').write_bytes(json_bytes(manifest))
                    raise RuntimeError(f'同時更新を検知。自動復元を停止しました。バックアップ: {backup}')
                if raw[name] is None:
                    (db / name).unlink()
                else:
                    temp = db / (name + '.rollback.tmp')
                    temp.write_bytes(raw[name])
                    os.replace(temp, db / name)
            manifest['status'] = 'rolled_back'
            (backup / 'manifest.json').write_bytes(json_bytes(manifest))
            raise
        manifest['status'] = 'applied'
        (backup / 'manifest.json').write_bytes(json_bytes(manifest))
        return {'changes': len(plan['changes']), 'backup': str(backup), 'message': 'DBへの反映が完了しました。最新台帳を書き出して、続きの編集を行ってください。'}
    finally:
        lock.unlink(missing_ok=True)


def protect_xlsx(path):
    """Add missing native protection capability to artifact-authored OOXML.

    Input IDs stay locked; input cells stay editable. Filtering and sorting are
    allowed. Excel may restrict sorting a protected range containing locked IDs;
    unprotecting has no password and the importer still verifies all IDs.
    """
    physical = read_xlsx(path, canonical=False)
    with zipfile.ZipFile(path) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    ns = NS['s']
    tag = lambda n: '{' + ns + '}' + n
    styles = ET.fromstring(parts['xl/styles.xml'])
    xfs = styles.find('s:cellXfs', NS)
    unlocked = {}
    for i, xf in enumerate(list(xfs)):
        new = copy.deepcopy(xf)
        p = new.find('s:protection', NS)
        if p is None:
            p = ET.SubElement(new, tag('protection'))
        p.set('locked', '0')
        new.set('applyProtection', '1')
        unlocked[i] = len(xfs)
        xfs.append(new)
    xfs.set('count', str(len(xfs)))
    parts['xl/styles.xml'] = ET.tostring(styles, encoding='utf-8', xml_declaration=True)
    wb = ET.fromstring(parts['xl/workbook.xml'])
    # HYPERLINK is a native Excel function not evaluated by the authoring runtime.
    # Save its friendly label and ask Excel to recalculate the live URL on open.
    calc = wb.find('s:calcPr', NS)
    if calc is None:
        calc = ET.SubElement(wb, tag('calcPr'))
    calc.set('calcMode', 'auto')
    calc.set('fullCalcOnLoad', '1')
    calc.set('forceFullCalc', '1')
    strings=[]
    if 'xl/sharedStrings.xml' in parts:
        strings=[''.join(t.text or '' for t in si.findall('.//s:t',NS))
                 for si in ET.fromstring(parts['xl/sharedStrings.xml'])]
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(parts['xl/_rels/workbook.xml.rels'])}
    for sheet in wb.findall('s:sheets/s:sheet', NS):
        name = sheet.attrib['name']
        if name == '_編集情報':
            sheet.set('state', 'veryHidden')
        target = rels[sheet.attrib['{' + REL + '}id']]
        member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        xml = ET.fromstring(parts[member])
        kind = next((k for k, n in SHEETS.items() if n == name), None)
        if kind:
            mapping = column_map(physical[name])
            by_column = {idx: field for field, idx in mapping.items()}
            fields = (DB_EDITABLE['external'] | META_EDITABLE | {'youtube_channel_id'}) if kind == 'new' else DB_EDITABLE[kind] | META_EDITABLE
            for r in xml.findall('s:sheetData/s:row', NS):
                if int(r.attrib['r']) <= 4:
                    continue
                for c in r.findall('s:c', NS):
                    idx = col_index(c.attrib['r'])
                    if by_column.get(idx) in fields:
                        c.set('s', str(unlocked[int(c.attrib.get('s', '0'))]))
                cells={col_index(c.attrib['r']):c for c in r.findall('s:c',NS)}
                link=cells.get(mapping.get('_link'))
                if link is not None and 'HYPERLINK("https://www.youtube.com/channel/"' in link.findtext('s:f',default='',namespaces=NS):
                    channel=cells.get(mapping['youtube_channel_id'])
                    cid=''
                    if channel is not None:
                        if channel.attrib.get('t')=='s':
                            cid=strings[int(channel.findtext('s:v',namespaces=NS))]
                        elif channel.attrib.get('t')=='inlineStr':
                            cid=''.join(t.text or '' for t in channel.findall('.//s:t',NS))
                        else:
                            cid=channel.findtext('s:v',default='',namespaces=NS)
                    cached=link.find('s:v',NS)
                    if cached is None:
                        cached=ET.SubElement(link,tag('v'))
                    link.set('t','str')
                    cached.text='チャンネルを開く' if len(cid)==24 and cid.startswith('UC') else ''
        existing = xml.find('s:sheetProtection', NS)
        if existing is not None:
            xml.remove(existing)
        protection = ET.Element(tag('sheetProtection'), sheet='1', objects='1', scenarios='1',
                                autoFilter='0', sort='0', selectLockedCells='0', selectUnlockedCells='0')
        # sheetProtection belongs immediately after sheetData (before filters).
        data = xml.find('s:sheetData', NS)
        xml.insert(list(xml).index(data) + 1, protection)
        validations = xml.find('s:dataValidations', NS)
        if validations is not None:
            for v in validations:
                v.set('showErrorMessage', '1')
                v.set('errorStyle', 'stop')
                v.set('errorTitle', '選択肢を確認してください')
                v.set('error', 'プルダウンから選択してください。')
                v.set('showDropDown', '0')
        parts[member] = ET.tostring(xml, encoding='utf-8', xml_declaration=True)
    parts['xl/workbook.xml'] = ET.tostring(wb, encoding='utf-8', xml_declaration=True)
    temp = path.with_suffix('.protected.tmp')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, b in parts.items():
            z.writestr(n, b)
    os.replace(temp, path)


def cli():
    p = argparse.ArgumentParser(description='追加候補のExcel編集台帳')
    p.add_argument('--db', type=Path, default=DEFAULT_DB)
    p.add_argument('--out', type=Path, default=DEFAULT_OUT)
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    e = commands.add_parser('layout-input')
    e.add_argument('workbook', type=Path)
    e = commands.add_parser('protect')
    e.add_argument('workbook', type=Path)
    e = commands.add_parser('preview')
    e.add_argument('workbook', type=Path, nargs='?', default=DEFAULT_OUT / BOOK_NAME)
    e = commands.add_parser('apply')
    e.add_argument('plan', type=Path, nargs='?', default=DEFAULT_OUT / '反映計画.json')
    args = p.parse_args()
    try:
        if args.command == 'prepare':
            print(export_input(args.db, args.out))
        elif args.command == 'layout-input':
            print(layout_input(args.workbook, args.out))
        elif args.command == 'protect':
            protect_xlsx(args.workbook)
            print('入力欄・識別IDの保護を設定しました。')
        elif args.command == 'preview':
            plan = preview(args.db, args.workbook, args.out)
            print(f'変更対象 {len(plan["changes"]):,}件 / 修正が必要な項目 {len(plan["errors"]):,}件')
            for error in plan['errors'][:12]:
                print(error)
            print(args.out / '変更内容.html')
            if plan['errors']:
                return 2
        else:
            print(json.dumps(apply_plan(args.db, args.plan), ensure_ascii=False))
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError) as e:
        print('処理を停止しました: ' + str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(cli())
