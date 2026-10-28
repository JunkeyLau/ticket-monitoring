"""Concurrent clients cannot silently overwrite or truncate configuration."""
import concurrent.futures
import http.client
import json
import logging
import threading
from http.server import ThreadingHTTPServer

import pytest
import yaml


@pytest.fixture
def config_server(tmp_path, monkeypatch):
    import webui
    path = tmp_path / 'config.yaml'
    path.write_text('users:\n  - name: original\nweb:\n  port: 8765\n', encoding='utf-8')
    monkeypatch.setattr(webui._State, 'config_path', str(path))
    monkeypatch.setattr(webui._State, 'cfg', {})
    monkeypatch.setattr(webui._State, 'logger', logging.getLogger('config-test'))
    monkeypatch.setattr(webui._State, 'reload_cb', lambda: 1)
    monkeypatch.setattr(webui, '_storage_save_error', lambda cfg: None)
    monkeypatch.setattr(webui, '_storage_view', lambda cfg: {})
    monkeypatch.setattr(webui, '_restart_view', lambda cfg: {})
    monkeypatch.setattr(webui, '_runtime_view', lambda **kwargs: {})
    monkeypatch.setitem(webui.DEMO, 'on', False)
    monkeypatch.delenv('TM_PROXY_PROTECTED', raising=False)
    monkeypatch.delenv('TM_PUBLIC_ORIGIN', raising=False)
    monkeypatch.delenv('TM_PILOT_OPEN', raising=False)
    monkeypatch.setattr('socket.getfqdn', lambda _: 'localhost')
    server = ThreadingHTTPServer(('127.0.0.1', 0), webui._Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, path
    server.shutdown()
    server.server_close()
    thread.join(2)


def request(server, method, data=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    body = json.dumps(data) if data is not None else None
    conn.request(method, '/api/config', body=body,
                 headers={'Content-Type': 'application/json'})
    response = conn.getresponse()
    result = response.status, json.loads(response.read())
    conn.close()
    return result


def test_concurrent_same_revision_only_one_commit(config_server):
    server, path = config_server
    status, config = request(server, 'GET')
    assert status == 200 and config['revision']
    revision = config['revision']
    start = threading.Barrier(2)
    def save(name):
        start.wait()
        return request(server, 'POST', {'revision': revision,
                       'users': [{'name': name}], 'globals': {}})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['first', 'second']))
    assert sorted(status for status, _ in results) == [200, 409]
    assert yaml.safe_load(path.read_text())['users'][0]['name'] in ('first', 'second')
    assert not list(path.parent.glob('.config-*.tmp'))


def test_failed_atomic_replace_leaves_original_intact(config_server, monkeypatch):
    import webui
    server, path = config_server
    before = path.read_bytes()
    _, config = request(server, 'GET')
    def fail(*args): raise OSError('injected replacement failure')
    monkeypatch.setattr(webui.os, 'replace', fail)
    status, result = request(server, 'POST', {'revision': config['revision'],
                            'users': [{'name': 'changed'}], 'globals': {}})
    assert status == 500 and not result['ok']
    assert path.read_bytes() == before
    assert not list(path.parent.glob('.config-*.tmp'))
