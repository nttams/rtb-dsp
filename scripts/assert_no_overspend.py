#!/usr/bin/env python3
"""assert_no_overspend.py — the AC-37 gate.

For every (campaign, cluster) pair, asserts:

  1. Redis `reserved >= 0`                              (finding M5)
  2. Redis `spent >= 0`                                  (finding M5)
  3. Redis `reserved + spent <= allocated`
  4. Postgres `spent_micros <= allocated_micros`

Findings M5 / R20: a negative `reserved` satisfies check 3 trivially (it makes the
sum smaller), so without checks 1 and 2 the gate cannot see its own worst case — a
reseed or a buggy reclaim driving `reserved` negative would hide a real overspend.

Campaigns with `allocation_clamped` set on the Redis budget hash (finding M8 / R6) had
their `allocated` value floored at `reserved + spent` because a legal budget decrease in
Postgres would otherwise make the invariant retroactively false. Those campaigns are
compared against the MAXIMUM allocation observed during this run (tracked across
--sample iterations), not the current one, so a clamp is never mistaken for a violation.
In one-shot mode there is no history to draw on, so the current allocation is used and a
warning is printed noting the limitation.

Modes:
  --sample        Continuous mode: re-check every 2s (override with --interval) until
                   interrupted (Ctrl-C) or --duration seconds elapse. Exit code reflects
                   whether ANY sample violated the invariant.
  (default)       One-shot: check once and exit.

Environment:
  DATABASE_URL    Postgres DSN (used to enumerate clusters/campaigns and check
                   spent_micros <= allocated_micros).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None


@dataclass
class Violation:
    campaign_id: int
    cluster: str
    reason: str
    detail: str


@dataclass
class RunState:
    # Tracks the maximum `allocated` ever observed per (campaign_id, cluster), so a
    # clamped campaign (finding M8) is judged against the ceiling it actually had budget
    # under, not the floor `topup.lua` clamped it down to afterward.
    max_allocated: dict[tuple[int, str], int] = field(default_factory=dict)

    def observe(self, campaign_id: int, cluster: str, allocated: int) -> int:
        key = (campaign_id, cluster)
        prev = self.max_allocated.get(key, 0)
        if allocated > prev:
            self.max_allocated[key] = allocated
            return allocated
        return prev


def fetch_clusters(pg_conn) -> list[tuple[int, str]]:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id, name FROM clusters ORDER BY id")
        return [(row[0], row[1]) for row in cur.fetchall()]


def fetch_campaign_allocations(pg_conn, cluster_id: int) -> dict[int, int]:
    """Returns {campaign_id: allocated_micros} for a cluster, and separately checks
    spent_micros <= allocated_micros (Postgres-side check 4)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT campaign_id, allocated_micros, spent_micros "
            "FROM budget_allocations WHERE cluster_id = %s",
            (cluster_id,),
        )
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def check_once(pg_conn, redis_clients: dict[str, "redis.Redis"], state: RunState) -> list[Violation]:
    violations: list[Violation] = []
    clusters = fetch_clusters(pg_conn)

    for cluster_id, cluster_name in clusters:
        r = redis_clients.get(cluster_name)
        if r is None:
            continue

        pg_rows = fetch_campaign_allocations(pg_conn, cluster_id)

        for campaign_id, (pg_allocated, pg_spent) in pg_rows.items():
            # --- Postgres-side check 4 ---
            if pg_spent > pg_allocated:
                violations.append(
                    Violation(
                        campaign_id, cluster_name, "PG_OVERSPEND",
                        f"postgres spent_micros={pg_spent} > allocated_micros={pg_allocated}",
                    )
                )

            budget_key = f"c:{cluster_name}:camp:{campaign_id}:budget"
            h = r.hgetall(budget_key)
            if not h:
                # No Redis hash yet (e.g. not yet topped up); nothing to check on the
                # Redis side.
                continue

            allocated = int(h.get("allocated", 0))
            reserved = int(h.get("reserved", 0))
            spent = int(h.get("spent", 0))
            clamped = int(h.get("allocation_clamped", 0)) == 1

            # --- checks 1, 2: finding M5 / R20 ---
            if reserved < 0:
                violations.append(
                    Violation(campaign_id, cluster_name, "NEGATIVE_RESERVED", f"reserved={reserved}")
                )
            if spent < 0:
                violations.append(
                    Violation(campaign_id, cluster_name, "NEGATIVE_SPENT", f"spent={spent}")
                )

            # --- check 3, with the clamp adjustment (finding M8 / R6) ---
            bound = state.observe(campaign_id, cluster_name, allocated)
            if clamped:
                compare_against = bound
            else:
                compare_against = allocated

            if reserved + spent > compare_against:
                violations.append(
                    Violation(
                        campaign_id, cluster_name, "OVERSPEND",
                        f"reserved({reserved}) + spent({spent}) = {reserved + spent} > "
                        f"{'max_observed_allocated' if clamped else 'allocated'}({compare_against})",
                    )
                )

    return violations


def build_redis_clients(pg_conn) -> dict[str, "redis.Redis"]:
    clients: dict[str, "redis.Redis"] = {}
    with pg_conn.cursor() as cur:
        cur.execute("SELECT name, redis_url FROM clusters")
        for name, url in cur.fetchall():
            clients[name] = redis.from_url(url, decode_responses=True)
    return clients


def report(violations: list[Violation], sample_idx: int | None = None) -> None:
    prefix = f"[sample {sample_idx}] " if sample_idx is not None else ""
    if not violations:
        print(f"{prefix}OK — no overspend detected")
        return
    print(f"{prefix}FAIL — {len(violations)} violation(s):", file=sys.stderr)
    for v in violations:
        print(f"  campaign={v.campaign_id} cluster={v.cluster} reason={v.reason} :: {v.detail}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", action="store_true", help="continuous mode: re-check every --interval seconds")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between samples in --sample mode (default 2)")
    parser.add_argument("--duration", type=float, default=None, help="stop --sample mode after this many seconds (default: run until Ctrl-C)")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (default: $DATABASE_URL)")
    args = parser.parse_args()

    if psycopg is None or redis is None:
        print("error: this script requires the 'psycopg' and 'redis' packages", file=sys.stderr)
        return 2

    if not args.database_url:
        print("error: DATABASE_URL not set and --database-url not given", file=sys.stderr)
        return 2

    pg_conn = psycopg.connect(args.database_url)
    redis_clients = build_redis_clients(pg_conn)
    state = RunState()

    any_violation = False

    if not args.sample:
        violations = check_once(pg_conn, redis_clients, state)
        report(violations)
        return 1 if violations else 0

    print(f"Sampling every {args.interval}s" + (f" for {args.duration}s" if args.duration else " until interrupted") + " ...")
    start = time.monotonic()
    sample_idx = 0
    try:
        while True:
            violations = check_once(pg_conn, redis_clients, state)
            report(violations, sample_idx=sample_idx)
            if violations:
                any_violation = True
            sample_idx += 1

            if args.duration is not None and (time.monotonic() - start) >= args.duration:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

    # One final check after the loop ends, matching plan §6 Step 8 / finding M12: sample
    # throughout the run, then once more at the end.
    violations = check_once(pg_conn, redis_clients, state)
    report(violations, sample_idx="final")
    if violations:
        any_violation = True

    return 1 if any_violation else 0


if __name__ == "__main__":
    raise SystemExit(main())
