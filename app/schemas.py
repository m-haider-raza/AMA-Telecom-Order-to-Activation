"""Request models and the transaction helper shared by the staff API and the customer-accounts router."""

import json
import logging
import sys
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain import live_mode


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contactName: str = Field(min_length=2, max_length=80)
    email: str | None = Field(default=None, max_length=120, pattern=r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
    phone: str | None = Field(default=None, pattern=r"^\+[1-9][0-9]{7,14}$")
    preferredChannel: Literal["email", "sms"]
    updateFrequency: Literal["every-step", "daily", "weekly", "critical-only"]

    @model_validator(mode="after")
    def channel_has_address(self):
        if (self.email if self.preferredChannel == "email" else self.phone) is None:
            raise ValueError("Add an email address to receive updates by email." if self.preferredChannel == "email"
                             else "Add a mobile number to receive updates by SMS.")
        return self


def check_customer(value):
    if not live_mode() and not (len(value) >= 5 and value.startswith("Demo ")):
        raise ValueError("Demo mode only accepts company names that begin with 'Demo '")
    return value.strip()


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer: str = Field(min_length=2, max_length=80)
    city: Literal["Istanbul", "Ankara", "Izmir"]
    product: Literal["connectivity", "sdwan", "cloud"]
    scenario: Literal["happy", "auto-recovery", "human-exception", "change-window", "address-correction"] = "happy"
    language: Literal["tr", "en"] = "tr"
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    siteReference: Literal["IST-001", "ANK-001", "IZM-001", "DEMO-UNKNOWN"] | None = None
    siteAddress: str | None = Field(default=None, min_length=5, max_length=200)
    contact: Contact | None = None

    @field_validator("customer")
    @classmethod
    def demo_names(cls, value):
        return check_customer(value)


class PortalOrder(BaseModel):
    """What a customer may choose themselves: no simulator scenarios, a contact is mandatory."""
    model_config = ConfigDict(extra="forbid")
    customer: str = Field(min_length=2, max_length=80)
    city: Literal["Istanbul", "Ankara", "Izmir"]
    product: Literal["connectivity", "sdwan", "cloud"]
    language: Literal["tr", "en"] = "tr"
    idempotencyKey: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    siteAddress: str = Field(min_length=5, max_length=200)
    contact: Contact

    @field_validator("customer")
    @classmethod
    def demo_names(cls, value):
        return check_customer(value)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


class SiteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    siteReference: Literal["IST-001", "ANK-001", "IZM-001"]


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: Literal[50, 100] = 100


STAFF_LOG = logging.getLogger("ama.audit")
LOGGED_FIELDS = ("sequence", "at", "orderId", "actor", "action", "detail", "by", "changes", "hash")


def configure_staff_log():
    """Send the staff audit lines to the container log as bare JSON, one per line, so they can be queried as such."""
    if not STAFF_LOG.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        STAFF_LOG.addHandler(handler)
    STAFF_LOG.setLevel(logging.INFO)
    STAFF_LOG.propagate = False


def log_staff_actions(entries):
    """A second, independent copy of what each signed-in staff member did, written only after the change is saved."""
    for entry in entries:
        if entry.get("by"):
            line = {"log": "staff-audit", **{key: entry[key] for key in LOGGED_FIELDS if key in entry}}
            STAFF_LOG.info(json.dumps(line, ensure_ascii=False, separators=(",", ":")))


def transact(app, action):
    added = []

    def run(state):
        start = len(state["audit"])
        result = action(state)
        added[:] = state["audit"][start:]  # replaced on every retry, so only the committed attempt is logged
        return result

    try:
        result = app.state.store.transact(run)
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    log_staff_actions(added)
    return result
