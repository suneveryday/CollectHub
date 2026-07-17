from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import default_output
from .doctor import run_doctor
from .notion import NotionClient, NotionError
from .service import ingest_urls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="content-ingestor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest = subparsers.add_parser(
        "ingest", help="Route supported platform links into the local CollectHub library"
    )
    ingest.add_argument("urls", nargs="+", help="One or more supported single-content or public webpage URLs")
    ingest.add_argument("--output", type=Path, default=default_output())
    ingest.add_argument(
        "--force", action="store_true", help="Refresh the local item and any requested sync target"
    )
    ingest.add_argument(
        "--sync", choices=("notion",),
        help="Explicitly sync locally saved items to Notion",
    )
    doctor = subparsers.add_parser(
        "doctor", help="Check local platform runtimes and optional sync targets"
    )
    doctor.add_argument(
        "--sync", choices=("notion",), help="Also check the requested sync target"
    )
    schema = subparsers.add_parser(
        "notion-schema", help="Preview or apply the required Notion database schema"
    )
    schema.add_argument("--apply", action="store_true", help="Add missing properties to the configured data source")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        payload = run_doctor(sync=args.sync)
    elif args.command == "notion-schema":
        client: NotionClient | None = None
        try:
            client = NotionClient()
            before = client.schema_report()
            after = client.apply_schema() if args.apply else before
            payload = {
                "ok": after["ok"] if args.apply else True,
                "dry_run": not args.apply,
                "before": before,
                "after": after,
            }
        except NotionError as exc:
            payload = {"ok": False, "error": {"stage": exc.stage, "code": exc.code, "message": str(exc)}}
        finally:
            if client is not None:
                client.close()
    else:
        results = ingest_urls(
            args.urls,
            args.output,
            force=args.force,
            sync=args.sync,
        )
        payload = {
            "ok": all(result["status"] != "failed" for result in results),
            "sync_ok": all(
                result.get("sync", {}).get("notion", {}).get("status") != "failed"
                for result in results
            ),
            "results": results,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
