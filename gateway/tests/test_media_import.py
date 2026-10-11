import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gateway.media_import import MediaRedirect, import_manifest, local_path, materialize


ROW = {"path": "https://media.geni.com/p14/example/portrait_original.jpg?hash=private",
       "mime": "image/jpeg", "desc": "Portrait"}


class Response(io.BytesIO):
    def __init__(self, data, length=None, url=ROW["path"]):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}
        self.url = url

    def geturl(self):
        return self.url


class Opener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


class MediaImportTest(unittest.TestCase):
    def test_materialized_media_is_local_and_reused_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            result = materialize(ROW, folder, Opener(Response(b"\xff\xd8\xffportrait")))
            self.assertTrue(result[2])
            self.assertEqual((Path(folder) / result[1]).read_bytes(), b"\xff\xd8\xffportrait")
            self.assertFalse(materialize(ROW, folder, object())[2])
            self.assertEqual(local_path(ROW), local_path({**ROW, "path": ROW["path"].split("?")[0]}))

    def test_external_hosts_and_act_records_are_rejected_before_download(self):
        for row in [{**ROW, "path": "http://media.geni.com/x"},
                    {**ROW, "path": "https://127.0.0.1/x"},
                    {**ROW, "path": "https://media.geni.com@localhost/x"},
                    {**ROW, "desc": "Фотокопия записи акта"}]:
            with self.assertRaises(ValueError):
                local_path(row)
        with self.assertRaises(ValueError):
            MediaRedirect().redirect_request(None, None, 302, "", {}, "https://localhost/x")

    def test_partial_or_wrong_format_does_not_install_a_file(self):
        for data, length in [(b"\xff\xd8\xffpartial", 100), (b"%PDF-1.4", None)]:
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    materialize(ROW, folder, Opener(Response(data, length)))
                self.assertEqual(list(Path(folder).rglob("*.jpg")), [])
                self.assertEqual(list((Path(folder) / "geni").iterdir()), [])

    def test_duplicate_references_share_one_file_and_existing_map_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            mapping = Path(folder) / "map.json"
            mapping.write_text(json.dumps({"old": "old.jpg"}))
            with patch("gateway.media_import.materialize", return_value=(ROW["path"], "geni/portrait.jpg", True)) as download:
                result = import_manifest([ROW, ROW], folder, mapping)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(result, {"objects": 2, "files": 1, "downloaded": 1})
            self.assertEqual(json.loads(mapping.read_text())["old"], "old.jpg")

    def test_failed_download_does_not_publish_a_partial_map(self):
        with tempfile.TemporaryDirectory() as folder:
            mapping = Path(folder) / "map.json"
            mapping.write_text('{"old":"old.jpg"}')
            with patch("gateway.media_import.materialize", side_effect=ValueError("Invalid response")):
                with self.assertRaises(ValueError):
                    import_manifest([ROW], folder, mapping)
            self.assertEqual(mapping.read_text(), '{"old":"old.jpg"}')

    def test_renewed_signed_urls_share_one_download_and_both_are_mapped(self):
        renewed = {**ROW, "path": ROW["path"].replace("hash=private", "hash=renewed")}
        with tempfile.TemporaryDirectory() as folder:
            mapping = Path(folder) / "map.json"
            with patch("gateway.media_import.materialize", return_value=(ROW["path"], local_path(ROW), True)) as download:
                result = import_manifest([ROW, renewed], folder, mapping)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(result["files"], 1)
            self.assertEqual(json.loads(mapping.read_text()),
                             {ROW["path"]: local_path(ROW), renewed["path"]: local_path(ROW)})
