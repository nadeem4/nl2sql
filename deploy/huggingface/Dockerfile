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
#   * the three sample databases and their configs (`nl2sql setup --demo`),
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
    # Hosted mode, so the server holds no key even if the command is overridden.
    NL2SQL_DEMO_HOSTED=1 \
    # The port a Space expects; any other works too.
    PORT=7860 \
    # The limits, per visitor. See docs/deployment/hosted-demo.md.
    NL2SQL_DEMO_QUESTIONS_PER_MINUTE=6 \
    NL2SQL_DEMO_QUESTIONS_PER_SESSION=30

RUN pip install --upgrade pip && pip install "${NL2SQL_SPEC}"

# A Space runs the container as uid 1000; so does everything else here, so the
# prebuilt project and the model cache are readable by the process that needs
# them and writable by nobody else.
RUN useradd --create-home --uid 1000 demo
USER demo
ENV HOME=/home/demo
WORKDIR /home/demo/demo

# Writes data/*.sqlite, configs/*.demo.*, .env.demo and the stamp, then indexes
# the three schemas with the local embedder -- which is what pulls the ONNX
# model into $HOME/.cache/chroma and leaves it in the image. No key is needed:
# `.env.demo` embeds locally and the optional LLM enrichment pass is skipped.
#
# The archive the model was extracted from is 83 MB of the same bytes: chroma
# reaches for it only when the extracted files are missing, so it goes.
RUN nl2sql setup --demo \
    && rm -f "$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz"

EXPOSE 7860

# Answers as soon as the playground is serving; this route reads nothing but
# process state, and reports the engine version the release pipeline waits for.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/api/health',timeout=4)"

# A shell, so $PORT is the container's rather than the build's; `exec`, so the
# server is pid 1 and a stop signal reaches it instead of the shell.
CMD ["sh", "-c", "exec nl2sql demo --hosted --dir /home/demo/demo --host 0.0.0.0 --port \"$PORT\" --no-browser"]
