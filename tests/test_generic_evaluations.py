import hashlib,json,sys,threading,time,unittest
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
import test_pipeline as fixtures
from test_core import meta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from runs import discover
from process_show.run_sessions import RunSessions
from process_show.sync import SyncManager


class GenericEvaluationTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown

    def write(self,folder,name,model,complete):
        value=meta();value.update(run_id='same-run',model=model,dataset='CustomNavigationSet')
        events=[{'schema_version':1,'type':'start','metadata':value},
                {'schema_version':1,'type':'step','data':{'step':0,'action':'RESET'}}]
        if complete:events.append({'schema_version':1,'type':'end','result':{'total_steps':0,'status':'completed','success':True}})
        path=folder/name;path.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
        return path

    def get(self,port,path='/api/episodes'):
        connection=HTTPConnection('127.0.0.1',port,timeout=5)
        try:
            connection.request('GET',path);response=connection.getresponse()
            self.assertEqual(response.status,200);return json.loads(response.read())
        finally:connection.close()

    def test_generic_manifest_without_habitat_map_can_replay_and_export(self):
        from PIL import Image
        from process_show.engine import Engine
        from process_show.server import make_server
        from process_show.render import encode
        folder=self.root/'custom-simulator';folder.mkdir()
        (folder/'run_manifest.json').write_text(json.dumps({'model':'CustomSimulator','navmesh_profile':'optional-but-not-verified.json'}))
        value=meta();value.update(model='CustomSimulator',source_run=str(folder),source_kind='live_sensor')
        engine=Engine(self.store);eid=engine.start(value)
        engine.step(eid,{'step':0,'action':'RESET','pose':{'position':[0,0,0],'rotation_xyzw':[0,0,0,1]}},Image.new('RGB',(32,32)),render_preview=False)
        engine.end(eid,{'total_steps':0,'status':'completed','success':False},encode_now=False)
        server=make_server(self.store,port=0);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:self.assertFalse(self.get(server.server_port,'/api/episodes/'+eid+'/map')['available'])
        finally:server.shutdown();server.server_close()
        encode(self.store,eid)
        self.assertEqual(self.store.get(eid)['status'],'ready')

    def test_arbitrary_models_in_one_jsonl_folder_have_independent_ports_and_live_updates(self):
        folder=self.root/'third-party-evaluation';folder.mkdir()
        a=self.write(folder,'alpha.jsonl','ThirdParty-Alpha',False)
        self.write(folder,'beta.jsonl','Unrelated-Beta',True)
        (folder/'scores.jsonl').write_text('{"score":1}\n')
        entries=discover(self.root,self.store.root)['runs']
        self.assertEqual(len(entries),2)
        self.assertTrue(all(e['adapter']=='generic' for e in entries))
        sync=SyncManager(self.store);sync.service_stop=threading.Event()
        sessions=RunSessions(self.store,sync,SimpleNamespace(get=lambda:{'runs':entries,'pending':False}))
        ports={}
        try:
            for chosen in entries:ports[chosen['model']]=sessions.open(chosen)['port']
            self.assertEqual(len(set(ports.values())),2)
            deadline=time.monotonic()+5
            for model,port in ports.items():
                while not (rows:=self.get(port)):
                    self.assertLess(time.monotonic(),deadline);time.sleep(.02)
                self.assertEqual({r['meta']['model'] for r in rows},{model})
            port=ports['ThirdParty-Alpha'];started=time.monotonic()
            with a.open('a') as stream:
                stream.write(json.dumps({'schema_version':1,'type':'step','data':{'step':1,'action':'FORWARD'}})+'\n')
                stream.write('{"schema_version":1,"type":"end",')
            while (rows:=self.get(port))[0]['max_step']!=1:
                self.assertLess(time.monotonic()-started,3);time.sleep(.02)
            self.assertEqual(rows[0]['status'],'live')
            with a.open('a') as stream:stream.write('"result":{"total_steps":1,"status":"completed","success":false}}\n')
            deadline=time.monotonic()+3
            while self.get(port)[0]['status']=='live':
                self.assertLess(time.monotonic(),deadline);time.sleep(.02)
            self.assertEqual(self.get(ports['Unrelated-Beta'])[0]['max_step'],0)
            self.assertEqual(len(sync.sources),2)
            self.assertEqual(len(discover(self.root,self.store.root)['runs']),2)
        finally:
            sync.service_stop.set();sessions.close_all()
            for source in sync.sources:self.assertTrue(source.done.wait(3))


if __name__=='__main__':unittest.main()
