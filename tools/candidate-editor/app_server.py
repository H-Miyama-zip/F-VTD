"""Loopback-only candidate DB editor. Dictionary files are read-only references."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import daily
import editor

ASSETS = Path(__file__).resolve().parent / 'app'
REVIEW = 'app_review.json'


class Conflict(ValueError):
    pass


class Workspace:
    def __init__(self, root=editor.ROOT, db=editor.DEFAULT_DB, out=daily.DEFAULT_OUT):
        self.root, self.db, self.out = root.resolve(), db.resolve(), out.resolve()
        self.lock = threading.RLock()
        self.preview_token = None
        self.preview_batch = None

    def pool(self):
        pool, excluded, hashes = daily.candidates(self.db, self.root)
        return [r for r in pool if r['dictionary_decision'] != '採用'], excluded, hashes

    def batch(self):
        latest = editor.read_json(self.out / 'latest.json')
        if latest:
            batch = Path(latest['batch']).resolve()
            batch.relative_to((self.out / 'batches').resolve())
            if (batch / '候補情報.json').exists():
                return batch
        return self.new_batch()

    def new_batch(self, count=10, carry=None):
        pool, excluded, hashes = self.pool()
        if not pool and carry:
            batch = self.out / ('batches/' + editor.now()[:10] + '_' + daily.uuid.uuid4().hex[:8])
            batch.mkdir(parents=True)
            records = [{k: v for k, v in r.items() if k not in ('decision', 'memo', 'registration', 'recalled')} for r in carry]
            daily.replace_bytes(batch / '候補情報.json', editor.json_bytes({'version': 1, 'batch_id': batch.name,
                'created_at': editor.now(), 'root': str(self.root), 'db': str(self.db), 'records': records,
                'db_hashes': hashes, 'master_hash': editor.file_hash(self.root / 'data/master.tsv'),
                'eligible_count': 0, 'excluded_counts': excluded}))
        else:
            batch = daily.prepare(self.db, self.root, self.out, count, randomize=True, exclude_approved=True)
        if carry:
            pool, _, _ = self.pool()
            seen = {r['key'] for r in carry}
            pool = [r for r in pool if r['key'] not in seen]
            fresh = secrets.SystemRandom().sample(pool, min(max(0, count-len(carry)), len(pool)))
            records = [{k: v for k, v in r.items() if k not in ('decision', 'memo', 'registration', 'recalled')} for r in carry] + fresh
            carried = {r['key']: r for r in carry}
            info = self.info(batch)
            entries = [{'key': r['key'], 'decision': carried[r['key']]['decision'] if r['key'] in carried else '未判断',
                        'memo': carried[r['key']]['memo'] if r['key'] in carried else r['decision_reason']} for r in records]
            for entry in entries:
                if 'registration' in carried.get(entry['key'], {}):
                    entry['registration'] = carried[entry['key']]['registration']
            daily.replace_bytes(batch / REVIEW, editor.json_bytes({'version': 1, 'batch_id': info['batch_id'],
                'candidate_records': records, 'entries': sorted(entries, key=lambda r: r['key']), 'revision': 0,
                'updated_at': editor.now(), 'excel_hash': None, 'available_count': len(pool)-len(fresh),
                'recalled_keys': [r['key'] for r in carry if r.get('recalled')]}))
        daily.replace_bytes(self.out / 'latest.json', editor.json_bytes({'batch': str(batch.resolve())}))
        self.preview_token = None
        return batch

    def info(self, batch):
        info = daily.review_info(batch / REVIEW)
        if Path(info['root']).resolve() != self.root or Path(info['db']).resolve() != self.db:
            raise ValueError('候補一覧の保存先がこの辞書と一致しません。')
        return info

    def document(self, batch):
        info = self.info(batch)
        path = batch / REVIEW
        if not path.exists():
            excel = batch / daily.BOOK_NAME
            entries = daily.read_review(excel, info) if excel.exists() else [
                {'key': r['key'], 'decision': '未判断', 'memo': r['decision_reason']}
                for r in info['records']]
            document = {'version': 1, 'batch_id': info['batch_id'], 'entries': daily.validate_review(entries, info),
                'revision': 0, 'updated_at': editor.now(), 'excel_hash': editor.file_hash(excel)}
            daily.replace_bytes(path, editor.json_bytes(document))
        document = editor.read_json(path)
        daily.read_review(path, info)
        if 'candidate_records' not in document:
            records = list(info['records'])
            if not (batch / '完了.json').exists():
                if all(r['decision'] == '未判断' and not r['memo'] for r in document['entries']):
                    pool, _, _ = self.pool()
                    if pool:
                        records = secrets.SystemRandom().sample(pool, min(len(info['records']), len(pool)))
                        document['entries'] = sorted([{'key': r['key'], 'decision': '未判断', 'memo': r['decision_reason']} for r in records], key=lambda r: r['key'])
                        document['available_count'] = len(pool)-len(records)
                else:
                    secrets.SystemRandom().shuffle(records)
            document.update(candidate_records=records, revision=document['revision']+1)
            daily.replace_bytes(path, editor.json_bytes(document))
        return document

    def state(self):
        with self.lock:
            batch = self.batch()
            document = self.document(batch)
            info = self.info(batch)
            decisions = {r['key']: r for r in document['entries']}
            records = [{**r, **{k: v for k, v in decisions[r['key']].items() if k != 'key'},
                        **({'recalled': True} if r['key'] in info.get('recalled_keys', []) else {})}
                       for r in info['records']]
            history = []
            for completion in sorted((self.out / 'batches').glob('*/完了.json'), reverse=True)[:7]:
                result = editor.read_json(completion)
                history.append({'date': result['applied_at'][:10], 'added': result['added_words'],
                    'saved': result.get('saved_decisions', 0), 'target': result.get('target', 'dictionary')})
            return {'batch_id': info['batch_id'], 'date': info['created_at'][:10],
                'revision': document['revision'], 'records': records, 'saved_at': document['updated_at'],
                'dictionary_count': len(daily.master_rows(self.root)),
                'remaining': document.get('available_count', max(0, info['eligible_count']-len(records))),
                'pool_exhausted': document.get('available_count') == 0,
                'complete': editor.read_json(batch / '完了.json'),
                'history': history, 'preview_valid': self.preview_token is not None and self.preview_batch == batch}

    def check_version(self, payload):
        batch = self.batch()
        document = self.document(batch)
        if payload.get('batch_id') != self.info(batch)['batch_id'] or payload.get('revision') != document['revision']:
            raise Conflict('別の画面で作業内容が変わりました。画面を再読み込みして最新の判断を確認してください。')
        return batch, document

    def save(self, payload):
        with self.lock:
            batch, document = self.check_version(payload)
            if (batch / '完了.json').exists():
                raise Conflict('この候補は保存済みです。次の候補を開いてください。')
            entries = daily.validate_review(payload.get('entries'), self.info(batch))
            old = {r['key']: r for r in document['entries']}
            for entry in entries:
                if 'registration' not in entry and 'registration' in old[entry['key']]:
                    entry['registration'] = old[entry['key']]['registration']
            first_decision = any(old[r['key']]['decision'] == '未判断' and r['decision'] != '未判断' for r in entries)
            replenished = False
            if entries != document['entries']:
                if first_decision:
                    pending = sum(r['decision'] == '未判断' for r in entries)
                    if pending < 10:
                        pool, _, _ = self.pool()
                        seen = {r['key'] for r in document['candidate_records']}
                        pool = [r for r in pool if r['key'] not in seen]
                        fresh = secrets.SystemRandom().sample(pool, min(10-pending, len(pool)))
                        document['candidate_records'] += fresh
                        entries += [{'key': r['key'], 'decision': '未判断', 'memo': r['decision_reason']} for r in fresh]
                        entries.sort(key=lambda r: r['key'])
                        document['available_count'] = len(pool)-len(fresh)
                        replenished = True
                document.update(entries=entries, revision=document['revision'] + 1, updated_at=editor.now())
                daily.replace_bytes(batch / REVIEW, editor.json_bytes(document))
                self.preview_token = None
            result = {'revision': document['revision'], 'saved_at': document['updated_at']}
            if replenished:
                result['state'] = self.state()
            return result

    def preview(self, payload):
        with self.lock:
            batch, _ = self.check_version(payload)
            plan = daily.preview_candidates(self.db, self.root, batch / REVIEW)
            self.preview_token = secrets.token_urlsafe(24) if plan['applicable'] else None
            self.preview_batch = batch
            return {'token': self.preview_token, 'conversions': plan['conversions'], 'errors': plan['errors'],
                'counts': {label: sum(r['decision'] == label for r in self.document(batch)['entries']) for label in daily.DECISIONS}}

    def apply(self, payload):
        with self.lock:
            batch, _ = self.check_version(payload)
            if not self.preview_token or self.preview_batch != batch or payload.get('token') != self.preview_token:
                raise Conflict('先に追加内容を確認してください。判断を変えた場合は、もう一度確認が必要です。')
            result = daily.save_candidates(self.db, self.root, batch / '候補DB保存計画.json')
            self.preview_token = None
            return {'result': result, 'state': self.state()}

    def next(self, payload):
        with self.lock:
            batch, _ = self.check_version(payload)
            if not (batch / '完了.json').exists():
                raise Conflict('今の候補の判断を確認して保存してから、次の候補を開いてください。')
            count = payload.get('count', 10)
            if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 100:
                raise ValueError('候補の件数は1〜100語です。')
            carry = [r for r in self.state()['records'] if r['decision'] == '未判断']
            pool, _, _ = self.pool()
            if not pool and not carry:
                document = self.document(batch)
                document['available_count'] = 0
                daily.replace_bytes(batch / REVIEW, editor.json_bytes(document))
                return self.state()
            self.new_batch(count, carry)
            return self.state()

    def catalog(self, kind):
        if kind not in ('approved', 'held'):
            raise ValueError('採用済みか保留の一覧を選んでください。')
        with self.lock:
            tables, metadata, _, _ = editor.load_db(self.db)
            eligible, _, _ = daily.candidates(self.db, self.root, include_held=True, include_existing=True)
            ready = {r['key'] for r in eligible}
            names = {r['word'] for r in daily.master_rows(self.root)}
            result = []
            for table_kind, rows in tables.items():
                for row in rows:
                    view = editor.editable_view(table_kind, row, metadata)
                    if view['dictionary_decision'] != ('採用' if kind == 'approved' else '保留'):
                        continue
                    key = editor.make_key(table_kind, row)
                    record = {'key': key, 'kind': table_kind, 'name': view['name'], 'reading': view['reading'],
                        'source_url': view['source_url'], 'evidence_summary': view['evidence_summary'],
                        'youtube_channel_id': row.get('youtube_channel_id', ''),
                        'dictionary_decision': view['dictionary_decision'], 'decision_reason': view.get('decision_reason', '')}
                    result.append({**record, 'registered': view['name'] in names, 'ready': key in ready,
                        'aliases': metadata['records'].get(key, {}).get('extra_conversions', [])})
            return {'records': result}

    def recall(self, payload):
        with self.lock:
            batch, document = self.check_version(payload)
            keys = payload.get('keys', [payload.get('key')])
            if (not isinstance(keys, list) or not 1 <= len(keys) <= 1000
                    or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys)):
                raise ValueError('重複のない候補を1〜1000件選んでください。')
            kind = payload.get('kind')
            items = {r['key']: r for r in self.catalog(kind)['records']}
            complete = (batch / '完了.json').exists()
            existing = {r['key']: r for r in document['candidate_records']}
            decisions = {r['key']: r for r in document['entries']}
            additions = []
            # Validate the complete selection before changing the queue.
            for key in keys:
                local = not complete and key in existing
                if key not in items and not (local and decisions[key]['decision'] == ('採用' if kind == 'approved' else '保留')):
                    raise Conflict('選んだ候補の判断が変わりました。一覧を開き直してください。')
                if local:
                    continue  # Preserve its existing choice, edits and memo.
                item = items[key]
                record = {k: v for k, v in item.items() if k not in ('registered', 'ready', 'aliases')}
                entry = {'key': key, 'decision': '未判断', 'memo': item['decision_reason']}
                if item['aliases']:
                    entry['registration'] = daily.validate_registration({'name': item['name'], 'reading': item['reading'], 'aliases': item['aliases']})
                additions.append((record, entry))
            if complete:
                carry = [r for r in self.state()['records'] if r['decision'] == '未判断']
                carried = {r['key'] for r in carry}
                carry += [{**record, **entry, 'recalled': True} for record, entry in additions if record['key'] not in carried]
                self.new_batch(10, carry)
            elif additions:
                document['candidate_records'] += [record for record, _ in additions]
                document['entries'] += [entry for _, entry in additions]
                document['entries'].sort(key=lambda r: r['key'])
                document.setdefault('recalled_keys', []).extend(record['key'] for record, _ in additions)
                pool, _, _ = self.pool()
                seen = {r['key'] for r in document['candidate_records']}
                document['available_count'] = sum(r['key'] not in seen for r in pool)
                document.update(revision=document['revision']+1, updated_at=editor.now())
                daily.replace_bytes(batch / REVIEW, editor.json_bytes(document))
            if complete or additions:
                self.preview_token = None
            return {'state': self.state(), 'selected': keys[0], 'selected_keys': keys}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, workspace, port=8769):
        super().__init__(('127.0.0.1', port), Handler)
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        self.instance = secrets.token_urlsafe(18)
        self.root_id = hashlib.sha256(str(workspace.root).encode('utf-8')).hexdigest()

    @property
    def url(self):
        return 'http://127.0.0.1:' + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def allowed_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def send(self, status, content, content_type='application/json; charset=utf-8'):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if not self.allowed_host():
            self.send(403, {'error': 'この画面からの操作だけを受け付けます。'})
            return
        route = urllib.parse.urlsplit(self.path).path
        try:
            if route == '/api/health':
                self.send(200, {'app': 'f-vtd-daily', 'root_id': self.server.root_id,
                    'instance': self.server.instance, 'storage_mode': 'candidate_db'})
            elif route == '/api/session':
                self.send(200, {'token': self.server.token})
            elif route == '/api/state':
                self.send(200, self.server.workspace.state())
            elif route == '/api/catalog':
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self.send(200, self.server.workspace.catalog(query.get('kind', [''])[0]))
            else:
                files = {'/': ('index.html', 'text/html; charset=utf-8'),
                    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                    '/app.css': ('app.css', 'text/css; charset=utf-8'),
                    '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
                if route not in files:
                    self.send(404, {'error': 'ページがありません。'})
                    return
                name, mime = files[route]
                self.send(200, (ASSETS / name).read_bytes(), mime)
        except (ValueError, OSError, KeyError) as error:
            self.send(409, {'error': str(error)})

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed_origins = (self.server.url, f'http://localhost:{self.server.server_port}')
        if not self.allowed_host() or (origin and origin not in allowed_origins) or not secrets.compare_digest(self.headers.get('X-App-Token', ''), self.server.token):
            self.send(403, {'error': '画面を開き直してください。'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2_000_000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('操作の形式が不正です。')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('操作の形式が不正です。')
            route = urllib.parse.urlsplit(self.path).path
            actions = {'/api/save': self.server.workspace.save, '/api/preview': self.server.workspace.preview,
                '/api/apply': self.server.workspace.apply, '/api/next': self.server.workspace.next,
                '/api/recall': self.server.workspace.recall}
            if route == '/api/shutdown':
                self.send(200, {'stopped': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif route in actions:
                self.send(200, actions[route](payload))
            else:
                self.send(404, {'error': '操作がありません。'})
        except Conflict as error:
            self.send(409, {'error': str(error)})
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.send(400, {'error': str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=editor.ROOT)
    parser.add_argument('--db', type=Path, default=editor.DEFAULT_DB)
    parser.add_argument('--out', type=Path, default=daily.DEFAULT_OUT)
    parser.add_argument('--port', type=int, default=8769)
    args = parser.parse_args()
    workspace = Workspace(args.root, args.db, args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        server = Server(workspace, args.port)
    except OSError:
        server = Server(workspace, 0)
    descriptor = args.out / 'app_server.json'
    data = {'url': server.url, 'pid': os.getpid(), 'root_id': server.root_id, 'instance': server.instance}
    daily.replace_bytes(descriptor, editor.json_bytes(data))
    print('辞書追加アプリ: ' + server.url, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if editor.read_json(descriptor, {}).get('instance') == server.instance:
            descriptor.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
