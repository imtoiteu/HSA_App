"""Exam blueprint configuration (data, not code). Stored in exam_blueprint.config (JSONB)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QTYPES = ["single_choice", "multiple_choice", "true_false", "true_false_statements", "numeric_response",
          "short_response", "error_identification", "constructed_response", "open_or_unknown"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pool(_M):
    subjects: list[str] = Field(default_factory=list, description="app subject codes; empty = any")
    types: list[str] = Field(default_factory=list, description="question types; empty = any")
    exam_systems: list[str] = Field(default_factory=list, description="restrict to questions seen in these exam systems")
    topics: list[str] = Field(default_factory=list)
    banks: list[str] = Field(default_factory=list, description="question bank codes; empty = any")
    count: int = Field(ge=1, le=500)

    @model_validator(mode="after")
    def _types(self):
        bad = [t for t in self.types if t not in QTYPES]
        if bad:
            raise ValueError(f"unknown question types: {bad}")
        return self


class FixedItem(_M):
    external_id: str
    bank: str = "hsa"


class Section(_M):
    key: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    title: str
    description: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=600)
    points_per_question: float = Field(default=1.0, gt=0)
    pools: list[Pool] = Field(default_factory=list)
    items: list[FixedItem] = Field(default_factory=list, description="fixed exam: exact questions in order")
    order: Literal["pool", "shuffled"] = "pool"


class Scoring(_M):
    correct: float = 1.0          # multiplier of the item's points
    incorrect: float = 0.0        # e.g. -0.25 for negative marking
    unanswered: float = 0.0
    multi_choice: Literal["all_or_nothing", "partial"] = "all_or_nothing"
    tf_sequence: Literal["all_or_nothing", "thpt2025", "per_statement"] = "all_or_nothing"
    numeric_tolerance: float = Field(default=1e-6, ge=0)  # relative
    scale_to: float | None = Field(default=None, gt=0)    # report a scaled score (e.g. 150 or 10)


class BlueprintConfig(_M):
    sections: list[Section] = Field(min_length=1)
    timing: Literal["none", "global", "per_section"] = "global"
    duration_minutes: int | None = Field(default=None, ge=1, le=600)
    shuffle_options: bool = True
    keep_groups_together: bool = True
    max_group_size: int = Field(default=8, ge=1, le=30)
    require_auto_scoring: bool = True     # exams contain only deterministically scorable questions
    feedback: Literal["end", "immediate"] = "end"
    allow_review: bool = True             # show answers/solutions after submission
    scoring: Scoring = Field(default_factory=Scoring)

    @model_validator(mode="after")
    def _check(self):
        if self.timing == "global" and not self.duration_minutes:
            raise ValueError("timing=global requires duration_minutes")
        if self.timing == "per_section" and any(not s.duration_minutes for s in self.sections):
            raise ValueError("timing=per_section requires duration_minutes on every section")
        keys = [s.key for s in self.sections]
        if len(keys) != len(set(keys)):
            raise ValueError("section keys must be unique")
        for s in self.sections:
            if bool(s.pools) == bool(s.items):
                raise ValueError(f"section {s.key}: give either pools (random) or items (fixed), not both")
        return self

    @property
    def total_questions(self) -> int:
        return sum(sum(p.count for p in s.pools) + len(s.items) for s in self.sections)

    @property
    def total_minutes(self) -> int | None:
        if self.timing == "global":
            return self.duration_minutes
        if self.timing == "per_section":
            return sum(s.duration_minutes or 0 for s in self.sections)
        return None


def parse_config(data: dict) -> BlueprintConfig:
    return BlueprintConfig.model_validate(data)
