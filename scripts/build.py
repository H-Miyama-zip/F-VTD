"""Build IME dictionaries and the additions record from the UTF-8 master (Python standard library only)."""
import csv
import datetime
import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with (ROOT / 'data/master.tsv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    seen = set()
    for number, row in enumerate(rows, 2):
        for key in ('reading', 'word', 'google_pos', 'microsoft_pos', 'atok_pos'):
            value = row[key]
            if not value or value != value.strip() or any(c in value for c in '\t\r\n'):
                raise ValueError(f'Invalid {key} at line {number}')
        pair = (row['reading'], row['word'])
        if pair in seen:
            raise ValueError(f'Duplicate pair at line {number}: {pair}')
        seen.add(pair)
        if row['origin'] == 'added':
            datetime.date.fromisoformat(row['added_on'])
            if not row['source_url'].startswith(('https://', 'http://')):
                raise ValueError(f'Added row without source_url at line {number}')
        elif row['origin'] != 'upstream' or row['added_on']:
            raise ValueError(f'Invalid origin/added_on at line {number}')
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    for name, pos in [('Google日本語入力', 'google_pos'), ('MicrosoftIME', 'microsoft_pos'), ('ATOK', 'atok_pos')]:
        lines = []
        if name == 'MicrosoftIME':
            lines = ['!Microsoft IME Dictionary Tool', '!Format:WORDLIST', '!F-VTD VTuber変換辞書', '!利用条件・変更内容はリポジトリのREADME.mdとNOTICE.mdを参照']
        lines += ['\t'.join((r['reading'], r['word'], r[pos])) for r in rows]
        (out / f'VTuber変換辞書_{name}.tsv').write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-16'))
    data = [{'phrase': r['word'], 'shortcut': r['reading']} for r in rows]
    (out / 'VTuber変換辞書_macOS.plist').write_bytes(plistlib.dumps(data, sort_keys=False))
    # Read back every output to check encoding, fields and cross-format parity.
    for name, pos in [('Google日本語入力', 'google_pos'), ('MicrosoftIME', 'microsoft_pos'), ('ATOK', 'atok_pos')]:
        actual = [line.split('\t') for line in (out / f'VTuber変換辞書_{name}.tsv').read_text(encoding='utf-16').splitlines() if not line.startswith('!')]
        assert actual == [[r['reading'], r['word'], r[pos]] for r in rows]
    assert plistlib.loads((out / 'VTuber変換辞書_macOS.plist').read_bytes()) == data
    # data/additions.tsv is a generated, human-readable record of what F-VTD added to the upstream data.
    columns = ['added_on', 'reading', 'word', 'source_url', 'note']
    added = sorted((r for r in rows if r['origin'] == 'added'), key=lambda r: r['added_on'])
    lines = ['\t'.join(columns)] + ['\t'.join(r[c] for c in columns) for r in added]
    (ROOT / 'data/additions.tsv').write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
    print(f'Validated and generated {len(rows)} entries in 4 formats ({len(added)} added).')


if __name__ == '__main__':
    main()
