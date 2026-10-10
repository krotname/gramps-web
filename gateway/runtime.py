"""Run the pinned Gramps API as an unprivileged private projection."""
import os
import secrets
from pathlib import Path

def create():
    data=Path(os.environ.get("GRAMPS_RUNTIME_DATA","/data/gramps"))
    data.mkdir(parents=True,exist_ok=True)
    os.environ["HOME"]=str(data)
    os.environ["GRAMPSHOME"]=str(data)
    os.environ["GRAMPS_DATABASE_PATH"]=str(data/"db")
    os.environ.pop("GRAMPS_API_CONFIG",None)
    (data/"db").mkdir(exist_ok=True)
    secret=data/"secret"
    try:
        with secret.open("x") as stream:
            os.chmod(secret,0o600)
            stream.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    from gramps_webapi.app import create_app
    from gramps_webapi.auth import add_user, user_db, User
    from gramps_webapi.dbmanager import WebDbManager
    config=dict(TREE=os.environ.get("GRAMPS_TREE_NAME","Family archive"),SECRET_KEY=secret.read_text(),
        USER_DB_URI="sqlite:///"+str(data/"users.sqlite"),SEARCH_INDEX_DB_URI="sqlite:///"+str(data/"search.sqlite"),
        STATIC_PATH="/app/static",MEDIA_BASE_DIR=str(data/"media"),REPORT_DIR=str(data/"reports"),
        EXPORT_DIR=str(data/"exports"),REGISTRATION_DISABLED=True,DISABLE_TELEMETRY=True,
        BASE_URL=os.environ["MASTER_ORIGIN"])
    for field in ["REQUEST_CACHE_CONFIG","THUMBNAIL_CACHE_CONFIG","PERSISTENT_CACHE_CONFIG"]:
        config[field]={"CACHE_TYPE":"FileSystemCache","CACHE_DIR":str(data/field.lower()),"CACHE_DEFAULT_TIMEOUT":0}
    for directory in ["media","reports","exports"]:
        (data/directory).mkdir(exist_ok=True)
    app=create_app(config,config_from_env=False)
    tree_id=WebDbManager(name=config["TREE"]).dirname
    with app.app_context():
        user_db.create_all()
        name=os.environ.get("GRAMPS_OWNER_USER","owner")
        existing=User.query.filter_by(name=name).first()
        if existing is None:
            password=os.environ.get("GRAMPS_OWNER_PASSWORD","")
            if len(password)<16:
                raise ValueError("Set the initial owner password through runtime secret storage")
            add_user(name=name,password=password,role=4,tree=tree_id)
        elif existing.role<4 or existing.tree!=tree_id:
            raise ValueError("Configured owner account does not match this tree")
    return app

if __name__=="__main__":
    create().run(host="127.0.0.1",port=5000,threaded=True)
