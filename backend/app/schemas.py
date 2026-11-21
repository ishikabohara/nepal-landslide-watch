"""Small public schemas; scientific metadata deliberately remains extensible."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtensibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class RunManifest(ExtensibleModel):
    run_id: str
    issued_at: str
    valid_from: str
    valid_to: str
    mode: str
    model_version: str
    target: str
    score_type: str
    cell_count: int = Field(ge=0)
    source_times: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, int] = Field(default_factory=dict)


class HistoryEntry(ExtensibleModel):
    run_id: str
    issued_at: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    score: float | None = None
    probability: float | None = None
    risk_level: str | None = None
    mode: str | None = None


class LocationResponse(BaseModel):
    cell: dict[str, Any] | None = None
    history: list[HistoryEntry] = Field(default_factory=list)
    rainfall: list[dict[str, Any]] = Field(default_factory=list)


class Place(ExtensibleModel):
    name: str
    lat: float
    lon: float
    kind: str = "place"
