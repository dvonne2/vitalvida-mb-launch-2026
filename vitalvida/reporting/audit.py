"""Read-only audit projections over immutable domain events (no writes)."""


def control_exceptions_open(limit=100):
    """Derived: an exception is Open iff no Control Resolution Event refers to it."""
    from vitalvida.controls.engine import open_exceptions
    return open_exceptions(limit)
