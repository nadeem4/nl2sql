# Changelog

## [0.1.0](https://github.com/nadeem4/nl2sql/compare/v0.1.0...v0.1.0) (2026-08-28)


### ⚠ BREAKING CHANGES

* collapse distribution to nl2sql, nl2sql-api, nl2sql-adapter-sdk

### Features

* **adapters:** add DuckDB adapter as the nl2sql[duckdb] extra ([60318d1](https://github.com/nadeem4/nl2sql/commit/60318d1efc6f183b17d70c05f5024a05feb9f12a))
* **adapters:** add DuckDB adapter as the nl2sql[duckdb] extra ([42b5bdf](https://github.com/nadeem4/nl2sql/commit/42b5bdf1bc5b6f904da716d35b182cf6ea1c7dea))
* **cli:** add application container to docker demo; make mssql opt-in ([a7bd223](https://github.com/nadeem4/nl2sql/commit/a7bd2232282f70f57a1beeb420a60a8244dd2852))
* **cli:** add application container to docker demo; make mssql opt-in ([7d761ff](https://github.com/nadeem4/nl2sql/commit/7d761ff7a98ae0b2a0f53b40a6c974db3e995ce3))


### Bug Fixes

* **api:** require explicit CORS origins instead of wildcard-with-credentials ([6edfc95](https://github.com/nadeem4/nl2sql/commit/6edfc95065c0c5f876dcc91726a46851c9054d1b))
* **api:** require explicit CORS origins instead of wildcard-with-credentials ([96c1464](https://github.com/nadeem4/nl2sql/commit/96c146473ad2a8c1ca71ef9310a34999471fefe3))
* **core:** construct SettingsAPI from the settings singleton ([e9b9d43](https://github.com/nadeem4/nl2sql/commit/e9b9d4369deef22eb9a9e988f88c911578517c71))
* **core:** construct SettingsAPI from the settings singleton ([770a737](https://github.com/nadeem4/nl2sql/commit/770a7379d5db1f84ff20a0a26142b66b69e25969))
* **core:** correct pydantic/langgraph floors and declare chromadb directly ([c2fde1a](https://github.com/nadeem4/nl2sql/commit/c2fde1a67f1a2ebd2aa672912c1eeeda043f9b52))
* **core:** correct pydantic/langgraph floors and declare chromadb directly ([d4339ac](https://github.com/nadeem4/nl2sql/commit/d4339ac59cef997e89b5cd7af2cd4bcf53457751))
* **core:** make cancellation per-run instead of process-global ([afd3473](https://github.com/nadeem4/nl2sql/commit/afd3473b7f16748008afc4ed805b14b4191671a6))
* **core:** make cancellation per-run instead of process-global ([6cebac7](https://github.com/nadeem4/nl2sql/commit/6cebac7b0863abe74cee82674348a65be8b12df8))
* **core:** stop configuring the root logger at import time ([bbb0597](https://github.com/nadeem4/nl2sql/commit/bbb0597462aa7666f5ad8cc8a9f1f6d6bee8a186))
* **core:** stop configuring the root logger at import time ([ccbebda](https://github.com/nadeem4/nl2sql/commit/ccbebda59b33327067e1c52dfe8a416c9f8d9bb7))


### Documentation

* align README and architecture docs with the code that exists ([b066250](https://github.com/nadeem4/nl2sql/commit/b066250e3404bb61c71837e2d353043320949107))
* align README and architecture docs with the code that exists ([75bbb86](https://github.com/nadeem4/nl2sql/commit/75bbb86e3096e78acd3ea5b21ec276fff821e28c))
* describe the three-distribution layout ([36014e6](https://github.com/nadeem4/nl2sql/commit/36014e6ae9c9c8aa8e8e75a901f894ee63024738))
* sync documentation with recent changes ([c01c42b](https://github.com/nadeem4/nl2sql/commit/c01c42b9106aebdfdea1dbb9c9d08f2a15e8454a))
* sync documentation with recent changes ([40f3bea](https://github.com/nadeem4/nl2sql/commit/40f3bea44796fb83f0f251a393a376b1c48067b2))


### Code Refactoring

* collapse distribution to nl2sql, nl2sql-api, nl2sql-adapter-sdk ([c7755ad](https://github.com/nadeem4/nl2sql/commit/c7755ad19607f6d1e27ade8ad237653a0702c86a))


### Continuous Integration

* pin the first release to 0.1.0 and scope its changelog ([f8d3137](https://github.com/nadeem4/nl2sql/commit/f8d313789b80bb0e97726eb0088571b8c0ada5ac))
