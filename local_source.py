"""Offline mail source that reads .eml files from a folder.

Lets you demo and test the pipeline without a Microsoft 365 tenant.
"""
from __future__ import annotations

import logging
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .graph_client import Email

log = logging.getLogger(__name__)


class LocalEmlSource:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def fetch_unread(self) -> list[Email]:
        emails = []
        for path in sorted(self.folder.glob("*.eml")):
            msg = BytesParser(policy=policy.default).parse(path.open("rb"))
            part = msg.get_body(preferencelist=("plain", "html"))
            body = part.get_content() if part else ""
            emails.append(Email(
                id=path.name,
                internet_message_id=msg.get("Message-ID", path.name),
                subject=msg.get("Subject", ""),
                sender=msg.get("From", ""),
                received=msg.get("Date", ""),
                body=body,
                body_is_html=bool(part and part.get_content_subtype() == "html"),
            ))
        log.info("Loaded %d .eml file(s) from %s", len(emails), self.folder)
        return emails

    def mark_processed(self, message_id: str, category: str) -> None:
        log.debug("(local) would tag %s as %s", message_id, category)
