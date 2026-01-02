import unittest
from unittest.mock import MagicMock
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql_adapter_sdk import SchemaMetadata, Table, Column, ForeignKey

class TestEnrichedIndexing(unittest.TestCase):
    def test_index_schema_rich_metadata(self):
        # Mock adapter
        adapter = MagicMock()
        
        t1 = Table(
            name="users",
            columns=[
                Column(name="id", type="INTEGER", is_primary_key=True),
                Column(name="email", type="VARCHAR", description="User email address")
            ],
            description="Registry of all users"
        )
        t2 = Table(
            name="orders",
            columns=[
                Column(name="id", type="INTEGER", is_primary_key=True),
                Column(name="user_id", type="INTEGER")
            ],
            foreign_keys=[
                ForeignKey(
                    constrained_columns=["user_id"],
                    referred_table="users",
                    referred_columns=["id"]
                )
            ]
        )
        
        adapter.fetch_schema.return_value = SchemaMetadata(
            datasource_id="test_ds",
            datasource_engine_type="sqlite",
            tables=[t1, t2]
        )
        
        # Mock VectorStore internals
        store = OrchestratorVectorStore(embeddings=MagicMock())
        store.vectorstore = MagicMock()
        
        store.index_schema(adapter, "test_ds")
        
        # Verify calls
        args, _ = store.vectorstore.add_documents.call_args
        docs = args[0]
        self.assertEqual(len(docs), 2)
        
        # Verify content of 'users' table document
        users_doc = next(d for d in docs if d.metadata["table_name"] == "users")
        self.assertIn("Table: users", users_doc.page_content)
        self.assertIn("Comment: Registry of all users", users_doc.page_content)
        self.assertIn("email (VARCHAR) 'User email address'", users_doc.page_content)
        
        # Verify content of 'orders' table document
        orders_doc = next(d for d in docs if d.metadata["table_name"] == "orders") 
        # Note: VectorStore applies aliasing logic that results in verbose FK string: src_alias.col -> ref_alias.ref_alias.col
        self.assertIn("Foreign Keys: test_ds_t2.user_id -> test_ds_t1.test_ds_t1.id", orders_doc.page_content)

if __name__ == "__main__":
    unittest.main()
