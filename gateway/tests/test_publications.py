import tempfile, unittest
from gateway.store import Store
from gateway.tree import Conflict, InvalidGedcom
from gateway.tests.test_tree import FIXTURE

class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.store.initialize(FIXTURE)
        self.store.retry(lambda *_: None)
    def tearDown(self):self.tmp.cleanup()
    def test_frozen_revision_survives_a_later_edit(self):
        revision=self.store.revision
        self.store.publish(revision)
        frozen=self.store.publication_snapshot(revision)
        self.store.commit(revision,'next',[{'op':'person','xref':'I1','name':'Changed /Name/'}],lambda *_:None)
        self.assertEqual(self.store.publication_snapshot(revision),frozen)
        self.assertNotEqual(self.store.file.read_bytes(),frozen)
    def test_complete_requires_each_target_and_evidence(self):
        revision=self.store.revision
        self.store.publish(revision)
        with self.assertRaises(InvalidGedcom):self.store.publication_result(revision,'geni','ready','')
        self.store.publication_result(revision,'geni','ready','Profile readback URL verified')
        self.assertEqual(self.store.next_publication()['revision'],revision)
        self.store.publication_result(revision,'books','ready','Six PDF files validated and uploaded')
        self.assertIsNone(self.store.next_publication())
    def test_publication_cannot_be_completed_for_an_unknown_snapshot(self):
        with self.assertRaises(Conflict):self.store.publication_result('a'*64,'books','ready','Verified')
    def test_book_upload_rejects_a_path_and_non_pdf(self):
        revision=self.store.revision
        self.store.publish(revision)
        with self.assertRaises(InvalidGedcom):self.store.save_book(revision,'../private.pdf',b'%PDF-1.7')
        with self.assertRaises(InvalidGedcom):self.store.save_book(revision,'book.pdf',b'not a PDF')
    def test_pending_publications_are_not_discarded_when_queue_is_full(self):
        for number in range(10):
            revision=self.store.revision
            self.store.publish(revision)
            self.store.commit(revision,'change-'+str(number),[{'op':'person','xref':'I1','name':str(number)+' /Name/'}],lambda *_:None)
        with self.assertRaises(Conflict):self.store.publish(self.store.revision)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM publications').fetchone()[0],10)
