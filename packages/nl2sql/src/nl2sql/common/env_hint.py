"""Name the env file this process is configured from.

Telling a user to "set OPENAI_API_KEY" is only actionable if it also says
where. A run started with ``--env demo`` reads ``.env.demo``; one started with
``--env-file`` reads whatever path that named; a bare run reads ``.env``.
Resolution order mirrors :func:`nl2sql.common.settings.load_settings` exactly,
so the file named here is the file that was loaded.
"""

from __future__ import annotations

import os

__all__ = ["active_env_file"]


def active_env_file() -> str:
    """Returns the env file the current environment selects.

    Returns:
        ``ENV_FILE_PATH`` when set, else ``.env.<ENV>`` (or ``<APP_ENV>``) when
        an environment name is set, else ``.env``.
    """
    env_file_path = os.getenv("ENV_FILE_PATH")
    if env_file_path:
        return env_file_path

    env_name = os.getenv("ENV") or os.getenv("APP_ENV")
    if env_name:
        return f".env.{env_name}"

    return ".env"
