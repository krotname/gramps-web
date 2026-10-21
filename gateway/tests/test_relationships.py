"""Neutral relationships, direction, privacy boundaries, and unchanged facts."""
import copy
import unittest
from types import SimpleNamespace
from gateway.relationships import NeutralCalculator


class Person:
    def __init__(self, handle, gender=1):
        self.handle, self.gender = handle, gender

    def get_gender(self):
        return self.gender


class Ref:
    def __init__(self, ref, father=6, mother=6):
        self.ref, self.father, self.mother = ref, father, mother

    def get_father_relation(self):
        return self.father

    def get_mother_relation(self):
        return self.mother


class Family:
    def __init__(self, father, mother, *children):
        self.father, self.mother = father, mother
        self.children = [c if isinstance(c, Ref) else Ref(c) for c in children]

    def get_father_handle(self):
        return self.father

    def get_mother_handle(self):
        return self.mother

    def get_child_ref_list(self):
        return self.children


class DB:
    def __init__(self, people, families):
        self.people = {p.handle: p for p in people}
        self.families = families

    def iter_people(self):
        return iter(self.people.values())

    def iter_families(self):
        return iter(self.families)


class Native:
    def set_depth(self, depth):
        pass

    def get_one_relationship(self, *args, **kwargs):
        return ("", -1, -1)

    def get_all_relationships(self, *args, **kwargs):
        return [], []


class TestRelationships(unittest.TestCase):
    def test_siblings_without_named_parents(self):
        db = DB([Person('child', 0), Person('father'), Person('uncle'),
                 Person('cousin', 0)], [Family('father', '', 'child'),
                                       Family('', '', 'father', 'uncle'),
                                       Family('uncle', '', 'cousin')])
        calc = NeutralCalculator(Native(), SimpleNamespace(language=['ru']))
        self.assertEqual(calc.get_one_relationship(db, db.people['father'], db.people['uncle']), 'брат')
        self.assertEqual(calc.get_one_relationship(db, db.people['child'], db.people['uncle']), 'брат отца')
        self.assertEqual(calc.get_one_relationship(db, db.people['child'], db.people['cousin']), 'дочь брата отца')

    def setUp(self):
        self.db = DB([Person(p, 0 if p in {"eva", "mother", "grandmother", "sister", "wife"} else 1)
                      for p in ("eva", "father", "mother", "brother", "sister", "grandfather",
                                "grandmother", "great", "greatgreat", "common", "uncle",
                                "artem", "cousin", "wife", "child", "stranger")], [
            Family("father", "mother", "eva", "brother", "sister"),
            Family("grandfather", "grandmother", "father", "uncle"),
            Family("great", "", "grandmother"),
            Family("greatgreat", "", "great"),
            Family("common", "", "greatgreat", "artem"),
            Family("uncle", "", "cousin"),
            Family("brother", "wife", "child"),
        ])
        self.calc = NeutralCalculator(Native(), SimpleNamespace(language=["ru"]))
        self.calc.set_depth(20)

    def relation(self, origin, other, extra=False):
        return self.calc.get_one_relationship(self.db, self.db.people[origin],
                                              self.db.people[other], extra_info=extra)

    def test_reference_and_direct_ancestors_both_directions(self):
        for target, expected in [("eva", "сама персона"), ("father", "отец"),
                                 ("mother", "мать"), ("grandfather", "дедушка"),
                                 ("great", "прадед"), ("greatgreat", "прапрадед")]:
            with self.subTest(target=target):
                self.assertEqual(self.relation("eva", target), expected)
        self.assertEqual(self.relation("father", "eva"), "дочь")
        self.assertEqual(self.relation("greatgreat", "eva"), "праправнучка")

    def test_collateral_relatives_and_reverse(self):
        self.assertEqual(self.relation("eva", "brother"), "брат")
        self.assertEqual(self.relation("eva", "sister"), "сестра")
        self.assertEqual(self.relation("eva", "uncle"), "дядя")
        self.assertEqual(self.relation("eva", "cousin"), "двоюродный брат")
        self.assertEqual(self.relation("uncle", "eva"), "племянница")
        self.assertEqual(self.relation("eva", "artem", True), ("брат прапрадеда", 5, 1))
        self.assertEqual(self.relation("artem", "eva"), "праправнучка брата")
        self.assertEqual(self.relation("father", "artem"), "брат прадеда")

    def test_union_and_affinity(self):
        self.assertEqual(self.relation("brother", "wife"), "жена")
        self.assertEqual(self.relation("wife", "brother"), "муж")
        self.assertEqual(self.relation("eva", "wife"), "жена брата")
        self.assertEqual(self.relation("wife", "eva"), "сестра мужа")

    def test_disconnected_and_depth_limit(self):
        self.assertEqual(self.relation("eva", "stranger", True), ("Связь в дереве не найдена", -1, -1))
        self.calc.set_depth(2)
        self.assertEqual(self.relation("eva", "artem"), "Связь в дереве не найдена")

    def test_unknown_not_changed_and_siblings_not_asserted_full_or_half(self):
        before = copy.deepcopy([[vars(ref) for ref in f.children] for f in self.db.families])
        for person in self.db.people:
            self.relation("eva", person)
        self.assertEqual(before, [[vars(ref) for ref in f.children] for f in self.db.families])
        self.assertEqual(self.relation("greatgreat", "artem"), "брат")

    def test_all_relationships_has_real_common_handles_and_no_redundant_ancestors(self):
        labels, ancestors = self.calc.get_all_relationships(self.db, self.db.people["eva"],
                                                           self.db.people["brother"])
        self.assertEqual(labels, ["брат"])
        self.assertEqual(ancestors, [["father", "mother"]])

    def test_private_missing_parent_not_used(self):
        del self.db.people["common"]
        self.assertEqual(self.relation("eva", "artem"), "Связь в дереве не найдена")

    def test_explicit_adoption_is_delegated_even_with_alternative_union_path(self):
        self.db.families[0].children[0].father = 1
        class AdoptionNative(Native):
            def get_one_relationship(self, *args, **kwargs):
                return "приёмный отец", 1, 0
        self.calc.native = AdoptionNative()
        self.assertEqual(self.relation("eva", "father"), "приёмный отец")

    def test_unknown_gender_and_cycles_terminate(self):
        self.db.people["father"].gender = 2
        self.assertEqual(self.relation("eva", "father"), "родитель")
        self.db.families.append(Family("eva", "", "common"))
        self.assertTrue(self.relation("eva", "artem"))


if __name__ == "__main__":
    unittest.main()
