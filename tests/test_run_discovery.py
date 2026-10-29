import json,os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import test_pipeline as fixtures
from test_core import meta
from runs import discover,select
from process_show.core import atomic_json
from process_show.scope import Scope

class RunDiscoveryTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def run_folder(self,name,status='RUNNING',completed=4,updated=1000):
        folder=self.root/'outputs'/name
        trace=folder/'shards/shard-0/attempt-1/episodes/a/trace.jsonl'
        trace.parent.mkdir(parents=True);trace.write_text('{}\n')
        atomic_json(folder/'run_manifest.json',{'created_at':'2026-10-24T08:30:00+00:00','model':'model-A','model_sha256':'abc123','datasets':['R2R'],'expected_tasks':10})
        atomic_json(folder/'progress.json',{'status':status,'completed':completed,'expected':10})
        os.utime(folder/'progress.json',(updated,updated))
        return folder
    def test_live_and_completed_counts_and_expired_filter(self):
        self.run_folder('live')
        self.run_folder('recent','COMPLETED',10,4500)
        self.run_folder('expired','COMPLETED',10,1000)
        result=discover(self.root,self.store.root,now=5000)
        self.assertEqual({r['run_id'] for r in result['runs']},{'live','recent'})
        self.assertEqual(result['expired_runs'],1)
        live=next(r for r in result['runs'] if r['run_id']=='live')
        self.assertEqual(live['completed'],4);self.assertEqual(live['expected'],10)
        self.assertEqual(live['date'],'2026-10-24 16:30')
    def test_retained_samples_survive_run_expiry(self):
        folder=self.run_folder('retained','COMPLETED',10)
        value=meta();value.update(source_run=str(folder),run_id='retained',model='old-import-identity')
        eid=self.store.create(value)
        with self.store.db() as db:db.execute('UPDATE episodes SET selected=1 WHERE id=?',(eid,))
        result=discover(self.root,self.store.root,now=5000)
        self.assertEqual(len(result['runs']),1)
        self.assertTrue(result['runs'][0]['retained'])
        self.assertEqual(result['runs'][0]['source_model'],'old-import-identity')
    def test_no_recursive_or_score_only_import(self):
        folder=self.root/'outputs'/'scores';folder.mkdir(parents=True)
        atomic_json(folder/'run_manifest.json',{'model':'scores-only'})
        self.assertEqual(discover(self.root,self.store.root)['runs'],[])
    def test_nested_runs_in_final_root_are_discovered(self):
        original=self.run_folder('nested')
        target=self.root/'experiments'/'model-a'/'run'
        target.parent.mkdir(parents=True);original.rename(target)
        result=discover(self.root,self.store.root)
        self.assertEqual([r['run_root'] for r in result['runs']],[str(target)])
    def test_flat_episode_traces_inside_a_manifest_container_are_discovered(self):
        container=self.root/'experiments'/'new-model';container.mkdir(parents=True)
        atomic_json(container/'run_manifest.json',{'run_id':'container'})
        source=container/'jobs'/'job-a'/'attempt-01'/'trace'
        trace=source/'episodes'/'one'/'trace.jsonl';trace.parent.mkdir(parents=True);trace.write_text('{}\n')
        atomic_json(source/'run_manifest.json',{'run_id':'attempt-01','model':'PortableModel','dataset':'CustomSet','expected_tasks':1})
        result=discover(self.root,self.store.root)
        self.assertEqual([r['run_root'] for r in result['runs']],[str(source)])
        self.assertEqual(result['runs'][0]['trace_layout'],'episodes')
    def test_sdk_evaluations_are_ordered_by_start_time_within_same_day(self):
        old=meta();old.update(run_id='old',started_at='2026-10-29T01:00:00+00:00')
        new=meta(2);new.update(run_id='latest',started_at='2026-10-29T09:00:00+00:00')
        self.store.create(old);self.store.create(new)
        result=discover(self.root,self.store.root)
        self.assertEqual(result['runs'][0]['run_id'],'latest')
        self.assertIn('17:00',result['runs'][0]['date'])
    def test_sdk_runs_without_manifests_are_separated_by_model(self):
        first=meta();first['run_id']='shared-display-name'
        second=meta(2);second.update(run_id='shared-display-name',model='second-model')
        self.store.create(first);self.store.create(second)
        value=discover(self.root,self.store.root)
        self.assertEqual(len(value['runs']),2)
        self.assertEqual(len({r['id'] for r in value['runs']}),2)
        self.assertTrue(all(r['catalog_only'] for r in value['runs']))
        self.assertTrue(all(r['expected'] is None for r in value['runs']))
    def test_selected_catalog_id_is_scoped_and_stale_selection_rejected(self):
        self.run_folder('live')
        result=discover(self.root,self.store.root)
        atomic_json(self.store.root/'run-catalog.json',result)
        scope=Scope(self.root,{'project_id':'fixture','project_roots':{'windows':str(self.root),'posix':str(self.root)},'output':'out','watch_config':'process-show/config/sources.json'})
        select(scope,result['runs'][0]['id'])
        sources=json.loads((self.root/'process-show/config/sources.json').read_text())['sources']
        self.assertEqual(sources[0]['run_root'],str(self.root/'outputs/live'))
        with self.assertRaises(ValueError):select(scope,'not-a-catalog-id')
        result['scanned_at']=0;atomic_json(self.store.root/'run-catalog.json',result)
        with self.assertRaises(ValueError):select(scope,result['runs'][0]['id'])
