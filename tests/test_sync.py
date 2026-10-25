import json,hashlib,threading,unittest
from pathlib import Path
from http.client import HTTPConnection
from unittest.mock import patch
import test_pipeline as fixtures
from process_show.adapters import Importer
from process_show.sync import SyncManager
from process_show.server import make_server


class SyncTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def source(self,complete=True):
        root=self.root/'source';folder=root/'shards/shard-00/attempt-01/episodes/one';folder.mkdir(parents=True)
        trace=folder/'trace.jsonl';pose={'position':[0,0,0],'rotation_xyzw':[0,0,0,1]}
        events=[{'kind':'reset','task_key':'r2r|val_unseen|en|mp3d/scene/scene.glb|1|-','episode_id':'1','state_evaluator_only':pose},{'kind':'model_input','call_id':1,'prompt':'Instruction: Turn left.','images':[]},{'kind':'primitive_action','step':1,'action':2,'last_call_id':1,'after_evaluator_only':pose}]
        if complete:events.append({'kind':'terminal','steps':1,'native_metrics':{'success':1},'termination_reason':'stop'})
        trace.write_text(''.join(json.dumps(e)+'\n' for e in events))
        if complete:(folder/'result.json').write_text('{"primitive_steps":1}')
        importer=Importer(self.store,root,'test','fixture',lazy=True)
        manager=SyncManager(self.store);worker=manager.add(importer,False)
        return trace,manager,worker
    def drain(self,worker):
        for _ in range(40):
            worker.tick()
            if not worker.queue:return
        self.fail('sync did not drain')
    def test_history_backfill_idempotent_restart_and_source_unchanged(self):
        trace,manager,worker=self.source();before=hashlib.sha256(trace.read_bytes()).hexdigest()
        self.drain(worker);self.assertEqual(self.store.list(),[]);self.assertEqual(worker.snapshot()['excluded_history'],1)
        manager.request(True);self.drain(worker)
        self.assertEqual(len(self.store.list()),1);eid=self.store.list()[0]['id']
        self.assertEqual(len(self.store.steps(eid)),2)
        manager.request(True);self.drain(worker)
        self.assertEqual(len(self.store.steps(eid)),2)
        restarted=SyncManager(self.store).add(worker.importer,False)
        self.assertTrue(restarted.history)
        self.assertEqual(hashlib.sha256(trace.read_bytes()).hexdigest(),before)
    def test_partial_line_then_append(self):
        trace,manager,worker=self.source(False)
        terminal=json.dumps({'kind':'terminal','steps':1,'native_metrics':{},'termination_reason':'stop'})
        with trace.open('a') as f:f.write(terminal[:10])
        self.drain(worker);eid=self.store.list()[0]['id']
        self.assertEqual(self.store.get(eid)['status'],'live')
        with trace.open('a') as f:f.write(terminal[10:]+'\n')
        worker.next_scan=0;self.drain(worker)
        self.assertEqual(self.store.get(eid)['status'],'pending_encode')
    def test_retry_reports_errors_without_resetting_cursor(self):
        trace,manager,worker=self.source(False)
        with patch.object(worker.importer,'poll',side_effect=OSError('transient read failure')):self.drain(worker)
        self.assertIn('transient',worker.snapshot()['errors'][0]['message'])
        manager.request(True);self.drain(worker)
        self.assertEqual(worker.snapshot()['errors'],[])
        self.assertEqual(len(self.store.list()),1)
    def test_expired_history_is_not_resurrected(self):
        trace,manager,worker=self.source();manager.request(True);self.drain(worker)
        eid=self.store.list()[0]['id']
        with self.store.db() as db:
            db.execute("UPDATE episodes SET status='expired',deleted=1 WHERE id=?",(eid,))
            db.execute('UPDATE imports SET state=? WHERE path=?',(json.dumps({'eid':eid,'expired':True}),str(trace)))
        manager.request(True);self.drain(worker)
        self.assertEqual(self.store.list(),[]);self.assertEqual(worker.snapshot()['expired'],1)
    def test_late_result_updates_metadata_without_trace_growth(self):
        trace,manager,worker=self.source(False);self.drain(worker)
        (trace.parent/'result.json').write_text('{"primitive_steps":1}')
        worker.next_scan=0;self.drain(worker)
        self.assertTrue(self.store.list()[0]['catching_up'])
    def test_restart_does_not_report_complete_files_as_pending(self):
        trace,manager,worker=self.source();manager.request(True);self.drain(worker)
        restarted=SyncManager(self.store).add(worker.importer,True)
        restarted.scan()
        self.assertEqual(restarted.snapshot()['pending'],0)
        self.assertEqual(list(restarted.queue),[])

    def test_rescan_preserves_pending_history_order(self):
        from collections import deque
        trace,manager,worker=self.source()
        second=trace.parent.parent/'two'/'trace.jsonl';second.parent.mkdir()
        second.write_bytes(trace.read_bytes());(second.parent/'result.json').write_text('{"primitive_steps":1}')
        manager.request(True);worker.scan();worker.queue=deque([second,trace])
        worker.scan()
        self.assertEqual(list(worker.queue),[second,trace])

    def test_http_refresh_rejects_foreign_path_and_is_async(self):
        trace,manager,worker=self.source();server=make_server(self.store,port=0);server.sync_manager=manager
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            for body,status in [({'history':True},202),({'history':True,'run_root':'/outside'},400),({'history':'yes'},400)]:
                conn.request('POST','/api/refresh',json.dumps(body),{'Content-Type':'application/json'});response=conn.getresponse();self.assertEqual(response.status,status);response.read()
            self.assertEqual(self.store.list(),[])  # HTTP never executes source import inline
            conn.request('GET','/api/sync');response=conn.getresponse();self.assertEqual(json.loads(response.read())['configured_sources'],1)
            conn.close()
        finally:server.shutdown();server.server_close()
