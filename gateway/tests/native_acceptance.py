"""Acceptance against the pinned Gramps Web API image, with synthetic data only."""
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError
os.environ.pop("GRAMPS_API_CONFIG", None)
os.environ["DISABLE_CACHES"] = "1"
import tempfile
fixture_root = tempfile.TemporaryDirectory(prefix="gateway-native-", dir="/work")
base=Path(fixture_root.name)
os.environ["GRAMPSHOME"]=str(base)
os.environ["HOME"]=str(base)
Path("/work/native").mkdir(parents=True, exist_ok=True)
os.environ["GRAMPS_DATABASE_PATH"] = str(base/"grampsdb")
Path(os.environ["GRAMPS_DATABASE_PATH"]).mkdir(parents=True, exist_ok=True)
from gramps_webapi.app import create_app
from gramps_webapi.auth import add_user, user_db
from gramps_webapi.dbmanager import WebDbManager
from gateway.tree import Gedcom, Conflict
from gateway.store import Store
from gateway.projection import objects, project, handle
from gateway.tests.test_tree import FIXTURE

config = dict(TREE="Synthetic gateway acceptance", SECRET_KEY="isolated-synthetic-fixture-key-at-least-32-bytes",
    USER_DB_URI="sqlite:////work/native/users.sqlite", SEARCH_INDEX_DB_URI="sqlite:////work/native/search.sqlite",
    MEDIA_BASE_DIR="/work/native/media", STATIC_PATH="/app/static", REGISTRATION_DISABLED=True,
    REPORT_DIR="/work/native/reports", EXPORT_DIR="/work/native/exports", DISABLE_TELEMETRY=True,
    REQUEST_CACHE_CONFIG={"CACHE_TYPE":"NullCache"}, THUMBNAIL_CACHE_CONFIG={"CACHE_TYPE":"NullCache"},
    PERSISTENT_CACHE_CONFIG={"CACHE_TYPE":"NullCache"})
config.update(USER_DB_URI="sqlite:///"+str(base/"users.sqlite"), SEARCH_INDEX_DB_URI="sqlite:///"+str(base/"search.sqlite"))
app=create_app(config, config_from_env=False)
from gateway.relationships import install
install()
tree_id=WebDbManager(name=config["TREE"]).dirname
with app.app_context():
    user_db.create_all()
    add_user(name="owner", password="synthetic-fixture-password", role=4, tree=tree_id)
client=app.test_client()
login=client.post("/api/token/",json={"username":"owner","password":"synthetic-fixture-password"})
assert login.status_code==200,login.get_data(as_text=True)
auth={"Authorization":"Bearer "+login.json["access_token"]}
class NativeAPI:
    def request(self, method, path, data=None, headers=None):
        response=client.open(path, method=method, json=data, headers={**auth,**(headers or {})})
        if response.status_code>=400:
            raise HTTPError(path,response.status_code,response.get_data(as_text=True),response.headers,io.BytesIO(response.data))
        return response.json,dict(response.headers)
api=NativeAPI()
user,_=api.request("GET","/api/users/-/")
assert user["role"]==4
store=Store(base/"master")
data=FIXTURE.replace(b"1 NAME Anna", b"1 RESN privacy\r\n1 CENS\r\n2 DATE 1910\r\n2 PLAC Census place\r\n2 NOTE Census evidence\r\n1 NAME Anna").replace(b"2 DATE ABT 1900",b"2 DATE ABT 1900\r\n2 PLAC Original city")
data=data.replace(b"1 NAME Anna",b"1 OBJE\r\n2 FILE synthetic.svg\r\n3 FORM svg\r\n2 TITL Synthetic portrait\r\n1 NAME Anna")
data=data.replace(b"0 TRLR",b"0 @I3@ INDI\r\n1 NAME Sibling /Tree/\r\n1 FAMC @F2@\r\n0 @F2@ FAM\r\n1 CHIL @I2@\r\n1 CHIL @I3@\r\n0 TRLR")
Path(config["MEDIA_BASE_DIR"]).mkdir(parents=True,exist_ok=True)
(Path(config["MEDIA_BASE_DIR"])/"synthetic.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="blue"/></svg>')
store.initialize(data)
callback=lambda tree,ops:project(api,store.folder,tree,ops)
seed=store.retry(callback)
assert seed["projection"]=="ready",seed
family,_=api.request("GET","/api/families/"+handle("F1", "Family"))
assert family["child_ref_list"][0]["frel"]==["Unknown",6] or "Unknown" in str(family["child_ref_list"][0]["frel"]),family["child_ref_list"]
for origin,other,label in [("I2","I1","родитель"),("I1","I2","ребёнок"),
                           ("I2","I3","брат или сестра")]:
    relation,_=api.request("GET",f'/api/relations/{handle(origin)}/{handle(other)}?locale=ru&depth=20')
    assert relation["relationship_string"]==label,relation
all_relations,_=api.request("GET",f'/api/relations/{handle("I2")}/{handle("I1")}/all?locale=ru&depth=20')
assert all_relations[0]["relationship_string"]=="родитель",all_relations
assert client.get(f'/api/relations/{handle("I2")}/{handle("I1")}?locale=ru').status_code==401
family_after,_=api.request("GET","/api/families/"+handle("F1", "Family"))
assert family_after==family
before=store.revision
result=store.commit(before,"fixture-edit",[{"op":"person","xref":"I1","name":"Anne /New/","birth":{"date":"1899-03-14","place":"New city"},"append_notes":["Synthetic acceptance note"],"append_sources":["Synthetic source record"]}],callback)
assert result["projection"]=="ready",result
person,_=api.request("GET","/api/people/"+handle("I1"))
assert person["private"] is True
media_handle=person["media_list"][0]["ref"]
media,_=api.request("GET","/api/media/"+media_handle)
assert media["path"]=="synthetic.svg" and media["desc"]=="Synthetic portrait"
assert client.get("/api/media/"+media_handle+"/file",headers=auth).status_code==200
census,_=api.request("GET","/api/events/"+handle("I1:event:CENS:0", "Event"))
assert census["private"] is True and census["date"]["dateval"][2]==1910
assert person["primary_name"]["first_name"]=="Anne"
assert person["primary_name"]["surname_list"][0]["surname"]=="New"
event,_=api.request("GET","/api/events/"+handle("I1:event:BIRT:0", "Event"))
assert event["date"]["dateval"][:3]==[14,3,1899],event["date"]
try:store.commit(before,"stale", [{"op":"person","xref":"I1","sex":"F"}],callback)
except Conflict:pass
else:raise AssertionError("Stale revision accepted")
assert len(client.get("/api/people/",headers=auth).json)==3
print(json.dumps({"native_api":"passed","people":3,"stable_parentage":"Unknown","unnamed_parents":"siblings_preserved","date":"1899-03-14","projection":"ready","stale_revision":"rejected"}))
