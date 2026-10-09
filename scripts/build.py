"""Validate the ledger, then build four current dictionaries (standard library)."""
import argparse
import plistlib
from pathlib import Path
import ledger

ROOT = Path(__file__).resolve().parents[1]
FORMATS = [('Google日本語入力', 'google_pos'), ('MicrosoftIME', 'microsoft_pos'), ('ATOK', 'atok_pos')]
DIST_FILES = [f'VTuber変換辞書_{name}.tsv' for name, _ in FORMATS] + ['VTuber変換辞書_macOS.plist']


def render(rows):
    outputs = {}
    for name, pos in FORMATS:
        lines = []
        if name == 'MicrosoftIME':
            lines = ['!Microsoft IME Dictionary Tool', '!Format:WORDLIST', '!F-VTD VTuber変換辞書',
                     '!利用条件・変更内容はリポジトリのREADME.mdとNOTICE.mdを参照']
        lines += ['\t'.join((r['reading'], r['word'], r[pos])) for r in rows]
        outputs[f'VTuber変換辞書_{name}.tsv'] = b'\xff\xfe' + ('\r\n'.join(lines) + '\r\n').encode('utf-16le')
    outputs['VTuber変換辞書_macOS.plist'] = plistlib.dumps(
        [{'phrase': r['word'], 'shortcut': r['reading']} for r in rows], sort_keys=False)
    validate_outputs(rows, outputs)
    return outputs


def validate_outputs(rows, outputs):
    for name, pos in FORMATS:
        value = outputs[f'VTuber変換辞書_{name}.tsv']
        ledger.require(value.startswith(b'\xff\xfe'), 'Missing UTF-16LE BOM')
        text = value[2:].decode('utf-16le')
        ledger.require(text.endswith('\r\n') and '\n' not in text.replace('\r\n', ''), 'Invalid TSV newlines')
        actual = [line.split('\t') for line in text.splitlines() if not line.startswith('!')]
        ledger.require(actual == [[r['reading'], r['word'], r[pos]] for r in rows], 'TSV parity mismatch')
    ledger.require(plistlib.loads(outputs['VTuber変換辞書_macOS.plist']) ==
                   [{'phrase': r['word'], 'shortcut': r['reading']} for r in rows], 'Plist parity mismatch')


def prepare(root=ROOT):
    rows, _, _, _ = ledger.load(root)
    return rows, render(rows)


def check_dist(root, outputs):
    stale = [name for name, value in outputs.items()
             if not (root / 'dist' / name).exists() or (root / 'dist' / name).read_bytes() != value]
    ledger.require(not stale, 'Stale dist; run python scripts/build.py: ' + ', '.join(stale))


def main(root=ROOT, check=False):
    root = Path(root)
    rows, outputs = prepare(root)
    if check:
        check_dist(root, outputs)
    else:
        out = root / 'dist'
        out.mkdir(exist_ok=True)
        temporary = []
        try:
            for name, value in outputs.items():
                path = out / (name + '.tmp')
                path.write_bytes(value)
                temporary.append((path, out / name))
            for path, target in temporary:
                path.replace(target)
        finally:
            for path, _ in temporary:
                path.unlink(missing_ok=True)
    print(f'Validated {len(rows)} entries in four formats; ' + ('read-only check.' if check else 'generated dist.'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--check', action='store_true', help='Read-only validation of tracked outputs')
    args = parser.parse_args()
    main(args.root, args.check)
