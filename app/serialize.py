from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Recursively converts Stat dataclasses (and Confidence enums) into
    plain JSON-safe dicts, leaving everything else untouched."""
    if is_dataclass(obj) and not isinstance(obj, type):
        d = asdict(obj)
        return {k: to_jsonable(v) for k, v in d.items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj
