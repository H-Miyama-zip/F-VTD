"""Exercise the local app on disposable copies of the daily fixtures."""
import json
import copy
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app_server
import daily
import editor
import test_daily


class AppTests(test_daily.DailyTests):
    def setUp(self):
        super().setUp()
        daily.activate(self.batch, self.out)
        self.workspace = app_server.Workspace(self.root, self.db, self.out)

    def payload(self, state):
        return {'batch_id': state['batch_id'], 'revision': state['revision']}

    def save_choice(self, decision='採用'):
        state = self.workspace.state()
        entries = [{'key': r['key'], 'decision': r['decision'], 'memo': r['memo']} for r in state['records']]
        entries[0].update(decision=decision, memo='手動で確認\nメモを保存')
        result = self.workspace.save({**self.payload(state), 'entries': entries})
        return {**self.payload(state), 'revision': result['revision']}

    def extend_pool(self, count=40):
        rows = editor.read_json(self.db / 'vtuberpost_readings.json')
        template = rows[0]
        for i in range(count):
            row = copy.deepcopy(template)
            row.update(name=f'補充テスト{i:02}', reading='ほじゅうてすと',
                       youtube_channel_id='UC'+str(i).zfill(22), source_url=f'https://example.com/test/{i}')
            rows.append(row)
        (self.db / 'vtuberpost_readings.json').write_bytes(editor.json_bytes(rows))
        (self.db / 'vtuberpost_readings.tsv').write_bytes(editor.tsv_bytes(rows, list(rows[0])))

    def test_continuous_queue_over_ten_and_restart_preserves_choices(self):
        self.extend_pool()
        state = self.workspace.state()
        original = (self.root / 'data/master.tsv').read_bytes()
        first_order = [r['key'] for r in state['records']]
        stale = self.payload(state)
        for i in range(25):
            pending = next(r for r in state['records'] if r['decision'] == '未判断')
            entries = [{'key': r['key'], 'decision': r['decision'], 'memo': r['memo']} for r in state['records']]
            next(e for e in entries if e['key'] == pending['key']).update(decision=['採用','保留','不採用'][i%3], memo=f'確認 {i}')
            self.workspace.save({**self.payload(state), 'entries': entries})
            state = self.workspace.state()
            self.assertEqual(sum(r['decision']=='未判断' for r in state['records']), 10)
            self.assertEqual(len(state['records']), 11+i)
            self.assertEqual(len({r['key'] for r in state['records']}), len(state['records']))
        restarted = app_server.Workspace(self.root, self.db, self.out).state()
        self.assertEqual(restarted['records'], state['records'])
        self.assertEqual([r['key'] for r in state['records'][:10]], first_order)
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), original)
        with self.assertRaises(app_server.Conflict):
            self.workspace.save({**stale, 'entries': entries})

    def test_random_pool_selection_is_saved_once(self):
        self.extend_pool()
        pool, _, _ = daily.candidates(self.db, self.root)
        with patch.object(app_server.secrets, 'SystemRandom') as rng:
            rng.return_value.sample.side_effect = lambda rows, count: list(reversed(rows))[:count]
            state = self.workspace.state()
        self.assertEqual([r['key'] for r in state['records']], [r['key'] for r in reversed(pool[-10:])])
        self.assertEqual(app_server.Workspace(self.root, self.db, self.out).state()['records'], state['records'])

    def test_registration_keeps_pending_candidate_memos(self):
        self.extend_pool()
        state = self.workspace.state()
        memo_key = state['records'][1]['key']
        entries = [{'key': r['key'], 'decision': r['decision'], 'memo': '次に確認' if r['key']==memo_key else r['memo']} for r in state['records']]
        entries[0]['decision'] = '採用'
        self.workspace.save({**self.payload(state), 'entries': entries})
        state = self.workspace.state()
        plan = self.workspace.preview(self.payload(state))
        result = self.workspace.apply({**self.payload(state), 'token': plan['token']})
        pending = [r for r in result['state']['records'] if r['decision']=='未判断']
        next_state = self.workspace.next({**self.payload(result['state']), 'count': 10})
        self.assertEqual(next_state['records'], pending)
        self.assertEqual(next(r for r in next_state['records'] if r['key']==memo_key)['memo'], '次に確認')
        self.assertEqual(result['result']['added_words'], 0)
        self.assertEqual(result['result']['saved_conversions'], 1)

    def test_exhausted_queue_finishes_without_repeating_candidates(self):
        state = self.workspace.state()
        entries = [{'key': r['key'], 'decision': '不採用', 'memo': ''} for r in state['records']]
        self.workspace.save({**self.payload(state), 'entries': entries})
        state = self.workspace.state()
        self.assertTrue(state['pool_exhausted'])
        plan = self.workspace.preview(self.payload(state))
        result = self.workspace.apply({**self.payload(state), 'token': plan['token']})
        next_state = self.workspace.next(self.payload(result['state']))
        self.assertEqual(next_state['batch_id'], result['state']['batch_id'])
        self.assertTrue(next_state['complete'])

    def test_app_save_restart_and_no_dictionary_write(self):
        before = (self.root / 'data/master.tsv').read_bytes()
        self.save_choice()
        restarted = app_server.Workspace(self.root, self.db, self.out)
        state = restarted.state()
        self.assertEqual(state['records'][0]['decision'], '採用')
        self.assertEqual(state['records'][0]['memo'], '手動で確認\nメモを保存')
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), before)

    def test_app_stale_version_and_invalid_entries(self):
        old = self.workspace.state()
        self.save_choice()
        entries = [{'key': r['key'], 'decision': r['decision'], 'memo': r['memo']} for r in old['records']]
        with self.assertRaises(app_server.Conflict):
            self.workspace.save({**self.payload(old), 'entries': entries})
        state = self.workspace.state()
        entries[0]['key'] = 'unknown-key'
        with self.assertRaises(ValueError):
            self.workspace.save({**self.payload(state), 'entries': entries})

    def test_app_review_then_apply_and_next_without_excel(self):
        payload = self.save_choice()
        with self.assertRaises(app_server.Conflict):
            self.workspace.apply({**payload, 'token': 'not-reviewed'})
        preview = self.workspace.preview(payload)
        self.assertEqual(len(preview['conversions']), 1)
        result = self.workspace.apply({**payload, 'token': preview['token']})
        self.assertEqual(result['result']['added_words'], 0)
        self.assertEqual(result['result']['saved_conversions'], 1)
        self.assertEqual(result['state']['dictionary_count'], 1)
        next_state = self.workspace.next({**self.payload(result['state']), 'count': 3})
        self.assertEqual(len(next_state['records']), 9)
        self.assertFalse((self.workspace.batch() / daily.BOOK_NAME).exists())
        self.assertNotEqual(next_state['batch_id'], result['state']['batch_id'])
        self.assertTrue(all(r['name'] != result['state']['records'][0]['name'] for r in next_state['records']))

    def test_candidate_save_never_writes_dictionary_distribution_or_site(self):
        for name in ('data/additions.tsv', 'dist/dictionary.tsv', 'public/data/latest.json', 'README.md'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'keep this exact content\n')
        protected = [self.root / 'README.md', *[p for name in ('data', 'dist', 'public')
            for p in (self.root / name).rglob('*') if p.is_file()]]
        before = {p: p.read_bytes() for p in protected}
        payload = self.save_choice()
        preview = self.workspace.preview(payload)
        with patch.object(daily, 'build_staged', side_effect=AssertionError('Dictionary build called')), \
                patch.object(daily, 'apply', side_effect=AssertionError('Dictionary apply called')):
            result = self.workspace.apply({**payload, 'token': preview['token']})
        self.assertEqual({p: p.read_bytes() for p in protected}, before)
        self.assertEqual(result['result']['target'], 'candidate_db')
        self.assertEqual(result['result']['added_words'], 0)
        self.assertEqual(result['result']['saved_decisions'], 1)
        backup = editor.read_json(self.workspace.batch() / '完了.json')
        self.assertEqual(backup['target'], 'candidate_db')
        metadata = editor.read_json(self.db / 'candidate_editor_state.json')
        approved = result['state']['records'][0]
        self.assertEqual(metadata['records'][approved['key']]['dictionary_decision'], '採用')
        self.assertIn(approved['key'], {r['key'] for r in self.workspace.catalog('approved')['records']})
        next_state = self.workspace.next(self.payload(result['state']))
        self.assertNotIn(approved['key'], {r['key'] for r in next_state['records']})

    def test_candidate_plan_cannot_be_applied_to_dictionary(self):
        payload = self.save_choice()
        self.workspace.preview(payload)
        before = (self.root / 'data/master.tsv').read_bytes()
        with self.assertRaisesRegex(ValueError, '保存先が異なる'):
            daily.apply(self.db, self.root, self.workspace.batch() / '候補DB保存計画.json')
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), before)

    def test_dictionary_change_does_not_invalidate_candidate_db_save(self):
        payload = self.save_choice()
        preview = self.workspace.preview(payload)
        path = self.root / 'data/master.tsv'
        rows = daily.master_rows(self.root)
        rows.append({**rows[0], 'reading': 'ついかされたよみ'})
        path.write_bytes(editor.tsv_bytes(rows, daily.MASTER_COLUMNS))
        before = path.read_bytes()
        result = self.workspace.apply({**payload, 'token': preview['token']})
        self.assertEqual(result['result']['target'], 'candidate_db')
        self.assertEqual(path.read_bytes(), before)

    def test_app_change_after_review_requires_new_preview(self):
        payload = self.save_choice()
        preview = self.workspace.preview(payload)
        payload = self.save_choice('保留')
        with self.assertRaises(app_server.Conflict):
            self.workspace.apply({**payload, 'token': preview['token']})
        self.assertEqual(len(daily.master_rows(self.root)), 1)

    def test_app_incomplete_batch_and_tampered_json(self):
        state = self.workspace.state()
        with self.assertRaises(app_server.Conflict):
            self.workspace.next({**self.payload(state), 'count': 3})
        doc = editor.read_json(self.batch / app_server.REVIEW)
        doc['entries'][0]['decision'] = '自動で採用'
        (self.batch / app_server.REVIEW).write_bytes(editor.json_bytes(doc))
        with self.assertRaises(ValueError):
            self.workspace.state()

    def test_app_preserves_existing_excel_work_and_detects_later_edits(self):
        self.edit({'C6': '採用', 'F6': 'Excelで保存したメモ'})
        state = self.workspace.state()
        imported = next(r for r in state['records'] if r['key'] == self.info['records'][0]['key'])
        self.assertEqual(imported['decision'], '採用')
        self.assertEqual(imported['memo'], 'Excelで保存したメモ')
        self.edit({'F6': '引き継ぎ後の変更'})
        with self.assertRaisesRegex(ValueError, 'Excelが変更'):
            self.workspace.state()

    def test_app_api_host_origin_token_and_routes(self):
        server = app_server.Server(self.workspace, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(route, body=None, headers=None):
            data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
            defaults = {'Content-Type':'application/json', 'X-App-Token':server.token}
            defaults.update(headers or {})
            req = urllib.request.Request(server.url + route, data=data, headers=defaults)
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        try:
            status, state = request('/api/state')
            self.assertEqual(status, 200)
            status, catalog = request('/api/catalog?kind=approved')
            self.assertEqual(status, 200)
            self.assertIn('records', catalog)
            for headers in ({'Host':'evil.example'}, {'Origin':'https://evil.example'}, {'X-App-Token':'wrong'}):
                with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as failure:
                    request('/api/save', {**self.payload(state), 'entries': []}, headers)
                self.assertEqual(failure.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as failure:
                request('/api/save', ['invalid'])
            self.assertEqual(failure.exception.code, 400)
            with self.assertRaises(urllib.error.HTTPError) as failure:
                request('/../daily.py')
            self.assertEqual(failure.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def review_entries(self, state):
        return [{k: copy.deepcopy(r[k]) for k in ('key', 'decision', 'memo', 'registration') if k in r} for r in state['records']]

    def test_edited_name_and_aliases_staged_then_applied_atomically(self):
        state = self.workspace.state()
        record = state['records'][0]
        before = {p.name: p.read_bytes() for p in self.db.iterdir() if p.is_file()}
        entries = self.review_entries(state)
        form = {'name': '名前修正テスト', 'reading': 'なまえしゅうせいてすと',
            'aliases': [{'reading': 'したのなまえ', 'word': '名前修正テスト'}, {'reading': 'したのなまえ', 'word': '下の名前'}]}
        entries[0].update(decision='採用', registration=form)
        self.workspace.save({**self.payload(state), 'entries': entries})
        state = app_server.Workspace(self.root, self.db, self.out).state()
        self.assertEqual(state['records'][0]['registration'], form)
        self.assertEqual({p.name: p.read_bytes() for p in self.db.iterdir() if p.is_file()}, before)
        self.assertEqual(len(daily.master_rows(self.root)), 1)
        plan = self.workspace.preview(self.payload(state))
        self.assertEqual(len(plan['conversions']), 3)
        result = self.workspace.apply({**self.payload(state), 'token': plan['token']})
        tables, metadata, _, _ = editor.load_db(self.db)
        row = next(r for r in tables[record['kind']] if editor.make_key(record['kind'], r) == record['key'])
        self.assertEqual((row['name'], row['reading']), (form['name'], form['reading']))
        self.assertEqual(metadata['records'][record['key']]['extra_conversions'], form['aliases'])
        self.assertEqual(result['result']['added_words'], 0)
        self.assertEqual(result['result']['saved_conversions'], 3)
        if record['kind'] == 'main':
            override = editor.read_json(self.db / 'candidate_manual_overrides.json')[record['youtube_channel_id']]
            self.assertEqual(override['name'], form['name'])

    def set_db_decision(self, key, decision):
        metadata = editor.read_json(self.db / 'candidate_editor_state.json')
        metadata['records'].setdefault(key, {}).update(dictionary_decision=decision, decision_reason='過去の確認メモ')
        (self.db / 'candidate_editor_state.json').write_bytes(editor.json_bytes(metadata))

    def test_held_recall_does_not_duplicate_or_overwrite_current_choices(self):
        self.extend_pool()
        pool, _, _ = daily.candidates(self.db, self.root)
        record = pool[0]
        self.set_db_decision(record['key'], '保留')
        state = self.workspace.state()
        self.assertNotIn(record['key'], {r['key'] for r in state['records']})
        self.assertIn(record['key'], {r['key'] for r in self.workspace.catalog('held')['records']})
        result = self.workspace.recall({**self.payload(state), 'kind': 'held', 'key': record['key']})
        state = result['state']
        recalled = next(r for r in state['records'] if r['key'] == record['key'])
        self.assertEqual(recalled['decision'], '未判断')
        self.assertTrue(recalled['recalled'])
        self.assertEqual(recalled['memo'], '過去の確認メモ')
        entries = self.review_entries(state)
        next(r for r in entries if r['key'] == record['key']).update(decision='採用', memo='呼び出して採用')
        self.workspace.save({**self.payload(state), 'entries': entries})
        state = self.workspace.state()
        again = self.workspace.recall({**self.payload(state), 'kind': 'held', 'key': record['key']})['state']
        self.assertEqual(state['records'], again['records'])
        self.assertEqual(editor.read_json(self.db / 'candidate_editor_state.json')['records'][record['key']]['dictionary_decision'], '保留')
        plan = self.workspace.preview(self.payload(again))
        self.assertFalse(plan['errors'])
        result = self.workspace.apply({**self.payload(again), 'token': plan['token']})
        self.assertEqual(result['result']['added_words'], 0)
        self.assertEqual(result['result']['saved_conversions'], 1)

    def test_registered_recall_saves_all_aliases_to_candidate_db(self):
        pool, _, _ = daily.candidates(self.db, self.root)
        record = pool[0]
        self.set_db_decision(record['key'], '採用')
        master = daily.master_rows(self.root)
        master.append({**master[0], 'reading': record['reading'], 'word': record['name']})
        (self.root / 'data/master.tsv').write_bytes(editor.tsv_bytes(master, daily.MASTER_COLUMNS))
        state = self.workspace.state()
        catalog = self.workspace.catalog('approved')['records']
        item = next(r for r in catalog if r['key'] == record['key'])
        self.assertTrue(item['registered'])
        self.assertTrue(item['ready'])
        state = self.workspace.recall({**self.payload(state), 'kind': 'approved', 'key': record['key']})['state']
        entries = self.review_entries(state)
        aliases = [{'reading': 'したのなまえ', 'word': record['name']}, {'reading': 'きぞんてすと', 'word': '既存テスト'}]
        next(r for r in entries if r['key'] == record['key']).update(decision='採用',
            registration={'name': record['name'], 'reading': record['reading'], 'aliases': aliases})
        self.workspace.save({**self.payload(state), 'entries': entries})
        state = self.workspace.state()
        plan = self.workspace.preview(self.payload(state))
        self.assertEqual([(r['reading'], r['word']) for r in plan['conversions']], [(record['reading'], record['name']), *[(r['reading'], r['word']) for r in aliases]])
        self.workspace.apply({**self.payload(state), 'token': plan['token']})
        self.assertEqual(len(daily.master_rows(self.root)), 2)
        self.assertIn((record['reading'], record['name']), {(r['reading'], r['word']) for r in daily.master_rows(self.root)})

    def test_pending_edits_and_recalled_flags_survive_next_batch(self):
        self.extend_pool()
        pool, _, _ = daily.candidates(self.db, self.root)
        held = pool[0]
        self.set_db_decision(held['key'], '保留')
        state = self.workspace.state()
        state = self.workspace.recall({**self.payload(state), 'kind': 'held', 'key': held['key']})['state']
        entries = self.review_entries(state)
        form = {'name': held['name'], 'reading': held['reading'], 'aliases': [{'reading':'ほかのよみ','word':held['name']}]}
        next(r for r in entries if r['key']==held['key'])['registration'] = form
        entries[0]['decision'] = '保留'
        self.workspace.save({**self.payload(state), 'entries': entries})
        state = self.workspace.state()
        preview = self.workspace.preview(self.payload(state))
        result = self.workspace.apply({**self.payload(state), 'token': preview['token']})
        state = self.workspace.next(self.payload(result['state']))
        recalled = next(r for r in state['records'] if r['key']==held['key'])
        self.assertEqual(recalled['registration'], form)
        self.assertTrue(recalled['recalled'])

    def test_invalid_edits_and_old_preview_are_rejected(self):
        state = self.workspace.state()
        entries = self.review_entries(state)
        for form in ({'name':'', 'reading':'よみ', 'aliases':[]},
                     {'name':'名前', 'reading':'カタカナ', 'aliases':[]},
                     {'name':'名前', 'reading':'よみ', 'aliases':[{'reading':'よみ','word':'名前'}]}):
            entries[0]['registration'] = form
            with self.assertRaises(ValueError):
                self.workspace.save({**self.payload(state), 'entries':entries})
        entries[0].update(decision='採用', registration={'name':'修正', 'reading':'しゅうせい', 'aliases':[]})
        self.workspace.save({**self.payload(state), 'entries':entries})
        state = self.workspace.state()
        preview = self.workspace.preview(self.payload(state))
        entries = self.review_entries(state)
        entries[0]['registration']['aliases'] = [{'reading':'ついか','word':'追加'}]
        self.workspace.save({**self.payload(state), 'entries':entries})
        state = self.workspace.state()
        with self.assertRaises(app_server.Conflict):
            self.workspace.apply({**self.payload(state), 'token':preview['token']})
        legacy_entries = [{k:r[k] for k in ('key','decision','memo')} for r in state['records']]
        self.workspace.save({**self.payload(state), 'entries':legacy_entries})
        self.assertEqual(self.workspace.state()['records'][0]['registration']['aliases'], [{'reading':'ついか','word':'追加'}])

    def test_unverified_held_can_be_recalled_and_deferred_but_not_adopted(self):
        pool, _, _ = daily.candidates(self.db, self.root)
        record = pool[0]
        self.set_db_decision(record['key'], '保留')
        prefix = 'vtuberpost_readings' if record['kind']=='main' else 'candidates_external'
        rows = editor.read_json(self.db / (prefix+'.json'))
        row = next(r for r in rows if editor.make_key(record['kind'], r)==record['key'])
        row.update(reading_status='unknown', review_required='true')
        (self.db / (prefix+'.json')).write_bytes(editor.json_bytes(rows))
        (self.db / (prefix+'.tsv')).write_bytes(editor.tsv_bytes(rows, list(rows[0])))
        item = next(r for r in self.workspace.catalog('held')['records'] if r['key']==record['key'])
        self.assertFalse(item['ready'])
        state = self.workspace.state()
        state = self.workspace.recall({**self.payload(state),'kind':'held','key':record['key']})['state']
        entries = self.review_entries(state)
        entry = next(r for r in entries if r['key']==record['key'])
        entry['decision'] = '採用'
        self.workspace.save({**self.payload(state),'entries':entries})
        state = self.workspace.state()
        self.assertTrue(self.workspace.preview(self.payload(state))['errors'])
        entries = self.review_entries(state)
        next(r for r in entries if r['key']==record['key'])['decision'] = '保留'
        self.workspace.save({**self.payload(state),'entries':entries})
        state = self.workspace.state()
        self.assertFalse(self.workspace.preview(self.payload(state))['errors'])

    def test_recall_after_exhaustion_preserves_all_pending_drafts(self):
        state = self.workspace.state()
        held_key = state['records'][0]['key']
        entries = self.review_entries(state)
        entries[0]['decision'] = '保留'
        form = {'name':state['records'][1]['name'], 'reading':state['records'][1]['reading'], 'aliases':[{'reading':'べつのよみ','word':'別の名前'}]}
        entries[1]['registration'] = form
        self.workspace.save({**self.payload(state), 'entries':entries})
        state = self.workspace.state()
        plan = self.workspace.preview(self.payload(state))
        self.workspace.apply({**self.payload(state), 'token':plan['token']})
        # Make every unjudged source ineligible, so recall must create a queue without a fresh pool.
        metadata = editor.read_json(self.db / 'candidate_editor_state.json')
        for key in [r['key'] for r in state['records'][1:]]:
            metadata['records'].setdefault(key, {})['dictionary_decision'] = '対象外'
        (self.db / 'candidate_editor_state.json').write_bytes(editor.json_bytes(metadata))
        state = self.workspace.state()
        result = self.workspace.recall({**self.payload(state),'kind':'held','key':held_key})['state']
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['records']), 10)
        self.assertEqual(result['records'][0]['registration'], form)
        self.assertTrue(next(r for r in result['records'] if r['key']==held_key)['recalled'])

    def test_duplicate_edited_candidate_names_are_blocked(self):
        state = self.workspace.state()
        entries = self.review_entries(state)
        for entry in entries[:2]:
            entry.update(decision='採用', registration={'name':'同じ名前', 'reading':'おなじなまえ', 'aliases':[]})
        self.workspace.save({**self.payload(state),'entries':entries})
        state = self.workspace.state()
        self.assertTrue(self.workspace.preview(self.payload(state))['errors'])

    def test_rename_to_fully_registered_conversions_saves_without_duplicates(self):
        state = self.workspace.state()
        record = state['records'][0]
        entries = self.review_entries(state)
        form = {'name': '既存テスト', 'reading': 'きぞんてすと',
                'aliases': [{'reading': 'きぞんべつよみ', 'word': '既存テスト'}]}
        master = daily.master_rows(self.root)
        master.append({**master[0], 'reading': 'きぞんべつよみ'})
        (self.root / 'data/master.tsv').write_bytes(editor.tsv_bytes(master, daily.MASTER_COLUMNS))
        before = (self.root / 'data/master.tsv').read_bytes()
        next(e for e in entries if e['key'] == record['key']).update(
            decision='採用', memo='既存の変換を確認', registration=form)
        pending = next(e for e in entries if e['key'] != record['key'])
        pending['memo'] = '未確認のメモを残す'
        self.workspace.save({**self.payload(state), 'entries': entries})
        saved = self.workspace.state()
        preview = self.workspace.preview(self.payload(saved))
        self.assertEqual(preview['errors'], [])
        self.assertEqual(len(preview['conversions']), 2)
        self.assertTrue(preview['token'])
        result = self.workspace.apply({**self.payload(saved), 'token': preview['token']})
        self.assertEqual(result['result']['added_words'], 0)
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), before)
        metadata = editor.read_json(self.db / 'candidate_editor_state.json')
        self.assertEqual(metadata['records'][record['key']]['dictionary_decision'], '採用')
        self.assertEqual(metadata['records'][record['key']]['extra_conversions'], form['aliases'])
        tables, _, _, _ = editor.load_db(self.db)
        updated = next(r for r in tables[record['kind']] if editor.make_key(record['kind'], r) == record['key'])
        self.assertEqual(updated['name'], form['name'])
        self.assertEqual(next(r for r in result['state']['records'] if r['key'] == pending['key'])['memo'],
                         '未確認のメモを残す')

    def test_existing_dictionary_name_can_be_saved_as_candidate(self):
        for reading, aliases in [('べつのよみ', []), ('きぞんてすと',
                [{'reading': 'べつのよみ', 'word': '既存テスト'}])]:
            with self.subTest(reading=reading, aliases=aliases):
                state = self.workspace.state()
                entries = self.review_entries(state)
                entries[0].update(decision='採用', registration={
                    'name': '既存テスト', 'reading': reading, 'aliases': aliases})
                self.workspace.save({**self.payload(state), 'entries': entries})
                saved = self.workspace.state()
                preview = self.workspace.preview(self.payload(saved))
                self.assertEqual(preview['errors'], [])
                self.assertTrue(preview['token'])

    def test_edited_candidate_and_dictionary_roll_back_together(self):
        state = self.workspace.state()
        entries = self.review_entries(state)
        entries[0].update(decision='採用', registration={'name':'巻き戻しテスト','reading':'まきもどしてすと',
            'aliases':[{'reading':'べつよみ','word':'別の名前'}]})
        self.workspace.save({**self.payload(state),'entries':entries})
        state = self.workspace.state()
        preview = self.workspace.preview(self.payload(state))
        originals = {p:p.read_bytes() for p in self.db.iterdir() if p.is_file()}
        master_before = (self.root / 'data/master.tsv').read_bytes()
        replace = daily.replace_bytes
        failed = False
        def failing(target, data):
            nonlocal failed
            if target.name == 'candidate_editor_state.json' and not failed:
                failed = True
                raise OSError('テスト用の保存失敗')
            replace(target, data)
        with patch.object(daily, 'replace_bytes', side_effect=failing), self.assertRaises(OSError):
            self.workspace.apply({**self.payload(state),'token':preview['token']})
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), master_before)
        self.assertEqual({p:p.read_bytes() for p in self.db.iterdir() if p.is_file()}, originals)
        self.assertFalse((self.workspace.batch() / '完了.json').exists())

    def test_bulk_held_recall_is_saved_once_and_keeps_all_candidates(self):
        self.extend_pool()
        pool, _, _ = daily.candidates(self.db, self.root)
        keys = [r['key'] for r in pool[:15]]
        for key in keys:
            self.set_db_decision(key, '保留')
        state = self.workspace.state()
        original = copy.deepcopy(state['records'])
        db_before = {p:p.read_bytes() for p in self.db.iterdir() if p.is_file()}
        result = self.workspace.recall({**self.payload(state), 'kind':'held', 'keys':keys})
        after = result['state']
        self.assertEqual(after['revision'], state['revision']+1)
        self.assertEqual(result['selected_keys'], keys)
        self.assertEqual(after['records'][:len(original)], original)
        self.assertEqual(len(after['records']), len(original)+15)
        self.assertTrue(all(r['decision']=='未判断' and r['recalled'] for r in after['records'][-15:]))
        self.assertEqual(app_server.Workspace(self.root, self.db, self.out).state()['records'], after['records'])
        self.assertEqual({p:p.read_bytes() for p in self.db.iterdir() if p.is_file()}, db_before)
        repeated = self.workspace.recall({**self.payload(after), 'kind':'held', 'keys':keys})['state']
        self.assertEqual(repeated['records'], after['records'])
        self.assertEqual(repeated['revision'], after['revision'])
        with self.assertRaises(app_server.Conflict):
            self.workspace.recall({**self.payload(state), 'kind':'held', 'keys':keys})

    def test_bulk_approved_recall_combines_local_and_db_without_overwriting(self):
        self.extend_pool()
        state = self.workspace.state()
        local = state['records'][0]
        entries = self.review_entries(state)
        entries[0].update(decision='採用', memo='今の採用メモ', registration={
            'name':'編集した採用予定', 'reading':'へんしゅうしたさいようよてい', 'aliases':[]})
        self.workspace.save({**self.payload(state),'entries':entries})
        state = self.workspace.state()
        pool, _, _ = daily.candidates(self.db, self.root)
        existing_keys = {r['key'] for r in state['records']}
        db_keys = [r['key'] for r in pool if r['key'] not in existing_keys][:3]
        for key in db_keys:
            self.set_db_decision(key, '採用')
        keys = [local['key'], *db_keys]
        result = self.workspace.recall({**self.payload(state),'kind':'approved','keys':keys})
        self.assertEqual(result['selected_keys'], keys)
        self.assertEqual(result['state']['records'][0], state['records'][0])
        self.assertEqual(len(result['state']['records']), len(state['records'])+3)
        self.assertEqual(len(daily.master_rows(self.root)), 1)

    def test_bulk_recall_invalid_selection_never_adds_partial_results(self):
        self.extend_pool()
        pool, _, _ = daily.candidates(self.db, self.root)
        key = pool[0]['key']
        self.set_db_decision(key, '保留')
        state = self.workspace.state()
        before = (self.workspace.batch() / app_server.REVIEW).read_bytes()
        for keys in ([], [key, key], [key, 'main:missing'], [key, 123], 'not-a-list'):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.workspace.recall({**self.payload(state),'kind':'held','keys':keys})
            self.assertEqual((self.workspace.batch() / app_server.REVIEW).read_bytes(), before)

    def test_bulk_recall_after_completion_keeps_pending_edits_and_large_selection(self):
        self.extend_pool()
        pool, _, _ = daily.candidates(self.db, self.root)
        held = [r['key'] for r in pool[:15]]
        for key in held:
            self.set_db_decision(key, '保留')
        state = self.workspace.state()
        entries = self.review_entries(state)
        entries[0]['decision'] = '保留'
        entries[1]['registration'] = {'name':'次回の編集', 'reading':'じかいのへんしゅう', 'aliases':[]}
        self.workspace.save({**self.payload(state),'entries':entries})
        state = self.workspace.state()
        preview = self.workspace.preview(self.payload(state))
        done = self.workspace.apply({**self.payload(state),'token':preview['token']})['state']
        pending = [r for r in done['records'] if r['decision']=='未判断']
        result = self.workspace.recall({**self.payload(done),'kind':'held','keys':held})['state']
        self.assertFalse(result['complete'])
        self.assertEqual(result['records'][:len(pending)], pending)
        self.assertEqual(len(result['records']), len(pending)+15)


if __name__ == '__main__':
    unittest.main()
