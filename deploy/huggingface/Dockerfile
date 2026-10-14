# The nl2sql playground as a public demo (`nl2sql demo --hosted`).
#
# This folder is a Space root: it needs no build context from the repository,
# so the whole deploy is this directory, copied to the Space's root by
# .github/workflows/publish_space.yml. See README.md here, and
# docs/deployment/hosted-demo.md in the repository.
#
# Three things are baked in so the container answers the first question
# immediately and reaches nothing but the model at run time:
#
#   * the three sample databases and their config (`nl2sql init --demo`),
#   * their vector index, built with the local embedder,
#   * the local embedding model itself (~79 MB of ONNX all-MiniLM-L6-v2, which
#     chromadb otherwise downloads on the first question).
#
# The image holds no API key and never will: hosted mode takes the visitor's
# own, per request, and writes it nowhere.
FROM python:3.12-slim

# What to install the engine from.
#
# A Space build takes no build arguments from us, so
# .github/workflows/publish_space.yml rewrites this one line in the copy it
# pushes. A release pins the version it just published:
#   ARG NL2SQL_SPEC="nl2sql-engine[demo]==0.2.0"
# so the public demo always runs a released version, the same bytes a user
# gets from `pip install`. A dispatch with no tag (a rehearsal on a scratch
# Space) pins a GitHub archive of the commit it was run from instead. Either
# way the text of this instruction changes, which busts the layer cache for
# the install below.
#
# The default here, the tip of `main` as a GitHub archive (so the image needs
# no git), is only what a local `docker build` of this folder gets. To build a
# release locally: --build-arg NL2SQL_SPEC="nl2sql-engine[demo]==0.2.0".
ARG NL2SQL_SPEC="nl2sql-engine[demo] @ https://github.com/nadeem4/nl2sql/archive/main.tar.gz#subdirectory=packages/nl2sql"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # The port a Space expects; any other works too.
    PORT=7860

# Retried, bounded: 10 attempts, 30 seconds apart. A release pushes the Space
# minutes after PyPI accepts the upload, and the index the Hub's builder sees
# can still be the old one -- v0.2.0's first build failed with "from versions:
# 0.1.0, 0.1.1, 0.1.2" although the release had already installed it from PyPI.
RUN pip install --upgrade pip \
    && attempt=1 \
    && until pip install --no-cache-dir "${NL2SQL_SPEC}"; do \
         if [ "$attempt" -ge 10 ]; then \
           echo "pip could not install ${NL2SQL_SPEC} after $attempt attempts." >&2; \
           exit 1; \
         fi; \
         echo "Install attempt $attempt of 10 failed; the index may lag a new release. Retrying in 30s."; \
         attempt=$((attempt + 1)); \
         sleep 30; \
       done

# A Space runs the container as uid 1000; so does everything else here, so the
# prebuilt project and the model cache are readable by the process that needs
# them and writable by nobody else.
RUN useradd --create-home --uid 1000 demo
USER demo
# Every nl2sql command in the image, and the server, read this config.
ENV HOME=/home/demo \
    NL2SQL_CONFIG=/home/demo/demo/nl2sql.yaml
WORKDIR /home/demo/demo

# Writes nl2sql.yaml, its section files and data/*.sqlite, then indexes the
# three schemas with the local embedder -- which is what pulls the ONNX model
# into $HOME/.cache/chroma and leaves it in the image. No key is needed: the
# demo embeds locally and the optional LLM enrichment pass is skipped.
#
# The archive the model was extracted from is 83 MB of the same bytes: chroma
# reaches for it only when the extracted files are missing, so it goes.
RUN nl2sql init --demo /home/demo/demo \
    && nl2sql index \
    && rm -f "$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz"

EXPOSE 7860

# Answers as soon as the playground is serving; this route reads nothing but
# process state, and reports the engine version the release pipeline waits for.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/api/health',timeout=4)"

# A shell, so $PORT is the container's rather than the build's; `exec`, so the
# server is pid 1 and a stop signal reaches it instead of the shell. Hosted
# mode holds no key of its own, and the limits are per visitor; see
# docs/deployment/hosted-demo.md.
CMD ["sh", "-c", "exec nl2sql demo --hosted --dir /home/demo/demo --host 0.0.0.0 --port \"$PORT\" --no-browser --questions-per-minute 6 --questions-per-session 30"]
