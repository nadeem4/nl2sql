from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> Any:
    """
    Try to extract the first JSON object from text.
    Falls back to json.loads of the whole string.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Naive extraction of first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


def strip_code_fences(text: str) -> str:
    """
    Remove markdown code fences from a string.
    """
    if text.startswith("```") and text.rstrip().endswith("```"):
        return re.sub(r"^```[a-zA-Z0-9]*\s*", "", text, flags=re.MULTILINE).rstrip("`").strip()
    return re.sub(r"```", "", text).strip()
