import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from gateway.store import Store
from gateway.tests.test_tree import FIXTURE
from gateway.publication_worker import PublicationWorker


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store.initialize(FIXTURE)
        self.store.retry(lambda *_: None)
        self.revision = self.store.revision
        self.store.publish(self.revision)
        self.config = {'books': ['python3', '/private/books.py'], 'geni': ['python3', '/private/geni.py'], 'timeout_seconds': 30}

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, target, command, environment, timeout):
        folder = Path(environment['GENEALOGY_JOB_DIR'])
        if target == 'geni':
            return {'canonical_revision': self.revision, 'state': 'ready', 'evidence': 'Targeted profile readback URLs verified'}
        volumes = {}
        for number in '123456':
            file = folder / (number + '.pdf')
            file.write_bytes(b'%PDF-1.7 fixture')
            volumes[number] = {'a4_pdf': file.name, 'title': number, 'a4_pages': 1}
        return {'canonical_revision': self.revision, 'checks': {'content': True}, 'volumes': volumes}

    def test_frozen_snapshot_and_repeated_button_do_not_repeat_jobs(self):
        self.store.publish(self.revision)
        frozen = self.store.publication_snapshot(self.revision)
        self.store.commit(self.revision, 'later', [{'op': 'person', 'xref': 'I1', 'name': 'Changed /Name/'}], lambda *_: None)
        calls = []
        def run(target, command, environment, timeout):
            self.assertEqual(Path(environment['GENEALOGY_SNAPSHOT']).read_bytes(), frozen)
            calls.append(target)
            return self.runner(target, command, environment, timeout)
        worker = PublicationWorker(self.store, self.config, run)
        worker.run_once()
        worker.run_once()
        self.assertCountEqual(calls, ['books', 'geni'])
        self.assertIsNone(self.store.next_publication())
        self.assertEqual(len(json.loads((self.store.folder / 'books/manifest.json').read_text())['books']), 6)

    def test_both_targets_start_and_geni_failure_does_not_block_books(self):
        barrier = threading.Barrier(2)
        def run(target, command, environment, timeout):
            barrier.wait(timeout=5)
            if target == 'geni':
                raise TimeoutError('Sensitive exception detail')
            return self.runner(target, command, environment, timeout)
        PublicationWorker(self.store, self.config, run).run_once()
        status = self.store.next_publication()
        self.assertEqual(status['books_state'], 'ready')
        self.assertEqual(status['geni_state'], 'blocked')
        self.assertNotIn('Sensitive', status['detail'])

    def test_restart_does_not_replay_an_uncertain_geni_write(self):
        self.store.publication_result(self.revision, 'geni', 'running', 'Started')
        self.store.publication_result(self.revision, 'books', 'running', 'Started')
        calls = []
        def run(target, command, environment, timeout):
            calls.append(target)
            return self.runner(target, command, environment, timeout)
        PublicationWorker(self.store, self.config, run).run_once()
        self.assertEqual(calls, ['books'])
        self.assertEqual(self.store.next_publication()['geni_state'], 'blocked')

    def test_wrong_revision_and_missing_books_cannot_be_reported_ready(self):
        def run(target, command, environment, timeout):
            result = self.runner(target, command, environment, timeout)
            result['canonical_revision'] = 'a' * 64
            return result
        PublicationWorker(self.store, self.config, run).run_once()
        self.assertEqual(self.store.next_publication()['books_state'], 'failed')
        self.assertFalse((self.store.folder / 'books/manifest.json').exists())

    def test_unsafe_book_path_cannot_be_installed(self):
        def run(target, command, environment, timeout):
            result = self.runner(target, command, environment, timeout)
            if target == 'books':
                result['volumes']['1']['a4_pdf'] = '../private.pdf'
            return result
        PublicationWorker(self.store, self.config, run).run_once()
        self.assertEqual(self.store.next_publication()['books_state'], 'failed')
        self.assertFalse((self.store.folder / 'books/manifest.json').exists())

    def test_unknown_shell_command_is_rejected_before_any_job(self):
        with self.assertRaises(ValueError):
            PublicationWorker(self.store, dict(self.config, books='python3 /private/books.py'))

    def test_live_worker_lock_prevents_a_second_processor(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        def run(target, command, environment, timeout):
            calls.append(target)
            started.set()
            release.wait(timeout=5)
            return self.runner(target, command, environment, timeout)
        first = PublicationWorker(self.store, self.config, run)
        second = PublicationWorker(self.store, self.config, run)
        thread = threading.Thread(target=first.run_once)
        thread.start()
        self.assertTrue(started.wait(5))
        self.assertFalse(second.run_once())
        release.set()
        thread.join(5)
        self.assertCountEqual(calls, ['books', 'geni'])

    def test_duplicate_book_names_are_rejected_before_upload(self):
        def run(target, command, environment, timeout):
            result = self.runner(target, command, environment, timeout)
            if target == 'books':
                result['volumes']['6']['a4_pdf'] = result['volumes']['1']['a4_pdf']
            return result
        PublicationWorker(self.store, self.config, run).run_once()
        self.assertEqual(self.store.next_publication()['books_state'], 'failed')
        self.assertFalse((self.store.folder / 'books/manifest.json').exists())

    def test_timeout_terminates_a_spawned_hook_child(self):
        folder = self.store.folder / 'process-test'
        folder.mkdir()
        script = folder / 'hook.py'
        script.write_text("import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\nPath('child.pid').write_text(str(p.pid))\ntime.sleep(60)\n")
        environment = dict(os.environ, GENEALOGY_JOB_DIR=str(folder))
        with self.assertRaises(subprocess.TimeoutExpired):
            PublicationWorker.execute('geni', [sys.executable, str(script)], environment, 5)
        child = int((folder / 'child.pid').read_text())
        state = Path('/proc') / str(child) / 'stat'
        if state.exists():
            self.assertEqual(state.read_text().split()[2], 'Z')

    def test_large_hook_report_is_stopped_before_loading(self):
        folder = self.store.folder / 'report-test'
        folder.mkdir()
        environment = dict(os.environ, GENEALOGY_JOB_DIR=str(folder))
        with self.assertRaises(ValueError):
            PublicationWorker.execute('books', [sys.executable, '-c', "import sys;sys.stdout.write('x'*3000000)"], environment, 5)
        self.assertLessEqual((folder / 'result.json').stat().st_size, 2 * 1024 * 1024)

    def test_later_geni_revision_waits_for_earlier_readback_while_books_continue(self):
        self.store.publication_result(self.revision,'geni','blocked','Ambiguous external result')
        newer=self.store.commit(self.revision,'later-publish',[{'op':'person','xref':'I1','name':'Changed /Name/'}],lambda *_:None)['revision']
        self.store.publish(newer)
        calls=[]
        def run(target,command,environment,timeout):
            revision=environment['GENEALOGY_REVISION']
            calls.append((target,revision))
            result=self.runner(target,command,environment,timeout)
            result['canonical_revision']=revision
            return result
        worker=PublicationWorker(self.store,self.config,run)
        worker.run_once();worker.run_once()
        self.assertFalse(worker.run_once())
        self.assertIn(('books',newer),calls)
        self.assertNotIn(('geni',newer),calls)
        self.store.publication_result(self.revision,'geni','ready','Exact prior profile readback verified')
        self.assertTrue(worker.run_once())
        self.assertIn(('geni',newer),calls)
