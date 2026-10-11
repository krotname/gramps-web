"""Materialize an operator-reviewed Geni media manifest for the private API.

The GEDCOM retains its source URLs; MASTER_MEDIA_MAP supplies local paths to
the derived Gramps database. No credentials or family data belong in Git.
"""
import argparse
import json
import os
import re
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError, URLError

MAX_BYTES = 32 * 1024 * 1024
FORMATS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
           "image/webp": ".webp", "application/pdf": ".pdf"}


def source_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "media.geni.com"
            or parsed.username or parsed.password or parsed.port not in {None, 443}
            or not parsed.path.startswith("/")):
        raise ValueError("Media source must be HTTPS on media.geni.com")
    return parsed


class MediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        source_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def local_path(row):
    parsed = source_url(row["path"])
    mime = row["mime"]
    if mime not in FORMATS:
        raise ValueError("Unsupported media format")
    title = unquote(parsed.path) + " " + row.get("desc", "")
    if re.search(r"фотокоп|photocop|запись[ _-]*акта|zapis[ _-]*akta", title, re.I):
        raise ValueError("Act-record copies must remain local to the operator")
    # Stable across renewed signed URLs, without hashing downloaded files.
    return "geni/" + uuid.uuid5(uuid.NAMESPACE_URL, parsed.path).hex + FORMATS[mime]


def valid_signature(data, mime):
    return {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        "application/pdf": data.startswith(b"%PDF-"),
    }.get(mime, False)


def materialize(row, media_dir, opener=None):
    relative = local_path(row)
    root = Path(media_dir).resolve()
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.resolve().is_relative_to(root):
        raise ValueError("Media path escapes its directory")
    if target.exists():
        with target.open("rb") as stream:
            if 0 < target.stat().st_size <= MAX_BYTES and valid_signature(stream.read(16), row["mime"]):
                return row["path"], relative, False
        raise ValueError("Existing media file is invalid")
    opener = opener or build_opener(MediaRedirect())
    request = Request(row["path"], headers={"User-Agent": "Private-family-archive/1.0"})
    temporary = None
    try:
        with opener.open(request, timeout=30) as response:
            source_url(response.geturl())
            length = response.headers.get("Content-Length")
            expected = int(length) if length is not None else None
            if expected is not None and not 0 < expected <= MAX_BYTES:
                raise ValueError("Media response exceeds its size limit")
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                total = 0
                prefix = b""
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Media response exceeds its size limit")
                    if len(prefix) < 16:
                        prefix += chunk[:16 - len(prefix)]
                    stream.write(chunk)
                if not total or (expected is not None and total != expected):
                    raise ValueError("Incomplete media response")
                if not valid_signature(prefix, row["mime"]):
                    raise ValueError("Media response does not match its format")
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        return row["path"], relative, True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def import_manifest(rows, media_dir, mapping_file, workers=4):
    if not 1 <= workers <= 4:
        raise ValueError("Use one to four download workers")
    unique = {}
    aliases = {}
    for row in rows:
        destination = local_path(row)
        aliases[row["path"]] = destination
        unique.setdefault(destination, row)
    mapping_file = Path(mapping_file)
    mapping = json.loads(mapping_file.read_text()) if mapping_file.exists() else {}
    if not isinstance(mapping, dict) or any(not isinstance(v, str) for v in mapping.values()):
        raise ValueError("Invalid existing media map")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda row: materialize(row, media_dir), unique.values()))
    mapping.update(aliases)
    mapping_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=mapping_file.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(mapping, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, mapping_file)
    finally:
        temporary.unlink(missing_ok=True)
    return {"objects": len(rows), "files": len(results),
            "downloaded": sum(downloaded for _, _, downloaded in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Private, operator-reviewed media API JSON")
    parser.add_argument("--media-dir", required=True)
    parser.add_argument("--map", required=True, dest="mapping_file")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    try:
        result = import_manifest(json.loads(Path(args.manifest).read_text(encoding="utf-8")),
                                 args.media_dir, args.mapping_file, args.workers)
    except (HTTPError, URLError, OSError, ValueError, KeyError) as exc:
        # Do not leak private signed URLs through an exception traceback.
        parser.exit(1, "Media import failed: " + type(exc).__name__ + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
