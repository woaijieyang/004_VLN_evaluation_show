import json,tempfile,unittest,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools/legacy'))
from project_mapping import local_config,project_identity,validate_remote
from configure_mapping import configure
from unittest.mock import patch

class MappingTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).resolve().parents[2]/'outputs/process-show/_qa'
        base.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_new_directories_get_distinct_mapping(self):
        a=local_config(self.root/'a'/'process-show','/projects/model-one','test-host',{'project_root':'/projects/model-one','project_id':project_identity('/projects/model-one'),'remote_port':22000})
        b=local_config(self.root/'b'/'process-show','/different/model-two','test-host',{'project_root':'/different/model-two','project_id':project_identity('/different/model-two'),'remote_port':23000})
        self.assertNotEqual(a['local_port'],b['local_port'])
        self.assertNotEqual(a['project_id'],b['project_id'])
        self.assertEqual(b['project_roots']['windows'],str((self.root/'b').resolve()))
    def test_remote_identity_mismatch_rejected(self):
        with self.assertRaises(ValueError):local_config(self.root/'process-show','/projects/a','test-host',{'project_root':'/projects/b'})
        for path in ('relative','/','/projects/../secret'):
            with self.assertRaises(ValueError):validate_remote(path)
    def test_macos_uses_its_own_client_root_mapping(self):
        with patch('project_mapping.sys.platform','darwin'):
            config=local_config(self.root/'process-show','/projects/remote-model','test-host',{'project_root':'/projects/remote-model','project_id':'project-mac','remote_port':23456})
        self.assertIn('macos',config['project_roots'])
        self.assertNotIn('windows',config['project_roots'])
        self.assertEqual(config['project_roots']['posix'],'/projects/remote-model')
    @unittest.skipIf(os.name=='nt','Remote configuration executes on Linux')
    def test_remote_configuration_never_reuses_cloned_sources(self):
        plugin=self.root/'project'/'process-show';(plugin/'config').mkdir(parents=True)
        (plugin/'config/service.json').write_text(json.dumps({'project_id':'old','project_roots':{'posix':'/old/project'}}))
        (plugin/'config/sources.json').write_text(json.dumps({'sources':[{'run_root':'/old/project/outputs/run'}]}))
        result=configure(plugin,r'C:\projects\different-local-name')
        self.assertEqual(result['sources_removed'],1)
        self.assertEqual(result['project_root'],str(plugin.parent.resolve()))
        self.assertNotEqual(result['project_id'],'old')
        self.assertEqual(json.loads((plugin/'config/sources.json').read_text())['sources'],[])
    @unittest.skipIf(os.name=='nt','Remote configuration executes on Linux')
    def test_existing_remote_identity_preserved(self):
        plugin=self.root/'project'/'process-show';(plugin/'config').mkdir(parents=True)
        (plugin/'config/service.json').write_text(json.dumps({'project_id':'existing-id','project_roots':{'posix':str(plugin.parent)},'remote_port':34567}))
        result=configure(plugin,r'C:\another\client')
        self.assertEqual(result['project_id'],'existing-id')
        self.assertEqual(result['remote_port'],34567)
