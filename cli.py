"""Command-line entry point.

Examples:
    python -m mini_emec --source local --dry-run      # no cloud accounts needed
    python -m mini_emec --source local                # .eml files -> real ServiceNow PDI
    python -m mini_emec                               # Graph mailbox -> ServiceNow PDI
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import ConfigError, GraphConfig, ServiceNowConfig
from .processor import AuditLog, process
from .servicenow_client import IMPACT_VALUES, RISK_VALUES, SN_DATE_FMT


class DryRunSink:
    """Prints the payload that WOULD be sent to ServiceNow. Makes no network calls."""

    def __init__(self):
        self._n = 0

    def find_by_correlation_id(self, correlation_id):
        return None

    def build_payload(self, cr, correlation_id, source_note):
        return {
            "short_description": cr.short_description,
            "type": cr.type,
            "assignment_group": f"<lookup: {cr.assignment_group}>",
            "risk": RISK_VALUES[cr.risk],
            "impact": IMPACT_VALUES[cr.impact],
            "start_date": cr.start_date.strftime(SN_DATE_FMT),
            "end_date": cr.end_date.strftime(SN_DATE_FMT),
            "category": cr.category,
            "correlation_id": correlation_id,
            "description": (cr.description + "\n\n" + source_note).strip(),
        }

    def create_change(self, payload):
        self._n += 1
        print(json.dumps(payload, indent=2))
        return {"number": f"DRYRUN{self._n:04d}", "sys_id": None}


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mini-emec", description="Email -> ServiceNow change request automation")
    ap.add_argument("--source", choices=["graph", "local"], default="graph", help="where to read emails from")
    ap.add_argument("--eml-dir", default="samples", help="folder of .eml files for --source local")
    ap.add_argument("--dry-run", action="store_true", help="parse and print payloads, don't call ServiceNow")
    ap.add_argument("--no-mark", action="store_true", help="don't mark emails as read / categorize them")
    ap.add_argument("--audit-log", default="logs/audit.jsonl", help="JSON Lines audit file")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(args.verbose)
    log = logging.getLogger("mini_emec")

    try:
        if args.source == "local":
            from .local_source import LocalEmlSource
            source = LocalEmlSource(Path(args.eml_dir))
        else:
            from .graph_client import GraphMailClient
            source = GraphMailClient(GraphConfig.from_env())

        if args.dry_run:
            sink = DryRunSink()
        else:
            from .servicenow_client import ServiceNowClient
            sink = ServiceNowClient(ServiceNowConfig.from_env())
    except ConfigError as exc:
        log.error("%s (see .env.example)", exc)
        return 2

    summary = process(source, sink, AuditLog(Path(args.audit_log)), mark=not (args.no_mark or args.dry_run))
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
