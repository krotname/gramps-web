"""Projection recovery and privacy acceptance cases."""
import copy, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from gateway.projection import objects, project, handle, KINDS
from gateway.tree import Gedcom
from gateway.tests.test_tree import FIXTURE

class MemoryAPI:
    def __init__(self): self.data = {}; self.writes = []
    def request(self, method, path, data=None):
        parsed = urlsplit(path); parts = parsed.path.strip('/').split('/')
        if method == 'GET' and len(parts) == 2:
            kind = next(k for k,v in KINDS.items() if v == parts[1])
            rows = [{'handle':h} for h,o in self.data.items() if o['_class']==kind]
            page = int(parse_qs(parsed.query).get('page',['1'])[0]); size = int(parse_qs(parsed.query).get('pagesize',['1000'])[0])
            return rows[(page-1)*size:page*size], {'X-Total-Count':str(len(rows))}
        if method == 'GET':
            if parts[-1] not in self.data: raise HTTPError(path,404,'Missing',None,None)
            return copy.deepcopy(self.data[parts[-1]]), {}
        self.writes.append((method,path))
        if method == 'POST':
            for obj in data: self.data[obj['handle']] = copy.deepcopy(obj)
        elif method == 'PUT': self.data[parts[-1]] = copy.deepcopy(data)
        elif method == 'DELETE': self.data.pop(parts[-1],None)
        return None, {}

class ProjectionRecoveryTest(unittest.TestCase):
    def test_resn_privacy_and_confidential_survive_on_projected_objects(self):
        tree = Gedcom(FIXTURE.replace(b'1 NAME Anna', b'1 RESN privacy\r\n1 CENS\r\n2 RESN confidential\r\n2 DATE 1910\r\n2 PLAC Hidden\r\n2 NOTE Secret\r\n1 NAME Anna'))
        plan = objects(tree,date_parser=lambda d:{'_class':'Date','text':d})
        for key in ['I1','I1:event:CENS:0','I1:event:CENS:0:note:0','place:Hidden']:
            self.assertTrue(plan[handle(key)]['private'])
        self.assertFalse(plan[handle('I2')]['private'])

    def test_standard_and_custom_events_keep_their_dates_places_and_notes(self):
        body = b''.join(('1 '+tag+'\r\n2 DATE 1901\r\n2 PLAC Example\r\n2 NOTE Evidence\r\n').encode() for tag in ['CENS','IMMI','EMIG','NATU','RETI','_MILT'])
        tree = Gedcom(FIXTURE.replace(b'1 NAME Anna',body+b'1 NAME Anna'))
        plan = objects(tree,date_parser=lambda d:{'_class':'Date','text':d})
        for tag in ['CENS','IMMI','EMIG','NATU','RETI','_MILT']:
            event = plan[handle('I1:event:'+tag+':0')]
            self.assertEqual(event['date']['text'],'1901'); self.assertTrue(event['place']); self.assertTrue(event['note_list'])

    def test_projection_reset_recreates_objects_without_trusting_stale_index(self):
        with tempfile.TemporaryDirectory() as folder, patch('gateway.projection.date_dict',side_effect=lambda d:{'_class':'Date','text':d}):
            api = MemoryAPI(); tree = Gedcom(FIXTURE)
            project(api,folder,tree); before = copy.deepcopy(api.data)
            api.data.clear(); project(api,folder,tree)
            self.assertEqual(api.data,before)
            api.writes.clear(); project(api,folder,tree)
            self.assertEqual(api.writes,[])

    def test_unknown_existing_database_is_blocked_before_creating_duplicates(self):
        with tempfile.TemporaryDirectory() as folder, patch('gateway.projection.date_dict',side_effect=lambda d:{'_class':'Date','text':d}):
            api = MemoryAPI(); api.data['foreign']={'_class':'Person','handle':'foreign'}
            with self.assertRaises(ValueError): project(api,folder,Gedcom(FIXTURE))
            self.assertEqual(api.writes,[])
