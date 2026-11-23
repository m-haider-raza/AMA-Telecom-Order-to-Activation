"""Verified Entra application identity, never caller-supplied agent names."""

import json
import os
from dataclasses import dataclass
from uuid import UUID

import jwt


@dataclass(frozen=True)
class Principal:
    oid: str
    agent: str
    client_id: str


class EntraVerifier:
    def __init__(self, tenant, audience, principals, key_provider=None):
        UUID(tenant)
        if set(principals) != {"supervisor", "validation", "decomposition", "provisioning",
                               "fallout", "customer_update"}:
            raise ValueError("Exactly six distinct runtime principal bindings are required")
        ids = [entry["oid"] for entry in principals.values()]
        clients = [entry["clientId"] for entry in principals.values()]
        if len(set(ids)) != 6 or len(set(clients)) != 6:
            raise ValueError("Shared runtime credentials/principals are prohibited")
        for value in ids + clients:
            UUID(value)
        self.tenant, self.audience, self.principals = tenant, audience, principals
        self.issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
        self.keys = key_provider or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", cache_keys=True)

    @classmethod
    def from_env(cls):
        return cls(os.environ["ISOLATED_TENANT_ID"], os.environ["ISOLATED_AUDIENCE"],
                   json.loads(os.environ["ISOLATED_PRINCIPALS"]))

    def verify(self, token):
        key = self.keys.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience,
                            issuer=self.issuer, leeway=0,
                            options={"require": ["exp", "iat", "nbf", "iss", "aud", "oid", "tid", "azp"]})
        if claims["tid"] != self.tenant or claims.get("ver") != "2.0" or claims.get("scp"):
            raise jwt.InvalidTokenError("Only tenant-bound application tokens are allowed")
        for agent, entry in self.principals.items():
            if claims["oid"] == entry["oid"] and claims["azp"] == entry["clientId"]:
                if claims.get("roles") != [f"Worker.{agent}"]:
                    raise jwt.InvalidTokenError("Application role does not match principal binding")
                return Principal(entry["oid"], agent, entry["clientId"])
        raise jwt.InvalidTokenError("Unregistered application principal")
