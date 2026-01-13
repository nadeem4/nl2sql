"""
Sandbox Manager for isolating unsafe operations (SQL Execution, Indexing).

This module manages two separate ProcessPoolExecutors:
1. Execution Pool: Low-latency, for user-facing query execution.
2. Indexing Pool: High-throughput, for background schema fetching.

It implements the Singleton pattern to ensure pools are reused across requests.
"""
from __future__ import annotations

import atexit
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings

logger = get_logger("sandbox_manager")

class SandboxManager:
    """Manages global process pools for sandboxed execution."""
    
    _exec_pool: Optional[ProcessPoolExecutor] = None
    _index_pool: Optional[ProcessPoolExecutor] = None

    @classmethod
    def get_execution_pool(cls) -> ProcessPoolExecutor:
        """Returns the process pool for latency-sensitive execution tasks."""
        if cls._exec_pool is None:
            workers = settings.sandbox_exec_workers
            logger.info(f"Initializing Execution Sandbox with {workers} workers.")
            cls._exec_pool = ProcessPoolExecutor(
                max_workers=workers,
                initializer=cls._init_worker,
                initargs=("execution",)
            )
        return cls._exec_pool

    @classmethod
    def get_indexing_pool(cls) -> ProcessPoolExecutor:
        """Returns the process pool for throughput-heavy indexing tasks."""
        if cls._index_pool is None:
            workers = settings.sandbox_index_workers
            logger.info(f"Initializing Indexing Sandbox with {workers} workers.")
            cls._index_pool = ProcessPoolExecutor(
                max_workers=workers,
                initializer=cls._init_worker,
                initargs=("indexing",)
            )
        return cls._index_pool

    @staticmethod
    def _init_worker(pool_type: str):
        """Initializer run once per worker process startup.
        
        Sets a distinct process name for debugging and prepares the worker execution environment.
        """
        import multiprocessing
        p = multiprocessing.current_process()
        p.name = f"SandboxWorker-{pool_type}-{p.name}"

    @classmethod
    def shutdown(cls):
        """Shuts down all sandbox pools waiting for pending tasks to complete."""
        if cls._exec_pool:
            logger.info("Shutting down Execution Sandbox...")
            cls._exec_pool.shutdown(wait=True)
            cls._exec_pool = None
            
        if cls._index_pool:
            logger.info("Shutting down Indexing Sandbox...")
            cls._index_pool.shutdown(wait=True)
            cls._index_pool = None

atexit.register(SandboxManager.shutdown)

def get_execution_pool() -> ProcessPoolExecutor:
    """Helper accessor for execution pool.

    Returns:
        ProcessPoolExecutor: The shared execution pool instance.
    """
    return SandboxManager.get_execution_pool()

def get_indexing_pool() -> ProcessPoolExecutor:
    """Helper accessor for indexing pool.

    Returns:
        ProcessPoolExecutor: The shared indexing pool instance.
    """
    return SandboxManager.get_indexing_pool()
