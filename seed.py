"""Generate a synthetic brokerage CRM in SQLite (stand-in for the production PostgreSQL DB).

    python seed.py                 # fresh DB with 80k leads
    python seed.py --activity 500  # simulate a day of new leads + status changes (for incremental sync demo)
"""

import argparse
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SOURCE_DB = Path("data/crm.db")
SCHEMA = Path(__file__).parent / "sql" / "schema.sql"
TS = "%Y-%m-%d %H:%M:%S"

STATUSES = ["New", "Contacted", "Qualified", "Tour Scheduled", "Toured", "Application", "Leased"]
STEP_PASS_RATE = [0.88, 0.58, 0.72, 0.78, 0.55, 0.82]  # chance of moving from step i to i+1
SOURCES = {"AI Voice Call": 1.04, "Zillow": 0.97, "Apartments.com": 0.95, "Website": 1.0, "Referral": 1.12, "Instagram": 0.88}
SOURCE_WEIGHTS = [34, 20, 16, 14, 6, 10]
NEIGHBORHOODS = {  # base 1BR rent
    "West Loop": 2900, "River North": 2800, "Gold Coast": 3000, "Lincoln Park": 2500, "Wicker Park": 2200,
    "Lakeview": 2000, "Logan Square": 1900, "South Loop": 2300, "Bucktown": 2300, "Uptown": 1650,
    "Pilsen": 1500, "Hyde Park": 1600,
}
TEAMS = ["Downtown", "North Side", "West Side"]
FIRST = ["Ava", "Marcus", "Priya", "Daniel", "Sofia", "Jamal", "Elena", "Noah", "Grace", "Omar", "Lena", "Victor",
         "Hannah", "Diego", "Maya", "Ethan", "Chloe", "Isaac", "Nina", "Caleb", "Zara", "Leo", "Ruby", "Adrian"]
LAST = ["Brooks", "Patel", "Nguyen", "Reyes", "Kowalski", "Bennett", "Okafor", "Lindqvist", "Hayes", "Moreno",
        "Fischer", "Shah", "Carter", "Ivanova", "Sullivan", "Kim", "Duarte", "Novak", "Hughes", "Ali"]
LOST_AFTER_DAYS = 30


def fmt(dt):
    return dt.strftime(TS)


def lead_row(rng, lead_id, agent_ids, created, now):
    source = rng.choices(list(SOURCES), SOURCE_WEIGHTS)[0]
    hood = rng.choice(list(NEIGHBORHOODS))
    beds = rng.choices([0, 1, 2, 3], [15, 45, 30, 10])[0]
    budget = int(NEIGHBORHOODS[hood] * (0.85 + 0.35 * beds) * rng.uniform(0.85, 1.2) / 50) * 50
    step = 0
    while step < len(STEP_PASS_RATE) and rng.random() < min(STEP_PASS_RATE[step] * SOURCES[source], 0.97):
        step += 1
    latest = now - timedelta(minutes=rng.randint(1, 720))  # spread out so rows don't share one timestamp
    updated = max(created, min(created + timedelta(days=step * rng.uniform(1.5, 5), hours=rng.uniform(0, 8)), latest))
    status = STATUSES[step]
    if status != "Leased" and (now - updated).days > LOST_AFTER_DAYS:
        status = "Lost"
    return leased(rng, [lead_id, rng.choice(agent_ids), source, hood, beds, budget, status, step, None, None,
                        fmt(created), fmt(updated)])


def leased(rng, row):
    if row[6] == "Leased":
        rent = round(row[5] * rng.uniform(0.9, 1.02), 2)
        row[8], row[9] = rent, round(rent * rng.choice([0.75, 0.85, 1.0]), 2)
    return row


def call_rows(rng, lead, first_call_id, now):
    lead_id, agent_id, created, updated = lead[0], lead[1], datetime.strptime(lead[10], TS), datetime.strptime(lead[11], TS)
    rows = []
    for i in range(rng.randint(1, 5)):
        at = min(created + (updated - created) * rng.random() + timedelta(minutes=rng.randint(5, 600)), now)
        ai = rng.random() < 0.7
        outcome = rng.choices(["Connected", "Voicemail", "No Answer"], [55, 25, 20] if ai else [65, 20, 15])[0]
        duration = rng.randint(45, 540) if outcome == "Connected" else rng.randint(5, 40)
        rows.append([first_call_id + i, lead_id, None if ai else agent_id, "AI Voice" if ai else "Agent",
                     outcome, duration, fmt(at), fmt(at)])
    return rows


def connect(path=SOURCE_DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA.read_text())
    return db


def insert(db, table, rows):
    if rows:
        db.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)


def seed(path=SOURCE_DB, n_leads=80_000, n_agents=24, days=540, seed=42, now=None):
    path.unlink(missing_ok=True)
    rng, now = random.Random(seed), now or datetime.now().replace(microsecond=0)
    start = now - timedelta(days=days)
    db = connect(path)
    names = rng.sample([f"{f} {l}" for f in FIRST for l in LAST], n_agents)
    insert(db, "agents", [[i + 1, n, TEAMS[i % len(TEAMS)], fmt(start)] for i, n in enumerate(names)])
    agent_ids = list(range(1, n_agents + 1))
    # more leads in recent months: square-root skew toward `now`
    created = sorted(start + timedelta(seconds=days * 86400 * rng.random() ** 0.8) for _ in range(n_leads))
    leads = [lead_row(rng, i + 1, agent_ids, c, now) for i, c in enumerate(created)]
    calls = []
    for lead in leads:
        calls += call_rows(rng, lead, len(calls) + 1, now)
    insert(db, "leads", leads)
    insert(db, "calls", calls)
    db.commit()
    return len(leads), len(calls)


def simulate_activity(path=SOURCE_DB, n=500, seed=None, now=None):
    """New leads arrive and open leads move one step down the funnel; everything touched gets updated_at = now."""
    rng, now = random.Random(seed), now or datetime.now().replace(microsecond=0)
    db = connect(path)
    agent_ids = [r[0] for r in db.execute("SELECT agent_id FROM agents")]
    next_lead = db.execute("SELECT COALESCE(MAX(lead_id), 0) + 1 FROM leads").fetchone()[0]
    next_call = db.execute("SELECT COALESCE(MAX(call_id), 0) + 1 FROM calls").fetchone()[0]
    new = []
    for i in range(n):
        row = lead_row(rng, next_lead + i, agent_ids, now - timedelta(hours=rng.uniform(0, 20)), now)
        row[6], row[7], row[8], row[9], row[11] = "New", 0, None, None, fmt(now)
        new.append(row)
    calls = []
    for lead in new:
        calls += call_rows(rng, lead, next_call + len(calls), now)
    for call in calls:
        call[7] = fmt(now)  # updated_at is stamped at write time, like the production DB
    insert(db, "leads", new)
    insert(db, "calls", calls)
    open_leads = db.execute(
        "SELECT * FROM leads WHERE status NOT IN ('Leased', 'Lost') AND lead_id < ? ORDER BY RANDOM() LIMIT ?",
        (next_lead, n)).fetchall()
    for lead in map(list, open_leads):
        lead[7] += 1
        lead[6], lead[11] = STATUSES[lead[7]], fmt(now)
        leased(rng, lead)
        db.execute("UPDATE leads SET status=?, funnel_step=?, monthly_rent=?, commission=?, updated_at=? WHERE lead_id=?",
                   (lead[6], lead[7], lead[8], lead[9], lead[11], lead[0]))
    db.commit()
    return len(new), len(open_leads), len(calls)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", type=int, help="simulate N new leads + N status changes on the existing DB")
    ap.add_argument("--leads", type=int, default=80_000)
    args = ap.parse_args()
    if args.activity:
        new, moved, calls = simulate_activity(n=args.activity)
        print(f"{new} new leads, {moved} leads advanced, {calls} new calls -> {SOURCE_DB}")
    else:
        leads, calls = seed(n_leads=args.leads)
        print(f"Seeded {leads:,} leads and {calls:,} calls -> {SOURCE_DB}")
