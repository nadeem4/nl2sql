from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Any, Optional, Set
from threading import RLock

from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql.common.logger import get_logger
from nl2sql.datasources.protocols import DatasourceAdapterProtocol
from nl2sql.registry import Registry
from nl2sql.secrets import SecretManager
from .models import SECRET_ARG_HINTS, _is_secret_arg  # noqa: F401

if TYPE_CHECKING:
    from nl2sql.config import Datasource

logger = get_logger(__name__)

# Resolved connection args hold plaintext secrets (see ``resolved_connection``),
# so anything that renders them -- an API payload, a `doctor` row, a pydantic
# ValidationError repr -- has to mask them by name first.


def mask_connection_args(connection_args: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of the connection args with secret values masked."""
    return {
        key: ("***" if _is_secret_arg(key) and value else value)
        for key, value in (connection_args or {}).items()
    }


def redact_connection_secrets(text: str, connection_args: Dict[str, Any]) -> str:
    """Replaces every secret value from the connection args found in ``text``."""
    for key, value in (connection_args or {}).items():
        if _is_secret_arg(key) and isinstance(value, str) and value:
            text = text.replace(value, "***")
    return text


class DatasourceRegistry:
    """The adapters one engine built, one per datasource, and which of them are available.

    Building an adapter never connects. ``mark_unavailable`` (called by
    ``engine.check()`` when a datasource cannot connect) keeps it out of
    ``list_ids`` and routing, and anything that targets it fails with the
    reason; ``mark_available`` brings it back.
    """

    def __init__(self, secret_manager: SecretManager, registry: Optional[Registry] = None,
                 base_dir: Optional[Path] = None):
        """Starts empty. ``registry`` turns a datasource ``type`` into an adapter class.

        ``registry`` defaults to ``Registry.default()``: the built-in and
        installed adapters. ``base_dir`` is the folder of the ``nl2sql.yaml``
        the datasources came from; each adapter is given it, so a relative
        file path in a connection (a SQLite or DuckDB file) is read from there.
        None means the working directory.
        """
        self._adapters: Dict[str, DatasourceAdapterProtocol] = {}
        self._datasources: Dict[str, "Datasource"] = {}
        self._capabilities: Dict[str, Set[str]] = {}
        self._unavailable: Dict[str, str] = {}
        self._registry = registry or Registry.default()
        self._secret_manager = secret_manager
        self._base_dir = base_dir
        self._lock = RLock()

    @property
    def secret_manager(self) -> SecretManager:
        return self._secret_manager

    def resolved_connection(self, connection: Dict[str, Any]) -> Dict[str, Any]:
        """``connection`` with every ``${provider:key}`` value resolved through the secret manager.

        Values are returned as plain strings. Each adapter's own config model
        declares which fields are secret and pydantic coerces the plain string
        into ``SecretStr`` for those, so masking is preserved exactly where it
        is declared.
        """
        return {
            key: self._secret_manager.resolve(value)
            if isinstance(value, str) and value.startswith("${") and value.endswith("}") else value
            for key, value in (connection or {}).items()
        }

    def register_datasources(self, datasources: List["Datasource"]) -> None:
        """Registers every datasource that can be built; records the rest as unavailable.

        For the tools that check a config without an engine (``doctor``,
        ``policy validate``, ``curated validate``). The engine builds its
        datasources with ``register_datasource`` and reports every failure
        as one ``ConfigError``.
        """
        for datasource in datasources:
            try:
                self.register_datasource(datasource)
            except Exception as e:
                self.mark_unavailable(datasource.id, str(e) or type(e).__name__)

    def register_datasource(self, datasource: "Datasource") -> DatasourceAdapterProtocol:
        """Builds the datasource's adapter, without connecting, and registers it.

        A ``Datasource`` with an ``adapter`` object is registered as given.
        Otherwise ``type`` is loaded from the registry, every reference in
        ``connection`` is resolved, and the adapter class is built with the
        datasource's limits.

        Raises:
            RegistryError: the type is unknown, implements another interface
                version, or its driver is not installed.
            ValueError: a reference did not resolve, or the adapter rejected
                the connection settings (with every secret masked).
        """
        adapter = datasource.adapter
        if adapter is None:
            adapter_cls = self._registry.load("adapter", datasource.type)
            connection_args = self.resolved_connection(datasource.connection)
            try:
                adapter = adapter_cls(
                    datasource_id=datasource.id,
                    datasource_engine_type=datasource.type,
                    connection_args=connection_args,
                    statement_timeout_ms=datasource.limits.timeout_ms,
                    row_limit=datasource.limits.row_limit,
                    max_bytes=datasource.limits.max_bytes,
                    base_dir=self._base_dir,
                )
            except Exception as e:
                # A pydantic ValidationError renders the whole input dict, which
                # now holds the plaintext password. Re-raise without the cause so
                # neither the message nor the traceback carries it.
                raise ValueError(redact_connection_secrets(str(e), connection_args)) from None
        capabilities = self._read_capabilities(datasource.id, adapter)
        with self._lock:
            self._adapters[datasource.id] = adapter
            self._datasources[datasource.id] = datasource
            self._capabilities[datasource.id] = capabilities
            self._unavailable.pop(datasource.id, None)
        return adapter

    def remove(self, datasource_id: str) -> None:
        """Forgets a datasource and closes its adapter."""
        with self._lock:
            adapter = self._adapters.pop(datasource_id, None)
            self._datasources.pop(datasource_id, None)
            self._capabilities.pop(datasource_id, None)
            self._unavailable.pop(datasource_id, None)
        if adapter is not None:
            close_adapter(adapter)

    def mark_unavailable(self, datasource_id: str, reason: str) -> None:
        """Keeps a datasource out of routing; anything that targets it fails with ``reason``."""
        with self._lock:
            self._unavailable[datasource_id] = reason
        logger.error(f"Datasource '{datasource_id}' is unavailable: {reason}")

    def mark_available(self, datasource_id: str) -> None:
        with self._lock:
            self._unavailable.pop(datasource_id, None)

    def unavailable(self) -> Dict[str, str]:
        """Datasources that are unavailable, mapped to the reason."""
        with self._lock:
            return dict(self._unavailable)

    def all_adapters(self) -> Dict[str, DatasourceAdapterProtocol]:
        """Every built adapter by id, available or not: what ``check()`` connects to."""
        with self._lock:
            return dict(self._adapters)

    def close(self) -> None:
        """Closes every adapter: disposes its connection pool."""
        with self._lock:
            adapters = list(self._adapters.values())
        for adapter in adapters:
            close_adapter(adapter)

    def _not_registered(self, datasource_id: str) -> ValueError:
        reason = self._unavailable.get(datasource_id)
        if reason is not None:
            return ValueError(f"Datasource '{datasource_id}' is unavailable: {reason}")
        return ValueError(f"Unknown datasource ID: {datasource_id}")

    def _normalize_capabilities(self, caps: Any) -> Set[str]:
        if not caps:
            return set()
        normalized = set()
        for cap in caps:
            if isinstance(cap, DatasourceCapability):
                normalized.add(cap.value)
            else:
                normalized.add(str(cap))
        return normalized

    def _read_capabilities(self, ds_id: str, adapter: Any) -> Set[str]:
        """The adapter's declared capabilities, or none at all if it cannot say.

        Fails closed: an adapter whose ``capabilities()`` is missing or raises
        registers with no capabilities, so nothing is routed to or executed on it.
        """
        try:
            return self._normalize_capabilities(adapter.capabilities())
        except Exception as exc:
            logger.error(f"Failed to get capabilities for datasource '{ds_id}'; it gets none. {exc}")
            return set()

    def _available(self, datasource_id: str) -> bool:
        return datasource_id in self._adapters and datasource_id not in self._unavailable

    def get_adapter(self, datasource_id: str) -> DatasourceAdapterProtocol:
        """The adapter for an available datasource.

        Raises:
            ValueError: the datasource is unknown, or unavailable (with the reason).
        """
        with self._lock:
            if not self._available(datasource_id):
                raise self._not_registered(datasource_id)
            return self._adapters[datasource_id]

    def get_datasource(self, datasource_id: str) -> "Datasource":
        """The ``Datasource`` the datasource was registered with: description, sample questions, limits."""
        with self._lock:
            if datasource_id not in self._datasources:
                raise self._not_registered(datasource_id)
            return self._datasources[datasource_id]

    def get_dialect(self, datasource_id: str) -> str:
        """Returns a normalized dialect string from the adapter.

        Args:
            datasource_id: The ID of the datasource.

        Returns:
            str: The dialect string (e.g., 'postgres').
        """
        return self.get_adapter(datasource_id).get_dialect()

    def get_preferred_tables(self, datasource_id: str) -> List[str]:
        """The datasource's ``preferred_tables``; empty for an unknown id."""
        with self._lock:
            datasource = self._datasources.get(datasource_id)
            return list(datasource.preferred_tables) if datasource else []

    def get_description(self, datasource_id: str) -> Optional[str]:
        """The ``description`` the datasource was registered with; None for an unknown id."""
        with self._lock:
            datasource = self._datasources.get(datasource_id)
            return datasource.description if datasource else None

    def get_sample_questions(self, datasource_id: str) -> List[str]:
        """The datasource's ``sample_questions``; empty for an unknown id."""
        with self._lock:
            datasource = self._datasources.get(datasource_id)
            return list(datasource.sample_questions) if datasource else []

    def get_capabilities(self, datasource_id: str) -> Set[str]:
        """Returns the capability flags for a datasource."""
        with self._lock:
            if not self._available(datasource_id):
                raise self._not_registered(datasource_id)
            return set(self._capabilities[datasource_id])

    def supports(self, datasource_id: str, *capabilities: Any) -> bool:
        """True only if the datasource is available and declares every one of ``capabilities``.

        The one capability check: routing and execution both ask it. An
        unknown or unavailable datasource, or one whose adapter could not
        report its capabilities, supports nothing.
        """
        with self._lock:
            declared = self._capabilities.get(datasource_id) if self._available(datasource_id) else None
        if declared is None:
            return False
        return self._normalize_capabilities(capabilities) <= declared

    def list_adapters(self) -> List[DatasourceAdapterProtocol]:
        """Every available adapter."""
        with self._lock:
            return [a for ds_id, a in self._adapters.items() if ds_id not in self._unavailable]

    def list_ids(self) -> List[str]:
        """Every available datasource id."""
        with self._lock:
            return [ds_id for ds_id in self._adapters if ds_id not in self._unavailable]


def close_adapter(adapter: Any) -> None:
    """Releases an adapter's connections through its optional ``close()``. Never raises."""
    try:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
    except Exception as e:
        logger.warning(f"Closing datasource '{getattr(adapter, 'datasource_id', '?')}' failed: {e}")
