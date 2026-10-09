"""One-time, fail-closed migration of the audited public history; never use in builds."""
import argparse
import csv
import io
import json
import subprocess
from pathlib import Path

import ledger

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root)


def table(data):
    return list(csv.DictReader(io.StringIO(data.decode('utf-8')), delimiter='\t'))


def migrate(root=ROOT, ref='365960717ee6bb06e8f270461fa524f8ca3bd464'):
    root = Path(root)
    sha = git(root, 'rev-parse', ref).decode().strip()
    targets = [root / 'data/baseline', root / 'data/ledger.jsonl', root / 'data/upstreams.json', root / 'data/publications.jsonl']
    ledger.require(not any(p.exists() for p in targets), 'Migration already exists; do not overwrite finalized history')
    master_bytes = git(root, 'show', sha + ':data/master.tsv')
    ledger.require((root / 'data/master.tsv').read_bytes() == master_bytes, 'Working master must equal audited public commit')
    rows = table(master_bytes)
    additions_bytes = git(root, 'show', sha + ':data/additions.tsv')
    additions = table(additions_bytes)
    columns = ('added_on', 'reading', 'word', 'source_url', 'note')
    ledger.require(sorted(tuple(r[c] for c in columns) for r in additions) ==
                   sorted(tuple(r[c] for c in columns) for r in rows if r['origin'] == 'added'),
                   'Legacy additions information would be lost')
    commits = git(root, 'log', '--reverse', '--format=%H', sha, '--', 'data/master.tsv').decode().splitlines()
    snapshots = []
    comparable = lambda r: tuple(r[c] for c in ledger.COLUMNS if c != 'added_on')
    reference = {comparable(r) for r in rows}
    for commit in commits:
        content = git(root, 'show', commit + ':data/master.tsv')
        records = table(content)
        # Audited history has one initial import and only an added_on schema change.
        # Stop for any other content change instead of silently omitting old deletions.
        ledger.require(len(records) == len(rows) and {comparable(r) for r in records} == reference,
                       'Unexpected historic content change: review explicit before/after migration')
        snapshots.append(dict(commit=commit, committed_at=git(root, 'show', '-s', '--format=%cI', commit).decode().strip(),
                              sha256=ledger.digest(content), count=len(records), columns=list(records[0])))
    upstream_hashes = json.loads(git(root, 'show', sha + ':docs/upstream-hashes.json'))
    upstream = dict(version='ver1_2', released_on='2026-09-03', files=upstream_hashes,
                    provenance='docs/upstream-hashes.json at ' + sha)
    update = dict(id='reconstructed-' + commits[0][:12], scope='reconstructed', status='final',
                  title='導入前の追加記録を復元（追加日 2026-09-21）', applied_on=None,
                  restoration=dict(sources=[{'kind': 'git', 'commit': sha, 'path': 'data/master.tsv'},
                                            {'kind': 'git', 'commit': sha, 'path': 'data/additions.tsv'},
                                            {'kind': 'git', 'commit': commits[0], 'path': 'data/master.tsv'}],
                                   snapshots=snapshots, unknown=['applied_on', 'checked_on', 'published_on', 'pre_import_history']),
                  changes=[])
    for i, row in enumerate((r for r in rows if r['origin'] == 'added'), 1):
        change = ledger.new_change(f'{update["id"]}-{i:04}', 'add', None, row, 'ver1_2', urls=[row['source_url']])
        change['legacy_added_on'] = row['added_on']
        change['unknown'] += ['applied_on', 'published_on', 'pre_import_history']
        change['evidence']['text'] = row['note'] or None
        update['changes'].append(change)
    update = ledger.seal(update, None)
    info = dict(schema=1, git_commit=sha, count=len(rows), sha256=ledger.digest(master_bytes),
                upstream='ver1_2', upstream_date='2026-09-03', upstream_sha256=ledger.digest(ledger.canonical(upstream)),
                reconstructed_updates=1, reconstructed_head=update['hash'],
                migration=dict(additions_sha256=ledger.digest(additions_bytes), additions_count=len(additions),
                               restored_additions=len(update['changes']), replacement_corrections=0, deletions=0,
                               note='Initial master import has no earlier tracked state; Git commit dates are not application or publication dates.'))
    ledger.replay(rows, [update], {'ver1_2': upstream})
    (root / 'data/baseline').mkdir()
    (root / 'data/baseline/master.tsv').write_bytes(master_bytes)
    (root / 'data/baseline/info.json').write_bytes(ledger.canonical(info) + b'\n')
    (root / 'data/upstreams.json').write_bytes(ledger.canonical({'ver1_2': upstream}) + b'\n')
    (root / 'data/ledger.jsonl').write_bytes(ledger.canonical(update) + b'\n')
    (root / 'data/publications.jsonl').write_bytes(b'')
    ledger.load(root)
    print(f'Migrated {len(additions)} additions; {len(rows)} baseline entries; {len(snapshots)} master snapshots audited.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--ref', default='365960717ee6bb06e8f270461fa524f8ca3bd464')
    args = parser.parse_args()
    migrate(args.root, args.ref)
