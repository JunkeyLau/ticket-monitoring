import base64
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from core.remote_login import RemoteLogin


class FakeMouse:
    def __init__(self): self.events = []
    def move(self, x, y): self.events.append(('move', x, y))
    def click(self, x, y): self.events.append(('click', x, y))
    def down(self): self.events.append(('down',))
    def up(self): self.events.append(('up',))
    def wheel(self, x, y): self.events.append(('wheel', x, y))


class FakeKeyboard:
    def __init__(self): self.events = []
    def insert_text(self, text): self.events.append(('text', text))
    def press(self, key): self.events.append(('key', key))


class FakePage:
    def __init__(self):
        self.mouse, self.keyboard = FakeMouse(), FakeKeyboard()
        self.viewport_size = {'width': 100, 'height': 200}
    def is_closed(self): return False
    def wait_for_timeout(self, ms): pass
    def screenshot(self, **kwargs): return b'frame'


def test_bridge_actions_and_cleanup():
    bridge, done, page = RemoteLogin(), threading.Event(), FakePage()
    ctx = type('Context', (), {'pages': [page]})()
    thread = threading.Thread(target=bridge.serve, args=(ctx, page, done), daemon=True)
    thread.start()
    with pytest.raises(PermissionError):
        bridge.call('bad-token', 'frame')
    frame = bridge.call(bridge.token, 'frame')
    assert base64.b64decode(frame['image']) == b'frame'
    bridge.call(bridge.token, 'down', {'x': 10, 'y': 20})
    bridge.call(bridge.token, 'up', {'x': 15, 'y': 25})
    bridge.call(bridge.token, 'text', {'text': '测试'})
    assert ('down',) in page.mouse.events and ('up',) in page.mouse.events
    assert page.keyboard.events == [('text', '测试')]
    with pytest.raises(RuntimeError):
        bridge.call(bridge.token, 'click', {'x': 1000, 'y': 0})
    done.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    bridge.close()
    with pytest.raises(RuntimeError):
        bridge.call(bridge.token, 'frame')


@pytest.fixture
def server(monkeypatch):
    import webui
    monkeypatch.setenv('TM_REMOTE_LOGIN', '1')
    monkeypatch.setenv('TM_REMOTE_LOGIN_SECRET', 'example-secret-12345')
    monkeypatch.setitem(webui.DEMO, 'on', False)
    monkeypatch.setattr('socket.getfqdn', lambda _: 'localhost')
    server = ThreadingHTTPServer(('127.0.0.1', 0), webui._Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(server, body, host=None, origin=None):
    port = server.server_address[1]
    host = host or f'127.0.0.1:{port}'
    con = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    con.request('POST', '/api/remote-login', json.dumps(body),
                {'Host': host, 'Origin': origin or 'http://' + host,
                 'Content-Type': 'application/json'})
    res = con.getresponse()
    result = res.status, dict(res.getheaders()), json.loads(res.read())
    con.close()
    return result


def test_remote_route_requires_loopback_origin_and_secret(server, monkeypatch):
    import webui
    calls = []
    monkeypatch.setattr(webui, '_start_login', lambda *args: calls.append(args) or {'ok': True})
    assert request(server, {'action': 'availability'})[0] == 200
    assert request(server, {'action': 'availability'}, host='example.com')[0] == 403
    assert request(server, {'action': 'availability'}, origin='http://evil.example')[0] == 403
    assert request(server, {'action': 'start', 'secret': 'wrong'})[0] == 403
    assert calls == []
    status, headers, result = request(server, {'action': 'start', 'slot': 'ctrip',
                                             'platform': 'ctrip', 'secret': 'example-secret-12345'})
    assert status == 200 and result['ok'] and calls == [('ctrip', 'ctrip', True)]
    assert headers.get('Cache-Control') == 'no-store'
    assert request(server, {'action': 'frame', 'slot': 'ctrip', 'token': 'fake'})[0] == 403
    monkeypatch.delenv('TM_REMOTE_LOGIN')
    assert request(server, {'action': 'availability'})[0] == 403
