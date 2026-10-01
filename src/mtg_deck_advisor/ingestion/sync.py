"""Deciding what an ingestion run changes, by comparing content hashes (ING-3, ING-4).

Pure functions, shared by every source (cards, rules). The database code that
applies a Diff lives with each source. See ADR 0006.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

from pydantic import BaseModel


class Existing(NamedTuple):
    """What the database already holds for one key."""

    content_hash: str
    removed: bool


class IngestionReport(BaseModel):
    """The counts ING-4 asks for. A record that returns after removal counts as added."""

    added: int
    updated: int
    unchanged: int
    removed: int


@dataclass(frozen=True)
class Diff[K]:
    added: list[K] = field(default_factory=list)
    # Previously removed, now back. Applied like an update (the row exists),
    # reported as added (from the data's point of view it is new again).
    restored: list[K] = field(default_factory=list)
    updated: list[K] = field(default_factory=list)
    unchanged: list[K] = field(default_factory=list)
    removed: list[K] = field(default_factory=list)

    def report(self) -> IngestionReport:
        return IngestionReport(
            added=len(self.added) + len(self.restored),
            updated=len(self.updated),
            unchanged=len(self.unchanged),
            removed=len(self.removed),
        )


def diff[K](existing: Mapping[K, Existing], incoming: Mapping[K, str]) -> Diff[K]:
    """Classify every key, given the stored state and the incoming content hashes."""
    result: Diff[K] = Diff()
    for key, incoming_hash in incoming.items():
        stored = existing.get(key)
        if stored is None:
            result.added.append(key)
        elif stored.removed:
            result.restored.append(key)
        elif stored.content_hash == incoming_hash:
            result.unchanged.append(key)
        else:
            result.updated.append(key)
    for key, stored in existing.items():
        if key not in incoming and not stored.removed:
            result.removed.append(key)
    return result
