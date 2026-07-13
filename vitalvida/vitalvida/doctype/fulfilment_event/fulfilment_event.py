from vitalvida.domain.immutable_event import ImmutableEventDocument


class FulfilmentEvent(ImmutableEventDocument):
    """Immutable domain event record (GOV-004); see domain.immutable_event."""
    PROTECTED_EXEMPT = {"status", "consequence_doctype", "consequence_name", "consequence_posted"}
