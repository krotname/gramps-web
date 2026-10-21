"""Describe neutral family links without interpreting Unknown as non-birth.

This is a read-only presentation adapter for the canonical GEDCOM projection.
Neither Unknown nor an ordinary kinship noun is evidence of biological parentage,
full siblings, adoption, or a legally registered marriage. Explicit non-birth
links are left to Gramps. All reads use the caller's privacy-filtered database.
"""
from collections import deque


def _word(gender, male, female, unknown):
    return (female if gender == 0 else male if gender == 1 else unknown)


def _lineage(generations, gender, ancestor=True):
    if ancestor:
        forms = [
            (("отец", "отца"), ("мать", "матери"), ("родитель", "родителя")),
            (("дедушка", "дедушки"), ("бабушка", "бабушки"), ("предок", "предка")),
            (("прадед", "прадеда"), ("прабабушка", "прабабушки"), ("предок", "предка")),
            (("прапрадед", "прапрадеда"), ("прапрабабушка", "прапрабабушки"), ("предок", "предка")),
        ]
    else:
        forms = [
            (("сын", "сына"), ("дочь", "дочери"), ("ребёнок", "ребёнка")),
            (("внук", "внука"), ("внучка", "внучки"), ("потомок", "потомка")),
            (("правнук", "правнука"), ("правнучка", "правнучки"), ("потомок", "потомка")),
            (("праправнук", "праправнука"), ("праправнучка", "праправнучки"), ("потомок", "потомка")),
        ]
    if generations <= 4:
        return _word(gender, *forms[generations - 1])
    noun = ("предок", "предка") if ancestor else ("потомок", "потомка")
    return tuple(f"{word} в {generations}-м поколении" for word in noun)


def _sibling(gender, degree=0):
    noun = _word(gender, ("брат", "брата"), ("сестра", "сестры"),
                 ("брат или сестра", "брата или сестры"))
    if not degree:
        return noun
    stems = {1: "двоюродн", 2: "троюродн", 3: "четвероюродн", 4: "пятиюродн"}
    if degree not in stems or gender not in (0, 1):
        return (f"родственник {degree}-й степени кузенства",
                f"родственника {degree}-й степени кузенства")
    stem = stems[degree]
    return (stem + ("ая " if gender == 0 else "ый ") + noun[0],
            stem + ("ой " if gender == 0 else "ого ") + noun[1])


def _compose(outer, inner):
    return outer[0] + " " + inner[1], outer[1] + " " + inner[1]


class FamilyGraph:
    def __init__(self, db):
        self.people = {p.handle: p for p in db.iter_people()}
        self.edges = {h: [] for h in self.people}
        self.explicit_pairs = set()
        for family in db.iter_families():
            parents = [family.get_father_handle(), family.get_mother_handle()]
            father, mother = parents
            if not any(parents):
                # A family can record siblings before either parent is named.
                # Preserve that visible link without inventing a parent record.
                siblings = [ref.ref for ref in family.get_child_ref_list()
                            if ref.ref in self.people and
                            int(ref.get_father_relation()) in (0, 6) and
                            int(ref.get_mother_relation()) in (0, 6)]
                for sibling in siblings:
                    self.edges[sibling].extend((other, "B") for other in siblings
                                               if other != sibling)
            if father in self.people and mother in self.people and father != mother:
                self.edges[father].append((mother, "S"))
                self.edges[mother].append((father, "S"))
            for ref in family.get_child_ref_list():
                if ref.ref not in self.people:
                    continue
                for parent, relation in zip(parents, [ref.get_father_relation(), ref.get_mother_relation()]):
                    if parent in self.people and int(relation) not in (0, 6):
                        self.explicit_pairs.add(frozenset((parent, ref.ref)))
                    # ChildRefType: Birth=0, Unknown=6. Never modify the objects.
                    if parent in self.people and parent != ref.ref and int(relation) in (0, 6):
                        self.edges[ref.ref].append((parent, "U"))
                        self.edges[parent].append((ref.ref, "D"))
        for edges in self.edges.values():
            edges.sort()

    def gender(self, handle):
        return self.people[handle].get_gender()

    def ancestors(self, start, depth):
        found = {start: (start,)}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            path = found[current]
            if len(path) > depth:
                continue
            for parent, kind in self.edges[current]:
                if kind == "U" and parent not in found:
                    found[parent] = path + (parent,)
                    queue.append(parent)
        return found

    def common_paths(self, origin, other, depth):
        left, right = self.ancestors(origin, depth), self.ancestors(other, depth)
        common = left.keys() & right.keys()
        # Ancestors above an already shared ancestor are redundant. Keep
        # independent branches (including pedigree collapse) as separate entries.
        nearest = [h for h in common if not
                   ((set(left[h][:-1]) | set(right[h][:-1])) & common)]
        return sorted([(left[h], right[h]) for h in nearest],
                      key=lambda pair: (len(pair[0]) + len(pair[1]),
                                        max(len(pair[0]), len(pair[1])), pair))

    def shortest_path(self, origin, other, depth):
        queue = deque([(origin, (origin,), "")])
        seen = {origin}
        while queue:
            current, path, kinds = queue.popleft()
            if current == other:
                return path, kinds
            if len(kinds) >= 2 * depth:
                continue
            for target, kind in self.edges[current]:
                if target not in seen:
                    seen.add(target)
                    queue.append((target, path + (target,), kinds + kind))
        return None

    def kinship(self, left, right):
        up, down = len(left) - 1, len(right) - 1
        gender = self.gender(right[0])
        if not down:
            return _lineage(up, gender)
        if not up:
            return _lineage(down, gender, False)
        if up == down:
            return _sibling(gender, up - 1)
        if (up, down) == (2, 1):
            return _word(gender, ("дядя", "дяди"), ("тётя", "тёти"),
                         ("брат или сестра родителя", "брата или сестры родителя"))
        if (up, down) == (1, 2):
            return _word(gender, ("племянник", "племянника"), ("племянница", "племянницы"),
                         ("ребёнок брата или сестры", "ребёнка брата или сестры"))
        if up > down:
            return _compose(_sibling(gender, down - 1),
                            _lineage(up - down, self.gender(left[up - down])))
        return _compose(_lineage(down - up, gender, False),
                        _sibling(self.gender(right[down - up]), up - 1))

    def describe_path(self, path, kinds):
        # Collapse each U*D* segment into a kinship noun; join segments through
        # family partners. The fallback also handles co-parents through a child.
        pieces, index = [], 0
        while index < len(kinds):
            if kinds[index] == "B":
                pieces.append(_sibling(self.gender(path[index + 1])))
                index += 1
                continue
            if kinds[index] == "S":
                pieces.append(_word(self.gender(path[index + 1]),
                                    ("муж", "мужа"), ("жена", "жены"),
                                    ("партнёр по семье", "партнёра по семье")))
                index += 1
                continue
            start = index
            while index < len(kinds) and kinds[index] == "U":
                index += 1
            turn = index
            while index < len(kinds) and kinds[index] == "D":
                index += 1
            left = path[start:turn + 1]
            right = tuple(reversed(path[turn:index + 1]))
            pieces.append(self.kinship(left, right))
        result = pieces[0]
        for piece in pieces[1:]:
            result = _compose(piece, result)
        return result[0]


class NeutralCalculator:
    def __init__(self, native, locale):
        self.native, self.locale, self.depth = native, locale, 15

    def __getattr__(self, name):
        return getattr(self.native, name)

    def set_depth(self, depth):
        self.depth = depth
        self.native.set_depth(depth)

    def _relations(self, db, person1, person2):
        graph = FamilyGraph(db)
        origin, other = person1.handle, person2.handle
        if origin == other:
            return [("сама персона", 0, 0, [origin])]
        if frozenset((origin, other)) in graph.explicit_pairs:
            return []
        common = graph.common_paths(origin, other, self.depth)
        if common:
            grouped = {}
            for left, right in common:
                key = (graph.kinship(left, right)[0], len(left) - 1, len(right) - 1)
                grouped.setdefault(key, []).append(left[-1])
            return [(*key, handles) for key, handles in grouped.items()]
        path = graph.shortest_path(origin, other, self.depth)
        if path:
            return [(graph.describe_path(*path), -1, -1, [])]
        return []

    def get_one_relationship(self, db, person1, person2, extra_info=False, **kwargs):
        rows = self._relations(db, person1, person2)
        if rows:
            result = rows[0][:3]
        else:
            result = self.native.get_one_relationship(db, person1, person2,
                                                     extra_info=True, **kwargs)
            if not result[0]:
                result = ("Связь в дереве не найдена", -1, -1)
        return result if extra_info else result[0]

    def get_all_relationships(self, db, person1, person2, **kwargs):
        rows = self._relations(db, person1, person2)
        if rows:
            return [row[0] for row in rows], [row[3] for row in rows]
        return self.native.get_all_relationships(db, person1, person2, **kwargs)


def install():
    """Wrap the API calculators after create_app has loaded its resources.

    Only the Russian presentation uses this adapter; other locale calculators
    and the Gramps data model are unchanged. No endpoint or permission changes.
    """
    from gramps.gen.relationship import get_relationship_calculator
    from gramps_webapi.api.resources import relations, timeline, util

    def factory(reinit=False, clocale=None):
        options = {"reinit": reinit}
        if clocale is not None:
            options["clocale"] = clocale
        native = get_relationship_calculator(**options)
        if clocale is not None and clocale.language[0].startswith("ru"):
            return NeutralCalculator(native, clocale)
        return native

    for module in (relations, timeline, util):
        module.get_relationship_calculator = factory
