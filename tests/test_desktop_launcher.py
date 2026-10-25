import importlib.util,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
path=Path(__file__).resolve().parents[1]/'tools/legacy/desktop_launcher.py'
spec=importlib.util.spec_from_file_location('desktop_launcher',path);launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        config=patch.object(launcher,'configuration',return_value={
            'project_roots':{'posix':'/data/zy/vln/case1'},
            'watch_config':'process-show/config/sources.json'})
        config.start();self.addCleanup(config.stop)
    def test_repeated_open_reuses_healthy_service(self):
        with patch.object(launcher,'health',return_value={'app':'process-show'}),patch.object(launcher,'adopt_task_tunnel'),patch.object(launcher,'run') as command:
            value=launcher.connect_once(False,lambda _:None)
            self.assertTrue(value['reused']);command.assert_not_called()
    def test_pid_reuse_does_not_close_another_process(self):
        base=Path(__file__).resolve().parents[2]/'outputs/process-show/_qa';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            receipt=Path(folder)/'session.json';receipt.write_text(json.dumps({'pid':123,'identity':'old'}))
            with patch.object(launcher,'RECEIPT',receipt),patch.object(launcher,'process_identity',return_value='new'),patch.object(launcher,'run') as command:
                self.assertFalse(launcher.close_tunnel());command.assert_not_called()
    def test_remote_command_stays_in_case1(self):
        with patch.object(launcher.shutil,'which',return_value='ssh'):
            self.assertIn('cd /data/zy/vln && cd case1 &&',launcher.ssh_command('start')[-1])
            self.assertNotIn('kill',launcher.ssh_command('stop')[-1])
