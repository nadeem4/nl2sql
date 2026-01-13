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

from typing import Callable, Any, Dict
from nl2sql.common.contracts import ExecutionRequest, ExecutionResult

def execute_in_sandbox(
    pool: ProcessPoolExecutor,
    func: Callable[[ExecutionRequest], ExecutionResult],
    request: ExecutionRequest,
    timeout_sec: int = 30
) -> ExecutionResult:
    """Executes a function in the sandbox with centralized error handling.

    This helper centralizes the logic for handling:
    1. BrokenProcessPool (Worker Crash/Segfault/OOM)
    2. TimeoutError (Job hung)
    3. Generic Exception (Serialization error or other infra failure)

    Args:
        pool (ProcessPoolExecutor): The execution pool to usage.
        func (Callable): The function to execute (must accept ExecutionRequest and return ExecutionResult).
        request (ExecutionRequest): The contract payload.
        timeout_sec (int): Maximum time to wait for the result.

    Returns:
        ExecutionResult: The result, safely wrapping any infrastructure errors.
    """
    try:
        future = pool.submit(func, request)
        return future.result(timeout=timeout_sec)
    
    except TimeoutError:
        logger.error(f"Sandbox Timeout ({timeout_sec}s) for {func.__name__}")
        return ExecutionResult(
            success=False,
            error=f"Operation timed out after {timeout_sec} seconds. The worker process may be hung.",
            metrics={"execution_time_ms": timeout_sec * 1000}
        )
        
    except Exception as e:
        msg = str(e)
        logger.error(f"Sandbox Exception: {msg}")
        
        is_crash = False
        try:
            from concurrent.futures.process import BrokenProcessPool
            if isinstance(e, BrokenProcessPool):
                is_crash = True
        except ImportError:
            pass
            
        if "BrokenProcessPool" in msg or "Terminated" in msg:
            is_crash = True
            
        if is_crash:
            return ExecutionResult(
                success=False,
                error=f"SANDBOX CRASH: The worker process terminated abruptly (Segfault/OOM). ({msg})",
                metrics={"is_crash": 1.0}
            )
            
        return ExecutionResult(
            success=False,
            error=f"Sandbox Execution Failed: {msg}"
        )
