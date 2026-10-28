"""The configuration UI must not redirect proxy users to the backend port."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_port_redirect_uses_previous_config_not_proxy_port():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed to exercise the browser helper')
    source = (Path(__file__).resolve().parent.parent / 'webui.py').read_text()
    start = source.index('function cfgPortRedirect(')
    end = source.index('\nasync function saveCfgImpl(', start)
    helper = source[start:end]
    script = helper + r'''
const assert = require('assert');
assert.strictEqual(cfgPortRedirect('https://ticket.example:8888/#view', 8765, 8765), '');
assert.strictEqual(cfgPortRedirect('https://ticket.example:8888/#view', 8765, 9000), '');
assert.strictEqual(cfgPortRedirect('http://localhost:8765/?a=1#view', 8765, 9000), 'http://localhost:9000/?a=1#view');
assert.strictEqual(cfgPortRedirect('http://localhost:8765/', 8765, 8765), '');
assert.strictEqual(cfgPortRedirect('https://ticket.example:8765/', 8765, 9000, true), '');
'''
    subprocess.run([node, '-e', script], check=True, timeout=10)
