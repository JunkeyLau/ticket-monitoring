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
        self.url = 'https://example.test/login?secret=not-displayed'
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
    assert frame['origin'] == 'https://example.test' and frame['secure']
    bridge.call(bridge.token, 'down', {'x': 10, 'y': 20})
    bridge.call(bridge.token, 'up', {'x': 15, 'y': 25})
    bridge.call(bridge.token, 'text', {'text': '测试'})
    assert ('down',) in page.mouse.events and ('up',) in page.mouse.events
    assert page.keyboard.events == [('text', '测试')]
    with pytest.raises(RuntimeError):
        bridge.call(bridge.token, 'click', {'x': 1000, 'y': 0})
    assert bridge.transition("finish")
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


def test_finish_and_cancel_are_mutually_exclusive():
    bridge = RemoteLogin()
    barrier = threading.Barrier(3)
    outcomes = []
    def transition(value):
        barrier.wait()
        outcomes.append((value, bridge.transition(value)))
    workers = [threading.Thread(target=transition, args=(value,)) for value in ('finish', 'cancel')]
    for worker in workers: worker.start()
    barrier.wait()
    for worker in workers: worker.join(2)
    assert sorted(ok for _, ok in outcomes) == [False, True]


def test_origin_tracks_newest_popup():
    bridge, done = RemoteLogin(), threading.Event()
    first, popup = FakePage(), FakePage()
    popup.url = 'http://popup.example.test/path?secret=hidden'
    ctx = type('Context', (), {'pages': [first, popup]})()
    thread = threading.Thread(target=bridge.serve, args=(ctx, first, done), daemon=True)
    thread.start()
    frame = bridge.call(bridge.token, 'frame')
    assert frame['origin'] == 'http://popup.example.test'
    assert not frame['secure'] and 'secret' not in frame['origin']
    assert bridge.transition('finish')
    done.set()
    thread.join(2)


@pytest.mark.parametrize('outcome', ['cancel', 'timeout', 'failure', 'finish'])
def test_remote_profile_only_published_after_finish(tmp_path, outcome, monkeypatch):
    import logging
    from contextlib import contextmanager
    from crawlers.base import BaseCrawler
    class Crawler(BaseCrawler):
        name = 'example'
        def login_url(self): return 'https://example.test/'
    crawler = Crawler({'user_data_dir': str(tmp_path)}, logging.getLogger('test'))
    target = tmp_path / 'example'
    target.mkdir()
    (target / 'profile.txt').write_text('original')
    class Page:
        def goto(self, *args, **kwargs): pass
    class Bridge:
        def serve(self, ctx, page, done, timeout):
            # Emulate Chromium persisting login state before the window closes.
            from pathlib import Path
            (Path(crawler.user_data_dir) / 'profile.txt').write_text('new')
            if outcome != 'finish':
                raise RuntimeError(outcome)
            done.set()
    @contextmanager
    def browser(**kwargs):
        yield object()
    monkeypatch.setattr(crawler, 'browser', browser)
    monkeypatch.setattr(crawler, 'new_page', lambda ctx: Page())
    if outcome == 'finish':
        crawler._remote_login_window(threading.Event(), Bridge(), 1)
        assert (target / 'profile.txt').read_text() == 'new'
    else:
        with pytest.raises(RuntimeError):
            crawler._remote_login_window(threading.Event(), Bridge(), 1)
        assert (target / 'profile.txt').read_text() == 'original'
    assert not list(tmp_path.glob('.remote-session-*'))
    assert not list(tmp_path.glob('.remote-backup-*'))


def test_interrupted_publish_restores_original_and_cleans_stale(tmp_path):
    import logging
    from crawlers.base import BaseCrawler
    class Crawler(BaseCrawler):
        name = 'example'
    backup = tmp_path / '.remote-backup-example'
    backup.mkdir()
    (backup / 'important').write_text('original')
    # Another caller may have created an empty target after the interruption.
    target = tmp_path / 'example'
    target.mkdir()
    stale = tmp_path / '.remote-session-example-abandoned'
    stale.mkdir()
    (stale / 'private').write_text('temporary session')
    crawler = Crawler({'user_data_dir': str(tmp_path)}, logging.getLogger('test'))
    assert (target / 'important').read_text() == 'original'
    assert crawler.session_state_path.endswith('session-state.json')
    assert not backup.exists() and not stale.exists()
    assert not list(tmp_path.glob('.remote-discard-*'))


def test_tuniu_snapshot_location_survives_surface_profile(tmp_path):
    import logging
    from crawlers.tuniu import TuniuCrawler
    root = tmp_path / 'tuniu'
    (root / 'surfaces' / 'pc' / 'Default').mkdir(parents=True)
    (root / 'session-state.json').write_text('{"saved_at": 123}')
    crawler = TuniuCrawler({'user_data_dir': str(tmp_path)}, logging.getLogger('test'))
    crawler.use_mobile = False
    assert crawler.user_data_dir == str(root / 'surfaces' / 'pc')
    assert crawler.session_state_path == str(root / 'session-state.json')
    assert crawler.session_saved_at == 123


def test_cleanup_of_partial_garbage_cannot_replace_committed_profile(tmp_path, monkeypatch):
    import logging
    import shutil
    from crawlers.base import BaseCrawler
    class Crawler(BaseCrawler):
        name = 'example'
    target = tmp_path / 'example'
    target.mkdir()
    (target / 'profile').write_text('new committed')
    garbage = tmp_path / '.remote-garbage-example'
    garbage.mkdir()
    (garbage / 'partial').write_text('old partial')
    original = shutil.rmtree
    def fails(path, *args, **kwargs):
        if str(path) == str(garbage):
            return None
        return original(path, *args, **kwargs)
    monkeypatch.setattr(shutil, 'rmtree', fails)
    crawler = Crawler({'user_data_dir': str(tmp_path)}, logging.getLogger('test'))
    assert (target / 'profile').read_text() == 'new committed'
    assert crawler.user_data_dir == str(target)
    assert garbage.exists()


def test_failed_profile_publish_rolls_back_original(tmp_path, monkeypatch):
    import logging
    import os
    from contextlib import contextmanager
    from pathlib import Path
    from crawlers.base import BaseCrawler
    class Crawler(BaseCrawler):
        name = 'example'
        def login_url(self): return 'https://example.test/'
    target = tmp_path / 'example'
    target.mkdir()
    (target / 'profile').write_text('original')
    crawler = Crawler({'user_data_dir': str(tmp_path)}, logging.getLogger('test'))
    class Page:
        def goto(self, *args, **kwargs): pass
    class Bridge:
        def serve(self, ctx, page, done, timeout):
            (Path(crawler.user_data_dir) / 'profile').write_text('new')
            done.set()
    @contextmanager
    def browser(**kwargs): yield object()
    monkeypatch.setattr(crawler, 'browser', browser)
    monkeypatch.setattr(crawler, 'new_page', lambda ctx: Page())
    replace = os.replace
    def fail_new_profile(src, dst):
        if '.remote-session-example-' in str(src) and str(dst) == str(target):
            raise OSError('injected publish failure')
        return replace(src, dst)
    monkeypatch.setattr(os, 'replace', fail_new_profile)
    with pytest.raises(OSError):
        crawler._remote_login_window(threading.Event(), Bridge(), 1)
    assert (target / 'profile').read_text() == 'original'
    assert not list(tmp_path.glob('.remote-backup-*'))
