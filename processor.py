"""Glue: email -> parse -> ServiceNow change -> audit log."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .graph_client import Email
from .parser import ParseError, is_change_email, parse_email

log = logging.getLogger(__name__)

CAT_SUCCESS = "EMEC-Created"
CAT_DUPLICATE = "EMEC-Duplicate"
CAT_FAILED = "EMEC-Failed"


class ChangeSink(Protocol):
    def find_by_correlation_id(self, correlation_id: str) -> dict | None: ...
    def build_payload(self, cr, correlation_id: str, source_note: str) -> dict: ...
    def create_change(self, payload: dict) -> dict: ...


class MailSource(Protocol):
    def fetch_unread(self) -> list[Email]: ...
    def mark_processed(self, message_id: str, category: str) -> None: ...


@dataclass
class RunSummary:
    fetched: int = 0
    skipped: int = 0
    created: int = 0
    duplicates: int = 0
    failed: int = 0
    results: list[dict] = field(default_factory=list)


def correlation_id_for(email: Email) -> str:
    """Stable, short ID derived from the RFC 822 Message-ID (fits SN's 100-char field)."""
    digest = hashlib.sha256(email.internet_message_id.encode()).hexdigest()[:24]
    return f"emec-{digest}"


class AuditLog:
    """Append-only JSON Lines file: one record per processed email."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def process(source: MailSource, sink: ChangeSink, audit: AuditLog, *, mark: bool = True) -> RunSummary:
    summary = RunSummary()
    emails: Iterable[Email] = source.fetch_unread()
    for email in emails:
        summary.fetched += 1
        base = {"message_id": email.id, "subject": email.subject, "sender": email.sender}

        if not is_change_email(email.subject):
            summary.skipped += 1
            log.info("SKIP  %r (not a change request)", email.subject)
            audit.write({**base, "status": "skipped", "reason": "subject does not match"})
            continue  # leave unread: it's not ours

        corr = correlation_id_for(email)
        try:
            cr = parse_email(email.subject, email.body, body_is_html=email.body_is_html)

            existing = sink.find_by_correlation_id(corr)
            if existing:
                summary.duplicates += 1
                log.warning("DUP   %r already created as %s", email.subject, existing.get("number"))
                record = {**base, "status": "duplicate", "change_number": existing.get("number"), "correlation_id": corr}
                category = CAT_DUPLICATE
            else:
                note = f"Created by mini-emec from email {email.internet_message_id} sent by {email.sender} at {email.received}"
                payload = sink.build_payload(cr, corr, note)
                result = sink.create_change(payload)
                summary.created += 1
                log.info("OK    %r -> %s", email.subject, result.get("number"))
                record = {**base, "status": "created", "change_number": result.get("number"),
                          "sys_id": result.get("sys_id"), "correlation_id": corr}
                category = CAT_SUCCESS
        except ParseError as exc:
            summary.failed += 1
            log.error("FAIL  %r parse errors: %s", email.subject, exc.errors)
            record = {**base, "status": "failed", "stage": "parse", "errors": exc.errors}
            category = CAT_FAILED
        except Exception as exc:  # noqa: BLE001 - we log every failure and keep going
            summary.failed += 1
            log.error("FAIL  %r %s: %s", email.subject, type(exc).__name__, exc)
            record = {**base, "status": "failed", "stage": "servicenow", "errors": [str(exc)]}
            category = CAT_FAILED

        audit.write(record)
        summary.results.append(record)
        if mark:
            try:
                source.mark_processed(email.id, category)
            except Exception as exc:  # noqa: BLE001
                log.error("Could not mark message %s as processed: %s", email.id, exc)

    log.info("Run complete: fetched=%d created=%d duplicates=%d failed=%d skipped=%d",
             summary.fetched, summary.created, summary.duplicates, summary.failed, summary.skipped)
    return summary
