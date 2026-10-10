"""Durable canonical commits and a replayable projection outbox; no credentials."""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from .tree import Gedcom, Conflict, InvalidGedcom

class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        self.file = self.folder / "export-Forest.ged"
        self.lock = threading.RLock()
        self.db = self.folder / "journal.sqlite3"
        descriptor = os.open(self.db, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.chmod(self.db, 0o600)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS commits(
                key TEXT PRIMARY KEY, before_rev TEXT, revision TEXT,
                operations TEXT, candidate BLOB, projection TEXT, error TEXT DEFAULT '')""")
            db.execute("""CREATE TABLE IF NOT EXISTS publications(
                revision TEXT PRIMARY KEY, state TEXT, snapshot BLOB,
                books_state TEXT DEFAULT 'pending', geni_state TEXT DEFAULT 'pending',
                detail TEXT DEFAULT '')""")
        self.recover()

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.db, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def serialized(self):
        with self.lock, (self.folder / ".writer.lock").open("a") as stream:
            os.chmod(self.folder / ".writer.lock", 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    @property
    def revision(self):
        # An ETag identifies file content for optimistic concurrency.
        return hashlib.sha256(self.file.read_bytes()).hexdigest()

    def atomic(self, data):
        tmp = self.folder / ".candidate.ged"
        with tmp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.file)
        descriptor = os.open(self.folder, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def initialize(self, data):
        tree = Gedcom(data)
        tree.neutralize()
        tree.validate()
        with self.serialized():
            if self.file.exists():
                raise Conflict("Canonical GEDCOM already exists")
            data = tree.dump()
            revision = hashlib.sha256(data).hexdigest()
            with self.connection() as db:
                db.execute("INSERT INTO commits VALUES(?,?,?,?,?,?,?)",
                           ("bootstrap-" + revision, None, revision, "[]", data, "prepared", ""))
            self.atomic(data)
            with self.connection() as db:
                db.execute("UPDATE commits SET projection='pending' WHERE key=?", ("bootstrap-" + revision,))

    def recover(self):
        with self.serialized(), self.connection() as db:
            rows = db.execute("SELECT * FROM commits WHERE projection='prepared' ORDER BY rowid").fetchall()
            for row in rows:
                rev = self.revision if self.file.exists() else None
                if rev == row["before_rev"]:
                    self.atomic(row["candidate"])
                elif rev != row["revision"]:
                    raise Conflict("External edit conflicts with a prepared commit")
                db.execute("UPDATE commits SET projection='pending' WHERE key=?", (row["key"],))

    def status(self):
        with self.connection() as db:
            pending = db.execute("SELECT revision,error FROM commits WHERE projection IN ('prepared','pending') ORDER BY rowid").fetchall()
            published = db.execute("SELECT revision,state,books_state,geni_state,detail FROM publications ORDER BY rowid DESC LIMIT 10").fetchall()
        return {"revision": self.revision, "projection": "pending" if pending else "ready",
                "pending": [dict(r) for r in pending], "publications": [dict(r) for r in published]}

    def commit(self, expected, key, operations, project):
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise InvalidGedcom("An idempotency key is required")
        if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
            raise InvalidGedcom("Provide between one and 100 operations")
        payload = json.dumps(operations, ensure_ascii=False, sort_keys=True)
        with self.serialized():
            with self.connection() as db:
                old = db.execute("SELECT * FROM commits WHERE key=?", (key,)).fetchone()
                if old:
                    if old["operations"] != payload:
                        raise Conflict("Idempotency key belongs to a different edit")
                    return {"revision": old["revision"], "projection": old["projection"], "error": old["error"]}
                if expected != self.revision:
                    raise Conflict("The tree changed; reload before saving")
                if db.execute("SELECT 1 FROM commits WHERE projection IN ('prepared','pending')").fetchone():
                    raise Conflict("Repair the pending projection before another edit")
                tree = Gedcom(self.file.read_bytes())
                for operation in operations:
                    tree.apply(operation)
                tree.neutralize()
                tree.validate()
                data = tree.dump()
                revision = hashlib.sha256(data).hexdigest()
                db.execute("INSERT INTO commits VALUES(?,?,?,?,?,?,?)",
                           (key, expected, revision, payload, data, "prepared", ""))
            self.atomic(data)
            with self.connection() as db:
                db.execute("UPDATE commits SET projection='pending' WHERE key=?", (key,))
            result = self._project(key, tree, operations, project)
            with self.connection() as db:
                db.execute("DELETE FROM commits WHERE rowid NOT IN (SELECT rowid FROM commits ORDER BY rowid DESC LIMIT 1000) AND projection='ready'")
            return result

    def _project(self, key, tree, operations, project):
        phase, error = "ready", ""
        try:
            project(tree, operations)
        except Exception as exc:
            phase, error = "pending", type(exc).__name__ + ": " + str(exc)[:500]
        with self.connection() as db:
            db.execute("UPDATE commits SET projection=?,error=? WHERE key=?", (phase, error, key))
            row = db.execute("SELECT revision FROM commits WHERE key=?", (key,)).fetchone()
        return {"revision": row["revision"], "projection": phase, "error": error}

    def retry(self, project):
        with self.serialized(), self.connection() as db:
            row = db.execute("SELECT * FROM commits WHERE projection='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row:
                return self._project(row["key"], Gedcom(self.file.read_bytes()), json.loads(row["operations"]), project)
        return self.status()

    def publish(self, expected):
        with self.serialized(), self.connection() as db:
            if expected != self.revision:
                raise Conflict("The tree changed; reload before publishing")
            if not db.execute("SELECT 1 FROM publications WHERE revision=?", (expected,)).fetchone():
                count = db.execute("SELECT COUNT(*) FROM publications").fetchone()[0]
                if count >= 10:
                    manifest = self.folder / "books" / "manifest.json"
                    current = json.loads(manifest.read_text()).get("revision", "") if manifest.exists() else ""
                    old = db.execute("SELECT revision FROM publications WHERE state='ready' AND revision!=? ORDER BY rowid LIMIT 1", (current,)).fetchone()
                    if not old:
                        raise Conflict("Complete queued publications before adding another revision")
                    folder = self.folder / "books" / old["revision"]
                    if folder.is_dir():
                        for file in folder.iterdir():
                            if file.is_file():
                                file.unlink()
                        folder.rmdir()
                    db.execute("DELETE FROM publications WHERE revision=?", (old["revision"],))
            if db.execute("SELECT 1 FROM commits WHERE projection != 'ready'").fetchone():
                raise Conflict("The Gramps projection is not ready")
            db.execute("INSERT OR IGNORE INTO publications(revision,state,snapshot) VALUES(?,?,?)",
                       (expected, "queued", self.file.read_bytes()))
        return self.status()

    def publication_snapshot(self, revision):
        with self.connection() as db:
            row=db.execute("SELECT snapshot FROM publications WHERE revision=?",(revision,)).fetchone()
        if not row:raise Conflict("Unknown publication revision")
        return row['snapshot']

    def next_publication(self):
        with self.connection() as db:
            row=db.execute("SELECT revision,state,books_state,geni_state,detail FROM publications WHERE state != 'ready' ORDER BY rowid LIMIT 1").fetchone()
        return dict(row) if row else None

    def publication_result(self, revision, target, state, detail):
        if target not in {'books','geni'} or state not in {'running','ready','blocked','failed'}:
            raise InvalidGedcom("Invalid publication result")
        if not isinstance(detail,str) or not detail.strip() or len(detail)>4000:
            raise InvalidGedcom("Provide concise result evidence")
        with self.serialized(),self.connection() as db:
            row=db.execute("SELECT * FROM publications WHERE revision=?",(revision,)).fetchone()
            if not row:raise Conflict("Unknown publication revision")
            states={'books':row['books_state'],'geni':row['geni_state']}
            states[target]=state
            overall='ready' if all(v=='ready' for v in states.values()) else 'blocked' if any(v in {'blocked','failed'} for v in states.values()) else 'running'
            notes=json.loads(row['detail']) if row['detail'].startswith('{') else {}
            notes[target]=detail
            db.execute("UPDATE publications SET state=?,books_state=?,geni_state=?,detail=? WHERE revision=?",(overall,states['books'],states['geni'],json.dumps(notes,ensure_ascii=False),revision))
        return self.status()

    def save_book(self, revision, name, data):
        self.publication_snapshot(revision)
        if not re.fullmatch(r'[a-f0-9]{64}',revision) or not isinstance(name,str) or not name.endswith('.pdf') or len(name)>180 or '/' in name or chr(92) in name or name.startswith('.'):
            raise InvalidGedcom("Invalid book filename")
        if not data.startswith(b'%PDF-') or len(data)>32*1024*1024:
            raise InvalidGedcom("Provide a PDF no larger than 32 MiB")
        with self.serialized():
            folder=self.folder/'books'/revision
            folder.mkdir(mode=0o700,parents=True,exist_ok=True)
            os.chmod(folder.parent,0o700)
            os.chmod(folder,0o700)
            tmp=folder/(name+'.tmp')
            tmp.write_bytes(data)
            os.chmod(tmp,0o600)
            tmp.replace(folder/name)
        return {'file':revision+'/'+name,'size':len(data)}

    def install_books(self, revision, books):
        self.publication_snapshot(revision)
        if not isinstance(books,list) or len(books)!=6:
            raise InvalidGedcom("Provide exactly six verified books")
        files=[book.get('file','') for book in books]
        if len(set(files))!=6:raise InvalidGedcom("Duplicate book file")
        for name in files:
            if not name.startswith(revision+'/') or len(Path(name).parts)!=2 or not (self.folder/'books'/name).is_file():
                raise InvalidGedcom("Book is missing")
        manifest={'revision':revision,'state':'ready','books':books}
        with self.serialized():
            tmp=self.folder/'books'/'.manifest.tmp'
            tmp.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
            os.chmod(tmp,0o600)
            tmp.replace(self.folder/'books'/'manifest.json')
        return self.publication_result(revision,'books','ready','Six PDF files uploaded; manifest revision matches frozen GEDCOM')
