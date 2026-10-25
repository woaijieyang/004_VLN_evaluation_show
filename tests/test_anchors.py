import unittest,json,gzip,hashlib
from pathlib import Path
import test_pipeline as fixtures
from test_core import meta
from process_show.anchors import resolve_anchors


class AnchorTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def test_final_stop_is_not_a_goal(self):
        markers=resolve_anchors(meta(),[{'step':0,'pose':{'position':[1,0,2]}},{'step':10,'pose':{'position':[8,0,9]}}])
        self.assertEqual(markers['start'],[1,0,2]);self.assertEqual(markers['goals'],[])
    def test_annotation_binding_and_mismatch(self):
        source=self.root/'run';source.mkdir()
        annotation=self.root/'annotation.json.gz'
        data={'episodes':[{'episode_id':1,'scene_id':'mp3d/scene/scene.glb','start_position':[0,0,0],'instruction':{'instruction_text':meta()['instruction']},'goals':[{'position':[4,0,7],'radius':3}]}]}
        annotation.write_bytes(gzip.compress(json.dumps(data).encode()))
        (source/'dataset-spec.json').write_text(json.dumps({'annotation':str(annotation),'annotation_sha256':hashlib.sha256(annotation.read_bytes()).hexdigest()}))
        m=dict(meta(),source_kind='case1_trace',source_run=str(source))
        steps=[{'step':0,'pose':{'position':[0,0,0]}}]
        good=resolve_anchors(m,steps,self.root);self.assertEqual(good['goals'][0]['position'],[4,0,7])
        m['instruction']='different';self.assertEqual(resolve_anchors(m,steps,self.root)['goals'],[])
        m['instruction']=meta()['instruction'];annotation.write_bytes(b'changed after cache')
        self.assertEqual(resolve_anchors(m,steps,self.root)['goals'],[])
    def test_explicit_goal_metadata(self):
        result=resolve_anchors(dict(meta(),goal_position=[0,0,0]),[])
        self.assertEqual(result['goals'][0]['position'],[0,0,0])
