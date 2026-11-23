from datetime import date, datetime, timedelta

import pytest

import queries as q
from seed import seed, simulate_activity
from sync import sync
from warehouse import Warehouse

NOW = datetime(2026, 11, 1, 12, 0, 0)


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "crm.db"
    seed(src, n_leads=3_000, n_agents=6, days=120, now=NOW)
    wh = Warehouse("duckdb", str(tmp_path / "wh.duckdb"))
    sync(wh, src)
    return src, wh


def counts(wh):
    return wh.query("SELECT (SELECT COUNT(*) FROM leads) l, (SELECT COUNT(DISTINCT lead_id) FROM leads) d").iloc[0]


def test_initial_load_copies_everything(env):
    _, wh = env
    c = counts(wh)
    assert c.l == c.d == 3_000


def test_incremental_sync_moves_only_changes_and_is_idempotent(env):
    src, wh = env
    at_watermark = wh.query("SELECT COUNT(*) n FROM leads WHERE updated_at = (SELECT MAX(updated_at) FROM leads)").n[0]
    simulate_activity(src, n=100, seed=1, now=NOW + timedelta(hours=2))
    moved = sync(wh, src)
    assert moved["leads"] == 200 + at_watermark  # 100 new + 100 advanced (+ boundary rows re-pulled by >=)
    assert sync(wh, src)["leads"] == 200  # same-second rows re-merged ...
    c = counts(wh)
    assert c.l == c.d == 3_100  # ... without creating duplicates


def test_dashboard_queries_agree_with_each_other(env):
    _, wh = env
    f = q.Filters(date(2026, 1, 1), date(2026, 12, 31))
    k = q.kpis(wh, f)
    fn = q.funnel(wh, f)
    assert fn.leads.iloc[0] == k.leads and fn.leads.iloc[-1] == k.leases
    assert fn.leads.is_monotonic_decreasing
    assert q.monthly(wh, f).revenue.sum() == pytest.approx(k.revenue)
    assert q.agent_leaderboard(wh, f).leases.sum() == k.leases
    assert len(q.lead_page(wh, f, 0)) == 50
    assert not q.calls_summary(wh, f).empty


def test_filters_narrow_results(env):
    _, wh = env
    base = q.Filters(date(2026, 1, 1), date(2026, 12, 31))
    hood = wh.query("SELECT neighborhood FROM leads LIMIT 1").neighborhood.iloc[0]
    narrowed = q.Filters(base.start, base.end, neighborhoods=(hood,))
    assert 0 < q.kpis(wh, narrowed).leads < q.kpis(wh, base).leads
    assert q.by_source(wh, narrowed).leads.sum() == q.kpis(wh, narrowed).leads
