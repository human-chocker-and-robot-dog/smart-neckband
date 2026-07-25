from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path

from .health_contract import compact_json, validate_wearer_id
from .health_store import HealthStore


def deletion_plan(
    store: HealthStore,
    *,
    wearer_id: str,
    before_utc: str,
) -> dict[str, object]:
    counts = store.deletion_counts(
        wearer_id=wearer_id,
        before_utc=before_utc,
    )
    confirmation_payload = {
        "wearer_id": wearer_id,
        "before_utc": before_utc,
        "counts": counts,
    }
    token = hashlib.sha256(compact_json(confirmation_payload).encode()).hexdigest()
    return {
        **confirmation_payload,
        "confirmation_token": token,
        "raw_sessions_deleted": False,
    }


def execute_deletion(
    store: HealthStore,
    *,
    wearer_id: str,
    before_utc: str,
    confirmation_token: str,
) -> dict[str, int]:
    plan = deletion_plan(
        store,
        wearer_id=wearer_id,
        before_utc=before_utc,
    )
    if not hmac.compare_digest(
        confirmation_token,
        str(plan["confirmation_token"]),
    ):
        raise ValueError("confirmation token does not match the current deletion plan")
    return store.delete_wearer_records(
        wearer_id=wearer_id,
        before_utc=before_utc,
        expected_counts=plan["counts"],
    )


def _default_db_path() -> Path:
    value = os.environ.get("SMART_COLLAR_HEALTH_DB_PATH")
    if value:
        return Path(value)
    return Path(__file__).resolve().parents[3] / "data" / "health" / "health_state.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Local administrator operations for Health MCP data"
    )
    parser.add_argument("--db", type=Path, default=_default_db_path())
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser(
        "status",
        help="Print non-sensitive local Health MCP observability as JSON",
    )
    status.add_argument("--wearer-id", required=True)
    for name in ("plan-delete", "delete"):
        child = subparsers.add_parser(name)
        child.add_argument("--wearer-id", required=True)
        child.add_argument(
            "--before-utc",
            required=True,
            help="Inclusive RFC3339 UTC millisecond timestamp ending in Z",
        )
        if name == "delete":
            child.add_argument("--confirmation-token", required=True)
    args = parser.parse_args(argv)
    try:
        validate_wearer_id(args.wearer_id)
    except ValueError as exc:
        parser.error(str(exc))
    if args.command in {"plan-delete", "delete"} and not args.before_utc.endswith("Z"):
        parser.error("--before-utc must be UTC and end in Z")
    store = HealthStore(args.db)
    if args.command == "status":
        print(
            json.dumps(
                store.get_observability(wearer_id=args.wearer_id),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "plan-delete":
        print(
            json.dumps(
                deletion_plan(
                    store,
                    wearer_id=args.wearer_id,
                    before_utc=args.before_utc,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            json.dumps(
                execute_deletion(
                    store,
                    wearer_id=args.wearer_id,
                    before_utc=args.before_utc,
                    confirmation_token=args.confirmation_token,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
