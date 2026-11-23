"""Dashboard SQL. All aggregation runs in the warehouse; only small result sets come back to Streamlit."""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from seed import STATUSES
from warehouse import Warehouse

TOURED = STATUSES.index("Toured")
LEASED = STATUSES.index("Leased")


@dataclass(frozen=True)
class Filters:
    start: date
    end: date  # inclusive
    neighborhoods: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()

    def shifted_back(self) -> "Filters":
        """Same-length window immediately before this one (for KPI deltas)."""
        days = (self.end - self.start).days + 1
        return Filters(self.start - timedelta(days), self.start - timedelta(1), self.neighborhoods, self.sources, self.teams)


def where(f: Filters) -> tuple[str, list]:
    clauses, params = ["l.created_at >= ?", "l.created_at < ?"], [f.start, f.end + timedelta(days=1)]
    for col, values in (("l.neighborhood", f.neighborhoods), ("l.source", f.sources), ("a.team", f.teams)):
        if values:  # empty selection = no filter
            clauses.append(f"{col} IN ({', '.join('?' * len(values))})")
            params += list(values)
    return " AND ".join(clauses), params


FROM = "FROM leads l JOIN agents a ON a.agent_id = l.agent_id"


def options(wh: Warehouse) -> dict:
    r = wh.query("SELECT MIN(created_at) lo, MAX(created_at) hi FROM leads").iloc[0]
    return {
        "neighborhoods": wh.query("SELECT DISTINCT neighborhood FROM leads ORDER BY 1")["neighborhood"].tolist(),
        "sources": wh.query("SELECT DISTINCT source FROM leads ORDER BY 1")["source"].tolist(),
        "teams": wh.query("SELECT DISTINCT team FROM agents ORDER BY 1")["team"].tolist(),
        "min_date": pd.Timestamp(r["lo"]).date(),
        "max_date": pd.Timestamp(r["hi"]).date(),
    }


def last_synced(wh: Warehouse) -> pd.Timestamp:
    return pd.Timestamp(wh.query("SELECT MAX(updated_at) AS ts FROM leads")["ts"].iloc[0])


def kpis(wh: Warehouse, f: Filters) -> pd.Series:
    w, p = where(f)
    return wh.query(
        f"""SELECT COUNT(*) AS leads,
                   COUNT_IF(l.funnel_step >= {TOURED}) AS tours,
                   COUNT_IF(l.status = 'Leased') AS leases,
                   COALESCE(SUM(l.commission), 0)::FLOAT AS revenue,
                   COALESCE(AVG(l.monthly_rent), 0)::FLOAT AS avg_rent
            {FROM} WHERE {w}""",
        p,
    ).iloc[0]


def funnel(wh: Warehouse, f: Filters) -> pd.DataFrame:
    w, p = where(f)
    steps = ",\n".join(f"COUNT_IF(l.funnel_step >= {i}) AS s{i}" for i in range(len(STATUSES)))
    row = wh.query(f"SELECT {steps} {FROM} WHERE {w}", p).iloc[0]
    return pd.DataFrame({"step": STATUSES, "leads": [int(row[f"s{i}"]) for i in range(len(STATUSES))]})


def monthly(wh: Warehouse, f: Filters) -> pd.DataFrame:
    w, p = where(f)
    return wh.query(
        f"""SELECT DATE_TRUNC('month', l.created_at) AS month,
                   COUNT(*) AS leads,
                   COUNT_IF(l.status = 'Leased') AS leases,
                   COALESCE(SUM(l.commission), 0)::FLOAT AS revenue
            {FROM} WHERE {w} GROUP BY 1 ORDER BY 1""",
        p,
    )


def by_source(wh: Warehouse, f: Filters) -> pd.DataFrame:
    w, p = where(f)
    return wh.query(
        f"""SELECT l.source, COUNT(*) AS leads, COUNT_IF(l.status = 'Leased') AS leases,
                   COUNT_IF(l.status = 'Leased') / NULLIF(COUNT(*), 0)::FLOAT AS conversion,
                   COALESCE(SUM(l.commission), 0)::FLOAT AS revenue
            {FROM} WHERE {w} GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )


def agent_leaderboard(wh: Warehouse, f: Filters) -> pd.DataFrame:
    w, p = where(f)
    return wh.query(
        f"""SELECT a.agent_name AS agent, a.team, COUNT(*) AS clients,
                   COUNT_IF(l.funnel_step >= {TOURED}) AS tours,
                   COUNT_IF(l.status = 'Leased') AS leases,
                   COUNT_IF(l.status = 'Leased') / NULLIF(COUNT(*), 0)::FLOAT AS conversion,
                   COALESCE(SUM(l.commission), 0)::FLOAT AS revenue
            {FROM} WHERE {w} GROUP BY 1, 2 ORDER BY revenue DESC""",
        p,
    )


def neighborhoods(wh: Warehouse, f: Filters) -> pd.DataFrame:
    w, p = where(f)
    return wh.query(
        f"""SELECT l.neighborhood, COUNT(*) AS leads, COUNT_IF(l.status = 'Leased') AS leases,
                   AVG(l.monthly_rent)::FLOAT AS avg_rent, COALESCE(SUM(l.commission), 0)::FLOAT AS revenue
            {FROM} WHERE {w} GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )


def calls_summary(wh: Warehouse, f: Filters) -> pd.DataFrame:
    w, p = where(f)
    return wh.query(
        f"""SELECT c.channel, COUNT(*) AS calls,
                   COUNT_IF(c.outcome = 'Connected') / NULLIF(COUNT(*), 0)::FLOAT AS connect_rate,
                   AVG(CASE WHEN c.outcome = 'Connected' THEN c.duration_sec END)::FLOAT AS avg_talk_sec
            FROM calls c JOIN leads l ON l.lead_id = c.lead_id JOIN agents a ON a.agent_id = l.agent_id
            WHERE {w} GROUP BY 1 ORDER BY calls DESC""",
        p,
    )


def lead_page(wh: Warehouse, f: Filters, page: int, page_size: int = 50) -> pd.DataFrame:
    w, p = where(f)
    offset = max(int(page), 0) * int(page_size)
    return wh.query(
        f"""SELECT l.lead_id, l.created_at, l.status, l.source, l.neighborhood, l.bedrooms, l.budget,
                   l.monthly_rent, a.agent_name AS agent
            {FROM} WHERE {w} ORDER BY l.created_at DESC LIMIT {int(page_size)} OFFSET {offset}""",
        p,
    )
