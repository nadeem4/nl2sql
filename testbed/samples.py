"""Fetch the test bed's sample databases and write the wide schema, into one folder.

    python testbed/samples.py /samples

Run by the compose file's one-shot ``samples`` service. Each sample is
downloaded from a pinned URL and its SHA-256 checked before it is kept, so a
changed or truncated file is refused rather than loaded. Whatever is already
in the folder is skipped, so a second run downloads nothing. Nothing here is
committed to the repository: we never redistribute the samples. Their
licences are listed in docs/testing/test-bed.md.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

import wide_schema

PAGILA = "https://raw.githubusercontent.com/devrimgunduz/pagila/5ba5a57aeb159f75f02aca2432d3c262186d13d3"


@dataclass(frozen=True)
class Sample:
    name: str
    url: str
    sha256: str
    extract: Tuple[str, ...] = ()  # members of a .tar.gz, written beside it without their folder


SAMPLES: Tuple[Sample, ...] = (
    Sample("pagila-schema.sql", f"{PAGILA}/pagila-schema.sql",
           "8ce358e4c8014087b85296694a0893887bd7a4190e3ce407f2721b86b98e5707"),
    Sample("pagila-insert-data.sql", f"{PAGILA}/pagila-insert-data.sql",
           "136f3105263a1338a9805da4c06b6b37b60f1abc15ce7dbc8d6f5501f506aa22"),
    Sample("sakila-db.tar.gz", "https://downloads.mysql.com/docs/sakila-db.tar.gz",
           "683e957d456341e48cccd66135ddb0d5701a8436523ed0a6366279f8e8719a68",
           extract=("sakila-db/sakila-schema.sql", "sakila-db/sakila-data.sql")),
    Sample("AdventureWorksLT2022.bak",
           "https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/AdventureWorksLT2022.bak",
           "7e1ae09a08ee0342781bacfba9da79e91b67991aabcd1a42454a3b64eb60d626"),
)

WIDE_SEED = 7

Download = Callable[[str, Path], None]


class ChecksumError(RuntimeError):
    """A downloaded sample is not the pinned file."""


def download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(sample: Sample, folder: Path, get: Download = download) -> Path:
    """``folder/<name>``, downloaded and checked unless it is already there.

    The file is written under a temporary name and renamed only once its
    checksum matches, so a file with the final name is always a checked one.
    """
    target = folder / sample.name
    if not target.exists():
        part = folder / (sample.name + ".part")
        get(sample.url, part)
        actual = sha256(part)
        if actual != sample.sha256:
            part.unlink()
            raise ChecksumError(f"{sample.name}: expected SHA-256 {sample.sha256}, got {actual} "
                                f"from {sample.url}. The file changed upstream; check it and pin the new hash.")
        part.replace(target)
    for member in sample.extract:
        out = folder / Path(member).name
        if not out.exists():
            with tarfile.open(target) as archive:
                source = archive.extractfile(member)
                if source is None:
                    raise ChecksumError(f"{sample.name} has no file {member}")
                part = out.with_name(out.name + ".part")
                part.write_bytes(source.read())
                part.replace(out)
    return target


def write_wide_schemas(folder: Path, seed: int = WIDE_SEED) -> None:
    """``wide_<dialect>.sql`` for each server, unless it is already there."""
    schemas = None
    for dialect in wide_schema.DIALECTS:
        target = folder / f"wide_{dialect}.sql"
        if target.exists():
            continue
        schemas = schemas or wide_schema.build(seed)
        part = target.with_name(target.name + ".part")
        part.write_text(wide_schema.render(schemas, dialect), encoding="utf-8", newline="\n")
        part.replace(target)


def prepare(folder: Path, samples: Sequence[Sample] = SAMPLES, get: Download = download) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        fetch(sample, folder, get)
    write_wide_schemas(folder)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python testbed/samples.py FOLDER", file=sys.stderr)
        return 2
    try:
        prepare(Path(args[0]))
    except ChecksumError as error:
        print(f"samples: {error}", file=sys.stderr)
        return 1
    print(f"samples: ready in {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
