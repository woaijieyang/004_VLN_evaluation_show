"""Headless entry -> picker -> selection -> CLIENT URL integration check.

Use --control URL for an existing service/bridge, or --fixture-entry to exercise
the native Windows launcher in an isolated project with two synthetic runs.
No browser is launched and no existing service is stopped.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PLUGIN = Path(__file__).resolve().parents[2]
PROJECT = PLUGIN.parent
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        request.add_header('Content-Type', 'application/json')
    with OPENER.open(request, timeout=25) as response:
        return response.status, dict(response.headers), response.read()


def get_json(url, body=None, headers=None):
    return json.loads(read(url, body, headers)[2])


def verify(control, expected_root=None):
    control = control.rstrip('/')
    status, _, html = read(control + '/?choose=1')
    assert status == 200 and b'viewer.js' in html
    health = get_json(control + '/api/health')
    if expected_root:
        assert Path(health['project_root']).resolve() == expected_root.resolve()
    deadline = time.monotonic() + 45
    while True:
        catalog = get_json(control + '/api/runs')
        if len(catalog.get('runs', [])) >= 2 and not catalog.get('pending'):
            break
        assert time.monotonic() < deadline, catalog
        time.sleep(.25)
    results = []
    selected = []
    for run in catalog['runs'][:2]:
        choice = get_json(control + '/api/runs/select', {'id': run['id']})
        url = choice['url']
        parsed = urllib.parse.urlsplit(url)
        assert parsed.hostname == '127.0.0.1'
        assert parsed.port == choice['port']
        base = f'{parsed.scheme}://{parsed.netloc}'
        page_status, _, page = read(url)
        assert page_status == 200 and b'viewer.js' in page
        assert read(base + '/viewer.js')[0] == 200
        probe = get_json(base + '/api/health?probe=' + choice['probe_token'],
                         headers={'Origin': control})
        _, headers, _ = read(base + '/api/health?probe=' + choice['probe_token'],
                             headers={'Origin': control})
        assert headers.get('Access-Control-Allow-Origin') == control
        assert probe['scope_id'] == health['scope_id']
        assert probe['evaluation_id'] == run['id']
        assert probe['run_id'] == run['run_id']
        deadline = time.monotonic() + 30
        while True:
            rows = get_json(base + '/api/episodes')
            if rows:
                break
            assert time.monotonic() < deadline, get_json(control + '/api/sync')
            time.sleep(.25)
        assert all(row['meta']['run_id'] == run['run_id'] for row in rows)
        again = get_json(control + '/api/runs/select', {'id': run['id']})
        assert again['url'] == url
        results.append({'run_id': run['run_id'], 'url': url, 'port': parsed.port,
                        'page_http': page_status, 'scope_id': probe['scope_id'],
                        'evaluation_id': probe['evaluation_id'],
                        'visible_episodes': len(rows), 'cors_identity_probe': True,
                        'repeat_selection_reused': True})
        selected.append((base, rows))
    assert results[0]['port'] != results[1]['port']
    foreign = selected[1][1][0]['id']
    try:
        read(selected[0][0] + '/api/episodes/' + foreign)
    except urllib.error.HTTPError as exc:
        assert exc.code in (403, 404), exc.code
    else:
        raise AssertionError('Cross-evaluation read accepted')
    return {'client_platform': sys.platform, 'control_url': control,
            'project_root': health['project_root'], 'picker_http': status,
            'distinct_ports': True, 'cross_evaluation_rejected': True,
            'returned_urls_checked_on_invoking_machine': True, 'runs': results}


def fixture():
    if os.name != 'nt':
        raise RuntimeError('--fixture-entry currently tests the native Windows CMD entry')
    qa = PROJECT / 'outputs/process-show/_qa'
    qa.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='entry-flow-', dir=qa) as directory:
        root = Path(directory).resolve()
        target = root / 'process-show'
        target.mkdir()
        for name in ('tools', 'process_show'):
            shutil.copytree(PLUGIN / name, target / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(PLUGIN / 'process-show-windows.cmd', target)
        for number in range(2):
            run = root / 'outputs' / ('synthetic-' + str(number))
            trace = run / 'shards/shard-0/attempt-1/episodes/one/trace.jsonl'
            trace.parent.mkdir(parents=True)
            (run / 'run_manifest.json').write_text(json.dumps({
                'created_at': datetime.now(timezone.utc).isoformat(),
                'model': 'SYNTHETIC ENTRY TEST', 'datasets': ['R2R'],
                'expected_tasks': 1}), encoding='utf-8')
            (run / 'progress.json').write_text(
                '{"status":"COMPLETED","completed":1,"expected":1}', encoding='utf-8')
            pose = {'position': [0, 0, 0], 'rotation_xyzw': [0, 0, 0, 1]}
            events = [
                {'kind': 'reset', 'task_key': 'r2r|test|en|fixture|1|-', 'episode_id': 'one',
                 'state_evaluator_only': pose},
                {'kind': 'model_input', 'call_id': 1, 'prompt': 'Instruction: Synthetic fixture.', 'images': []},
                {'kind': 'primitive_action', 'step': 1, 'action': 0, 'last_call_id': 1,
                 'after_evaluator_only': pose},
                {'kind': 'terminal', 'steps': 1, 'native_metrics': {'success': 0}, 'termination_reason': 'stop'}]
            trace.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
        def entry(action, answer=None):
            argv = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
                    str(target / 'process-show-windows.cmd'), action, '--no-browser']
            result = subprocess.run(argv, cwd=PROJECT, env=env, capture_output=True, input=answer,
                                    encoding='utf-8', timeout=60,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0, result.stdout + result.stderr
            return result.stdout
        try:
            startup = entry('start')
            state = json.loads(entry('status'))
            assert state['running']
            result = verify(state['process']['url'], root)
            terminal_selection = entry('select', '1\n')
            terminal_url = re.findall(r'http://127\.0\.0\.1:\d+/\?run=\S+', terminal_selection)[-1]
            assert read(terminal_url)[0] == 200
            result.update(synthetic_only=True, native_entry='process-show-windows.cmd',
                          foreign_cwd=str(PROJECT), startup_output=startup,
                          terminal_selection_url=terminal_url, no_browser=True)
        finally:
            entry('stop')
        assert not json.loads(entry('status'))['running']
        for run in result['runs']:
            try:
                read(run['url'])
            except (OSError, urllib.error.URLError):
                continue
            raise AssertionError('Child listener remained after project stop')
        result['owned_service_and_children_stopped'] = True
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control')
    parser.add_argument('--fixture-entry', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.fixture_entry == bool(args.control):
        parser.error('Choose exactly one of --fixture-entry or --control')
    result = fixture() if args.fixture_entry else verify(args.control)
    result['at'] = datetime.now(timezone.utc).isoformat()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
