import os

from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor, RandomForestClassifier, RandomForestRegressor


def build_model():
    task_type = os.getenv("TASK_TYPE", "classification")
    model_kind = os.getenv("MODEL_KIND", "random_forest")
    if task_type == "classification" and model_kind == "random_forest":
        return RandomForestClassifier(n_estimators=200, random_state=42, class_weight="balanced")
    if task_type == "classification" and model_kind == "extra_trees":
        return ExtraTreesClassifier(n_estimators=200, random_state=42, class_weight="balanced")
    if task_type == "regression" and model_kind == "random_forest":
        return RandomForestRegressor(n_estimators=200, random_state=42)
    if task_type == "regression" and model_kind == "extra_trees":
        return ExtraTreesRegressor(n_estimators=200, random_state=42)
    raise ValueError(f"Unsupported model kind '{model_kind}' for task '{task_type}'")