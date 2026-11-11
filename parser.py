"""Regex-based parser that turns a templated email into a change request.

Expected email format (see samples/ for full examples):

    Subject: [CHANGE REQUEST] Patch web servers

    Short Description: Apply October OS patches to WEB01-WEB03
    Type: Normal
    Assignment Group: Network
    Category: Software
    Risk: Moderate
    Impact: Medium
    Planned Start: 2026-11-20 22:00
    Planned End: 2026-11-21 02:00
    Requested By: test.user@contoso-test.example
    Description:
    Multi-line description goes here.
    It runs until the next "Key:" line or the end of the email.
    Backout Plan:
    Roll back via snapshot.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

SUBJECT_RE = re.compile(r"^\s*(?:(?:RE|FW|FWD):\s*)*\[?\s*CHANGE(?:\s+REQUEST)?\s*\]?", re.IGNORECASE)

# Field label -> internal key. Labels are matched case-insensitively at the
# start of a line, followed by a colon.
FIELD_LABELS = {
    "short description": "short_description",
    "type": "type",
    "assignment group": "assignment_group",
    "category": "category",
    "risk": "risk",
    "impact": "impact",
    "planned start": "start_date",
    "planned end": "end_date",
    "requested by": "requested_by",
    "description": "description",
    "justification": "justification",
    "implementation plan": "implementation_plan",
    "backout plan": "backout_plan",
    "test plan": "test_plan",
}

# One regex that finds every "Label:" at the start of a line.
_LABEL_RE = re.compile(
    r"^[ \t]*(?P<label>" + "|".join(re.escape(k) for k in sorted(FIELD_LABELS, key=len, reverse=True)) + r")[ \t]*:[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)

DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")

REQUIRED = ("short_description", "assignment_group", "start_date", "end_date")

ALLOWED = {
    "type": {"normal", "standard", "emergency"},
    "risk": {"high", "moderate", "low"},
    "impact": {"high", "medium", "low"},
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ParseError(ValueError):
    """Raised when an email cannot be turned into a valid change request."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class ChangeRequest:
    short_description: str
    assignment_group: str
    start_date: datetime
    end_date: datetime
    type: str = "normal"
    category: str | None = None
    risk: str = "moderate"
    impact: str = "medium"
    requested_by: str | None = None
    description: str = ""
    justification: str | None = None
    implementation_plan: str | None = None
    backout_plan: str | None = None
    test_plan: str | None = None
    extra: dict = field(default_factory=dict)


def is_change_email(subject: str | None) -> bool:
    return bool(subject and SUBJECT_RE.match(subject))


def html_to_text(body: str) -> str:
    """Very small HTML-to-text fallback (Graph can also return text directly)."""
    body = re.sub(r"(?is)<(script|style).*?</\1>", "", body)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body)
    return html.unescape(body)


def _clean(value: str) -> str:
    # Strip trailing whitespace on each line and drop leading/trailing blank lines.
    lines = [ln.rstrip() for ln in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def extract_fields(body: str) -> dict[str, str]:
    """Split the body into {key: value} using the label regex.

    Each value runs from the end of its label to the start of the next label,
    which lets Description / plans span multiple lines.
    """
    matches = list(_LABEL_RE.finditer(body))
    fields: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        key = FIELD_LABELS[m.group("label").lower()]
        value = _clean(body[start:end])
        # Cut off common signature / reply markers from the final field.
        value = re.split(r"\n(?:--\s*$|-{2,}\s*Original Message|From:\s)", value, maxsplit=1, flags=re.M)[0].strip()
        if key not in fields:  # first occurrence wins (ignores quoted replies)
            fields[key] = value
    return fields


def _parse_date(value: str) -> datetime | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_email(subject: str, body: str, *, body_is_html: bool = False) -> ChangeRequest:
    """Parse and validate. Raises ParseError listing every problem found."""
    if not is_change_email(subject):
        raise ParseError([f"Subject does not look like a change request: {subject!r}"])

    text = html_to_text(body) if body_is_html else body
    raw = extract_fields(text)
    errors: list[str] = []

    for key in REQUIRED:
        if not raw.get(key):
            errors.append(f"Missing required field: {key}")

    single_line = {k: v.splitlines()[0].strip() if v else v for k, v in raw.items()
                   if k not in {"description", "justification", "implementation_plan", "backout_plan", "test_plan"}}

    for key, allowed in ALLOWED.items():
        if raw.get(key) and single_line[key].lower() not in allowed:
            errors.append(f"Invalid {key} {single_line[key]!r}; expected one of {sorted(allowed)}")

    start = _parse_date(single_line["start_date"]) if raw.get("start_date") else None
    end = _parse_date(single_line["end_date"]) if raw.get("end_date") else None
    if raw.get("start_date") and start is None:
        errors.append(f"Planned Start is not a valid date (use YYYY-MM-DD HH:MM): {single_line['start_date']!r}")
    if raw.get("end_date") and end is None:
        errors.append(f"Planned End is not a valid date (use YYYY-MM-DD HH:MM): {single_line['end_date']!r}")
    if start and end and end <= start:
        errors.append("Planned End must be after Planned Start")

    if raw.get("requested_by") and not EMAIL_RE.match(single_line["requested_by"]):
        errors.append(f"Requested By is not an email address: {single_line['requested_by']!r}")

    if raw.get("short_description") and len(single_line["short_description"]) > 160:
        errors.append("Short Description must be 160 characters or fewer")

    if errors:
        raise ParseError(errors)

    return ChangeRequest(
        short_description=single_line["short_description"],
        assignment_group=single_line["assignment_group"],
        start_date=start,  # type: ignore[arg-type]
        end_date=end,  # type: ignore[arg-type]
        type=single_line.get("type", "normal").lower() or "normal",
        category=single_line.get("category") or None,
        risk=(single_line.get("risk") or "moderate").lower(),
        impact=(single_line.get("impact") or "medium").lower(),
        requested_by=single_line.get("requested_by") or None,
        description=raw.get("description", ""),
        justification=raw.get("justification"),
        implementation_plan=raw.get("implementation_plan"),
        backout_plan=raw.get("backout_plan"),
        test_plan=raw.get("test_plan"),
    )
