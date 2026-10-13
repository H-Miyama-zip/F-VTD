"""End-to-end tests against disposable DB copies; never mutate production DB."""
import copy
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('candidate_editor', HERE / 'editor.py')
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)
BOOK = editor.DEFAULT_OUT / editor.BOOK_NAME


def edit_book(source, target, edits, delete_row=None):
    """Modify values in the real artifact-authored file using OOXML only."""
    physical = editor.read_xlsx(source, canonical=False)
    mapped = {}
    for (name, ref), value in edits.items():
        if name in editor.SHEETS.values():
            column = editor.column_map(physical[name])[editor.FIELDS[editor.col_index(ref)]]
            ref = chr(65 + column) + ''.join(c for c in ref if c.isdigit())
        mapped[(name, ref)] = value
    edits = mapped
    with zipfile.ZipFile(source) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    wb = ET.fromstring(parts['xl/workbook.xml'])
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(parts['xl/_rels/workbook.xml.rels'])}
    for sheet in wb.findall('s:sheets/s:sheet', editor.NS):
        target_part = rels[sheet.attrib['{' + editor.REL + '}id']]
        member = target_part.lstrip('/') if target_part.startswith('/') else 'xl/' + target_part
        changes = {ref: val for (name, ref), val in edits.items() if name == sheet.attrib['name']}
        if not changes and not (delete_row and delete_row[0] == sheet.attrib['name']):
            continue
        xml = ET.fromstring(parts[member])
        for row in xml.findall('s:sheetData/s:row', editor.NS):
            if delete_row and sheet.attrib['name'] == delete_row[0] and int(row.attrib['r']) == delete_row[1]:
                xml.find('s:sheetData', editor.NS).remove(row)
                continue
            existing = {c.attrib['r']: c for c in row.findall('s:c', editor.NS)}
            for ref, value in changes.items():
                if int(''.join(c for c in ref if c.isdigit())) != int(row.attrib['r']):
                    continue
                cell = existing.get(ref)
                if cell is None:
                    cell = ET.SubElement(row, '{' + editor.NS['s'] + '}c', r=ref)
                cell.clear()
                cell.set('r', ref)
                if isinstance(value, dict):
                    ET.SubElement(cell, '{' + editor.NS['s'] + '}f').text = value['formula']
                    ET.SubElement(cell, '{' + editor.NS['s'] + '}v').text = '0'
                else:
                    cell.set('t', 'inlineStr')
                    inline = ET.SubElement(cell, '{' + editor.NS['s'] + '}is')
                    ET.SubElement(inline, '{' + editor.NS['s'] + '}t').text = value
        parts[member] = ET.tostring(xml, encoding='utf-8', xml_declaration=True)
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, b in parts.items():
            z.writestr(n, b)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sheets = editor.read_xlsx(BOOK)
        cls.baseline = editor.baseline_from(cls.sheets)
        cls.production_before = {n: editor.file_hash(editor.DEFAULT_DB / n) for n in editor.FILES}

    @classmethod
    def tearDownClass(cls):
        assert cls.production_before == {n: editor.file_hash(editor.DEFAULT_DB / n) for n in editor.FILES}

    def setUp(self):
        self.scratch = editor.DEFAULT_OUT / 'test_scratch'
        self.scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='candidate_editor_test_',dir=self.scratch)
        self.root = Path(self.temp.name)
        self.db = self.root / 'db'
        self.out = self.root / 'out'
        self.db.mkdir()
        for name in editor.FILES:
            source = editor.DEFAULT_DB / name
            if source.exists():
                shutil.copyfile(source, self.db / name)
        self.book = self.root / 'edited.xlsx'

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.scratch.resolve()))
        self.temp.cleanup()

    def plan(self, edits, **kwargs):
        edit_book(BOOK, self.book, edits, **kwargs)
        return editor.preview(self.db, self.book, self.out)

    def rewrite_main(self, mutate):
        tables, _, _, _ = editor.load_db(self.db)
        mutate(tables['main'])
        rows = tables['main']
        (self.db / 'vtuberpost_readings.json').write_bytes(editor.json_bytes(rows))
        (self.db / 'vtuberpost_readings.tsv').write_bytes(editor.tsv_bytes(rows, list(rows[0])))

    def test_unedited_roundtrip_all_rows_and_no_db_writes(self):
        plan = editor.preview(self.db, BOOK, self.out)
        self.assertEqual(plan['errors'], [])
        self.assertEqual(plan['changes'], [])
        self.assertEqual(len(self.baseline['rows']), 19999)
        editor.apply_plan(self.db, self.out / '反映計画.json')
        self.assertFalse((self.db / 'candidate_editor_state.json').exists())

    def test_edit_existing_verified_evidence_and_adoption(self):
        # Locate a verified main row by ID, never by row index assumptions.
        rn = next(n for n, c in self.sheets['主一覧'].items() if n >= 5 and c.get(3) == '確認済み')
        key = self.sheets['主一覧'][rn][9]
        plan = self.plan({('主一覧', f'B{rn}'): 'てすとよみ', ('主一覧', f'F{rn}'): 'https://example.com/profile',
                          ('主一覧', f'G{rn}'): '本人プロフィールに氏名とかなを明記。', ('主一覧', f'E{rn}'): '採用',
                          ('主一覧', f'H{rn}'): '読みを訂正。', ('主一覧', f'I{rn}'): '一次根拠を確認。'})
        self.assertEqual(plan['errors'], [])
        result = editor.apply_plan(self.db, self.out / '反映計画.json')
        self.assertEqual(result['changes'], 1)
        tables, state, _, _ = editor.load_db(self.db)
        row = next(r for r in tables['main'] if editor.make_key('main', r) == key)
        self.assertEqual(row['reading'], 'てすとよみ')
        self.assertEqual(state['records'][key]['dictionary_decision'], '採用')
        self.assertTrue((Path(result['backup']) / 'vtuberpost_readings.json').exists())
        self.assertEqual(editor.read_json(Path(result['backup']) / 'manifest.json')['status'], 'applied')
        # Repreview is idempotent, including all bookkeeping fields.
        self.assertEqual(editor.preview(self.db, self.book, self.out)['changes'], [])

    def test_meta_only_does_not_rewrite_evidence_tables(self):
        before = editor.file_hash(self.db / 'vtuberpost_readings.json')
        plan = self.plan({('主一覧', 'H5'): '後日本人プロフィールを確認。', ('主一覧', 'E5'): '保留'})
        self.assertEqual(plan['errors'], [])
        editor.apply_plan(self.db, self.out / '反映計画.json')
        self.assertEqual(editor.file_hash(self.db / 'vtuberpost_readings.json'), before)

    def test_xml_illegal_source_characters_do_not_become_edits(self):
        rownum=next(n for n,c in self.sheets['主一覧'].items() if n>=5 and '制御文字 U+' in c.get(0,''))
        plan=self.plan({('主一覧',f'H{rownum}'):'元の名前を保持してメモだけ追加。'})
        self.assertEqual(plan['errors'],[])
        self.assertEqual(set(plan['changes'][0]['updates']),{'editor_note'})

    def test_external_verified_requires_identity_and_consistent_status(self):
        plan=self.plan({('別管理の候補','B5'):'あさぎ',('別管理の候補','D5'):'確認済み'})
        self.assertTrue(any('合わせて変更' in e for e in plan['errors']))
        plan=self.plan({('別管理の候補','B5'):'あさぎ',('別管理の候補','D5'):'確認済み',
                        ('別管理の候補','P5'):'確認済みの別管理候補',
                        ('別管理の候補','F5'):'https://example.com/profile',
                        ('別管理の候補','G5'):'全名かなが本人プロフィールにある。',
                        ('別管理の候補','N5'):'本人の固定プロフィールに名前と同じ本人キーを確認。',
                        ('別管理の候補','E5'):'採用'})
        self.assertEqual(plan['errors'],[])
        editor.apply_plan(self.db,self.out/'反映計画.json')
        self.assertEqual(editor.preview(self.db,self.book,self.out)['changes'],[])

    def test_new_main_and_external_and_idempotency(self):
        cid = 'UC' + 'z' * 22
        plan = self.plan({('新規追加','A5'):'テスト新人',('新規追加','B5'):'てすとしんじん',
                          ('新規追加','D5'):'確認済み',('新規追加','E5'):'採用',
                          ('新規追加','F5'):'https://example.com/talent',('新規追加','G5'):'本人の全名かなを確認。',
                          ('新規追加','M5'):cid,('新規追加','A6'):'別管理テスト',('新規追加','C6'):'べつかんりてすと'})
        self.assertEqual(plan['errors'], [])
        self.assertEqual(len(plan['changes']), 2)
        editor.apply_plan(self.db, self.out / '反映計画.json')
        tables, _, _, _ = editor.load_db(self.db)
        self.assertEqual(len(tables['main']), 18290)
        self.assertEqual(len(tables['external']), 1711)
        self.assertEqual(editor.preview(self.db, self.book, self.out)['changes'], [])

    def test_duplicate_new_id_rejected(self):
        cid = self.sheets['主一覧'][5][12]
        plan = self.plan({('新規追加','A5'):'重複',('新規追加','M5'):cid})
        self.assertTrue(any('同じ固定ID' in e for e in plan['errors']))

    def test_invalid_verified_adoption_and_formula_rejected(self):
        plan = self.plan({('主一覧','D5'):'確認済み',('主一覧','E5'):'採用'})
        self.assertTrue(plan['errors'])
        plan = self.plan({('主一覧','H5'):{'formula':'HYPERLINK("https://example.com","test")'}})
        self.assertTrue(any('数式' in e for e in plan['errors']))

    def test_deleted_or_modified_ids_rejected(self):
        plan = self.plan({('主一覧','J5'):'wrong-id'})
        self.assertTrue(plan['errors'])
        plan = self.plan({}, delete_row=('主一覧',5))
        self.assertTrue(any('なくなって' in e for e in plan['errors']))

    def test_same_field_conflict_and_disjoint_update_preserved(self):
        key = self.sheets['主一覧'][5][9]
        def mutate(rows):
            row = next(r for r in rows if editor.make_key('main', r) == key)
            row['evidence_summary'] = '別の調査による新しい根拠。'
            row['notes'] += ' 別処理の注記。'
        self.rewrite_main(mutate)
        plan = self.plan({('主一覧','G5'):'Excelで変更した根拠。'})
        self.assertTrue(any('両方で変更' in e for e in plan['errors']))
        plan = self.plan({('主一覧','H5'):'Excelメモだけを追加。'})
        self.assertEqual(plan['errors'], [])
        editor.apply_plan(self.db, self.out / '反映計画.json')
        tables, _, _, _ = editor.load_db(self.db)
        row = next(r for r in tables['main'] if editor.make_key('main', r) == key)
        self.assertEqual(row['evidence_summary'], '別の調査による新しい根拠。')
        self.assertIn('別処理の注記。', row['notes'])

    def test_stale_plan_and_manually_edited_plan_rejected(self):
        self.plan({('主一覧','H5'):'メモ'})
        plan_path = self.out / '反映計画.json'
        modified = editor.read_json(plan_path)
        modified['changes'][0]['updates']['editor_note']['after'] = '勝手に書き換えた値'
        plan_path.write_bytes(editor.json_bytes(modified))
        with self.assertRaises(ValueError):
            editor.apply_plan(self.db, plan_path)
        self.plan({('主一覧','H5'):'メモ'})
        self.rewrite_main(lambda rows: rows[0].update(notes=rows[0]['notes'] + ' concurrent'))
        with self.assertRaises(ValueError):
            editor.apply_plan(self.db, plan_path)

    def test_atomic_rollback_on_write_failure(self):
        self.plan({('主一覧','G5'):'新しい未確認理由。'})
        before = {n: editor.file_hash(self.db / n) for n in editor.FILES}
        real_replace = os.replace
        fired = False
        def fail_once(src, dest):
            nonlocal fired
            if Path(dest).name == 'candidate_editor_state.json' and not fired:
                fired = True
                raise OSError('Injected disk failure')
            return real_replace(src, dest)
        with patch.object(editor.os, 'replace', side_effect=fail_once):
            with self.assertRaises(OSError):
                editor.apply_plan(self.db, self.out / '反映計画.json')
        self.assertEqual(before, {n: editor.file_hash(self.db / n) for n in editor.FILES})
        manifest = next((self.db / 'editor_backups').glob('*/manifest.json'))
        self.assertEqual(editor.read_json(manifest)['status'], 'rolled_back')

    def test_native_xlsx_controls_and_baseline(self):
        with zipfile.ZipFile(BOOK) as z:
            wb = ET.fromstring(z.read('xl/workbook.xml'))
            meta = next(s for s in wb.findall('s:sheets/s:sheet',editor.NS) if s.attrib['name'] == '_編集情報')
            self.assertEqual(meta.attrib['state'], 'veryHidden')
            sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            self.assertIsNotNone(sheet.find('s:sheetProtection',editor.NS))
            self.assertEqual(len(sheet.findall('s:dataValidations/s:dataValidation',editor.NS)), 2)
            self.assertIsNotNone(sheet.find('s:sheetViews/s:sheetView/s:pane',editor.NS))
            xfs = ET.fromstring(z.read('xl/styles.xml')).find('s:cellXfs',editor.NS)
            cells = {c.attrib['r']:c for c in sheet.findall('s:sheetData/s:row/s:c',editor.NS)}
            self.assertEqual(xfs[int(cells['D5'].attrib['s'])].find('s:protection',editor.NS).attrib['locked'], '0')
            protection = xfs[int(cells['Q5'].attrib.get('s','0'))].find('s:protection',editor.NS)
            self.assertTrue(protection is None or protection.attrib.get('locked') != '0')

    def test_youtube_links_are_live_and_input_columns_unchanged(self):
        with zipfile.ZipFile(BOOK) as z:
            for index,name in [(1,'主一覧'),(2,'別管理の候補'),(3,'新規追加')]:
                sheet=ET.fromstring(z.read(f'xl/worksheets/sheet{index}.xml'))
                for row in sheet.findall('s:sheetData/s:row',editor.NS):
                    rn=int(row.attrib['r'])
                    if rn<5:
                        continue
                    cells={editor.col_index(c.attrib['r']):c for c in row.findall('s:c',editor.NS)}
                    link=cells.get(1)
                    self.assertIsNotNone(link)
                    formula=link.findtext('s:f',namespaces=editor.NS)
                    self.assertIn('HYPERLINK("https://www.youtube.com/channel/"',formula)
                    self.assertIn(f'LEN(L{rn})=24',formula)
                    self.assertIn(f'&L{rn},',formula)
                    self.assertNotIn('not implemented',link.findtext('s:v',default='',namespaces=editor.NS))
                    cid=self.sheets[name][rn].get(12,'')
                    self.assertEqual(link.findtext('s:v',default='',namespaces=editor.NS),
                                     'チャンネルを開く' if len(cid)==24 and cid.startswith('UC') else '')
            wb=ET.fromstring(z.read('xl/workbook.xml'))
            self.assertEqual(wb.find('s:calcPr',editor.NS).attrib.get('fullCalcOnLoad'),'1')

    def test_work_layout_dropdown_and_rejection_roundtrip(self):
        raw=editor.read_xlsx(BOOK,canonical=False)
        for name in editor.SHEETS.values():
            self.assertEqual([raw[name][4][i] for i in range(6)],
                             ['名前','YouTubeチャンネル','収録判断','読み','確認状態','読み候補'])
        with zipfile.ZipFile(BOOK) as z:
            for n in range(1,4):
                sheet=ET.fromstring(z.read(f'xl/worksheets/sheet{n}.xml'))
                pane=sheet.find('s:sheetViews/s:sheetView/s:pane',editor.NS)
                self.assertEqual(pane.attrib['xSplit'],'3')
                self.assertEqual(pane.attrib['ySplit'],'4')
                validation=next(v for v in sheet.findall('s:dataValidations/s:dataValidation',editor.NS)
                                if v.attrib['sqref'].startswith('C5:'))
                self.assertEqual(validation.findtext('s:formula1',namespaces=editor.NS),
                                 '"未判断,採用,保留,不採用"')
                self.assertEqual(validation.attrib.get('showDropDown'),'0')
        key=self.sheets['主一覧'][5][9]
        plan=self.plan({('主一覧','E5'):'不採用',('主一覧','I5'):'判断理由の試験'})
        self.assertEqual(plan['errors'],[])
        self.assertEqual(plan['changes'][0]['updates']['dictionary_decision']['after'],'対象外')
        editor.apply_plan(self.db,self.out/'反映計画.json')
        state=editor.load_db(self.db)[1]
        self.assertEqual(state['records'][key]['dictionary_decision'],'対象外')
        self.assertEqual(editor.preview(self.db,self.book,self.out)['changes'],[])

    def test_layout_input_preserves_pending_edits_and_baseline(self):
        self.plan({('主一覧','H5'):'配置変更前の未反映メモ',('主一覧','E5'):'不採用'})
        path=editor.layout_input(self.book,self.out)
        data=editor.read_json(path)
        self.assertEqual(data['rows']['main'][0][7],'配置変更前の未反映メモ')
        self.assertEqual(data['rows']['main'][0][4],'対象外')
        self.assertEqual(data['metadata'][0][1],self.baseline['export_id'])
        self.assertEqual(len(data['rows']['main']),18289)
        self.assertEqual(len(data['logs']),19999)

    def test_import_guard_honors_new_human_evidence(self):
        guard_spec = importlib.util.spec_from_file_location('import_guard',editor.DEFAULT_DB / 'candidate_import_guard.py')
        guard = importlib.util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(guard)
        cid='UC'+'z'*22
        (self.db / 'candidate_manual_overrides.json').write_bytes(editor.json_bytes({cid:{
            'reading_status':'verified','reading':'てすと','source_url':'https://example.com',
            'record_id':'main:'+cid}}))
        approved=guard.approved_imports(self.db)
        self.assertEqual(approved[cid]['reading'],'てすと')
        self.assertTrue(guard.preserve_import({'youtube_channel_id':cid,'reading_status':'verified','reading':'てすと'},
                                             {'reading_status':'unknown'},approved))
        with self.assertRaises(RuntimeError):
            guard.preserve_import({'youtube_channel_id':cid,'reading_status':'verified','reading':'てすと'},
                                   {'reading_status':'verified','reading':'べつよみ'},approved)


if __name__ == '__main__':
    unittest.main(verbosity=2)
