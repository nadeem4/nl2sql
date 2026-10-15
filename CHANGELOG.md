# Changelog

## [0.7.0](https://github.com/nadeem4/nl2sql/compare/v0.6.0...v0.7.0) (2026-10-15)


### Features

* **adapters:** an encrypt option for SQL Server connections ([#307](https://github.com/nadeem4/nl2sql/issues/307)) ([4a294ba](https://github.com/nadeem4/nl2sql/commit/4a294badc3d310fcc4f9647b62858f822b9eae75))
* **cost:** the dated price table, per-question caps and a reusable budget (E3) ([#326](https://github.com/nadeem4/nl2sql/issues/326)) ([db34901](https://github.com/nadeem4/nl2sql/commit/db34901fb1849052bf08720bbc2ed311b5ccb442))
* **evals:** engine.evals core — suites, Chinook moved, scorers, run, regenerate (V1) ([#317](https://github.com/nadeem4/nl2sql/issues/317)) ([aa4292b](https://github.com/nadeem4/nl2sql/commit/aa4292bfa5dac786d144b3cb8c5afa0869054f57))
* **evals:** nl2sql eval, the regression gate on execution accuracy, records by mode and suite (V2) ([#320](https://github.com/nadeem4/nl2sql/issues/320)) ([cb7b5c3](https://github.com/nadeem4/nl2sql/commit/cb7b5c3f7582b1a1b1ebb20cc2c41a7d4b07b165))
* **evals:** seeding draft eval cases from a datasource (V3) ([#328](https://github.com/nadeem4/nl2sql/issues/328)) ([147b05a](https://github.com/nadeem4/nl2sql/commit/147b05a62f519d03027ce877bfa748151db52e8b))
* **evals:** the routing suite and gate over the demo's three databases (R3) ([#323](https://github.com/nadeem4/nl2sql/issues/323)) ([5920d3f](https://github.com/nadeem4/nl2sql/commit/5920d3f11e5d847c32c0b4c0541e5f901a9780db))
* **events:** run events on the bus, the caller-safe stream and engine.events (E1) ([#314](https://github.com/nadeem4/nl2sql/issues/314)) ([62e2e0d](https://github.com/nadeem4/nl2sql/commit/62e2e0db0d6194f9177ebde5768f3f04d990921a))
* **events:** the Phase 2 events published: index, datasource, parse retry, policy denials, purge (E4) ([#321](https://github.com/nadeem4/nl2sql/issues/321)) ([ea8d939](https://github.com/nadeem4/nl2sql/commit/ea8d939027b6ddda0de9ace835c58128e98e4f4c))
* **events:** Wave A foundation — event bus, catalog, run scope, registry kinds (A0) ([#313](https://github.com/nadeem4/nl2sql/issues/313)) ([653fa73](https://github.com/nadeem4/nl2sql/commit/653fa73c76eb2bc764795d19e9ee6e3c78e4617e))
* **hooks:** approval and the public pinned-plan entry (H2) ([#319](https://github.com/nadeem4/nl2sql/issues/319)) ([35d7f1b](https://github.com/nadeem4/nl2sql/commit/35d7f1b4e41abad298a5323233bdf0c2e1b9cf47))
* **hooks:** the hook runner, the six points and the boundary test (H1) ([#315](https://github.com/nadeem4/nl2sql/issues/315)) ([f839a15](https://github.com/nadeem4/nl2sql/commit/f839a1525277f60b5f5d21c9b2b46f87833dd8fb))
* **mcp:** MCP server over stdio and the caller's schema (M1) ([#325](https://github.com/nadeem4/nl2sql/issues/325)) ([0277fe5](https://github.com/nadeem4/nl2sql/commit/0277fe5f9b62a144f25ed5ca131f018b9ae74cd7))
* **observers:** log, audit and otel observers; llm.call and llm.changed on the bus (E2) ([#318](https://github.com/nadeem4/nl2sql/issues/318)) ([8fd2b60](https://github.com/nadeem4/nl2sql/commit/8fd2b60ff8c1cbd01f21ba8e411343b37dee9e20))
* **routing:** one entry per example phrasing, votes, rank fusion and the routing settings (R1) ([#316](https://github.com/nadeem4/nl2sql/issues/316)) ([d924099](https://github.com/nadeem4/nl2sql/commit/d924099c6c00adbe1a0ae013aeb97b7e46d79c4d))
* **routing:** the sub-query limit and join-key coercion (R2) ([#322](https://github.com/nadeem4/nl2sql/issues/322)) ([d66419b](https://github.com/nadeem4/nl2sql/commit/d66419bbc6c628a589c47988ebcdbd811c043f4d))
* **sanity:** join fan-out, all-null and value checks; typed RunWarning (S1) ([#324](https://github.com/nadeem4/nl2sql/issues/324)) ([646596e](https://github.com/nadeem4/nl2sql/commit/646596e053bb472756d2c4ed69844aaaa6126530))
* **schema:** schema drift: check, diff, impact (D1) ([#327](https://github.com/nadeem4/nl2sql/issues/327)) ([5d37713](https://github.com/nadeem4/nl2sql/commit/5d37713f99cf1c1e27d1594fc402e0c71d1073f6))


### Documentation

* **architecture:** Wave A design (events, observers, hooks, evals, routing) ([#308](https://github.com/nadeem4/nl2sql/issues/308)) ([a465654](https://github.com/nadeem4/nl2sql/commit/a46565411d0b66dbbd3b5ca541f5e49e37892ec1))
* **architecture:** Wave B design (knowledge, profiler, clarify, cost guard) ([#310](https://github.com/nadeem4/nl2sql/issues/310)) ([36f6d20](https://github.com/nadeem4/nl2sql/commit/36f6d2017077317aef85a291fe8ae73078f1935d))
* **architecture:** Wave C design (conversation, charts, dashboards, launchpad, MCP over HTTP) ([#311](https://github.com/nadeem4/nl2sql/issues/311)) ([4f86a9a](https://github.com/nadeem4/nl2sql/commit/4f86a9af9c9c9fafa2b7e1b77cc155a1862bcd4b))
* **architecture:** Wave D design (API v1, web app, Compose, terminal app) ([#312](https://github.com/nadeem4/nl2sql/issues/312)) ([02e4a39](https://github.com/nadeem4/nl2sql/commit/02e4a3907bb55f715f6a024b2e9632ad239619f3))
* correct two outdated rules in CLAUDE.md ([#306](https://github.com/nadeem4/nl2sql/issues/306)) ([782b291](https://github.com/nadeem4/nl2sql/commit/782b2911f376e1cf97170bbc42d7e29f3c9a2324))
* **release:** notes for 0.7.0 ([#331](https://github.com/nadeem4/nl2sql/issues/331)) ([544e48e](https://github.com/nadeem4/nl2sql/commit/544e48ef4402f3cca1a42da10be763c9d689ba6d))

## [0.6.0](https://github.com/nadeem4/nl2sql/compare/v0.5.0...v0.6.0) (2026-10-15)


### Features

* **datasources:** engine.datasources.types(), test(Datasource) and add() validating the index scope (phase 2, B5) ([#296](https://github.com/nadeem4/nl2sql/issues/296)) ([ee2756f](https://github.com/nadeem4/nl2sql/commit/ee2756fe36bc9b419a6a0b677d2f1b13c726abfa))
* **embeddings:** providers v2 with openai_compatible, base_url, dimension and the one-call probe ([#281](https://github.com/nadeem4/nl2sql/issues/281)) ([c6a72a5](https://github.com/nadeem4/nl2sql/commit/c6a72a5a7ad73aa28fdbe8f908cd037c91655ee3))
* **llm:** parse-and-retry for structured answers, with parse_retries and MODEL_OUTPUT_INVALID ([#287](https://github.com/nadeem4/nl2sql/issues/287)) ([b131e82](https://github.com/nadeem4/nl2sql/commit/b131e82ce286509e78d0abe71746cbb2f323e280))
* **llm:** providers v2 with openai_compatible, its openrouter and ollama presets, and the agent fields ([#284](https://github.com/nadeem4/nl2sql/issues/284)) ([2fb2605](https://github.com/nadeem4/nl2sql/commit/2fb26052d19ce3bf62a917554b177f8f2070cecf))
* **policies:** allow and deny globs, one Scope, denied tables hidden from the planner (P1) ([#285](https://github.com/nadeem4/nl2sql/issues/285)) ([e28cd44](https://github.com/nadeem4/nl2sql/commit/e28cd446cb457510f5dac10496f43f85e25cfc0a))
* **policies:** PolicyCheck extension point, intersected with the roles and failing closed (P2) ([#288](https://github.com/nadeem4/nl2sql/issues/288)) ([d5e5d89](https://github.com/nadeem4/nl2sql/commit/d5e5d895b5edec2b5cd7cb5c4ee00b3d84d5639f))
* **sdk:** adapter SDK v2 contract, strict connection models and the index scope (B1) ([#291](https://github.com/nadeem4/nl2sql/issues/291)) ([59d0f47](https://github.com/nadeem4/nl2sql/commit/59d0f47897045b6b944669ec2bd004fb2b0d6e9c))
* **sdk:** read-only sessions, statement timeouts and connection stages, honestly (B3) ([#297](https://github.com/nadeem4/nl2sql/issues/297)) ([fc60e32](https://github.com/nadeem4/nl2sql/commit/fc60e32cc04be937e6acbc74b1c4bb90c38a9b71))
* **sdk:** the adapter conformance kit, run on all five built-ins (phase 2, B4) ([#298](https://github.com/nadeem4/nl2sql/issues/298)) ([6846c09](https://github.com/nadeem4/nl2sql/commit/6846c094d9800bc78f36a28acf4e39efbf027dd9))
* **sdk:** the SQLAlchemy base in the SDK, reflect once, catalog row estimates and index issues (B2) ([#295](https://github.com/nadeem4/nl2sql/issues/295)) ([ebdc040](https://github.com/nadeem4/nl2sql/commit/ebdc040978b001f8fa9c5e05bcf328f1a7edcb77))
* **storage:** index store, namespaces per model and the build pointer as the commit (phase 2, S2) ([#289](https://github.com/nadeem4/nl2sql/issues/289)) ([13d971f](https://github.com/nadeem4/nl2sql/commit/13d971f909b243904a718d8d6366cfc9692d7500))
* **storage:** sqlite state store with immutable snapshots, migrations and the index lock (phase 2, S3) ([#293](https://github.com/nadeem4/nl2sql/issues/293)) ([4c09c45](https://github.com/nadeem4/nl2sql/commit/4c09c4557c9d72f572c8956a6c341d08a761fe0c))
* **storage:** store interfaces, the four storage slots and the store registry kind (phase 2, S1) ([#283](https://github.com/nadeem4/nl2sql/issues/283)) ([4d66b99](https://github.com/nadeem4/nl2sql/commit/4d66b99fac95a58c1ed9c06bc4633bd2aa1650a0))
* **storage:** trace and result stores, retention and engine.storage (phase 2, S4) ([#292](https://github.com/nadeem4/nl2sql/issues/292)) ([cc7aaf6](https://github.com/nadeem4/nl2sql/commit/cc7aaf6a3c7e3a84fbe57911d0fb9c4808431c91))
* **testbed:** real-database test bed, realdb marker and CI workflow ([#286](https://github.com/nadeem4/nl2sql/issues/286)) ([a883ef8](https://github.com/nadeem4/nl2sql/commit/a883ef83e5b624d9dd83dfa18120ad84d05f2504))
* **testbed:** scripts/testbed.py drives the GCP test-bed VM through IAP (T2) ([#290](https://github.com/nadeem4/nl2sql/issues/290)) ([632f417](https://github.com/nadeem4/nl2sql/commit/632f4172db891db0503765e4c5049a30a8084fda))


### Bug Fixes

* **adapters:** install cryptography for MySQL 8 logins, and pick the newest SQL Server ODBC driver ([#303](https://github.com/nadeem4/nl2sql/issues/303)) ([a9444a8](https://github.com/nadeem4/nl2sql/commit/a9444a8e1c5088376f17e63f4d0775a944021810))
* **docs:** resolve conflict markers in the SDK page and fail CI on any left behind ([#294](https://github.com/nadeem4/nl2sql/issues/294)) ([e7af71f](https://github.com/nadeem4/nl2sql/commit/e7af71f71b1d56b5334b19e11845be1ef6e07429))
* **duckdb:** read DuckDB's own catalog, so schema reading works on SQLAlchemy 2.1 ([#300](https://github.com/nadeem4/nl2sql/issues/300)) ([35c355b](https://github.com/nadeem4/nl2sql/commit/35c355bf6e10c9946113cbbbf87c27f9f88aee49))
* **testbed:** keep mssql-init running so a second up --wait passes, and write the VM startup script with LF endings ([#301](https://github.com/nadeem4/nl2sql/issues/301)) ([ae76887](https://github.com/nadeem4/nl2sql/commit/ae76887f222a3e622210bd096c174210d79f2259))
* **testbed:** pass the passwords through plink, retry the first connection and wait for Docker ([#299](https://github.com/nadeem4/nl2sql/issues/299)) ([a19c1f9](https://github.com/nadeem4/nl2sql/commit/a19c1f975c1870831f69303fd74e72f92884f849))
* **testbed:** send the passwords to the VM without a carriage return, and strip one there ([#304](https://github.com/nadeem4/nl2sql/issues/304)) ([86083d2](https://github.com/nadeem4/nl2sql/commit/86083d235f2d7a08084d3c3050597d7c9f2449ed))
* **testbed:** stop the servers cleanly before the VM, and wait for SQL Server's databases to come online ([#302](https://github.com/nadeem4/nl2sql/issues/302)) ([94ef5a5](https://github.com/nadeem4/nl2sql/commit/94ef5a51ae6f99ade2ab1126f552b8ca5a80e2f6))


### Documentation

* **architecture:** phase 2 design — test bed, storage, providers, policies, adapter SDK v2 ([#279](https://github.com/nadeem4/nl2sql/issues/279)) ([ba0a2f6](https://github.com/nadeem4/nl2sql/commit/ba0a2f6212806e530b770fa4e481de7895f7f19e))
* **release:** notes for 0.6.0 ([#305](https://github.com/nadeem4/nl2sql/issues/305)) ([219e882](https://github.com/nadeem4/nl2sql/commit/219e882e0ef599c2b75e4141f2e41f3d4f8c3a4a))

## [0.5.0](https://github.com/nadeem4/nl2sql/compare/v0.4.1...v0.5.0) (2026-10-14)


### Features

* **cli:** callers onto the new config (configuration v2, PR 7b) ([#272](https://github.com/nadeem4/nl2sql/issues/272)) ([712d849](https://github.com/nadeem4/nl2sql/commit/712d84912ede4d092a17985715a3070817dcd0a1))
* **cli:** nl2sql config check (configuration v2, PR 8) ([#273](https://github.com/nadeem4/nl2sql/issues/273)) ([f710d52](https://github.com/nadeem4/nl2sql/commit/f710d52050321d057a897577d17f08a684e31900))
* **cli:** nl2sql init writes the split config layout (configuration v2, PR 6) ([#267](https://github.com/nadeem4/nl2sql/issues/267)) ([4d00417](https://github.com/nadeem4/nl2sql/commit/4d004175e0d7a24df2034b6f39cf351772e44b4c))
* **config:** config models and load_config (configuration v2, PR 2) ([#264](https://github.com/nadeem4/nl2sql/issues/264)) ([4a581eb](https://github.com/nadeem4/nl2sql/commit/4a581eb9838d045d98c8ff25a8f4f4bdc8ae9719))
* **engine:** build the engine from a Config (configuration v2, PR 7a) ([#268](https://github.com/nadeem4/nl2sql/issues/268)) ([00b4d4b](https://github.com/nadeem4/nl2sql/commit/00b4d4b65aaaf1a4f294830f7da7de4e337cf6ac))
* **facade:** engine.feedback, traces.get, about(), llm.providers() and the playground on the public API (configuration v2, PR 10b) ([#275](https://github.com/nadeem4/nl2sql/issues/275)) ([6ed0b8c](https://github.com/nadeem4/nl2sql/commit/6ed0b8cb08c7d733b998bc429f978215772b24fd))
* **facade:** namespaces on NL2SQL, PUT /api/v1/settings/llm, and the CLI on the public API (configuration v2, PR 10a) ([#274](https://github.com/nadeem4/nl2sql/issues/274)) ([1459d5c](https://github.com/nadeem4/nl2sql/commit/1459d5c447094a872fb5b57b9015e25617982d45))
* **llm:** per-engine LLM client cache and engine.llm.replace (configuration v2, PR 9) ([#270](https://github.com/nadeem4/nl2sql/issues/270)) ([884eeb6](https://github.com/nadeem4/nl2sql/commit/884eeb6ce3c8d7baac01954210d47b2fd25b74aa))
* **registry:** the extension registry (configuration v2, PR 5) ([#266](https://github.com/nadeem4/nl2sql/issues/266)) ([8c9ff47](https://github.com/nadeem4/nl2sql/commit/8c9ff478227a8f24a1441257fc13f6881fcb79c3))
* **sdk:** adapter and secret provider interfaces for the registry ([#265](https://github.com/nadeem4/nl2sql/issues/265)) ([38ef321](https://github.com/nadeem4/nl2sql/commit/38ef321e36590acc297cce22d30d7f2241856556))


### Bug Fixes

* **config:** mask secrets in the config, log level from .env, no environment writes in scripts ([#277](https://github.com/nadeem4/nl2sql/issues/277)) ([6e0a66a](https://github.com/nadeem4/nl2sql/commit/6e0a66aed81d8159ff9089e6756db2850224dd6c))


### Performance Improvements

* **engine:** make import nl2sql light ([#263](https://github.com/nadeem4/nl2sql/issues/263)) ([5f4e964](https://github.com/nadeem4/nl2sql/commit/5f4e964a74102a43eed2b1e7dd93aa77296ebea0))


### Documentation

* **architecture:** configuration v2 and the extension registry (phase 1 design) ([#260](https://github.com/nadeem4/nl2sql/issues/260)) ([277d957](https://github.com/nadeem4/nl2sql/commit/277d957db2570080a4f3884fce5dc9acec1f5aa8))
* **release:** notes for 0.5.0 ([#278](https://github.com/nadeem4/nl2sql/issues/278)) ([4556b78](https://github.com/nadeem4/nl2sql/commit/4556b780afba84b52243791c6295347128e793f3))


### Code Refactoring

* **pipeline:** read settings from the runtime, not the global (configuration v2, PR 11) ([#269](https://github.com/nadeem4/nl2sql/issues/269)) ([65aaa23](https://github.com/nadeem4/nl2sql/commit/65aaa23fe725837d9a9e857eb13cc508b5b6c41b))
* **runtime:** delete the settings global and every environment write (configuration v2, PR 13) ([#276](https://github.com/nadeem4/nl2sql/issues/276)) ([13ca8b7](https://github.com/nadeem4/nl2sql/commit/13ca8b7b41a8cafaf70f15b6eb892291e578184f))
* **runtime:** indexing, tracing and evaluation read settings from the runtime (configuration v2, PR 12) ([#271](https://github.com/nadeem4/nl2sql/issues/271)) ([0713a3e](https://github.com/nadeem4/nl2sql/commit/0713a3e31b2e32e32a540ae66e8118d24a6da4cc))
* **sdk:** rename nl2sql-adapter-sdk to nl2sql-sdk ([#262](https://github.com/nadeem4/nl2sql/issues/262)) ([94e0041](https://github.com/nadeem4/nl2sql/commit/94e0041257e42302d69d57df8f12d047df108f5e))

## [0.4.1](https://github.com/nadeem4/nl2sql/compare/v0.4.0...v0.4.1) (2026-10-13)


### Bug Fixes

* **adapters:** escape connection credentials and enforce statement timeouts ([#249](https://github.com/nadeem4/nl2sql/issues/249)) ([20639a2](https://github.com/nadeem4/nl2sql/commit/20639a2c6232587ae557c5f7172bad014b0799bf))
* **adapters:** profile each column in isolation so one odd column cannot fail indexing ([#256](https://github.com/nadeem4/nl2sql/issues/256)) ([15835d7](https://github.com/nadeem4/nl2sql/commit/15835d7100029abbe89d8bab40538760137a0e0f))
* **api:** require an admin role on the routes that change the process ([#250](https://github.com/nadeem4/nl2sql/issues/250)) ([123e8d5](https://github.com/nadeem4/nl2sql/commit/123e8d5bec5f2d6e992a05c02c72021aabef17ee))
* **cli:** make the setup wizard's adapter check work for bring-your-own databases ([#258](https://github.com/nadeem4/nl2sql/issues/258)) ([15a92d0](https://github.com/nadeem4/nl2sql/commit/15a92d0b0bf016135ae91f40f5bd754442e9c10f))
* **evaluation:** run tier 2 with curated answers off and record each answer's source ([#255](https://github.com/nadeem4/nl2sql/issues/255)) ([4e37639](https://github.com/nadeem4/nl2sql/commit/4e37639fa71529fd559e5e07819c21a2761072a2))
* **indexing:** make enrichment opt-in and keep registered descriptions ([#254](https://github.com/nadeem4/nl2sql/issues/254)) ([4355435](https://github.com/nadeem4/nl2sql/commit/43554358e2a9c0b3ff87b64ef9b6db2631bb46a5))
* **rbac:** decide table access on datasource, schema and table ([#257](https://github.com/nadeem4/nl2sql/issues/257)) ([0ffac2b](https://github.com/nadeem4/nl2sql/commit/0ffac2bb4af798b8ee239a1da058dee54b61c354))
* **routing:** forward only the verdict's datasources and give the decomposer their context ([#252](https://github.com/nadeem4/nl2sql/issues/252)) ([5c552e6](https://github.com/nadeem4/nl2sql/commit/5c552e6ac0c0830debc01a8040e5aaa231cb5c39))
* **startup:** fail on a broken secret provider, degrade on a broken datasource ([#251](https://github.com/nadeem4/nl2sql/issues/251)) ([3f3005c](https://github.com/nadeem4/nl2sql/commit/3f3005c0758647de5e74fe812b9fcca0b54d2872))


### Documentation

* **release:** notes for 0.4.1 ([#259](https://github.com/nadeem4/nl2sql/issues/259)) ([e421cc5](https://github.com/nadeem4/nl2sql/commit/e421cc5b1b5a0480942492f87fec1466df4591e8))

## [0.4.0](https://github.com/nadeem4/nl2sql/compare/v0.3.0...v0.4.0) (2026-10-13)


### Features

* **curated:** curated.yaml config and nl2sql curated validate ([#240](https://github.com/nadeem4/nl2sql/issues/240)) ([1f3a6e8](https://github.com/nadeem4/nl2sql/commit/1f3a6e85c81f08897232df49fd467e99fa256724))
* **curated:** index verified queries and retrieve candidates ([#242](https://github.com/nadeem4/nl2sql/issues/242)) ([c6f08a2](https://github.com/nadeem4/nl2sql/commit/c6f08a23173b774c7269b2f0cc8dd37b13d1dcbe))
* **curated:** match verified queries and run them with bound parameters ([#243](https://github.com/nadeem4/nl2sql/issues/243)) ([ec0646b](https://github.com/nadeem4/nl2sql/commit/ec0646b64c3416c684054c1580d0688b86ba8c8a))
* **curated:** preferred tables in datasources.yaml ([#244](https://github.com/nadeem4/nl2sql/issues/244)) ([4636186](https://github.com/nadeem4/nl2sql/commit/4636186033b6b449199098895bb69cb8a2a5e658))
* **curated:** propose verified queries from thumbs-up feedback ([#246](https://github.com/nadeem4/nl2sql/issues/246)) ([027cfc4](https://github.com/nadeem4/nl2sql/commit/027cfc48682129bae6454edf3445b733f8667e3b))
* **curated:** verified badge, demo verified queries and nl2sql curated test ([#245](https://github.com/nadeem4/nl2sql/issues/245)) ([9473ea3](https://github.com/nadeem4/nl2sql/commit/9473ea36a26185de0fda73dd5ea57aa590b33ba2))


### Bug Fixes

* **demo:** record the curated matcher's choices for the keyless demo ([#248](https://github.com/nadeem4/nl2sql/issues/248)) ([f835818](https://github.com/nadeem4/nl2sql/commit/f835818ac03d07afe54e8f0a59d4958ae442a0f8))


### Documentation

* **design:** curated answers ([#239](https://github.com/nadeem4/nl2sql/issues/239)) ([72fe4f3](https://github.com/nadeem4/nl2sql/commit/72fe4f36005c21bf71176a3be241d6f9f7be211a))
* **release:** notes for 0.4.0 ([#247](https://github.com/nadeem4/nl2sql/issues/247)) ([bd48110](https://github.com/nadeem4/nl2sql/commit/bd48110bac6f0e95058260a477edf86941cc04e1))

## [0.3.0](https://github.com/nadeem4/nl2sql/compare/v0.2.4...v0.3.0) (2026-10-12)


### Features

* **answer:** send the answer model a 20-row preview and flag truncated results ([#231](https://github.com/nadeem4/nl2sql/issues/231)) ([3677d2e](https://github.com/nadeem4/nl2sql/commit/3677d2e9e9d200c993f46faf0586bd2be7d12741))
* **api:** page through and download a run's saved result by request id ([#235](https://github.com/nadeem4/nl2sql/issues/235)) ([cdf2dd0](https://github.com/nadeem4/nl2sql/commit/cdf2dd06ca11ce23324506fd97315cd49465a874))
* **api:** stream a run's progress and answer as Server-Sent Events ([#237](https://github.com/nadeem4/nl2sql/issues/237)) ([36ac61e](https://github.com/nadeem4/nl2sql/commit/36ac61e7c24f9094ef43295c162427765a8fb007))
* **executor:** fetch results in batches and save the combined result as an artifact ([#234](https://github.com/nadeem4/nl2sql/issues/234)) ([a834e28](https://github.com/nadeem4/nl2sql/commit/a834e287cc12b7ff9cb9c4bbeb498610648d4d18))
* **playground:** page through every saved result and download it whole ([#236](https://github.com/nadeem4/nl2sql/issues/236)) ([e9ca450](https://github.com/nadeem4/nl2sql/commit/e9ca4501e1a1938b7c7d562a215c0667e2b5b8b9))


### Bug Fixes

* **config:** honour a datasource's top-level row_limit ([#233](https://github.com/nadeem4/nl2sql/issues/233)) ([9e5ede2](https://github.com/nadeem4/nl2sql/commit/9e5ede21f22fc3dd7e28cb4650c20e3b59027777))


### Documentation

* **release:** notes for 0.3.0 ([#238](https://github.com/nadeem4/nl2sql/issues/238)) ([c964f77](https://github.com/nadeem4/nl2sql/commit/c964f7775fc132cdecc23dbf53658b6a0a566eff))

## [0.2.4](https://github.com/nadeem4/nl2sql/compare/v0.2.3...v0.2.4) (2026-10-12)


### Bug Fixes

* **cache:** key cached plans on the run date ([#228](https://github.com/nadeem4/nl2sql/issues/228)) ([cd37f43](https://github.com/nadeem4/nl2sql/commit/cd37f43fc1f27dcb66b5fcbcb200f0c6abedf0fc))


### Documentation

* **release:** notes for 0.2.4 ([#230](https://github.com/nadeem4/nl2sql/issues/230)) ([4c7af38](https://github.com/nadeem4/nl2sql/commit/4c7af386b0ee41942a0b72c50e1ea00a4180f129))

## [0.2.3](https://github.com/nadeem4/nl2sql/compare/v0.2.2...v0.2.3) (2026-10-12)


### Bug Fixes

* **executor:** say when an empty result filtered on a value that does not exist ([#226](https://github.com/nadeem4/nl2sql/issues/226)) ([a4f40f3](https://github.com/nadeem4/nl2sql/commit/a4f40f39d829870cbb8d2a3c8ce247a7448b5197))
* **generator:** render PlanModel.offset ([#221](https://github.com/nadeem4/nl2sql/issues/221)) ([1fb45b4](https://github.com/nadeem4/nl2sql/commit/1fb45b4193284732fc1e6960d9298b200910a7f7))
* **indexing:** keep every value of a text column with few distinct values ([#222](https://github.com/nadeem4/nl2sql/issues/222)) ([3a34679](https://github.com/nadeem4/nl2sql/commit/3a3467947c89bdfa6c496b674eb17d179fb7b02f))
* **planner:** send each date and numeric column's data range to the planner ([#224](https://github.com/nadeem4/nl2sql/issues/224)) ([e84d214](https://github.com/nadeem4/nl2sql/commit/e84d2145463c5dfc446c324185468f9325ff0ac1))
* **planner:** tell the planner and refiner today's date ([#220](https://github.com/nadeem4/nl2sql/issues/220)) ([ed0e08e](https://github.com/nadeem4/nl2sql/commit/ed0e08e9db92ad5ad5e0b677fb8af4ee3dad67a5))
* **planner:** the date-function allow-list agrees with the planner prompt ([#218](https://github.com/nadeem4/nl2sql/issues/218)) ([1e3248b](https://github.com/nadeem4/nl2sql/commit/1e3248b05341b2ca7cb81f2d201c1e898a4530c3))
* **sql-agent:** retry a query the database rejects as wrong ([#223](https://github.com/nadeem4/nl2sql/issues/223)) ([6b62347](https://github.com/nadeem4/nl2sql/commit/6b6234721ccd6cc445f80a6315aae06ee6bc9e12))
* **validator:** turn strict column checking on by default ([#219](https://github.com/nadeem4/nl2sql/issues/219)) ([6c7fad5](https://github.com/nadeem4/nl2sql/commit/6c7fad548153561e7846c9f407f589c3618f4f8b))


### Documentation

* **release:** a release page for every version, written by a skill and gated in CI ([#216](https://github.com/nadeem4/nl2sql/issues/216)) ([ab84f18](https://github.com/nadeem4/nl2sql/commit/ab84f18109f80a2b5f92de2764d70a48e5a4d253))
* **release:** notes for 0.2.3 ([#227](https://github.com/nadeem4/nl2sql/issues/227)) ([035aeae](https://github.com/nadeem4/nl2sql/commit/035aeaedd9881273d26f27993412ad2420200ed1))

## [0.2.2](https://github.com/nadeem4/nl2sql/compare/v0.2.1...v0.2.2) (2026-10-10)


### Features

* **demo:** serve the link preview and release checks from nl2sql.codewithnk.com ([#212](https://github.com/nadeem4/nl2sql/issues/212)) ([d04bc74](https://github.com/nadeem4/nl2sql/commit/d04bc74059a7238d2c24528e46bbf1ef22bc74a6))


### Bug Fixes

* **aggregator:** refuse a join on a sub-query cut short by the row cap ([#215](https://github.com/nadeem4/nl2sql/issues/215)) ([8ae6212](https://github.com/nadeem4/nl2sql/commit/8ae6212b5deaa47c524da9382c87919e5b604b67))
* **decomposer:** refuse a join or compare that lost one of its inputs ([#214](https://github.com/nadeem4/nl2sql/issues/214)) ([ae2a050](https://github.com/nadeem4/nl2sql/commit/ae2a0507395f2272d08a5a8d928fbfcaa1050233))
* **execution:** write one result artifact per sub-query ([#213](https://github.com/nadeem4/nl2sql/issues/213)) ([24dba37](https://github.com/nadeem4/nl2sql/commit/24dba370ec73cf353f94a1c453612b3766abeab5))


### Documentation

* link the live demo at nl2sql.codewithnk.com ([#210](https://github.com/nadeem4/nl2sql/issues/210)) ([2465926](https://github.com/nadeem4/nl2sql/commit/24659261bc0ba15a01893751a0cdfb2a8c5f4d78))


### Miscellaneous Chores

* release 0.2.2 ([27adaf6](https://github.com/nadeem4/nl2sql/commit/27adaf62fbed497e6648e849c61ede42423eb30e))

## [0.2.1](https://github.com/nadeem4/nl2sql/compare/v0.2.0...v0.2.1) (2026-10-10)


### Bug Fixes

* release image on Python 3.12, and a Space deploy that survives a lagging PyPI and never passes broken ([#208](https://github.com/nadeem4/nl2sql/issues/208)) ([41dbfb0](https://github.com/nadeem4/nl2sql/commit/41dbfb0a9d6364a2a6aff62044319d736f13c2e4))

## [0.2.0](https://github.com/nadeem4/nl2sql/compare/v0.1.2...v0.2.0) (2026-10-10)


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
* **demo:** ship real recorded answers for the 20 guided questions ([#205](https://github.com/nadeem4/nl2sql/issues/205)) ([490c352](https://github.com/nadeem4/nl2sql/commit/490c352d9b50e8f80fd58dbcb4e5c17d7eb495a3))
* **demo:** vendor the Chinook database and add DemoManager.setup_chinook ([#91](https://github.com/nadeem4/nl2sql/issues/91)) ([22cd257](https://github.com/nadeem4/nl2sql/commit/22cd25707596a3bccbd92e7e3e671c844fa5bd6b))
* **engine:** classify provider failures so callers can explain them ([#181](https://github.com/nadeem4/nl2sql/issues/181)) ([cc53a7d](https://github.com/nadeem4/nl2sql/commit/cc53a7d18edd0a951ea9e5c4ea9baa675b9c6870))
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
* **playground:** a home page ([#198](https://github.com/nadeem4/nl2sql/issues/198)) ([f04a4f5](https://github.com/nadeem4/nl2sql/commit/f04a4f5157ed5b132aa76b8402914acd220e004d))
* **playground:** a Pipeline page showing what each step does ([#171](https://github.com/nadeem4/nl2sql/issues/171)) ([8535e21](https://github.com/nadeem4/nl2sql/commit/8535e21ea642ff91c0b54244ff60462d6293c303))
* **playground:** a plan-spine favicon across the playground, docs and social card ([#189](https://github.com/nadeem4/nl2sql/issues/189)) ([9a6e823](https://github.com/nadeem4/nl2sql/commit/9a6e82378eaee9e1d9de89190b31ac8329e35939))
* **playground:** a three-phase Pipeline, a calmer Settings, schema-first rail ([#185](https://github.com/nadeem4/nl2sql/issues/185)) ([535a8fc](https://github.com/nadeem4/nl2sql/commit/535a8fc768e86fe2fd9487b3ed61dbedf25dd4cd))
* **playground:** add a settings panel for the API key and a model per LLM node ([#109](https://github.com/nadeem4/nl2sql/issues/109)) ([0e60f4e](https://github.com/nadeem4/nl2sql/commit/0e60f4e52191913c957a042c07bb72d3c0d3d123))
* **playground:** answer-first Ask page, chip suggestions, a run you can stop ([#186](https://github.com/nadeem4/nl2sql/issues/186)) ([09c2ef3](https://github.com/nadeem4/nl2sql/commit/09c2ef351117b8c06dfbb6072153d503b3854c21))
* **playground:** choose a model per step in the hosted demo, with a key per provider ([#173](https://github.com/nadeem4/nl2sql/issues/173)) ([2220e74](https://github.com/nadeem4/nl2sql/commit/2220e741364b066e8094082aa1b93cbcbd84dcf7))
* **playground:** give Settings and Retrieval their own pages with a header nav ([#154](https://github.com/nadeem4/nl2sql/issues/154)) ([4e6c6f5](https://github.com/nadeem4/nl2sql/commit/4e6c6f576a1c42f02be7fc5b47cda3abe5868ef4))
* **playground:** let the demo show each of its databases ([#172](https://github.com/nadeem4/nl2sql/issues/172)) ([67f06bd](https://github.com/nadeem4/nl2sql/commit/67f06bde6532b8fa448f290ff4e27e2595d94e87))
* **playground:** Porcelain palette, type and spacing scales, one raised surface ([#182](https://github.com/nadeem4/nl2sql/issues/182)) ([fe4334d](https://github.com/nadeem4/nl2sql/commit/fe4334dd997032ea994ddecd1b6d1422b67db190))
* **playground:** redesign the demo page around one run, and add a Debug toggle ([#102](https://github.com/nadeem4/nl2sql/issues/102)) ([2cfe121](https://github.com/nadeem4/nl2sql/commit/2cfe1210b1dd7dbc77219620b04070edaf644567))
* **playground:** SQL card, rows with magnitude bars, a readable ledger ([#184](https://github.com/nadeem4/nl2sql/issues/184)) ([1af556e](https://github.com/nadeem4/nl2sql/commit/1af556e9e7099a22271233380efdd22a8034c8cb))
* **playground:** stop that stops, motion and touch, finish review ([#187](https://github.com/nadeem4/nl2sql/issues/187)) ([201f99f](https://github.com/nadeem4/nl2sql/commit/201f99fa94ac2e8283572bfa7a22f62366314e2a))
* **playground:** tell a new visitor what the demo needs before they ask ([#170](https://github.com/nadeem4/nl2sql/issues/170)) ([4ff7dbd](https://github.com/nadeem4/nl2sql/commit/4ff7dbdcbde1c41296a2ccd03fea7cd5e30b82dd))
* **playground:** try the hosted demo without a key ([#197](https://github.com/nadeem4/nl2sql/issues/197)) ([2abc16d](https://github.com/nadeem4/nl2sql/commit/2abc16d5c7918a093edc18df9774a08b3da67574))
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
* **decomposer:** describe the tool so Claude fills it instead of wrapping it ([#202](https://github.com/nadeem4/nl2sql/issues/202)) ([630633a](https://github.com/nadeem4/nl2sql/commit/630633af086793530815cb4b327a76fc38148ce3))
* **decomposer:** reject a post-combine op on a column the combine lacks, and ask again ([#203](https://github.com/nadeem4/nl2sql/issues/203)) ([6d74763](https://github.com/nadeem4/nl2sql/commit/6d7476375e83c531d5922ca3f9fa705b1477112a))
* **decomposer:** reject SQL syntax, not English words that happen to be SQL keywords ([#105](https://github.com/nadeem4/nl2sql/issues/105)) ([022a3b3](https://github.com/nadeem4/nl2sql/commit/022a3b3bd9f04fe208337693aaaaed71546d3a0c))
* **decomposer:** two identical sub-queries no longer fail the run ([#190](https://github.com/nadeem4/nl2sql/issues/190)) ([a2050f0](https://github.com/nadeem4/nl2sql/commit/a2050f0e6dd39c904eb416fe6b09ee19ff38a23a))
* **demo:** be honest about replay mode, make --record replayable, and ship license notices ([#117](https://github.com/nadeem4/nl2sql/issues/117)) ([5430943](https://github.com/nadeem4/nl2sql/commit/54309436c15831dc5162105cdb113f47646ad68b))
* **demo:** label a replayed run with the model that recorded it ([#207](https://github.com/nadeem4/nl2sql/issues/207)) ([f3017e4](https://github.com/nadeem4/nl2sql/commit/f3017e4314a2c59a5af933a0ce3bbdefc43fdc1a))
* **demo:** record only the guided questions whose run succeeded ([#204](https://github.com/nadeem4/nl2sql/issues/204)) ([d55361e](https://github.com/nadeem4/nl2sql/commit/d55361e333b5a00186a659080f538a00282df0ef))
* **demo:** recording survives a cp1252 stdout and stops when indexing fails ([#200](https://github.com/nadeem4/nl2sql/issues/200)) ([fdebf1d](https://github.com/nadeem4/nl2sql/commit/fdebf1d187ea28fbbcf9f946ee45bb6fec289b04))
* **eval:** identify a benchmark run by the dataset's datasources, not every registered one ([#158](https://github.com/nadeem4/nl2sql/issues/158)) ([3256dae](https://github.com/nadeem4/nl2sql/commit/3256daeb317bd9874edb5f4d4d5d47b73fad2dcd))
* **generator:** attach the unjoined side of each join, render arithmetic, stop after a denial ([#100](https://github.com/nadeem4/nl2sql/issues/100)) ([fb618ab](https://github.com/nadeem4/nl2sql/commit/fb618abb2299995f638aecf2393d837bd74f5643))
* **generator:** quote select aliases and column names that are not bare identifiers ([#133](https://github.com/nadeem4/nl2sql/issues/133)) ([460df7c](https://github.com/nadeem4/nl2sql/commit/460df7c701e557c6aa59eac90475d1018a5bc236))
* **generator:** render string concatenation as the dialect's own, never numeric + ([#132](https://github.com/nadeem4/nl2sql/issues/132)) ([e55c1dc](https://github.com/nadeem4/nl2sql/commit/e55c1dc3f901b6388121f6180a61bdb705a91af9))
* **index:** give the datasource entry its description and example questions, and refresh snapshot metadata on re-index ([#111](https://github.com/nadeem4/nl2sql/issues/111)) ([29dab2f](https://github.com/nadeem4/nl2sql/commit/29dab2fdd09f4a61111d916f37148d4660de74cc))
* **index:** rebuild without ever leaving an empty index, and show index health with a Rebuild action ([#110](https://github.com/nadeem4/nl2sql/issues/110)) ([cf0cdcb](https://github.com/nadeem4/nl2sql/commit/cf0cdcb5e12cb2eadc51f33c3a965882b0270dcb))
* **llm:** unwrap Claude tool arguments nested under input/inputs ([#201](https://github.com/nadeem4/nl2sql/issues/201)) ([93a970d](https://github.com/nadeem4/nl2sql/commit/93a970d3e0c52853e55bb7978b630fe95238a86f))
* **pipeline:** derive sub-query status from its final attempt, not its error history ([#106](https://github.com/nadeem4/nl2sql/issues/106)) ([059f45a](https://github.com/nadeem4/nl2sql/commit/059f45a0e9fe019e3e5438dd40a210ae1865d82f))
* **pipeline:** honor execute=False so plan-only runs never touch a database ([#86](https://github.com/nadeem4/nl2sql/issues/86)) ([cfa9ad0](https://github.com/nadeem4/nl2sql/commit/cfa9ad0cae42930f2ac33903b0818723b31ee3a6))
* **pipeline:** return a structured result when a sub-query fails instead of crashing or looping ([#84](https://github.com/nadeem4/nl2sql/issues/84)) ([2c9d6a0](https://github.com/nadeem4/nl2sql/commit/2c9d6a0a96fe69ef360d4248345f11be9f32da9c))
* **postgres:** name the psycopg2 driver in the connection URI ([#183](https://github.com/nadeem4/nl2sql/issues/183)) ([01fa9d5](https://github.com/nadeem4/nl2sql/commit/01fa9d5780c81f8d41cae4c77189c51f7d6a8e42))
* **rbac:** refuse strictly without leaking forbidden data, and deny unknown or empty roles cleanly ([#112](https://github.com/nadeem4/nl2sql/issues/112)) ([ec18715](https://github.com/nadeem4/nl2sql/commit/ec1871597e7912ccb95292fd5908d1ed863abb76))
* **registry:** one capability check that fails closed ([#145](https://github.com/nadeem4/nl2sql/issues/145)) ([f65e691](https://github.com/nadeem4/nl2sql/commit/f65e691a3514d2155aa659898e4da776a4a6c881))
* report global planner failures and pass the refiner the failed plan as JSON ([#107](https://github.com/nadeem4/nl2sql/issues/107)) ([e391d35](https://github.com/nadeem4/nl2sql/commit/e391d35fdecbf92ba9505f806510ff8479daeb05))
* **runtime:** enforce the pipeline timeout and keep signal handlers on the main thread ([#87](https://github.com/nadeem4/nl2sql/issues/87)) ([4174e0b](https://github.com/nadeem4/nl2sql/commit/4174e0b90f20776af31f0c1894dd5b52914b3958))
* **sdk:** a query with no user_context runs instead of crashing ([#192](https://github.com/nadeem4/nl2sql/issues/192)) ([d797490](https://github.com/nadeem4/nl2sql/commit/d79749099e70ba3a4c1ab31b98eb6accc89ce329))
* **usage:** price Anthropic prompt-cache writes at 1.25x input, not the input rate ([#193](https://github.com/nadeem4/nl2sql/issues/193)) ([badf50d](https://github.com/nadeem4/nl2sql/commit/badf50dae82379848e7dbbc71750348d7ed08126))
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
* sync documentation with recent changes ([#188](https://github.com/nadeem4/nl2sql/issues/188)) ([f8c0172](https://github.com/nadeem4/nl2sql/commit/f8c01721e75201f014a43f6662f0cf36ed842edd))
* what to expect from the demo, and a link to the live one ([#191](https://github.com/nadeem4/nl2sql/issues/191)) ([a460b5f](https://github.com/nadeem4/nl2sql/commit/a460b5f4ca7360d83848c40ce32a21787397e4b0))


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
