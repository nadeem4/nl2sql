import unittest
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.state import GraphState
from nl2sql.auth import UserContext
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.common.settings import settings


class TestDatasourceResolverNode(unittest.TestCase):
    def setUp(self):
        self.ctx = MagicMock()
        self.ctx.vector_store = MagicMock()
        self.ctx.rbac = MagicMock()
        self.ctx.ds_registry = MagicMock()
        self.ctx.schema_store = MagicMock()
        self.ctx.rbac.get_allowed_datasources.return_value = ["sales_db"]
        self.ctx.ds_registry.get_capabilities.return_value = {
            DatasourceCapability.SUPPORTS_SQL.value
        }
        self.ctx.schema_store.get_latest_version.return_value = "v1"
        self.node = DatasourceResolverNode(self.ctx)

    def test_resolver_outputs_ids(self):
        doc = MagicMock()
        doc.metadata = {"datasource_id": "sales_db", "schema_version": "v1"}
        self.ctx.vector_store.retrieve_datasource_candidates.return_value = [doc]

        state = GraphState(user_query="sales", user_context=UserContext())
        result = self.node(state)

        resolved = result["datasource_resolver_response"].resolved_datasources[0]
        self.assertEqual(resolved.datasource_id, "sales_db")
        self.assertEqual(resolved.schema_version, "v1")
        self.assertEqual(resolved.chunk_schema_version, "v1")
        self.assertFalse(resolved.schema_version_mismatch)
        self.assertEqual(result["datasource_resolver_response"].unsupported_datasource_ids, [])

    def test_resolver_marks_unsupported(self):
        doc = MagicMock()
        doc.metadata = {"datasource_id": "legacy_db"}
        self.ctx.vector_store.retrieve_datasource_candidates.return_value = [doc]
        self.ctx.rbac.get_allowed_datasources.return_value = ["legacy_db"]
        self.ctx.ds_registry.get_capabilities.return_value = set()
        self.ctx.schema_store.get_latest_version.return_value = "v1"
        node = DatasourceResolverNode(self.ctx)

        state = GraphState(user_query="legacy", user_context=UserContext())
        result = node(state)

        resolved = result["datasource_resolver_response"].resolved_datasources[0]
        self.assertEqual(resolved.datasource_id, "legacy_db")
        self.assertEqual(result["datasource_resolver_response"].unsupported_datasource_ids, ["legacy_db"])

    def test_schema_version_mismatch_warns(self):
        doc = MagicMock()
        doc.metadata = {"datasource_id": "sales_db", "schema_version": "v0"}
        self.ctx.vector_store.retrieve_datasource_candidates.return_value = [doc]
        self.ctx.schema_store.get_latest_version.return_value = "v1"

        state = GraphState(user_query="sales", user_context=UserContext())
        result = self.node(state)

        resolved = result["datasource_resolver_response"].resolved_datasources[0]
        self.assertTrue(resolved.schema_version_mismatch)
        self.assertNotIn("errors", result)

    def test_schema_version_mismatch_fails(self):
        doc = MagicMock()
        doc.metadata = {"datasource_id": "sales_db", "schema_version": "v0"}
        self.ctx.vector_store.retrieve_datasource_candidates.return_value = [doc]
        self.ctx.schema_store.get_latest_version.return_value = "v1"

        previous_policy = settings.schema_version_mismatch_policy
        settings.schema_version_mismatch_policy = "fail"
        try:
            state = GraphState(user_query="sales", user_context=UserContext())
            result = self.node(state)
        finally:
            settings.schema_version_mismatch_policy = previous_policy

        self.assertIn("errors", result)
        self.assertEqual(
            result["datasource_resolver_response"].allowed_datasource_ids,
            [],
        )


if __name__ == "__main__":
    unittest.main()
