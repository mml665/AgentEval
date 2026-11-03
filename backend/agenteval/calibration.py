"""Human-label/Judge agreement. Failed or missing ratings remain in the denominator."""
from collections import Counter, defaultdict
import statistics

from pydantic import Field, model_validator
from typing import Literal
from agenteval.contracts import Contract, digest


class HumanLabel(Contract):
    case_key: str
    label: Literal["passed", "failed", "pending_review"]
    rationale: str = Field(min_length=1, max_length=2000)


class CalibrationRequest(Contract):
    grading_batch_id: str
    instance_id: str
    annotation_source: str = Field(min_length=1, max_length=200)
    labels: list[HumanLabel] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_labels(self):
        if len({x.case_key for x in self.labels}) != len(self.labels):
            raise ValueError("Duplicate calibration case key")
        return self


def analyze(labels, predictions, *, planned_repetitions=1):
    by_case = defaultdict(list)
    for row in predictions:
        by_case[row["case_key"]].append(row)
    confusion, agreement, errors, reviews, graded = Counter(), 0, 0, 0, 0
    stability = []
    for label in labels:
        key, expected = label["case_key"], label["label"]
        rows = by_case[key]
        outcomes, scores = [], []
        for row in rows:
            status = row["grading_status"]
            actual = ("passed" if row["passed"] else "failed") if status == "scored" else "pending_review" if status == "pending_review" else status
            outcomes.append(actual)
            confusion[(expected, actual)] += 1
            agreement += actual == expected
            errors += actual == "evaluator_error"
            reviews += actual == "pending_review"
            graded += actual in ("passed", "failed", "pending_review")
            if status == "scored":
                scores.append(row["score"])
        absent = max(0, planned_repetitions - len(rows))
        confusion[(expected, "missing_prediction")] += absent
        stability.append({"case_key": key, "samples": len(rows), "planned": planned_repetitions,
            "complete": len(rows) == planned_repetitions,
            "decision_consistent": len(set(outcomes)) == 1 if len(rows) >= 2 and graded_outcomes(outcomes) else None,
            "score_stddev": statistics.pstdev(scores) if len(scores) >= 2 else None,
            "score_range": max(scores) - min(scores) if len(scores) >= 2 else None})
    total = sum(confusion.values())
    return {"labels_hash": digest(labels), "planned_ratings": total, "agreement_rate": agreement / total if total else None,
        "rating_coverage": graded / total if total else 0, "judge_error_rate": errors / total if total else None,
        "review_rate": reviews / total if total else None,
        "confusion_matrix": [{"human": a, "judge": b, "count": n} for (a, b), n in sorted(confusion.items()) if n],
        "repeat_stability": stability,
        "scope": "Agreement with supplied labels; not proof of unbiased or injection-proof judging"}


def graded_outcomes(outcomes):
    return all(x in ("passed", "failed", "pending_review") for x in outcomes)
