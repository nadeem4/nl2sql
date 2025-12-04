import streamlit as st
import sys
import os
import pandas as pd
from typing import Dict, Any, List

# Add src to path so we can import nl2sql modules
# __file__ is src/nl2sql/dashboard.py
# dirname is src/nl2sql
# .. is src
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.datasource_config import load_profiles
from nl2sql.llm_registry import load_llm_config, LLMRegistry
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.settings import settings

# Page Config
st.set_page_config(
    page_title="NL2SQL Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "thought_process" not in st.session_state:
    st.session_state.thought_process = []

def main():
    st.title("📊 NL2SQL Dashboard")
    
    # --- Sidebar Configuration ---
    with st.sidebar:
        st.header("Configuration")
        
        # Datasource Config
        config_path = st.text_input("Datasource Config Path", value=settings.datasource_config_path)
        try:
            profiles = load_profiles(config_path)
            datasource_ids = list(profiles.keys())
            selected_datasource = st.selectbox("Select Datasource", ["auto-route"] + datasource_ids)
        except Exception as e:
            st.error(f"Failed to load profiles: {e}")
            return

        # LLM Config
        llm_config_path = st.text_input("LLM Config Path", value=settings.llm_config_path)
        
        # Vector Store
        vector_store_path = st.text_input("Vector Store Path", value=settings.vector_store_path)
        
        st.divider()
        st.caption("System Status: Ready")

    # --- Main Chat Interface ---
    
    # Display Chat History
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "sql" in message:
                st.code(message["sql"], language="sql")
            if "dataframe" in message:
                st.dataframe(message["dataframe"])
            if "thoughts" in message:
                with st.expander("See Thought Process"):
                    for node, logs in message["thoughts"].items():
                        st.markdown(f"**{node}**")
                        for log in logs:
                            st.text(log)

    # Chat Input
    if prompt := st.chat_input("Ask a question about your data..."):
        # Add user message to history
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Process Query
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            thought_placeholder = st.empty()
            
            try:
                # Initialize Components
                llm_cfg = load_llm_config(llm_config_path)
                llm_registry = LLMRegistry(llm_cfg)
                datasource_registry = DatasourceRegistry(profiles)
                vector_store = SchemaVectorStore(persist_directory=vector_store_path)
                
                # Capture Thoughts
                current_thoughts = {}
                
                def on_thought(node: str, logs: List[str], token: bool = False):
                    # Clean up node name (remove branch info for simpler display key, or keep it?)
                    # Let's keep it unique
                    if node not in current_thoughts:
                        current_thoughts[node] = []
                    
                    # Append new logs
                    for log in logs:
                        current_thoughts[node].append(log)
                    
                    # Update Thought Expander Live
                    with thought_placeholder.container():
                        with st.expander("Processing...", expanded=True):
                            for n, l in current_thoughts.items():
                                st.markdown(f"**{n}**")
                                st.text("\n".join(l[-5:])) # Show last 5 lines live

                # Run Graph
                datasource_id = None if selected_datasource == "auto-route" else selected_datasource
                
                state = run_with_graph(
                    registry=datasource_registry,
                    llm_registry=llm_registry,
                    user_query=prompt,
                    datasource_id=datasource_id,
                    execute=True,
                    vector_store=vector_store,
                    vector_store_path=vector_store_path,
                    on_thought=on_thought
                )
                
                # Final Response
                final_answer = state.get("final_answer", "No answer generated.")
                sql_query = state.get("sql_draft", {}).get("sql")
                execution_result = state.get("execution", {})
                rows = execution_result.get("sample", [])
                
                # Display Final Answer
                message_placeholder.markdown(final_answer)
                
                if sql_query:
                    st.code(sql_query, language="sql")
                
                df = None
                if rows:
                    df = pd.DataFrame(rows)
                    st.dataframe(df)
                
                # Save to history
                response_msg = {
                    "role": "assistant", 
                    "content": final_answer,
                    "thoughts": current_thoughts
                }
                if sql_query:
                    response_msg["sql"] = sql_query
                if df is not None:
                    response_msg["dataframe"] = df
                    
                st.session_state.messages.append(response_msg)
                
                # Clear live thought placeholder (it's saved in history now)
                thought_placeholder.empty()

            except Exception as e:
                st.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
