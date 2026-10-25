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
