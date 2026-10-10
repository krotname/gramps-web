"""Deterministic Gramps projection; canonical GEDCOM is never regenerated from it."""
import hashlib, json, re, uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .tree import MONTHS

KINDS = {"Person":"people", "Family":"families", "Event":"events", "Place":"places", "Note":"notes", "Source":"sources", "Citation":"citations"}

def handle(key):
    return uuid.uuid5(uuid.NAMESPACE_URL, "gedcom-master:" + key).hex

def value(node, tag):
    child = node.first(tag)
    return child.content() if child else ""

def date_dict(date):
    if not date:return {"_class":"Date"}
    from gramps.gen.datehandler import parser
    from gramps.gen.lib.json_utils import object_to_dict
    return object_to_dict(parser.parse(date))

def objects(tree, date_parser=date_dict):
    result = {}
    def put(kind, key, **fields):
        h = handle(key)
        result[h] = {"_class":kind, "handle":h, **fields}
        return h
    def note(text, key):
        return put("Note",key,text={"_class":"StyledText","string":text},type="General")
    def notes(node, key):
        values = []
        for i, child in enumerate(node.all("NOTE")):
            content = child.content()
            if content.startswith("@") and content.endswith("@") and content[1:-1] in tree.records:
                content = tree.records[content[1:-1]].content()
            values.append(note(content,f"{key}:note:{i}"))
        return values
    def citations(node, key):
        values = []
        for i, child in enumerate(node.all("SOUR")):
            source = child.content()
            ref = tree.records.get(source.strip("@")) if source.startswith("@") else None
            title = value(ref,"TITL") if ref else source
            source_key = "source:" + (ref.xref if ref else source)
            sh = put("Source",source_key,title=title or source,author=value(ref,"AUTH") if ref else "",abbrev=value(ref,"ABBR") if ref else "",pubinfo=value(ref,"PUBL") if ref else "",note_list=notes(ref,source_key) if ref else [])
            ch = put("Citation",f"{key}:citation:{i}",source_handle=sh,page=value(child,"PAGE"),note_list=notes(child,f"{key}:citation:{i}"))
            values.append(ch)
        return values
    event_names={"BIRT":"Birth","DEAT":"Death","BAPM":"Baptism","BURI":"Burial","RESI":"Residence","OCCU":"Occupation","MARR":"Marriage","DIV":"Divorce","EVEN":"Custom"}
    def events(node,key):
        refs=[];birth=-1;death=-1;counts={}
        for child in node.children:
            if child.tag not in event_names:continue
            index=counts.get(child.tag,0);counts[child.tag]=index+1
            ek=f"{key}:event:{child.tag}:{index}"
            place=value(child,"PLAC") or value(child,"ADDR")
            if not place:
                addr=child.first("ADDR")
                if addr:place=", ".join(value(addr,t) for t in ["ADR1","CITY","STAE","CTRY"] if value(addr,t))
            ph=put("Place","place:"+place,title=place,name={"_class":"PlaceName","value":place},place_type="Unknown") if place else ""
            eh=put("Event",ek,type=event_names[child.tag],date=date_parser(value(child,"DATE")),place=ph,description=child.value if child.value not in {"Y","N"} else "",citation_list=citations(child,ek),note_list=notes(child,ek))
            if child.tag=="BIRT":birth=len(refs)
            if child.tag=="DEAT":death=len(refs)
            refs.append({"_class":"EventRef","ref":eh,"role":"Primary" if node.tag=="INDI" else "Family"})
        return refs,birth,death
    def name(node):
        text=node.content();parts=text.split("/")
        given=value(node,"GIVN") or parts[0].strip()
        surname=value(node,"SURN") or (parts[1].strip() if len(parts)>1 else "")
        if not surname and " " in given:given,surname=given.rsplit(" ",1)
        return {"_class":"Name","first_name":given,"surname_list":[{"_class":"Surname","surname":surname}],"nick":value(node,"NICK"),"type":"Birth Name"}
    for pid,node in tree.people.items():
        names=node.all("NAME")
        refs,bi,di=events(node,pid)
        put("Person",pid,gramps_id=pid,gender={"M":1,"F":0}.get(value(node,"SEX"),2),primary_name=name(names[0]) if names else {"_class":"Name"},alternate_names=[name(n) for n in names[1:]],event_ref_list=refs,birth_ref_index=bi,death_ref_index=di,family_list=[handle(n.value.strip("@")) for n in node.all("FAMS")],parent_family_list=[handle(n.value.strip("@")) for n in node.all("FAMC")],note_list=notes(node,pid),citation_list=citations(node,pid),urls=[{"_class":"Url","path":value(node,"_URL"),"type":"Web Home"}] if value(node,"_URL") else [])
    for fid,node in tree.families.items():
        refs,_,_=events(node,fid)
        put("Family",fid,gramps_id=fid,father_handle=handle(value(node,"HUSB").strip("@")) if value(node,"HUSB") else "",mother_handle=handle(value(node,"WIFE").strip("@")) if value(node,"WIFE") else "",child_ref_list=[{"_class":"ChildRef","ref":handle(n.value.strip("@")),"frel":"Unknown","mrel":"Unknown"} for n in node.all("CHIL")],event_ref_list=refs,type="Unknown",note_list=notes(node,fid),citation_list=citations(node,fid))
    return result

class API:
    def __init__(self, base, token):self.base,self.token=base.rstrip("/"),token
    def request(self, method, path, data=None, headers=None):
        body=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
        req=Request(self.base+path,data=body,method=method,headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json",**(headers or {})})
        with urlopen(req,timeout=30) as res:
            raw=res.read()
            return (json.loads(raw) if raw else None), dict(res.headers)

def project(api, folder, tree, operations=None):
    desired=objects(tree)
    index=Path(folder)/"projection-index.json"
    previous=json.loads(index.read_text()) if index.exists() else {}
    current={h:{"kind":o["_class"],"digest":hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False).encode()).hexdigest()} for h,o in desired.items()}
    missing=[o for h,o in desired.items() if h not in previous]
    new=[]
    for obj in missing:
        try:api.request("GET",f'/api/{KINDS[obj["_class"]]}/{obj["handle"]}')
        except HTTPError as exc:
            if exc.code!=404:raise
            new.append(obj)
    if new:api.request("POST","/api/objects/",new)
    new_handles={o["handle"] for o in new}
    for h,obj in desired.items():
        if h in new_handles or previous.get(h)==current[h]:continue
        route=f'/api/{KINDS[obj["_class"]]}/{h}'
        existing,_=api.request("GET",route)
        # Store serializes projection writes under the canonical revision lock.
        # Native object writes are blocked at the public gateway.
        api.request("PUT",route,{**existing,**obj})
    for h,old in previous.items():
        if h in desired:continue
        try:api.request("DELETE",f'/api/{KINDS[old["kind"]]}/{h}')
        except HTTPError as exc:
            if exc.code!=404:raise
    tmp=index.with_suffix(".tmp")
    tmp.write_text(json.dumps(current),encoding="utf-8");tmp.replace(index)
    return {"objects":len(desired),"created":len(new)}
