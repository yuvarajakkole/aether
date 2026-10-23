from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ProviderKind = Literal["openai_chat", "openai_responses", "anthropic", "bedrock_converse", "mock"]


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str | None = None
    api_key: str | None = Field(default=None, description="Stored encrypted; never returned")
    api_key_env: str | None = Field(default=None, description="Read the key from this environment variable instead")
    headers: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class ProviderPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] | None = None
    options: dict[str, Any] | None = None


class ModelIn(BaseModel):
    provider_id: str
    name: str = Field(min_length=1)
    display_name: str | None = None
    input_cost_per_mtok: float = Field(default=0.0, ge=0)
    output_cost_per_mtok: float = Field(default=0.0, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    supports_tools: bool = True
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class ModelPatch(BaseModel):
    display_name: str | None = None
    input_cost_per_mtok: float | None = Field(default=None, ge=0)
    output_cost_per_mtok: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    supports_tools: bool | None = None
    enabled: bool | None = None
    options: dict[str, Any] | None = None


class ToolDef(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    http: dict[str, Any] | None = Field(default=None, description="{url, method, headers, timeout_s} for real-API mode")
    mock_response: Any | None = None

    @field_validator("parameters")
    @classmethod
    def _schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError

        try:
            Draft202012Validator.check_schema(v)
        except SchemaError as exc:
            raise ValueError(f"invalid JSON schema: {exc.message}") from exc
        if v.get("type") != "object":
            raise ValueError("tool parameters must be a JSON schema with type 'object'")
        return v


class VersionIn(BaseModel):
    label: str | None = None
    system_prompt: str = ""
    tools: list[ToolDef] = Field(default_factory=list)
    default_model_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("tools")
    @classmethod
    def _unique(cls, v: list[ToolDef]) -> list[ToolDef]:
        names = [t.name for t in v]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return v


class GateRule(BaseModel):
    metric: str
    op: Literal[">=", ">", "<=", "<", "=="] = ">="
    value: float
    label: str | None = None


class Gate(BaseModel):
    name: str = "production"
    rules: list[GateRule]


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    description: str = ""
    kind: Literal["builtin", "http", "openai_chat"] = "builtin"
    endpoint: dict[str, Any] = Field(default_factory=dict, description="{url, headers, api_key, model, timeout_s} for external agents")
    owner: str | None = None
    gate: Gate | None = None
    version: VersionIn | None = None


class AgentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    endpoint: dict[str, Any] | None = None
    owner: str | None = None
    gate: Gate | None = None
    archived: bool | None = None


class TaskIn(BaseModel):
    category: str = "everyday"
    input: str = Field(min_length=1)
    scenario: str | None = None
    max_turns: int = Field(default=1, ge=1, le=20)
    fixtures: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    inject: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class TaskPatch(BaseModel):
    category: str | None = None
    input: str | None = None
    scenario: str | None = None
    max_turns: int | None = Field(default=None, ge=1, le=20)
    fixtures: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    inject: dict[str, Any] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class SuiteIn(BaseModel):
    agent_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    tasks: list[TaskIn] = Field(default_factory=list)


class GenerateIn(BaseModel):
    mix: dict[str, int]
    version_id: str | None = None
    generator_model_id: str | None = None
    instructions: str | None = Field(default=None, max_length=4000)
    sandbox_data: str | None = Field(default=None, max_length=20000, description="Records that exist in your real test APIs, for real-API runs")


class ImportIn(BaseModel):
    tasks: list[Any] = Field(max_length=5000, description="Task objects; invalid rows are skipped and counted")


class RunIn(BaseModel):
    agent_id: str
    suite_id: str
    name: str | None = None
    model_ids: list[str] = Field(default_factory=list)
    version_ids: list[str] = Field(default_factory=list)
    trials: int = Field(default=3, ge=1, le=20)
    tool_mode: Literal["simulated", "real"] = "simulated"
    failure_rate: float = Field(default=0.0, ge=0, le=1)
    judge_model_id: str | None = None
    simulator_model_id: str | None = None
    auto_reply: bool = True
    task_ids: list[str] | None = None
    categories: list[str] | None = None
    seed: str | None = None


class ReviewIn(BaseModel):
    verdict: Literal["pass", "fail"] | None
    note: str | None = Field(default=None, max_length=2000)


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CIRunIn(BaseModel):
    agent: str = Field(description="agent slug or id")
    suite: str | None = Field(default=None, description="suite id or name; defaults to the agent's newest ready suite")
    models: list[str] = Field(default_factory=list, description="model ids, names or 'provider/model'")
    version: int | None = None
    trials: int = Field(default=3, ge=1, le=20)
    tool_mode: Literal["simulated", "real"] = "simulated"
    failure_rate: float = Field(default=0.0, ge=0, le=1)
    name: str | None = None


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,60}$")
