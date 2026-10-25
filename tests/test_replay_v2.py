import io,json,sys,threading,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import test_pipeline as fixtures
from test_core import meta
from process_show.presentation import observation_step,observation_image,replay_payload


class ReplayV2Tests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    episode=fixtures.PipelineTests.episode
    def test_missing_step_uses_previous_not_future_and_strict_is_empty(self):
        records=[{'step':12,'rgb_available':True},{'step':13,'rgb_available':False},{'step':15,'rgb_available':True}]
        self.assertEqual(observation_step(records,13)['step'],12)
        self.assertIsNone(observation_step(records,13,strict=True))
        self.assertIsNone(observation_step(records,0))
    def test_replay_schema_exposes_actual_and_observation_steps(self):
        eid=self.episode()
        self.engine.step(eid,{'step':3,'action':'FORWARD'})
        payload=replay_payload(self.store,eid)
        self.assertEqual(payload['steps'][3]['step'],3)
        self.assertEqual(payload['steps'][3]['observation_step'],2)
        self.assertFalse(payload['steps'][3]['rgb_available'])
    def test_sdk_keeps_small_observation_for_instant_replay(self):
        eid=self.episode()
        data=self.store.steps(eid)[1]
        self.assertIn('observation_asset',data)
        rgb,n=observation_image(self.store,eid,1)
        self.assertEqual(n,1)
        self.assertEqual(rgb.size,(320,240))
    def test_geometry_has_furniture_and_excludes_roof(self):
        import struct,numpy as np
        from process_show.scene_geometry import textured_topdown
        vertices=np.array([[-2,0,-2],[2,0,-2],[2,0,2],[-2,0,2],[-.5,1,-.5],[.5,1,-.5],[.5,1,.5],[-.5,1,.5],[-2,3,-2],[2,3,-2],[2,3,2],[-2,3,2]],dtype='<f4')
        indices=np.array([0,1,2,0,2,3,4,5,6,4,6,7,8,9,10,8,10,11],dtype='<u2')
        binary=vertices.tobytes()+indices.tobytes();binary+=b'\0'*((-len(binary))%4)
        doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':len(binary)}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':vertices.nbytes},{'buffer':0,'byteOffset':vertices.nbytes,'byteLength':indices.nbytes}],
             'accessors':[{'bufferView':0,'componentType':5126,'count':12,'type':'VEC3'},{'bufferView':1,'componentType':5123,'count':18,'type':'SCALAR'}],
             'materials':[{}],'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1,'material':0}]}],'nodes':[{'mesh':0}],'scenes':[{'nodes':[0]}],'scene':0}
        j=json.dumps(doc).encode();j+=b' '*((-len(j))%4)
        data=struct.pack('<4sII',b'glTF',2,12+8+len(j)+8+len(binary))+struct.pack('<II',len(j),0x4E4F534A)+j+struct.pack('<II',len(binary),0x004E4942)+binary
        source=self.root/'room.glb';source.write_bytes(data)
        image,info=textured_topdown(source,[-2,-2,2,2],0,max_side=200)
        self.assertGreater(info['rendered_triangles'],2)
        self.assertLess(sum(image.getpixel((100,100))),sum(image.getpixel((20,20))))
        self.assertEqual(info['geometry_bounds'][1][1],3)
    def test_requested_export_preempts_background_before_ffmpeg(self):
        from process_show.render import encode,RenderDeferred
        eid=self.episode();self.engine.end(eid,{'total_steps':2,'status':'completed'},False)
        other=self.episode(2);self.engine.end(other,{'total_steps':2,'status':'completed'},False)
        with self.store.db() as db:db.execute('INSERT INTO render_requests VALUES (?,?)',(other,self.store.clock()))
        with self.assertRaises(RenderDeferred):encode(self.store,eid)
        self.assertEqual(self.store.get(eid)['status'],'pending_encode')
    def test_generic_sdk_map_remains_available_with_affine_transform(self):
        from process_show.presentation import provided_map
        eid=self.engine.start(meta())
        self.engine.step(eid,{'step':0,'action':'RESET','map_transform':[[10,0,100],[0,-10,100]]},map_image=Image.new('RGB',(200,200),'white'))
        info=provided_map(self.store,eid,0)
        self.assertTrue(info['available'])
        self.assertEqual(info['bounds'],[-10,-10,10,10])
        self.assertEqual(info['mode'],'provided')
    def test_lightweight_observation_route_does_not_call_ffmpeg(self):
        from http.client import HTTPConnection
        from unittest.mock import patch
        from process_show.server import make_server
        eid=self.episode();self.engine.step(eid,{'step':3,'action':'FORWARD'})
        server=make_server(self.store,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch('process_show.render.extract_frame',side_effect=AssertionError('Must not decode MP4')):
                conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                conn.request('GET',f'/api/episodes/{eid}/observation?step=3');response=conn.getresponse()
                self.assertEqual(response.status,200);self.assertEqual(response.getheader('X-Observation-Step'),'2')
                self.assertEqual(Image.open(io.BytesIO(response.read())).size,(320,240))
                conn.request('GET',f'/api/episodes/{eid}/replay');r=conn.getresponse();payload=json.loads(r.read())
                self.assertEqual(payload['steps'][3]['observation_step'],2)
                conn.close()
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
