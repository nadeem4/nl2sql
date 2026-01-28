
import yaml
import concurrent.futures
import statistics
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import pandas as pd
from sqlalchemy import text, create_engine

from nl2sql.datasources import DatasourceRegistry
from nl2sql.llm import LLMRegistry
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.pipeline.graph import run_with_graph
from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql_cli.types import BenchmarkConfig

@dataclass
class BenchmarkResult:
    """Standardized result object from a benchmark run."""
    results: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    iterations: int = 1

class BenchmarkRunner:
    """
    Orchestrates the execution of the Benchmark suite.
    Decoupled from CLI presentation logic.
    """
    def __init__(
        self,
        config: BenchmarkConfig,
        datasource_registry: DatasourceRegistry,
        vector_store: VectorStore,
        llm_registry: LLMRegistry
    ):
        self.config = config
        self.ds_registry = datasource_registry
        self.vector_store = vector_store
        self.llm_registry = llm_registry

    def run_dataset(self, config_name: str = "default", progress_callback=None) -> BenchmarkResult:
        """
        Runs the dataset evaluation.
        """
        dataset = self._load_dataset()
        
        results = []
        workers = 5 
        iterations = self.config.iterations if self.config.iterations else 1
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = []
            for i in range(iterations):
                for item in dataset:
                    futures.append(executor.submit(self._evaluate_case, item)) 
            
            total_tasks = len(dataset) * iterations
            
            # Use callback for progress bar if provided
            iterator = concurrent.futures.as_completed(futures)
            if progress_callback:
                iterator = progress_callback(iterator, total=total_tasks, description=f"Evaluating ({workers} parallel, {iterations} runs)...")
                
            for future in iterator:
                results.append(future.result())

        # Sort results by ID
        results.sort(key=lambda x: x["id"])
        
        # Calculate Metrics
        metrics = ModelEvaluator.calculate_aggregate_metrics(results, len(results))
        
        if self.config.export_path:
             # Basic export logic here or let CLI handle it?
             # Let's keep file I/O out of core if possible, but calculating metrics is core.
             pass

        return BenchmarkResult(results=results, metrics=metrics, iterations=iterations)

    def _load_dataset(self) -> List[Dict]:
        """Loads and filters the dataset."""
        dataset_path = self.config.dataset_path
        if not dataset_path.exists():
            raise FileNotFoundError(f"Dataset file not found: {dataset_path}")
            
        dataset = yaml.safe_load(dataset_path.read_text())
        if not isinstance(dataset, list):
            raise ValueError("Dataset must be a list of test cases.")
            
        if self.config.include_ids:
            dataset = [item for item in dataset if item.get("id") in self.config.include_ids]
            if not dataset:
                raise ValueError(f"No test cases found matching IDs: {self.config.include_ids}")
                
        return dataset

    def _evaluate_case(self, item: dict) -> dict:
        """Evaluates a single test case."""
        q_id = item.get("id", "unknown")
        question = item.get("question")
        expected_sql = item.get("expected_sql")
        expected_ds = item.get("datasource")
        expected_layer = item.get("expected_routing_layer")
        
        try:
            state = run_with_graph(
                registry=self.ds_registry,
                llm_registry=self.llm_registry,
                user_query=question,
                datasource_id=None, 
                execute=not self.config.routing_only,
                vector_store=self.vector_store,
                vector_store_path=self.config.vector_store_path
            )
        except Exception as e:
            return {
                "id": q_id,
                "question": question,
                "status": "ERROR",
                "error": str(e),
                "routing_match": False,
                "sql_match": False
            }
            
        actual_ds = state.get("datasource_id") or set()
        expected_set = set(expected_ds) if expected_ds else set()
        routing_match = (actual_ds == expected_set)
        
        # --- Metrics Extraction ---
        all_routing_info = state.get("routing_info", {})
        primary_id = sorted(list(actual_ds))[0] if actual_ds else None
        routing_info = all_routing_info.get(primary_id) if primary_id else None
        
        def get_val(obj, key, default=None):
            if isinstance(obj, dict): return obj.get(key, default)
            return getattr(obj, key, default)

        if routing_info:
            routing_layer = get_val(routing_info, "layer", "unknown")
            routing_reasoning = get_val(routing_info, "reasoning", "")
            routing_tokens = get_val(routing_info, "tokens", 0)
            routing_latency = get_val(routing_info, "latency", 0)
            l1_score = get_val(routing_info, "l1_score", 0.0)
            candidates = get_val(routing_info, "candidates", [])
            if candidates and not isinstance(candidates[0], dict):
                candidates = [{"id": c.id, "score": c.score} for c in candidates]
        else:
            routing_layer = "unknown"
            routing_reasoning = "No routing info"
            routing_tokens = 0
            routing_latency = 0
            l1_score = 0.0
            candidates = []

        layer_match = (routing_layer == expected_layer)
        
        if self.config.routing_only:
            return {
                "id": q_id,
                "question": question,
                "status": "PASS" if routing_match else "ROUTE_FAIL",
                "routing_match": routing_match,
                "sql_match": None,
                "actual_ds": actual_ds,
                "expected_ds": expected_ds,
                "routing_layer": routing_layer,
                "routing_reasoning": routing_reasoning,
                "routing_tokens": routing_tokens,
                "routing_latency": routing_latency,
                "l1_score": l1_score,
                "candidates": candidates,
                "expected_layer": expected_layer,
                "layer_match": layer_match
            }
            
        generated_sql = None
        execution_res = None
        subgraph_outputs = state.get("subgraph_outputs") or {}
        if subgraph_outputs:
            first_output = next(iter(subgraph_outputs.values()))
            generated_sql = (
                first_output.get("sql_draft")
                if isinstance(first_output, dict)
                else getattr(first_output, "sql_draft", None)
            )
            execution_res = (
                first_output.get("artifact")
                if isinstance(first_output, dict)
                else getattr(first_output, "artifact", None)
            )

        if not generated_sql:
            generated_sql_data = state.get("sql_draft")
            if isinstance(generated_sql_data, str):
                generated_sql = generated_sql_data
            else:
                generated_sql = (
                    generated_sql_data.get("sql")
                    if isinstance(generated_sql_data, dict)
                    else getattr(generated_sql_data, "sql", None)
                )
        
        if execution_res is None:
            execution_res = state.get("execution")
        generated_rows = execution_res.get("rows") if isinstance(execution_res, dict) else getattr(execution_res, "rows", [])
        exec_error = execution_res.get("error") if isinstance(execution_res, dict) else getattr(execution_res, "error", None)
        
        if exec_error:
            return {
                "id": q_id,
                "question": question,
                "status": "EXEC_FAIL",
                "error": exec_error,
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }
            
        if not generated_sql:
             return {
                "id": q_id,
                "question": question,
                "status": "NO_SQL",
                "routing_match": routing_match,
                "sql_match": False
            }

        if not expected_sql:
             return {
                "id": q_id,
                "question": question,
                "status": "NO_GT", 
                "routing_match": routing_match,
                "sql_match": None,
                "semantic_sql_match": None,
                "gen_sql": generated_sql
            }

        if not expected_ds:
             return {
                "id": q_id,
                "status": "BAD_CONFIG",
                "error": "Dataset missing expected datasource",
                "routing_match": routing_match,
                "sql_match": False
            }
             
        try:
            profile = self.ds_registry.get_profile(expected_ds)
            # Use core engine creation if possible, but for now duplicate logic is safer than importing from CLI
            engine = create_engine(profile.sqlalchemy_url)
            with engine.connect() as conn:
                expected_rows_res = conn.execute(text(expected_sql))
                expected_rows = [dict(row._mapping) for row in expected_rows_res]
        except Exception as e:
             return {
                "id": q_id,
                "question": question,
                "status": "GT_FAIL", 
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }
             
        try:
            data_match = ModelEvaluator.compare_results(generated_rows, expected_rows, order_matters=False)
            
            try:
                semantic_sql_match = ModelEvaluator.compare_sql_semantic(generated_sql, expected_sql)
            except ValueError as ve:
                err_msg = str(ve)
                if "Ground Truth" in err_msg:
                    return {
                        "id": q_id,
                        "question": question,
                        "status": "INVALID_GT",
                        "error": err_msg,
                        "routing_match": routing_match,
                        "sql_match": None if data_match else False,
                        "semantic_sql_match": None,
                        "gen_sql": generated_sql
                    }
                else: 
                     return {
                        "id": q_id,
                        "question": question,
                        "status": "INVALID_SQL", 
                        "error": err_msg,
                        "routing_match": routing_match,
                        "sql_match": data_match,
                        "semantic_sql_match": False,
                        "gen_sql": generated_sql
                    }

            return {
                "id": q_id,
                "question": question,
                "status": "PASS" if data_match else "DATA_MISMATCH",
                "routing_match": routing_match,
                "sql_match": data_match,
                "semantic_sql_match": semantic_sql_match,
                "gen_sql": generated_sql,
                "exp_sql": expected_sql,
                "gen_rows": len(generated_rows),
                "exp_rows": len(expected_rows),
                "expected_layer": expected_layer,
                "layer_match": layer_match
            }
            
        except Exception as e:
             return {
                "id": q_id,
                "status": "COMPARE_FAIL",
                "error": str(e),
                "routing_match": routing_match,
                "sql_match": False,
                "gen_sql": generated_sql
            }
