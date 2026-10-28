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
    done.set()
    thread.join(10)
    assert not thread.is_alive()
    assert not errors
    assert saved == ['Test 123']
