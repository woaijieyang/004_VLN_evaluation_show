import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from process_show.core import Store,confined,slug,validate_step


def meta(n=1,dataset="R2R"):
    return dict(run_id="test-run",attempt="01",model="fixture",stage="test",dataset=dataset,split="val",scene_id="scene",episode_id=str(n),instruction="Turn left. 左转，再前进。",started_at="2026-10-24T08:00:00+00:00")


class CoreTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[2]/"outputs"/"process-show"/"_qa"
        base=confined(Path(__file__).resolve().parents[2],base)
        base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.now=10000
        self.store=Store(self.temp.name,lambda:self.now)
    def tearDown(self):self.temp.cleanup()
    def ready(self,n,outcome="success",dataset="R2R"):
        eid=self.store.create(meta(n,dataset))
        p=self.store.path(eid,"test.mp4");self.store.own(eid,p);p.write_bytes(b"owned")
        with self.store.db() as db:db.execute("UPDATE episodes SET status='ready',ready=?,outcome=? WHERE id=?",(self.now,outcome,eid))
        return eid
    def test_retention_balanced_cross_group(self):
        ids=[self.ready(n,"success" if n<15 else "failure") for n in range(30)]
        chosen=self.store.cleanup(True)["selected"]
        self.assertEqual(len(chosen),5)
        self.assertEqual(sum(self.store.get(e)["outcome"]=="success" for e in chosen),3)
        self.now+=3599;self.assertEqual(self.store.cleanup()["expired"],[])
        self.now+=1;self.assertEqual(len(self.store.cleanup()["expired"]),25)
        self.assertEqual(len(self.store.list()),5)
        self.assertEqual(self.store.cleanup(True)["selected"],chosen)
    def test_dataset_independent_fill(self):
        for n in range(13):self.ready(n,dataset="RxR")
        for n in range(3):self.ready(n,dataset="R2R")
        self.assertEqual(len(self.store.cleanup(True)["selected"]),8)
    def test_pin_over_quota_and_restart(self):
        ids=[self.ready(n) for n in range(12)]
        for eid in ids:self.store.pin(eid,True)
        self.now+=4000
        other=Store(self.temp.name,lambda:self.now)
        self.assertEqual(len(other.cleanup()["selected"]),12)
        self.assertEqual(len(other.list()),12)
    def test_explicit_selection_fill_overflow_and_restart(self):
        ids=[self.ready(n,"success" if n%2 else "failure") for n in range(12)]
        others=[self.ready(n,dataset="RxR") for n in range(8)]
        self.store.set_policy({"selections":{"R2R":["0","1","1"]}})
        selected=set(self.store.cleanup(True)["selected"])
        self.assertTrue(set(ids[:2])<=selected)
        self.assertEqual(len(selected&set(ids)),5)
        self.assertEqual(len(selected&set(others)),5)
        self.store.set_policy({"selections":{"R2R":[str(n) for n in range(7)]}})
        selected=set(self.store.cleanup(True)["selected"])
        self.assertEqual(selected&set(ids),set(ids[:7]))
        other=Store(self.temp.name,lambda:self.now)
        self.assertEqual(set(other.cleanup(True)["selected"]),selected)
        self.now+=3599
        self.assertEqual(other.cleanup()["expired"],[])
        self.now+=1
        other.cleanup()
        self.assertTrue(all(other.get(e)["deleted"] is None for e in ids[:7]))

    def test_selection_missing_future_and_validation(self):
        for n in range(7):self.ready(n)
        self.store.set_policy({"selections":{"R2R":["future"]}})
        self.assertEqual(len(self.store.cleanup(True)["selected"]),5)
        eid=self.ready("future","unknown")
        self.assertIn(eid,self.store.cleanup(True)["selected"])
        self.store.pin(eid,True)
        self.store.set_policy({"selections":{"R2R":[]}})
        self.assertIn(eid,self.store.cleanup(True)["selected"])
        original=self.store.setting("policy")
        for bad in ([],{"R2R":"1"},{"R2R":[1]},{"R2R":[""]}):
            with self.assertRaises(ValueError):self.store.set_policy({"selections":bad})
        self.assertEqual(self.store.setting("policy"),original)

    def test_lease_protects_and_expiry(self):
        eid=self.ready(1);self.store.set_policy({"count":0});self.now+=3601
        with self.store.lease(eid):self.assertEqual(self.store.cleanup()["expired"],[])
        self.assertEqual(self.store.cleanup()["expired"],[eid])
        with self.assertRaises(ValueError):self.store.pin(eid,True)
    def test_rules_and_lower_count(self):
        ids=[self.ready(n) for n in range(3)]
        self.store.set_policy({"count":0,"rules":[{"episode_id":"1"}]})
        self.now+=4000
        self.store.cleanup()
        self.assertEqual([r["id"] for r in self.store.list()],[ids[1]])
    def test_unknown_not_failure(self):
        eid=self.ready(1,"unknown");self.now+=4000
        self.assertEqual(self.store.cleanup()["expired"],[eid])
    def test_owned_only(self):
        eid=self.ready(1);foreign=self.store.path(eid,"external.txt");foreign.write_text("preserve")
        self.store.set_policy({"count":0});self.now+=4000;self.store.cleanup()
        self.assertEqual(foreign.read_text(),"preserve")
    @unittest.skipIf(os.name=="nt","symlink permission varies on Windows")
    def test_symlink_cleanup_fails_before_deletion(self):
        eid=self.ready(1);p=self.store.path(eid,"test.mp4");p.unlink()
        target=Path(self.temp.name)/"protected";target.write_text("protected");p.symlink_to(target)
        self.store.set_policy({"count":0});self.now+=4000
        with self.assertRaises(ValueError):self.store.cleanup()
        self.assertTrue(self.store.path(eid,"episode.json").exists())
        self.assertEqual(target.read_text(),"protected")
    def test_traversal(self):
        with self.assertRaises(ValueError):confined(self.store.root,self.store.root/".."/"outside")
    def test_identity_instruction_drift(self):
        self.store.create(meta())
        changed=meta();changed["instruction"]="changed"
        with self.assertRaises(ValueError):self.store.create(changed)
    def test_identity_attempt_collision(self):
        a=meta();b=meta();b["attempt"]="02"
        self.assertNotEqual(self.store.create(a),self.store.create(b))
    def test_step_invalid(self):
        for value in (-1,True,"1",1.5):
            with self.assertRaises(ValueError):validate_step({"step":value})
        with self.assertRaises(ValueError):validate_step({"step":1,"action":"F","pose":{"position":[float("nan"),0,0]}})
    def test_slug(self):
        self.assertNotEqual(slug("CON"),"CON")
        self.assertNotIn("/",slug("a/b"))
    def test_storage_limit(self):
        eid=self.store.create(meta());p=self.store.path(eid,"large");self.store.own(eid,p);p.write_bytes(b"x"*(1024**2+1))
        self.store.set_policy({"max_bytes":1024**2})
        with self.assertRaises(OSError):self.store.capacity()

    def test_custom_cache_boundary_pin_and_restart(self):
        ordinary=self.ready('ordinary');fixed=self.ready('fixed')
        self.store.pin(fixed,True)
        self.store.set_policy({'count':0,'ttl_seconds':120})
        restarted=Store(self.temp.name,lambda:self.now)
        self.assertEqual(restarted.setting('policy')['ttl_seconds'],120)
        self.now+=119
        self.assertEqual(restarted.cleanup()['expired'],[])
        self.now+=1
        self.assertEqual(restarted.cleanup()['expired'],[ordinary])
        self.assertIsNone(restarted.get(fixed)['deleted'])

    def test_cache_duration_limits(self):
        self.assertEqual(self.store.setting('policy')['ttl_seconds'],3600)
        for seconds in (60,900,3600,2592000):
            self.assertEqual(self.store.set_policy({'ttl_seconds':seconds})['ttl_seconds'],seconds)
        for seconds in (0,59,2592001,1.5,True):
            with self.assertRaises(ValueError):self.store.set_policy({'ttl_seconds':seconds})

if __name__=="__main__":unittest.main()
