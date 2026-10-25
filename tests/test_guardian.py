import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/legacy'))
import desktop_launcher as launcher
import connection_guard as guard

class GuardianTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[2]/'outputs/process-show/_qa'
        base.mkdir(exist_ok=True,parents=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.output=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        plugin=self.output/'process-show';(plugin/'config').mkdir(parents=True)
        (plugin/'config/service.json').write_text(json.dumps({
            'project_id':'guardian-test','project_roots':{
                'windows':str(self.output),'posix':str(self.output),'macos':str(self.output)},
            'local_port':18765,'remote_port':8765}),encoding='utf-8')
        isolated=patch.multiple(launcher,PLUGIN=plugin,OUTPUT=self.output/'desktop',
                                TRANSPORT_OVERRIDE=None,REMOTE_OVERRIDE=None,
                                PORT=18765,REMOTE_PORT=8765,URL='http://127.0.0.1:18765/')
        isolated.start();self.addCleanup(isolated.stop)
    def tearDown(self):
        pass
    def run_once(self,g):
        def wait(_):g.stop.set();return True
        return patch.object(g.stop,'wait',side_effect=wait)
    def test_internal_port_survives_configuration(self):
        with patch.object(launcher,'TRANSPORT_OVERRIDE',18766):
            launcher.configuration()
            self.assertEqual(launcher.PORT,18766)
    def test_reconnect_recovers(self):
        g=guard.Guardian()
        with patch.object(launcher,'OUTPUT',self.output),patch.object(launcher,'health',side_effect=[None,{'app':'process-show'}]),patch.object(launcher,'close_tunnel'),patch.object(launcher,'connect_once') as connect,self.run_once(g):
            g.loop()
            self.assertTrue(connect.call_args.kwargs['automatic'])
            self.assertEqual(g.status()['state'],'connected')
    def test_manual_stop_never_restarts(self):
        (self.output/'connection-stop.json').write_text('{}')
        g=guard.Guardian()
        with patch.object(launcher,'OUTPUT',self.output),patch.object(launcher,'close_tunnel'),patch.object(launcher,'connect_once') as connect,self.run_once(g):
            g.loop()
            connect.assert_not_called()
            self.assertEqual(g.status()['state'],'stopped')
    def test_remote_stop_suppresses_reconnect(self):
        g=guard.Guardian()
        with patch.object(launcher,'OUTPUT',self.output),patch.object(launcher,'health',return_value=None),patch.object(launcher,'close_tunnel'),patch.object(launcher,'connect_once',side_effect=RuntimeError('PAUSED_BY_USER')),self.run_once(g):
            g.loop()
            self.assertTrue((self.output/'connection-stop.json').exists())
            self.assertEqual(g.status()['state'],'stopped')
