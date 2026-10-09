"""Append-only change ledger and read-only replay (Python standard library)."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ('reading', 'word', 'google_pos', 'microsoft_pos', 'atok_pos',
           'origin', 'source_url', 'note', 'added_on')
REGISTRATION = COLUMNS[:5]
META = ('reason', 'evidence', 'checked_on', 'unknown')
KINDS = {'add', 'correct', 'delete', 'undo', 'annotate'}
UNKNOWN_FIELDS = {'reason', 'evidence', 'checked_on', 'applied_on', 'published_on', 'pre_import_history'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def row_id(row):
    return digest(canonical(row))


def key(row):
    return tuple(row[c] for c in REGISTRATION)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def date(value, nullable=False):
    if value is None and nullable:
        return
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'Invalid ISO date')
    dt.date.fromisoformat(value)


def http_url(value):
    """Validate absolute evidence links without making a network request."""
    require(isinstance(value, str) and value and not any(c.isspace() or ord(c) < 32 or c == '\\' for c in value),
            'Invalid HTTP URL')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port  # Access validates an explicitly supplied port.
    except ValueError as error:
        raise ValueError('Invalid HTTP URL') from error
    require(parsed.scheme in ('http', 'https') and bool(host) and
            not any(c in host for c in '%<>^`{|}'), 'Invalid HTTP URL')
    return value


def validate_row(row):
    require(isinstance(row, dict) and set(row) == set(COLUMNS), 'Invalid row columns')
    require(all(isinstance(v, str) for v in row.values()), 'Row values must be strings')
    for c in COLUMNS:
        require(not any(x in row[c] for x in '\t\r\n'), f'Invalid control character: {c}')
    for c in REGISTRATION:
        require(row[c] and row[c] == row[c].strip(), f'Invalid {c}')
    if row['origin'] == 'added':
        date(row['added_on'])
        http_url(row['source_url'])
    else:
        require(row['origin'] == 'upstream' and not row['added_on'], 'Invalid origin/added_on')


def validate_upstreams(upstreams):
    require(isinstance(upstreams, dict) and upstreams, 'Missing upstream catalog')
    for uid, value in upstreams.items():
        require(isinstance(uid, str) and uid and isinstance(value, dict), 'Invalid upstream identity')
        require(isinstance(value.get('version'), str) and value['version'].strip(), 'Missing upstream version')
        files = value.get('files')
        require(isinstance(files, dict) and files, 'Missing upstream file hashes')
        for name, sha in files.items():
            require(isinstance(name, str) and name and isinstance(sha, str) and
                    re.fullmatch(r'[0-9a-f]{64}', sha), 'Invalid upstream SHA-256')
        if 'released_on' in value:
            date(value['released_on'], nullable=True)


def read_rows(path):
    with Path(path).open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        require(tuple(reader.fieldnames or ()) == COLUMNS, 'Invalid master header')
        rows = list(reader)
    state = {}
    for row in rows:
        validate_row(row)
        require(key(row) not in state, f'Duplicate registration: {key(row)}')
        state[key(row)] = row
    return rows


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def seal(update, previous):
    value = dict(update, previous_hash=previous)
    value.pop('hash', None)
    value['hash'] = digest(canonical(value))
    return value


def git_seals(root):
    """Check finalized prefixes against the local HEAD, where a tracked seal exists.

    This is a local accident detector, not a signature or a protection against a
    person rewriting both the records and all of their independent anchors.
    """
    def head(path):
        result = subprocess.run(['git', 'show', 'HEAD:' + path], cwd=root, capture_output=True)
        return result.stdout if result.returncode == 0 else None
    for path in ('data/baseline/master.tsv', 'data/baseline/info.json'):
        old = head(path)
        if old is not None:
            require(old.replace(b'\r\n', b'\n') == (root / path).read_bytes().replace(b'\r\n', b'\n'),
                    f'Fixed baseline modified: {path}')
    for path in ('data/ledger.jsonl', 'data/publications.jsonl'):
        old = head(path)
        if old is not None:
            records = [json.loads(s) for s in old.decode('utf-8').splitlines() if s.strip()]
            require(jsonl(root / path)[:len(records)] == records, f'Finalized records modified: {path}')
    old = head('data/upstreams.json')
    if old is not None:
        current = json.loads((root / 'data/upstreams.json').read_text(encoding='utf-8'))
        require(all(current.get(k) == v for k, v in json.loads(old).items()), 'Upstream hashes modified')


def validate_metadata(change, reconstructed):
    require(set(META) <= set(change), 'Missing change metadata')
    require(change['reason'] is None or isinstance(change['reason'], str), 'Invalid reason')
    require(reconstructed or bool(change['reason'] and change['reason'].strip()), 'A reason is required')
    date(change['checked_on'], nullable=True)
    require(isinstance(change['unknown'], list) and all(isinstance(x, str) for x in change['unknown']), 'Invalid unknown fields')
    require(len(set(change['unknown'])) == len(change['unknown']) and set(change['unknown']) <= UNKNOWN_FIELDS, 'Duplicate/unrecognized unknown fields')
    for field in ('checked_on', 'reason'):
        require((change[field] is None) == (field in change['unknown']), f'Unknown {field} must match its value')
    require(reconstructed or not {'applied_on', 'pre_import_history'} & set(change['unknown']), 'Normal update cannot have an unknown application date/import history')
    ev = change['evidence']
    require(isinstance(ev, dict) and set(ev) == {'urls', 'text'}, 'Invalid evidence')
    require(isinstance(ev['urls'], list), 'Invalid evidence URL list')
    for url in ev['urls']:
        http_url(url)
    require(ev['text'] is None or isinstance(ev['text'], str), 'Invalid evidence text')
    evidence_unknown = not ev['urls'] and not (ev['text'] and ev['text'].strip())
    require(evidence_unknown == ('evidence' in change['unknown']), 'Unknown evidence must match its value')


def replay(baseline, updates, upstreams):
    validate_upstreams(upstreams)
    for row in baseline:
        validate_row(row)
    require(len({key(r) for r in baseline}) == len(baseline), 'Duplicate baseline registration')
    state = {key(r): dict(r) for r in baseline}
    known, update_ids, undone = {}, set(), set()
    previous, last_date, applied_started = None, None, False
    for update in updates:
        require(set(update) == {'id', 'scope', 'status', 'title', 'applied_on', 'restoration',
                                'changes', 'previous_hash', 'hash'}, 'Invalid update fields')
        require(isinstance(update['id'], str) and update['id'] and update['id'] not in update_ids, 'Duplicate/empty update ID')
        update_ids.add(update['id'])
        require(update == seal(update, previous), 'Ledger hash chain mismatch')
        previous = update['hash']
        require(update['status'] == 'final', 'Drafts must stay outside the finalized ledger')
        reconstructed = update['scope'] == 'reconstructed'
        require(update['scope'] in ('reconstructed', 'applied'), 'Invalid scope')
        require(not reconstructed or not applied_started, 'Reconstruction must precede normal updates')
        require(isinstance(update['title'], str) and update['title'], 'Missing title')
        require(isinstance(update['changes'], list) and update['changes'], 'Empty update')
        date(update['applied_on'], nullable=reconstructed)
        if not reconstructed:
            applied_started = True
            require(last_date is None or update['applied_on'] >= last_date, 'Application dates out of order')
            last_date = update['applied_on']
            require(update['restoration'] is None, 'Normal updates cannot claim reconstructed provenance')
        else:
            require(isinstance(update['restoration'], dict) and update['restoration'].get('sources'), 'Missing restoration sources')
        for change in update['changes']:
            allowed = {'id', 'kind', 'before', 'after', 'reason', 'evidence', 'checked_on',
                       'unknown', 'upstream', 'related_ids', 'legacy_added_on',
                       'metadata_before', 'metadata_after'}
            require(set(change) == allowed, 'Invalid change fields')
            cid, kind = change['id'], change['kind']
            require(isinstance(cid, str) and cid and cid not in known, 'Duplicate/empty change ID')
            require(kind in KINDS, 'Invalid change kind')
            require(change['upstream'] in upstreams, 'Unknown upstream reference')
            require(isinstance(change['related_ids'], list) and len(set(change['related_ids'])) == len(change['related_ids']), 'Invalid related IDs')
            require(all(x in known for x in change['related_ids']), 'Related ID must precede change')
            validate_metadata(change, reconstructed)
            date(change['legacy_added_on'], nullable=True)
            require(reconstructed or change['legacy_added_on'] is None, 'Legacy dates belong to reconstruction')
            if reconstructed and change['legacy_added_on']:
                last_date = max(last_date or change['legacy_added_on'], change['legacy_added_on'])
            before, after = change['before'], change['after']
            for row in (before, after):
                if row is not None:
                    validate_row(row)
            if kind == 'annotate':
                require(before is None and after is None and len(change['related_ids']) == 1, 'Annotation must target one record, not dictionary content')
                target = known[change['related_ids'][0]]
                require(change['upstream'] == target['upstream'], 'Annotation upstream must match target record')
                require(target['kind'] != 'annotate', 'Annotate the original record')
                require(change['metadata_before'] == {k: target[k] for k in META}, 'Annotation before metadata mismatch')
                replacement = change['metadata_after']
                require(isinstance(replacement, dict) and set(replacement) == set(META), 'Invalid annotation replacement')
                require(replacement != change['metadata_before'], 'No-op annotation')
                validate_metadata(replacement, target['_reconstructed'])
                target.update(replacement)
            else:
                require(change['metadata_before'] is None and change['metadata_after'] is None, 'Content change cannot edit ledger metadata')
                require(before is not None or after is not None, 'Empty content change')
                require(before != after, 'No-op content change')
                if kind == 'add':
                    require(before is None and after is not None, 'Invalid addition')
                elif kind == 'delete':
                    require(before is not None and after is None, 'Invalid deletion')
                elif kind == 'correct':
                    require(before is not None and after is not None, 'Correction requires explicit before/after')
                elif kind == 'undo':
                    require(len(change['related_ids']) == 1, 'Undo must target one content change')
                    tid = change['related_ids'][0]
                    target = known[tid]
                    require(change['upstream'] == target['upstream'], 'Undo upstream must match target record')
                    require(target['kind'] != 'annotate' and tid not in undone, 'Duplicate/invalid undo')
                    require(before == target['after'] and after == target['before'], 'Undo must be the exact inverse')
                    undone.add(tid)
                if before is not None and after is not None:
                    require(before['origin'] == after['origin'], 'Correction must preserve origin')
                    require(before['added_on'] == after['added_on'], 'Correction must preserve original added_on')
                if not reconstructed:
                    if before is not None:
                        require(state.get(key(before)) == before, f'Before state mismatch: {cid}')
                        del state[key(before)]
                    if after is not None:
                        require(key(after) not in state, f'Duplicate application/conflict: {cid}')
                        state[key(after)] = dict(after)
            known[cid] = dict(change, _reconstructed=reconstructed, _update_id=update['id'])
    return list(state.values()), known


def load(root=ROOT, match_master=True, check_git=True):
    root = Path(root)
    info = json.loads((root / 'data/baseline/info.json').read_text(encoding='utf-8'))
    baseline_path = root / 'data/baseline/master.tsv'
    require(digest(baseline_path.read_bytes()) == info['sha256'], 'Baseline hash mismatch')
    upstreams = json.loads((root / 'data/upstreams.json').read_text(encoding='utf-8'))
    require(digest(canonical(upstreams[info['upstream']])) == info['upstream_sha256'], 'Original upstream hashes changed')
    updates = jsonl(root / 'data/ledger.jsonl')
    count = info['reconstructed_updates']
    require(len(updates) >= count and (updates[count-1]['hash'] if count else None) == info['reconstructed_head'], 'Sealed reconstruction changed')
    require(all(u['scope'] == 'reconstructed' for u in updates[:count]) and
            all(u['scope'] == 'applied' for u in updates[count:]), 'Only sealed migration records may use reconstructed scope')
    if check_git:
        git_seals(root)
    baseline = read_rows(baseline_path)
    require(len(baseline) == info['count'], 'Baseline count mismatch')
    rows, known = replay(baseline, updates, upstreams)
    if match_master:
        master = read_rows(root / 'data/master.tsv')
        require({key(r): r for r in rows} == {key(r): r for r in master}, 'Unrecorded or ledger-only changes: replay differs from master')
        rows = master  # preserve distribution order; order alone has no historical meaning
    return rows, updates, known, info


def difference(before, after, pairs=None):
    """Only exact full-row matches are automatic; correction mappings are explicit."""
    old = {row_id(r): r for r in before}
    new = {row_id(r): r for r in after}
    removed = {h: r for h, r in old.items() if h not in new}
    added = {h: r for h, r in new.items() if h not in old}
    changes = []
    for pair in pairs or []:
        require(set(pair) == {'before', 'after'}, 'Pair requires before/after content hashes')
        require(pair['before'] in removed and pair['after'] in added, 'Ambiguous/duplicate correction mapping')
        changes.append(('correct', removed.pop(pair['before']), added.pop(pair['after'])))
    changes.extend(('delete', r, None) for _, r in sorted(removed.items()))
    changes.extend(('add', None, r) for _, r in sorted(added.items()))
    return changes


def new_change(cid, kind, before, after, upstream, reason=None, urls=None, checked_on=None):
    unknown = ['checked_on'] if checked_on is None else []
    if reason is None:
        unknown.append('reason')
    if not urls:
        unknown.append('evidence')
    return dict(id=cid, kind=kind, before=before, after=after, reason=reason,
                evidence={'urls': urls or [], 'text': None}, checked_on=checked_on,
                unknown=unknown, upstream=upstream, related_ids=[], legacy_added_on=None,
                metadata_before=None, metadata_after=None)


def append_draft(path, root=ROOT):
    root = Path(root)
    _, updates, _, _ = load(root, match_master=False)
    draft = json.loads(Path(path).read_text(encoding='utf-8'))
    require(draft['status'] == 'draft' and draft['scope'] == 'applied', 'Expected a normal draft')
    draft['status'] = 'final'
    finalized = seal(draft, updates[-1]['hash'] if updates else None)
    upstreams = json.loads((root / 'data/upstreams.json').read_text(encoding='utf-8'))
    result, _ = replay(read_rows(root / 'data/baseline/master.tsv'), updates + [finalized], upstreams)
    require({key(r): r for r in result} == {key(r): r for r in read_rows(root / 'data/master.tsv')}, 'Draft does not explain the master difference')
    ledger_path = root / 'data/ledger.jsonl'
    # Validate the whole candidate before one atomic replacement; never touch master.
    value = ledger_path.read_bytes().rstrip(b'\r\n')
    value = (value + b'\n' if value else b'') + canonical(finalized) + b'\n'
    temp = ledger_path.with_suffix('.jsonl.tmp')
    temp.write_bytes(value)
    temp.replace(ledger_path)
    return finalized


def upstream_audit(rows, known):
    """Report exact and suspicious matches in an UNMODIFIED candidate upstream.

    It does not merge, reapply, delete, or infer people's identities.
    """
    results = []
    seen = {}
    for cid, change in known.items():
        if change['kind'] not in ('correct', 'delete', 'undo'):
            continue
        before, after = change['before'], change['after']
        if not any(r and r['origin'] == 'upstream' for r in (before, after)):
            continue
        signature = lambda r: tuple(r[c] for c in REGISTRATION) if r else None
        olds = [r for r in rows if before and signature(r) == signature(before) and r['origin'] == before['origin']]
        news = [r for r in rows if after and signature(r) == signature(after) and r['origin'] == after['origin']]
        suspicious = [r for r in rows if any(t and (r['reading'] == t['reading'] or r['word'] == t['word']) for t in (before, after)) and r not in olds + news]
        if len(olds) > 1 or len(news) > 1:
            status = 'multiple_matches'
        elif olds and news:
            status = 'both_present'
        elif olds:
            status = 'old_present'
        elif news:
            status = 'already_corrected'
        else:
            status = 'neither_present'
        conflicts = sorted({prior for r in (before, after) if r for prior in seen.get((r['reading'], r['word']), [])})
        results.append(dict(change_id=cid, status=status, suspicious=suspicious,
                            related_history=conflicts, action='manual_review'))
        for r in (before, after):
            if r:
                seen.setdefault((r['reading'], r['word']), []).append(cid)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    d = sub.add_parser('draft')
    d.add_argument('--id', required=True)
    d.add_argument('--applied-on', required=True)
    d.add_argument('--title', default='辞書を更新')
    d.add_argument('--pairs', type=Path)
    d.add_argument('--output', type=Path, required=True)
    d.add_argument('--reason')
    d.add_argument('--checked-on')
    d.add_argument('--url', action='append', default=[])
    d.add_argument('--upstream', help='Explicit upstream catalog key; required when more than one version is recorded')
    special = d.add_mutually_exclusive_group()
    special.add_argument('--undo', help='Explicit ID of the content change to reverse; edit master first')
    special.add_argument('--annotate', help='Explicit ID of the record whose metadata will be corrected')
    c = sub.add_parser('confirm')
    c.add_argument('draft', type=Path)
    a = sub.add_parser('audit-upstream')
    a.add_argument('candidate', type=Path, help='Unmodified upstream normalized to master columns')
    args = parser.parse_args()
    if args.command == 'check':
        rows, updates, known, _ = load(args.root)
        print(f'Ledger valid: {len(rows)} rows, {len(updates)} updates, {len(known)} changes')
    elif args.command == 'confirm':
        value = append_draft(args.draft, args.root)
        print('Finalized locally (publication not recorded):', value['id'])
    elif args.command == 'draft':
        rows, _, known, info = load(args.root, match_master=False)
        require(not args.pairs or not (args.undo or args.annotate), 'Pairs cannot be combined with undo/annotation')
        catalog = json.loads((args.root / 'data/upstreams.json').read_text(encoding='utf-8'))
        upstream = args.upstream or info['upstream']
        if args.undo or args.annotate:
            require(args.upstream is None, 'Undo/annotation inherits the target upstream; do not specify --upstream')
        else:
            require(args.upstream is not None or len(catalog) == 1,
                    'Multiple upstream versions; specify --upstream: ' + ', '.join(catalog))
            require(upstream in catalog, 'Unknown upstream reference: ' + upstream)
        changes = difference(rows, read_rows(args.root / 'data/master.tsv'),
                             json.loads(args.pairs.read_text(encoding='utf-8')) if args.pairs else None)
        value = dict(id=args.id, scope='applied', status='draft', title=args.title,
                     applied_on=args.applied_on, restoration=None,
                     changes=[new_change(f'{args.id}-{i:04}', k, b, a, upstream,
                                         args.reason, args.url, args.checked_on)
                              for i, (k, b, a) in enumerate(changes, 1)])
        if args.undo or args.annotate:
            target_id = args.undo or args.annotate
            require(target_id in known, 'Unknown target ID')
            target = known[target_id]
            require(target['kind'] != 'annotate', 'Target the original content record')
            if args.undo:
                change = new_change(f'{args.id}-0001', 'undo', target['after'], target['before'],
                                    target['upstream'], args.reason, args.url, args.checked_on)
            else:
                require(not changes, 'Metadata annotation must not change the dictionary')
                change = new_change(f'{args.id}-0001', 'annotate', None, None,
                                    target['upstream'], args.reason, args.url, args.checked_on)
                change['metadata_before'] = {k: target[k] for k in META}
                change['metadata_after'] = dict(change['metadata_before'])
            change['related_ids'] = [target_id]
            value['changes'] = [change]
        require(not args.output.exists(), 'Draft output already exists')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(value) + b'\n')
        print('Draft only; inspect before/after, reason and evidence:', args.output)
        for k, b, a in changes:
            print(k, row_id(b) if b else '-', row_id(a) if a else '-')
    else:
        _, _, known, _ = load(args.root)
        # Permit duplicate rows here so the audit can report multiple matches.
        with args.candidate.open(encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f, delimiter='\t'))
        for row in rows:
            validate_row(row)
        print(json.dumps(upstream_audit(rows, known), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
