from vitalvida.domain.immutable_event import ImmutableEventDocument


class DeliveryAttemptEvent(ImmutableEventDocument):
    """Immutable domain event record (GOV-004); see domain.immutable_event."""
    PROTECTED_EXEMPT = set()
