"""
ML Container - Unified machine learning hub

Wraps ml_engine + learning_engine + mlflow_tracker into one routable container.
Routes: train, predict, evaluate, explain, cross_validate, hyperparameter_tune,
        feature_select, drift_detect, list_models, experiment tracking,
        auto_retrain pipeline.
"""

import os
import time
from typing import Any, Dict, List, Optional

from app.core.universal_base import UniversalContainer


class MLContainer(UniversalContainer):
    """
    ML Container: full machine learning lifecycle in one block.

    Operations (pass as action param):
    ── Training & Inference
    train              → fit a model on X, y
    predict            → inference with saved model
    evaluate           → metrics on test set
    explain            → feature importance + SHAP
    cross_validate     → k-fold CV scores
    hyperparameter_tune→ grid/random search
    feature_select     → SelectKBest scoring
    ── Model Management
    list_models        → saved models in store
    delete_model       → remove model
    list_algorithms    → available sklearn/xgboost algorithms
    ── Drift & Monitoring
    drift_detect       → KS-test between reference and new distributions
    ── Experiments (MLflow)
    experiment_start   → create / resume mlflow experiment
    experiment_log     → log params, metrics, artifacts
    experiment_list    → list runs
    ── Auto-Retrain
    auto_retrain       → drift_detect → retrain if drifted
    ── Feedback Loop
    record_correction  → delegate to learning_engine
    tier_status        → tier promotion status
    ── Meta
    health_check       → container health
    list_actions       → all available actions
    """

    name = "ml"
    version = "1.0.0"
    description = "Full ML lifecycle: train, predict, evaluate, explain, drift detection, MLflow tracking, auto-retrain"
    layer = 3
    tags = ["container", "ml", "ai", "training", "mlops", "sklearn", "domain"]
    requires = ["ml_engine", "learning_engine"]

    default_config = {
        "mlflow_tracking_uri": os.environ.get("MLFLOW_TRACKING_URI", ""),
        "experiment_name": "cerebrum_default",
        "drift_retrain_threshold": 0.3,   # retrain if >30% features drift
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"action": "train", "algorithm": "random_forest", "X": [[...]], "y": [...], "model_id": "my_model"}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "model_id", "type": "text", "label": "Model ID"},
                {"name": "metrics", "type": "json", "label": "Metrics"},
                {"name": "predictions", "type": "list", "label": "Predictions"},
                {"name": "feature_importance", "type": "json", "label": "Importance"},
            ],
        },
        "quick_actions": [
            {"icon": "🤖", "label": "Train", "prompt": "Train a random forest classifier"},
            {"icon": "🔮", "label": "Predict", "prompt": "Run predictions with my trained model"},
            {"icon": "📊", "label": "Evaluate", "prompt": "Evaluate model performance"},
            {"icon": "🔍", "label": "Explain", "prompt": "Show feature importance"},
            {"icon": "⚡", "label": "Auto-Retrain", "prompt": "Check for drift and retrain if needed"},
        ],
    }

    # ── Route ──────────────────────────────────────────────────────────────────

    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        data = input_data if isinstance(input_data, dict) else {}
        p = params or {}
        action = data.get("action") or p.get("action") or action

        ml_passthrough = {
            "train", "predict", "evaluate", "explain", "cross_validate",
            "hyperparameter_tune", "feature_select", "drift_detect",
            "list_models", "delete_model", "list_algorithms",
        }

        if action in ml_passthrough:
            return await self._ml_engine(data, p, operation=action)

        handlers = {
            # MLflow experiment tracking
            "experiment_start":  self._experiment_start,
            "experiment_log":    self._experiment_log,
            "experiment_list":   self._experiment_list,
            # Auto-retrain pipeline
            "auto_retrain":      self._auto_retrain,
            # Learning engine delegation
            "record_correction": self._record_correction,
            "tier_status":       self._tier_status,
            # Meta
            "health_check":      self._health_check,
            "list_actions":      self._list_actions,
        }

        handler = handlers.get(action)
        if not handler:
            return {
                "status": "error",
                "error": f"Unknown ML action: '{action}'",
                "available_actions": list(ml_passthrough) + list(handlers.keys()),
            }
        return await handler(data, p)

    # ── ML Engine delegation ───────────────────────────────────────────────────

    async def _ml_engine(self, data: Dict, params: Dict, operation: str) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("ml_engine")
        if not cls:
            return {"status": "error", "error": "ml_engine block not registered"}
        data_with_op = {**data, "operation": operation}
        return await cls().process(data_with_op, params)

    # ── MLflow experiment tracking ─────────────────────────────────────────────

    async def _experiment_start(self, data: Dict, params: Dict) -> Dict:
        experiment_name = data.get("experiment_name") or self.config.get("experiment_name", "cerebrum_default")
        tracking_uri = data.get("tracking_uri") or self.config.get("mlflow_tracking_uri", "")

        try:
            import mlflow
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            experiment = mlflow.set_experiment(experiment_name)
            run = mlflow.start_run(run_name=data.get("run_name", f"run_{int(time.time())}"))
            run_id = run.info.run_id
            mlflow.end_run()
            return {
                "status": "success",
                "experiment_name": experiment_name,
                "experiment_id": experiment.experiment_id,
                "run_id": run_id,
                "tracking_uri": tracking_uri or "local",
            }
        except ImportError:
            return {"status": "error", "error": "mlflow not installed. Run: pip install mlflow"}
        except Exception as e:
            return {"status": "error", "error": f"MLflow error: {e}"}

    async def _experiment_log(self, data: Dict, params: Dict) -> Dict:
        run_id = data.get("run_id") or params.get("run_id")
        log_params = data.get("params", {})
        log_metrics = data.get("metrics", {})
        tags = data.get("tags", {})
        artifacts = data.get("artifacts", [])

        try:
            import mlflow
            tracking_uri = data.get("tracking_uri") or self.config.get("mlflow_tracking_uri", "")
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)

            with mlflow.start_run(run_id=run_id) if run_id else mlflow.start_run():
                if log_params:
                    mlflow.log_params(log_params)
                if log_metrics:
                    mlflow.log_metrics(log_metrics)
                if tags:
                    mlflow.set_tags(tags)
                for artifact_path in artifacts:
                    if os.path.exists(artifact_path):
                        mlflow.log_artifact(artifact_path)
                active_run_id = mlflow.active_run().info.run_id

            return {
                "status": "success",
                "run_id": active_run_id,
                "logged_params": list(log_params.keys()),
                "logged_metrics": log_metrics,
            }
        except ImportError:
            return {"status": "error", "error": "mlflow not installed. Run: pip install mlflow"}
        except Exception as e:
            return {"status": "error", "error": f"MLflow log error: {e}"}

    async def _experiment_list(self, data: Dict, params: Dict) -> Dict:
        experiment_name = data.get("experiment_name") or self.config.get("experiment_name", "cerebrum_default")
        max_results = int(data.get("max_results", 20))

        try:
            import mlflow
            tracking_uri = data.get("tracking_uri") or self.config.get("mlflow_tracking_uri", "")
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            runs = mlflow.search_runs(
                experiment_names=[experiment_name],
                max_results=max_results,
                output_format="list",
            )
            run_list = [
                {
                    "run_id": r.info.run_id,
                    "status": r.info.status,
                    "start_time": r.info.start_time,
                    "metrics": dict(r.data.metrics),
                    "params": dict(r.data.params),
                }
                for r in runs
            ]
            return {
                "status": "success",
                "experiment_name": experiment_name,
                "runs": run_list,
                "count": len(run_list),
            }
        except ImportError:
            return {"status": "error", "error": "mlflow not installed. Run: pip install mlflow"}
        except Exception as e:
            return {"status": "error", "error": f"MLflow list error: {e}"}

    # ── Auto-Retrain pipeline ──────────────────────────────────────────────────

    async def _auto_retrain(self, data: Dict, params: Dict) -> Dict:
        """
        Pipeline: drift_detect → if drift > threshold → retrain → log to MLflow
        """
        model_id = data.get("model_id") or params.get("model_id")
        X_ref = data.get("X_reference")
        X_new = data.get("X_new")
        X_train = data.get("X")
        y_train = data.get("y")
        threshold = float(data.get("drift_retrain_threshold", self.config.get("drift_retrain_threshold", 0.3)))

        if not model_id:
            return {"status": "error", "error": "model_id required for auto_retrain"}

        pipeline_log: List[str] = []

        # Step 1: Drift detection
        drift_result = {"drift_detected": False, "n_drifted": 0, "n_features": 0}
        if X_ref and X_new:
            drift_result = await self._ml_engine(
                {"X_reference": X_ref, "X_new": X_new, "p_threshold": 0.05},
                params, operation="drift_detect"
            )
            pipeline_log.append(f"Drift check: {drift_result.get('n_drifted', 0)}/{drift_result.get('n_features', 0)} features drifted")

        n_features = drift_result.get("n_features", 1) or 1
        drift_ratio = drift_result.get("n_drifted", 0) / n_features

        # Step 2: Retrain if above threshold (or forced)
        retrain_result = None
        retrained = False
        if drift_ratio >= threshold or data.get("force_retrain"):
            if X_train and y_train:
                retrain_data = {**data, "operation": "train", "model_id": model_id}
                retrain_result = await self._ml_engine(retrain_data, params, operation="train")
                retrained = retrain_result.get("status") == "success"
                pipeline_log.append(f"Retrained: {retrained}, metrics: {retrain_result.get('metrics', {})}")
            else:
                pipeline_log.append("Drift detected but no training data (X, y) provided — skipping retrain")

        # Step 3: Log to MLflow if available
        mlflow_run_id = None
        try:
            import mlflow
            tracking_uri = self.config.get("mlflow_tracking_uri", "")
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(self.config.get("experiment_name", "cerebrum_default"))
            with mlflow.start_run(run_name=f"auto_retrain_{model_id}_{int(time.time())}"):
                mlflow.log_params({"model_id": model_id, "drift_ratio": round(drift_ratio, 3), "retrained": retrained})
                if retrain_result and retrain_result.get("metrics"):
                    mlflow.log_metrics(retrain_result["metrics"])
                mlflow_run_id = mlflow.active_run().info.run_id
            pipeline_log.append(f"Logged to MLflow: run_id={mlflow_run_id}")
        except Exception:
            pipeline_log.append("MLflow logging skipped (not configured)")

        return {
            "status": "success",
            "model_id": model_id,
            "drift_detected": drift_result.get("drift_detected", False),
            "drift_ratio": round(drift_ratio, 3),
            "retrained": retrained,
            "retrain_metrics": retrain_result.get("metrics") if retrain_result else None,
            "mlflow_run_id": mlflow_run_id,
            "pipeline_log": pipeline_log,
        }

    # ── Learning engine delegation ─────────────────────────────────────────────

    async def _record_correction(self, data: Dict, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("learning_engine")
        if not cls:
            return {"status": "error", "error": "learning_engine not registered"}
        return await cls().process({**data, "operation": "record_correction"}, params)

    async def _tier_status(self, data: Dict, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("learning_engine")
        if not cls:
            return {"status": "error", "error": "learning_engine not registered"}
        return await cls().process({"operation": "status"}, params)

    # ── Health & meta ──────────────────────────────────────────────────────────

    async def _health_check(self, data: Dict, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        sub = {b: ("registered" if b in BLOCK_REGISTRY else "missing") for b in self.requires}

        mlflow_ok = False
        try:
            import mlflow
            mlflow_ok = True
        except ImportError:
            pass

        sklearn_ok = False
        try:
            import sklearn
            sklearn_ok = True
        except ImportError:
            pass

        return {
            "status": "success",
            "container": self.name,
            "version": self.version,
            "sub_blocks": sub,
            "mlflow_available": mlflow_ok,
            "sklearn_available": sklearn_ok,
            "model_store": self.config.get("model_store", _MODEL_STORE if False else ""),
        }

    async def _list_actions(self, data: Dict, params: Dict) -> Dict:
        return {
            "status": "success",
            "actions": {
                "training_inference": ["train", "predict", "evaluate", "explain"],
                "validation": ["cross_validate", "hyperparameter_tune", "feature_select"],
                "drift_monitoring": ["drift_detect", "auto_retrain"],
                "model_management": ["list_models", "delete_model", "list_algorithms"],
                "experiment_tracking": ["experiment_start", "experiment_log", "experiment_list"],
                "feedback_loop": ["record_correction", "tier_status"],
                "meta": ["health_check", "list_actions"],
            },
        }


_MODEL_STORE = os.environ.get("ML_MODEL_STORE", "/tmp/cerebrum_ml_models")
