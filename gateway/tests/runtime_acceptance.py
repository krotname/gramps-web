"""Bootstrap acceptance in the pinned API container using synthetic credentials."""
import os, tempfile, json
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='runtime-',dir='/work') as folder:
    os.environ.update(GRAMPS_RUNTIME_DATA=folder,MASTER_ORIGIN='https://example.invalid',GRAMPS_TREE_NAME='Synthetic runtime',GRAMPS_OWNER_USER='owner',GRAMPS_OWNER_PASSWORD='synthetic-runtime-password')
    from gateway.runtime import create
    app=create()
    client=app.test_client()
    response=client.post('/api/token/',json={'username':'owner','password':'synthetic-runtime-password'})
    assert response.status_code==200,response.get_data(as_text=True)
    token=response.json['access_token']
    user=client.get('/api/users/-/',headers={'Authorization':'Bearer '+token})
    assert user.status_code==200 and user.json['role']==4
    secret=(Path(folder)/'secret').read_text()
    app2=create()
    assert (Path(folder)/'secret').read_text()==secret
    response2=app2.test_client().post('/api/token/',json={'username':'owner','password':'synthetic-runtime-password'})
    assert response2.status_code==200
    assert os.getuid()!=0
    print(json.dumps({'runtime':'passed','owner_login':'passed','restart_login':'passed','uid':os.getuid()}))
