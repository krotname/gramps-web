"""Deterministic Gramps projection; canonical GEDCOM is never regenerated from it."""
import hashlib, json, mimetypes, os, re, uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from .tree import MONTHS

KINDS = {"Person":"people", "Family":"families", "Event":"events", "Place":"places", "Note":"notes", "Source":"sources", "Citation":"citations", "Media":"media"}

def handle(key, kind="Person"):
    return uuid.uuid5(uuid.NAMESPACE_URL, "gedcom-master:" + kind + ":" + key).hex

def value(node, tag):
    child = node.first(tag)
    return child.content() if child else ""

def date_dict(date):
    if not date:return {"_class":"Date"}
    from gramps.gen.datehandler import parser
    from gramps.gen.lib.json_utils import object_to_dict
    return object_to_dict(parser.parse(date))

def objects(tree, date_parser=None, media_paths=None):
    date_parser=date_parser or date_dict
    media_paths = media_paths or {}
    result = {}
    def put(kind, key, **fields):
        h = handle(key, kind)
        previous_private=result.get(h,{}).get("private",False)
        result[h] = {"_class":kind, "handle":h, "private":False, **fields}
        result[h]['private']=result[h]['private'] or bool(previous_private)
        return h
    def restricted(node):
        return bool(node and any(n.value.lower() in {'privacy','confidential'} for n in node.all('RESN')))
    def note(text, key, private=False):
        return put("Note",key,text={"_class":"StyledText","string":text},type="General",private=private)
    def notes(node, key, private=False):
        values = []
        for i, child in enumerate(node.all("NOTE")):
            content = child.content()
            if content.startswith("@") and content.endswith("@") and content[1:-1] in tree.records:
                ref = tree.records[content[1:-1]]
                content = ref.content()
            else:
                ref = None
            values.append(note(content,f"{key}:note:{i}",private or restricted(child) or restricted(ref)))
        return values
    def citations(node, key, private=False):
        values = []
        for i, child in enumerate(node.all("SOUR")):
            source = child.content()
            ref = tree.records.get(source.strip("@")) if source.startswith("@") else None
            title = value(ref,"TITL") if ref else source
            source_key = "source:" + (ref.xref if ref else source)
            sh = put("Source",source_key,title=title or source,author=value(ref,"AUTH") if ref else "",abbrev=value(ref,"ABBR") if ref else "",pubinfo=value(ref,"PUBL") if ref else "",note_list=notes(ref,source_key,private or restricted(ref)) if ref else [],private=private or restricted(ref))
            ch = put("Citation",f"{key}:citation:{i}",source_handle=sh,page=value(child,"PAGE"),note_list=notes(child,f"{key}:citation:{i}",private),private=private or restricted(child))
            values.append(ch)
        return values
    def media(node, key, private=False):
        refs = []
        for i, child in enumerate(node.all("OBJE")):
            target = tree.records.get(child.value.strip("@")) if child.value.startswith("@") else child
            if target is None:
                raise ValueError("Missing media reference")
            for j, file in enumerate(target.all("FILE")):
                original = file.content()
                if not original:
                    continue
                mapped = media_paths.get(original, original)
                if not isinstance(mapped,str) or urlsplit(mapped).scheme not in {"", "https", "http"}:
                    raise ValueError("Unsupported media location")
                if not urlsplit(mapped).scheme and (Path(mapped).is_absolute() or ".." in Path(mapped).parts):
                    raise ValueError("Unsafe private media mapping")
                form = value(file, "FORM") or value(target, "FORM")
                mime = form if "/" in form else mimetypes.guess_type("file." + form)[0] or "application/octet-stream"
                title = value(file, "TITL") or value(target, "TITL")
                mk = "media:" + (target.xref or key + ":" + str(i)) + ":" + str(j)
                hidden = private or restricted(child) or restricted(target)
                mh = put("Media", mk, gramps_id=target.xref or "", path=mapped, mime=mime,
                    desc=title, date=date_parser(value(target, "DATE")),
                    note_list=notes(target, mk, hidden), citation_list=citations(target, mk, hidden), private=hidden)
                refs.append({"_class":"MediaRef", "ref":mh, "private":hidden})
        return refs
    event_names={"BIRT":"Birth","DEAT":"Death","BAPM":"Baptism","BURI":"Burial","RESI":"Residence","OCCU":"Occupation","MARR":"Marriage","DIV":"Divorce","EVEN":"Custom"}
    standard_events=set('BIRT CHR DEAT BURI CREM BAPM BARM BASM BLES CHRA CONF FCOM ORDN NATU EMIG IMMI CENS PROB WILL GRAD RETI ADOP EVEN CAST DSCR EDUC IDNO NATI NCHI NMR OCCU PROP RELI RESI SSN TITL FACT ANUL DIV DIVF ENGA MARB MARC MARL MARS MARR'.split())
    def events(node,key):
        refs=[];birth=-1;death=-1;counts={}
        for child in node.children:
            if child.tag not in standard_events and not (child.tag.startswith('_') and (child.first('DATE') or child.first('PLAC'))):continue
            private=restricted(node) or restricted(child)
            index=counts.get(child.tag,0);counts[child.tag]=index+1
            ek=f"{key}:event:{child.tag}:{index}"
            place=value(child,"PLAC") or value(child,"ADDR")
            if not place:
                addr=child.first("ADDR")
                if addr:place=", ".join(value(addr,t) for t in ["ADR1","CITY","STAE","CTRY"] if value(addr,t))
            ph=put("Place","place:"+place,title=place,name={"_class":"PlaceName","value":place},place_type="Unknown",private=private) if place else ""
            eh=put("Event",ek,type=event_names.get(child.tag) or value(child,"TYPE") or child.tag,date=date_parser(value(child,"DATE")),place=ph,description=child.value if child.value not in {"Y","N"} else "",citation_list=citations(child,ek,private),note_list=notes(child,ek,private),media_list=media(child,ek,private),private=private)
            if child.tag=="BIRT":birth=len(refs)
            if child.tag=="DEAT":death=len(refs)
            refs.append({"_class":"EventRef","ref":eh,"role":"Primary" if node.tag=="INDI" else "Family"})
        return refs,birth,death
    def name(node):
        text=node.content();parts=text.split("/")
        given=value(node,"GIVN") or parts[0].strip()
        surname=value(node,"SURN") or (parts[1].strip() if len(parts)>1 else "")
        return {"_class":"Name","first_name":given,"surname_list":[{"_class":"Surname","surname":surname}],"nick":value(node,"NICK"),"type":"Birth Name"}
    for pid,node in tree.people.items():
        names=node.all("NAME")
        refs,bi,di=events(node,pid)
        put("Person",pid,gramps_id=pid,gender={"M":1,"F":0}.get(value(node,"SEX"),2),primary_name=name(names[0]) if names else {"_class":"Name"},alternate_names=[name(n) for n in names[1:]],event_ref_list=refs,birth_ref_index=bi,death_ref_index=di,family_list=[handle(n.value.strip("@"), "Family") for n in node.all("FAMS")],parent_family_list=[handle(n.value.strip("@"), "Family") for n in node.all("FAMC")],private=restricted(node),note_list=notes(node,pid,restricted(node)),citation_list=citations(node,pid,restricted(node)),media_list=media(node,pid,restricted(node)),urls=[{"_class":"Url","path":value(node,"_URL"),"type":"Web Home"}] if value(node,"_URL") else [])
    for fid,node in tree.families.items():
        refs,_,_=events(node,fid)
        put("Family",fid,gramps_id=fid,father_handle=handle(value(node,"HUSB").strip("@")) if value(node,"HUSB") else "",mother_handle=handle(value(node,"WIFE").strip("@")) if value(node,"WIFE") else "",child_ref_list=[{"_class":"ChildRef","ref":handle(n.value.strip("@")),"frel":"Unknown","mrel":"Unknown"} for n in node.all("CHIL")],event_ref_list=refs,type="Unknown",private=restricted(node),note_list=notes(node,fid,restricted(node)),citation_list=citations(node,fid,restricted(node)),media_list=media(node,fid,restricted(node)))
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
    media_map=os.environ.get("MASTER_MEDIA_MAP")
    media_paths=json.loads(Path(media_map).read_text()) if media_map else {}
    desired=objects(tree, media_paths=media_paths)
    index=Path(folder)/"projection-index.json"
    previous=json.loads(index.read_text()) if index.exists() else {}
    current={h:{"kind":o["_class"],"digest":hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False).encode()).hexdigest()} for h,o in desired.items()}
    inventory={}
    for kind,route in KINDS.items():
        for page in range(1,101):
            rows,headers=api.request('GET',f'/api/{route}/?keys=handle&page={page}&pagesize=1000')
            if not isinstance(rows,list):raise ValueError('Invalid projection inventory')
            for row in rows:inventory[row['handle']]=kind
            if len(rows)<1000:break
        else:raise ValueError('Projection inventory exceeds limit')
    unexpected=set(inventory)-set(desired)-set(previous)
    if unexpected:raise ValueError('Existing projection contains unowned objects; reconcile before replacing it')
    missing=[o for h,o in desired.items() if h not in inventory]
    new=[]
    for obj in missing:
        try:api.request("GET",f'/api/{KINDS[obj["_class"]]}/{obj["handle"]}')
        except HTTPError as exc:
            if exc.code!=404:raise
            new.append(obj)
    if new:api.request("POST","/api/objects/",new)
    new_handles={o["handle"] for o in new}
    for h,obj in desired.items():
        if h in new_handles or (h in inventory and previous.get(h)==current[h]):continue
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
    tmp.write_text(json.dumps(current),encoding="utf-8");os.chmod(tmp,0o600);tmp.replace(index)
    return {"objects":len(desired),"created":len(new)}
