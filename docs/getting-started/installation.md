# Installation

The NL2SQL Platform is designed to run as a modular Python application. It uses a **Monorepo** structure managed by `poetry` (optional) or standard `pip`.

## Prerequisites

* **Python 3.9+**
* **Docker** (Required for database containers in Demo/Test modes)
* **Git**

## Local Development Setup

Clone the repository:

```bash
git clone https://github.com/your-org/nl2sql-platform.git
cd nl2sql-platform
```

### Option 1: Using Pip (Standard)

Install the core package in editable mode:

```bash
pip install -e packages/core
pip install -e packages/cli
pip install -e packages/adapter-sdk
# Install specific adapters as needed
pip install -e packages/adapters/postgres
```

### Option 2: Using Make (Convenience)

If a `Makefile` is present:

```bash
make install
```

## Verifying Installation

Verify the CLI is installed and accessible:

```bash
nl2sql --version
```

Run the `doctor` command to check for missing dependencies or configuration issues:

```bash
nl2sql doctor
```
