"""Private gateway: one JWT authority, canonical writes, and read-only native API."""
import json, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs, quote, unquote
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from .tree import Gedcom, Conflict, InvalidGedcom
from .store import Store
from .projection import API, project, value, handle

PUBLIC_AUTH={"/api/token/", "/api/token/refresh/", "/api/metadata/"}
PUBLIC_OIDC={
    '/api/oidc/config/': {'GET'}, '/api/oidc/login/': {'GET'},
    '/api/oidc/callback/': {'GET'}, '/api/oidc/tokens/': {'POST'},
    '/api/oidc/logout/': {'GET', 'POST'},
}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args): return None

UPSTREAM_OPENER=build_opener(NoRedirect())
MAX_BODY=2*1024*1024

def person_data(node):
    events={}
    for field,tag in [("birth","BIRT"),("death","DEAT"),("baptism","BAPM"),("burial","BURI")]:
        ev=node.first(tag)
        events[field]={"date":value(ev,"DATE"),"place":value(ev,"PLAC")} if ev else None
    return {"xref":node.xref,"name":value(node,"NAME"),"given":value(node.first("NAME"),"GIVN") if node.first("NAME") else "","surname":value(node.first("NAME"),"SURN") if node.first("NAME") else "","sex":value(node,"SEX"),"occupation":value(node,"OCCU"),"names":[n.content() for n in node.all("NAME")],"notes":[n.content() for n in node.all("NOTE")],"sources":[n.content() for n in node.all("SOUR")],**events}

class Gateway(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,folder,upstream,origin):
        self.store=Store(folder);self.upstream=upstream.rstrip("/");self.origin=origin
        super().__init__(address,Handler)
        self.publisher=None
        if os.getenv('MASTER_WORKER_CONFIG'):
            from .publication_worker import start_worker
            self.publisher=start_worker(self.store,os.environ['MASTER_WORKER_CONFIG'])
    def server_close(self):
        if self.publisher:self.publisher.stop.set()
        super().server_close()

class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args):pass
    def reply(self,status,body,content_type="application/json",headers=None):
        data=json.dumps(body,ensure_ascii=False).encode() if not isinstance(body,bytes) else body
        self.send_response(status)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","same-origin")
        for k,v in (headers or {}).items():self.send_header(k,v)
        if status>=400:
            self.close_connection=True;self.send_header("Connection","close")
        self.end_headers()
        if self.command!="HEAD":self.wfile.write(data)
    def body(self, maximum=MAX_BODY):
        if self.headers.get("Transfer-Encoding"):raise InvalidGedcom("Chunked requests are not supported")
        size=int(self.headers.get("Content-Length","0"))
        if not 0<=size<=maximum:raise InvalidGedcom("Request is too large")
        return self.rfile.read(size)
    def auth(self,owner=False):
        header=self.headers.get("Authorization","")
        route=urlsplit(self.path).path
        if not header and not owner and self.command in {'GET','HEAD'} and re.match(r'^/api/(media/|maps/|exporters/)',route):
            tokens=parse_qs(urlsplit(self.path).query).get('jwt',[])
            if len(tokens)==1:header='Bearer '+tokens[0]
        if not header.startswith("Bearer ") or len(header)>16384:raise HTTPError("",401,"Sign in",None,None)
        token=header[7:]
        api=API(self.server.upstream,token)
        user,_=api.request("GET","/api/users/-/")
        if owner and user.get("role",0)<4:raise HTTPError("",403,"Owner access required",None,None)
        if user.get("role",-1)<0:raise HTTPError("",403,"Account is disabled",None,None)
        return api
    def route(self):
        parsed=urlsplit(self.path);route=parsed.path
        if parsed.scheme or parsed.netloc or not route.startswith("/") or chr(92) in route:raise InvalidGedcom("Invalid path")
        if self.command not in {"GET","HEAD"}:
            origin=self.headers.get("Origin")
            if origin and origin!=self.server.origin:raise HTTPError("",403,"Origin mismatch",None,None)
        if route=="/healthz":return self.reply(200,{"status":"ok"})
        if route.startswith("/api/master/"):
            api=self.auth(owner=True)
            store=self.server.store
            if self.command=="GET":
                with store.serialized():
                    snapshot=store.file.read_bytes(); snapshot_rev=store.revision
                if route=="/api/master/status/":return self.reply(200,store.status())
                if route=="/api/master/publications/next/":return self.reply(200,store.next_publication())
                publication=re.fullmatch(r"/api/master/publications/([a-f0-9]{64})/snapshot/",route)
                if publication:return self.reply(200,store.publication_snapshot(publication[1]),"application/x-gedcom")
                if route=="/api/master/download/":return self.reply(200,snapshot,"application/x-gedcom",{"Content-Disposition":"attachment; filename=export-Forest.ged"})
                if route=="/api/master/people/":
                    tree=Gedcom(snapshot);query=parse_qs(parsed.query).get("q",[""])[0].casefold()
                    return self.reply(200,[{"xref":k,"name":value(n,"NAME")} for k,n in tree.people.items() if query in (k+" "+value(n,"NAME")).casefold()])
                if route.startswith("/api/master/person/"):
                    pid=route.removeprefix("/api/master/person/").strip("/")
                    tree=Gedcom(snapshot)
                    if pid not in tree.people:return self.reply(404,{"message":"Unknown person"})
                    return self.reply(200,{"revision":snapshot_rev,**person_data(tree.people[pid])})
                if route=="/api/master/families/":
                    tree=Gedcom(snapshot)
                    return self.reply(200,[{"xref":k,"parents":[n.value.strip("@") for n in node.children if n.tag in {"HUSB","WIFE"}],"parent_roles":{n.tag:n.value.strip("@") for n in node.children if n.tag in {"HUSB","WIFE"}},"children":[n.value.strip("@") for n in node.all("CHIL")]} for k,node in tree.families.items()])
                if route=="/api/master/books/":
                    manifest=store.folder/"books/manifest.json"
                    return self.reply(200,json.loads(manifest.read_text()) if manifest.exists() else {"books":[],"state":"not built"})
                if route.startswith("/api/master/books/"):
                    name=unquote(route.removeprefix("/api/master/books/"))
                    manifest=store.folder/"books/manifest.json"
                    listed=json.loads(manifest.read_text()).get("books",[]) if manifest.exists() else []
                    if name not in [b["file"] for b in listed]:return self.reply(404,{"message":"Unknown book"})
                    file=store.folder/"books"/name
                    return self.reply(200,file.read_bytes(),"application/pdf",{"Content-Disposition":"attachment; filename*=UTF-8''"+quote(name)})
            elif self.command=="POST":
                upload=re.fullmatch(r"/api/master/publications/([a-f0-9]{64})/books/([^/]+)",route)
                if upload:return self.reply(200,store.save_book(upload[1],unquote(upload[2]),self.body(32*1024*1024)))
                payload=json.loads(self.body())
                result_route=re.fullmatch(r"/api/master/publications/([a-f0-9]{64})/(result|books)/",route)
                if result_route:
                    if result_route[2]=="books":return self.reply(200,store.install_books(result_route[1],payload.get("books")))
                    if payload.get("target")=="books" and payload.get("state")=="ready":
                        raise InvalidGedcom("Upload and verify the six books before completing publication")
                    return self.reply(200,store.publication_result(result_route[1],payload.get("target"),payload.get("state"),payload.get("detail")))
                callback=lambda tree,ops:project(api,store.folder,tree,ops)
                if route=="/api/master/commit/":
                    result=store.commit(payload.get("revision"),payload.get("key"),payload.get("operations"),callback)
                    return self.reply(200 if result["projection"]=="ready" else 202,result)
                if route in {"/api/master/retry/", "/api/master/project/"}:
                    result=store.retry(callback)
                    return self.reply(200 if result["projection"]=="ready" else 202,result)
                if route=="/api/master/publish/":return self.reply(202,store.publish(payload.get("revision")))
            return self.reply(404,{"message":"Unknown master route"})
        if route.startswith("/api/"):
            public=route in PUBLIC_AUTH or self.command in PUBLIC_OIDC.get(route,set())
            config_write=route.rstrip('/')=='/api/trees/-/config' and self.command in {'PUT','POST'}
            if not public:self.auth(owner=config_write)
            if self.command not in {"GET","HEAD"} and not public and not config_write and not (route.startswith("/api/users/") and self.command in {"PUT","POST","DELETE"}):
                return self.reply(409,{"message":"Изменяйте дерево через редактор GEDCOM"})
            if route=="/api/metadata/" and self.command not in {"GET","HEAD"}:return self.reply(405,{"message":"Method not allowed"})
        elif self.command not in {"GET","HEAD"}:
            return self.reply(405,{"message":"Method not allowed"})
        forwarded=["Authorization","Content-Type","Accept","If-Match","Range","If-Range","If-None-Match"]
        if route.startswith('/api/oidc/'):forwarded.append('Cookie')
        req=Request(self.server.upstream+self.path,method=self.command,data=self.body() if self.command not in {"GET","HEAD"} else None,headers={k:self.headers[k] for k in forwarded if k in self.headers})
        try:
            response=UPSTREAM_OPENER.open(req,timeout=60)
        except HTTPError as exc:
            response=exc
        with response as res:
            if route=="/api/metadata/" and self.command=='GET' and self.headers.get("Authorization") and os.getenv("MASTER_FOCUS") and res.status==200:
                raw=res.read(MAX_BODY+1)
                if len(raw)>MAX_BODY:raise ValueError('Metadata response is too large')
                metadata=json.loads(raw)
                metadata.setdefault("db",{})["default_person"]=handle(os.environ["MASTER_FOCUS"])
                return self.reply(200,json.dumps(metadata).encode(),res.headers.get('Content-Type','application/json'))
            self.relay(res)

    def relay(self,res):
        self.response_started=True
        self.send_response(res.status if hasattr(res,'status') else res.code)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        for key in ['Content-Type','Content-Length','Content-Range','Accept-Ranges','ETag','X-Total-Count','Location']:
            if key in res.headers:self.send_header(key,res.headers[key])
        if urlsplit(self.path).path.startswith('/api/oidc/'):
            for cookie in res.headers.get_all('Set-Cookie',[]):self.send_header('Set-Cookie',cookie)
        if 'Content-Length' not in res.headers:
            self.close_connection=True;self.send_header('Connection','close')
        self.end_headers()
        if self.command!='HEAD':
            while True:
                chunk=res.read(64*1024)
                if not chunk:break
                self.wfile.write(chunk)

    def do_HEAD(self):self.handle_request()
    def do_GET(self):self.handle_request()
    def do_POST(self):self.handle_request()
    def do_PUT(self):self.handle_request()
    def do_PATCH(self):self.handle_request()
    def do_DELETE(self):self.handle_request()
    def handle_request(self):
        try:self.route()
        except HTTPError as exc:self.reply(exc.code,{"message":exc.reason})
        except Conflict as exc:self.reply(409,{"message":str(exc)})
        except (InvalidGedcom,ValueError,KeyError,TypeError):self.reply(400,{"message":"Invalid request"})
        except (OSError,URLError):
            if getattr(self,'response_started',False):self.close_connection=True
            else:self.reply(503,{"message":"Service is temporarily unavailable"})

if __name__=="__main__":
    Gateway(("0.0.0.0",int(os.getenv("MASTER_PORT","8000"))),os.getenv("MASTER_DATA","/data/master"),os.getenv("MASTER_UPSTREAM","http://127.0.0.1:5000"),os.environ["MASTER_ORIGIN"]).serve_forever()
