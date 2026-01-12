# Deployment Overview

The NL2SQL Platform is designed to be deployed as a containerized application. It is stateless (except for local vector stores, which should be persisted) and scales horizontally.

## Supported Methods

We provide official support for the following deployment methods:

* **[Docker](docker.md)**: For local testing and single-instance deployments.
* **[Kubernetes](kubernetes.md)**: For production, high-availability clusters.

## Architecture Guidelines

* **Statelessness**: The core API is stateless. However, if using a local Vector Store (ChromaDB/FAISS), you must mount a persistent volume.
* **Database Access**: ensuring network connectivity between the container and your target databases (Postgres, MySQL, etc.) is critical.
* **Security**: All secrets should be injected via Environment Variables.
