import json,socket,threading,unittest
from http.client import HTTPConnection
import test_pipeline as fixtures
from test_core import meta
from process_show.run_sessions import RunSessions
from process_show.sync import SyncManager

class RunSessionTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def chosen(self,name):
        value=meta();value['run_id']=name
        eid=self.store.create(value)
        return eid,{'id':name,'run_id':name,'run_root':str(self.store.root),'model':value['model'],'source_model':value['model'],'stage':'test','catalog_only':True}
    def manager(self):
        sync=SyncManager(self.store);sync.service_stop=threading.Event()
        return RunSessions(self.store,sync)
    def test_runs_get_distinct_ports_and_cannot_read_or_pin_other_run(self):
        first,a=self.chosen('one');second,b=self.chosen('two');manager=self.manager()
        try:
            left=manager.open(a);right=manager.open(b)
            self.assertNotEqual(left['port'],right['port'])
            self.assertEqual(manager.open(a)['port'],left['port'])
            conn=HTTPConnection('127.0.0.1',left['port'],timeout=5)
            conn.request('GET','/api/episodes');r=conn.getresponse();rows=json.loads(r.read())
            self.assertEqual([row['id'] for row in rows],[first])
            conn.request('GET',f'/api/episodes/{second}/replay');r=conn.getresponse();self.assertEqual(r.status,403);r.read()
            conn.request('POST',f'/api/episodes/{second}/pin',json.dumps({'pinned':True}),{'Content-Type':'application/json'});r=conn.getresponse();self.assertEqual(r.status,403);r.read()
            conn.close()
        finally:manager.close_all()
    def test_occupied_port_is_skipped_and_explicit_stop_closes_only_one(self):
        _,a=self.chosen('one');_,b=self.chosen('two');manager=self.manager()
        with socket.socket() as busy:
            busy.bind(('127.0.0.1',0));busy.listen();port=busy.getsockname()[1]
            manager.prior[a['id']]={'port':port}
            try:
                left=manager.open(a);right=manager.open(b)
                self.assertNotEqual(left['port'],port)
                manager.close(a['id'])
                conn=HTTPConnection('127.0.0.1',right['port'],timeout=5);conn.request('GET','/api/health');r=conn.getresponse();self.assertEqual(json.loads(r.read())['run_id'],'two');conn.close()
                self.assertNotIn('one',manager.prior)
            finally:manager.close_all()
    def test_outside_final_root_is_rejected(self):
        from process_show.scope import Scope
        manager=self.manager();_,a=self.chosen('one');a['run_root']=str(Scope.current().project.parent/'rejected-fake-run')
        try:
            with self.assertRaises(ValueError):manager.open(a)
        finally:manager.close_all()
    def test_same_run_label_different_model_is_not_mixed(self):
        first,a=self.chosen('same-label')
        metadata=meta(2);metadata.update(run_id='same-label',model='different-model')
        other=self.store.create(metadata);manager=self.manager()
        try:
            address=manager.open(a)
            conn=HTTPConnection('127.0.0.1',address['port'],timeout=5)
            conn.request('GET','/api/episodes');r=conn.getresponse();rows=json.loads(r.read())
            self.assertEqual([row['id'] for row in rows],[first])
            conn.request('GET',f'/api/episodes/{other}/replay');r=conn.getresponse();self.assertEqual(r.status,403);r.read();conn.close()
        finally:manager.close_all()
    def test_client_probe_is_scoped_and_does_not_grant_data_cors(self):
        _,chosen=self.chosen('probe-run');manager=self.manager()
        try:
            address=manager.open(chosen);conn=HTTPConnection('127.0.0.1',address['port'],timeout=5)
            origin='http://127.0.0.1:47831'
            conn.request('GET','/api/health?probe='+address['probe_token'],headers={'Origin':origin})
            response=conn.getresponse();self.assertEqual(response.status,200);self.assertEqual(response.getheader('Access-Control-Allow-Origin'),origin)
            self.assertEqual(json.loads(response.read())['evaluation_id'],chosen['id'])
            conn.request('GET','/api/health?probe=wrong',headers={'Origin':origin});response=conn.getresponse();self.assertEqual(response.status,400);self.assertIsNone(response.getheader('Access-Control-Allow-Origin'));response.read()
            conn.request('GET','/api/episodes?probe='+address['probe_token'],headers={'Origin':origin});response=conn.getresponse();self.assertEqual(response.status,400);response.read()
            conn.request('GET','/api/health?probe='+address['probe_token'],headers={'Origin':'http://untrusted.invalid'});response=conn.getresponse();self.assertEqual(response.status,400);response.read();conn.close()
        finally:manager.close_all()
