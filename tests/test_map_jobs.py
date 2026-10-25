import hashlib,json,threading,time,unittest
from unittest.mock import patch
import test_pipeline as fixtures
from process_show.scene_map import SceneMaps
from process_show.map_jobs import MapJobs

class MapJobTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def test_cached_nav_does_not_wait_for_generation_lock(self):
        maps=SceneMaps(self.root,self.root);maps.root.mkdir()
        key=hashlib.sha256(b'sha|0.00|0.05|map-v2').hexdigest()
        (maps.root/(key+'.json')).write_text('{"available":true}')
        (maps.root/(key+'.webp')).write_bytes(b'cached')
        result=[]
        with patch.object(maps,'asset',return_value=(self.root/'unused','sha')):
            with maps.lock:
                thread=threading.Thread(target=lambda:result.append(maps.get({},0,cached_only=True)),daemon=True);thread.start();thread.join(.5)
                ready=not thread.is_alive()
            thread.join(2)
        self.assertTrue(ready,'cached map waited for unrelated generation')
        self.assertTrue(result[0]['available'])
    def test_cache_miss_does_not_start_renderer(self):
        maps=SceneMaps(self.root,self.root)
        with patch.object(maps,'asset',return_value=(self.root/'not-read','sha')):
            self.assertTrue(maps.get({},0,cached_only=True)['uncached'])
    def test_job_pending_dedup_and_completed(self):
        jobs=MapJobs();release=threading.Event();calls=[]
        def generate():calls.append(1);release.wait(2);return {'available':True,'key':'done'}
        try:
            before=time.monotonic();self.assertTrue(jobs.request('one',generate)['pending']);self.assertLess(time.monotonic()-before,.2)
            self.assertTrue(jobs.request('one',generate)['pending']);release.set();jobs.queue.join()
            self.assertEqual(jobs.request('one',generate)['key'],'done');self.assertEqual(len(calls),1)
        finally:release.set();jobs.close();jobs.thread.join(1)
    def test_queue_is_bounded_and_failure_visible(self):
        jobs=MapJobs();release=threading.Event()
        def generate():release.wait(2);raise ValueError('bad scene registration')
        try:
            replies=[jobs.request(str(i),generate) for i in range(20)]
            self.assertTrue(any(r.get('state')=='busy' for r in replies));self.assertLessEqual(jobs.queue.qsize(),8)
            release.set();jobs.queue.join()
            self.assertIn('bad scene registration',jobs.request('0',generate)['reason'])
        finally:release.set();jobs.close();jobs.thread.join(1)
