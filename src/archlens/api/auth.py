"""API-key authentication (SECURITY.md §7). Keys are compared in constant time; only their
fingerprint (`key_id`) is ever stored or logged."""

import hashlib
import hmac
from dataclasses import dataclass

from pydantic import SecretStr

HEADER = "X-API-Key"


def key_id(key: str) -> str:
    """Stable, non-reversible fingerprint of a key: 16 hex chars of its sha256."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ApiKeys:
    keys: tuple[str, ...]

    @classmethod
    def from_settings(cls, secrets: list[SecretStr]) -> "ApiKeys":
        """From `Settings.api_keys` (ARCHLENS_API_KEYS, comma-separated); empty keys dropped."""
        return cls(tuple(k for s in secrets if (k := s.get_secret_value().strip())))

    def authenticate(self, presented: str | None) -> str | None:
        """The key's fingerprint if `presented` is one of the keys, else None."""
        if not presented:
            return None
        matched = False
        for key in self.keys:  # no early exit: the time doesn't depend on which key matched
            matched |= hmac.compare_digest(presented.encode(), key.encode())
        return key_id(presented) if matched else None
