"""Canonical serialisation + deterministic hashing, shared by all packages.

One implementation so that a hash computed in Controls, Schemas or CoA means
exactly the same thing everywhere.
"""
import hashlib
import json


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
