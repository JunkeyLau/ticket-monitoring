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
    return request_path(server, method, '/api/config', data)


def request_path(server, method, path, data=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    body = json.dumps(data) if data is not None else None
    conn.request(method, path, body=body,
                 headers={'Content-Type': 'application/json'})
    response = conn.getresponse()
    raw = response.read()
    result = response.status, (json.loads(raw) if raw else {})
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


def _wizard_payload(date):
    route = {'from': 'SHA', 'from_name': '上海', 'to': 'JFK',
             'to_name': '纽约', 'dates': [date],
             'transfer_arrival_max': '不限', 'alert_direct': 7200,
             'alert_transfer': 7200}
    watch = {'from': 'SHA', 'to': 'JFK', 'date': date, 'code': 'MU587',
             'depTime': '10:00', 'arrTime': '13:30', 'trans': 'Dubai',
             'cross': '+1天', 'channels': ['ctrip'], 'target': 7200,
             'active': True, 'notify': True}
    return {'revision': '', 'users': [{'name': 'original', 'routes': [route]}],
            'globals': {}, 'wizard_user': 'original', 'wizard_watch': watch}


def test_wizard_saves_route_and_selected_flight_together(config_server,
                                                          tmp_path, monkeypatch):
    import webui
    server, path = config_server
    watch_path = tmp_path / 'flight_watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    payload['revision'] = current['revision']
    status, result = request(server, 'POST', payload)
    assert status == 200 and result['wizard_saved'] is True
    saved = yaml.safe_load(path.read_text())['users'][0]['routes'][0]
    assert saved['transfer_arrival_max'] == '不限'
    watches = json.loads(watch_path.read_text())['original']
    assert watches[0]['code'] == 'MU587' and watches[0]['target'] == 7200


def test_wizard_transaction_receipt_identifies_exact_commit(config_server,
                                                              tmp_path,
                                                              monkeypatch):
    import webui
    server, path = config_server
    watch_path = tmp_path / 'flight_watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    txid = 'a' * 32
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    payload.update(revision=current['revision'], wizard_txid=txid)
    status, result = request(server, 'POST', payload)
    assert status == 200 and result['ok']
    status, receipt = request_path(server, 'GET',
        '/api/config/wizard-result?txid=' + txid)
    assert status == 200
    assert receipt == {'ok': True, 'saved': True,
                        'revision': result['revision'], 'wizard_saved': True,
                        'reload_warning': ''}


def test_wizard_receipt_writer_prunes_only_receipts_older_than_24_hours(
        tmp_path):
    import os
    import time
    import webui
    config_path = tmp_path / 'config.yaml'
    now = time.time()
    old = tmp_path / ('.ticket-monitoring-wizard-receipt-' + 'a' * 32 + '.json')
    recent = tmp_path / ('.ticket-monitoring-wizard-receipt-' + 'b' * 32 + '.json')
    unrelated = tmp_path / '.ticket-monitoring-wizard-receipt-not-a-txid.json'
    old.write_text('{}')
    recent.write_text('{}')
    unrelated.write_text('{}')
    os.utime(old, (now - 25 * 60 * 60, now - 25 * 60 * 60))
    os.utime(recent, (now - 23 * 60 * 60, now - 23 * 60 * 60))

    webui._wizard_write_receipt(str(config_path), {'txid': 'c' * 32,
                                                   'saved': True})

    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()
    assert (tmp_path / ('.ticket-monitoring-wizard-receipt-' + 'c' * 32 + '.json')).exists()


def test_watch_read_recovers_pending_config_watch_pair(config_server,
                                                        tmp_path, monkeypatch):
    import webui
    server, path = config_server
    watch_path = tmp_path / 'flight_watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    txid = 'c' * 32
    payload.update(revision=current['revision'], wizard_txid=txid)
    real_replace = webui.os.replace
    failed = False
    def interrupt_watch_publish(src, dst):
        nonlocal failed
        if str(dst) == str(watch_path) and not failed:
            failed = True
            raise OSError('injected interruption')
        return real_replace(src, dst)
    monkeypatch.setattr(webui.os, 'replace', interrupt_watch_publish)
    status, _ = request(server, 'POST', payload)
    assert status == 500 and failed and not watch_path.exists()
    monkeypatch.setattr(webui.os, 'replace', real_replace)
    status, result = request_path(server, 'GET',
        '/api/srx/watch?user=original')
    assert status == 200 and result['watches'][0]['code'] == 'MU587'
    status, receipt = request_path(server, 'GET',
        '/api/config/wizard-result?txid=' + txid)
    assert status == 200 and receipt['saved'] is True
    assert not (tmp_path / '.ticket-monitoring-wizard-transaction.json').exists()


def test_receipt_recovery_hot_reloads_the_same_process(config_server,
                                                        tmp_path, monkeypatch):
    import webui
    server, path = config_server
    watch_path = tmp_path / 'flight_watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    reloads = []
    def reload_runtime():
        users = yaml.safe_load(path.read_text())['users']
        reloads.append(len(users[0]['routes']))
        return 1
    monkeypatch.setattr(webui._State, 'reload_cb', reload_runtime)
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    txid = 'e' * 32
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    payload.update(revision=current['revision'], wizard_txid=txid)
    real_replace = webui.os.replace
    failed = False
    def interrupt_watch_publish(src, dst):
        nonlocal failed
        if str(dst) == str(watch_path) and not failed:
            failed = True
            raise OSError('injected interruption')
        return real_replace(src, dst)
    monkeypatch.setattr(webui.os, 'replace', interrupt_watch_publish)
    status, _ = request(server, 'POST', payload)
    assert status == 500 and not reloads
    monkeypatch.setattr(webui.os, 'replace', real_replace)
    status, receipt = request_path(server, 'GET',
        '/api/config/wizard-result?txid=' + txid)
    assert status == 200 and receipt['saved'] is True
    assert reloads == [1]
    assert receipt['reload_warning'] == ''


def test_config_post_recovery_hot_reloads_before_revision_conflict(
        config_server, tmp_path, monkeypatch):
    """Any config write request must refresh runtime after journal recovery."""
    import webui
    server, path = config_server
    watch_path = tmp_path / 'flight_watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    reloads = []

    def reload_runtime():
        users = yaml.safe_load(path.read_text())['users']
        reloads.append(len(users[0]['routes']))
        return 1

    monkeypatch.setattr(webui._State, 'reload_cb', reload_runtime)
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    txid = 'f' * 32
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    payload.update(revision=current['revision'], wizard_txid=txid)
    real_replace = webui.os.replace
    failed = False

    def interrupt_watch_publish(src, dst):
        nonlocal failed
        if str(dst) == str(watch_path) and not failed:
            failed = True
            raise OSError('injected interruption')
        return real_replace(src, dst)

    monkeypatch.setattr(webui.os, 'replace', interrupt_watch_publish)
    status, _ = request(server, 'POST', payload)
    assert status == 500 and failed and not reloads

    # The next request is a regular config save with the now-stale revision.
    # Recovery must roll files forward and refresh runtime before it returns 409.
    monkeypatch.setattr(webui.os, 'replace', real_replace)
    status, conflict = request(server, 'POST', {
        'revision': current['revision'], 'users': [{'name': 'stale'}],
        'globals': {},
    })
    assert status == 409 and conflict['conflict'] is True
    assert reloads == [1]
    _, recovered_config = request(server, 'GET')
    assert webui._State.config_revision == recovered_config['revision']
    status, receipt = request_path(server, 'GET',
        '/api/config/wizard-result?txid=' + txid)
    assert status == 200 and receipt['saved'] is True
    assert receipt['reload_warning'] == ''


def test_wizard_recovers_both_files_after_second_replace_failure(
        config_server, tmp_path, monkeypatch):
    import webui
    server, path = config_server
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    txid = 'd' * 32
    payload.update(revision=current['revision'], wizard_txid=txid)
    watch_path = tmp_path / 'watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    real_replace = webui.os.replace
    failed = False
    def fail_watch_once(src, dst):
        nonlocal failed
        if str(dst) == str(watch_path) and not failed:
            failed = True
            raise OSError('injected interruption before watch replace')
        return real_replace(src, dst)
    monkeypatch.setattr(webui.os, 'replace', fail_watch_once)
    status, result = request(server, 'POST', payload)
    assert status == 500 and not result['ok']
    assert failed and path.read_bytes() != b'users:\n  - name: original\nweb:\n  port: 8765\n'
    assert watch_path.exists() is False
    # A new page load recovers the journal and observes both files committed.
    status, loaded = request(server, 'GET')
    assert status == 200 and loaded['users'][0]['routes'][0]['from'] == 'SHA'
    assert json.loads(watch_path.read_text())['original'][0]['code'] == 'MU587'
    status, receipt = request_path(server, 'GET',
        '/api/config/wizard-result?txid=' + txid)
    assert status == 200 and receipt['saved'] is True
    assert not (tmp_path / '.ticket-monitoring-wizard-transaction.json').exists()


def test_runtime_recovery_refreshes_config_revision_and_invalidates_state(
        tmp_path, monkeypatch):
    import hashlib
    import webui
    path = tmp_path / 'config.yaml'
    recovered = b'users:\n  - name: recovered\n'
    path.write_bytes(recovered)
    old_cache = dict(webui._STATE_CACHE)
    old_users, old_cfg = webui._State.users, webui._State.cfg
    monkeypatch.setattr(webui, '_wizard_txn_journal', lambda *_: str(tmp_path / 'journal.json'))
    monkeypatch.setattr(webui, '_wizard_txn_recover', lambda *_: True)
    monkeypatch.setattr(webui._State, 'config_revision', 'before-recovery')
    def reload_runtime():
        with webui._STATE_LOCK:
            webui._State.users = [{'name': 'recovered', 'routes': []}]
            webui._State.cfg = {'users': [{'name': 'recovered'}]}
    monkeypatch.setattr(webui._State, 'reload_cb', reload_runtime)
    webui._STATE_CACHE.update({'sig': 'stale', 'payload': {'stale': True},
                               'body': b'stale', 'etag': 'stale', 'gz': b'stale'})
    try:
        assert webui._wizard_txn_recover_runtime(str(path)) is True
        assert webui._State.config_revision == hashlib.sha256(recovered).hexdigest()
        assert all(webui._STATE_CACHE[k] is None for k in ('sig', 'payload', 'body', 'etag', 'gz'))
    finally:
        with webui._STATE_LOCK:
            webui._State.users, webui._State.cfg = old_users, old_cfg
        webui._STATE_CACHE.clear()
        webui._STATE_CACHE.update(old_cache)


def test_wizard_reports_persisted_save_when_runtime_reload_fails(
        config_server, tmp_path, monkeypatch):
    import webui
    server, path = config_server
    watch_path = tmp_path / 'watch.json'
    monkeypatch.setattr(webui, '_watch_path', lambda: str(watch_path))
    monkeypatch.setattr(webui._State, 'reload_cb',
                        lambda: (_ for _ in ()).throw(RuntimeError('reload failed')))
    _, current = request(server, 'GET')
    from datetime import date, timedelta
    payload = _wizard_payload((date.today() + timedelta(days=30)).isoformat())
    payload['revision'] = current['revision']
    status, result = request(server, 'POST', payload)
    assert status == 200 and result['ok'] and result['wizard_saved']
    assert result['reload_warning'] == 'reload failed'
    assert yaml.safe_load(path.read_text())['users'][0]['routes'][0]['from'] == 'SHA'
    assert json.loads(watch_path.read_text())['original'][0]['code'] == 'MU587'
