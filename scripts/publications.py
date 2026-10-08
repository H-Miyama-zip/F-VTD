"""Record a release only after a human separately verifies publication.

This command neither deploys nor contacts the public site. Builds never append.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

import ledger
import release_archive

ROOT = Path(__file__).resolve().parents[1]


def validate(records):
    ids, versions = set(), set()
    for record in records:
        ledger.require(set(record) == {'id', 'version', 'published_at', 'url', 'evidence',
                                      'update_ids', 'change_ids', 'release_sha256', 'zip_sha256'}, 'Invalid publication fields')
        ledger.require(record['id'] and record['id'] not in ids and record['version'] not in versions, 'Duplicate publication ID/version')
        ids.add(record['id'])
        versions.add(record['version'])
        release_archive.version_name(record['version'])
        stamp = dt.datetime.fromisoformat(record['published_at'])
        ledger.require(stamp.tzinfo is not None, 'Publication timestamp must include timezone')
        ledger.http_url(record['url'])
        ledger.require(bool(record['evidence'].strip()), 'Publication confirmation requires evidence')
        for field in ('release_sha256', 'zip_sha256'):
            ledger.require(isinstance(record[field], str) and len(record[field]) == 64 and all(c in '0123456789abcdef' for c in record[field]), 'Invalid publication hash')
        for field in ('update_ids', 'change_ids'):
            ledger.require(isinstance(record[field], list) and all(isinstance(x, str) and x for x in record[field]) and len(set(record[field])) == len(record[field]), 'Invalid publication IDs')
    return records


def load(root=ROOT):
    return validate(ledger.jsonl(Path(root) / 'data/publications.jsonl'))


def append(record, root=ROOT, *, release_bytes=None, zip_bytes=None):
    root = Path(root)
    _, updates, _, _ = ledger.load(root, match_master=False)
    ledger.require(set(record['update_ids']) <= {u['id'] for u in updates}, 'Unknown published update IDs')
    expected = [c['id'] for u in updates if u['id'] in record['update_ids'] for c in u['changes']]
    ledger.require(record['change_ids'] == expected, 'Published update/change IDs disagree')
    existing = load(root)
    records = validate(existing + [record])
    release_archive.verify(root, existing)
    ledger.require(release_bytes is not None and zip_bytes is not None, 'Publication confirmation requires exact release/ZIP artifacts')
    descriptor = release_archive.validate_artifacts(root, release_bytes, zip_bytes)
    ledger.require(record['version'] == descriptor['version'] and record['update_ids'] == descriptor['updateIds'] and
                   record['change_ids'] == descriptor['changeIds'], 'Publication artifact IDs mismatch')
    ledger.require(record['release_sha256'] == ledger.digest(release_bytes) and
                   record['zip_sha256'] == ledger.digest(zip_bytes), 'Publication artifact hashes mismatch')
    path = root / 'data/publications.jsonl'
    temp = path.with_suffix('.jsonl.tmp')
    try:
        with release_archive.preserve(root, descriptor, release_bytes, zip_bytes):
            temp.write_bytes(b''.join(ledger.canonical(r) + b'\n' for r in records))
            temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--release', type=Path, required=True, help='Generated release.json actually published')
    parser.add_argument('--zip', type=Path, required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--published-at', required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--evidence', required=True, help='How this exact release and its hashes were verified publicly')
    args = parser.parse_args()
    release_bytes, zip_bytes = args.release.read_bytes(), args.zip.read_bytes()
    release = json.loads(release_bytes)
    version = release['version']
    record = dict(id=args.id, version=version, published_at=args.published_at, url=args.url,
                  evidence=args.evidence, update_ids=release['updateIds'], change_ids=release['changeIds'],
                  release_sha256=ledger.digest(release_bytes), zip_sha256=ledger.digest(zip_bytes))
    append(record, args.root, release_bytes=release_bytes, zip_bytes=zip_bytes)
    print('Recorded separate publication confirmation and preserved:', version)


if __name__ == '__main__':
    main()
