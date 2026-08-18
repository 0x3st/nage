import json
from typing import Any, Dict, Mapping, Union

from pydantic import ValidationError

from ..errors import ProtocolError
from .models import AssistantDecision

MAX_DECISION_BYTES = 1024 * 1024


def parse_assistant_decision(
    payload: Union[str, bytes, Mapping[str, Any]],
    max_bytes: int = MAX_DECISION_BYTES,
) -> AssistantDecision:
    if max_bytes <= 0:
        raise ProtocolError("Protocol response size limit must be positive.")

    if isinstance(payload, bytes):
        if len(payload) > max_bytes:
            raise ProtocolError("Provider response exceeds the protocol size limit.")
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("Provider response is not valid UTF-8.") from exc
        data = _load_json_object(decoded)
    elif isinstance(payload, str):
        try:
            payload_size = len(payload.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ProtocolError("Provider response is not valid Unicode.") from exc
        if payload_size > max_bytes:
            raise ProtocolError("Provider response exceeds the protocol size limit.")
        data = _load_json_object(payload)
    elif isinstance(payload, Mapping):
        data = dict(payload)
    else:
        raise ProtocolError("Provider response must be JSON text or an object.")

    try:
        return AssistantDecision.model_validate(data)
    except ValidationError as exc:
        raise ProtocolError(_validation_summary(exc)) from exc


def decision_json_schema() -> Dict[str, Any]:
    return AssistantDecision.model_json_schema()


def _load_json_object(payload: str) -> Dict[str, Any]:
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("Provider response is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ProtocolError("Provider response root must be a JSON object.")
    return data


def _validation_summary(error: ValidationError) -> str:
    summaries = []
    for item in error.errors(include_input=False)[:5]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        summaries.append("{0}: {1}".format(location, item.get("msg", "invalid")))
    detail = "; ".join(summaries) or "response does not match protocol"
    return "Provider response failed protocol validation: {0}".format(detail)
