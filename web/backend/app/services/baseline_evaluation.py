"""Transparent, no-training historical PA baseline evaluated on held-out dates.

This does not score the original two-stage AI and does not claim a learned model.
The majority-class comparator uses only training labels; test labels never
influence predictions. All metrics are descriptive, not profit guarantees.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

_CLASSES = ("up", "down", "flat")
_RULE = {
    "bullish_trend": "up",
    "upside_breakout": "up",
    "bullish_reversal": "up",
    "bearish_trend": "down",
    "downside_breakout": "down",
    "bearish_reversal": "down",
    "range": "flat",
    "mixed": "flat",
    "trend_end_candidate": "flat",
}


def _metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    count = len(y_true)
    confusion = {actual: {predicted: 0 for predicted in _CLASSES} for actual in _CLASSES}
    for actual, predicted in zip(y_true, y_pred):
        confusion[actual][predicted] += 1

    per_class: dict[str, dict[str, float | int]] = {}
    for cls in _CLASSES:
        tp = confusion[cls][cls]
        support = sum(confusion[cls].values())
        predicted_count = sum(confusion[actual][cls] for actual in _CLASSES)
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[cls] = {
            "support": support,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    populated = [cls for cls in _CLASSES if per_class[cls]["support"] > 0]
    accuracy = sum(actual == predicted for actual, predicted in zip(y_true, y_pred)) / count if count else 0.0
    balanced = sum(float(per_class[cls]["recall"]) for cls in populated) / len(populated) if populated else 0.0
    macro_f1 = sum(float(per_class[cls]["f1"]) for cls in populated) / len(populated) if populated else 0.0
    return {
        "count": count,
        "accuracy": round(accuracy, 4),
        "balanced_accuracy": round(balanced, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion": confusion,
    }


def evaluate_rule_baseline(samples: list[dict[str, Any]]) -> dict[str, Any]:
    train = sorted(
        (item for item in samples if item.get("split") == "train"),
        key=lambda item: item["decision_time_ms"],
    )
    test = sorted(
        (item for item in samples if item.get("split") == "test"),
        key=lambda item: item["decision_time_ms"],
    )
    if len(train) < 50 or len(test) < 20:
        return {
            "status": "insufficient_data",
            "train_count": len(train),
            "test_count": len(test),
            "required_train": 50,
            "required_test": 20,
            "note": "需要更多已标注的非重叠时间样本，不能报告预测准确率。",
        }
    training_counts = Counter(item["labels"].get("forward_direction") for item in train)
    majority = max(_CLASSES, key=lambda name: (training_counts[name], -_CLASSES.index(name)))
    actual = [item["labels"]["forward_direction"] for item in test]
    predicted = [_RULE.get(item["regime"], "flat") for item in test]
    baseline = [majority for _ in test]
    return {
        "status": "evaluated",
        "train_count": len(train),
        "test_count": len(test),
        "rule_name": "pa-regime-fixed-rules-v1",
        "majority_class_from_train": majority,
        "rule_baseline": _metrics(actual, predicted),
        "train_majority_baseline": _metrics(actual, baseline),
        "test_start_ms": test[0]["decision_time_ms"],
        "test_end_ms": test[-1]["decision_time_ms"],
        "note": (
            "仅为固定启发式规则的测试集分类表现，不是 PA Agent AI 的准确率；"
            "未来 12 根行情的标签未参与预测。重叠样本会降低统计独立性，"
            "正式发布模型还需要逐时段 walk-forward 验证。"
        ),
    }
