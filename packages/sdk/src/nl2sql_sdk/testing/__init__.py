"""The adapter conformance kit: ``pip install "nl2sql-sdk[testing]"``.

``AdapterConformance`` is a pytest test class an adapter author subclasses,
giving it the adapter and a database to seed; it then checks what every
adapter promises. Unlike the rest of the SDK, this subpackage imports pytest
and sqlglot.
"""

import pytest

# The kit's asserts are in this package, not in a test module: have pytest
# rewrite them, so a failure shows the values.
pytest.register_assert_rewrite("nl2sql_sdk.testing.kit")

from .kit import AdapterConformance  # noqa: E402

__all__ = ["AdapterConformance"]
