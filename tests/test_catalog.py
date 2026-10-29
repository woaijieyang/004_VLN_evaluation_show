import threading,time,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from unittest.mock import patch
import test_pipeline as fixtures
from process_show.scope import Scope
from process_show.service.catalog import RunCatalog

class CatalogTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def test_slow_scan_is_nonblocking_and_only_one_scan_runs(self):
        release=threading.Event();called=threading.Event()
        scope=Scope.current();catalog=RunCatalog(scope,self.store)
        def scan(*args):called.set();release.wait(3);return {'runs':[],'expired_runs':2,'ttl_seconds':3600,'errors':[],'scanned_at':time.time()}
        with patch('runs.discover',side_effect=scan) as mocked:
            start=time.monotonic();value=catalog.get()
            self.assertTrue(value['pending']);self.assertLess(time.monotonic()-start,.5)
            self.assertTrue(called.wait(1));catalog.get();self.assertEqual(mocked.call_count,1)
            release.set()
            deadline=time.monotonic()+2
            while catalog.busy and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(catalog.get()['pending'])
            self.assertEqual(catalog.get()['expired_runs'],2)
    def test_explicit_refresh_bypasses_recent_cache_without_duplicate_scans(self):
        catalog=RunCatalog(Scope.current(),self.store)
        with patch('runs.discover',return_value={'runs':[],'expired_runs':0,'ttl_seconds':3600,'errors':[],'scanned_at':time.time()}) as scan:
            catalog.get()
            deadline=time.monotonic()+2
            while catalog.busy and time.monotonic()<deadline:time.sleep(.01)
            catalog.get();self.assertEqual(scan.call_count,1)
            catalog.get(force=True)
            deadline=time.monotonic()+2
            while catalog.busy and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(scan.call_count,2)
    def test_new_candidates_visible_before_a_slow_historical_scan_finishes(self):
        release=threading.Event();published=threading.Event()
        catalog=RunCatalog(Scope.current(),self.store)
        row={'id':'latest','run_root':str(self.root),'run_id':'latest','sort_time':100}
        def scan(project,output,now,publish):
            value={'runs':[row],'expired_runs':0,'ttl_seconds':3600,'errors':[],'scanned_at':time.time()}
            publish(value);published.set();release.wait(3);return value
        with patch('runs.discover',side_effect=scan):
            catalog.get();self.assertTrue(published.wait(1))
            try:
                value=catalog.get();self.assertTrue(value['pending'])
                self.assertEqual(value['runs'][0]['id'],'latest')
                self.assertTrue(value['runs'][0]['display_name'])
            finally:
                release.set()
                deadline=time.monotonic()+2
                while catalog.busy and time.monotonic()<deadline:time.sleep(.01)
    def test_known_family_refresh_finds_new_jobs_and_updates_completed_counts(self):
        import json
        scope=Scope(self.root,{'project_id':'quick-fixture','project_roots':{'windows':str(self.root),'posix':str(self.root)},'output':'out'})
        def source(name):
            path=self.root/'family/jobs'/name/'attempt/trace';episode=path/'episodes/one';episode.mkdir(parents=True)
            (episode/'trace.jsonl').write_text('{}\n')
            (path/'run_manifest.json').write_text(json.dumps({'run_id':'attempt','model':'Portable','dataset':'D','expected_tasks':1}))
            return path
        first=source('one');catalog=RunCatalog(scope,self.store)
        catalog.value={'runs':[{'id':'old','run_root':str(first),'trace_layout':'episodes'}],'scanned_at':0}
        second=source('two');(first/'episodes/one/result.json').write_text('{}')
        catalog.refresh_known()
        rows=[r for r in catalog.value['runs'] if r.get('model')=='Portable']
        self.assertEqual({r['run_root'] for r in rows},{str(first),str(second)})
        self.assertEqual(next(r for r in rows if r['run_root']==str(first))['completed'],1)
