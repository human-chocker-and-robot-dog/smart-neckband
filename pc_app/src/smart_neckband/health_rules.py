from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import statistics
from typing import Mapping


RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
NAMESPACED_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
SUPPORTED_METRICS = {"heart_rate_bpm", "hrv_rmssd_ms", "motion_score"}
SUPPORTED_OPERATORS = {"gt", "gte", "lt", "lte"}


@dataclass(frozen=True, slots=True)
class HealthRuleCondition:
    metric: str
    operator: str
    threshold: float

    def matches(self, metrics: Mapping[str, float | None]) -> bool:
        value = metrics.get(self.metric)
        if value is None:
            return False
        if self.operator == "gt":
            return value > self.threshold
        if self.operator == "gte":
            return value >= self.threshold
        if self.operator == "lt":
            return value < self.threshold
        return value <= self.threshold


@dataclass(frozen=True, slots=True)
class HealthAlertRule:
    rule_id: str
    event_type: str
    summary: str
    severity: str
    priority: str
    conditions: tuple[HealthRuleCondition, ...]
    for_s: float
    clear_for_s: float
    cooldown_s: float
    minimum_signal_quality: float
    recommended_capabilities: tuple[str, ...]
    recommended_window_s: int

    def matches(self, metrics: Mapping[str, float | None]) -> bool:
        quality = metrics.get("signal_quality")
        if quality is None or quality < self.minimum_signal_quality:
            return False
        return all(condition.matches(metrics) for condition in self.conditions)


def load_health_rules(path: str | Path) -> tuple[HealthAlertRule, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("health rules require schema_version=1")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list):
        raise ValueError("health rules must contain a rules array")
    rules = tuple(
        _parse_rule(item)
        for item in raw_rules
        if isinstance(item, dict) and bool(item.get("enabled", True))
    )
    rule_ids = [rule.rule_id for rule in rules]
    event_types = [rule.event_type for rule in rules]
    if len(set(rule_ids)) != len(rule_ids):
        raise ValueError("health rule_id values must be unique")
    if len(set(event_types)) != len(event_types):
        raise ValueError("enabled health event_type values must be unique")
    return rules


def rmssd_ms(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    differences = [values[index] - values[index - 1] for index in range(1, len(values))]
    if not differences:
        return None
    return math.sqrt(statistics.fmean(value * value for value in differences))


def _parse_rule(data: dict[str, object]) -> HealthAlertRule:
    rule_id = str(data.get("rule_id", ""))
    event_type = str(data.get("event_type", ""))
    summary = str(data.get("summary", "")).strip()
    if not RULE_ID_PATTERN.fullmatch(rule_id):
        raise ValueError(f"invalid health rule_id: {rule_id}")
    if not NAMESPACED_PATTERN.fullmatch(event_type):
        raise ValueError(f"invalid health event_type: {event_type}")
    if not 1 <= len(summary) <= 500:
        raise ValueError(f"invalid summary for health rule {rule_id}")
    severity = str(data.get("severity", "warning"))
    priority = str(data.get("priority", "normal"))
    if severity not in {"info", "warning", "critical"}:
        raise ValueError(f"invalid severity for health rule {rule_id}")
    if priority not in {"normal", "urgent"}:
        raise ValueError(f"invalid priority for health rule {rule_id}")
    raw_conditions = data.get("conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise ValueError(f"health rule {rule_id} requires conditions")
    conditions: list[HealthRuleCondition] = []
    for raw in raw_conditions:
        if not isinstance(raw, dict):
            raise ValueError(f"invalid condition in health rule {rule_id}")
        metric = str(raw.get("metric", ""))
        operator = str(raw.get("operator", ""))
        threshold = raw.get("threshold")
        if metric not in SUPPORTED_METRICS or operator not in SUPPORTED_OPERATORS:
            raise ValueError(f"unsupported condition in health rule {rule_id}")
        if not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
            raise ValueError(f"invalid threshold in health rule {rule_id}")
        conditions.append(
            HealthRuleCondition(metric, operator, float(threshold))
        )
    capabilities = tuple(
        str(value) for value in data.get(
            "recommended_capabilities",
            ["health.inspect_recent_metrics"],
        )
    )
    if any(not NAMESPACED_PATTERN.fullmatch(value) for value in capabilities):
        raise ValueError(f"invalid capability in health rule {rule_id}")
    return HealthAlertRule(
        rule_id=rule_id,
        event_type=event_type,
        summary=summary,
        severity=severity,
        priority=priority,
        conditions=tuple(conditions),
        for_s=_non_negative(data.get("for_s", 5), "for_s", rule_id),
        clear_for_s=_non_negative(data.get("clear_for_s", 5), "clear_for_s", rule_id),
        cooldown_s=_non_negative(data.get("cooldown_s", 300), "cooldown_s", rule_id),
        minimum_signal_quality=_bounded(
            data.get("minimum_signal_quality", 0.7),
            "minimum_signal_quality",
            rule_id,
            0,
            1,
        ),
        recommended_capabilities=capabilities,
        recommended_window_s=int(
            _bounded(data.get("recommended_window_s", 30), "recommended_window_s", rule_id, 10, 300)
        ),
    )


def _non_negative(value: object, name: str, rule_id: str) -> float:
    return _bounded(value, name, rule_id, 0, 86_400)


def _bounded(
    value: object,
    name: str,
    rule_id: str,
    minimum: float,
    maximum: float,
) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"invalid {name} for health rule {rule_id}")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"invalid {name} for health rule {rule_id}")
    return result
