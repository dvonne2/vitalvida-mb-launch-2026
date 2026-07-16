"""VitalVida shared integration utilities (Package 01).

Reusable infrastructure only: idempotency + duplicate-race recovery,
consequence linking, an authoritative-owner registry reader, and an async
outbox. None of these create or mirror event truth; they operate ON the
authoritative records the domain already owns.
"""
