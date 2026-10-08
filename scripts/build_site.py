"""Stage and validate a deterministic static site without changing dist or ledger."""
import argparse
import io
import json
import shutil
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path
import build
import ledger
import publications

ROOT = Path(__file__).resolve().parents[1]


def read_changes(updates, known):
    changes = []
    for update in updates:
        legacy_dates = [c['legacy_added_on'] for c in update['changes'] if c['legacy_added_on']]
        day = update['applied_on'] or (max(legacy_dates) if legacy_dates else None)
        entry = dict(id=update['id'], date=day, dateMeaning='applied_on' if update['applied_on'] else 'legacy_added_on',
                     appliedOn=update['applied_on'], scope=update['scope'], title=update['title'],
                     added=[], corrected=[], removed=[], undone=[], annotations=[])
        for original in update['changes']:
            c = known[original['id']]
            item = {k: c[k] for k in ('id', 'before', 'after', 'related_ids', 'reason', 'evidence', 'checked_on', 'unknown', 'metadata_before', 'metadata_after')}
            group = {'add': 'added', 'correct': 'corrected', 'delete': 'removed', 'undo': 'undone', 'annotate': 'annotations'}[c['kind']]
            entry[group].append(item)
        changes.append(entry)
    # Same-day updates follow reverse ledger order, independently of their IDs.
    return sorted(reversed(changes), key=lambda c: c['date'] or '', reverse=True)


def source_bytes(path):
    return path.read_bytes().replace(b'\r\n', b'\n')


def prepare_release(root=ROOT):
    root = Path(root)
    rows, updates, known, info = ledger.load(root)
    outputs = build.render(rows)
    build.check_dist(root, outputs)
    search = ledger.canonical([{'reading': r['reading'], 'word': r['word'], 'excluded': []} for r in rows])
    hashes = {name: ledger.digest(data) for name, data in outputs.items()}
    hashes['search.json'] = ledger.digest(search)
    # Version-neutral inputs only: no versioned ZIP/README/release hashes feed back.
    inputs = dict(hashes)
    for name in ('package/README.txt', 'NOTICE.md', 'scripts/build.py', 'scripts/build_site.py',
                  'scripts/ledger.py', 'scripts/publications.py', 'scripts/release_archive.py'):
        inputs[name] = ledger.digest(source_bytes(root / name))
    inputs['ledger'] = ledger.digest(ledger.canonical(updates))
    inputs['baseline'] = ledger.digest(ledger.canonical(info))
    inputs['upstreams'] = ledger.digest(ledger.canonical(json.loads((root / 'data/upstreams.json').read_text(encoding='utf-8'))))
    inputs['zip_runtime'] = dict(zlib=zlib.ZLIB_VERSION, zlibRuntime=zlib.ZLIB_RUNTIME_VERSION,
                                python=sys.implementation.name, pythonVersion=list(sys.version_info[:3]))
    dates = [u['applied_on'] for u in updates if u['scope'] == 'applied']
    legacy = [c['legacy_added_on'] for u in updates for c in u['changes'] if c['legacy_added_on']]
    latest = max(dates or legacy or [info['upstream_date']])
    date_meaning = 'applied_on' if dates else ('legacy_added_on' if legacy else 'upstream_date')
    version = latest.replace('-', '') + '-' + ledger.digest(ledger.canonical(inputs))[:16]
    descriptor = dict(version=version, count=len(rows), updatedOn=latest, appliedThrough=max(dates) if dates else None,
                      dateMeaning=date_meaning,
                      updateIds=[u['id'] for u in updates], changeIds=[c['id'] for u in updates for c in u['changes']],
                      hashes=hashes, inputs=inputs)
    release_bytes = ledger.canonical(descriptor) + b'\n'
    readme = source_bytes(root / 'package/README.txt').decode('utf-8').replace('{version}', version).replace('{count}', f'{len(rows):,}')
    entries = dict(outputs, **{'README.txt': readme.encode('utf-8-sig'),
                               'NOTICE.md': source_bytes(root / 'NOTICE.md'), 'release.json': release_bytes})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            item = zipfile.ZipInfo(f'F-VTD-{version}/{name}', date_time=(2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data)
    zip_bytes = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        ledger.require(archive.testzip() is None, 'Invalid ZIP')
        ledger.require(all(archive.read(f'F-VTD-{version}/{n}') == v for n, v in entries.items()), 'ZIP parity mismatch')
    ledger.require(json.loads(search) == [{'reading': r['reading'], 'word': r['word'], 'excluded': []} for r in rows], 'Search parity mismatch')
    published = publications.load(root)
    publications.release_archive.verify(root, published)
    known_updates = {u['id'] for u in updates}
    for record in published:
        ledger.require(set(record['update_ids']) <= known_updates, 'Publication refers to unknown update')
        expected = [c['id'] for u in updates if u['id'] in record['update_ids'] for c in u['changes']]
        ledger.require(record['change_ids'] == expected, 'Publication change IDs mismatch')
    current = next((r for r in published if r['version'] == version), None)
    if current:
        ledger.require(current['release_sha256'] == ledger.digest(release_bytes) and current['zip_sha256'] == ledger.digest(zip_bytes), 'Publication does not match generated release')
        ledger.require(current['update_ids'] == descriptor['updateIds'] and current['change_ids'] == descriptor['changeIds'], 'Publication IDs mismatch')
    changes = read_changes(updates, known)
    for history_update in changes:
        releases = [r['version'] for r in published if history_update['id'] in r['update_ids']]
        history_update['publishedIn'] = releases
        history_update['publicationStatus'] = 'confirmed' if releases else 'unconfirmed'
        for group in ('added', 'corrected', 'removed', 'undone', 'annotations'):
            for change in history_update[group]:
                change['publishedIn'] = [r['version'] for r in published if change['id'] in r['change_ids']]
    manifest = dict(version=version, updatedOn=latest, updatedAt=latest + 'T00:00:00+09:00',
                    dateMeaning=descriptor['dateMeaning'], count=len(rows),
                    data=f'../files/{version}/search.json', zip=f'../files/{version}/F-VTD-{version}.zip',
                    release=f'../files/{version}/release.json', updateIds=descriptor['updateIds'],
                    changeIds=descriptor['changeIds'], hashes=dict(hashes, zip=ledger.digest(zip_bytes)),
                    publicationStatus='confirmed' if current else 'unconfirmed',
                    publishedAt=current['published_at'] if current else None, changes=changes)
    return version, manifest, dict(outputs, **{'search.json': search, 'release.json': release_bytes,
                                              f'F-VTD-{version}.zip': zip_bytes})


def main(root=ROOT, output=None, check=False):
    root = Path(root).resolve()
    version, manifest, files = prepare_release(root)
    if check:
        print(f'Site preflight valid: {version}; publication {manifest["publicationStatus"]}')
        return
    public = Path(output).resolve() if output else root / 'public'
    ledger.require(public != root and public not in root.parents and ((root not in public.parents) or public == root / 'public'),
                   'Use default public/ or a separated output directory')
    ledger.require(not public.exists() or (public / '.fvtd-generated').is_file(), 'Refusing to replace an unmarked output directory; use a fresh --output')
    public.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.fvtd-stage-', dir=public.parent))
    backup = public.with_name(public.name + '.previous')
    try:
        shutil.copytree(root / 'site', staging, dirs_exist_ok=True)
        target = staging / 'files' / version
        target.mkdir(parents=True)
        for name, data in files.items():
            (target / name).write_bytes(data)
        (staging / 'data').mkdir(exist_ok=True)
        (staging / 'data/latest.json').write_bytes(ledger.canonical(manifest) + b'\n')
        (staging / '.fvtd-generated').write_text('F-VTD generated site\n', encoding='utf-8')
        ledger.require(not backup.exists(), 'Output backup already exists; inspect it before retrying')
        if public.exists():
            public.rename(backup)
        try:
            staging.rename(public)
        except OSError:
            if backup.exists():
                backup.rename(public)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(f'Built {version}: {manifest["count"]} entries -> {public}; publication {manifest["publicationStatus"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true', help='Read-only preflight')
    args = parser.parse_args()
    main(args.root, args.output, args.check)
