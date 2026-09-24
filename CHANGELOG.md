# Changelog

## [0.2.0](https://github.com/nadeem4/nl2sql/compare/v0.1.2...v0.2.0) (2026-09-24)


### ⚠ BREAKING CHANGES

* **demo:** make Chinook the only demo dataset and test against it ([#98](https://github.com/nadeem4/nl2sql/issues/98))

### Features

* **api:** expose plan, validation checks, row sample, status and timings on QueryResult ([#88](https://github.com/nadeem4/nl2sql/issues/88)) ([7a41a60](https://github.com/nadeem4/nl2sql/commit/7a41a60b409680fcb2200a42187277ff32716150))
* **api:** NL2SQL accepts env and env_file; embeddings fail with an actionable message ([#90](https://github.com/nadeem4/nl2sql/issues/90)) ([e8ca01c](https://github.com/nadeem4/nl2sql/commit/e8ca01c09e9dac6017a4ca6c1ce4e65535cf959a))
* **api:** take the role from an auth dependency, not the request body ([#142](https://github.com/nadeem4/nl2sql/issues/142)) ([f8c06fd](https://github.com/nadeem4/nl2sql/commit/f8c06fde0cb9ef4acb24ba5b037fe800f8407eb1))
* **cli:** pass an API key to `nl2sql demo` and persist it in .env.demo ([#97](https://github.com/nadeem4/nl2sql/issues/97)) ([b0305f8](https://github.com/nadeem4/nl2sql/commit/b0305f8e36c27be07876305631143354ab32b032))
* **demo:** a link preview card for the hosted demo ([#178](https://github.com/nadeem4/nl2sql/issues/178)) ([927baf1](https://github.com/nadeem4/nl2sql/commit/927baf111834bcf42a9ea51789ef7c56c95a110d))
* **demo:** add support and web-analytics sample databases linked to Chinook customers ([#157](https://github.com/nadeem4/nl2sql/issues/157)) ([659039f](https://github.com/nadeem4/nl2sql/commit/659039f14b40e3a6b68c6b25fcc80bb645635449))
* **demo:** Dockerfile and Hugging Face Space configuration for the hosted demo ([#168](https://github.com/nadeem4/nl2sql/issues/168)) ([835687f](https://github.com/nadeem4/nl2sql/commit/835687fb877c7c80bad9052f0b6b04a996bfe16d))
* **demo:** hosted mode so a public demo can take each visitor's own key ([#165](https://github.com/nadeem4/nl2sql/issues/165)) ([cfb6cd3](https://github.com/nadeem4/nl2sql/commit/cfb6cd3341a3a306946704ae3f91b69c132040c7))
* **demo:** React playground with schema, plan, validation, SQL and rows, plus `nl2sql demo` ([#93](https://github.com/nadeem4/nl2sql/issues/93)) ([9f0a922](https://github.com/nadeem4/nl2sql/commit/9f0a922905d5170266dd62f62597587006db4945))
* **demo:** vendor the Chinook database and add DemoManager.setup_chinook ([#91](https://github.com/nadeem4/nl2sql/issues/91)) ([22cd257](https://github.com/nadeem4/nl2sql/commit/22cd25707596a3bccbd92e7e3e671c844fa5bd6b))
* **eval:** --model and built-in presets for tier 2, with project-relative outputs and publish --from ([#129](https://github.com/nadeem4/nl2sql/issues/129)) ([1a8101d](https://github.com/nadeem4/nl2sql/commit/1a8101dd97c94e49aacb93c44170e1a590dcd510))
* **eval:** add the Chinook gold dataset with executed gold results ([#114](https://github.com/nadeem4/nl2sql/issues/114)) ([176a369](https://github.com/nadeem4/nl2sql/commit/176a36986ebcf70dd5eaa52768bd95560e3eb6e5))
* **eval:** alternative gold answers, confidence intervals and a paired regression gate ([#156](https://github.com/nadeem4/nl2sql/issues/156)) ([de42c40](https://github.com/nadeem4/nl2sql/commit/de42c401a43b094242244192696fb8591a7d74eb))
* **eval:** answer faithfulness and schema retrieval recall ([#128](https://github.com/nadeem4/nl2sql/issues/128)) ([571cd16](https://github.com/nadeem4/nl2sql/commit/571cd16451e96ed0d6e155d16131a926c4e2c953))
* **eval:** keep each sub-query's plan and the answer text in tier 2 records ([#146](https://github.com/nadeem4/nl2sql/issues/146)) ([db73b92](https://github.com/nadeem4/nl2sql/commit/db73b921b42d39f326ecde10c8642acedaf94e86))
* **eval:** report a lenient accuracy that allows extra and reordered columns ([#152](https://github.com/nadeem4/nl2sql/issues/152)) ([37351c3](https://github.com/nadeem4/nl2sql/commit/37351c3506f3e3ec5dcbfcb2be00bb83c81f115e))
* **eval:** tier 1 harness runs gold plans through the code nodes on every PR ([#119](https://github.com/nadeem4/nl2sql/issues/119)) ([6be6357](https://github.com/nadeem4/nl2sql/commit/6be6357d58b8f003c81faf187b6976a304b05edf))
* **eval:** tier 2 benchmark runs the real model across configs with a cost cap and a comparison scoreboard ([#126](https://github.com/nadeem4/nl2sql/issues/126)) ([e1dc47e](https://github.com/nadeem4/nl2sql/commit/e1dc47e037dfda0313924468cdc232ff990e68e6))
* **feedback:** record answer feedback in the playground and report guardrail rates ([#127](https://github.com/nadeem4/nl2sql/issues/127)) ([4d24267](https://github.com/nadeem4/nl2sql/commit/4d2426780bd3a44f9a3fef950bb206176e5552be))
* **generator:** break row-order ties with the selected columns so results are deterministic ([#116](https://github.com/nadeem4/nl2sql/issues/116)) ([88ed9f3](https://github.com/nadeem4/nl2sql/commit/88ed9f3971e9d1e2956fa51fdb006c467356de50))
* **llm:** default to gpt-5.4 and make temperature optional ([#108](https://github.com/nadeem4/nl2sql/issues/108)) ([1509153](https://github.com/nadeem4/nl2sql/commit/1509153199bc954c4fbcb5b5ce7dade6d9d50d07))
* **llm:** provider adapters per wire format, native Claude support, and a provider per node ([#124](https://github.com/nadeem4/nl2sql/issues/124)) ([2f39f9a](https://github.com/nadeem4/nl2sql/commit/2f39f9ad1c186354310753f347b594e5ca5a9c9b))
* **llm:** replay store and recording proxy for key-free demo mode ([#92](https://github.com/nadeem4/nl2sql/issues/92)) ([a02c236](https://github.com/nadeem4/nl2sql/commit/a02c23608da0783b2d9f3d7c6f9506508e084ea0))
* **planner:** cache validated plans by question and schema version, re-validating on every use ([#122](https://github.com/nadeem4/nl2sql/issues/122)) ([f5e34b3](https://github.com/nadeem4/nl2sql/commit/f5e34b312a484ba7744ac289468b2f8e63beff21))
* **playground:** a Pipeline page showing what each step does ([#171](https://github.com/nadeem4/nl2sql/issues/171)) ([8535e21](https://github.com/nadeem4/nl2sql/commit/8535e21ea642ff91c0b54244ff60462d6293c303))
* **playground:** add a settings panel for the API key and a model per LLM node ([#109](https://github.com/nadeem4/nl2sql/issues/109)) ([0e60f4e](https://github.com/nadeem4/nl2sql/commit/0e60f4e52191913c957a042c07bb72d3c0d3d123))
* **playground:** choose a model per step in the hosted demo, with a key per provider ([#173](https://github.com/nadeem4/nl2sql/issues/173)) ([2220e74](https://github.com/nadeem4/nl2sql/commit/2220e741364b066e8094082aa1b93cbcbd84dcf7))
* **playground:** give Settings and Retrieval their own pages with a header nav ([#154](https://github.com/nadeem4/nl2sql/issues/154)) ([4e6c6f5](https://github.com/nadeem4/nl2sql/commit/4e6c6f576a1c42f02be7fc5b47cda3abe5868ef4))
* **playground:** let the demo show each of its databases ([#172](https://github.com/nadeem4/nl2sql/issues/172)) ([67f06bd](https://github.com/nadeem4/nl2sql/commit/67f06bde6532b8fa448f290ff4e27e2595d94e87))
* **playground:** redesign the demo page around one run, and add a Debug toggle ([#102](https://github.com/nadeem4/nl2sql/issues/102)) ([2cfe121](https://github.com/nadeem4/nl2sql/commit/2cfe1210b1dd7dbc77219620b04070edaf644567))
* **playground:** tell a new visitor what the demo needs before they ask ([#170](https://github.com/nadeem4/nl2sql/issues/170)) ([4ff7dbd](https://github.com/nadeem4/nl2sql/commit/4ff7dbdcbde1c41296a2ccd03fea7cd5e30b82dd))
* **resolver:** skip vector search for a single datasource and refuse questions no datasource can answer ([#121](https://github.com/nadeem4/nl2sql/issues/121)) ([5430c9c](https://github.com/nadeem4/nl2sql/commit/5430c9cc19ee31fbeee5e6d9127a1b9d8cc2673e))
* **retrieval:** pass the full schema to the planner for small schemas ([#89](https://github.com/nadeem4/nl2sql/issues/89)) ([88e4651](https://github.com/nadeem4/nl2sql/commit/88e4651391750bb2ffb94183b10741242012eed4))
* **retrieval:** record retrieval candidates, scores and MMR picks in traces, and add a Retrieval inspector to the playground ([#123](https://github.com/nadeem4/nl2sql/issues/123)) ([03f74c0](https://github.com/nadeem4/nl2sql/commit/03f74c073eda9c24a4c029269f93542f402253af))
* **telemetry:** report per-node and per-question LLM tokens, calls and time on every run ([#101](https://github.com/nadeem4/nl2sql/issues/101)) ([717758a](https://github.com/nadeem4/nl2sql/commit/717758a420ce45ebafc9d5c25e61d7d373437097))
* **trace:** record every node of a run to one file, show it, and replay it without the model ([#104](https://github.com/nadeem4/nl2sql/issues/104)) ([1e55710](https://github.com/nadeem4/nl2sql/commit/1e557100d201c19145d59355cb666a39a72185ad))


### Bug Fixes

* **adapters:** return sqlglot dialect names from get_dialect ([#136](https://github.com/nadeem4/nl2sql/issues/136)) ([be25596](https://github.com/nadeem4/nl2sql/commit/be25596b57b43257f2306d78401af2eda86a18ad))
* **aggregator:** resolve prefixed join keys when combining sub-query results ([#162](https://github.com/nadeem4/nl2sql/issues/162)) ([e03bab3](https://github.com/nadeem4/nl2sql/commit/e03bab39826683fcdc454e3876f03b4a426b4d4b))
* **aggregator:** use polars group_by, resolve prefixed join keys, apply every post-op field ([#144](https://github.com/nadeem4/nl2sql/issues/144)) ([4f052dc](https://github.com/nadeem4/nl2sql/commit/4f052dc5707a7493984e7d1ccffdc0625de44504))
* **cli:** doctor checks the LLM key, errors name the env file, no traceback by default, add --version ([#95](https://github.com/nadeem4/nl2sql/issues/95)) ([5c6ae6e](https://github.com/nadeem4/nl2sql/commit/5c6ae6ea6aa1478b736933ad5c111fa2d3053fb5))
* **cli:** exit 1 when a run ends with pipeline errors ([#115](https://github.com/nadeem4/nl2sql/issues/115)) ([0ec4906](https://github.com/nadeem4/nl2sql/commit/0ec490613d142199d8876d083902d54ddf3904cf))
* **cli:** load the --env file into the environment; plan ORDER BY keeps the dialect's NULL placement ([#120](https://github.com/nadeem4/nl2sql/issues/120)) ([efcd76f](https://github.com/nadeem4/nl2sql/commit/efcd76f7c16072cfa9bb297fe109475ae8d25539))
* config path options, explicit-path descriptions, and COUNT(DISTINCT) in plans ([#125](https://github.com/nadeem4/nl2sql/issues/125)) ([4d5e625](https://github.com/nadeem4/nl2sql/commit/4d5e6252d2aa6edca35a05a1e08ac3f9701313b5))
* **config:** hold API keys and connection secrets as SecretStr so they never print ([#113](https://github.com/nadeem4/nl2sql/issues/113)) ([1cec9cf](https://github.com/nadeem4/nl2sql/commit/1cec9cfead18160eb898d085382d70c9612b2a9a))
* **decomposer:** carry a sub-query's ranking and limit into its plan ([#141](https://github.com/nadeem4/nl2sql/issues/141)) ([46cdc43](https://github.com/nadeem4/nl2sql/commit/46cdc43cb503baa40de2adcc4df493ee4b6f4c36))
* **decomposer:** reject SQL syntax, not English words that happen to be SQL keywords ([#105](https://github.com/nadeem4/nl2sql/issues/105)) ([022a3b3](https://github.com/nadeem4/nl2sql/commit/022a3b3bd9f04fe208337693aaaaed71546d3a0c))
* **demo:** be honest about replay mode, make --record replayable, and ship license notices ([#117](https://github.com/nadeem4/nl2sql/issues/117)) ([5430943](https://github.com/nadeem4/nl2sql/commit/54309436c15831dc5162105cdb113f47646ad68b))
* **eval:** identify a benchmark run by the dataset's datasources, not every registered one ([#158](https://github.com/nadeem4/nl2sql/issues/158)) ([3256dae](https://github.com/nadeem4/nl2sql/commit/3256daeb317bd9874edb5f4d4d5d47b73fad2dcd))
* **generator:** attach the unjoined side of each join, render arithmetic, stop after a denial ([#100](https://github.com/nadeem4/nl2sql/issues/100)) ([fb618ab](https://github.com/nadeem4/nl2sql/commit/fb618abb2299995f638aecf2393d837bd74f5643))
* **generator:** quote select aliases and column names that are not bare identifiers ([#133](https://github.com/nadeem4/nl2sql/issues/133)) ([460df7c](https://github.com/nadeem4/nl2sql/commit/460df7c701e557c6aa59eac90475d1018a5bc236))
* **generator:** render string concatenation as the dialect's own, never numeric + ([#132](https://github.com/nadeem4/nl2sql/issues/132)) ([e55c1dc](https://github.com/nadeem4/nl2sql/commit/e55c1dc3f901b6388121f6180a61bdb705a91af9))
* **index:** give the datasource entry its description and example questions, and refresh snapshot metadata on re-index ([#111](https://github.com/nadeem4/nl2sql/issues/111)) ([29dab2f](https://github.com/nadeem4/nl2sql/commit/29dab2fdd09f4a61111d916f37148d4660de74cc))
* **index:** rebuild without ever leaving an empty index, and show index health with a Rebuild action ([#110](https://github.com/nadeem4/nl2sql/issues/110)) ([cf0cdcb](https://github.com/nadeem4/nl2sql/commit/cf0cdcb5e12cb2eadc51f33c3a965882b0270dcb))
* **pipeline:** derive sub-query status from its final attempt, not its error history ([#106](https://github.com/nadeem4/nl2sql/issues/106)) ([059f45a](https://github.com/nadeem4/nl2sql/commit/059f45a0e9fe019e3e5438dd40a210ae1865d82f))
* **pipeline:** honor execute=False so plan-only runs never touch a database ([#86](https://github.com/nadeem4/nl2sql/issues/86)) ([cfa9ad0](https://github.com/nadeem4/nl2sql/commit/cfa9ad0cae42930f2ac33903b0818723b31ee3a6))
* **pipeline:** return a structured result when a sub-query fails instead of crashing or looping ([#84](https://github.com/nadeem4/nl2sql/issues/84)) ([2c9d6a0](https://github.com/nadeem4/nl2sql/commit/2c9d6a0a96fe69ef360d4248345f11be9f32da9c))
* **rbac:** refuse strictly without leaking forbidden data, and deny unknown or empty roles cleanly ([#112](https://github.com/nadeem4/nl2sql/issues/112)) ([ec18715](https://github.com/nadeem4/nl2sql/commit/ec1871597e7912ccb95292fd5908d1ed863abb76))
* **registry:** one capability check that fails closed ([#145](https://github.com/nadeem4/nl2sql/issues/145)) ([f65e691](https://github.com/nadeem4/nl2sql/commit/f65e691a3514d2155aa659898e4da776a4a6c881))
* report global planner failures and pass the refiner the failed plan as JSON ([#107](https://github.com/nadeem4/nl2sql/issues/107)) ([e391d35](https://github.com/nadeem4/nl2sql/commit/e391d35fdecbf92ba9505f806510ff8479daeb05))
* **runtime:** enforce the pipeline timeout and keep signal handlers on the main thread ([#87](https://github.com/nadeem4/nl2sql/issues/87)) ([4174e0b](https://github.com/nadeem4/nl2sql/commit/4174e0b90f20776af31f0c1894dd5b52914b3958))
* **validator:** match FK joins when relationships carry qualified names ([#94](https://github.com/nadeem4/nl2sql/issues/94)) ([8575932](https://github.com/nadeem4/nl2sql/commit/85759327dee2501f856d950eead36204242713cb))
* **validator:** only allow known function names in plans ([#137](https://github.com/nadeem4/nl2sql/issues/137)) ([948f1a3](https://github.com/nadeem4/nl2sql/commit/948f1a37020a7b432f90a92b66b5e4fd63e6071a))
* **validator:** reject unjoinable plans before generation so the model can retry ([#161](https://github.com/nadeem4/nl2sql/issues/161)) ([906cf99](https://github.com/nadeem4/nl2sql/commit/906cf996f376b7e1e4c6ab9d33cb34eb52d21cf4))
* **validator:** wrap ORDER BY terms and stop treating sampled values as a domain ([#99](https://github.com/nadeem4/nl2sql/issues/99)) ([f443df1](https://github.com/nadeem4/nl2sql/commit/f443df1e9a41378adb624577ff8ab7b29b919bd0))


### Performance Improvements

* **prompts:** compact the schema and put stable context first so prompts cache ([#118](https://github.com/nadeem4/nl2sql/issues/118)) ([4513d1a](https://github.com/nadeem4/nl2sql/commit/4513d1a98ae3bcfa92bd5720471e9e05dac87c49))


### Documentation

* align README, docs and packaging metadata with the code that exists ([#96](https://github.com/nadeem4/nl2sql/issues/96)) ([3f35661](https://github.com/nadeem4/nl2sql/commit/3f35661ba795dca40b24c4879c21e0d846dc689a))
* **playground:** describe the pages and several databases in the README and rail ([#160](https://github.com/nadeem4/nl2sql/issues/160)) ([d914978](https://github.com/nadeem4/nl2sql/commit/d9149784c050496eddcb49d62e8f60b33fa3c0a1))
* **readme:** drop engine.results, removed with ResultAPI ([#164](https://github.com/nadeem4/nl2sql/issues/164)) ([072a6f4](https://github.com/nadeem4/nl2sql/commit/072a6f403fdfecd2efe72a91702781d8dc023ffe))
* **readme:** restructure around quickstart, results, CLI, REST API and screenshots ([#131](https://github.com/nadeem4/nl2sql/issues/131)) ([58df5c9](https://github.com/nadeem4/nl2sql/commit/58df5c90882ee3393ec2e3ca5b0e9ede1f23a9a3))
* **readme:** update for the hosted demo and fix the diagram on dark backgrounds ([#179](https://github.com/nadeem4/nl2sql/issues/179)) ([6d47a14](https://github.com/nadeem4/nl2sql/commit/6d47a14da4954d9b00ad208c3905fbe4473b1b4d))
* sync documentation with recent changes ([#103](https://github.com/nadeem4/nl2sql/issues/103)) ([362d943](https://github.com/nadeem4/nl2sql/commit/362d943af656b97779d923346f1f9df195138ad1))
* sync documentation with recent changes ([#135](https://github.com/nadeem4/nl2sql/issues/135)) ([b176f0f](https://github.com/nadeem4/nl2sql/commit/b176f0ff10280c92fabd8b456142a3bd1895c6a4))
* sync documentation with recent changes ([#167](https://github.com/nadeem4/nl2sql/issues/167)) ([082f053](https://github.com/nadeem4/nl2sql/commit/082f05309585e19892470dcb4f382a1c6a4d1b57))


### Code Refactoring

* **demo:** make Chinook the only demo dataset and test against it ([#98](https://github.com/nadeem4/nl2sql/issues/98)) ([047751b](https://github.com/nadeem4/nl2sql/commit/047751b3d7fcbafa5b90852010823a0f9e7ed504))

## [0.1.2](https://github.com/nadeem4/nl2sql/compare/v0.1.1...v0.1.2) (2026-09-07)


### Bug Fixes

* **ci:** publish without attestations from the called workflow ([#80](https://github.com/nadeem4/nl2sql/issues/80)) ([2bcdbe8](https://github.com/nadeem4/nl2sql/commit/2bcdbe84a4cc4ab0bd40a66f84b7efa467797f9a))

## [0.1.1](https://github.com/nadeem4/nl2sql/compare/v0.1.0...v0.1.1) (2026-09-07)


### Bug Fixes

* **ci:** build the API image from the repository root ([495c1c3](https://github.com/nadeem4/nl2sql/commit/495c1c3840508e8eac69696af81840b2884fb2d8))
* **ci:** build the API image from the repository root ([6ce3c49](https://github.com/nadeem4/nl2sql/commit/6ce3c4925c8e3770904ab50aa3773928711321d7))
* **cli:** repair the doctor connectivity check ([2abb600](https://github.com/nadeem4/nl2sql/commit/2abb60004d48a983d8cf39f8c075bf91386495eb))
* **cli:** repair the doctor connectivity check ([1cc9e44](https://github.com/nadeem4/nl2sql/commit/1cc9e44b5a16a69d0e0a28c473e568b39dbc8a8d))
* **config:** treat an empty providers list as no secret providers ([97fe5d2](https://github.com/nadeem4/nl2sql/commit/97fe5d28cce7c9d6ca1b694c7c97b9c4a07e690f))
* **config:** treat an empty providers list as no secret providers ([d08a45c](https://github.com/nadeem4/nl2sql/commit/d08a45cad76dcc372c11010ec9629aa9ce605a92))
* **demo:** make the docker demo datasources reachable ([b7edaf9](https://github.com/nadeem4/nl2sql/commit/b7edaf99125e04b7731f87d555e6bf86e7f9affb))
* **demo:** make the docker demo datasources reachable ([1381c9c](https://github.com/nadeem4/nl2sql/commit/1381c9c1bdd9de45ae0cb93c29b60019babefb3e))
* **demo:** survive an unusable prompt during docker demo setup ([#79](https://github.com/nadeem4/nl2sql/issues/79)) ([6b9f712](https://github.com/nadeem4/nl2sql/commit/6b9f7120d285840626b32ecf61ee4cae0efca1e2))
* **pipeline:** keep the error code when a routing error reaches the runtime ([#78](https://github.com/nadeem4/nl2sql/issues/78)) ([2cf2170](https://github.com/nadeem4/nl2sql/commit/2cf21707dfeb6b1496a41845acf0968cb76d1f9e))
* **pipeline:** raise a real exception when no subgraph matches ([#75](https://github.com/nadeem4/nl2sql/issues/75)) ([74f283b](https://github.com/nadeem4/nl2sql/commit/74f283b840a99b119f25565c5d19848966e8f8dc))


### Documentation

* sync documentation with recent changes ([#77](https://github.com/nadeem4/nl2sql/issues/77)) ([3dd87e6](https://github.com/nadeem4/nl2sql/commit/3dd87e6c73ca520b9564d31873e48004cc711c1c))

## 0.1.0 (2026-08-28)


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

## Changelog
