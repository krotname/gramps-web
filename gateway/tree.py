"""Lossless GEDCOM 5.5.1 editing; unchanged lines retain their original spelling."""
import copy
import datetime
import re
from dataclasses import dataclass, field

class InvalidGedcom(ValueError):
    pass

class Conflict(ValueError):
    pass

LINE = re.compile(r"^(\d+) (?:(@[^@\s]+@) )?([A-Z0-9_]+)(?: (.*))?$")
XREF = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,120}$")
MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
NEUTRAL_REMOVE = {"PEDI", "ADOP", "_FREL", "_MREL"}

def text(value):
    if not isinstance(value, str) or "\x00" in value or "\r" in value:
        raise InvalidGedcom("Text must be a string without CR or NUL")
    if len(value) > 100000:
        raise InvalidGedcom("Text is too long")
    return value

def ged_date(value):
    value = text(value).strip()
    if not value:
        return ""
    iso = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", value)
    if iso:
        year, month, day = iso.groups()
        try:
            datetime.date(int(year), int(month or 1), int(day or 1))
        except ValueError as exc:
            raise InvalidGedcom("Invalid calendar date") from exc
        return (str(int(day)) + " " if day else "") + (MONTHS[int(month)-1] + " " if month else "") + year

    def calendar_date(date):
        match = re.fullmatch(r"(?:@#DGREGORIAN@ )?(?:(\d{1,2}) )?(?:(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC) )?(\d{3,4})", date)
        if not match:
            raise InvalidGedcom("Use an ISO date or a GEDCOM Gregorian date")
        day, month, year = match.groups()
        if day and not month:
            raise InvalidGedcom("A day requires a month")
        try:
            datetime.date(int(year), MONTHS.index(month) + 1 if month else 1, int(day or 1))
        except ValueError as exc:
            raise InvalidGedcom("Invalid calendar date") from exc

    if value.startswith("BET "):
        parts = value[4:].split(" AND ")
        if len(parts) != 2:
            raise InvalidGedcom("A date range requires BET date AND date")
    elif value.startswith("FROM "):
        parts = value[5:].split(" TO ")
        if len(parts) not in {1, 2}:
            raise InvalidGedcom("A date period requires FROM date TO date")
    else:
        single = re.sub(r"^(ABT|CAL|EST|BEF|AFT|TO) ", "", value)
        parts = [single]
    for date in parts:
        calendar_date(date)
    return value

@dataclass
class Node:
    level: int
    tag: str
    value: str = ""
    xref: str = ""
    raw: str | None = None
    children: list = field(default_factory=list)

    def lines(self):
        line = self.raw
        if line is None:
            line = f"{self.level} " + (f"@{self.xref}@ " if self.xref else "") + self.tag
            if self.value:
                line += " " + self.value
        return [line] + [line for child in self.children for line in child.lines()]

    def render(self):
        return "\n".join(self.lines())

    def first(self, tag):
        return next((n for n in self.children if n.tag == tag), None)

    def all(self, tag):
        return [n for n in self.children if n.tag == tag]

    def content(self):
        result = self.value
        for child in self.children:
            if child.tag == "CONT":
                result += "\n" + child.content()
            elif child.tag == "CONC":
                result += child.content()
        return result

    def remove(self, tag):
        self.children = [n for n in self.children if n.tag != tag]

    def set(self, tag, value):
        value = text(value)
        child = self.first(tag)
        if not value:
            self.remove(tag)
            return
        if child and child.content() == value:
            return
        if child is None:
            child = Node(self.level + 1, tag)
            self.children.append(child)
        child.value, child.raw = value.split("\n")[0], None
        child.children = [n for n in child.children if n.tag not in {"CONT", "CONC"}]
        # Split newly authored values into GEDCOM lines without splitting UTF-8.
        pieces = value.split("\n")
        first = pieces.pop(0)
        chunks = []
        while len(first.encode("utf-8")) > 240:
            n = 1
            while len(first[:n+1].encode("utf-8")) <= 240:
                n += 1
            chunks.append(first[:n])
            first = first[n:]
        chunks.append(first)
        child.value = chunks[0]
        child.children += [Node(child.level + 1, "CONC", v) for v in chunks[1:]]
        for piece in pieces:
            extra = Node(child.level + 1, "CONT")
            # CONT content may itself require continuation lines.
            remaining = piece
            chunks = []
            while len(remaining.encode("utf-8")) > 240:
                n = 1
                while len(remaining[:n+1].encode("utf-8")) <= 240:
                    n += 1
                chunks.append(remaining[:n])
                remaining = remaining[n:]
            chunks.append(remaining)
            extra.value = chunks[0]
            # CONC/CONT are peers under their original owner in GEDCOM.
            child.children.append(extra)
            child.children += [Node(child.level + 1, "CONC", v) for v in chunks[1:]]

    def append_unique(self, tag, value):
        value = text(value)
        if value and value not in [n.content() for n in self.all(tag)]:
            holder = Node(self.level, "_HOLDER")
            holder.set(tag, value)
            self.children += holder.children

class Gedcom:
    def __init__(self, data):
        self.bom = data.startswith(b"\xef\xbb\xbf")
        decoded = data.decode("utf-8-sig")
        self.newline = "\r\n" if "\r\n" in decoded else "\n"
        self.trailing = decoded.endswith("\n")
        self.roots = []
        self.records = {}
        stack = []
        for line in decoded.splitlines():
            match = LINE.fullmatch(line)
            if not match:
                raise InvalidGedcom("Malformed GEDCOM line")
            level, xref, tag, value = match.groups()
            node = Node(int(level), tag, value or "", (xref or "").strip("@"), line)
            while stack and stack[-1].level >= node.level:
                stack.pop()
            if node.level == 0:
                self.roots.append(node)
                if node.xref:
                    if node.xref in self.records:
                        raise InvalidGedcom("Duplicate XREF: " + node.xref)
                    self.records[node.xref] = node
            else:
                if not stack or stack[-1].level != node.level - 1:
                    raise InvalidGedcom("Invalid nesting level")
                stack[-1].children.append(node)
            stack.append(node)
        head = self.roots[0] if self.roots else None
        if not head or head.tag != "HEAD" or self.roots[-1].tag != "TRLR":
            raise InvalidGedcom("HEAD and TRLR are required")
        version = head.first("GEDC")
        if not version or not version.first("VERS") or version.first("VERS").value != "5.5.1":
            raise InvalidGedcom("Only stable GEDCOM 5.5.1 is accepted")
        if not head.first("CHAR") or head.first("CHAR").value != "UTF-8":
            raise InvalidGedcom("GEDCOM must declare CHAR UTF-8")

    @property
    def people(self):
        return {k: v for k, v in self.records.items() if v.tag == "INDI"}

    @property
    def families(self):
        return {k: v for k, v in self.records.items() if v.tag == "FAM"}

    def dump(self):
        s = self.newline.join(line for root in self.roots for line in root.lines())
        return (("\ufeff" if self.bom else "") + s + (self.newline if self.trailing else "")).encode("utf-8")

    def add(self, xref, tag):
        if not XREF.fullmatch(xref) or xref in self.records:
            raise InvalidGedcom("Invalid or existing XREF")
        node = Node(0, tag, xref=xref)
        self.records[xref] = node
        self.roots.insert(-1, node)
        return node

    def neutralize(self):
        def clean(node):
            node.children = [n for n in node.children if n.tag not in NEUTRAL_REMOVE]
            for child in node.children:
                clean(child)
        for root in self.roots:
            clean(root)

    def validate(self):
        for node in self.records.values():
            def refs(n):
                if re.fullmatch(r"@[^@\s]+@", n.value) and n.value.strip("@") not in self.records:
                    raise InvalidGedcom("Dangling reference: " + n.value)
                for c in n.children:
                    refs(c)
            refs(node)
        for fid, family in self.families.items():
            parents = [n.value.strip("@") for n in family.children if n.tag in {"HUSB", "WIFE"}]
            children = [n.value.strip("@") for n in family.all("CHIL")]
            if len(parents) != len(set(parents)) or len(children) != len(set(children)):
                raise InvalidGedcom("Duplicate family member: " + fid)
            for pid, tag in [(p, "FAMS") for p in parents] + [(p, "FAMC") for p in children]:
                if pid not in self.people or f"@{fid}@" not in [n.value for n in self.people[pid].all(tag)]:
                    raise InvalidGedcom("Family backlink mismatch: " + fid + "/" + pid)
            if set(parents) & set(children):
                raise InvalidGedcom("A parent cannot be their own child")
        for pid, person in self.people.items():
            for tag, roles in [("FAMS", {"HUSB", "WIFE"}), ("FAMC", {"CHIL"})]:
                for n in person.all(tag):
                    family = self.families.get(n.value.strip("@"))
                    if family is None or f"@{pid}@" not in [c.value for c in family.children if c.tag in roles]:
                        raise InvalidGedcom("Person backlink mismatch: " + pid)
        parents_of = {}
        for family in self.families.values():
            parents = [n.value.strip("@") for n in family.children if n.tag in {"HUSB", "WIFE"}]
            for child in family.all("CHIL"):
                parents_of.setdefault(child.value.strip("@"), set()).update(parents)
        def visit(pid, visiting, done):
            if pid in visiting:
                raise InvalidGedcom("Ancestry cycle")
            if pid in done:
                return
            visiting.add(pid)
            for parent in parents_of.get(pid, ()):
                visit(parent, visiting, done)
            visiting.remove(pid)
            done.add(pid)
        done = set()
        for pid in self.people:
            visit(pid, set(), done)

    def merge(self, other):
        for xref, incoming in other.records.items():
            old = self.records.get(xref)
            if old is None:
                node = copy.deepcopy(incoming)
                self.records[xref] = node
                self.roots.insert(-1, node)
                continue
            if old.tag != incoming.tag:
                raise Conflict("Record type differs: " + xref)
            links = {"FAMS", "FAMC"} if old.tag == "INDI" else {"HUSB", "WIFE", "CHIL"} if old.tag == "FAM" else set()
            def signature(n):
                return [line for child in n.children if child.tag not in links for line in child.lines()]
            if signature(old) != signature(incoming):
                raise Conflict("Fresh exports disagree on fields: " + xref)
            for n in incoming.children:
                if n.tag in links:
                    if n.tag in {"HUSB", "WIFE"} and old.first(n.tag) and old.first(n.tag).value != n.value:
                        raise Conflict("Fresh exports disagree on parent: " + xref)
                    old.append_unique(n.tag, n.value)

    def event(self, node, tag, fields):
        if fields is None:
            node.remove(tag)
            return
        if not isinstance(fields, dict) or set(fields) - {"date", "place"}:
            raise InvalidGedcom("Only event date and place can be changed")
        child = node.first(tag)
        if child is None:
            child = Node(1, tag)
            node.children.append(child)
        if "date" in fields:
            child.set("DATE", ged_date(fields["date"]))
        if "place" in fields:
            # An explicitly changed place supersedes structured Geni address fields.
            child.children = [n for n in child.children if n.tag not in {"ADDR", "CITY", "STAE", "CTRY", "ADR1", "POST"}]
            child.set("PLAC", text(fields["place"]))

    def family_backlinks(self, xref, members, tag):
        for pid, person in self.people.items():
            if pid in members:
                person.append_unique(tag, f"@{xref}@")
            else:
                person.children = [n for n in person.children if not (n.tag == tag and n.value == f"@{xref}@")]

    def apply(self, op):
        if not isinstance(op, dict):
            raise InvalidGedcom("Operation must be an object")
        kind, xref = op.get("op"), op.get("xref", "")
        allowed = {"op", "xref", "create", "name", "sex", "given", "surname", "occupation",
                   "birth", "death", "baptism", "burial", "append_names", "append_notes",
                   "append_sources", "married_names", "nicknames", "clear"}
        if kind == "person":
            if set(op) - allowed:
                raise InvalidGedcom("Unsupported person field")
            node = self.add(xref, "INDI") if op.get("create") else self.people.get(xref)
            if node is None:
                raise InvalidGedcom("Unknown person")
            if "name" in op:
                old_name = node.first("NAME")
                changed = old_name is None or old_name.content() != op["name"]
                node.set("NAME", text(op["name"]))
                updated_name = node.first("NAME")
                if changed and updated_name:
                    for field_name, tag in [("given", "GIVN"), ("surname", "SURN")]:
                        if field_name not in op:
                            updated_name.remove(tag)
            for field_name, tag in [("sex", "SEX"), ("occupation", "OCCU")]:
                if field_name in op:
                    if tag == "SEX" and op[field_name] not in {"", "M", "F", "U"}:
                        raise InvalidGedcom("Invalid sex")
                    node.set(tag, text(op[field_name]))
            name = node.first("NAME")
            if name:
                for field_name, tag in [("given", "GIVN"), ("surname", "SURN")]:
                    if field_name in op:
                        name.set(tag, op[field_name])
                for field_name, tag in [("married_names", "_MARNM"), ("nicknames", "NICK")]:
                    for value in op.get(field_name, []):
                        name.append_unique(tag, value)
            for field_name, tag in [("birth", "BIRT"), ("death", "DEAT"), ("baptism", "BAPM"), ("burial", "BURI")]:
                if field_name in op:
                    self.event(node, tag, op[field_name])
            for field_name, tag in [("append_names", "NAME"), ("append_notes", "NOTE"), ("append_sources", "SOUR")]:
                for value in op.get(field_name, []):
                    node.append_unique(tag, value)
            for field_name in op.get("clear", []):
                tags = {"occupation": "OCCU", "notes": "NOTE", "death": "DEAT"}
                if field_name in tags:
                    node.remove(tags[field_name])
                elif field_name in {"birth.place", "death.place"}:
                    ev = node.first("BIRT" if field_name.startswith("birth") else "DEAT")
                    if ev:
                        self.event(node, ev.tag, {"place": ""})
                else:
                    raise InvalidGedcom("Unsupported clear field")
        elif kind == "family":
            if set(op) - {"op", "xref", "create", "parents", "parent_roles", "children", "marriage", "divorce", "append_notes", "append_sources"}:
                raise InvalidGedcom("Unsupported family field")
            node = self.add(xref, "FAM") if op.get("create") else self.families.get(xref)
            if node is None:
                raise InvalidGedcom("Unknown family")
            if "parents" in op and "parent_roles" in op:
                raise InvalidGedcom("Choose either parents or explicit parent roles")
            roles = None
            if "parent_roles" in op:
                roles = op["parent_roles"]
                if not isinstance(roles, dict) or set(roles) - {"HUSB", "WIFE"}:
                    raise InvalidGedcom("Invalid parent role")
            elif "parents" in op:
                members = op["parents"]
                if not isinstance(members, list) or any(not isinstance(p, str) or p not in self.people for p in members) or len(members) != len(set(members)) or len(members) > 2:
                    raise InvalidGedcom("Unknown or duplicate parent")
                roles = {tag: node.first(tag).value.strip("@") for tag in ["HUSB", "WIFE"] if node.first(tag) and node.first(tag).value.strip("@") in members}
                new_members = [p for p in members if p not in roles.values()]
                free = [tag for tag in ["HUSB", "WIFE"] if tag not in roles]
                if new_members and (len(new_members) != 1 or len(free) != 1):
                    raise InvalidGedcom("Select explicit roles for new parents")
                roles.update(zip(free, new_members))
            if roles is not None:
                members = list(roles.values())
                if any(not isinstance(p, str) or p not in self.people for p in members) or len(members) != len(set(members)):
                    raise InvalidGedcom("Unknown or duplicate parent")
                for tag in ["HUSB", "WIFE"]:
                    old = node.first(tag)
                    pid = roles.get(tag)
                    if old and old.value == f"@{pid}@":
                        continue
                    node.remove(tag)
                    if pid:
                        node.append_unique(tag, f"@{pid}@")
                self.family_backlinks(xref, members, "FAMS")
            if "children" in op:
                members = op["children"]
                if not isinstance(members, list) or any(not isinstance(p, str) or p not in self.people for p in members) or len(members) != len(set(members)):
                    raise InvalidGedcom("Unknown or duplicate family member")
                existing = {n.value.strip("@"): n for n in node.all("CHIL")}
                node.remove("CHIL")
                node.children += [existing.get(pid) or Node(node.level + 1, "CHIL", f"@{pid}@") for pid in members]
                self.family_backlinks(xref, members, "FAMC")
            for field_name, tag in [("marriage", "MARR"), ("divorce", "DIV")]:
                if field_name in op:
                    self.event(node, tag, op[field_name])
            for value in op.get("append_notes", []):
                node.append_unique("NOTE", value)
            for value in op.get("append_sources", []):
                node.append_unique("SOUR", value)
        else:
            raise InvalidGedcom("Unsupported operation")
        return xref
