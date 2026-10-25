import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from local_project import ensure_local_project

class LocalProjectTests(unittest.TestCase):
    def test_relocated_plugin_binds_only_its_current_root(self):
        base=Path(__file__).resolve().parents[2]/'outputs/process-show/_qa';base.mkdir(exist_ok=True,parents=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            root=Path(temporary);plugin=root/'process-show';(plugin/'config').mkdir(parents=True)
            (plugin/'config/service.json').write_text(json.dumps({'project_id':'old','project_roots':{}}))
            (plugin/'config/sources.json').write_text(json.dumps({'sources':[{'run_root':'/outside/project'}]}))
            cfg=ensure_local_project(plugin)
            self.assertTrue(str(cfg['project_id']).startswith('process-show-'))
            self.assertIn(str(root),cfg['project_roots'].values())
            self.assertNotIn('ssh_alias',cfg)
            self.assertEqual(json.loads((plugin/'config/sources.json').read_text())['sources'],[])
            self.assertEqual(ensure_local_project(plugin)['project_id'],cfg['project_id'])
