# NL2SQL Platform

Welcome to the **NL2SQL Platform**, a production-grade **Natural Language to SQL** engine built on an Agentic Graph Architecture.

This platform transforms complex user questions into safe, optimized, and strictly validated SQL queries across multiple database engines (PostgreSQL, MySQL, MSSQL, SQLite).

## 🚀 Key Features

* **Agentic Graph Architecture**: Powered by [LangGraph](https://langchain-ai.github.io/langgraph/), the system orchestrates a graph of specialized nodes (Planner, Validator, Generator) that can self-correct and backtrack.
* **Production Security**: Implementation of **Strict AST Validation**, **Role-Based Access Control (RBAC)**, and **Secrets Management**.
* **Polyglot Support**: Works seamlessly with multiple SQL dialects via an **Adapter SDK**.
* **Smart Routing**: A specialized **Decomposer Node** handles complex multi-datasource queries by splitting them into sub-queries.
* **Optimization**: Built-in **Vector Store** for schema and few-shot example retrieval to optimize context window usage.

## 🏗️ High-Level Architecture

The system follows a directed cyclic graph (DCG) flow, allowing for feedback loops and self-correction.

```mermaid
graph TD
    User([User Query]) --> Semantic[Semantic Analysis]
    Semantic --> Decomposer[Decomposer Node]
    
    subgraph "Orchestration & Routing"
        Decomposer -->|Sub-Query 1| Execution{Execution Branch}
        Decomposer -->|Sub-Query 2| Execution
    end

    subgraph "SQL Agent (ReAct Loop)"
        Execution --> Planner[Planner Node]
        Planner --> L_Validator[Logical Validator (AST)]
        
        L_Validator -->|Error| Refiner[Refiner Node]
        Refiner -->|Feedback| Planner
        
        L_Validator -->|OK| Generator[Generator Node]
        Generator --> P_Validator[Physical Validator]
        
        P_Validator -->|Error| Refiner
        P_Validator -->|OK| Executor[Executor Node]
    end

    Executor --> Aggregator[Aggregator Node]
    Aggregator --> Result([Final Result])
```

## 📚 Documentation Guide

* [**Getting Started**](getting-started/installation.md): Installation and quickstart demos.
* [**Core Engine**](core/nodes.md): Deep dive into the Neural Components (Nodes) and Graph State.
* [**Security**](safety/security.md): How we ensure Safety, Compliance, and Data Protection.
* [**Operations**](ops/configuration.md): Configuration, Logging, and Benchmarking guides.
* [**Development**](dev/adapters.md): Guide to building custom adapters and extending the platform.
