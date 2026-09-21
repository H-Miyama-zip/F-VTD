"""Build the static website into public/ (Python standard library only).

Cloudflare Pages runs this on every push: build command `python scripts/build_site.py`,
output directory `public`.
"""
import csv
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build  # noqa: E402

PUBLIC = ROOT / 'public'
DIST_FILES = ['VTuber変換辞書_MicrosoftIME.tsv', 'VTuber変換辞書_Google日本語入力.tsv', 'VTuber変換辞書_ATOK.tsv', 'VTuber変換辞書_macOS.plist']
GENERATED = [ROOT / 'dist' / name for name in DIST_FILES] + [ROOT / 'data/additions.tsv']
ZIP_README = ROOT / 'package/README.txt'


def read_tsv(path):
    with path.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def regenerate_dist():
    """Regenerate dist/ and additions.tsv, and fail if the committed files were stale."""
    before = {path: path.read_bytes() for path in GENERATED}
    build.main()
    stale = [path.name for path in GENERATED if path.read_bytes() != before[path]]
    if stale:
        raise SystemExit('生成ファイルが master.tsv と一致しません。python scripts/build.py を実行してコミットしてください: ' + ', '.join(stale))


def read_changes(rows):
    """Group the rows F-VTD added by their added_on date, newest first."""
    changes = {}
    for r in rows:
        if r['origin'] == 'added':
            entry = changes.setdefault(r['added_on'], {'date': r['added_on'], 'title': '辞書を更新', 'added': [], 'removed': []})
            entry['added'].append({'reading': r['reading'], 'word': r['word']})
    return sorted(changes.values(), key=lambda c: c['date'], reverse=True)


def write_zip(path, folder, readme):
    entries = [(name, (ROOT / 'dist' / name).read_bytes()) for name in DIST_FILES]
    entries += [('README.txt', readme.encode('utf-8-sig')), ('NOTICE.md', (ROOT / 'NOTICE.md').read_bytes())]
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(f'{folder}/{name}', date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)


def main():
    regenerate_dist()
    rows = read_tsv(ROOT / 'data/master.tsv')
    changes = read_changes(rows)
    latest = changes[0]['date'] if changes else '2026-09-03'
    # Hash everything that goes into the published files, so any change gets new URLs (they are cached as immutable).
    sha = hashlib.sha256()
    for path in [ROOT / 'data/master.tsv', ZIP_README, ROOT / 'NOTICE.md']:
        sha.update(path.read_bytes())
    digest = sha.hexdigest()[:8]
    version = latest.replace('-', '') + '-' + digest

    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    shutil.copytree(ROOT / 'site', PUBLIC)
    files = PUBLIC / 'files' / version
    files.mkdir(parents=True)

    search = [{'reading': r['reading'], 'word': r['word'], 'excluded': []} for r in rows]
    (files / 'search.json').write_text(json.dumps(search, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    zip_name = f'F-VTD-{version}.zip'
    readme = ZIP_README.read_text(encoding='utf-8').replace('{version}', version).replace('{count}', f'{len(rows):,}')
    write_zip(files / zip_name, f'F-VTD-{version}', readme)

    manifest = {
        'version': version,
        'updatedAt': latest + 'T00:00:00+09:00',
        'count': len(rows),
        'data': f'../files/{version}/search.json',
        'zip': f'../files/{version}/{zip_name}',
        'changes': changes,
    }
    (PUBLIC / 'data').mkdir(exist_ok=True)
    (PUBLIC / 'data/latest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'Built site {version}: {len(rows)} entries, {len(changes)} changelog dates -> {PUBLIC.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
