"""Approved public teaching snapshots. Never reads the private deployment."""
from .projection import value


def public_snapshot(tree):
    def visible(node):
        return value(node, "RESN").lower() not in {"privacy", "confidential", "locked"}

    sources = {key: {"id": key, "title": value(node, "TITL"), "author": value(node, "AUTH"),
                     "notes": [note.content() for note in node.all("NOTE")]}
               for key, node in tree.records.items() if node.tag == "SOUR" and visible(node)}
    people = []
    for key, node in tree.people.items():
        if not visible(node):
            continue
        people.append({"id": key, "name": value(node, "NAME").replace("/", ""),
                       "notes": [note.content() for note in node.all("NOTE")],
                       "sources": [source.content().strip("@") for source in node.all("SOUR")
                                   if source.content().strip("@") in sources]})
    return {"title": "Учебное дерево — вымышленные данные", "people": people, "sources": list(sources.values())}
