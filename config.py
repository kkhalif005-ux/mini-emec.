"""Configuration loaded from environment variables (or a local .env file).

Nothing secret is ever hard-coded. Copy .env.example to .env and fill in
values for YOUR OWN test tenant and ServiceNow developer instance.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class GraphConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str
    folder: str = "inbox"
    max_messages: int = 25

    @classmethod
    def from_env(cls) -> "GraphConfig":
        load_dotenv()
        return cls(
            tenant_id=_require("GRAPH_TENANT_ID"),
            client_id=_require("GRAPH_CLIENT_ID"),
            client_secret=_require("GRAPH_CLIENT_SECRET"),
            mailbox=_require("GRAPH_MAILBOX"),
            folder=os.getenv("GRAPH_FOLDER", "inbox"),
            max_messages=int(os.getenv("GRAPH_MAX_MESSAGES", "25")),
        )


@dataclass(frozen=True)
class ServiceNowConfig:
    instance: str  # e.g. "dev123456" -> https://dev123456.service-now.com
    username: str
    password: str
    timeout: int = 30

    @property
    def base_url(self) -> str:
        if self.instance.startswith("http"):
            return self.instance.rstrip("/")
        return f"https://{self.instance}.service-now.com"

    @classmethod
    def from_env(cls) -> "ServiceNowConfig":
        load_dotenv()
        return cls(
            instance=_require("SN_INSTANCE"),
            username=_require("SN_USERNAME"),
            password=_require("SN_PASSWORD"),
            timeout=int(os.getenv("SN_TIMEOUT", "30")),
        )
