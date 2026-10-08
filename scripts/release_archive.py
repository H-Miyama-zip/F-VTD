"""Preserve verified published artifacts outside the site's current download tree."""
from collections import Counter
from contextlib import contextmanager
import io
import json
import re
import tempfile
import zipfile
from pathlib import Path

import build
import ledger


def version_name(value):
    ledger.require(isinstance(value, str) and re.fullmatch(r'\d{8}-[0-9a-f]{16}', value),
                   'Invalid archive version')
    return value


def names(version):
    return ('release.json', f'F-VTD-{version_name(version)}.zip')


def destination(root, version):
    return Path(root) / 'releases' / version_name(version)


def validate_artifacts(root, release_bytes, zip_bytes):
    """Validate the recorded historical snapshot, independently of today's master/code."""
    descriptor = json.loads(release_bytes)
    ledger.require(set(descriptor) == {'version', 'count', 'updatedOn', 'appliedThrough',
                                      'dateMeaning', 'updateIds', 'changeIds', 'hashes', 'inputs'},
                   'Invalid release descriptor')
    version = version_name(descriptor['version'])
    ledger.date(descriptor['updatedOn'])
    hashes, inputs = descriptor['hashes'], descriptor['inputs']
    ledger.require(isinstance(hashes, dict) and set(hashes) == set(build.DIST_FILES + ['search.json']),
                   'Invalid release hashes')
    ledger.require(isinstance(inputs, dict), 'Invalid release inputs')
    for value in hashes.values():
        ledger.require(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value), 'Invalid release SHA-256')
    ledger.require(all(inputs.get(name) == value for name, value in hashes.items()), 'Release output inputs disagree')
    expected_version = descriptor['updatedOn'].replace('-', '') + '-' + ledger.digest(ledger.canonical(inputs))[:16]
    ledger.require(version == expected_version, 'Release version/input mismatch')

    _, updates, _, info = ledger.load(root, match_master=False)
    ids = descriptor['updateIds']
    ledger.require(isinstance(ids, list) and len(ids) >= info['reconstructed_updates'] and
                   ids == [u['id'] for u in updates[:len(ids)]], 'Release update IDs must be a finalized ledger prefix')
    prefix = updates[:len(ids)]
    ledger.require(descriptor['changeIds'] == [c['id'] for u in prefix for c in u['changes']],
                   'Release change IDs mismatch')
    ledger.require(inputs.get('ledger') == ledger.digest(ledger.canonical(prefix)) and
                   inputs.get('baseline') == ledger.digest(ledger.canonical(info)), 'Release history inputs mismatch')
    catalog = json.loads((Path(root) / 'data/upstreams.json').read_text(encoding='utf-8'))
    historic, _ = ledger.replay(ledger.read_rows(Path(root) / 'data/baseline/master.tsv'), prefix, catalog)
    ledger.require(type(descriptor['count']) is int and descriptor['count'] == len(historic), 'Release count mismatch')
    dates = [u['applied_on'] for u in prefix if u['scope'] == 'applied']
    legacy = [c['legacy_added_on'] for u in prefix for c in u['changes'] if c['legacy_added_on']]
    latest = max(dates or legacy or [info['upstream_date']])
    meaning = 'applied_on' if dates else ('legacy_added_on' if legacy else 'upstream_date')
    ledger.require(descriptor['updatedOn'] == latest and descriptor['dateMeaning'] == meaning and
                   descriptor['appliedThrough'] == (max(dates) if dates else None), 'Release dates mismatch')

    folder = f'F-VTD-{version}/'
    expected = set(build.DIST_FILES + ['README.txt', 'NOTICE.md', 'release.json'])
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            ledger.require(len(archive.namelist()) == len(expected) and
                           set(archive.namelist()) == {folder + n for n in expected}, 'Unexpected ZIP members')
            ledger.require(archive.testzip() is None, 'Invalid ZIP checksum')
            entries = {name: archive.read(folder + name) for name in expected}
    except zipfile.BadZipFile as error:
        raise ValueError('Invalid release ZIP') from error
    ledger.require(entries['release.json'] == release_bytes, 'ZIP release descriptor mismatch')
    for name in build.DIST_FILES:
        ledger.require(ledger.digest(entries[name]) == hashes[name], 'ZIP dictionary hash mismatch: ' + name)
    ledger.require(ledger.digest(entries['NOTICE.md']) == inputs.get('NOTICE.md'), 'ZIP NOTICE input mismatch')

    # The archived Google TSV preserves the actual master order at release time.
    # Order alone is deliberately absent from ledger events, so do not use today's order.
    google = entries[build.DIST_FILES[0]]
    ledger.require(google.startswith(b'\xff\xfe'), 'Missing archived TSV BOM')
    registrations = [line.split('\t') for line in google[2:].decode('utf-16le').splitlines() if not line.startswith('!')]
    ledger.require(all(len(r) == 3 for r in registrations), 'Invalid archived TSV row')
    other = []
    for name in build.DIST_FILES[1:3]:
        data = entries[name]
        ledger.require(data.startswith(b'\xff\xfe'), 'Missing archived TSV BOM')
        other.append([line.split('\t') for line in data[2:].decode('utf-16le').splitlines() if not line.startswith('!')])
    ledger.require(all(len(rows) == len(registrations) and all(len(r) == 3 for r in rows) for rows in other),
                   'Archived TSV counts disagree')
    keys = []
    for google_row, microsoft, atok in zip(registrations, *other):
        ledger.require(google_row[:2] == microsoft[:2] == atok[:2], 'Archived TSV order mismatch')
        keys.append(tuple(google_row + [microsoft[2], atok[2]]))
    state = {ledger.key(row): row for row in historic}
    ledger.require(Counter(keys) == Counter(state.keys()), 'Archived dictionary differs from historical ledger')
    ordered = [state[key] for key in keys]
    build.validate_outputs(ordered, {n: entries[n] for n in build.DIST_FILES})
    search = ledger.canonical([{'reading': r['reading'], 'word': r['word'], 'excluded': []} for r in ordered])
    ledger.require(ledger.digest(search) == hashes['search.json'], 'Archived search hash mismatch')
    return descriptor


def read(root, record):
    folder = destination(root, record['version'])
    expected = names(record['version'])
    ledger.require(folder.is_dir() and {p.name for p in folder.iterdir()} == set(expected),
                   'Missing/incomplete published archive: ' + record['version'])
    release_bytes, zip_bytes = [(folder / n).read_bytes() for n in expected]
    ledger.require(ledger.digest(release_bytes) == record['release_sha256'] and
                   ledger.digest(zip_bytes) == record['zip_sha256'], 'Published archive hash mismatch: ' + record['version'])
    descriptor = json.loads(release_bytes)
    ledger.require(descriptor['version'] == record['version'] and descriptor['updateIds'] == record['update_ids'] and
                   descriptor['changeIds'] == record['change_ids'], 'Published archive IDs mismatch')
    return release_bytes, zip_bytes


def verify(root, records):
    for record in records:
        read(root, record)


def remove_created(folder, filenames):
    # Only remove the two files this operation created; never delete a tree recursively.
    for name in filenames:
        (folder / name).unlink(missing_ok=True)
    folder.rmdir()


@contextmanager
def preserve(root, descriptor, release_bytes, zip_bytes):
    """Save exact bytes; roll back a newly created archive if confirmation fails."""
    target = destination(root, descriptor['version'])
    filenames = names(descriptor['version'])
    if target.exists():
        ledger.require(target.is_dir() and {p.name for p in target.iterdir()} == set(filenames) and
                       all((target / name).read_bytes() == data for name, data in zip(filenames, (release_bytes, zip_bytes))),
                       'Refusing to overwrite an existing archive')
        yield target
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.release-stage-', dir=target.parent))
    created = False
    try:
        for name, data in zip(filenames, (release_bytes, zip_bytes)):
            (staging / name).write_bytes(data)
        ledger.require(not target.exists(), 'Archive appeared during confirmation; retry after inspection')
        staging.rename(target)
        created = True
        try:
            yield target
        except BaseException:
            remove_created(target, filenames)
            created = False
            raise
    finally:
        if not created and staging.exists():
            remove_created(staging, filenames)
