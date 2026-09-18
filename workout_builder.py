# 스펙 dict → garminconnect Workout 조립 · 인터벌 텍스트 파서 · 이름 규칙
#
# 이 모듈에는 네트워크도 인증도 없다. 스텝 조립은 순수 계산이라
# (analytics.pace_zones 출력 + 스펙 dict) 만으로 전부 만들어지고, 그래서 단위 시험이
# 실 API 를 타지 않는다. 업로드는 garmin_client 쪽 책임이다.
from __future__ import annotations

import re
from typing import Any

from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    SportType,
    StepType,
    TargetType,
    WorkoutSegment,
    create_repeat_group,
)

# WorkoutSegment 는 sportType 이 필수다(RunningWorkout 과 달리 기본값이 없다).
# RunningWorkout 이 쓰는 것과 같은 값을 준다 — 둘이 어긋나면 Garmin 이 거절한다.
_RUNNING_SPORT = {"sportTypeId": SportType.RUNNING, "sportTypeKey": "running", "displayOrder": 1}

# 질주 구간의 목표 타입을 가르는 경계.
#   짧은 구간은 목표 심박에 도달하기 전에 끝난다(심박 지연) — 그래서 페이스로 준다.
#   긴 구간은 자극 크기를 직접 재는 게 맞아서 심박존으로 준다.
SHORT_INTERVAL_M = 600
SHORT_INTERVAL_SEC = 120

# 워밍업·회복·쿨다운의 기본 심박존. 회복이 실제로 회복이 되게 한다 —
# 페이스로 주면 회복 구간에서 과하게 뛴다.
EASY_HR_ZONE = 2

# analytics.pace_zones 의 키는 "R (Repetition)" 같은 모양이다. 약칭으로 찾을 수 있게 한다.
_ZONE_ALIASES = {
    "E": "E (Easy)",
    "M": "M (Marathon)",
    "T": "T (Threshold)",
    "I": "I (Interval)",
    "R": "R (Repetition)",
}

# 스텝 종류 → Garmin stepType. displayOrder 는 라이브러리 헬퍼가 쓰는 값과 같다.
_STEP_TYPES: dict[str, dict[str, Any]] = {
    "warmup": {"stepTypeId": StepType.WARMUP, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": StepType.COOLDOWN, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": StepType.RECOVERY, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": StepType.REST, "stepTypeKey": "rest", "displayOrder": 5},
}

_END_DISTANCE = {
    "conditionTypeId": ConditionType.DISTANCE,
    "conditionTypeKey": "distance",
    "displayOrder": 3,
    "displayable": True,
}
_END_TIME = {
    "conditionTypeId": ConditionType.TIME,
    "conditionTypeKey": "time",
    "displayOrder": 2,
    "displayable": True,
}

_NO_TARGET = {
    "workoutTargetTypeId": TargetType.NO_TARGET,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}
_PACE_TARGET = {
    "workoutTargetTypeId": TargetType.PACE_ZONE,
    "workoutTargetTypeKey": "pace.zone",
    "displayOrder": 1,
}
_HR_TARGET = {
    "workoutTargetTypeId": TargetType.HEART_RATE_ZONE,
    "workoutTargetTypeKey": "heart.rate.zone",
    "displayOrder": 1,
}


class SpecError(ValueError):
    """스펙이 조립 불가능할 때. 호출부는 이걸 잡아 사용자에게 되묻는다."""


# ── 스텝 조립 ────────────────────────────────────────────────────────────────

def _resolve_pace(target: dict[str, Any], pace_zones: dict[str, tuple[float, float]] | None) -> tuple[float, float]:
    """목표 페이스를 (빠른 초/km, 느린 초/km) 로 확정한다."""
    if "pace_range_sec" in target:
        lo, hi = target["pace_range_sec"]
        return float(lo), float(hi)
    zone = target.get("pace_zone")
    if zone is None:
        raise SpecError("페이스 목표에 pace_range_sec 도 pace_zone 도 없다")
    if not pace_zones:
        raise SpecError(f"pace_zone '{zone}' 을 풀려면 pace_zones 가 필요하다")
    key = _ZONE_ALIASES.get(str(zone).upper(), zone)
    if key not in pace_zones:
        raise SpecError(f"모르는 페이스존: {zone}")
    lo, hi = pace_zones[key]
    return float(lo), float(hi)


def _target_fields(
    kind: str,
    condition: dict[str, Any],
    target: dict[str, Any] | None,
    pace_zones: dict[str, tuple[float, float]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(targetType dict, 추가 필드 dict) 를 만든다.

    추가 필드는 ExecutableStep 의 extra 로 들어간다(모델이 extra='allow' 다).
    페이스는 Garmin 이 ★m/s★ 로 받는다 — 초/km 를 그대로 넣으면 안 된다.
    심박은 존 번호만 넘긴다. 경계 bpm 은 계정 설정값이라 우리가 계산하지 않는다.
    """
    if target is None:
        target = _default_target(kind, condition)
    if "hr_zone" in target:
        return _HR_TARGET, {"zoneNumber": int(target["hr_zone"])}
    if "pace_range_sec" in target or "pace_zone" in target:
        fast_sec, slow_sec = _resolve_pace(target, pace_zones)
        if fast_sec <= 0 or slow_sec <= 0:
            raise SpecError("페이스는 0보다 커야 한다")
        # 초/km → m/s. 느린 페이스가 낮은 속도다.
        speeds = sorted((1000.0 / slow_sec, 1000.0 / fast_sec))
        return _PACE_TARGET, {"targetValueOne": speeds[0], "targetValueTwo": speeds[1]}
    if target.get("none"):
        return _NO_TARGET, {}
    raise SpecError(f"목표를 해석할 수 없다: {target}")


def _default_target(kind: str, condition: dict[str, Any]) -> dict[str, Any]:
    """목표를 안 적었을 때의 기본값 — 구간 길이로 가른다."""
    if kind in ("warmup", "cooldown", "recovery", "rest"):
        return {"hr_zone": EASY_HR_ZONE}
    dist = condition.get("distance_m")
    secs = condition.get("duration_sec")
    short = (dist is not None and dist < SHORT_INTERVAL_M) or (secs is not None and secs < SHORT_INTERVAL_SEC)
    return {"pace_zone": "R"} if short else {"hr_zone": 4}


def _executable_step(
    step: dict[str, Any],
    order: int,
    pace_zones: dict[str, tuple[float, float]] | None,
) -> ExecutableStep:
    """스펙 스텝 하나 → ExecutableStep.

    라이브러리의 create_* 헬퍼를 그대로 쓰지 않는 이유: 거리로 끝나는 스텝을 만드는 헬퍼가
    interval 에만 있다(create_distance_interval_step). warmup·recovery·cooldown 헬퍼는
    ★시간 전용★ 이라 "1km 조깅으로 워밍업" 같은 스펙을 표현할 수 없다.
    그래서 헬퍼가 쓰는 것과 ★같은 stepType·endCondition 모양★ 으로 직접 만든다.
    """
    kind = step.get("type")
    if kind not in _STEP_TYPES:
        raise SpecError(f"모르는 스텝 종류: {kind}")
    condition = step.get("condition") or {}
    if "distance_m" in condition:
        end, value = _END_DISTANCE, float(condition["distance_m"])
    elif "duration_sec" in condition:
        end, value = _END_TIME, float(condition["duration_sec"])
    else:
        raise SpecError(f"'{kind}' 스텝에 distance_m 도 duration_sec 도 없다")
    if value <= 0:
        raise SpecError(f"'{kind}' 스텝의 종료 조건이 0 이하다")
    target_type, extra = _target_fields(kind, condition, step.get("target"), pace_zones)
    return ExecutableStep(
        stepOrder=order,
        stepType=_STEP_TYPES[kind],
        endCondition=end,
        endConditionValue=value,
        targetType=target_type,
        **extra,
    )


def _build_steps(
    steps: list[dict[str, Any]],
    pace_zones: dict[str, tuple[float, float]] | None,
    start_order: int = 1,
) -> tuple[list[ExecutableStep | RepeatGroup], int]:
    """스펙 스텝 목록 → Garmin 스텝 목록. 반환 두 번째 값은 다음 stepOrder."""
    out: list[ExecutableStep | RepeatGroup] = []
    order = start_order
    for step in steps:
        if step.get("type") == "repeat":
            iterations = int(step.get("iterations", 0))
            if iterations < 1:
                raise SpecError("repeat 의 iterations 는 1 이상이어야 한다")
            children = step.get("steps") or []
            if not children:
                raise SpecError("repeat 에 하위 스텝이 없다")
            group_order = order
            order += 1
            child_steps, order = _build_steps(children, pace_zones, order)
            out.append(create_repeat_group(iterations, child_steps, group_order))
        else:
            out.append(_executable_step(step, order, pace_zones))
            order += 1
    return out, order


# ── 소요 시간 추정 ───────────────────────────────────────────────────────────

# 거리만 있는 스텝의 시간을 추정할 때 쓰는 기본 페이스(초/km).
# Garmin 이 estimatedDurationInSecs 를 요구하는데 이 값은 표시용이라, 없을 때 대충이라도 채운다.
_FALLBACK_PACE_SEC_PER_KM = 360.0


def _estimate_seconds(
    steps: list[dict[str, Any]],
    pace_zones: dict[str, tuple[float, float]] | None,
) -> float:
    total = 0.0
    for step in steps:
        if step.get("type") == "repeat":
            total += int(step.get("iterations", 1)) * _estimate_seconds(step.get("steps") or [], pace_zones)
            continue
        condition = step.get("condition") or {}
        if "duration_sec" in condition:
            total += float(condition["duration_sec"])
            continue
        dist = float(condition.get("distance_m", 0))
        if dist <= 0:
            continue
        target = step.get("target") or _default_target(step.get("type", ""), condition)
        try:
            fast, slow = _resolve_pace(target, pace_zones)
            pace = (fast + slow) / 2
        except SpecError:
            pace = _FALLBACK_PACE_SEC_PER_KM
        total += dist / 1000.0 * pace
    return total


# ── 공개 API ─────────────────────────────────────────────────────────────────

def build(
    spec: dict[str, Any],
    pace_zones: dict[str, tuple[float, float]] | None = None,
    hr_zone_map: dict[str, Any] | None = None,
) -> RunningWorkout:
    """스펙 dict → RunningWorkout. 네트워크를 타지 않는다.

    hr_zone_map 은 받지만 쓰지 않는다 — 심박 목표는 ★존 번호만★ 넘기고 경계 bpm 은
    Garmin 계정 설정값을 시계가 해석한다. 우리가 bpm 을 계산하면 계정 설정과 어긋난다.
    인자를 남겨 둔 것은 호출부 계약을 바꾸지 않기 위해서다.
    """
    steps = spec.get("steps") or []
    if not steps:
        raise SpecError("스펙에 스텝이 없다")
    built, _ = _build_steps(steps, pace_zones)
    name = spec.get("name") or name_for(spec, spec.get("date"))
    return RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=int(round(_estimate_seconds(steps, pace_zones))),
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=_RUNNING_SPORT, workoutSteps=built)],
        description=spec.get("description"),
    )


def name_for(spec: dict[str, Any], date: str | None = None) -> str:
    """`RC-YYYYMMDD-<약칭>`. 접두사 RC- 가 곧 소유권 표시다 — 정리는 이걸로만 고른다."""
    day = (date or spec.get("date") or "").replace("-", "")
    return f"RC-{day}-{_abbrev(spec)}" if day else f"RC-{_abbrev(spec)}"


def _abbrev(spec: dict[str, Any]) -> str:
    for step in spec.get("steps") or []:
        if step.get("type") != "repeat":
            continue
        iterations = int(step.get("iterations", 0))
        for child in step.get("steps") or []:
            if child.get("type") != "interval":
                continue
            condition = child.get("condition") or {}
            if "distance_m" in condition:
                return f"I{int(condition['distance_m'])}x{iterations}"
            if "duration_sec" in condition:
                return f"I{int(condition['duration_sec'] // 60)}minx{iterations}"
    for step in spec.get("steps") or []:
        if step.get("type") != "interval":
            continue
        condition = step.get("condition") or {}
        if "duration_sec" in condition:
            return f"T{int(condition['duration_sec'] // 60)}min"
        if "distance_m" in condition:
            return f"T{int(condition['distance_m'])}m"
    return "W"


# ── 인터벌 텍스트 파서 ───────────────────────────────────────────────────────

_RE_REPEAT = re.compile(r"(\d+)\s*m\s*[x×*]\s*(\d+)|(\d+)\s*[x×*]\s*(\d+)\s*m", re.I)
_RE_TIME_REPEAT = re.compile(r"(\d+)\s*분\s*[x×*]\s*(\d+)")
_RE_ZONE = re.compile(r"@\s*([EMTIR])\b", re.I)
_RE_RECOVERY_M = re.compile(r"(\d+)\s*m[^\d]{0,6}(회복|조깅|jog|rec)", re.I)
_RE_RECOVERY_MIN = re.compile(r"(\d+)\s*분[^\d]{0,6}(회복|조깅|jog|rec)", re.I)

DEFAULT_WARMUP_SEC = 600
DEFAULT_COOLDOWN_SEC = 600


def parse_interval_text(text: str, date: str | None = None) -> dict[str, Any] | None:
    """`"400m x8 @R 200m 회복"` → 스펙 dict. 못 읽으면 None.

    ★완벽하게 만들 필요가 없다★ — 실패하면 호출부가 그 텍스트를 그대로 자유대화로 넘긴다.
    여기서 억지로 추측하면 사용자가 의도하지 않은 세션이 시계로 올라간다.
    """
    if not text or not text.strip():
        return None

    reps = dist = None
    m = _RE_REPEAT.search(text)
    if m:
        if m.group(1):
            dist, reps = int(m.group(1)), int(m.group(2))
        else:
            reps, dist = int(m.group(3)), int(m.group(4))
    dur = None
    if dist is None:
        mt = _RE_TIME_REPEAT.search(text)
        if mt:
            dur, reps = int(mt.group(1)) * 60, int(mt.group(2))
    if reps is None or (dist is None and dur is None):
        return None

    zone = (_RE_ZONE.search(text).group(1).upper() if _RE_ZONE.search(text) else None)
    work: dict[str, Any] = {
        "type": "interval",
        "condition": {"distance_m": dist} if dist is not None else {"duration_sec": dur},
    }
    if zone:
        work["target"] = {"pace_zone": zone}

    rec: dict[str, Any] | None = None
    mr = _RE_RECOVERY_M.search(text)
    if mr:
        rec = {"type": "recovery", "condition": {"distance_m": int(mr.group(1))},
               "target": {"hr_zone": EASY_HR_ZONE}}
    else:
        mrm = _RE_RECOVERY_MIN.search(text)
        if mrm:
            rec = {"type": "recovery", "condition": {"duration_sec": int(mrm.group(1)) * 60},
                   "target": {"hr_zone": EASY_HR_ZONE}}

    inner = [work] + ([rec] if rec else [])
    spec: dict[str, Any] = {
        "date": date,
        "steps": [
            {"type": "warmup", "condition": {"duration_sec": DEFAULT_WARMUP_SEC},
             "target": {"hr_zone": EASY_HR_ZONE}},
            {"type": "repeat", "iterations": reps, "steps": inner},
            {"type": "cooldown", "condition": {"duration_sec": DEFAULT_COOLDOWN_SEC},
             "target": {"hr_zone": EASY_HR_ZONE}},
        ],
    }
    spec["name"] = name_for(spec, date)
    return spec


# ── Anthropic tool 정의 ──────────────────────────────────────────────────────
# coach 가 이걸 import 해서 chat(tools=[...]) 에 싣는다. 같은 호출에서 설명과 스펙이
# 함께 나오므로 LLM 추가 호출이 없다.

_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["warmup", "interval", "recovery", "rest", "cooldown"]},
        "condition": {
            "type": "object",
            "properties": {
                "distance_m": {"type": "number", "description": "미터. duration_sec 과 둘 중 하나만"},
                "duration_sec": {"type": "number", "description": "초. distance_m 과 둘 중 하나만"},
            },
        },
        "target": {
            "type": "object",
            "properties": {
                "hr_zone": {"type": "integer", "description": "심박존 번호(1~5). 경계 bpm 은 시계가 해석한다"},
                "pace_zone": {"type": "string", "enum": ["E", "M", "T", "I", "R"]},
                "pace_range_sec": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "[빠른 초/km, 느린 초/km]. pace_zone 대신 직접 줄 때만",
                },
            },
        },
    },
    "required": ["type", "condition"],
}

WORKOUT_SPEC_TOOL: dict[str, Any] = {
    "name": "propose_workout",
    "description": (
        "인터벌·템포 세션을 시계로 보낼 수 있는 구조로 제안한다. "
        "짧은 질주(600m 미만 또는 2분 미만)는 pace_zone 으로, 긴 구간은 hr_zone 으로 준다. "
        "워밍업·회복·쿨다운은 hr_zone 2 로 준다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD (KST)"},
            "description": {"type": "string", "description": "이 세션의 의도 한 줄. 예: 'Z4 이상 16분'"},
            "steps": {
                "type": "array",
                "items": {
                    "anyOf": [
                        _STEP_SCHEMA,
                        {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["repeat"]},
                                "iterations": {"type": "integer", "minimum": 1},
                                "steps": {"type": "array", "items": _STEP_SCHEMA},
                            },
                            "required": ["type", "iterations", "steps"],
                        },
                    ]
                },
            },
        },
        "required": ["date", "steps"],
    },
}
