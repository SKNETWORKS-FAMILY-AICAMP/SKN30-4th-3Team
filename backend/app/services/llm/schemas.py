"""Pydantic validation for structured LLM responses."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def parse_json_object(raw_content: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


class QueryExpansion(BaseModel):
    queries: list[str] = Field(default_factory=list)


class RerankDecision(BaseModel):
    relevant: list[int] = Field(default_factory=list)


class QuestionScopeDecision(BaseModel):
    scope: Literal["plant_care", "smalltalk", "out_of_scope"]


class GeneratedAnswer(BaseModel):
    evidenceNotes: str = ""
    summary: str = ""
    possibleCauses: list[str] = Field(default_factory=list)
    todayActions: list[str] = Field(default_factory=list)
    observationChecklist: list[str] = Field(default_factory=list)


class VisionObservation(BaseModel):
    observedSymptoms: list[str] = Field(default_factory=list)
    affectedParts: list[str] = Field(default_factory=list)
    severity: str = "판독불가"
    description: str = ""

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value):
        normalized = str(value or "").strip().lower()
        aliases = {
            "경미": "경미",
            "낮음": "경미",
            "mild": "경미",
            "보통": "보통",
            "중등도": "보통",
            "중간": "보통",
            "moderate": "보통",
            "심각": "심각",
            "높음": "심각",
            "severe": "심각",
            "판독불가": "판독불가",
            "unknown": "판독불가",
        }
        return aliases.get(normalized, "판독불가")
