"""Approved public teaching snapshots. Never reads the private deployment."""
from .projection import value


def public_snapshot(tree):
    def visible(node):
        return not any(item.content().lower() in {"privacy", "confidential", "locked"}
                       for item in node.all("RESN"))

    def notes(node):
        result = []
        for note in node.all("NOTE"):
            if not visible(note):
                continue
            text = note.content()
            if text.startswith("@") and text.endswith("@"):
                referenced = tree.records.get(text[1:-1])
                if referenced is None or not visible(referenced):
                    continue
                text = referenced.content()
            result.append(text)
        return result

    sources = {key: {"id": key, "title": value(node, "TITL"), "author": value(node, "AUTH"),
                     "notes": notes(node)}
               for key, node in tree.records.items() if node.tag == "SOUR" and visible(node)}
    people = []
    for key, node in tree.people.items():
        if not visible(node):
            continue
        people.append({"id": key, "name": value(node, "NAME").replace("/", ""),
                       "notes": notes(node),
                       "sources": [source.content().strip("@") for source in node.all("SOUR")
                                   if visible(source) and source.content().strip("@") in sources]})
    return {"title": "Учебное дерево — вымышленные данные", "people": people, "sources": list(sources.values())}
