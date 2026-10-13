"""Candidate DB saves and the separate legacy daily dictionary workflow.

No network or Git operations. Candidate saves never build dictionary files.
Legacy dictionary plans build in a disposable project before applying.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import editor

DEFAULT_OUT = editor.ROOT / 'outputs/daily-dictionary'
BOOK_NAME = '今日の候補.xlsx'
SHEET = '今日の候補'
HEADERS = ['名前', '読み', '収録判断', '根拠の要点', '根拠URL', '判断メモ', 'YouTubeチャンネル', '識別ID']
DECISIONS = ['未判断', '採用', '保留', '不採用']
MASTER_COLUMNS = ['reading', 'word', 'google_pos', 'microsoft_pos', 'atok_pos', 'origin', 'source_url', 'note', 'added_on']


def master_rows(root):
    return editor.tsv_rows((root / 'data/master.tsv').read_bytes())


def candidates(db, root, include_held=False, include_existing=False):
    tables, state, hashes, _ = editor.load_db(db)
    rows = master_rows(root)
    pairs = {(r['reading'], r['word']) for r in rows}
    names = {r['word'] for r in rows}
    eligible, excluded = [], {}
    name_counts = {}
    for records in tables.values():
        for r in records:
            name_counts[r['name']] = name_counts.get(r['name'], 0) + 1
    for kind, records in tables.items():
        for record in records:
            view = editor.editable_view(kind, record, state)
            reason = None
            if view['dictionary_decision'] in ('保留', '対象外') and not include_held:
                reason = '保留・不採用'
            elif view.get('reading_status') != 'verified' or record.get('review_required') != 'false':
                reason = '確認が必要'
            elif (view.get('reading'), view.get('name')) in pairs and not include_existing:
                reason = '登録済み'
            elif view.get('name') in names and not include_existing:
                reason = '同名が収録済み（別途確認）'
            elif not re.fullmatch(r'[ぁ-ゖー]+', view.get('reading', '')):
                reason = '読みの形式を確認'
            elif not editor.valid_url(view.get('source_url', '')) or not view.get('evidence_summary', '').strip():
                reason = '根拠が不足'
            elif not view.get('name', '').strip() or view['name'] != view['name'].strip() or any(ord(c) < 32 for c in view['name']):
                reason = '名前の形式を確認'
            elif name_counts[view['name']] > 1:
                reason = '候補内に同名あり（別途確認）'
            elif kind == 'main' and re.search(r'グループ|ユニット|団体|group|unit', view['evidence_summary'], re.IGNORECASE):
                reason = '個人・団体の区分を確認'
            elif kind == 'external' and (view.get('decision') != 'verified_external'
                    or not view.get('identity_evidence', '').strip()
                    or view.get('entity_classification') != 'individual'):
                reason = '本人・区分の確認が必要'
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            key = editor.make_key(kind, record)
            eligible.append({'key': key, 'kind': kind, 'name': view['name'],
                'reading': view['reading'], 'source_url': view['source_url'],
                'evidence_summary': view['evidence_summary'],
                'youtube_channel_id': record.get('youtube_channel_id', ''),
                'dictionary_decision': view['dictionary_decision'],
                'decision_reason': view.get('decision_reason', '')})
    eligible.sort(key=lambda r: (r['dictionary_decision'] != '採用', r['name'], r['key']))
    return eligible, excluded, hashes


def cells(record):
    channel = record['youtube_channel_id']
    url = 'https://www.youtube.com/channel/' + channel if re.fullmatch(r'UC[A-Za-z0-9_-]{22}', channel) else ''
    return [record['name'], record['reading'], record['dictionary_decision'],
        record['evidence_summary'], record['source_url'], record['decision_reason'], url, record['key']]


def prepare(db, root, out, count=10, randomize=False, exclude_approved=False):
    if not 1 <= count <= 100:
        raise ValueError('件数は1〜100語で指定してください。')
    latest = out / 'latest.json'
    if latest.exists():
        previous = Path(editor.read_json(latest)['batch'])
        if not (previous / '完了.json').exists():
            raise ValueError('前の候補一覧が作業中です。保存して内容確認・反映を行ってください。前のExcelは残っています。')
    pool, excluded, hashes = candidates(db, root)
    if exclude_approved:
        excluded['追加予定として保存済み'] = sum(r['dictionary_decision'] == '採用' for r in pool)
        pool = [r for r in pool if r['dictionary_decision'] != '採用']
    selected = secrets.SystemRandom().sample(pool, min(count, len(pool))) if randomize else pool[:count]
    if not selected:
        raise ValueError('日次一覧へ出せる確認済みの未収録候補がありません。既存台帳で確認・採用判断を進めてください。')
    batch = out / ('batches/' + editor.now()[:10] + '_' + uuid.uuid4().hex[:8])
    batch.mkdir(parents=True)
    manifest = {'version': 1, 'batch_id': batch.name, 'created_at': editor.now(),
        'root': str(root.resolve()), 'db': str(db.resolve()), 'records': selected,
        'db_hashes': hashes, 'master_hash': editor.file_hash(root / 'data/master.tsv'),
        'eligible_count': len(pool), 'excluded_counts': excluded}
    (batch / '候補情報.json').write_bytes(editor.json_bytes(manifest))
    data = {**manifest, 'headers': HEADERS, 'cells': [cells(r) for r in selected]}
    (batch / 'daily_input.json').write_bytes(editor.json_bytes(data))
    return batch


def review_info(book):
    """Browser queues can grow; keep their records and choices in one atomic file."""
    info = editor.read_json(book.parent / '候補情報.json')
    if book.suffix == '.json' and book.exists():
        document = editor.read_json(book)
        records = document.get('candidate_records')
        if records is not None:
            if not isinstance(records, list) or not records or any(not isinstance(r, dict) or not isinstance(r.get('key'), str) for r in records):
                raise ValueError('アプリの候補情報が不正です。')
            if len({r['key'] for r in records}) != len(records):
                raise ValueError('アプリの候補が重複しています。')
            info = {**info, 'records': records}
        recalled = document.get('recalled_keys', [])
        if not isinstance(recalled, list) or any(not isinstance(k, str) for k in recalled) or not set(recalled) <= {r['key'] for r in info['records']}:
            raise ValueError('呼び出した候補の記録が不正です。')
        info = {**info, 'recalled_keys': recalled}
    return info


def activate(batch, out):
    info = editor.read_json(batch / '候補情報.json')
    book = batch / BOOK_NAME
    if not book.is_file():
        raise ValueError('候補Excelがまだ生成されていません。')
    read_review(book, info)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'latest.json').write_bytes(editor.json_bytes({'batch': str(batch.resolve())}))


def read_review(book, info):
    if book.suffix == '.json':
        document = editor.read_json(book)
        if not isinstance(document, dict) or document.get('version') != 1 or document.get('batch_id') != info['batch_id']:
            raise ValueError('アプリの作業記録と候補一覧が一致していません。')
        excel = book.parent / BOOK_NAME
        if document.get('excel_hash') and editor.file_hash(excel) != document['excel_hash']:
            raise ValueError('引き継いだExcelが変更されました。Excelとアプリの同時編集を避け、変更内容を確認してください。')
        return validate_review(document.get('entries'), info)
    sheets = editor.read_xlsx(book, canonical=False)
    if sheets.get('_日次情報', {}).get(1, {}).get(1) != info['batch_id']:
        raise ValueError('Excelと候補情報が一致していません。')
    sheet = sheets.get(SHEET, {})
    if [sheet.get(5, {}).get(i, '') for i in range(8)] != HEADERS:
        raise ValueError('今日の候補の見出しが変更されています。')
    records = {r['key']: r for r in info['records']}
    seen, review = set(), []
    for number, row in sheet.items():
        if number <= 5 or not any(row.get(i, '') != '' for i in range(8)):
            continue
        key = row.get(7, '')
        if not isinstance(key, str) or key not in records or key in seen:
            raise ValueError(f'{number}行: 識別IDが変更・追加・重複しています。')
        seen.add(key)
        expected = cells(records[key])
        for i in (0, 1, 3, 4, 6, 7):
            if row.get(i, '') != expected[i]:
                raise ValueError(f'{number}行: 名前・読み・根拠などの参照欄は変更できません。修正は元の候補台帳で行ってください。')
        decision, memo = row.get(2, ''), row.get(5, '')
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError(f'{number}行: 収録判断はプルダウンから選んでください。')
        if not isinstance(memo, str) or len(memo) > 32767 or any(ord(c) < 32 and c not in '\r\n\t' for c in memo):
            raise ValueError(f'{number}行: 判断メモが不正です。')
        review.append({'key': key, 'decision': decision, 'memo': memo})
    if seen != set(records):
        raise ValueError('候補の行が削除されています。保留または不採用を選んでください。')
    return sorted(review, key=lambda r: r['key'])


def validate_review(entries, info):
    """Use the same stable IDs and judgement rules for the browser front end."""
    if not isinstance(entries, list):
        raise ValueError('判断の一覧が不正です。')
    expected = {r['key'] for r in info['records']}
    seen, result = set(), []
    for entry in entries:
        if not isinstance(entry, dict) or not {'key', 'decision', 'memo'} <= set(entry) <= {'key', 'decision', 'memo', 'registration'}:
            raise ValueError('判断の項目が不正です。')
        key, decision, memo = entry['key'], entry['decision'], entry['memo']
        if not isinstance(key, str) or key not in expected or key in seen:
            raise ValueError('候補の識別IDが変更・追加・重複しています。')
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError('採用・保留・不採用・未判断から選んでください。')
        if not isinstance(memo, str) or len(memo) > 32767 or any(ord(c) < 32 and c not in '\r\n\t' for c in memo):
            raise ValueError('判断メモが不正です。')
        if 'registration' in entry:
            validate_registration(entry['registration'])
        seen.add(key)
        result.append(dict(entry))
    if seen != expected:
        raise ValueError('候補の判断が抜けています。未判断のまま残すこともできます。')
    return sorted(result, key=lambda r: r['key'])


def validate_registration(form):
    if not isinstance(form, dict) or set(form) != {'name', 'reading', 'aliases'}:
        raise ValueError('登録内容の形式が不正です。')
    def word(value):
        return isinstance(value, str) and 0 < len(value) <= 200 and value == value.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    def reading(value):
        return isinstance(value, str) and len(value) <= 200 and re.fullmatch(r'[ぁ-ゖー]+', value)
    if not word(form['name']):
        raise ValueError('名前は空欄や改行を含めず、200文字以内で入力してください。')
    if not reading(form['reading']):
        raise ValueError('読みはひらがな・長音で入力してください。')
    aliases = form['aliases']
    if not isinstance(aliases, list) or len(aliases) > 20:
        raise ValueError('別の変換は20件まで追加できます。')
    pairs = {(form['reading'], form['name'])}
    for row in aliases:
        if not isinstance(row, dict) or set(row) != {'reading', 'word'} or not word(row['word']) or not reading(row['reading']):
            raise ValueError('別の変換には、ひらがなの読みと変換される名前を入力してください。')
        pair = (row['reading'], row['word'])
        if pair in pairs:
            raise ValueError('同じ読みと名前の組み合わせが重複しています。')
        pairs.add(pair)
    return form


def source_hashes(root):
    paths = [root / 'scripts/build.py', root / 'scripts/build_site.py',
             root / 'package/README.txt', root / 'NOTICE.md', *sorted((root / 'site').rglob('*'))]
    return {str(p.relative_to(root)): editor.file_hash(p) for p in paths if p.is_file()}


def calculate_plan(db, root, book, *, target='dictionary'):
    if target not in ('dictionary', 'candidate_db'):
        raise ValueError('保存先が不正です。')
    candidate_only = target == 'candidate_db'
    info = review_info(book)
    if info.get('version') != 1 or Path(info['root']).resolve() != root.resolve() or Path(info['db']).resolve() != db.resolve():
        raise ValueError('候補一覧の対象辞書・DBが違います。')
    if (book.parent / '完了.json').exists():
        raise ValueError('この一覧は反映済みです。次の候補一覧を作成してください。')
    review = read_review(book, info)
    pool, _, hashes = candidates(db, root, include_existing=candidate_only)
    lookup = {r['key']: r for r in pool}
    if info.get('recalled_keys'):
        recalled_pool, _, _ = candidates(db, root, include_held=True, include_existing=True)
        lookup.update({r['key']: r for r in recalled_pool if r['key'] in info['recalled_keys']})
    recalled_views = {}
    if info.get('recalled_keys'):
        tables, metadata, _, _ = editor.load_db(db)
        for kind, rows in tables.items():
            for row in rows:
                key = editor.make_key(kind, row)
                if key not in info['recalled_keys']:
                    continue
                view = editor.editable_view(kind, row, metadata)
                recalled_views[key] = {'key': key, 'kind': kind, 'name': view['name'], 'reading': view['reading'],
                    'source_url': view['source_url'], 'evidence_summary': view['evidence_summary'],
                    'youtube_channel_id': row.get('youtube_channel_id', ''),
                    'dictionary_decision': view['dictionary_decision'], 'decision_reason': view.get('decision_reason', '')}
    existing = {(r['reading'], r['word']) for r in master_rows(root)}
    planned = set()
    existing_names = {word for _, word in existing}
    additions, actions, errors, saved_conversions = [], [], [], []
    adopted_names = {}
    for action in review:
        old = next(r for r in info['records'] if r['key'] == action['key'])
        if action['decision'] == '未判断':
            continue
        current = (recalled_views.get(action['key']) if action['decision'] != '採用' and action['key'] in recalled_views
                   else lookup.get(action['key']))
        if current != old:
            reason = ('読み・出典・本人情報の確認が済んでいません。元の台帳で確認してください。'
                      if action['decision'] == '採用' and recalled_views.get(action['key']) == old
                      else '候補情報・収録判断・掲載状況が変わりました。元の台帳で確認してください。')
            errors.append(old['name'] + ': ' + reason)
            continue
        actions.append(action)
        if action['decision'] == '採用':
            form = action.get('registration', {'name': old['name'], 'reading': old['reading'], 'aliases': []})
            validate_registration(form)
            if form['name'] in adopted_names and adopted_names[form['name']] != action['key']:
                errors.append(form['name'] + ': 別の候補と同じ名前になっています。本人の区別を確認してください。')
                continue
            adopted_names[form['name']] = action['key']
            conversions = [{'reading': form['reading'], 'word': form['name']}, *form['aliases']]
            # A fully registered selection only saves candidate edits/decisions;
            # it adds no conversion and cannot introduce a same-name collision.
            already_registered = all((r['reading'], r['word']) in existing for r in conversions)
            if not candidate_only and form['name'] != old['name'] and form['name'] in existing_names and not already_registered:
                errors.append(form['name'] + ': 変更先の同名が辞書にあります。本人の区別を確認してください。')
                continue
            note = re.sub(r'[\t\r\n]+', ' ', old['evidence_summary']).strip()
            for conversion in conversions:
                pair = (conversion['reading'], conversion['word'])
                if pair in planned or (not candidate_only and pair in existing):
                    continue
                planned.add(pair)
                if candidate_only:
                    saved_conversions.append(conversion)
                    continue
                additions.append({**conversion, 'google_pos': '人名', 'microsoft_pos': '人名', 'atok_pos': '人名',
                    'origin': 'added', 'source_url': old['source_url'], 'note': note,
                    'added_on': editor.now()[:10]})
    additions.sort(key=lambda r: (r['word'], r['reading']))
    return {'version': 1, 'target': target, 'root': str(root.resolve()), 'db': str(db.resolve()),
        'workbook': str(book.resolve()), 'workbook_hash': editor.file_hash(book),
        'manifest_hash': editor.file_hash(book.parent / '候補情報.json'),
        'master_hash': None if candidate_only else editor.file_hash(root / 'data/master.tsv'), 'db_hashes': hashes,
        'build_hashes': {} if candidate_only else source_hashes(root), 'actions': actions, 'additions': additions,
        **({'conversions': saved_conversions} if candidate_only else {}),
        'errors': errors, 'applicable': not errors, 'date': editor.now()[:10]}


def preview(db, root, book):
    plan = calculate_plan(db, root, book)
    (book.parent / '辞書反映計画.json').write_bytes(editor.json_bytes(plan))
    esc = editor.html.escape
    rows = ''.join('<tr>' + ''.join('<td>' + esc(r[k]) + '</td>' for k in ('reading', 'word', 'source_url', 'note')) + '</tr>' for r in plan['additions'])
    text = '<!doctype html><html lang="ja"><meta charset="utf-8"><title>今日の辞書追加</title><style>body{font:16px system-ui;margin:32px;color:#243b53}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}th{background:#edf2f7}li{color:#a22}</style>'
    text += f'<h1>今日の辞書追加</h1><p>追加 {len(plan["additions"])}語。判断の保存 {len(plan["actions"])}件。</p>'
    text += '<p>選択した名前・読みと、入力した別の変換を登録します。既にある同じ変換は重複追加しません。Web公開は別の操作です。</p>'
    text += '<ul>' + ''.join('<li>' + esc(e) + '</li>' for e in plan['errors']) + '</ul>'
    text += '<table><tr><th>読み</th><th>名前</th><th>根拠URL</th><th>根拠の要点</th></tr>' + rows + '</table>'
    (book.parent / '辞書への追加内容.html').write_text(text, encoding='utf-8')
    return plan


def preview_candidates(db, root, book):
    """Review a candidate-DB save independently of dictionary generation."""
    plan = calculate_plan(db, root, book, target='candidate_db')
    (book.parent / '候補DB保存計画.json').write_bytes(editor.json_bytes(plan))
    return plan


def build_staged(root, additions, stage):
    for name in ('scripts', 'package', 'site'):
        shutil.copytree(root / name, stage / name, ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(root / 'NOTICE.md', stage / 'NOTICE.md')
    (stage / 'data').mkdir()
    columns = list(master_rows(root)[0])
    (stage / 'data/master.tsv').write_bytes(editor.tsv_bytes(master_rows(root) + additions, columns))
    for script in ('build.py', 'build_site.py'):
        result = subprocess.run([sys.executable, str(stage / 'scripts' / script)],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise ValueError('辞書・サイトの生成に失敗しました: ' + result.stdout + result.stderr)
    return [p for name in ('data', 'dist', 'public') for p in sorted((stage / name).rglob('*')) if p.is_file()]


def replace_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.daily-' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def apply(db, root, plan_path):
    return _apply_review(db, root, plan_path, mode='dictionary')


def save_candidates(db, root, plan_path):
    """Save reviewed choices and conversions only to the separate candidate DB."""
    return _apply_review(db, root, plan_path, mode='candidate_db')


def _apply_review(db, root, plan_path, *, mode):
    plan = editor.read_json(plan_path)
    if not plan or not plan.get('applicable'):
        raise ValueError('反映できる計画がありません。先に内容確認を行ってください。')
    if plan.get('target', 'dictionary') != mode:
        raise ValueError('保存先が異なる計画です。内容確認をやり直してください。')
    book = Path(plan['workbook'])
    fresh = calculate_plan(db, root, book, target=mode)
    if fresh != plan:
        raise ValueError('確認後に判断・候補DB・辞書・生成元が変更されました。内容確認をやり直してください。')
    # Both editors share this lock. Research scripts must also be run separately.
    lock = db / '.candidate-editor.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('候補DBの反映処理が実行中です。完了後に再実行してください。')
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory(prefix='fvtd-daily-', dir=book.parent) as temporary:
            stage = Path(temporary).resolve()
            stage.relative_to(book.parent.resolve())  # Bound recursive build/cleanup to this batch.
            outputs = {}
            if mode == 'dictionary' and plan['additions']:
                for p in build_staged(root, plan['additions'], stage):
                    outputs[root / p.relative_to(stage)] = p.read_bytes()
            state = editor.read_json(db / 'candidate_editor_state.json', {'version': 1, 'records': {}, 'new_origins': {}})
            history = editor.read_json(db / 'candidate_editor_history.json', [])
            stamp = editor.now()
            tables, _, _, _ = editor.load_db(db)
            maps = {kind: {editor.make_key(kind, r): r for r in rows} for kind, rows in tables.items()}
            touched = set()
            overrides = editor.read_json(db / 'candidate_manual_overrides.json', {})
            for action in plan['actions']:
                key = action['key']
                kind = key.split(':', 1)[0]
                row = maps[kind][key]
                candidate_before = copy.deepcopy(row)
                before = copy.deepcopy(state['records'].get(key, {}))
                after = state['records'].setdefault(key, {})
                after.update(dictionary_decision='対象外' if action['decision'] == '不採用' else action['decision'],
                    decision_reason=action['memo'], updated_at=stamp)
                if action.get('registration'):
                    form = action['registration']
                    after['extra_conversions'] = copy.deepcopy(form['aliases'])
                    if form['name'] != row['name'] or form['reading'] != row['reading']:
                        row.update(name=form['name'], reading=form['reading'])
                        touched.add(kind)
                        if kind == 'main':
                            row.update(last_checked=stamp, reading_source='human_app_review', match_method='human_review_by_stable_id')
                            overrides[row['youtube_channel_id']] = {'name': row['name'], 'reading': row['reading'],
                                'reading_status': row['reading_status'], 'source_url': row['source_url'],
                                'evidence_summary': row['evidence_summary'], 'reviewed_at': stamp, 'record_id': key}
                history.append({'operation': 'candidate_db_review' if mode == 'candidate_db' else 'daily_dictionary_review', 'key': key, 'applied_at': stamp,
                    'before': before, 'editorial_after': copy.deepcopy(after), 'workbook': str(book),
                    'candidate_before': candidate_before, 'candidate_after': copy.deepcopy(row)})
            for kind, prefix in [('main', 'vtuberpost_readings'), ('external', 'candidates_external')]:
                if kind in touched:
                    outputs[db / (prefix+'.json')] = editor.json_bytes(tables[kind])
                    outputs[db / (prefix+'.tsv')] = editor.tsv_bytes(tables[kind], list(tables[kind][0]))
            if overrides != editor.read_json(db / 'candidate_manual_overrides.json', {}):
                outputs[db / 'candidate_manual_overrides.json'] = editor.json_bytes(overrides)
            if 'main' in touched:
                report = editor.read_json(db / 'vtuberpost_readings_report.json', {})
                report.update(updated_at=stamp, output_rows=len(tables['main']),
                    tsv_json_consistent=True, last_app_batch=book.parent.name)
                for item in report.get('outputs', []):
                    target = db / Path(item.get('path', '')).name
                    if target in outputs:
                        item.update(rows=len(tables['main']), sha256=editor.digest(outputs[target]))
                outputs[db / 'vtuberpost_readings_report.json'] = editor.json_bytes(report)
                checkpoint = editor.read_json(db / 'vtuberpost_readings_checkpoint.json', {})
                checkpoint.update(updated_at=stamp, records=len(tables['main']), last_app_batch=book.parent.name)
                outputs[db / 'vtuberpost_readings_checkpoint.json'] = editor.json_bytes(checkpoint)
            if plan['actions']:
                state['updated_at'] = stamp
                outputs[db / 'candidate_editor_state.json'] = editor.json_bytes(state)
                outputs[db / 'candidate_editor_history.json'] = editor.json_bytes(history)
                index = editor.read_json(db / 'candidate_db_index.json', {'version': 1, 'tables': {}})
                index.setdefault('tables', {}).update(editorial_state={'json': 'candidate_editor_state.json', 'key': 'namespaced stable ID', 'rows': len(state['records'])},
                    editorial_history={'json': 'candidate_editor_history.json', 'rows': len(history)})
                if overrides:
                    index['tables']['human_review_overrides'] = {'json': 'candidate_manual_overrides.json',
                        'key': 'youtube_channel_id', 'rows': len(overrides)}
                index.update(updated_at=stamp, editorial_updated_at=stamp)
                outputs[db / 'candidate_db_index.json'] = editor.json_bytes(index)
            result = {'applied_at': stamp, 'added_words': len(plan['additions']),
                'saved_decisions': len(plan['actions']), 'published': False,
                'message': 'ローカル反映完了。Web公開はコミット・pushが必要です。'}
            if mode == 'candidate_db':
                result.update(target=mode, saved_conversions=len(plan['conversions']),
                    message='候補DBに保存しました。辞書への追加は別の作業です。')
            outputs[book.parent / '完了.json'] = editor.json_bytes(result)
            backup = book.parent / ('backups/' + uuid.uuid4().hex)
            backup.mkdir(parents=True)
            snapshots = {}
            entries = []
            for i, (target, data) in enumerate(outputs.items()):
                old = target.read_bytes() if target.exists() else None
                snapshots[target] = old
                if old is not None:
                    (backup / f'{i}.before').write_bytes(old)
                (backup / f'{i}.after').write_bytes(data)
                entries.append({'target': str(target), 'before': f'{i}.before' if old is not None else None,
                    'after': f'{i}.after', 'before_hash': editor.digest(old) if old is not None else None,
                    'after_hash': editor.digest(data)})
            manifest = {'status': 'prepared', 'files': entries}
            (backup / 'manifest.json').write_bytes(editor.json_bytes(manifest))
            if calculate_plan(db, root, book, target=mode) != plan:
                raise ValueError('生成中に入力が変更されました。内容確認をやり直してください。')
            written = []
            try:
                for target, data in outputs.items():
                    old = snapshots[target]
                    if editor.file_hash(target) != (editor.digest(old) if old is not None else None):
                        raise ValueError('反映中にファイルが変更されました: ' + str(target))
                    replace_bytes(target, data)
                    written.append(target)
                manifest['status'] = 'applied'
            except BaseException:
                for target in reversed(written):
                    old = snapshots[target]
                    if old is None:
                        target.unlink(missing_ok=True)
                    else:
                        replace_bytes(target, old)
                manifest['status'] = 'rolled_back'
                raise
            finally:
                (backup / 'manifest.json').write_bytes(editor.json_bytes(manifest))
            result['backup'] = str(backup)
            return result
    finally:
        lock.unlink(missing_ok=True)


def protect(book):
    # Artifact Tool authors the workbook; this OOXML pass supplies native protection
    # and clickable source URLs, which lack a documented public authoring setter.
    ns = editor.NS['s']
    tag = lambda n: '{' + ns + '}' + n
    with zipfile.ZipFile(book) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    styles = ET.fromstring(parts['xl/styles.xml'])
    xfs = styles.find('s:cellXfs', editor.NS)
    unlocked = {}
    for i, xf in enumerate(list(xfs)):
        new = copy.deepcopy(xf)
        for p in list(new.findall('s:protection', editor.NS)):
            new.remove(p)
        ET.SubElement(new, tag('protection'), locked='0')
        new.set('applyProtection', '1')
        unlocked[i] = len(xfs)
        xfs.append(new)
    xfs.set('count', str(len(xfs)))
    parts['xl/styles.xml'] = ET.tostring(styles, encoding='utf-8', xml_declaration=True)
    wb = ET.fromstring(parts['xl/workbook.xml'])
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(parts['xl/_rels/workbook.xml.rels'])}
    values = editor.read_xlsx(book, canonical=False)[SHEET]
    for sheet in wb.findall('s:sheets/s:sheet', editor.NS):
        if sheet.attrib['name'] == '_日次情報':
            sheet.set('state', 'veryHidden')
        target = rels[sheet.attrib['{' + editor.REL + '}id']]
        member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        xml = ET.fromstring(parts[member])
        data = xml.find('s:sheetData', editor.NS)
        if sheet.attrib['name'] == SHEET:
            for row in data:
                if int(row.attrib['r']) < 6:
                    continue
                for c in row:
                    if editor.col_index(c.attrib['r']) in (2, 5):
                        c.set('s', str(unlocked[int(c.attrib.get('s', '0'))]))
            # Keep the stable identifier off the daily working view.
            cols = xml.find('s:cols', editor.NS)
            if cols is not None:
                for col in cols:
                    if int(col.attrib['min']) == 8 and int(col.attrib['max']) == 8:
                        col.set('hidden', '1')
            relation_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
            relation_part = str(Path(member).parent / '_rels' / (Path(member).name + '.rels')).replace('\\', '/')
            relations = ET.fromstring(parts[relation_part]) if relation_part in parts else ET.Element('{' + relation_ns + '}Relationships')
            hyperlinks = ET.Element(tag('hyperlinks'))
            for number, row in values.items():
                if number < 6:
                    continue
                for col, label in ((4, 'E'), (6, 'G')):
                    url = row.get(col, '')
                    if isinstance(url, str) and editor.valid_url(url):
                        rid = 'dailyLink' + label + str(number)
                        ET.SubElement(relations, '{' + relation_ns + '}Relationship', Id=rid,
                            Type=editor.REL + '/hyperlink', Target=url, TargetMode='External')
                        ET.SubElement(hyperlinks, tag('hyperlink'), {'ref': label + str(number), '{' + editor.REL + '}id': rid})
            if len(hyperlinks):
                # hyperlinks follow dataValidations, before print/page properties.
                successors = ('printOptions', 'pageMargins', 'pageSetup', 'headerFooter', 'drawing', 'tableParts', 'extLst')
                idx = next((i for i, node in enumerate(xml) if node.tag in {tag(s) for s in successors}), len(xml))
                xml.insert(idx, hyperlinks)
                parts[relation_part] = ET.tostring(relations, encoding='utf-8', xml_declaration=True)
        protection = ET.Element(tag('sheetProtection'), sheet='1', objects='1', scenarios='1', autoFilter='0', sort='0')
        xml.insert(list(xml).index(data) + 1, protection)
        for validation in xml.findall('s:dataValidations/s:dataValidation', editor.NS):
            validation.set('showErrorMessage', '1')
            validation.set('errorStyle', 'stop')
            validation.set('showDropDown', '0')
        parts[member] = ET.tostring(xml, encoding='utf-8', xml_declaration=True)
    parts['xl/workbook.xml'] = ET.tostring(wb, encoding='utf-8', xml_declaration=True)
    temp = book.with_suffix('.protected.tmp')
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, value in parts.items():
            z.writestr(name, value)
    os.replace(temp, book)


def cli():
    parser = argparse.ArgumentParser(description='1日10語の候補確認と辞書反映')
    parser.add_argument('--db', type=Path, default=editor.DEFAULT_DB)
    parser.add_argument('--root', type=Path, default=editor.ROOT)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--count', type=int, default=10)
    p = sub.add_parser('activate')
    p.add_argument('batch', type=Path)
    for name in ('preview', 'protect'):
        p = sub.add_parser(name)
        p.add_argument('workbook', type=Path)
    p = sub.add_parser('apply')
    p.add_argument('plan', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = {'batch': str(prepare(args.db, args.root, args.out, args.count))}
    elif args.command == 'activate':
        activate(args.batch, args.out)
        result = {'workbook': str(args.batch / BOOK_NAME)}
    elif args.command == 'protect':
        protect(args.workbook)
        result = {'workbook': str(args.workbook)}
    elif args.command == 'preview':
        result = preview(args.db, args.root, args.workbook)
        print(json.dumps({'added_words': len(result['additions']), 'actions': len(result['actions']), 'errors': result['errors']}, ensure_ascii=False))
        return 0 if result['applicable'] else 1
    else:
        result = apply(args.db, args.root, args.plan)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(cli())
    except (ValueError, OSError, KeyError) as error:
        print('操作を停止しました: ' + str(error), file=sys.stderr)
        sys.exit(1)
