"""Append local application layers to an OCI layout without a Docker daemon."""
import copy, gzip, hashlib, io, json, os, tarfile
from pathlib import Path
import sys

layout, dist, gateway = map(Path, sys.argv[1:4])
blobs=layout/"blobs/sha256"
def read(desc):return json.loads((blobs/desc["digest"].split(":")[1]).read_bytes())
def save(data,media):
    raw=data if isinstance(data,bytes) else json.dumps(data,separators=(",",":")).encode()
    digest=hashlib.sha256(raw).hexdigest()
    (blobs/digest).write_bytes(raw)
    return {"mediaType":media,"digest":"sha256:"+digest,"size":len(raw)}
index=json.loads((layout/"index.json").read_text())
descriptor=index["manifests"][0]
manifest=read(descriptor)
if "manifests" in manifest:raise SystemExit("Pull one platform into the OCI layout first")
config=read(manifest["config"])
tarbuf=io.BytesIO()
with tarfile.open(fileobj=tarbuf,mode="w",format=tarfile.PAX_FORMAT) as archive:
    for source,prefix in [(dist,"app/static"),(gateway,"app/gateway")]:
        for file in sorted(source.rglob("*")):
            if not file.is_file() or "__pycache__" in file.parts or "tests" in file.parts:continue
            raw=file.read_bytes()
            member=tarfile.TarInfo(prefix+"/"+file.relative_to(source).as_posix())
            member.size=len(raw);member.mode=0o644;member.uid=member.gid=10001;member.mtime=0
            archive.addfile(member,io.BytesIO(raw))
plain=tarbuf.getvalue()
layer=save(gzip.compress(plain,mtime=0),"application/vnd.oci.image.layer.v1.tar+gzip")
manifest["layers"].append(layer)
config["rootfs"]["diff_ids"].append("sha256:"+hashlib.sha256(plain).hexdigest())
config.setdefault("history",[]).append({"created_by":"Add canonical GEDCOM gateway and stable frontend"})
config["config"]["User"]="10001:10001"
config["config"]["WorkingDir"]="/app"
config["config"]["Entrypoint"]=["python3","-m","gateway.server"]
config["config"]["Cmd"]=[]
config["config"].setdefault("Env",[]).append("PYTHONPATH=/app")
config["config"].setdefault("Labels",{}).update({"org.opencontainers.image.source":"https://github.com/krotname/gramps-web","org.opencontainers.image.revision":os.environ.get("SOURCE_SHA","")})
manifest["config"]=save(config,"application/vnd.oci.image.config.v1+json")
out=save(manifest,"application/vnd.oci.image.manifest.v1+json")
out["platform"]={"os":"linux","architecture":"amd64"}
index["manifests"]=[out]
(layout/"index.json").write_text(json.dumps(index))
print(out["digest"])
