import json
from typing import Any

def estimate_size_bytes(data: Any) -> int:
    """Estimates the memory/payload size of a Python object (List/Dict) in bytes.
    
    Uses JSON serialization length (UTF-8) as a robust proxy for payload size.
    This effectively measures "how large is this data when transmitted/serialized".
    
    Args:
        data: The object to measure (typically a list of dicts).
        
    Returns:
        int: Estimated size in bytes.
    """
    try:
        # standard separators=(',', ':') removes whitespace for tighter, more realistic API payload estimation
        # We encode to utf-8 to get actual byte count, though for pure string length it's close enough.
        return len(json.dumps(data, separators=(',', ':')).encode('utf-8'))
    except TypeError:
        # Fallback for non-serializable objects (e.g. datetimes not handled by default json encoder?)
        # For safety, we just stringify.
        return len(str(data).encode('utf-8'))
