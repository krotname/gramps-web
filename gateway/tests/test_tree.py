import tempfile
import unittest
from pathlib import Path

from gateway.tree import Gedcom, Conflict, InvalidGedcom, ged_date
from gateway.store import Store


FIXTURE = ("\ufeff0 HEAD\r\n1 GEDC\r\n2 VERS 5.5.1\r\n1 CHAR UTF-8\r\n"
           "0 @I1@ INDI\r\n1 NAME Anna /Example/\r\n2 _LOCAL untouched\r\n"
           "1 BIRT\r\n2 DATE ABT 1900\r\n2 SOUR Original evidence\r\n"
           "1 FAMS @F1@\r\n1 _URL https://example.org/person/1\r\n"
           "0 @I2@ INDI\r\n1 NAME Bob /Example/\r\n1 FAMC @F1@\r\n"
           "2 PEDI adopted\r\n0 @F1@ FAM\r\n1 WIFE @I1@\r\n"
           "1 CHIL @I2@\r\n0 TRLR\r\n").encode('utf-8')


class TreeTest(unittest.TestCase):
    def test_non_utf8_declaration_is_rejected_before_edits(self):
        for declared in (b'ANSEL', b'ASCII', b'UNICODE'):
            with self.assertRaises(InvalidGedcom):
                Gedcom(FIXTURE.replace(b'CHAR UTF-8',b'CHAR '+declared))
        with self.assertRaises(InvalidGedcom):
            Gedcom(FIXTURE.replace(b'1 CHAR UTF-8\r\n',b''))

    def test_unchanged_roundtrip_preserves_extensions_and_bytes(self):
        self.assertEqual(Gedcom(FIXTURE).dump(), FIXTURE)

    def test_date_change_keeps_evidence_and_untouched_name_extension(self):
        tree = Gedcom(FIXTURE)
        tree.apply({'op': 'person', 'xref': 'I1', 'birth': {'date': '1899-03-14'}})
        out = tree.dump().decode('utf-8')
        self.assertIn('2 DATE 14 MAR 1899\r\n2 SOUR Original evidence', out)
        self.assertIn('2 _LOCAL untouched', out)
        self.assertIn('1 _URL https://example.org/person/1', out)

    def test_place_change_supersedes_structured_old_address(self):
        data=FIXTURE.replace(b"2 DATE ABT 1900",b"2 ADDR\r\n3 CITY Old city\r\n2 DATE ABT 1900")
        tree=Gedcom(data)
        tree.apply({"op":"person","xref":"I1","birth":{"place":"New city"}})
        self.assertNotIn(b"Old city",tree.dump())
        self.assertIn(b"2 SOUR Original evidence",tree.dump())

    def test_name_edit_cannot_retain_stale_structured_name(self):
        data = FIXTURE.replace(b"2 _LOCAL untouched", b"2 GIVN Anna\r\n2 SURN Example\r\n2 _LOCAL untouched")
        tree = Gedcom(data)
        tree.apply({"op": "person", "xref": "I1", "name": "Anne /New/"})
        name = tree.people["I1"].first("NAME")
        self.assertIsNone(name.first("GIVN"))
        self.assertIsNone(name.first("SURN"))
        self.assertIn("2 _LOCAL untouched", name.render())

    def test_clearing_place_removes_old_structured_address(self):
        data = FIXTURE.replace(b"2 DATE ABT 1900", b"2 ADDR\r\n3 CITY Old city\r\n2 DATE ABT 1900")
        tree = Gedcom(data)
        tree.apply({"op": "person", "xref": "I1", "clear": ["birth.place"]})
        self.assertNotIn(b"Old city", tree.dump())
        self.assertIn(b"2 SOUR Original evidence", tree.dump())

    def test_family_edit_updates_both_sides_and_removes_parentage_details(self):
        tree = Gedcom(FIXTURE)
        tree.apply({'op': 'person', 'xref': 'I3', 'create': True, 'name': 'Cora /Example/'})
        tree.apply({'op': 'family', 'xref': 'F1', 'parents': ['I1'], 'children': ['I3']})
        tree.neutralize()
        tree.validate()
        self.assertNotIn('FAMC', tree.records['I2'].render())
        self.assertIn('FAMC @F1@', tree.records['I3'].render())
        self.assertNotIn('PEDI', tree.dump().decode('utf-8'))

    def test_adding_father_preserves_existing_wife_role(self):
        tree = Gedcom(FIXTURE)
        tree.apply({"op": "person", "xref": "I3", "create": True, "name": "Carl /Example/"})
        tree.apply({"op": "family", "xref": "F1", "parents": ["I1", "I3"]})
        self.assertEqual(tree.families["F1"].first("WIFE").value, "@I1@")
        self.assertEqual(tree.families["F1"].first("HUSB").value, "@I3@")
        tree.validate()

    def test_explicit_parent_roles_and_backlinks(self):
        tree = Gedcom(FIXTURE)
        tree.apply({"op": "person", "xref": "I3", "create": True, "name": "Carl /Example/"})
        tree.apply({"op": "family", "xref": "F1", "parent_roles": {"HUSB": "I3", "WIFE": "I1"}})
        self.assertEqual(tree.families["F1"].first("WIFE").value, "@I1@")
        self.assertEqual(tree.families["F1"].first("HUSB").value, "@I3@")
        tree.validate()
        tree.apply({"op": "family", "xref": "F1", "parent_roles": {"WIFE": "I1"}})
        self.assertIsNone(tree.families["F1"].first("HUSB"))
        self.assertIsNone(tree.people["I3"].first("FAMS"))
        tree.validate()

    def test_ambiguous_new_parent_roles_require_explicit_selection(self):
        tree = Gedcom(FIXTURE)
        with self.assertRaises(InvalidGedcom):
            tree.apply({"op": "family", "xref": "F2", "create": True, "parents": ["I1"]})

    def test_invalid_calendar_days_and_qualifiers_are_rejected(self):
        for date in ["99 DEC 1900", "31 FEB 1900", "29 FEB 1900", "BET 1900", "FROM 1900 AND 2000", "ABT 1900 TO 2000", "1900 AND 2000", "0000"]:
            with self.subTest(date=date), self.assertRaises(InvalidGedcom):
                ged_date(date)

    def test_valid_partial_range_and_period_dates(self):
        for date in ["1900", "FEB 1900", "29 FEB 2000", "ABT 1900", "BET 1900 AND 1902", "FROM 1900", "TO 1900", "FROM 1900 TO 1902"]:
            with self.subTest(date=date):
                self.assertEqual(ged_date(date), date)

    def test_unknown_parent_is_rejected(self):
        tree = Gedcom(FIXTURE)
        with self.assertRaises(InvalidGedcom):
            tree.apply({'op': 'family', 'xref': 'F1', 'parents': ['I404']})

    def test_repeated_note_is_not_duplicated(self):
        tree = Gedcom(FIXTURE)
        op = {'op': 'person', 'xref': 'I1', 'append_notes': ['A verified note\nSecond line']}
        tree.apply(op)
        tree.apply(op)
        self.assertEqual(tree.dump().decode('utf-8').count('A verified note'), 1)

    def test_merge_fresh_boundary_records_without_stale_fallback(self):
        left = Gedcom(FIXTURE.replace(b'1 FAMS @F1@\r\n', b'').replace(
            b'0 @I2@ INDI\r\n1 NAME Bob /Example/\r\n1 FAMC @F1@\r\n2 PEDI adopted\r\n'
            b'0 @F1@ FAM\r\n1 WIFE @I1@\r\n1 CHIL @I2@\r\n', b''))
        left.merge(Gedcom(FIXTURE))
        left.validate()
        self.assertEqual(set(left.people), {'I1', 'I2'})

    def test_merge_conflicting_name_is_blocked(self):
        with self.assertRaises(Conflict):
            Gedcom(FIXTURE).merge(Gedcom(FIXTURE.replace(b'Anna /Example/', b'Anna /Different/')))


class StoreTest(unittest.TestCase):
    def test_initial_projection_blocks_edit_until_it_is_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.initialize(FIXTURE)
            self.assertEqual(store.status()["projection"], "pending")
            with self.assertRaises(Conflict):
                store.commit(store.revision, "premature", [{"op": "person", "xref": "I1", "sex": "F"}], lambda *_: None)
            store.retry(lambda *_: None)
            self.assertEqual(store.status()["projection"], "ready")

    def test_commit_precedes_projection_failure_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.initialize(FIXTURE)
            store.retry(lambda *_: None)
            before = store.revision
            operation = {'op': 'person', 'xref': 'I1', 'birth': {'date': '1899'}}
            def failing_projection(*_):
                self.assertIn(b'2 DATE 1899', store.file.read_bytes())
                raise RuntimeError('Projection offline')
            result = store.commit(before, 'test-key', [operation], failing_projection)
            self.assertEqual(result['projection'], 'pending')
            self.assertNotEqual(before, store.revision)
            repeat = store.commit(before, 'test-key', [operation], failing_projection)
            self.assertEqual(result['revision'], repeat['revision'])
            with self.assertRaises(Conflict):
                store.commit(before, 'other-key', [operation], lambda *_: None)

    def test_idempotency_key_cannot_be_reused_for_different_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder))
            store.initialize(FIXTURE)
            store.retry(lambda *_: None)
            rev = store.revision
            store.commit(rev, 'key', [{'op': 'person', 'xref': 'I1', 'sex': 'F'}], lambda *_: None)
            with self.assertRaises(Conflict):
                store.commit(rev, 'key', [{'op': 'person', 'xref': 'I1', 'sex': 'M'}], lambda *_: None)


if __name__ == '__main__':
    unittest.main()
