"""Opt-in local Chromium smoke test; no airline account or network required."""
import os
import threading

import pytest
from core.remote_login import RemoteLogin


@pytest.mark.skipif(os.environ.get('TM_RUN_BROWSER_TEST') != '1', reason='requires local Chromium')
def test_remote_browser_real_input_and_save(tmp_path):
    from playwright.sync_api import sync_playwright
    bridge = RemoteLogin()
    done, ready = threading.Event(), threading.Event()
    errors, saved = [], []

    def browser():
        try:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(str(tmp_path / 'profile'), headless=True,
                                                          viewport={'width': 400, 'height': 300})
                page = ctx.new_page()
                page.set_content('<input id="value" style="position:absolute;left:10px;top:10px;width:180px;height:40px">')
                ready.set()
                bridge.serve(ctx, page, done, 30)
                saved.append(page.locator('#value').input_value())
                ctx.close()
        except Exception as exc:
            errors.append(exc)
            ready.set()
        finally:
            bridge.close()

    thread = threading.Thread(target=browser, daemon=True)
    thread.start()
    assert ready.wait(20)
    if errors:
        raise errors[0]
    assert bridge.call(bridge.token, 'frame')['image']
    bridge.call(bridge.token, 'click', {'x': 30, 'y': 25})
    bridge.call(bridge.token, 'text', {'text': 'Test 123'})
    assert bridge.transition("finish")
    done.set()
    thread.join(10)
    assert not thread.is_alive()
    assert not errors
    assert saved == ['Test 123']


@pytest.mark.skipif(os.environ.get('TM_RUN_BROWSER_TEST') != '1', reason='requires local Chromium')
@pytest.mark.parametrize('finish', [False, True])
def test_chromium_profile_cancel_and_finish(tmp_path, monkeypatch, finish):
    import logging
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from crawlers.base import BaseCrawler
    monkeypatch.setattr('socket.getfqdn', lambda _: 'localhost')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<html><body>local login test</body></html>'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:%d/' % server.server_address[1]
    class Crawler(BaseCrawler):
        name = 'example'
        def login_url(self): return url
    crawler = Crawler({'user_data_dir': str(tmp_path)}, logging.getLogger('browser-test'))
    def set_state(ctx, page, value):
        ctx.add_cookies([{'name': 'login', 'value': value, 'url': url}])
        page.evaluate('(v) => localStorage.setItem("login", v)', value)
    def get_state():
        with crawler.browser(headless=True) as ctx:
            page = crawler.new_page(ctx)
            page.goto(url)
            cookies = {c['name']: c['value'] for c in ctx.cookies()}
            return cookies.get('login'), page.evaluate('localStorage.getItem("login")')
    try:
        with crawler.browser(headless=True, save_session='login') as ctx:
            page = crawler.new_page(ctx)
            page.goto(url)
            set_state(ctx, page, 'original')
        class Bridge:
            def serve(self, ctx, page, done, timeout):
                set_state(ctx, page, 'new')
                if not finish:
                    raise RuntimeError('cancelled')
                done.set()
        if finish:
            crawler._remote_login_window(threading.Event(), Bridge(), 5)
            assert get_state() == ('new', 'new')
        else:
            with pytest.raises(RuntimeError):
                crawler._remote_login_window(threading.Event(), Bridge(), 5)
            assert get_state() == ('original', 'original')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
