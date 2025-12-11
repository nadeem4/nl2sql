import pandas as pd
import sqlglot
from sqlglot import exp
from typing import List, Dict, Any, Optional

class ModelEvaluator:
    """
    Evaluates the correctness of AI-generated SQL and its execution results.
    """

    @staticmethod
    def compare_sql_semantic(generated_sql: str, expected_sql: str) -> bool:
        """
        Compares two SQL queries semantically by normalizing them to ASTs.
        Ignores formatting, casing, and quote styles.
        """
        if not generated_sql or not expected_sql:
            return False
            
        try:
            # Parse both into ASTs
            gen_ast = sqlglot.parse_one(generated_sql)
            exp_ast = sqlglot.parse_one(expected_sql)
            
            # Simple equality check on the AST objects often works for structure
            # But let's verify if they generate the same canonical SQL
            gen_canonical = gen_ast.sql()
            exp_canonical = exp_ast.sql()
            
            return gen_canonical == exp_canonical
        except Exception:
            # If parsing fails, fall back to relaxed string matching
            return ModelEvaluator._normalize_string(generated_sql) == ModelEvaluator._normalize_string(expected_sql)

    @staticmethod
    def _normalize_string(s: str) -> str:
        """Removes whitespace and lowercases for basic comparison."""
        return " ".join(s.split()).lower().strip(";")

    @staticmethod
    def compare_results(
        generated_rows: List[Dict[str, Any]], 
        expected_rows: List[Dict[str, Any]], 
        order_matters: bool = False
    ) -> bool:
        """
        Compares two result sets (lists of dicts).
        
        Args:
            generated_rows: Rows returned by the AI query.
            expected_rows: Rows returned by the ground truth query.
            order_matters: If True, strictly enforces row order.
        """
        if len(generated_rows) != len(expected_rows):
            return False
            
        if not generated_rows and not expected_rows:
            return True
            
        try:
            df_gen = pd.DataFrame(generated_rows)
            df_exp = pd.DataFrame(expected_rows)
            
            # Normalize column names to lower case to avoid case sensitivity issues in headers
            df_gen.columns = df_gen.columns.str.lower()
            df_exp.columns = df_exp.columns.str.lower()
            
            # Sort columns to ensure column order doesn't matter
            df_gen = df_gen.reindex(sorted(df_gen.columns), axis=1)
            df_exp = df_exp.reindex(sorted(df_exp.columns), axis=1)
            
            # If columns don't match, fail immediately
            if list(df_gen.columns) != list(df_exp.columns):
                return False
            
            if not order_matters:
                # Sort by all columns to align rows
                df_gen = df_gen.sort_values(by=list(df_gen.columns)).reset_index(drop=True)
                df_exp = df_exp.sort_values(by=list(df_exp.columns)).reset_index(drop=True)
            
            # Fuzzy comparison using pandas testing util or simple equality with tolerance
            # We use check_dtype=False because one might be int and other float from different DB engines
            pd.testing.assert_frame_equal(df_gen, df_exp, check_dtype=False, check_like=True, atol=1e-5)
            return True
            
        except AssertionError:
            return False
        except Exception:
            # Fallback for complex types or other issues
            return False

    @staticmethod
    def calculate_aggregate_metrics(results: List[Dict[str, Any]], total_samples: int) -> Dict[str, Any]:
        """
        Calculates high-level metrics from a list of result dictionaries.

        Returns:
            Dict containing:
            - routing_accuracy
            - execution_accuracy 
            - valid_sql_rate
            - layer_distribution (count and percentage)
        """
        if not total_samples:
            return {}

        correct_routing = sum(1 for r in results if r.get("routing_match"))
        correct_sql = sum(1 for r in results if r.get("sql_match"))
        valid_sql = sum(1 for r in results if r.get("status") not in ["EXEC_FAIL", "NO_SQL", "ERROR", "ROUTE_FAIL", "BAD_CONFIG", "GT_FAIL"])
        
        # Layer Distribution
        layer_counts = {"layer_1": 0, "layer_2": 0, "layer_3": 0, "fallback": 0}
        for r in results:
            layer = r.get("routing_layer", "unknown")
            if layer in layer_counts:
                layer_counts[layer] += 1
        
        # Calculate percentages
        metrics = {
            "routing_accuracy": (correct_routing / total_samples) * 100,
            "execution_accuracy": (correct_sql / total_samples) * 100,
            "valid_sql_rate": (valid_sql / total_samples) * 100,
            "layer_distribution": layer_counts,
            "layer_percentages": {k: (v / total_samples) * 100 for k, v in layer_counts.items()}
        }
        
        return metrics
