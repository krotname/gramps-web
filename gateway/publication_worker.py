"""Bounded publication hooks over durable, independently reported snapshots."""
import concurrent.futures
import fcntl
import json
import os
import shutil
import selectors
import signal
import time
import subprocess
import threading
from pathlib import Path


class PublicationWorker:
    def __init__(self, store, config, runner=None):
        self.store = store
        self.commands = {}
        for target in ('books', 'geni'):
            command = config.get(target)
            if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg and '\0' not in arg for arg in command):
                raise ValueError('Use an explicit argument array for each publication hook')
            self.commands[target] = command
        self.timeout = config.get('timeout_seconds', 1200)
        if not isinstance(self.timeout, int) or not 5 <= self.timeout <= 1800:
            raise ValueError('Publication timeout must be between 5 and 1800 seconds')
        self.stop = threading.Event()
        self.runner = runner or self.execute
        self.jobs = store.folder / '.publication-jobs'
        self.jobs.mkdir(mode=0o700, exist_ok=True)

    @staticmethod
    def execute(target, command, environment, timeout):
        report = Path(environment['GENEALOGY_JOB_DIR']) / 'result.json'
        process = subprocess.Popen(command, env=environment, cwd=environment['GENEALOGY_JOB_DIR'],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        size = 0
        try:
            with report.open('wb') as stream, selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    if not selector.select(min(remaining, 0.25)):
                        continue
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ValueError('Publication report exceeds the size limit')
                    stream.write(chunk)
                code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
                if code:
                    raise subprocess.CalledProcessError(code, command)
        except BaseException:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                if sig == signal.SIGTERM:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            process.wait()
            raise
        finally:
            process.stdout.close()
        return json.loads(report.read_text(encoding='utf-8'))

    def recover(self):
        with self.store.serialized(), self.store.connection() as db:
            rows = db.execute("SELECT revision,geni_state FROM publications WHERE geni_state='running' OR books_state='running'").fetchall()
            db.execute("UPDATE publications SET books_state='pending' WHERE books_state='running'")
        for row in rows:
            if row['geni_state'] == 'running':
                self.store.publication_result(row['revision'], 'geni', 'blocked',
                    'Interrupted external publication: read back the exact profiles before retrying')

    def books(self, revision, result, folder):
        if not result.get('checks') or not all(value is True for value in result['checks'].values()):
            raise ValueError('Book acceptance failed')
        volumes = result.get('volumes', {})
        if set(volumes) != set('123456'):
            raise ValueError('Provide exactly six volumes')
        prepared = []
        filenames = set()
        for number in '123456':
            volume = volumes[number]
            file = (folder / volume['a4_pdf']).resolve()
            if not file.is_relative_to(folder.resolve()) or not file.is_file():
                raise ValueError('Book leaves its job folder or is missing')
            if not isinstance(volume.get('a4_pages'), int) or volume['a4_pages'] < 1:
                raise ValueError('Missing book page count')
            if file.stat().st_size > 32 * 1024 * 1024 or file.name in filenames:
                raise ValueError('Oversized or duplicate book file')
            filenames.add(file.name)
            prepared.append((file, volume, file.read_bytes()))
        manifest = []
        for file, volume, data in prepared:
            uploaded = self.store.save_book(revision, file.name, data)
            manifest.append({'file': uploaded['file'], 'title': volume['title'], 'pages': volume['a4_pages']})
        self.store.install_books(revision, manifest)

    def process(self, revision, target):
        folder = self.jobs / revision / target
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        snapshot = folder / 'snapshot.ged'
        snapshot.write_bytes(self.store.publication_snapshot(revision))
        os.chmod(snapshot, 0o600)
        environment = dict(os.environ, GENEALOGY_SNAPSHOT=str(snapshot.resolve()),
                           GENEALOGY_REVISION=revision, GENEALOGY_JOB_DIR=str(folder.resolve()))
        self.store.publication_result(revision, target, 'running', 'Frozen-revision publication started')
        try:
            result = self.runner(target, self.commands[target], environment, self.timeout)
            if not isinstance(result, dict) or result.get('canonical_revision') != revision:
                raise ValueError('Publication report belongs to another snapshot')
            if result.get('state') in {'failed', 'blocked'}:
                self.store.publication_result(revision, target, result['state'], result.get('evidence', 'Hook requires operator action'))
                return
            if target == 'books':
                self.books(revision, result, folder)
            elif result.get('state') == 'ready' and isinstance(result.get('evidence'), str) and result['evidence'].strip():
                self.store.publication_result(revision, target, 'ready', result['evidence'])
            else:
                raise ValueError('Geni hook did not provide readback evidence')
            shutil.rmtree(folder)
        except Exception as exc:
            state = 'blocked' if target == 'geni' else 'failed'
            self.store.publication_result(revision, target, state,
                type(exc).__name__ + ': inspect the private job result; read back external changes before retrying')

    def run_once(self):
        with (self.store.folder / '.publication-worker.lock').open('a') as lock:
            os.chmod(self.store.folder / '.publication-worker.lock', 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            try:
                self.recover()
                with self.store.connection() as db:
                    row = db.execute("""SELECT p.revision,p.books_state,p.geni_state,
                        NOT EXISTS(SELECT 1 FROM publications older
                            WHERE older.rowid < p.rowid AND older.geni_state != 'ready') AS geni_eligible
                        FROM publications p WHERE p.books_state='pending'
                        OR (p.geni_state='pending' AND NOT EXISTS(SELECT 1 FROM publications older
                            WHERE older.rowid < p.rowid AND older.geni_state != 'ready'))
                        ORDER BY p.rowid LIMIT 1""").fetchone()
                if row:
                    targets = [target for target in ('books', 'geni') if row[target + '_state'] == 'pending' and (target != 'geni' or row['geni_eligible'])]
                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                        futures = [executor.submit(self.process, row['revision'], target) for target in targets]
                        for future in futures:
                            future.result()
                return bool(row)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def run_forever(self, stop=None):
        stop = stop or self.stop
        while not stop.is_set():
            try:
                self.run_once()
            except (OSError, ValueError):
                pass
            stop.wait(30)


def start_worker(store, config_path):
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    worker = PublicationWorker(store, config)
    threading.Thread(target=worker.run_forever, name='publication-worker', daemon=True).start()
    return worker
