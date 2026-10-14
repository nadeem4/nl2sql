"""The section models of ``nl2sql.config``, built in code.

One test per rule the design gives a section (configuration_v2.md, section 2).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from nl2sql.config import (
    LLM,
    AgentLLM,
    Api,
    Config,
    Curated,
    Datasource,
    Embeddings,
    Limits,
    Policies,
    Price,
    Role,
    SecretProviderConfig,
    Secrets,
    Settings,
    Storage,
)


def minimal(**overrides) -> Config:
    sections = dict(
        datasources=[],
        llm=LLM(provider="anthropic", model="claude-sonnet-5-5"),
        embeddings=Embeddings(provider="local"),
        policies=Policies.allow_all(),
        storage=Storage(path=".nl2sql"),
    )
    sections.update(overrides)
    return Config(**sections)


# ---------------------------------------------------------------- Config

def test_config_in_code_needs_no_version():
    assert minimal().version == 2


def test_config_rejects_another_version():
    with pytest.raises(ValidationError):
        minimal(version=1)


def test_config_is_frozen():
    cfg = minimal()
    with pytest.raises(ValidationError):
        cfg.storage = Storage(path="elsewhere")
    with pytest.raises(ValidationError):
        cfg.settings.tenant_id = "acme"


@pytest.mark.parametrize("model", [
    Config, Datasource, Limits, LLM, AgentLLM, Price, Embeddings, Policies, Role, Storage,
    Secrets, Curated, Settings, Api,
])
def test_every_section_is_strict_and_frozen(model):
    assert model.model_config["extra"] == "forbid"
    assert model.model_config["frozen"] is True


def test_with_datasource_returns_a_new_config():
    cfg = minimal()
    shop = Datasource(id="shop", type="sqlite", connection={"database": "shop.db"})
    added = cfg.with_datasource(shop)
    assert cfg.datasources == []
    assert added.datasources == [shop]


def test_with_datasource_rejects_an_id_already_present():
    cfg = minimal(datasources=[Datasource(id="shop", type="sqlite")])
    with pytest.raises(ValueError, match="shop"):
        cfg.with_datasource(Datasource(id="shop", type="postgres"))


def test_datasource_ids_are_unique():
    with pytest.raises(ValidationError, match="duplicate datasource id"):
        minimal(datasources=[Datasource(id="a", type="sqlite"), Datasource(id="a", type="sqlite")])


def test_curated_off_by_copying_without_it():
    cfg = minimal(curated=Curated())
    assert cfg.model_copy(update={"curated": None}).curated is None


# ---------------------------------------------------------------- Datasource

def test_datasource_defaults():
    ds = Datasource(id="shop", type="sqlite")
    assert ds.description is None
    assert ds.connection == {}
    assert ds.limits == Limits()
    assert ds.preferred_tables == []
    assert ds.sample_questions == []
    assert ds.domains == []
    assert ds.required is False
    assert ds.adapter is None


def test_datasource_needs_a_type_or_an_adapter():
    with pytest.raises(ValidationError, match="type"):
        Datasource(id="shop")
    adapter = object()
    assert Datasource(id="dw", adapter=adapter).adapter is adapter


def test_datasource_has_no_options_bag():
    with pytest.raises(ValidationError):
        Datasource(id="shop", type="sqlite", options={"row_limit": 5})


def test_limits_are_one_place_each():
    ds = Datasource(id="shop", type="sqlite", limits={"row_limit": 1000, "timeout_ms": 8000, "max_bytes": 10})
    assert (ds.limits.row_limit, ds.limits.timeout_ms, ds.limits.max_bytes) == (1000, 8000, 10)
    with pytest.raises(ValidationError):
        Limits(row_limit=0)


def test_preferred_tables_rejects_a_blank_name():
    with pytest.raises(ValidationError, match="blank"):
        Datasource(id="shop", type="sqlite", preferred_tables=["public.orders", " "])


def test_sample_questions_belong_to_the_datasource():
    ds = Datasource(id="shop", type="sqlite", sample_questions=["Top customers?"])
    assert ds.sample_questions == ["Top customers?"]


# ---------------------------------------------------------------- LLM

def test_llm_defaults():
    llm = LLM(provider="anthropic", model="m")
    assert llm.temperature == 0.0
    assert llm.api_key is None and llm.base_url is None
    assert llm.agents == {} and llm.prices == {}


def test_llm_needs_a_provider_when_the_model_is_a_name():
    with pytest.raises(ValidationError, match="provider"):
        LLM(model="claude-sonnet-5-5")


def test_llm_model_may_be_an_object_without_a_provider():
    model = object()
    assert LLM(model=model).model is model


def test_llm_temperature_null_means_send_none():
    assert LLM(provider="openai", model="m", temperature=None).temperature is None


def test_agent_names_are_the_node_names():
    agents = {name: AgentLLM(model="x") for name in (
        "curated_matcher", "datasource_resolver", "decomposer", "ast_planner",
        "refiner", "answer_synthesizer", "indexing_enrichment")}
    assert set(LLM(provider="openai", model="m", agents=agents).agents) == set(agents)
    with pytest.raises(ValidationError):
        LLM(provider="openai", model="m", agents={"astplanner": AgentLLM(model="x")})


def test_agent_records_which_fields_it_sets():
    agent = AgentLLM(model="x", temperature=None)
    assert agent.model_fields_set == {"model", "temperature"}
    assert AgentLLM.model_fields.keys() == {"provider", "model", "temperature", "api_key", "base_url",
                                            "structured_output", "max_tokens", "timeout_s", "max_retries",
                                            "headers"}


def test_price_fields():
    price = Price(input=0.2, cached_input=0.1, output=0.6)
    assert price.cache_write is None
    with pytest.raises(ValidationError):
        Price(input=1)


# ---------------------------------------------------------------- Embeddings

def test_embeddings_needs_a_provider_or_an_instance():
    assert Embeddings(provider="local").model is None
    with pytest.raises(ValidationError, match="provider"):
        Embeddings()
    instance = object()
    assert Embeddings(instance=instance).instance is instance


# ---------------------------------------------------------------- Policies

def test_policies_allow_all_shorthand():
    policies = Policies.allow_all()
    assert policies.allow_all is True
    assert Policies().allow_all is False


def test_policies_defaults():
    policies = Policies()
    assert policies.default_role is None
    assert policies.admin_roles == ["admin"]
    assert policies.refusal_names_tables is False
    assert policies.roles == {}


@pytest.mark.parametrize("extra", [{"default_role": "viewer"}, {"roles": {"viewer": {"description": "v"}}}])
def test_allow_all_cannot_be_combined_with_roles(extra):
    with pytest.raises(ValidationError, match="allow_all"):
        Policies(allow_all=True, **extra)


def test_role_has_no_role_field():
    with pytest.raises(ValidationError):
        Role(description="d", role="analyst")
    role = Role(description="d", allowed_datasources=["sales"], allowed_tables=["sales.public.*"])
    assert role.allowed_tables == ["sales.public.*"]


@pytest.mark.parametrize("table", ["sales", "sales..orders", "sales.*.orders", "a.b.c.d"])
def test_role_rejects_a_badly_formed_table(table):
    with pytest.raises(ValidationError, match="Invalid table"):
        Role(description="d", allowed_tables=[table])


# ---------------------------------------------------------------- Storage

def test_storage_path_in_code_stays_as_given():
    assert Storage(path=".nl2sql").path == Path(".nl2sql")


# ---------------------------------------------------------------- Secrets

def test_secrets_default_to_no_providers():
    assert Secrets().providers == []


def test_env_is_a_reserved_secret_provider_id():
    with pytest.raises(ValidationError, match="reserved"):
        Secrets(providers=[SecretProviderConfig(id="env", type="aws")])


def test_secret_provider_ids_are_unique():
    with pytest.raises(ValidationError, match="duplicate"):
        Secrets(providers=[SecretProviderConfig(id="v", type="aws"), SecretProviderConfig(id="v", type="aws")])


def test_secret_provider_keeps_its_own_settings_for_the_provider_to_check():
    provider = SecretProviderConfig(id="vault", type="hashicorp", url="https://vault")
    assert provider.model_extra == {"url": "https://vault"}


# ---------------------------------------------------------------- Curated

def test_curated_body_is_keyed_by_datasource():
    curated = Curated(datasources={"shop": {"verified_queries": [{
        "id": "top_customers", "description": "d", "questions": ["Top customers?"],
        "sql": "SELECT 1"}]}})
    assert curated.datasources["shop"].verified_queries[0].id == "top_customers"


def test_curated_has_no_version():
    with pytest.raises(ValidationError):
        Curated(version=1)


# ---------------------------------------------------------------- Settings

def test_settings_defaults():
    s = Settings()
    assert s.tenant_id == "default_tenant"
    assert (s.pipeline.timeout_s, s.pipeline.plan_cache, s.pipeline.strict_columns, s.pipeline.today) == (
        60, True, True, None)
    assert (s.pipeline.retries.max, s.pipeline.retries.base_delay_s,
            s.pipeline.retries.max_delay_s, s.pipeline.retries.jitter_s) == (3, 1.0, 10.0, 0.5)
    assert s.retrieval.full_snapshot_max_tables == 15
    assert (s.results.row_limit, s.results.fetch_batch_size, s.results.preview_rows) == (100_000, 10_000, 20)
    assert (s.traces.mode, s.traces.sample_rows, s.traces.max_field_chars) == ("on_failure", 50, 20000)
    assert (s.schema_.max_versions, s.schema_.version_mismatch) == (3, "warn")
    assert (s.curated.match_top_k, s.curated.match_floor) == (5, 0.6)
    assert s.feedback.enabled is True
    assert (s.observability.exporter, s.observability.otlp_endpoint) == ("none", None)


def test_settings_schema_key_is_schema():
    assert Settings.model_validate({"schema": {"max_versions": 5}}).schema_.max_versions == 5
    assert "schema" in Settings().model_dump(by_alias=True)


def test_settings_pins_the_date():
    assert Settings(pipeline={"today": "2013-12-31"}).pipeline.today == date(2013, 12, 31)


@pytest.mark.parametrize("update", [
    {"curated": {"match_floor": 1.5}},
    {"curated": {"match_top_k": 0}},
    {"results": {"fetch_batch_size": 0}},
    {"traces": {"mode": "sometimes"}},
    {"schema": {"version_mismatch": "panic"}},
    {"observability": {"exporter": "jaeger"}},
])
def test_settings_keep_todays_validation(update):
    with pytest.raises(ValidationError):
        Settings.model_validate(update)


# ---------------------------------------------------------------- Api

def test_api_defaults():
    api = Api()
    assert (api.role_header, api.role, api.trust_body_role, api.cors_origins) == (None, None, False, [])


def test_api_in_code_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Api(admin_roles=["admin"])


def test_config_needs_its_required_sections():
    with pytest.raises(ValidationError, match="datasources"):
        Config(llm=LLM(provider="openai", model="m"), embeddings=Embeddings(provider="local"),
               policies=Policies.allow_all(), storage=Storage(path="."))


def test_default_role_must_be_one_of_the_roles():
    with pytest.raises(ValidationError, match="default_role"):
        Policies(default_role="viewer", roles={"admin": Role(description="a")})
    assert Policies(default_role="viewer", roles={"viewer": Role(description="v")}).default_role == "viewer"
