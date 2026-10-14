import importlib.util


def check_package(name: str) -> bool:
    """Checks if a python package is installed."""
    import_name = name.replace("-", "_")
    return importlib.util.find_spec(import_name) is not None
