import tempfile
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from gateway.learning import public_snapshot
from gateway.learning_sessions import Sessions, SessionDenied
from gateway.store import Store
from gateway.tree import Conflict, Gedcom


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.calls = []
        self.state = dict(active=True, sub="student-a", client_id="gramps", groups=["students"])
        self.manager = Sessions(Path(self.folder.name) / "sessions.sqlite", "https://auth.example/realms/test", "gramps", "test-only", self.transport)
        self.sid = self.manager.create("student-a", "local-a", 3, dict(access_token="first", refresh_token="refresh-first", expires_at=0))

    def transport(self, endpoint, form):
        self.calls.append((endpoint, form))
        if endpoint == "token":
            return dict(access_token="second", refresh_token="refresh-second", expires_in=300)
        return self.state

    def test_refresh_is_persisted_and_subject_bound(self):
        self.assertEqual(self.manager.validate(self.sid, "local-a")["subject"], "student-a")
        self.manager.validate(self.sid, "local-a")
        self.assertEqual(sum(endpoint == "token" for endpoint, _ in self.calls), 1)
        with self.assertRaises(SessionDenied):
            self.manager.validate(self.sid, "local-b")

    def test_revoked_central_session_or_changed_membership_denies_native_token(self):
        for state in [dict(active=False), dict(self.state, sub="different"), dict(self.state, groups=[]), dict(self.state, groups=["curators"])]:
            self.state = state
            with self.assertRaises(SessionDenied):
                self.manager.validate(self.sid, "local-a")

    def test_provider_failure_and_issuer_change_fail_closed(self):
        def unavailable(*args):
            raise TimeoutError()
        self.manager.transport = unavailable
        with self.assertRaises(SessionDenied):
            self.manager.validate(self.sid, "local-a")
        with self.assertRaises(ValueError):
            Sessions(self.manager.path, "https://other.example", "gramps", "test-only")

    def test_introspections_do_not_serialize_requests_or_hold_sqlite_writer(self):
        self.manager.validate(self.sid, "local-a")
        barrier = threading.Barrier(4, timeout=3)
        def parallel(endpoint, form):
            self.assertEqual(endpoint, "token/introspect")
            barrier.wait()
            # Session creation is a write and must remain available during introspection.
            self.manager.create("student-a", "local-a", 3,
                                dict(access_token="new", refresh_token="new", expires_at=time.time()+300))
            return self.state
        self.manager.transport = parallel
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.manager.validate(self.sid, "local-a"), range(4)))
        self.assertEqual(len(results), 4)

    def test_parallel_refresh_rotates_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.manager.validate(self.sid, "local-a"), range(4)))
        self.assertEqual(sum(endpoint == "token" for endpoint, _ in self.calls), 1)


class SnapshotTest(unittest.TestCase):
    def test_nested_restrictions_and_referenced_notes_are_not_published(self):
        tree = Gedcom(("0 HEAD\n1 GEDC\n2 VERS 5.5.1\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Demo /Person/\n"
                       "1 NOTE public note\n1 NOTE hidden inline\n2 RESN privacy\n"
                       "1 NOTE @N1@\n1 NOTE @N2@\n1 SOUR @S1@\n2 RESN confidential\n"
                       "1 SOUR @S2@\n0 @N1@ NOTE hidden reference\n1 RESN confidential\n"
                       "0 @N2@ NOTE public reference\n0 @S1@ SOUR\n1 TITL Source one\n"
                       "1 NOTE hidden source note\n2 RESN privacy\n0 @S2@ SOUR\n1 TITL Source two\n0 TRLR\n").encode())
        result = public_snapshot(tree)
        self.assertEqual(result["people"][0]["notes"], ["public note", "public reference"])
        self.assertEqual(result["people"][0]["sources"], ["S2"])
        self.assertEqual(result["sources"][0]["notes"], [])

    def test_edit_audit_conflict_and_explicit_snapshot_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            seed = Path(__file__).parents[1] / "learning_assets/seed.ged"
            store.initialize(seed.read_bytes())
            store.retry(lambda *args: None)
            actor = dict(issuer="https://auth.example", subject="student-a", user_id="local-a", role=3)
            before = store.revision
            self.assertIsNone(store.learning_snapshot())
            store.publish_learning(before, {**actor, "role": 4}, public_snapshot)
            operations = [{"op": "person", "xref": "I001", "append_notes": ["Проверено на занятии"]}]
            result = store.commit(before, "edit-a", operations, lambda *args: None, actor=actor)
            self.assertEqual(store.learning_snapshot()["revision"], before)
            self.assertEqual(store.learning_audit()[0]["actor"]["subject"], "student-a")
            with self.assertRaises(Conflict):
                store.commit(before, "edit-b", operations, lambda *args: None, actor=actor)
            with self.assertRaises(Conflict):
                store.commit(before, "edit-a", operations, lambda *args: None, actor={**actor, "subject": "student-b"})
            store.publish_learning(result["revision"], {**actor, "role": 4}, public_snapshot)
            snapshot = store.learning_snapshot()
            self.assertEqual(snapshot["revision"], result["revision"])
            self.assertEqual(snapshot["people"][0]["id"], "I001")
            self.assertEqual(len(snapshot["sources"]), 3)


if __name__ == "__main__":
    unittest.main()
