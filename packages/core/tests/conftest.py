import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE_SRC = ROOT / "packages" / "core" / "src"
SDK_SRC = ROOT / "packages" / "adapter-sdk" / "src"

for path in (CORE_SRC, SDK_SRC):
    sys.path.insert(0, str(path))
