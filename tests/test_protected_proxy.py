"""Access and CSRF checks for the explicitly enabled HTTPS proxy mode."""
import base64
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest


@pytest.fixture
def server(monkeypatch):
    import webui
    monkeypatch.setenv('TM_PROXY_PROTECTED', '1')
    monkeypatch.setenv('TM_PUBLIC_ORIGIN', 'https://tickets.example.test')
    webui._PROXY_AUTH_FAILURES.clear()
    monkeypatch.setenv('TM_WEB_PASSWORD', 'random-test-password-123456789')
    monkeypatch.setenv('TM_REMOTE_LOGIN', '1')
    monkeypatch.setitem(webui.DEMO, 'on', False)
    monkeypatch.setattr('socket.getfqdn', lambda _: 'localhost')
    server = ThreadingHTTPServer(('127.0.0.1', 0), webui._Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(2)


def req(server, method='GET', path='/api/config', *, auth=True, host='tickets.example.test',
        origin='https://tickets.example.test', proto='https', body=None):
    port = server.server_address[1]
    headers = {'Host': host, 'X-Forwarded-Proto': proto}
    if auth:
        headers['Authorization'] = 'Basic ' + base64.b64encode(b'admin:random-test-password-123456789').decode()
    if origin is not None:
        headers['Origin'] = origin
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body)
    con = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    con.request(method, path, body=body, headers=headers)
    res = con.getresponse()
    status = res.status
    res.read()
    con.close()
    return status


def test_authentication_covers_get_head_and_post(server):
    assert req(server, auth=False) == 401
    assert req(server, method='HEAD', path='/', auth=False) == 401
    assert req(server, method='POST', path='/api/config', auth=False, body={}) == 401
    assert req(server, path='/no-such-page') == 404


def test_proxy_origin_and_csrf_enforced(server):
    assert req(server, host='evil.example') == 403
    assert req(server, proto='http') == 403
    assert req(server, method='POST', path='/api/remote-login',
               origin='https://evil.example', body={'action': 'availability'}) == 403
    assert req(server, method='POST', path='/api/remote-login',
               origin=None, body={'action': 'availability'}) == 403
    assert req(server, method='POST', path='/api/remote-login',
               body={'action': 'availability'}) == 200


def test_proxy_mode_fails_closed_on_missing_or_invalid_settings(server, monkeypatch):
    monkeypatch.delenv('TM_PUBLIC_ORIGIN')
    assert req(server) == 403
    monkeypatch.setenv('TM_PUBLIC_ORIGIN', 'https://tickets.example.test')
    monkeypatch.delenv('TM_WEB_PASSWORD')
    assert req(server) == 403
    monkeypatch.setenv('TM_WEB_PASSWORD', 'random-test-password-123456789')
    monkeypatch.delenv('TM_PROXY_PROTECTED')
    assert req(server) == 403
    monkeypatch.setenv('TM_PROXY_PROTECTED', '1')
    monkeypatch.setenv('TM_PUBLIC_ORIGIN', 'http://tickets.example.test')
    assert req(server) == 403


def test_wrong_password_attempts_are_throttled(server):
    import webui
    port = server.server_address[1]
    for _ in range(5):
        con = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        con.request('GET', '/api/config', headers={'Host': 'tickets.example.test',
                    'X-Forwarded-Proto': 'https', 'Authorization': 'Basic YWRtaW46d3Jvbmc='})
        res = con.getresponse()
        assert res.status == 401
        res.read()
        con.close()
    assert req(server) == 429
    webui._PROXY_AUTH_FAILURES.clear()


def test_liveness_survives_password_lockout(server):
    import webui
    webui._PROXY_AUTH_FAILURES[:] = [__import__('time').monotonic()] * 5
    assert req(server, path='/healthz', auth=False) == 200
    assert req(server, path='/api/config') == 429
    webui._PROXY_AUTH_FAILURES.clear()


def test_docker_context_excludes_local_secret_files():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    lines = (root / '.dockerignore').read_text().splitlines()
    assert '.env' in lines and '.env.*' in lines and '.session-secret' in lines
    assert 'docker-compose*.yml' in lines and 'compose*.yaml' in lines
    assert '.env.*' in (root / '.gitignore').read_text().splitlines()


def test_explicit_pilot_requires_exact_https_origin_but_no_password(server, monkeypatch):
    import webui
    monkeypatch.setenv('TM_PILOT_OPEN', '1')
    monkeypatch.delenv('TM_WEB_PASSWORD')
    monkeypatch.delenv('TM_REMOTE_LOGIN_SECRET', raising=False)
    calls = []
    monkeypatch.setattr(webui, '_start_login', lambda *args: calls.append(args) or {'ok': True})
    assert req(server, path='/no-such-page', auth=False) == 404
    assert req(server, method='POST', path='/api/remote-login', auth=False,
               body={'action': 'availability'}) == 200
    assert req(server, method='POST', path='/api/remote-login', auth=False,
               body={'action': 'start', 'slot': 'ctrip', 'platform': 'ctrip'}) == 200
    assert calls == [('ctrip', 'ctrip', True)]
    assert req(server, method='POST', path='/api/remote-login', auth=False,
               origin='https://evil.example', body={'action': 'start'}) == 403
    assert req(server, path='/no-such-page', auth=False, proto='http') == 403
    monkeypatch.delenv('TM_PROXY_PROTECTED')
    assert req(server, path='/no-such-page', auth=False) == 403


def test_pilot_start_token_and_cancel(server, monkeypatch):
    import webui
    from core.remote_login import RemoteLogin
    monkeypatch.setenv('TM_PILOT_OPEN', '1')
    monkeypatch.delenv('TM_WEB_PASSWORD')
    monkeypatch.delenv('TM_REMOTE_LOGIN_SECRET', raising=False)
    bridge = RemoteLogin()
    monkeypatch.setitem(webui._State.login_sessions, 'ctrip', {'remote': bridge})
    monkeypatch.setattr(webui, '_start_login', lambda *args: {'ok': True, 'remote_token': bridge.token})
    port = server.server_address[1]
    def post(body):
        con = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        con.request('POST', '/api/remote-login', json.dumps(body),
                    {'Host': 'tickets.example.test', 'X-Forwarded-Proto': 'https',
                     'Origin': 'https://tickets.example.test', 'Content-Type': 'application/json'})
        res = con.getresponse()
        status, payload = res.status, json.loads(res.read())
        con.close()
        return status, payload
    assert post({'action': 'availability'}) == (200, {'ok': True, 'pilot': True})
    status, started = post({'action': 'start', 'platform': 'ctrip', 'slot': 'ctrip'})
    assert status == 200 and started['remote_token'] == bridge.token
    assert post({'action': 'cancel', 'slot': 'ctrip', 'token': 'invalid'})[0] == 403
    assert post({'action': 'cancel', 'slot': 'ctrip', 'token': bridge.token}) == (200, {'ok': True})
    assert bridge.transition('finish') is False
    assert req(server, method='POST', path='/api/config', auth=False,
               origin='https://evil.example', body={}) == 403


def test_protected_mode_still_requires_remote_login_secret(server, monkeypatch):
    import webui
    monkeypatch.delenv('TM_PILOT_OPEN', raising=False)
    monkeypatch.delenv('TM_REMOTE_LOGIN_SECRET', raising=False)
    calls = []
    monkeypatch.setattr(webui, '_start_login', lambda *args: calls.append(args) or {'ok': True})
    assert req(server, method='POST', path='/api/remote-login',
               body={'action': 'start', 'slot': 'ctrip', 'platform': 'ctrip'}) == 403
    assert not calls
