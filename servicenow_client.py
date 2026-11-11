"""Minimal ServiceNow Table API client for creating change requests."""
from __future__ import annotations

import logging
from functools import lru_cache

import requests

from .config import ServiceNowConfig
from .parser import ChangeRequest

log = logging.getLogger(__name__)

# ServiceNow stores these choice fields as numbers.
RISK_VALUES = {"high": "2", "moderate": "3", "low": "4"}
IMPACT_VALUES = {"high": "1", "medium": "2", "low": "3"}
SN_DATE_FMT = "%Y-%m-%d %H:%M:%S"


class ServiceNowError(RuntimeError):
    pass


class ServiceNowClient:
    def __init__(self, cfg: ServiceNowConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.auth = (cfg.username, cfg.password)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        # Cache group lookups per client instance.
        self.lookup_group_sys_id = lru_cache(maxsize=128)(self._lookup_group_sys_id)

    def _url(self, table: str) -> str:
        return f"{self.cfg.base_url}/api/now/table/{table}"

    def _request(self, method: str, url: str, **kwargs) -> dict:
        resp = self.session.request(method, url, timeout=self.cfg.timeout, **kwargs)
        if resp.status_code >= 400:
            raise ServiceNowError(f"ServiceNow {method} {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _lookup_group_sys_id(self, name: str) -> str:
        data = self._request("GET", self._url("sys_user_group"), params={
            "sysparm_query": f"name={name}^active=true",
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": "1",
        })
        results = data.get("result", [])
        if not results:
            raise ServiceNowError(f"Assignment group not found in ServiceNow: {name!r}")
        return results[0]["sys_id"]

    def find_by_correlation_id(self, correlation_id: str) -> dict | None:
        """Idempotency check: has this email already produced a change?"""
        data = self._request("GET", self._url("change_request"), params={
            "sysparm_query": f"correlation_id={correlation_id}",
            "sysparm_fields": "sys_id,number",
            "sysparm_limit": "1",
        })
        results = data.get("result", [])
        return results[0] if results else None

    def build_payload(self, cr: ChangeRequest, correlation_id: str, source_note: str) -> dict:
        description_parts = [cr.description.strip()] if cr.description else []
        if cr.requested_by:
            description_parts.append(f"Requested by: {cr.requested_by}")
        description_parts.append(source_note)

        payload = {
            "short_description": cr.short_description,
            "description": "\n\n".join(description_parts),
            "type": cr.type,
            "assignment_group": self.lookup_group_sys_id(cr.assignment_group),
            "risk": RISK_VALUES[cr.risk],
            "impact": IMPACT_VALUES[cr.impact],
            "start_date": cr.start_date.strftime(SN_DATE_FMT),
            "end_date": cr.end_date.strftime(SN_DATE_FMT),
            "correlation_id": correlation_id,
            "correlation_display": "mini-emec",
        }
        optional = {
            "category": cr.category.lower() if cr.category else None,
            "justification": cr.justification,
            "implementation_plan": cr.implementation_plan,
            "backout_plan": cr.backout_plan,
            "test_plan": cr.test_plan,
        }
        payload.update({k: v for k, v in optional.items() if v})
        return payload

    def create_change(self, payload: dict) -> dict:
        data = self._request("POST", self._url("change_request"), json=payload)
        result = data["result"]
        log.info("Created %s (sys_id=%s)", result.get("number"), result.get("sys_id"))
        return result
