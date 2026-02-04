import argparse
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and run NL2SQL API image.")
    parser.add_argument(
        "--tag",
        default="nl2sql-api",
        help="Docker image tag to build and run.",
    )
    parser.add_argument(
        "--dockerfile",
        default="packages/api/Dockerfile.dev",
        help="Dockerfile path (relative to repo root).",
    )
    parser.add_argument(
        "--extras",
        default="all",
        help="Comma-separated adapter extras to install.",
    )
    parser.add_argument(
        "--env",
        default="",
        help="Set ENV for runtime (e.g. dev, demo, prod).",
    )
    parser.add_argument(
        "--env-file-path",
        default="",
        help="Set ENV_FILE_PATH for runtime.",
    )
    parser.add_argument(
        "--port",
        default="8000",
        help="Host port to map to container 8000.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent

    build_cmd = [
        "docker",
        "build",
        "-f",
        args.dockerfile,
        "-t",
        args.tag,
        ".",
    ]

    if args.extras:
        build_cmd.extend(["--build-arg", f"NL2SQL_EXTRAS={args.extras}"])

    run_cmd = [
        "docker",
        "run",
        "--rm",
        "-p",
        f"{args.port}:8000",
    ]

    if args.env:
        run_cmd.extend(["-e", f"ENV={args.env}"])
    if args.env_file_path:
        run_cmd.extend(["-e", f"ENV_FILE_PATH={args.env_file_path}"])

    run_cmd.append(args.tag)

    try:
        subprocess.run(build_cmd, cwd=repo_root, check=True)
        subprocess.run(run_cmd, cwd=repo_root, check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
