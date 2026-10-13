"""Behavioral checks use tiny fictitious dictionaries and isolated output trees."""
import copy
import csv
import io
import json
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build
import build_site
import ledger
import publications
import release_archive


def row(reading='ふぃくすちゃ', word='架空甲', origin='upstream'):
    return dict(reading=reading, word=word, google_pos='人名', microsoft_pos='人名', atok_pos='人名',
                origin=origin, source_url='https://example.test/profile' if origin == 'added' else '',
                note='', added_on='2026-10-05' if origin == 'added' else '')


def tsv(rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, ledger.COLUMNS, delimiter='\t', lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')


def publication_record(version, files):
    release = json.loads(files['release.json'])
    return dict(id='publication-fixture', version=version, published_at='2026-10-05T12:34:56+09:00',
                url='https://example.test/release', evidence='fixture only; no network request',
                update_ids=release['updateIds'], change_ids=release['changeIds'],
                release_sha256=ledger.digest(files['release.json']),
                zip_sha256=ledger.digest(files[f'F-VTD-{version}.zip']))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.a, self.b = row(), row('ふぃくすちゃおつ', '架空乙')
        self.baseline = [self.a, self.b]
        self.upstreams = {'test': {'version': 'fixture', 'files': {'fixture.tsv': 'f' * 64}}}
        for folder in ('data/baseline', 'dist', 'scripts', 'site', 'package'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        for path in ('scripts/build.py', 'scripts/build_site.py', 'scripts/ledger.py', 'scripts/publications.py', 'scripts/release_archive.py',
                     'package/README.txt', 'NOTICE.md'):
            (self.root / path).write_bytes((ROOT / path).read_bytes())
        (self.root / 'site/index.html').write_text('<html>fixture</html>', encoding='utf-8')
        content = tsv(self.baseline)
        (self.root / 'data/baseline/master.tsv').write_bytes(content)
        info = dict(count=2, sha256=ledger.digest(content), upstream='test', upstream_date='2026-09-03',
                    upstream_sha256=ledger.digest(ledger.canonical(self.upstreams['test'])),
                    reconstructed_updates=0, reconstructed_head=None)
        (self.root / 'data/baseline/info.json').write_bytes(ledger.canonical(info))
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical(self.upstreams))
        (self.root / 'data/ledger.jsonl').write_bytes(b'')
        (self.root / 'data/publications.jsonl').write_bytes(b'')
        self.write_master(self.baseline)
        self.updates = []
        # In tests, HEAD seals are separately simulated; no fixture commits are made.
        self.git_patch = patch.object(ledger, 'git_seals')
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def write_master(self, rows):
        (self.root / 'data/master.tsv').write_bytes(tsv(rows))

    def change(self, cid, kind, before, after):
        return ledger.new_change(cid, kind, before, after, 'test', reason='fixture evidence',
                                 urls=['https://example.test/evidence'], checked_on='2026-10-05')

    def update(self, changes, uid=None, day='2026-10-05'):
        value = dict(id=uid or f'update-{len(self.updates)+1}', scope='applied', status='final',
                     title='架空更新', applied_on=day, restoration=None, changes=changes)
        sealed = ledger.seal(value, self.updates[-1]['hash'] if self.updates else None)
        self.updates.append(sealed)
        (self.root / 'data/ledger.jsonl').write_bytes(b''.join(ledger.canonical(u)+b'\n' for u in self.updates))
        return sealed

    def build(self):
        build.main(self.root)
        return build_site.prepare_release(self.root)

    def tree(self, path):
        return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}

    def test_add_correct_delete_and_exact_parity(self):
        added = row('しんき', '架空新規', 'added')
        corrected = dict(self.a, reading='せいてい')
        self.update([self.change('c1', 'add', None, added)])
        self.update([self.change('c2', 'correct', self.a, corrected)], day='2026-10-06')
        self.update([self.change('c3', 'delete', self.b, None)], day='2026-10-07')
        self.write_master([corrected, added])
        version, manifest, files = self.build()
        self.assertTrue(version.startswith('20261007-'))
        self.assertEqual(manifest['changeIds'], ['c1', 'c2', 'c3'])
        self.assertEqual(json.loads(files['search.json']), [{'reading': r['reading'], 'word': r['word'], 'excluded': []} for r in [corrected, added]])
        self.assertNotIn(self.a['reading'], files['search.json'].decode())
        build.validate_outputs([corrected, added], files)
        with zipfile.ZipFile(io.BytesIO(files[f'F-VTD-{version}.zip'])) as archive:
            for name in build.DIST_FILES:
                self.assertEqual(archive.read(f'F-VTD-{version}/{name}'), files[name])
            release = json.loads(archive.read(f'F-VTD-{version}/release.json'))
            self.assertEqual(release['version'], manifest['version'])
            for name in build.DIST_FILES + ['search.json']:
                self.assertEqual(release['hashes'][name], ledger.digest(files[name]))

    def test_correction_only_changes_date_version_and_history(self):
        v1, _, _ = self.build()
        corrected = dict(self.a, reading='しゅうせい')
        self.update([self.change('fix', 'correct', self.a, corrected)], day='2026-10-06')
        self.write_master([corrected, self.b])
        v2, manifest, _ = self.build()
        self.assertNotEqual(v1, v2)
        self.assertEqual(manifest['updatedAt'], '2026-10-06T00:00:00+09:00')
        self.assertEqual(manifest['ledgerChanges'][0]['corrected'][0]['before'], self.a)
        self.assertEqual(manifest['ledgerChanges'][0]['corrected'][0]['after'], corrected)

    def test_delete_only_changes_date_version_and_history(self):
        v1, _, _ = self.build()
        self.update([self.change('del', 'delete', self.a, None)], day='2026-10-07')
        self.write_master([self.b])
        v2, manifest, files = self.build()
        self.assertNotEqual(v1, v2)
        self.assertEqual(manifest['count'], 1)
        self.assertEqual(manifest['ledgerChanges'][0]['removed'][0]['before'], self.a)
        self.assertNotIn(self.a['word'], files['search.json'].decode())

    def test_undo_add_correct_delete(self):
        added = row('しんき', '架空新規', 'added')
        corrected = dict(self.a, reading='せい')
        for kind, before, after in [('add', None, added), ('correct', self.a, corrected), ('delete', self.b, None)]:
            original = self.change(kind, kind, before, after)
            self.update([original])
            inverse = self.change('undo-' + kind, 'undo', after, before)
            inverse['related_ids'] = [kind]
            self.update([inverse])
        rows, _, _, _ = ledger.load(self.root)
        self.assertEqual(rows, self.baseline)

    def test_annotation_does_not_change_dictionary_or_overwrite_original(self):
        original = self.change('fix', 'correct', self.a, dict(self.a, reading='せい'))
        self.update([original])
        saved = copy.deepcopy(self.updates[0])
        annotation = self.change('note', 'annotate', None, None)
        annotation['related_ids'] = ['fix']
        annotation['metadata_before'] = {k: original[k] for k in ledger.META}
        annotation['metadata_after'] = dict(annotation['metadata_before'], reason='訂正した理由', evidence={'urls': ['https://example.test/new'], 'text': '最小記述'})
        self.update([annotation], day='2026-10-06')
        self.write_master([original['after'], self.b])
        _, _, known, _ = ledger.load(self.root)
        self.assertEqual(known['fix']['reason'], '訂正した理由')
        self.assertEqual(self.updates[0], saved)
        self.assertEqual(known['fix']['after'], original['after'])

    def test_annotation_precondition_conflict(self):
        original = self.change('fix', 'correct', self.a, dict(self.a, reading='せい'))
        self.update([original])
        annotation = self.change('note', 'annotate', None, None)
        annotation['related_ids'] = ['fix']
        annotation['metadata_before'] = {k: original[k] for k in ledger.META}
        annotation['metadata_before']['reason'] = 'wrong'
        annotation['metadata_after'] = {k: original[k] for k in ledger.META}
        self.update([annotation])
        with self.assertRaisesRegex(ValueError, 'metadata mismatch'):
            ledger.load(self.root, match_master=False)

    def test_duplicate_undo_rejected(self):
        self.update([self.change('del', 'delete', self.a, None)])
        undo = self.change('undo', 'undo', None, self.a)
        undo['related_ids'] = ['del']
        self.update([undo])
        duplicate = dict(undo, id='undo-again')
        self.update([duplicate])
        with self.assertRaisesRegex(ValueError, 'Duplicate/invalid undo'):
            ledger.load(self.root)

    def test_before_mismatch_and_double_application(self):
        bad = dict(self.a, note='wrong old state')
        self.update([self.change('bad', 'correct', bad, dict(self.a, reading='せい'))])
        with self.assertRaisesRegex(ValueError, 'Before state mismatch'):
            ledger.load(self.root)
        self.updates = []
        corrected = dict(self.a, reading='せい')
        self.update([self.change('fix', 'correct', self.a, corrected)])
        self.update([self.change('twice', 'correct', self.a, corrected)])
        with self.assertRaisesRegex(ValueError, 'Before state mismatch'):
            ledger.load(self.root)

    def test_duplicate_ids_and_registration_conflict(self):
        added = row('しん', '架空新', 'added')
        self.update([self.change('same', 'add', None, added), self.change('same', 'delete', self.b, None)])
        with self.assertRaisesRegex(ValueError, 'change ID'):
            ledger.load(self.root)
        self.updates = []
        self.update([self.change('one', 'add', None, added)], uid='same')
        self.update([self.change('two', 'delete', self.b, None)], uid='same')
        with self.assertRaisesRegex(ValueError, 'update ID'):
            ledger.load(self.root)
        self.updates = []
        self.update([self.change('one', 'add', None, self.a)])
        with self.assertRaisesRegex(ValueError, 'Duplicate application/conflict'):
            ledger.load(self.root)

    def test_origin_and_added_on_are_not_correction_dates(self):
        self.update([self.change('wrong', 'correct', self.a, dict(self.a, origin='added', added_on='2026-10-05', source_url='https://example.test'))])
        with self.assertRaisesRegex(ValueError, 'preserve origin'):
            ledger.load(self.root)
        self.updates = []
        bad = dict(self.a, added_on='2026-10-05')
        self.update([self.change('wrong', 'correct', self.a, bad)])
        with self.assertRaisesRegex(ValueError, 'origin/added_on'):
            ledger.load(self.root)

    def test_unrecorded_and_ledger_only_changes_no_partial_outputs(self):
        self.build()
        public = self.root / 'public'
        build_site.main(self.root)
        before_dist, before_public = self.tree(self.root / 'dist'), self.tree(public)
        self.write_master([dict(self.a, reading='みきろく'), self.b])
        for func in (build.main, build_site.main):
            with self.assertRaisesRegex(ValueError, 'Unrecorded or ledger-only'):
                func(self.root)
        self.assertEqual(before_dist, self.tree(self.root / 'dist'))
        self.assertEqual(before_public, self.tree(public))
        self.write_master(self.baseline)
        self.update([self.change('only-ledger', 'delete', self.a, None)])
        with self.assertRaisesRegex(ValueError, 'Unrecorded or ledger-only'):
            build.main(self.root)
        self.assertEqual(before_dist, self.tree(self.root / 'dist'))

    def test_stale_dist_site_check_is_read_only(self):
        self.build()
        build_site.main(self.root)
        target = self.root / 'dist' / build.DIST_FILES[0]
        target.write_bytes(b'stale')
        before = self.tree(self.root)
        with self.assertRaisesRegex(ValueError, 'Stale dist'):
            build_site.main(self.root)
        self.assertEqual(before, self.tree(self.root))

    def test_exact_duplicates_rejected_other_readings_names_preserved(self):
        self.write_master([self.a, dict(self.a)])
        with self.assertRaisesRegex(ValueError, 'Duplicate registration'):
            ledger.read_rows(self.root / 'data/master.tsv')
        independent = [self.a, dict(self.a, reading='べつよみ'), dict(self.a, word='同名別人候補')]
        self.write_master(independent)
        self.assertEqual(ledger.read_rows(self.root / 'data/master.tsv'), independent)

    def test_reproducibility_and_drafts_excluded(self):
        v1, m1, f1 = self.build()
        drafts = self.root / 'data/drafts'
        drafts.mkdir()
        (drafts / 'pending.json').write_text('{"status":"draft"}', encoding='utf-8')
        v2, m2, f2 = self.build()
        self.assertEqual((v1, m1, f1), (v2, m2, f2))
        build_site.main(self.root)
        before = self.tree(self.root / 'public')
        build_site.main(self.root)
        self.assertEqual(before, self.tree(self.root / 'public'))
        self.assertEqual((self.root / 'data/publications.jsonl').read_bytes(), b'')
        self.assertEqual(m1['publicationStatus'], 'unconfirmed')

    def test_generator_or_output_change_never_reuses_immutable_url(self):
        v1, _, _ = self.build()
        script = self.root / 'scripts/build.py'
        script.write_bytes(script.read_bytes() + b'\n# generation change\n')
        v2, _, _ = self.build()
        self.assertNotEqual(v1, v2)
        original_render = build.render
        def modified_render(rows):
            outputs = original_render(rows)
            name = 'VTuber変換辞書_MicrosoftIME.tsv'
            outputs[name] = b'\xff\xfe' + ('!fixture header\r\n' + outputs[name][2:].decode('utf-16le')).encode('utf-16le')
            build.validate_outputs(rows, outputs)
            return outputs
        with patch.object(build, 'render', modified_render):
            v3, _, _ = self.build()
        self.assertNotEqual(v2, v3)

    def test_explicit_correction_pairing_only(self):
        new = dict(self.a, reading='ふぃくすちや')
        automatic = ledger.difference([self.a], [new])
        self.assertEqual([k for k, _, _ in automatic], ['delete', 'add'])
        pair = dict(before=ledger.row_id(self.a), after=ledger.row_id(new))
        self.assertEqual(ledger.difference([self.a], [new], [pair]), [('correct', self.a, new)])
        with self.assertRaisesRegex(ValueError, 'duplicate correction mapping'):
            ledger.difference([self.a], [new], [pair, pair])

    def test_confirm_requires_complete_draft_and_never_edits_master(self):
        added = row('しん', '架空新', 'added')
        change = self.change('new', 'add', None, added)
        draft = dict(id='draft1', scope='applied', status='draft', title='下書き', applied_on='2026-10-05', restoration=None, changes=[change])
        path = self.root / 'draft.json'
        self.write_master(self.baseline + [added])
        saved = (self.root / 'data/master.tsv').read_bytes()
        change['reason'] = None
        change['unknown'] = ['reason']
        path.write_bytes(ledger.canonical(draft))
        with self.assertRaisesRegex(ValueError, 'reason is required'):
            ledger.append_draft(path, self.root)
        self.assertEqual((self.root / 'data/ledger.jsonl').read_bytes(), b'')
        change['reason'] = '確認した根拠'
        change['unknown'] = []
        path.write_bytes(ledger.canonical(draft))
        ledger.append_draft(path, self.root)
        ledger.load(self.root)
        self.assertEqual((self.root / 'data/master.tsv').read_bytes(), saved)

    def test_draft_in_finalized_ledger_and_date_order_rejected(self):
        value = self.update([self.change('d', 'delete', self.a, None)])
        value['status'] = 'draft'
        self.updates[0] = ledger.seal(value, None)
        (self.root / 'data/ledger.jsonl').write_bytes(ledger.canonical(self.updates[0]))
        with self.assertRaisesRegex(ValueError, 'Drafts'):
            ledger.load(self.root)
        self.updates = []
        self.update([self.change('d', 'delete', self.a, None)], day='2026-10-06')
        self.update([self.change('e', 'delete', self.b, None)], day='2026-10-05')
        with self.assertRaisesRegex(ValueError, 'dates out of order'):
            ledger.load(self.root)

    def test_baseline_upstream_and_hash_chain_tampering(self):
        path = self.root / 'data/baseline/master.tsv'
        path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError, 'Baseline hash mismatch'):
            ledger.load(self.root)
        path.write_bytes(tsv(self.baseline))
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical({'test': {'version': 'changed'}}))
        with self.assertRaisesRegex(ValueError, 'upstream hashes changed'):
            ledger.load(self.root)
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical(self.upstreams))
        value = self.update([self.change('d', 'delete', self.a, None)])
        value['title'] = 'tampered'
        (self.root / 'data/ledger.jsonl').write_bytes(ledger.canonical(value))
        with self.assertRaisesRegex(ValueError, 'hash chain mismatch'):
            ledger.load(self.root)

    def test_reconstructed_records_are_skipped_and_sealed(self):
        historical = self.change('old', 'add', None, self.a)
        value = dict(id='historic', scope='reconstructed', status='final', title='復元', applied_on=None,
                     restoration={'sources': ['fixture']}, changes=[historical])
        value = ledger.seal(value, None)
        path = self.root / 'data/baseline/info.json'
        info = json.loads(path.read_bytes())
        info.update(reconstructed_updates=1, reconstructed_head=value['hash'])
        path.write_bytes(ledger.canonical(info))
        (self.root / 'data/ledger.jsonl').write_bytes(ledger.canonical(value))
        self.assertEqual(ledger.load(self.root)[0], self.baseline)
        extra = dict(value, id='extra', changes=[dict(historical, id='extra-c')])
        extra = ledger.seal(extra, value['hash'])
        (self.root / 'data/ledger.jsonl').write_bytes(ledger.canonical(value)+b'\n'+ledger.canonical(extra))
        with self.assertRaisesRegex(ValueError, 'sealed migration records'):
            ledger.load(self.root)

    def test_publication_confirmation_is_separate_and_keeps_immutable_files(self):
        self.update([self.change('published-delete', 'delete', self.a, None)])
        self.write_master([self.b])
        version, before, files = self.build()
        release = json.loads(files['release.json'])
        record = dict(id='publication-fixture', version=version, published_at='2026-10-05T12:34:56+09:00',
                      url='https://example.test/release', evidence='fixture only; no network request',
                      update_ids=release['updateIds'], change_ids=release['changeIds'],
                      release_sha256=ledger.digest(files['release.json']), zip_sha256=ledger.digest(files[f'F-VTD-{version}.zip']))
        bad = dict(record, published_at='2026-10-05T12:34:56')
        with self.assertRaisesRegex(ValueError, 'timezone'):
            publications.append(bad, self.root)
        self.assertEqual((self.root / 'data/publications.jsonl').read_bytes(), b'')
        publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=files[f'F-VTD-{version}.zip'])
        v2, after, files2 = build_site.prepare_release(self.root)
        self.assertEqual(version, v2)
        self.assertEqual(files, files2)
        self.assertEqual(before['publicationStatus'], 'unconfirmed')
        self.assertEqual(after['publicationStatus'], 'confirmed')
        self.assertEqual(before['ledgerChanges'][0]['publishedIn'], [])
        self.assertEqual(after['ledgerChanges'][0]['publishedIn'], [version])
        self.assertEqual(after['ledgerChanges'][0]['removed'][0]['publishedIn'], [version])
        with self.assertRaisesRegex(ValueError, 'Duplicate publication'):
            publications.append(record, self.root)

    def test_old_publication_cli_archives_exact_artifacts_after_work_moves_on(self):
        corrected = dict(self.a, reading='ていせいこう')
        self.update([self.change('first', 'correct', self.a, corrected)])
        self.write_master([corrected, self.b])
        v1, _, files1 = self.build()
        retained_release, retained_zip = self.root / 'retained.json', self.root / 'retained.zip'
        retained_release.write_bytes(files1['release.json'])
        retained_zip.write_bytes(files1[f'F-VTD-{v1}.zip'])
        build_site.main(self.root)
        self.assertFalse((self.root / 'releases').exists())
        self.update([self.change('second', 'delete', self.b, None)], day='2026-10-06')
        self.write_master([corrected])
        script = self.root / 'scripts/build.py'
        script.write_bytes(script.read_bytes() + b'\n# later generator revision\n')
        self.upstreams['next'] = dict(version='next-fixture', files={'next.tsv': 'e'*64})
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical(self.upstreams))
        v2, _, files2 = self.build()
        build_site.main(self.root)
        self.assertFalse((self.root / 'public/files' / v1).exists())
        self.write_master([corrected, row('さぎょうちゅう', '架空下書き', 'added')])
        saved_master, saved_dist = (self.root / 'data/master.tsv').read_bytes(), self.tree(self.root / 'dist')
        command = [sys.executable, str(ROOT / 'scripts/publications.py'), '--root', str(self.root),
                   '--release', str(retained_release), '--zip', str(retained_zip), '--id', 'late-publication',
                   '--published-at', '2026-10-05T12:00:00+09:00', '--url', 'https://example.test/release',
                   '--evidence', 'Fixture of the separately verified earlier publication']
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = publications.load(self.root)[0]
        self.assertEqual(record['version'], v1)
        self.assertEqual(release_archive.read(self.root, record), (retained_release.read_bytes(), retained_zip.read_bytes()))
        self.assertEqual(saved_master, (self.root / 'data/master.tsv').read_bytes())
        self.assertEqual(saved_dist, self.tree(self.root / 'dist'))
        self.write_master([corrected])
        v3, manifest, files3 = build_site.prepare_release(self.root)
        self.assertEqual((v2, files2), (v3, files3))
        self.assertEqual(manifest['publicationStatus'], 'unconfirmed')
        self.assertEqual(manifest['ledgerChanges'][0]['publishedIn'], [])
        self.assertEqual(manifest['ledgerChanges'][1]['publishedIn'], [v1])
        archive_before = self.tree(self.root / 'releases')
        build_site.main(self.root)
        self.assertEqual(archive_before, self.tree(self.root / 'releases'))
        self.assertEqual([p.name for p in (self.root / 'public/files').iterdir()], [v2])

    def test_invalid_confirmation_never_writes_archive_or_publication(self):
        version, _, files = self.build()
        record = publication_record(version, files)
        broken_zip = b'not a ZIP'
        record['zip_sha256'] = ledger.digest(broken_zip)
        data_before, dist_before = self.tree(self.root / 'data'), self.tree(self.root / 'dist')
        with self.assertRaisesRegex(ValueError, 'Invalid release ZIP'):
            publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=broken_zip)
        self.assertEqual(data_before, self.tree(self.root / 'data'))
        self.assertEqual(dist_before, self.tree(self.root / 'dist'))
        self.assertFalse((self.root / 'releases').exists())

    def test_archive_corruption_blocks_site_before_replacing_output(self):
        version, _, files = self.build()
        record = publication_record(version, files)
        publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=files[f'F-VTD-{version}.zip'])
        build_site.main(self.root)
        before = self.tree(self.root / 'public')
        archive_zip = release_archive.destination(self.root, version) / f'F-VTD-{version}.zip'
        archive_zip.write_bytes(archive_zip.read_bytes() + b'tamper')
        with self.assertRaisesRegex(ValueError, 'archive hash mismatch'):
            build_site.main(self.root)
        self.assertEqual(before, self.tree(self.root / 'public'))

    def test_confirmation_refuses_to_overwrite_existing_archive(self):
        version, _, files = self.build()
        record = publication_record(version, files)
        folder = release_archive.destination(self.root, version)
        folder.mkdir(parents=True)
        (folder / 'release.json').write_bytes(files['release.json'])
        (folder / f'F-VTD-{version}.zip').write_bytes(b'previous archive bytes')
        before = self.tree(folder)
        with self.assertRaisesRegex(ValueError, 'Refusing to overwrite'):
            publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=files[f'F-VTD-{version}.zip'])
        self.assertEqual(before, self.tree(folder))
        self.assertEqual((self.root / 'data/publications.jsonl').read_bytes(), b'')

    def test_failed_publication_write_rolls_back_new_archive(self):
        version, _, files = self.build()
        record = publication_record(version, files)
        with patch.object(Path, 'replace', side_effect=OSError('fixture write failure')):
            with self.assertRaisesRegex(OSError, 'fixture write failure'):
                publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=files[f'F-VTD-{version}.zip'])
        self.assertEqual((self.root / 'data/publications.jsonl').read_bytes(), b'')
        self.assertFalse(release_archive.destination(self.root, version).exists())
        self.assertFalse((self.root / 'data/publications.jsonl.tmp').exists())
        self.assertEqual(self.tree(self.root / 'releases'), {})

    def test_archive_preserves_master_order_not_recorded_as_events(self):
        self.write_master([self.b, self.a])
        version, _, files = self.build()
        record = publication_record(version, files)
        publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=files[f'F-VTD-{version}.zip'])
        self.assertEqual(release_archive.read(self.root, record)[1], files[f'F-VTD-{version}.zip'])

    def test_self_consistent_forged_zip_cannot_replace_historical_registrations(self):
        version, _, files = self.build()
        descriptor = json.loads(files['release.json'])
        forged_rows = [dict(self.a, reading='べつのとうろく'), self.b]
        outputs = build.render(forged_rows)
        readme_hash = descriptor['hashes']['README.txt']
        descriptor['hashes'] = {name: ledger.digest(value) for name, value in outputs.items()}
        descriptor['hashes']['search.json'] = ledger.digest(ledger.canonical([
            {'reading': r['reading'], 'word': r['word'], 'excluded': []} for r in forged_rows]))
        descriptor['inputs'].update(descriptor['hashes'])
        descriptor['hashes']['README.txt'] = readme_hash
        forged_version = descriptor['updatedOn'].replace('-', '') + '-' + ledger.digest(ledger.canonical(descriptor['inputs']))[:16]
        descriptor['version'] = forged_version
        rendered = descriptor['readmeTemplate'].replace('{version}', forged_version).replace('{count}', f'{descriptor["count"]:,}').encode('utf-8-sig')
        descriptor['hashes']['README.txt'] = ledger.digest(rendered)
        release = ledger.canonical(descriptor) + b'\n'
        with zipfile.ZipFile(io.BytesIO(files[f'F-VTD-{version}.zip'])) as original:
            extras = {name: original.read(f'F-VTD-{version}/{name}') for name in ('README.txt', 'NOTICE.md')}
        extras['README.txt'] = rendered
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, data in dict(outputs, **extras, **{'release.json': release}).items():
                archive.writestr(f'F-VTD-{forged_version}/{name}', data)
        with self.assertRaisesRegex(ValueError, 'differs from historical ledger'):
            release_archive.validate_artifacts(self.root, release, buffer.getvalue())

    def test_release_cannot_claim_only_a_later_update_without_prior_history(self):
        changed = dict(self.a, reading='ていせい')
        self.update([self.change('first', 'correct', self.a, changed)])
        self.update([self.change('second', 'delete', self.b, None)], day='2026-10-06')
        self.write_master([changed])
        version, _, files = self.build()
        descriptor = json.loads(files['release.json'])
        descriptor['updateIds'] = [self.updates[1]['id']]
        descriptor['changeIds'] = ['second']
        descriptor['inputs']['ledger'] = ledger.digest(ledger.canonical(self.updates[1:]))
        descriptor['version'] = descriptor['updatedOn'].replace('-', '') + '-' + ledger.digest(ledger.canonical(descriptor['inputs']))[:16]
        with self.assertRaisesRegex(ValueError, 'finalized ledger prefix'):
            release_archive.validate_artifacts(self.root, ledger.canonical(descriptor), files[f'F-VTD-{version}.zip'])

    def repack(self, version, files, *, readme=None, release=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(files[f'F-VTD-{version}.zip'])) as original:
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as archive:
                for name in reversed(original.namelist()):
                    content = original.read(name)
                    if name.endswith('/README.txt') and readme is not None:
                        content = readme
                    if name.endswith('/release.json') and release is not None:
                        content = release
                    archive.writestr(name, content)
        return buffer.getvalue()

    def test_readme_only_tamper_is_rejected_before_any_confirmation_write(self):
        version, _, files = self.build()
        broken = self.repack(version, files, readme=b'wrong release instructions')
        record = publication_record(version, files)
        record['zip_sha256'] = ledger.digest(broken)
        before = self.tree(self.root)
        with self.assertRaisesRegex(ValueError, 'README hash mismatch'):
            publications.append(record, self.root, release_bytes=files['release.json'], zip_bytes=broken)
        self.assertEqual(before, self.tree(self.root))

    def test_readme_and_descriptor_hash_tamper_cannot_reuse_version(self):
        version, _, files = self.build()
        descriptor = json.loads(files['release.json'])
        descriptor['hashes']['README.txt'] = ledger.digest(b'wrong')
        release = ledger.canonical(descriptor) + b'\n'
        broken = self.repack(version, files, readme=b'wrong', release=release)
        with self.assertRaisesRegex(ValueError, 'README template mismatch'):
            release_archive.validate_artifacts(self.root, release, broken)
        descriptor['readmeTemplate'] = 'wrong'
        release = ledger.canonical(descriptor) + b'\n'
        broken = self.repack(version, files, readme=b'wrong', release=release)
        with self.assertRaisesRegex(ValueError, 'template hash mismatch'):
            release_archive.validate_artifacts(self.root, release, broken)

    def test_equivalent_recompression_and_json_whitespace_keep_confirmed_bytes(self):
        version, _, files = self.build()
        release = json.dumps(json.loads(files['release.json']), indent=2).encode()
        repacked = self.repack(version, files, release=release)
        self.assertNotEqual(repacked, files[f'F-VTD-{version}.zip'])
        candidate = dict(files, **{'release.json': release, f'F-VTD-{version}.zip': repacked})
        publications.append(publication_record(version, candidate), self.root,
                            release_bytes=release, zip_bytes=repacked)
        after_version, after_manifest, after_files = build_site.prepare_release(self.root)
        self.assertEqual(after_version, version)
        self.assertEqual(after_manifest['publicationStatus'], 'confirmed')
        self.assertEqual(after_files['release.json'], release)
        self.assertEqual(after_files[f'F-VTD-{version}.zip'], repacked)
        self.assertEqual(after_manifest['hashes']['zip'], ledger.digest(repacked))

    def test_legacy_readme_requires_retained_template_after_generators_move_on(self):
        version, _, files = self.build()
        template = (self.root / 'package/README.txt').read_bytes()
        descriptor = json.loads(files['release.json'])
        del descriptor['hashes']['README.txt']
        del descriptor['readmeTemplate']
        release = ledger.canonical(descriptor) + b'\n'
        original = self.repack(version, files, release=release)
        (self.root / 'package/README.txt').write_bytes(b'next template {version}')
        (self.root / 'scripts/build.py').write_bytes(b'next generator')
        with patch.object(build_site.sys, 'version_info', (99, 1, 2)), patch.object(build_site, 'prepare_release', side_effect=AssertionError('No current rebuild allowed')):
            release_archive.validate_artifacts(self.root, release, original, readme_template=template)
        self.write_master([self.a])  # draft is intentionally ahead of finalized history
        with self.assertRaisesRegex(ValueError, 'requires historical README template'):
            release_archive.validate_artifacts(self.root, release, original)
        with self.assertRaisesRegex(ValueError, 'template hash mismatch'):
            release_archive.validate_artifacts(self.root, release, original, readme_template=b'wrong')
        bad = self.repack(version, files, release=release, readme=b'wrong')
        with self.assertRaisesRegex(ValueError, 'README template mismatch'):
            release_archive.validate_artifacts(self.root, release, bad, readme_template=template)
        candidate = dict(files, **{'release.json': release, f'F-VTD-{version}.zip': original})
        publications.append(publication_record(version, candidate), self.root,
                            release_bytes=release, zip_bytes=original, readme_template=template)
        self.assertEqual(release_archive.read(self.root, publications.load(self.root)[0]), (release, original))

    def test_reloaded_html_uses_new_asset_cache_keys(self):
        import shutil
        import re
        shutil.copytree(ROOT / 'site', self.root / 'site', dirs_exist_ok=True)
        self.build()
        build_site.main(self.root)
        first = (self.root / 'public/index.html').read_text(encoding='utf-8')
        for name in ('app.js', 'style.css'):
            self.assertIn('/' + name + '?v=' + ledger.digest((self.root / 'site' / name).read_bytes())[:16], first)
        app = self.root / 'site/app.js'
        app.write_bytes(app.read_bytes() + b'\n// fixture next client revision\n')
        build_site.main(self.root)
        second = (self.root / 'public/index.html').read_text(encoding='utf-8')
        self.assertNotEqual(re.search(r'/app.js\?v=[0-9a-f]+', first).group(),
                            re.search(r'/app.js\?v=[0-9a-f]+', second).group())

    def test_rendered_readme_hash_is_outside_version_inputs(self):
        version, _, files = self.build()
        descriptor = json.loads(files['release.json'])
        self.assertNotIn('README.txt', descriptor['inputs'])
        self.assertEqual(version, descriptor['updatedOn'].replace('-', '') + '-' +
                         ledger.digest(ledger.canonical(descriptor['inputs']))[:16])
        with zipfile.ZipFile(io.BytesIO(files[f'F-VTD-{version}.zip'])) as archive:
            self.assertEqual(descriptor['hashes']['README.txt'],
                             ledger.digest(archive.read(f'F-VTD-{version}/README.txt')))

    def test_legacy_manifest_does_not_mislabel_special_events(self):
        added = row('しんき', '架空追加', 'added')
        corrected = dict(self.a, source_url='https://example.test/corrected')
        fix = self.change('fix', 'correct', self.a, corrected)
        annotation = self.change('annotation', 'annotate', None, None)
        annotation['related_ids'] = ['fix']
        annotation['metadata_before'] = {k: fix[k] for k in ledger.META}
        annotation['metadata_after'] = dict({k: fix[k] for k in ledger.META}, reason='架空の記録訂正')
        undo = self.change('undo', 'undo', corrected, self.a)
        undo['related_ids'] = ['fix']
        self.update([self.change('add', 'add', None, added), fix,
                     self.change('delete', 'delete', self.b, None), annotation, undo])
        self.write_master([self.a, added])
        _, manifest, _ = self.build()
        old, new = manifest['changes'][0], manifest['ledgerChanges'][0]
        self.assertEqual(old['added'], [added])
        self.assertEqual(old['removed'], [self.b])
        self.assertIn('再読み込み', old['title'])
        for group in ('added', 'corrected', 'removed', 'undone', 'annotations'):
            self.assertEqual(len(new[group]), 1)


    def test_upstream_audit_conditions_no_reapply_or_identity_guessing(self):
        corrected = dict(self.a, reading='しゅうせい')
        change = dict(self.change('fix', 'correct', self.a, corrected), _reconstructed=False, _update_id='u')
        known = {'fix': change}
        for candidates, expected in [([self.a], 'old_present'), ([corrected], 'already_corrected'),
                                      ([self.a, corrected], 'both_present'), ([], 'neither_present'),
                                      ([self.a, self.a], 'multiple_matches')]:
            saved = copy.deepcopy(candidates)
            result = ledger.upstream_audit(candidates, known)[0]
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['action'], 'manual_review')
            self.assertEqual(candidates, saved)
        for candidate in (dict(self.a, word='想定外'), dict(self.a, google_pos='名詞'),
                          dict(self.a, origin='added', added_on='2026-10-05', source_url='https://example.test')):
            result = ledger.upstream_audit([candidate], known)[0]
            self.assertEqual(result['status'], 'neither_present')
            self.assertEqual(result['suspicious'], [candidate])
        second = dict(self.change('fix2', 'correct', corrected, dict(corrected, reading='さいしゅう')), _reconstructed=False, _update_id='u2')
        report = ledger.upstream_audit([corrected], dict(known, fix2=second))
        self.assertEqual(report[1]['related_history'], ['fix'])
        deleted = dict(self.change('del', 'delete', self.a, None), _reconstructed=False, _update_id='d')
        self.assertEqual(ledger.upstream_audit([self.a], {'del': deleted})[0]['status'], 'old_present')

    def test_output_cannot_replace_repository_or_ancestor(self):
        self.build()
        before = self.tree(self.root)
        for destination in (self.root, self.root.parent, self.root / 'data'):
            with self.assertRaisesRegex(ValueError, 'separated output'):
                build_site.main(self.root, destination)
        self.assertEqual(before, self.tree(self.root))

    def test_cli_undo_and_annotation_workflow(self):
        corrected = dict(self.a, reading='せい')
        original = self.change('fix', 'correct', self.a, corrected)
        self.update([original])
        self.write_master(self.baseline)  # user explicitly edits master to reverse the change
        draft = self.root / 'undo.json'
        def cli(*args):
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/ledger.py'),
                                     '--root', str(self.root), *args], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        cli('draft', '--id', 'undo-update', '--applied-on', '2026-10-06', '--undo', 'fix',
            '--reason', '取消しの根拠', '--url', 'https://example.test/evidence',
            '--checked-on', '2026-10-06', '--output', str(draft))
        cli('confirm', str(draft))
        self.assertEqual(ledger.load(self.root)[0], self.baseline)
        annotation = self.root / 'annotation.json'
        cli('draft', '--id', 'note-update', '--applied-on', '2026-10-07', '--annotate', 'fix',
            '--reason', '理由の記録訂正', '--url', 'https://example.test/evidence',
            '--checked-on', '2026-10-07', '--output', str(annotation))
        value = json.loads(annotation.read_bytes())
        value['changes'][0]['metadata_after']['reason'] = '訂正された理由'
        annotation.write_bytes(ledger.canonical(value))
        cli('confirm', str(annotation))
        self.assertEqual(ledger.load(self.root)[2]['fix']['reason'], '訂正された理由')

    def test_application_date_cannot_precede_sealed_legacy_date(self):
        historical = self.change('historic', 'add', None, self.a)
        historical['legacy_added_on'] = '2026-10-05'
        first = ledger.seal(dict(id='historic-update', scope='reconstructed', status='final',
                                title='復元', applied_on=None, restoration={'sources': ['fixture']},
                                changes=[historical]), None)
        second = ledger.seal(dict(id='new-update', scope='applied', status='final', title='変更',
                                 applied_on='2026-10-04', restoration=None,
                                 changes=[self.change('del', 'delete', self.a, None)]), first['hash'])
        with self.assertRaisesRegex(ValueError, 'dates out of order'):
            ledger.replay(self.baseline, [first, second], self.upstreams)

    def test_upstream_date_fallback_is_explicit(self):
        _, manifest, files = self.build()
        self.assertEqual(manifest['dateMeaning'], 'upstream_date')
        self.assertEqual(json.loads(files['release.json'])['dateMeaning'], 'upstream_date')
        self.assertIsNone(json.loads(files['release.json'])['appliedThrough'])
        self.assertEqual(manifest['updatedOn'], '2026-09-03')

    def test_unknown_metadata_is_explicit_and_consistent(self):
        original = self.change('example', 'delete', self.a, None)
        for unknown in (['checked_on'], ['reason'], ['evidence'], ['bogus'], ['published_on', 'published_on']):
            with self.assertRaises(ValueError):
                ledger.validate_metadata(dict(original, unknown=unknown), False)
        missing = ledger.new_change('missing', 'delete', self.a, None, 'test', reason='資料不明')
        ledger.validate_metadata(missing, False)
        missing['unknown'] = []
        with self.assertRaisesRegex(ValueError, 'Unknown checked_on'):
            ledger.validate_metadata(missing, False)

    def test_every_upstream_reference_has_a_version_and_file_hashes(self):
        for bad in ({'test': {'files': {'source': 'f'*64}}},
                    {'test': {'version': 'test', 'files': {}}},
                    {'test': {'version': 'test', 'files': {'source': 'not-a-hash'}}}):
            with self.assertRaises(ValueError):
                ledger.validate_upstreams(bad)
        ledger.validate_upstreams(self.upstreams)


    def test_invalid_evidence_urls_fail_before_any_generated_write(self):
        self.build()
        build_site.main(self.root)
        before_dist, before_site = self.tree(self.root / 'dist'), self.tree(self.root / 'public')
        change = self.change('bad-evidence', 'delete', self.a, None)
        change['evidence']['urls'] = ['https://']
        self.update([change])
        self.write_master([self.b])
        for action in (lambda: build.main(self.root), lambda: build_site.main(self.root)):
            with self.assertRaisesRegex(ValueError, 'Invalid HTTP URL'):
                action()
        self.assertEqual(before_dist, self.tree(self.root / 'dist'))
        self.assertEqual(before_site, self.tree(self.root / 'public'))
        for url in ('http://', 'https://[', 'https://example.test:bad', 'https://exam ple.test',
                    'https://example.test/\n', 'https://%host', 'javascript:alert(1)'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'Invalid HTTP URL'):
                ledger.http_url(url)
        for url in ('https://example.test/evidence?q=%E6%A0%B9%E6%8B%A0', 'http://localhost:8788/',
                    'https://[::1]:443/evidence', 'https://例え.jp/根拠'):
            ledger.http_url(url)
        bad_added = dict(row(origin='added'), source_url='https://')
        with self.assertRaisesRegex(ValueError, 'Invalid HTTP URL'):
            ledger.validate_row(bad_added)

    def test_same_day_history_keeps_latest_ledger_updates_visible(self):
        current = self.a
        ids = ['z-first', 'y-second', 'x-third', 'w-fourth', 'v-fifth', 'a-latest']
        for i, uid in enumerate(ids):
            after = dict(current, reading=f'ていせい{i}')
            self.update([self.change(f'fix-{i}', 'correct', current, after)], uid=uid)
            current = after
        self.write_master([current, self.b])
        _, manifest, _ = self.build()
        self.assertEqual([c['id'] for c in manifest['ledgerChanges']], list(reversed(ids)))
        self.assertEqual(manifest['ledgerChanges'][0]['corrected'][0]['after'], current)
        self.assertIn('a-latest', [c['id'] for c in manifest['ledgerChanges'][:5]])

    def check_target_upstream_inheritance(self, kind):
        self.upstreams['next'] = dict(version='next-fixture', files={'next.tsv': 'e' * 64})
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical(self.upstreams))
        corrected = dict(self.a, reading='ていせい')
        original = self.change('original', 'correct', self.a, corrected)
        self.update([original])
        self.write_master([corrected, self.b])
        self.build()
        build_site.main(self.root)

        if kind == 'undo':
            change = self.change('follow-up', kind, corrected, self.a)
            final_rows = self.baseline
        else:
            change = self.change('follow-up', kind, None, None)
            change['metadata_before'] = {k: original[k] for k in ledger.META}
            change['metadata_after'] = dict(change['metadata_before'], reason='corrected fixture evidence')
            final_rows = [corrected, self.b]
        change['related_ids'] = [original['id']]
        change['upstream'] = 'next'  # Registered, but different from the target's original edition.
        value = dict(id='follow-up-update', scope='applied', status='draft', title='fixture follow-up',
                     applied_on='2026-10-06', restoration=None, changes=[change])
        draft = self.root / 'follow-up.json'
        draft.write_bytes(ledger.canonical(value))
        self.write_master(final_rows)
        before = self.tree(self.root)
        command = [sys.executable, '-B', str(ROOT / 'scripts/ledger.py'), '--root', str(self.root),
                   'confirm', str(draft)]
        refused = subprocess.run(command, capture_output=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(b'upstream must match target record', refused.stderr)
        self.assertEqual(before, self.tree(self.root))
        with self.assertRaisesRegex(ValueError, 'upstream must match target record'):
            ledger.append_draft(draft, self.root)
        self.assertEqual(before, self.tree(self.root))

        # A forged finalized record must also be refused by the shared generation path.
        ledger_path = self.root / 'data/ledger.jsonl'
        saved = ledger_path.read_bytes()
        finalized = ledger.seal(dict(value, status='final'), self.updates[-1]['hash'])
        ledger_path.write_bytes(saved + ledger.canonical(finalized) + b'\n')
        before_generated = self.tree(self.root)
        try:
            for action in (lambda: build.main(self.root), lambda: build_site.main(self.root)):
                with self.assertRaisesRegex(ValueError, 'upstream must match target record'):
                    action()
            self.assertEqual(before_generated, self.tree(self.root))
        finally:
            ledger_path.write_bytes(saved)

        # The legitimate same-edition operation remains confirmable with multiple editions registered.
        change['upstream'] = original['upstream']
        draft.write_bytes(ledger.canonical(value))
        accepted = subprocess.run(command, capture_output=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        rows, _, known, _ = ledger.load(self.root)
        self.assertEqual({ledger.key(r): r for r in rows}, {ledger.key(r): r for r in final_rows})
        self.assertEqual(known['follow-up']['upstream'], original['upstream'])
        _, manifest, _ = self.build()
        bucket = 'undone' if kind == 'undo' else 'annotations'
        self.assertEqual(manifest['ledgerChanges'][0][bucket][0]['id'], change['id'])

    def test_undo_requires_target_upstream_at_confirmation_and_generation(self):
        self.check_target_upstream_inheritance('undo')

    def test_annotation_requires_target_upstream_at_confirmation_and_generation(self):
        self.check_target_upstream_inheritance('annotate')


    def test_cli_requires_upstream_selection_after_catalog_grows(self):
        self.upstreams['next'] = dict(version='next-fixture', files={'next.tsv': 'e'*64})
        (self.root / 'data/upstreams.json').write_bytes(ledger.canonical(self.upstreams))
        added = row('げんばんしんき', '架空新原版')
        self.write_master(self.baseline + [added])
        draft = self.root / 'next-draft.json'
        command = [sys.executable, str(ROOT / 'scripts/ledger.py'), '--root', str(self.root), 'draft',
                   '--id', 'next-update', '--applied-on', '2026-10-06', '--reason', '新版根拠',
                   '--url', 'https://example.test/next', '--checked-on', '2026-10-06', '--output', str(draft)]
        missing = subprocess.run(command, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn(b'specify --upstream', missing.stderr)
        self.assertFalse(draft.exists())
        unknown = subprocess.run(command + ['--upstream', 'unknown'], capture_output=True)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertFalse(draft.exists())
        selected = subprocess.run(command + ['--upstream', 'next'], capture_output=True)
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(json.loads(draft.read_bytes())['changes'][0]['upstream'], 'next')
        ledger.append_draft(draft, self.root)
        self.assertEqual(ledger.load(self.root)[2]['next-update-0001']['upstream'], 'next')


class HeadSeals(unittest.TestCase):
    def test_recomputed_chain_cannot_rewrite_local_head_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'data/ledger.jsonl').write_text('{"id":"changed"}\n', encoding='utf-8')
            def command(args, **kwargs):
                if args[-1] == 'HEAD:data/ledger.jsonl':
                    return subprocess.CompletedProcess(args, 0, b'{"id":"final"}\n', b'')
                return subprocess.CompletedProcess(args, 128, b'', b'not tracked')
            with patch.object(ledger.subprocess, 'run', side_effect=command):
                with self.assertRaisesRegex(ValueError, 'Finalized records modified'):
                    ledger.git_seals(root)


if __name__ == '__main__':
    unittest.main()
