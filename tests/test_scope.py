import json,os,unittest
import subprocess
from pathlib import Path
from unittest.mock import patch
import test_pipeline as fixtures
from process_show.scope import Scope


class ScopeTests(unittest.TestCase):
    setUp=fixtures.PipelineTests.setUp
    tearDown=fixtures.PipelineTests.tearDown
    def config(self):return {'project_id':'fixture','project_roots':{'windows':str(self.root),'posix':str(self.root)},'output':'out'}
    def test_foreign_source_and_output_rejected(self):
        scope=Scope(self.root,self.config())
        with self.assertRaises(ValueError):scope.source(self.root.parent/'other-project')
        with self.assertRaises(ValueError):scope.output_path(self.root/'outside-output')
    def test_copied_config_cannot_connect_original_project(self):
        cfg=self.config();cfg['project_roots']['windows' if os.name=='nt' else 'posix']=str(self.root.parent/'different')
        with self.assertRaises(ValueError):Scope(self.root,cfg)
    @unittest.skipUnless(os.name=='nt','Windows junction test')
    def test_windows_junction_cannot_escape_final_root(self):
        project=self.root/'local-project';target=self.root/'synthetic-peer'
        project.mkdir();target.mkdir();protected=target/'keep.txt';protected.write_text('unchanged',encoding='utf-8')
        link=project/'escape'
        def native(path):
            text=str(path)
            return text[4:] if text.startswith('\\\\?\\') else text
        subprocess.check_output(['cmd','/d','/c','mklink','/J',native(link),native(target)],creationflags=subprocess.CREATE_NO_WINDOW)
        cfg={'project_id':'junction-fixture','project_roots':{'windows':str(project),'posix':str(project)},'output':'out'}
        try:
            scope=Scope(project,cfg)
            with self.assertRaises(ValueError):scope.source(link/'keep.txt')
            self.assertEqual(protected.read_text(encoding='utf-8'),'unchanged')
        finally:link.rmdir()
    def test_manual_stop_suppresses_autostart(self):
        from process_show.autostart import ensure_service
        scope=Scope(self.root,dict(self.config(),enabled=True,auto_start_on_evaluation=True))
        scope.stop()
        with patch('process_show.scope.Scope.current',return_value=scope),patch('process_show.autostart.subprocess.Popen') as launch:
            self.assertFalse(ensure_service(scope.output));launch.assert_not_called()
    def test_page_stop_rejects_a_different_scope(self):
        import threading
        from http.client import HTTPConnection
        from process_show.server import make_server
        server=make_server(self.store,port=0);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with patch.object(Scope,'stop',side_effect=AssertionError('Must not stop')):
                conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                conn.request('POST','/api/control/stop',body=json.dumps({'scope_id':'different-project'}),headers={'Content-Type':'application/json'})
                response=conn.getresponse();self.assertEqual(response.status,400);response.read();conn.close()
        finally:server.shutdown();server.server_close()
