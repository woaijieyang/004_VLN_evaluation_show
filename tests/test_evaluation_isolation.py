import json,sys,threading,unittest
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import test_pipeline as fixtures
from test_core import meta
from process_show.server import make_server
from process_show.run_sessions import RunSessions
from process_show.sync import SyncManager
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from runs import discover


class EvaluationIsolationTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown

    def indexed(self,folder,episode=1):
        source=self.root/folder/'trace';source.mkdir(parents=True,exist_ok=True)
        value=meta(episode);value.update(run_id='same-display-name',source_run=str(source),source_kind='live_sensor')
        return self.store.create(value),source

    def request(self,port,path,body=None):
        connection=HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            connection.request('POST' if body is not None else 'GET',path,
                               json.dumps(body) if body is not None else None,
                               {'Content-Type':'application/json'} if body is not None else {})
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def test_same_ids_in_different_source_directories_do_not_merge(self):
        first,_=self.indexed('batch-one');second,_=self.indexed('batch-two')
        self.assertNotEqual(first,second)
        self.assertEqual(len(self.store.list()),2)

    def test_existing_episode_identity_remains_usable(self):
        import hashlib
        from process_show.core import dumps,validate_meta
        value=validate_meta(meta());source=self.root/'source';source.mkdir()
        value['source_run']=str(source)
        legacy=hashlib.sha256(dumps([value.get(k) for k in ('run_id','attempt','model','dataset','split','scene_id','episode_id','instruction_id')]).encode()).hexdigest()
        created=self.store.create(value)
        if created!=legacy:
            with self.store.db() as db:db.execute('UPDATE episodes SET id=? WHERE id=?',(legacy,created))
        self.assertEqual(self.store.create(value),legacy)

    def test_indexed_live_sensor_evaluations_are_selectable_without_case1_trace(self):
        self.indexed('batch-one');self.indexed('batch-two',2)
        result=discover(self.root,self.store.root)
        self.assertEqual(len(result['runs']),2)
        self.assertEqual(len({r['id'] for r in result['runs']}),2)
        self.assertTrue(all(r['catalog_only'] and r['source_run'] for r in result['runs']))

    def test_control_port_requires_selection_instead_of_showing_every_run(self):
        eid,_=self.indexed('batch-one')
        server=make_server(self.store,port=0);server.run_sessions=object()
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            self.assertEqual(self.request(server.server_port,'/api/episodes'),(200,[]))
            self.assertEqual(self.request(server.server_port,'/api/episodes/'+eid+'/replay')[0],403)
            _,health=self.request(server.server_port,'/api/health')
            self.assertTrue(health['selection_required'])
        finally:server.shutdown();server.server_close()

    def test_same_run_name_and_model_have_distinct_ports_and_scoped_state(self):
        first,left_root=self.indexed('batch-one');second,right_root=self.indexed('batch-two',2)
        def choice(source,identity):
            return {'id':identity,'run_id':'same-display-name','run_root':str(source),
                    'source_run':str(source),'model':'fixture','source_model':'fixture',
                    'stage':'test','catalog_only':True}
        left,right=choice(left_root,'left'),choice(right_root,'right')
        sync=SyncManager(self.store);sync.service_stop=threading.Event()
        catalog=SimpleNamespace(get=lambda:{'runs':[left,right],'pending':False})
        manager=RunSessions(self.store,sync,catalog)
        try:
            a=manager.open(left);b=manager.open(right)
            self.assertNotEqual(a['port'],b['port'])
            _,rows=self.request(a['port'],'/api/episodes')
            self.assertEqual([r['id'] for r in rows],[first])
            self.assertEqual(rows[0]['evaluation_id'],'left')
            self.assertEqual(self.request(a['port'],'/api/episodes/'+second+'/replay')[0],403)
            _,listing=self.request(a['port'],'/api/runs')
            self.assertEqual(listing['selected']['id'],'left')
            self.assertIn('evaluation=left',a['url'])
        finally:sync.service_stop.set();manager.close_all()

    def test_refresh_and_sync_only_touch_the_bound_evaluation(self):
        first,left_root=self.indexed('batch-one');self.indexed('batch-two',2)
        manager=SyncManager(self.store)
        def source(root):
            return SimpleNamespace(root=str(root),request=Mock(),snapshot=lambda:{
                'source':root.name,'pending':0,'errors':[]})
        a,b=source(left_root),source(self.root/'batch-two/trace');manager.sources=[a,b]
        chosen={'id':'left','run_id':'same-display-name','run_root':str(left_root),
                'source_run':str(left_root),'model':'fixture','catalog_only':True}
        server=make_server(self.store,port=0);server.bound_run=chosen;server.sync_manager=manager
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            status,value=self.request(server.server_port,'/api/refresh',{'history':True})
            self.assertEqual(status,202)
            self.assertLessEqual(len(value['sources']),1)
            b.request.assert_not_called()
            self.assertEqual(value['evaluation_id'],'left')
        finally:server.shutdown();server.server_close()

    def test_unrelated_updates_do_not_change_evaluation_revision(self):
        from process_show.service.evaluations import revision
        first,source=self.indexed('batch-one');other,_=self.indexed('batch-two',2)
        chosen={'run_id':'same-display-name','run_root':str(source),'source_run':str(source),
                'model':'fixture','catalog_only':True}
        before=revision(self.store,chosen)
        self.store.error(other,'other evaluation changed')
        self.assertEqual(revision(self.store,chosen),before)
        self.store.error(first,'selected evaluation changed')
        self.assertNotEqual(revision(self.store,chosen),before)

    def test_live_capture_publishes_without_composing_export_frames(self):
        from PIL import Image
        from unittest.mock import patch
        from process_show.engine import Engine
        eid,_=self.indexed('batch-one')
        with patch('process_show.capture.engine.compose',side_effect=AssertionError('Not on capture path')):
            Engine(self.store).step(eid,{'step':0,'action':'RESET'},Image.new('RGB',(32,32)),render_preview=False)
        step=self.store.steps(eid)[0]
        self.assertTrue(step['rgb_available']);self.assertIn('published_at',step)
        self.assertTrue(self.store.path(eid,step['observation_asset']).is_file())


if __name__=='__main__':unittest.main()
