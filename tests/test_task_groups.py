import json,sys,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from http.client import HTTPConnection
import test_pipeline as fixtures
from test_core import meta
from process_show.core import atomic_json
from process_show.run_sessions import RunSessions
from process_show.sync import SyncManager
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from runs import discover


class TaskGroupTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown

    def source(self,task,part,episode):
        parent=self.root/task;parent.mkdir(exist_ok=True)
        atomic_json(parent/'process-show-task.json',{'id':task,'name':task,'expected':10})
        source=parent/'workers'/part;source.mkdir(parents=True,exist_ok=True)
        value=meta(episode);value.update(source_run=str(source),run_id=part,source_kind='live_sensor')
        return self.store.create(value),source

    def get(self,port,path):
        connection=HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            connection.request('GET',path);response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def test_two_shards_are_one_task_card_and_one_port(self):
        a,_=self.source('task-A','shard-one',1);b,_=self.source('task-A','shard-two',2)
        foreign,_=self.source('task-B','shard-three',3)
        rows=discover(self.root,self.store.root)['runs']
        self.assertEqual(len(rows),2)
        first=next(r for r in rows if r['run_id']=='task-A');second=next(r for r in rows if r['run_id']=='task-B')
        self.assertEqual(len(first['members']),2)
        sync=SyncManager(self.store);sync.service_stop=threading.Event()
        catalog=SimpleNamespace(get=lambda:{'runs':rows,'pending':False})
        sessions=RunSessions(self.store,sync,catalog)
        try:
            left=sessions.open(first);right=sessions.open(second)
            self.assertEqual(len(sessions.sessions),2)
            self.assertNotEqual(left['port'],right['port'])
            _,episodes=self.get(left['port'],'/api/episodes')
            self.assertEqual({r['id'] for r in episodes},{a,b})
            self.assertTrue(all(r['evaluation_id']==first['id'] for r in episodes))
            self.assertEqual(self.get(left['port'],'/api/sync')[1]['capture_mode'],'indexed_producer')
            self.assertEqual(self.get(left['port'],'/api/episodes/'+foreign+'/replay')[0],403)
            before=left['port'];third,_=self.source('task-A','shard-new',4)
            current=next(r for r in discover(self.root,self.store.root)['runs'] if r['run_id']=='task-A')
            rows[:]=[current,second];sessions.update_binding(current)
            self.assertEqual(sessions.open(current)['port'],before)
            self.assertEqual({r['id'] for r in self.get(before,'/api/episodes')[1]},{a,b,third})
        finally:sync.service_stop.set();sessions.close_all()

    def test_saved_shard_bindings_are_not_restored_as_task_ports(self):
        _,source=self.source('task-A','part',1)
        sync=SyncManager(self.store);sync.service_stop=threading.Event();sessions=RunSessions(self.store,sync)
        sessions.prior={'old':{'chosen':{'id':'old','run_id':'part','run_root':str(source),'model':'fixture','stage':'test','catalog_only':True}}}
        try:sessions.restore();self.assertEqual(sessions.sessions,{})
        finally:sync.service_stop.set();sessions.close_all()

    def test_task_with_terminal_records_does_not_expire_while_encoding_is_pending(self):
        from process_show.engine import Engine
        eid,_=self.source('task-A','part',1)
        atomic_json(self.root/'task-A/process-show-task.json',{'id':'task-A','expected':1})
        self.store.put_step(eid,{'step':0,'action':'RESET'})
        Engine(self.store).end(eid,{'total_steps':0,'status':'completed','success':False},encode_now=False)
        task=discover(self.root,self.store.root)['runs'][0]
        self.assertEqual(task['completed'],1)
        self.assertIsNone(task['expires_at'])
