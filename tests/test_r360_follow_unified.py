# -*- coding: utf-8 -*-
"""r360: watches move into one route/flight list with one add flow."""
import os
import threading

import webui
from core.models import Route
from core.storage import PriceStorage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "webui.py"), encoding="utf-8").read()


def test_follow_navigation_is_one_unified_entry():
    nav = SRC[SRC.index('<div class="tabs" id="montabs"'):
              SRC.index('<!-- montabs 形制裁决')]
    assert nav.count('data-t="watch"') == 1
    assert 'data-t="trend"' not in nav
    assert 'data-t="details"' not in nav
    assert '关注<i id="wsTabN"' in nav


def test_follow_board_combines_route_and_flight_rows_and_details():
    for marker in (
        'id="followBoard"', 'id="followRows"', 'id="followDetail"',
        'id="followRouteDetail"', 'id="followFlightDetail"',
        "function followItems()", "kind:'route'", "kind:'watch'",
        "function followSelect(kind,id)",
    ):
        assert marker in SRC, marker
    assert "appendChild(route)" in SRC
    assert "appendChild(details)" in SRC
    assert "appendChild(studio)" in SRC
    mount = SRC[SRC.index("function followMount()"):SRC.index("function followItems()")]
    assert "style.display='none'" not in mount


def test_follow_edits_reuse_existing_save_paths_and_add_wizard():
    assert "setRoute(match.ui,match.index,key,v)" in SRC
    assert "x.r.from===row.from&&x.r.to===row.to&&(x.r.dates||[]).includes(date)" in SRC
    assert "wsPost({op:'update',id,patch:{target}})" in SRC
    assert "wsPost({op:'update',id,patch:{channels:[...channels]}})" in SRC
    assert "function followAdd()" in SRC and "cfgWatchWizardOpen()" in SRC
    assert "onclick=\"followAdd()\"" in SRC
    cfg = SRC[SRC.index("function cfgWatchRender()"):
              SRC.index("async function loadCfgWatch", SRC.index("function cfgWatchRender()"))]
    assert 'data-cwact="wizard"' in cfg
    assert 'data-cwact="add"' not in cfg


def test_route_details_filter_to_the_selected_route_and_date():
    detail = SRC[SRC.index("function followRenderDetail(item)"):
                 SRC.index("function followConfigRoute(item)")]
    assert "FOLLOW_SCOPE={route:routeKey,date:String(r.date||'')}" in detail
    assert "FLT.routes=new Set([routeKey]);FLT.dates=r.date?new Set([r.date]):new Set()" in detail
    assert "buildRouteChips();buildDateChips();" in detail
    assert "function followRowInScope(f)" in SRC
    assert "if(!followRowInScope(f))return false" in SRC


def test_route_selection_and_target_edit_are_stable_and_unambiguous():
    items = SRC[SRC.index("function followItems()"):SRC.index("function followRenderList()")]
    assert "const configIndex=r.config_index==null?i:r.config_index" in items
    assert "String(configIndex),trip].map(encodeURIComponent).join('|')" in items
    assert "label:routeLabel+' · '+trip" in items
    assert "function followItemSignature(item)" in SRC
    match = SRC[SRC.index("function followConfigRoute(item)"):
                SRC.index("function followEditRoute()")]
    assert "x.index===sourceIndex&&x.r.enabled!==false&&x.r.from===row.from" in match
    assert "if(matches.length!==1)return null" in match
    assert "followRouteIsRoundTrip(r)!==Boolean(row.config_round_trip)" in match
    assert "if(followRouteIsRoundTrip(r))return null" in match
    assert "S.config_revision!==CFG_REVISION" in match
    assert "followCfgRoutesUnchanged(cfgUser)" in match
    assert "function followCfgRoutesUnchanged(cfgUser)" in SRC
    assert "JSON.stringify(base.routes||[])===JSON.stringify(cfgUser.routes||[])" in SRC
    assert "routesDirty" in SRC and "航线尚未保存" in SRC
    assert '"config_revision": st.config_revision' in SRC
    assert 'with _STATE_LOCK:\n                        n = st.reload_cb()' in SRC
    main = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert 'with _STATE_LOCK:' in main[main.index('def reload_config():'):main.index('# 调度周期热更新', main.index('def reload_config():'))]
    assert '_STATE_SCHEMA = "probe-r365-2"' in SRC
    state_builder = SRC[SRC.index("def _build_state(sig):"):SRC.index("def _state_rebuild_async():")]
    assert "with _STATE_LOCK:" in state_builder
    assert "runtime_users = st.users" in state_builder
    assert "config_revision = st.config_revision" in state_builder
    assert "st.users is not runtime_users" in state_builder
    assert "st.config_revision != config_revision" in state_builder
    recover = SRC[SRC.index("def _wizard_txn_recover_runtime(config_path):"):
                  SRC.index("def _wizard_txn_commit(", SRC.index("def _wizard_txn_recover_runtime(config_path):"))]
    assert "with _STATE_LOCK:" in recover
    assert "_State.config_revision = hashlib.sha256" in recover
    state = SRC[SRC.index("def _user_state(u):"):SRC.index("\ndef _state_cache_path", SRC.index("def _user_state(u):"))]
    assert '_route["config_index"] = _config_index' in state
    assert '"config_round_trip": r.get("config_round_trip", False)' in state
    assert "routes_arr = []" in state
    assert "seen_rd" not in state
    main = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    build_routes = main[main.index("def build_routes(routes_cfg: list)"):
                        main.index("def build_users(cfg: dict)")]
    assert "for config_index, r in enumerate(routes_cfg or []):" in build_routes
    assert "route._config_index = config_index" in build_routes
    assert "getattr(_raw_route, \"_config_index\", _runtime_index)" in state


def test_user_state_keeps_config_identity_for_route_objects_and_dicts(tmp_path):
    dbp = str(tmp_path / "follow-state.db")
    PriceStorage(dbp)
    old_cfg = webui._State.cfg
    old_revision = webui._State.config_revision
    webui._State.cfg = {"output": {"db_path": dbp}}
    webui._State.config_revision = "test-revision"
    one_way = Route("SHA", "上海", "JFK", "纽约", ["2026-12-17"])
    one_way._config_index = 1  # raw config index after a filtered disabled rule
    routes = [
        one_way,
        {"config_index": 2, "from": "SHA", "from_name": "上海", "to": "JFK",
         "to_name": "纽约", "dates": ["2026-12-17"], "ret_dates": ["2027-01-01"]},
    ]
    try:
        state = webui._user_state({"name": "follow", "routes": routes})
    finally:
        webui._State.cfg = old_cfg
        webui._State.config_revision = old_revision
    rows = state["routesArr"]
    assert state["config_revision"] == "test-revision"
    assert [r["config_index"] for r in rows] == [1, 2]
    assert [r["config_round_trip"] for r in rows] == [False, True]


def test_build_state_discards_snapshot_if_hot_reload_wins(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    old_users = [{"name": "old", "routes": [{"from": "SHA"}]}]
    new_users = [{"name": "new", "routes": [{"from": "JFK"}]}]
    old_cfg, new_cfg = {"output": {"db_path": "/missing/old.db"}}, {"output": {"db_path": "/missing/new.db"}}
    monkeypatch.setattr(webui._State, "users", old_users)
    monkeypatch.setattr(webui._State, "cfg", old_cfg)
    monkeypatch.setattr(webui._State, "config_revision", "old-revision")
    monkeypatch.setattr(webui._State, "version", "test")
    def slow_user_state(user):
        entered.set()
        assert release.wait(3)
        return {"name": user["name"], "routes": []}
    monkeypatch.setattr(webui, "_user_state", slow_user_state)
    monkeypatch.setattr(webui, "_suspended_users", lambda *_: [])
    monkeypatch.setattr(webui, "_next_run", lambda: "")
    monkeypatch.setattr(webui, "_state_persist", lambda *_: None)
    cache = dict(webui._STATE_CACHE)
    webui._STATE_CACHE.update({"sig": None, "payload": None, "body": None,
                               "etag": None, "gz": None})
    thread = threading.Thread(target=webui._build_state, args=(None,))
    try:
        thread.start()
        assert entered.wait(3)
        with webui._STATE_LOCK:
            webui._State.users = new_users
            webui._State.cfg = new_cfg
            webui._State.config_revision = "new-revision"
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert webui._STATE_CACHE["payload"] is None
    finally:
        release.set()
        thread.join(3)
        webui._STATE_CACHE.clear()
        webui._STATE_CACHE.update(cache)


def test_config_read_retry_is_single_flight_and_rate_limited():
    ensure = SRC[SRC.index("function followEnsureConfig()"):SRC.index("async function followSetRouteTarget")]
    assert "FOLLOW_CFG_LOADING" in ensure
    assert "Date.now()-FOLLOW_CFG_LAST_TRY<15000" in ensure
    assert "loadCfg().then(ok=>{if(ok)" in ensure
    load = SRC[SRC.index("async function loadCfg()"):SRC.index("/* ===== r351 配置中心")]
    assert "return true}catch(e)" in load
    assert "return false}}" in load


def test_remove_confirmation_refreshes_visible_detail_button():
    remove = SRC[SRC.index("async function wsRemoveWatch(w)"):
                 SRC.index("function wsLocate(w)")]
    assert "WS.confirm=w.id;wsRenderList();wsRenderFocus()" in remove
    assert "WS.confirm='';wsRenderList();wsRenderFocus()" in remove


def test_legacy_watch_tabs_redirect_and_success_returns_to_unified_list():
    show = SRC[SRC.index("function showMonTab("):
              SRC.index("document.querySelectorAll('#montabs .mtab')", SRC.index("function showMonTab("))]
    assert "if(t==='trend'||t==='details')t='watch'" in show
    assert "function followWizardDone()" in SRC
    assert "showMonTab('watch');renderMonWatch(true)" in SRC

