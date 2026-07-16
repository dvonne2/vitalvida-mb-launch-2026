"""`vitalvida-event-schema-1` — a deliberately constrained schema dialect.

Chosen over a full JSON Schema dependency: enough to be real validation, small
enough to audit in one sitting. Supported keywords ONLY:

    type                 object|array|string|number|integer|boolean|null
    properties           nested schemas
    required             list of required keys
    additionalProperties bool (default False for objects — strict by default)
    items                schema for array elements
    enum                 allowed values
    minLength/maxLength  strings
    minimum/maximum      numbers
    minItems/maxItems    arrays

Anything else in a schema is REJECTED at definition time, so a schema can never
silently mean less than its author thought (`$ref`, allOf/anyOf, conditionals
are unsupported by design — if VitalVida needs them, adopt a real library then).
"""
# Re-exported so callers use ONE canonical hashing implementation across
# Controls, Schemas and CoA (validation.py uses validator.canonical/stable_hash).
from vitalvida.governance.hashing import canonical, stable_hash  # noqa: F401

ALLOWED_KEYWORDS = {"type", "properties", "required", "additionalProperties",
                    "items", "enum", "minLength", "maxLength", "minimum",
                    "maximum", "minItems", "maxItems", "description"}
ALLOWED_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}

DIALECT = "vitalvida-event-schema-1"


# ------------------------------------------------------------ schema check
def validate_schema_definition(schema, path="$"):
    """Reject anything outside the dialect. Returns list of problems."""
    errs = []
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    unknown = set(schema) - ALLOWED_KEYWORDS
    if unknown:
        errs.append(f"{path}: unsupported keyword(s) {sorted(unknown)}; "
                    f"dialect {DIALECT} supports {sorted(ALLOWED_KEYWORDS)}")
    t = schema.get("type")
    if t is None:
        errs.append(f"{path}: 'type' is required")
    elif t not in ALLOWED_TYPES:
        errs.append(f"{path}: unsupported type {t!r}")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            errs.append(f"{path}.properties: must be an object")
        else:
            for k, sub in schema["properties"].items():
                errs += validate_schema_definition(sub, f"{path}.{k}")
    if "items" in schema:
        errs += validate_schema_definition(schema["items"], f"{path}[]")
    if "required" in schema and not isinstance(schema["required"], list):
        errs.append(f"{path}.required: must be a list")
    if "enum" in schema and not isinstance(schema["enum"], list):
        errs.append(f"{path}.enum: must be a list")
    return errs


# ----------------------------------------------------------- payload check
def _type_ok(value, t):
    if t == "object":  return isinstance(value, dict)
    if t == "array":   return isinstance(value, list)
    if t == "string":  return isinstance(value, str)
    if t == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":  return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean": return isinstance(value, bool)
    if t == "null":    return value is None
    return False


def validate_payload(schema, payload, path="$"):
    """Validate payload against a dialect schema. Returns list of errors."""
    errs = []
    t = schema.get("type")
    if not _type_ok(payload, t):
        return [f"{path}: expected {t}, got {type(payload).__name__}"]

    if "enum" in schema and payload not in schema["enum"]:
        errs.append(f"{path}: {payload!r} not in enum {schema['enum']}")

    if t == "string":
        if "minLength" in schema and len(payload) < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(payload) > schema["maxLength"]:
            errs.append(f"{path}: longer than maxLength {schema['maxLength']}")

    if t in ("number", "integer"):
        if "minimum" in schema and payload < schema["minimum"]:
            errs.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and payload > schema["maximum"]:
            errs.append(f"{path}: above maximum {schema['maximum']}")

    if t == "array":
        if "minItems" in schema and len(payload) < schema["minItems"]:
            errs.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(payload) > schema["maxItems"]:
            errs.append(f"{path}: more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for i, item in enumerate(payload):
                errs += validate_payload(schema["items"], item, f"{path}[{i}]")

    if t == "object":
        props = schema.get("properties") or {}
        for key in schema.get("required", []):
            if key not in payload:
                errs.append(f"{path}.{key}: required field missing")
        # strict by default: unknown keys are an error unless explicitly allowed
        if not schema.get("additionalProperties", False):
            extra = set(payload) - set(props)
            if extra:
                errs.append(f"{path}: unexpected field(s) {sorted(extra)} "
                            "(additionalProperties is false)")
        for key, sub in props.items():
            if key in payload:
                errs += validate_payload(sub, payload[key], f"{path}.{key}")
    return errs
