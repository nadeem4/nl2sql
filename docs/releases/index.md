# Releases

What each release is for, in plain words. The full list of commits is in
[`CHANGELOG.md`](https://github.com/nadeem4/nl2sql/blob/main/CHANGELOG.md).
How a release is cut is in [Releasing](../development/releasing.md).

- [0.6.0](0.6.0.md): a real-database test bed, adapter SDK v2 and its conformance kit, four storage slots, OpenAI-compatible providers and allow/deny policy globs.
- [0.5.0](0.5.0.md): Configuration v2, one `nl2sql.yaml`, an extension registry, a namespaced Python API and per-engine config; a clean break from 0.4.
- [0.4.1](0.4.1.md): admin-only REST routes, opt-in enrichment, schema-aware policies and a datasource that fails no longer stops startup.
- [0.4.0](0.4.0.md): curated answers, where a question that matches a verified query runs its reviewed SQL and is labelled verified.
- [0.3.0](0.3.0.md): results at any size, paged and downloadable, with a 20-row preview for the answer and a streamed run.
- [0.2.4](0.2.4.md): a cached plan is reused only on the day it was made, so relative dates stay current.
- [0.2.3](0.2.3.md): the model knows today's date and its data, and wrong or empty queries are retried or explained.
- [0.2.2](0.2.2.md): correct answers or clear refusals for multi-sub-query questions, and the demo at nl2sql.codewithnk.com.
- [0.2.1](0.2.1.md): the release image on Python 3.12, and a reliable Space deploy.
- [0.2.0](0.2.0.md): the hosted demo, with or without a key, and `pip install "nl2sql-engine[demo]"`.
- [0.1.2](0.1.2.md): a merged release PR publishes to PyPI on its own.
- [0.1.1](0.1.1.md): fixes for `nl2sql doctor`, the Docker demo, pipeline errors and the API image.
- [0.1.0](0.1.0.md): the first release, as three packages on PyPI.
