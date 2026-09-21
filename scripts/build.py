"""Build IME dictionaries from the UTF-8 master (Python standard library only)."""
import csv
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
    out = ROOT / 'dist'
    out.mkdir(exist_ok=True)
    for name, pos in [('Google日本語入力', 'google_pos'), ('MicrosoftIME', 'microsoft_pos'), ('ATOK', 'atok_pos')]:
        lines = []
        if name == 'MicrosoftIME':
            lines = ['!Microsoft IME Dictionary Tool', '!Format:WORDLIST', '!VTuber変換辞書 福井関連追加版', '!利用条件・変更内容はリポジトリのREADME.mdとNOTICE.mdを参照']
        lines += ['\t'.join((r['reading'], r['word'], r[pos])) for r in rows]
        (out / f'VTuber変換辞書_{name}.tsv').write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-16'))
    data = [{'phrase': r['word'], 'shortcut': r['reading']} for r in rows]
    (out / 'VTuber変換辞書_macOS.plist').write_bytes(plistlib.dumps(data, sort_keys=False))
    # Read back every output to check encoding, fields and cross-format parity.
    for name, pos in [('Google日本語入力', 'google_pos'), ('MicrosoftIME', 'microsoft_pos'), ('ATOK', 'atok_pos')]:
        actual = [line.split('\t') for line in (out / f'VTuber変換辞書_{name}.tsv').read_text(encoding='utf-16').splitlines() if not line.startswith('!')]
        assert actual == [[r['reading'], r['word'], r[pos]] for r in rows]
    assert plistlib.loads((out / 'VTuber変換辞書_macOS.plist').read_bytes()) == data
    print(f'Validated and generated {len(rows)} entries in 4 formats.')


if __name__ == '__main__':
    main()
