import json, threading, tempfile, unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from gateway.server import Gateway
from gateway.projection import objects, handle
from gateway.tree import Gedcom
from gateway.tests.test_tree import FIXTURE

class Upstream(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        token=self.headers.get('Authorization','')
        if self.path=='/api/users/-/':
            role=4 if token=='Bearer owner' else 0
            status=200 if token in {'Bearer owner','Bearer guest'} else 401
            data=json.dumps({'role':role}).encode()
        elif self.path == '/api/oidc/login/':
            self.send_response(302); self.send_header('Location', 'https://idp.example/authorize'); self.send_header('Content-Length', '0'); self.end_headers(); return
        elif self.headers.get('Range') == 'bytes=0-1':
            self.send_response(206); self.send_header('Content-Range', 'bytes 0-1/100'); self.send_header('Accept-Ranges', 'bytes'); self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'ab'); return
        else:status=200;data=b'{}'
        self.send_response(status);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.send_response(200); self.send_header('Content-Length','2'); self.end_headers(); self.wfile.write(b'{}')

class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.upstream=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        self.gateway=Gateway(('127.0.0.1',0),self.folder.name,'http://127.0.0.1:'+str(self.upstream.server_port),'https://example.org')
        self.gateway.store.initialize(FIXTURE)
        for server in [self.upstream,self.gateway]:
            threading.Thread(target=server.serve_forever,daemon=True).start()
    def tearDown(self):
        for server in [self.gateway,self.upstream]:server.shutdown();server.server_close()
        self.folder.cleanup()
    def request(self,path,token=None,data=None,origin=None):
        headers={}
        if token:headers['Authorization']='Bearer '+token
        if origin:headers['Origin']=origin
        req=Request('http://127.0.0.1:'+str(self.gateway.server_port)+path,headers=headers,data=json.dumps(data).encode() if data is not None else None)
        try:
            with urlopen(req,timeout=5) as res:return res.status,res.read()
        except HTTPError as e:return e.code,e.read()
    def test_private_canonical_requires_owner_even_with_valid_guest_token(self):
        self.assertEqual(self.request('/api/master/download/')[0],401)
        self.assertEqual(self.request('/api/master/download/','guest')[0],403)
        self.assertEqual(self.request('/api/master/download/','owner')[0],200)
    def test_native_write_is_blocked_at_gateway(self):
        self.assertEqual(self.request('/api/objects/','owner',[])[0],409)
    def test_cross_origin_cannot_publish(self):
        self.assertEqual(self.request('/api/master/publish/','owner',{},'https://attacker.example')[0],403)
    def test_snapshot_returns_same_revision_as_file(self):
        status,data=self.request('/api/master/person/I1/','owner')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(data)['revision'],self.gateway.store.revision)
    def test_pending_retry_returns_accepted_without_claiming_completion(self):
        for route in ["/api/master/retry/", "/api/master/project/"]:
            with self.subTest(route=route), patch("gateway.server.project", side_effect=RuntimeError("Projection unavailable")):
                status, data = self.request(route, "owner", {})
                self.assertEqual(status, 202)
                self.assertEqual(json.loads(data)["projection"], "pending")

    def test_family_list_keeps_explicit_parent_roles(self):
        status, data = self.request("/api/master/families/", "owner")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)[0]["parent_roles"], {"WIFE": "I1"})

    def test_unknown_book_and_traversal_are_rejected(self):
        self.assertEqual(self.request('/api/master/books/../../journal.sqlite3','owner')[0],404)
    def test_books_cannot_be_marked_ready_without_upload(self):
        self.gateway.store.retry(lambda *_: None)
        revision=self.gateway.store.revision
        self.gateway.store.publish(revision)
        status,_=self.request('/api/master/publications/'+revision+'/result/','owner',{'target':'books','state':'ready','detail':'Uploaded'})
        self.assertEqual(status,400)
        self.assertEqual(self.gateway.store.next_publication()['books_state'],'pending')

    def test_media_query_token_is_scoped_and_ranges_are_relayed(self):
        status, _ = self.request('/api/media/example/file?jwt=owner')
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/master/download/?jwt=owner')[0], 401)
        req = Request('http://127.0.0.1:' + str(self.gateway.server_port) + '/api/media/example/file?jwt=owner', headers={'Range': 'bytes=0-1'})
        with urlopen(req, timeout=5) as res:
            self.assertEqual(res.status, 206)
            self.assertEqual(res.headers['Content-Range'], 'bytes 0-1/100')
            self.assertEqual(res.headers['Accept-Ranges'], 'bytes')
            self.assertEqual(res.read(), b'ab')

    def test_oidc_bootstrap_redirect_is_returned_to_browser(self):
        from urllib.request import build_opener, HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args): return None
        self.assertEqual(self.request('/api/oidc/config/')[0], 200)
        with self.assertRaises(HTTPError) as caught:
            build_opener(NoRedirect()).open('http://127.0.0.1:' + str(self.gateway.server_port) + '/api/oidc/login/', timeout=5)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(caught.exception.headers['Location'], 'https://idp.example/authorize')
        caught.exception.close()

    def test_owner_can_save_tree_configuration_but_guest_cannot(self):
        self.assertEqual(self.request('/api/trees/-/config', 'owner', {'title': 'Archive'})[0], 200)
        self.assertEqual(self.request('/api/trees/-/config', 'guest', {})[0], 403)


class ProjectionTest(unittest.TestCase):
    def test_all_links_use_stable_handles_and_parentage_is_unknown(self):
        tree=Gedcom(FIXTURE)
        plan=objects(tree,date_parser=lambda d:{'_class':'Date','text':d})
        family=plan[handle('F1')]
        self.assertEqual(family['child_ref_list'][0]['frel'],'Unknown')
        self.assertEqual(family['child_ref_list'][0]['mrel'],'Unknown')
        self.assertEqual(plan[handle('I2')]['parent_family_list'],[handle('F1')])
        self.assertEqual(plan[handle('I1')]['family_list'],[handle('F1')])
        self.assertEqual(plan,objects(tree,date_parser=lambda d:{'_class':'Date','text':d}))
