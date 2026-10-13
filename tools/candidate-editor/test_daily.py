"""Daily workflow tests use disposable dictionaries and DBs only."""
import copy
import os
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import daily
import editor
from test_editor import edit_book


class DailyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        latest = editor.read_json(daily.DEFAULT_OUT / 'latest.json')
        cls.source_batch = Path(latest['batch'])
        if not (cls.source_batch / daily.BOOK_NAME).exists():
            books = sorted((daily.DEFAULT_OUT / 'batches').glob('*/' + daily.BOOK_NAME), key=lambda p: p.stat().st_mtime, reverse=True)
            if not books:
                raise ValueError('テスト用の元候補Excelがありません。')
            cls.source_batch = books[0].parent
        cls.source_info = editor.read_json(cls.source_batch / '候補情報.json')
        cls.tables, cls.state, _, _ = editor.load_db(editor.DEFAULT_DB)

    def setUp(self):
        test_root = daily.DEFAULT_OUT / 'test-runs'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='fvtd-test-daily-', dir=test_root)
        Path(self.temp.name).resolve().relative_to(test_root.resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'db'
        self.db.mkdir()
        self.out = self.root / 'daily'
        self.batch = self.out / 'batches/test'
        self.batch.mkdir(parents=True)
        self.book = self.batch / daily.BOOK_NAME
        shutil.copyfile(self.source_batch / daily.BOOK_NAME, self.book)
        self.info = copy.deepcopy(self.source_info)
        self.info.update(root=str(self.root.resolve()), db=str(self.db.resolve()))
        (self.batch / '候補情報.json').write_bytes(editor.json_bytes(self.info))
        keys = {r['key'] for r in self.info['records']}
        for kind, prefix in [('main', 'vtuberpost_readings'), ('external', 'candidates_external')]:
            captured = {r['key']: r for r in self.info['records']}
            rows = [copy.deepcopy(r) for r in self.tables[kind] if editor.make_key(kind, r) in keys]
            # Replay the captured batch, even after names/readings were edited in the live DB.
            for row in rows:
                record = captured[editor.make_key(kind, row)]
                row.update({field: record[field] for field in ('name', 'reading', 'source_url', 'evidence_summary')})
            (self.db / (prefix + '.tsv')).write_bytes(editor.tsv_bytes(rows, list(self.tables[kind][0])))
            (self.db / (prefix + '.json')).write_bytes(editor.json_bytes(rows))
        state = copy.deepcopy(self.state)
        state['records'] = {k: v for k, v in state['records'].items() if k in keys}
        # Use the captured batch decisions, independently of later live DB work.
        for r in self.info['records']:
            state['records'].setdefault(r['key'], {}).update(dictionary_decision=r['dictionary_decision'], decision_reason=r['decision_reason'])
        (self.db / 'candidate_editor_state.json').write_bytes(editor.json_bytes(state))
        (self.root / 'data').mkdir()
        baseline = dict(zip(daily.MASTER_COLUMNS, ['きぞんてすと', '既存テスト', '人名', '人名', '人名', 'upstream', '', '', '']))
        (self.root / 'data/master.tsv').write_bytes(editor.tsv_bytes([baseline], daily.MASTER_COLUMNS))
        for name in ('scripts', 'site', 'package'):
            shutil.copytree(editor.ROOT / name, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(editor.ROOT / 'NOTICE.md', self.root / 'NOTICE.md')
        # Fixtures are inside the live checkout; never inherit its Git seals.
        ceiling = patch.dict(os.environ, {'GIT_CEILING_DIRECTORIES': str(self.root.parent.resolve())})
        ceiling.start()
        self.addCleanup(ceiling.stop)
        ledger = daily.ledger_module(self.root)
        baseline_dir = self.root / 'data/baseline'
        baseline_dir.mkdir()
        content = (self.root / 'data/master.tsv').read_bytes()
        (baseline_dir / 'master.tsv').write_bytes(content)
        upstream = {'fixture': {'version': 'fixture', 'files': {'fixture.tsv': 'f' * 64}}}
        info = dict(count=1, sha256=ledger.digest(content), upstream='fixture', upstream_date='2026-09-03',
                    upstream_sha256=ledger.digest(ledger.canonical(upstream['fixture'])),
                    reconstructed_updates=0, reconstructed_head=None)
        (baseline_dir / 'info.json').write_bytes(ledger.canonical(info))
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical(upstream))
        (self.root / 'data/ledger.jsonl').write_bytes(b'')
        (self.root / 'data/publications.jsonl').write_bytes(b'')

    def edit(self, edits):
        target = self.book.with_name('edited.xlsx')
        edit_book(self.book, target, {(daily.SHEET, k): v for k, v in edits.items()})
        target.replace(self.book)

    def preview(self):
        return daily.preview(self.db, self.root, self.book)

    def test_adopt_hold_reject_and_regenerate(self):
        self.edit({'C6': '採用', 'C7': '保留', 'C8': '不採用', 'F7': 'あとで確認'})
        plan = self.preview()
        self.assertEqual(len(plan['additions']), 1)
        before = (self.root / 'data/master.tsv').read_bytes()
        result = daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.assertEqual(result['added_words'], 1)
        self.assertFalse(result['published'])
        self.assertEqual(len(daily.master_rows(self.root)), 2)
        state = editor.read_json(self.db / 'candidate_editor_state.json')
        keys = [r['key'] for r in self.info['records']]
        self.assertEqual(state['records'][keys[0]]['dictionary_decision'], '採用')
        self.assertEqual(state['records'][keys[1]]['dictionary_decision'], '保留')
        self.assertEqual(state['records'][keys[2]]['dictionary_decision'], '対象外')
        self.assertEqual(state['records'][keys[1]]['decision_reason'], 'あとで確認')
        master_after = daily.master_rows(self.root)
        for file in ('Google日本語入力', 'ATOK'):
            rows = (self.root / f'dist/VTuber変換辞書_{file}.tsv').read_text(encoding='utf-16').splitlines()
            self.assertEqual(len(rows), 2)
            self.assertIn(plan['additions'][0]['reading'] + '\t' + plan['additions'][0]['word'] + '\t人名', rows)
        manifest = editor.read_json(self.root / 'public/data/latest.json')
        self.assertEqual(manifest['count'], 2)
        replayed, updates, _, _ = daily.ledger_module(self.root).load(self.root)
        self.assertEqual(replayed, master_after)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]['changes'][0]['after'], plan['additions'][0])
        self.assertFalse((self.root / 'data/additions.tsv').exists())
        self.assertEqual((self.root / 'data/publications.jsonl').read_bytes(), b'')
        backup = Path(result['backup'])
        backup_info = editor.read_json(backup / 'manifest.json')
        master_backup = next(r for r in backup_info['files'] if r['target'] == str(self.root / 'data/master.tsv'))
        self.assertEqual((backup / master_backup['before']).read_bytes(), before)
        self.assertEqual(backup_info['status'], 'applied')
        remaining, _, _ = daily.candidates(self.db, self.root)
        self.assertTrue(all(r['key'] not in keys[:3] for r in remaining))
        with self.assertRaisesRegex(ValueError, '反映済み'):
            daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')

    def test_reference_formula_and_id_tampering_rejected(self):
        for ref, value in [('A6', '別人'), ('B6', 'べつよみ'), ('E6', 'https://bad.example'), ('H6', 'unknown'), ('C6', {'formula': '1+1'})]:
            with self.subTest(ref=ref):
                shutil.copyfile(self.source_batch / daily.BOOK_NAME, self.book)
                self.edit({ref: value})
                with self.assertRaises(ValueError):
                    self.preview()

    def test_changed_workbook_and_master_block_apply(self):
        self.edit({'C6': '採用'})
        self.preview()
        self.edit({'F6': '確認後の変更'})
        with self.assertRaisesRegex(ValueError, '変更'):
            daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.preview()
        master = self.root / 'data/master.tsv'
        master.write_bytes(master.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, '変更'):
            daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')

    def test_hand_edited_plan_cannot_add_unreviewed_words(self):
        self.edit({'C6': '採用'})
        plan = self.preview()
        plan['additions'][0]['reading'] = 'べつよみ'
        (self.batch / '辞書反映計画.json').write_bytes(editor.json_bytes(plan))
        with self.assertRaisesRegex(ValueError, '変更'):
            daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')

    def test_changed_ledger_blocks_apply(self):
        self.edit({'C6': '採用'})
        self.preview()
        master_before = (self.root / 'data/master.tsv').read_bytes()
        ledger = self.root / 'data/ledger.jsonl'
        ledger.write_bytes(ledger.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, '変更'):
            daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), master_before)

    def test_unrecorded_dictionary_change_is_not_absorbed_into_daily_update(self):
        master = self.root / 'data/master.tsv'
        rows = daily.master_rows(self.root)
        rows[0]['word'] = '台帳に未記録の編集'
        master.write_bytes(editor.tsv_bytes(rows, daily.MASTER_COLUMNS))
        self.edit({'C6': '採用'})
        self.preview()
        before = master.read_bytes()
        with self.assertRaisesRegex(ValueError, 'master'):
            daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.assertEqual(master.read_bytes(), before)
        self.assertEqual((self.root / 'data/ledger.jsonl').read_bytes(), b'')

    def test_changed_candidate_blocks_preview(self):
        self.edit({'C6': '採用'})
        record = self.info['records'][0]
        prefix = 'vtuberpost_readings' if record['kind'] == 'main' else 'candidates_external'
        rows = editor.read_json(self.db / (prefix + '.json'))
        row = next(r for r in rows if editor.make_key(record['kind'], r) == record['key'])
        row['reading_status'] = 'unknown'
        (self.db / (prefix + '.json')).write_bytes(editor.json_bytes(rows))
        (self.db / (prefix + '.tsv')).write_bytes(editor.tsv_bytes(rows, list(rows[0])))
        plan = self.preview()
        self.assertFalse(plan['applicable'])
        self.assertEqual(plan['additions'], [])

    def test_same_name_and_candidate_duplicates_excluded(self):
        first = self.info['records'][0]
        rows = daily.master_rows(self.root)
        rows[0]['word'] = first['name']
        (self.root / 'data/master.tsv').write_bytes(editor.tsv_bytes(rows, daily.MASTER_COLUMNS))
        pool, excluded, _ = daily.candidates(self.db, self.root)
        self.assertTrue(all(r['key'] != first['key'] for r in pool))
        self.assertEqual(excluded['同名が収録済み（別途確認）'], 1)
        records = editor.read_json(self.db / 'vtuberpost_readings.json')
        other = copy.deepcopy(records[1])
        other['youtube_channel_id'] = 'UC' + 'z' * 22
        records.append(other)
        (self.db / 'vtuberpost_readings.json').write_bytes(editor.json_bytes(records))
        (self.db / 'vtuberpost_readings.tsv').write_bytes(editor.tsv_bytes(records, list(records[0])))
        pool, excluded, _ = daily.candidates(self.db, self.root)
        self.assertTrue(all(r['name'] != other['name'] for r in pool))
        self.assertEqual(excluded['候補内に同名あり（別途確認）'], 2)

    def test_failed_build_and_partial_apply_preserve_dictionary(self):
        self.edit({'C6': '採用'})
        self.preview()
        master = self.root / 'data/master.tsv'
        before = master.read_bytes()
        state_before = (self.db / 'candidate_editor_state.json').read_bytes()
        ledger_before = (self.root / 'data/ledger.jsonl').read_bytes()
        with patch.object(daily, 'build_staged', side_effect=ValueError('test build failure')):
            with self.assertRaisesRegex(ValueError, 'test build failure'):
                daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.assertEqual(master.read_bytes(), before)
        actual_replace = daily.replace_bytes
        calls = []
        def fail_once(path, data):
            calls.append(path)
            if len(calls) == 3:
                raise OSError('test write failure')
            actual_replace(path, data)
        with patch.object(daily, 'replace_bytes', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'test write failure'):
                daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.assertEqual(master.read_bytes(), before)
        self.assertEqual((self.db / 'candidate_editor_state.json').read_bytes(), state_before)
        self.assertEqual((self.root / 'data/ledger.jsonl').read_bytes(), ledger_before)
        self.assertFalse((self.batch / '完了.json').exists())
        self.assertFalse((self.db / '.candidate-editor.lock').exists())

    def test_pending_work_preserved_and_unjudged_completion(self):
        daily.activate(self.batch, self.out)
        with self.assertRaisesRegex(ValueError, '作業中'):
            daily.prepare(self.db, self.root, self.out)
        before = (self.root / 'data/master.tsv').read_bytes()
        self.preview()
        result = daily.apply(self.db, self.root, self.batch / '辞書反映計画.json')
        self.assertEqual(result['added_words'], 0)
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), before)
        batch = daily.prepare(self.db, self.root, self.out, 3)
        self.assertEqual(len(editor.read_json(batch / '候補情報.json')['records']), 3)

    def test_native_protection_and_source_links(self):
        with zipfile.ZipFile(self.book) as z:
            workbook = ET.fromstring(z.read('xl/workbook.xml'))
            hidden = next(r for r in workbook.findall('s:sheets/s:sheet', editor.NS) if r.attrib['name'] == '_日次情報')
            self.assertEqual(hidden.attrib['state'], 'veryHidden')
            sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            self.assertIsNotNone(sheet.find('s:sheetProtection', editor.NS))
            self.assertEqual(len(sheet.findall('s:hyperlinks/s:hyperlink', editor.NS)), 20)
            styles = ET.fromstring(z.read('xl/styles.xml')).find('s:cellXfs', editor.NS)
            for cell in sheet.findall('s:sheetData/s:row/s:c', editor.NS):
                if cell.attrib['r'] in ('C6', 'F6'):
                    self.assertEqual(styles[int(cell.attrib['s'])].find('s:protection', editor.NS).attrib['locked'], '0')

    def test_groups_and_missing_evidence_require_separate_review(self):
        rows = editor.read_json(self.db / 'vtuberpost_readings.json')
        rows[0]['evidence_summary'] = 'VTuberグループの公式アカウント。'
        rows[1]['source_url'] = ''
        (self.db / 'vtuberpost_readings.json').write_bytes(editor.json_bytes(rows))
        (self.db / 'vtuberpost_readings.tsv').write_bytes(editor.tsv_bytes(rows, list(rows[0])))
        pool, excluded, _ = daily.candidates(self.db, self.root)
        self.assertEqual(excluded['個人・団体の区分を確認'], 1)
        self.assertEqual(excluded['根拠が不足'], 1)
        self.assertTrue(all(r['name'] not in (rows[0]['name'], rows[1]['name']) for r in pool))


if __name__ == '__main__':
    unittest.main()
