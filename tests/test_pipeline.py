import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from process_show.core import Store,dumps,confined
from process_show.engine import Engine
from process_show.adapters import Importer
from process_show.render import encode,ffmpeg_exe,extract_frame,instruction_pages,heading
from process_show.server import make_server
from test_core import meta


class PipelineTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[2]/"outputs"/"process-show"/"_qa"
        base=confined(Path(__file__).resolve().parents[2],base)
        base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/"out")
        self.engine=Engine(self.store)
    def tearDown(self):self.temp.cleanup()
    def episode(self,n=1):
        eid=self.engine.start(meta(n))
        for k in range(3):
            self.engine.step(eid,{"step":k,"action":"RESET" if k==0 else "STOP" if k==2 else "FORWARD","call_id":1 if k else None,"pose":{"position":[k*.25,0,0],"rotation_xyzw":[0,0,0,1]},"collided":False},Image.new("RGB",(320,240),(k*80,90,120)))
        return eid
    def test_long_text_complete_pages(self):
        text="这是很长的指令"*300
        pages=instruction_pages(text)
        self.assertGreater(len(pages),1)
        self.assertEqual(''.join(''.join(p) for p in pages),text)
    def test_heading_identity(self):self.assertEqual(heading([0,0,0,1]),(0,-1))
    def test_cjk_font_is_real_glyph(self):
        from process_show.render import font
        f=font(25)
        self.assertNotEqual(bytes(f.getmask('中')),bytes(f.getmask('\u0378')), 'Chinese must not render as missing-glyph box')
    def test_missing_steps_explicit(self):
        eid=self.engine.start(meta())
        self.engine.step(eid,{"step":0,"action":"RESET"})
        self.engine.step(eid,{"step":2,"action":"STOP"})
        self.engine.end(eid,{"total_steps":2,"status":"completed","success":False},False)
        result=self.store.get(eid)["result"]
        self.assertEqual(result["missing_steps"],[1])
        self.assertTrue(self.store.steps(eid)[1]["gap"])
        self.assertFalse(result["complete_telemetry"])
    def test_encode_seek_and_preview_cleanup(self):
        try:ffmpeg_exe()
        except RuntimeError:self.skipTest("FFmpeg unavailable")
        eid=self.episode()
        self.engine.end(eid,{"total_steps":2,"status":"completed","success":True})
        self.assertEqual(self.store.get(eid)["status"],"ready")
        self.assertFalse(list(self.store.path(eid,"previews").glob("*.webp")))
        data=json.loads(self.store.path(eid,"steps.json").read_text())
        self.assertEqual([i["start"] for i in data["index"]],[0,.5,1])
        import io
        for n in (0,1,2):
            im=Image.open(io.BytesIO(extract_frame(self.store,eid,n)))
            self.assertEqual(im.size,(1920,1080))
            # RGB center recovers the intended step's red channel, not an adjacent keyframe.
            self.assertLess(abs(im.getpixel((600,400))[0]-n*80),15)
    def test_encoding_failure_keeps_previews(self):
        from unittest.mock import patch
        eid=self.episode()
        self.engine.end(eid,{"total_steps":2,"status":"completed"},False)
        with patch('process_show.render.ffmpeg_exe',return_value=str(self.root/'missing-ffmpeg')):
            with self.assertRaises(OSError):encode(self.store,eid)
        self.assertEqual(self.store.get(eid)["status"],"encode_error")
        self.assertTrue(list(self.store.path(eid,"previews").glob("*.webp")))
    def test_case1_partial_line_and_alignment(self):
        root=self.root/"source";root.mkdir()
        Image.new("RGB",(40,30),"red").save(root/"rgb.png")
        trace=root/"trace.jsonl"
        pose={"position":[0,0,0],"rotation_xyzw":[0,0,0,1]}
        events=[{"kind":"reset","task_key":"r2r|val_unseen|en|mp3d/scene/scene.glb|1|-","episode_id":"1","state_evaluator_only":pose},
          {"kind":"model_input","call_id":1,"prompt":"Instruction: Turn left.","images":[{"path":"rgb.png"}]},
          {"kind":"model_call","call_id":1,"prompt":"Instruction: Turn left.","raw_decoded_output":"turn left 30 degree"},
          {"kind":"primitive_action","step":1,"action":2,"last_call_id":1,"after_evaluator_only":pose},
          {"kind":"primitive_action","step":2,"action":2,"last_call_id":1,"after_evaluator_only":pose},
          {"kind":"model_input","call_id":2,"prompt":"Instruction: Turn left.","images":[{"path":"rgb.png"}]},
          {"kind":"terminal","steps":2,"native_metrics":{"success":0},"termination_reason":"stop"}]
        trace.write_bytes(('\n'.join(dumps(e) for e in events[:-1])+'\n'+dumps(events[-1])[:10]).encode())
        importer=Importer(self.store,root,"test","fixture")
        eid=importer.poll(trace)
        self.assertEqual(self.store.get(eid)["status"],"live")
        steps=self.store.steps(eid)
        self.assertEqual([s["rgb_available"] for s in steps],[True,False,True])
        self.assertEqual(steps[1]["call_id"],steps[2]["call_id"])
        self.assertEqual(steps[2]["observation_call_id"],2)
        with trace.open('ab') as f:f.write((dumps(events[-1])[10:]+'\n').encode())
        before=hashlib.sha256(trace.read_bytes()).hexdigest()
        importer.poll(trace);importer.poll(trace)
        self.assertEqual(len(self.store.steps(eid)),3)
        self.assertEqual(self.store.get(eid)["status"],"pending_encode")
        self.assertEqual(hashlib.sha256(trace.read_bytes()).hexdigest(),before)
    def test_http_safety_ranges_and_pin(self):
        eid=self.episode();row=self.store.get(eid)
        file=self.store.path(eid,Path(row['directory']).name+'.mp4');self.store.own(eid,file);file.write_bytes(b'0123456789')
        with self.store.db() as db:db.execute("UPDATE episodes SET status='ready' WHERE id=?",(eid,))
        server=make_server(self.store,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            conn.request('GET','/api/episodes');r=conn.getresponse();self.assertEqual(r.status,200);self.assertEqual(len(json.loads(r.read())),1)
            conn.request('GET',f'/api/episodes/{eid}/video',headers={'Range':'bytes=2-5'});r=conn.getresponse();self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345')
            conn.request('POST',f'/api/episodes/{eid}/pin',body='{"pinned":true}',headers={'Content-Type':'application/json'});r=conn.getresponse();self.assertEqual(r.status,200);r.read();self.assertEqual(self.store.get(eid)['pinned'],1)
            conn.request('POST','/api/policy',body='{"count":0}',headers={'Content-Type':'application/json','Origin':'http://evil.invalid'});r=conn.getresponse();self.assertEqual(r.status,400);r.read()
            conn.close()
        finally:server.shutdown();server.server_close()
    def test_lazy_trace_keeps_rgb_and_materializes_on_request(self):
        from process_show.render import ensure_preview
        source=self.root/'source';source.mkdir();Image.new('RGB',(40,30),'red').save(source/'rgb.png')
        trace=source/'trace.jsonl';pose={'position':[0,0,0],'rotation_xyzw':[0,0,0,1]}
        trace.write_text(dumps({'kind':'reset','task_key':'r2r|val_unseen|en|mp3d/scene/scene.glb|1|-','episode_id':'1','state_evaluator_only':pose})+'\n'+dumps({'kind':'model_input','call_id':1,'prompt':'Instruction: test','images':[{'path':'rgb.png'}]})+'\n')
        importer=Importer(self.store,source,'test','fixture',lazy=True);eid=importer.poll(trace)
        self.assertTrue(self.store.steps(eid)[0]['rgb_available'])
        self.assertFalse(self.store.steps(eid)[0]['previews'])
        files=ensure_preview(self.store,eid,0)
        self.assertEqual(len(files),1)
        self.assertTrue(self.store.path(eid,files[0]).is_file())
        (trace.parent/'result.json').write_text(dumps({'primitive_steps':20}))
        importer.poll(trace)
        self.assertTrue(self.store.list()[0]['catching_up'])
        self.assertEqual(self.store.get(eid)['meta']['source_total_steps'],20)
    def test_sdk_queue_and_input_immutability(self):
        from process_show.sdk import Recorder
        recorder=Recorder(self.root/'sdk',queue_size=2)
        eid=recorder.start_episode(meta())
        rgb=Image.new('RGB',(32,32),'red')
        recorder.record_step({'step':0,'action':'RESET'},rgb)
        rgb.paste('blue',(0,0,32,32))
        for k in range(1,10):recorder.record_step({'step':k,'action':'FORWARD'},rgb)
        recorder.end_episode({'total_steps':9,'status':'completed','success':False})
        recorder.close()
        store=Store(self.root/'sdk');result=store.get(eid)['result']
        self.assertTrue(result['dropped_steps'])
        self.assertEqual(len(store.steps(eid)),10)
    def test_optional_hook_failure_does_not_abort_evaluator(self):
        from unittest.mock import patch
        from process_show.case1_hook import Case1Observer
        with patch.dict(os.environ,{'PROCESS_SHOW_OUTPUT':str(self.root/'blocked')}):
            with patch('process_show.case1_hook.Recorder',side_effect=OSError('disk unavailable')):
                with self.assertWarns(RuntimeWarning):observer=Case1Observer()
        observer.before(None,None,None)
        observer.after(None,None,None,None)
        observer.close()
        self.assertIsNone(observer.recorder)

if __name__=='__main__':unittest.main()
