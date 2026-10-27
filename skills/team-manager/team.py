#!/usr/bin/env python3
"""CLI for team-manager database workflows."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from team_store import TeamStore
import team_ingest


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True))


def cmd_member_add(args: argparse.Namespace) -> int:
    print(TeamStore().add_member(args.member, json.loads(args.data)))
    return 0


def cmd_member_set(args: argparse.Namespace) -> int:
    TeamStore().update_member(args.member, args.revision, json.loads(args.data))
    print("ok")
    return 0


def cmd_leave_import(args: argparse.Namespace) -> int:
    result = team_ingest.import_leave_csv(
        TeamStore(),
        Path(args.csv),
        apply=args.apply,
        source_label=args.source_label,
        date_format=args.date_format,
        assume_status=args.assume_status,
        member_map=json.loads(args.member_map) if args.member_map else None,
        allow_partial=args.allow_partial,
    )
    _print(result)
    return 0 if result.get("status") in {"preview", "imported", "skipped-already-imported"} else 3


def cmd_leave_stats(args: argparse.Namespace) -> int:
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    _print(team_ingest.leave_stats(TeamStore(), args.member, as_of))
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    _print(team_ingest.backfill_evidence(
        TeamStore(),
        tasks_db=Path(args.tasks_db).expanduser(),
        bugs_db=Path(args.bugs_db).expanduser(),
        apply=args.apply,
        confirm_merged=args.confirm_merged,
    ))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    TeamStore().init_db()
    print("initialized team store")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    summary = TeamStore().migrate(Path(args.source).expanduser(), dry_run=args.dry_run)
    _print(summary)
    return 0


def cmd_members(args: argparse.Namespace) -> int:
    _print(TeamStore().list_members())
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    member = TeamStore().get_member(args.member)
    if not member:
        print(f"member not found: {args.member}", file=sys.stderr)
        return 2
    _print(member)
    return 0


def cmd_payload(args: argparse.Namespace) -> int:
    _print(TeamStore().payload(args.member, args.audience))
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    data = json.loads(args.data)
    ident = TeamStore().create_entity(args.type, args.member, data)
    print(ident)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    _print(TeamStore().list_entities(args.type, args.member))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    patch = json.loads(args.data)
    TeamStore().update_entity(args.type, args.id, args.revision, patch)
    print("ok")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    ident = TeamStore().append_note(args.type, args.id, args.text)
    print(ident)
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    ident = TeamStore().link_ref(
        source_type=args.type,
        source_id=args.id,
        domain_name=args.domain,
        target_type=args.target_type,
        target_id=args.target_id,
        relation=args.relation,
    )
    print(ident)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    print(TeamStore().export_entity(args.type, args.id, args.audience), end="")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    if args.as_of:
        as_of = date.fromisoformat(args.as_of)
    else:
        as_of = date.today()
    result = TeamStore().metrics(
        member_key=args.member,
        scope=args.scope,
        as_of=as_of,
        quarter=args.quarter,
    )
    _print(result)
    return 0


def cmd_group_prs(args: argparse.Namespace) -> int:
    prs = json.loads(args.data)
    if not isinstance(prs, list):
        raise ValueError("--data must be a JSON array")
    _print(TeamStore().group_prs_by_jira_title(prs))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="team-manager data CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("migrate")
    p.add_argument("--source", default="~/team-planner/members")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("members")
    p.set_defaults(func=cmd_members)

    p = sub.add_parser("show")
    p.add_argument("member")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("payload")
    p.add_argument("--member")
    p.add_argument("--audience", choices=["private", "shared"], default="shared")
    p.set_defaults(func=cmd_payload)

    p = sub.add_parser("new")
    p.add_argument("type", choices=[
        "assignment", "feedback", "roadblock", "document", "feature", "issue-completion"
    ])
    p.add_argument("--member")
    p.add_argument("--data", required=True, help="JSON object payload")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list")
    p.add_argument("type", choices=[
        "assignment", "meeting", "feedback", "roadblock", "document", "feature", "issue-completion"
    ])
    p.add_argument("--member")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("set")
    p.add_argument("type", choices=["assignment", "feedback", "roadblock", "document", "feature"])
    p.add_argument("id")
    p.add_argument("--revision", required=True)
    p.add_argument("--data", required=True)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("note")
    p.add_argument("type", choices=["assignment", "meeting"])
    p.add_argument("id")
    p.add_argument("text")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("link")
    p.add_argument("type", choices=["assignment", "meeting", "feedback", "roadblock", "document", "feature"])
    p.add_argument("id")
    p.add_argument("--domain", required=True)
    p.add_argument("--target-type", required=True)
    p.add_argument("--target-id", required=True)
    p.add_argument("--relation", required=True)
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("export")
    p.add_argument("type", choices=["meeting", "assignment", "feedback", "roadblock", "document", "feature"])
    p.add_argument("id")
    p.add_argument("--audience", choices=["private", "shared"], default="shared")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("metrics")
    p.add_argument("--member", required=True)
    p.add_argument("--scope", choices=["total", "quarter", "last-sprint", "current-sprint"], required=True)
    p.add_argument("--as-of")
    p.add_argument("--quarter")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("group-prs")
    p.add_argument("--data", required=True, help="JSON array of PR objects")
    p.set_defaults(func=cmd_group_prs)

    p = sub.add_parser("member-add", help="create a new roster member")
    p.add_argument("member", help="stable member key, e.g. kostya")
    p.add_argument("--data", required=True, help="JSON object; display_name required")
    p.set_defaults(func=cmd_member_add)

    p = sub.add_parser("member-set", help="revision-guarded edit of member profile fields")
    p.add_argument("member")
    p.add_argument("--revision", required=True, help="from `show <member>`")
    p.add_argument("--data", required=True, help="JSON object of editable fields")
    p.set_defaults(func=cmd_member_set)

    p = sub.add_parser("leave-import", help="ingest an HR leave CSV (preview unless --apply)")
    p.add_argument("csv")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--source-label", default="hr-csv")
    p.add_argument("--date-format", choices=sorted(team_ingest.DATE_FORMATS))
    p.add_argument("--assume-status", choices=sorted(team_ingest.STATUS_MAP))
    p.add_argument("--member-map", help='JSON {"<csv identity>": "<member_key>"}')
    p.add_argument("--allow-partial", action="store_true", help="apply valid rows despite row errors")
    p.set_defaults(func=cmd_leave_import)

    p = sub.add_parser("leave-stats")
    p.add_argument("--member", required=True)
    p.add_argument("--as-of")
    p.set_defaults(func=cmd_leave_stats)

    p = sub.add_parser("backfill", help="evidence from tasks.db reviews + bugs.db (preview unless --apply)")
    p.add_argument("--tasks-db", default=str(team_ingest.TASKS_DB))
    p.add_argument("--bugs-db", default=str(team_ingest.BUGS_DB))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-merged", action="store_true",
                   help="mark fully merged review-task features confirmed (counts in metrics)")
    p.set_defaults(func=cmd_backfill)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # explicit surfacing for CLI consumers
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
