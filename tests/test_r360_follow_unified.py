# -*- coding: utf-8 -*-
"""r360: watches move into one route/flight list with one add flow."""
import os

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


def test_follow_edits_reuse_existing_save_paths_and_add_wizard():
    assert "setRoute(match.ui,match.index,key,v)" in SRC
    assert "r.from===row.from&&r.to===row.to&&(!date||(r.dates||[]).includes(date))" in SRC
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
    assert "FLT.routes=new Set([String(r.label||'').replace" in detail
    assert "FLT.dates=r.date?new Set([r.date]):new Set()" in detail
    assert "buildRouteChips();buildDateChips();" in detail


def test_legacy_watch_tabs_redirect_and_success_returns_to_unified_list():
    show = SRC[SRC.index("function showMonTab("):
              SRC.index("document.querySelectorAll('#montabs .mtab')", SRC.index("function showMonTab("))]
    assert "if(t==='trend'||t==='details')t='watch'" in show
    assert "function followWizardDone()" in SRC
    assert "showMonTab('watch');renderMonWatch(true)" in SRC

