"""Minimal Microsoft Graph mail client (app-only / client-credentials flow)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import msal
import requests

from .config import GraphConfig

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = ["https://graph.microsoft.com/.default"]


class GraphError(RuntimeError):
    pass


@dataclass
class Email:
    id: str
    internet_message_id: str
    subject: str
    sender: str
    received: str
    body: str
    body_is_html: bool


class GraphMailClient:
    def __init__(self, cfg: GraphConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self._app: msal.ConfidentialClientApplication | None = None  # created lazily on first use

    # --- auth -------------------------------------------------------------
    def _token(self) -> str:
        if self._app is None:
            self._app = msal.ConfidentialClientApplication(
                client_id=self.cfg.client_id,
                client_credential=self.cfg.client_secret,
                authority=f"https://login.microsoftonline.com/{self.cfg.tenant_id}",
            )
        result = self._app.acquire_token_for_client(scopes=SCOPE)  # MSAL caches tokens
        if "access_token" not in result:
            raise GraphError(f"Token request failed: {result.get('error')}: {result.get('error_description')}")
        return result["access_token"]

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token()}"
        resp = self.session.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise GraphError(f"Graph {method} {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp

    # --- mail -------------------------------------------------------------
    def _msg_url(self, message_id: str = "") -> str:
        base = f"{GRAPH}/users/{self.cfg.mailbox}"
        return f"{base}/messages/{message_id}" if message_id else f"{base}/mailFolders/{self.cfg.folder}/messages"

    def fetch_unread(self) -> list[Email]:
        params = {
            "$filter": "isRead eq false",
            "$top": str(self.cfg.max_messages),
            "$select": "id,internetMessageId,subject,from,receivedDateTime,body",
            "$orderby": "receivedDateTime asc",
        }
        # Ask Graph to give us plain text instead of HTML where possible.
        headers = {"Prefer": 'outlook.body-content-type="text"'}
        data = self._request("GET", self._msg_url(), params=params, headers=headers).json()
        emails = []
        for m in data.get("value", []):
            body = m.get("body") or {}
            emails.append(Email(
                id=m["id"],
                internet_message_id=m.get("internetMessageId") or m["id"],
                subject=m.get("subject") or "",
                sender=((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                received=m.get("receivedDateTime", ""),
                body=body.get("content", ""),
                body_is_html=(body.get("contentType", "text").lower() == "html"),
            ))
        log.info("Fetched %d unread message(s) from %s/%s", len(emails), self.cfg.mailbox, self.cfg.folder)
        return emails

    def mark_processed(self, message_id: str, category: str) -> None:
        """Mark read and tag with an Outlook category so you can see the outcome in the mailbox."""
        self._request("PATCH", self._msg_url(message_id), json={"isRead": True, "categories": [category]})
