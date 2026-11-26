import hashlib
import importlib
import json
import math
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def split_records(records: list[dict[str, Any]], test_fraction: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not 0 < test_fraction < 1:
        raise ValueError("TEST_FRACTION must be between 0 and 1")
    threshold = int(test_fraction * 10_000)
    training_records = []
    test_records = []
    for item in records:
        digest = hashlib.sha256(item["record_id"].encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % 10_000
        (test_records if bucket < threshold else training_records).append(item)
    return training_records, test_records


def retraining_due(labeled_count: int, previous_count: int, minimum_rows: int, retrain_every: int) -> bool:
    if minimum_rows < 2 or retrain_every < 1:
        raise ValueError("Minimum training rows must be at least 2 and retrain threshold must be positive")
    return labeled_count >= minimum_rows and (previous_count == 0 or labeled_count - previous_count >= retrain_every)


def classification_metrics(actual: list[Any], predicted: list[Any]) -> dict[str, float]:
    if not actual or len(actual) != len(predicted):
        raise ValueError("Evaluation requires matching non-empty actual and predicted values")
    accuracy = sum(expected == result for expected, result in zip(actual, predicted)) / len(actual)
    labels = set(actual) | set(predicted)
    f1_values = []
    for label in labels:
        true_positive = sum(expected == label and result == label for expected, result in zip(actual, predicted))
        false_positive = sum(expected != label and result == label for expected, result in zip(actual, predicted))
        false_negative = sum(expected == label and result != label for expected, result in zip(actual, predicted))
        denominator = 2 * true_positive + false_positive + false_negative
        f1_values.append((2 * true_positive / denominator) if denominator else 0.0)
    return {"accuracy": accuracy, "macro_f1": sum(f1_values) / len(f1_values)}


def regression_metrics(actual: list[Any], predicted: list[Any]) -> dict[str, float]:
    if not actual or len(actual) != len(predicted):
        raise ValueError("Evaluation requires matching non-empty actual and predicted values")
    errors = [float(result) - float(expected) for expected, result in zip(actual, predicted)]
    return {
        "mae": sum(abs(error) for error in errors) / len(errors),
        "rmse": math.sqrt(sum(error * error for error in errors) / len(errors)),
    }


def train_and_evaluate(
    records: list[dict[str, Any]],
    feature_columns: list[str],
    target_column: str,
    task_type: str,
    model_factory: Callable[[], Any],
    test_fraction: float,
) -> tuple[Any, dict[str, float], int, int]:
    training_records, test_records = split_records(records, test_fraction)
    if not training_records or not test_records:
        raise ValueError("Not enough records in both stable train and test partitions")

    def features(item: dict[str, Any]) -> list[Any]:
        return [item["record"][column] for column in feature_columns]

    model = model_factory()
    model.fit([features(item) for item in training_records], [item["record"][target_column] for item in training_records])
    actual = [item["record"][target_column] for item in test_records]
    predicted = list(model.predict([features(item) for item in test_records]))
    metrics = classification_metrics(actual, predicted) if task_type == "classification" else regression_metrics(actual, predicted)
    return model, metrics, len(training_records), len(test_records)


class RecordStore:
    def __init__(self, path: str, target_column: str):
        self.path = Path(path)
        self.target_column = target_column
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS records (record_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, received_at TEXT NOT NULL, is_labeled INTEGER NOT NULL)"
                )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def insert(self, record_id: str, record: dict[str, Any]) -> bool:
        payload = json.dumps(record, allow_nan=False, separators=(",", ":"))
        received_at = datetime.now(timezone.utc).isoformat()
        is_labeled = int(record.get(self.target_column) is not None)
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO records (record_id, record_json, received_at, is_labeled) VALUES (?, ?, ?, ?)",
                    (record_id, payload, received_at, is_labeled),
                )
                return cursor.rowcount == 1

    def read_all(self, limit: int) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record_id, record_json, received_at FROM records ORDER BY received_at DESC, record_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        rows.reverse()
        return [
            {"record_id": row[0], "record": json.loads(row[1]), "received_at": row[2]}
            for row in rows
        ]

    def count(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])

    def count_labeled(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COALESCE(SUM(is_labeled), 0) FROM records").fetchone()[0])


class TrainingService:
    def __init__(self, environ: dict[str, str] | None = None):
        config = environ or os.environ
        self.feature_columns = [value.strip() for value in config.get("FEATURE_COLUMNS", "").split(",") if value.strip()]
        self.target_column = config.get("TARGET_COLUMN", "target").strip()
        self.task_type = config.get("TASK_TYPE", "classification").strip().lower()
        if not self.feature_columns:
            raise ValueError("Configure FEATURE_COLUMNS as a comma-separated list")
        if self.task_type not in {"classification", "regression"}:
            raise ValueError("TASK_TYPE must be classification or regression")
        self.model_factory_path = config.get("MODEL_FACTORY", "models:build_model")
        self.min_training_rows = int(config.get("MIN_TRAINING_ROWS", "80"))
        self.retrain_every = int(config.get("RETRAIN_EVERY_N_LABELED", "25"))
        self.training_window_records = int(config.get("TRAINING_WINDOW_RECORDS", "50000"))
        self.test_fraction = float(config.get("TEST_FRACTION", "0.2"))
        if self.min_training_rows < 2 or self.retrain_every < 1:
            raise ValueError("MIN_TRAINING_ROWS must be at least 2 and RETRAIN_EVERY_N_LABELED must be positive")
        if self.training_window_records < self.min_training_rows:
            raise ValueError("TRAINING_WINDOW_RECORDS must be at least MIN_TRAINING_ROWS")
        if not 0 < self.test_fraction < 1:
            raise ValueError("TEST_FRACTION must be between 0 and 1")
        self.model_path = Path(config.get("MODEL_PATH", "artifacts/model.joblib"))
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path = self.model_path.with_suffix(".state.json")
        self.store = RecordStore(config.get("RECORDS_DB_PATH", "data/records.sqlite3"), self.target_column)
        self._training_lock = threading.Lock()

    @classmethod
    def from_env(cls):
        return cls()

    def add_record(self, record_id: str | None, record: dict[str, Any]) -> str:
        missing = [column for column in self.feature_columns if column not in record]
        if missing:
            raise ValueError(f"Record is missing configured features: {', '.join(missing)}")
        target = record.get(self.target_column)
        if target is not None and isinstance(target, (dict, list)):
            raise ValueError("The target value must be a string, number, boolean, or null")
        resolved_id = record_id or str(uuid.uuid4())
        if not self.store.insert(resolved_id, record):
            raise ValueError(f"Record ID '{resolved_id}' has already been ingested")
        return resolved_id

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        return {
            "model_ready": self.model_path.exists(),
            "record_count": self.store.count(),
            "labeled_record_count": self.store.count_labeled(),
            "training_record_count": state.get("training_record_count", 0),
            "evaluation_record_count": state.get("evaluation_record_count", 0),
            "trained_at": state.get("trained_at"),
            "metrics": state.get("metrics"),
            "model_version": state.get("model_version", 0),
        }

    def _load_model_factory(self):
        module_name, function_name = self.model_factory_path.split(":", maxsplit=1)
        return getattr(importlib.import_module(module_name), function_name)

    def retrain_if_due(self, force: bool = False) -> dict[str, Any]:
        with self._training_lock:
            labeled_count = self.store.count_labeled()
            records = self.store.read_all(self.training_window_records)
            labeled_records = [item for item in records if item["record"].get(self.target_column) is not None]
            state = self._read_state()
            previous_count = int(state.get("labeled_record_count", 0))
            if labeled_count < self.min_training_rows or len(labeled_records) < self.min_training_rows:
                return {"status": "waiting_for_training_data", **self.status()}
            if not force and state and not retraining_due(
                labeled_count, previous_count, self.min_training_rows, self.retrain_every
            ):
                return {"status": "up_to_date", **self.status()}

            model, metrics, training_count, evaluation_count = train_and_evaluate(
                labeled_records,
                self.feature_columns,
                self.target_column,
                self.task_type,
                self._load_model_factory(),
                self.test_fraction,
            )
            import joblib

            with tempfile.NamedTemporaryFile(dir=self.model_path.parent, suffix=".joblib", delete=False) as temp_file:
                model_temp_path = Path(temp_file.name)
            try:
                joblib.dump(model, model_temp_path)
                os.replace(model_temp_path, self.model_path)
            finally:
                model_temp_path.unlink(missing_ok=True)

            updated_state = {
                "model_version": int(state.get("model_version", 0)) + 1,
                "labeled_record_count": labeled_count,
                "training_record_count": training_count,
                "evaluation_record_count": evaluation_count,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "metrics": metrics,
            }
            self.state_path.write_text(json.dumps(updated_state, indent=2), encoding="utf-8")
            return {"status": "trained", **updated_state}

    def predict(self, record: dict[str, Any]) -> Any:
        missing = [column for column in self.feature_columns if column not in record]
        if missing:
            raise ValueError(f"Record is missing configured features: {', '.join(missing)}")
        if not self.model_path.exists():
            raise RuntimeError("No trained model is available yet")
        import joblib

        model = joblib.load(self.model_path)
        prediction = model.predict([[record[column] for column in self.feature_columns]])[0]
        return prediction.item() if hasattr(prediction, "item") else prediction