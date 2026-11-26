# ${{ values.name }}

${{ values.description }}

This service accepts a continuous HTTP stream of supervised tabular records, persists them to SQLite, periodically trains a user-selected scikit-learn-compatible model, evaluates it on a stable held-out partition, and publishes the latest model artifact.

## Configure

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, activate the virtual environment with `.venv\\Scripts\\Activate.ps1` and copy the sample environment file with `Copy-Item .env.example .env`.

Set `FEATURE_COLUMNS` to the comma-separated feature keys in each record and `TARGET_COLUMN` to its supervised label. Select classification or regression with `TASK_TYPE`. Choose a built-in `MODEL_KIND`, or implement a trusted model factory in Python and point `MODEL_FACTORY` to `module:function`. A custom estimator must implement the scikit-learn `fit` and `predict` interface and may include its own preprocessing.

## Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Push each incoming event to `POST /records`:

```json
{
  "record_id": "source-event-123",
  "record": { "feature_a": 1.2, "feature_b": 8, "target": "accepted" }
}
```

For regression, `target` must be numeric. The service stores records in SQLite, uses the most recent `TRAINING_WINDOW_RECORDS` and a stable record-ID hash split so test rows are never used for fitting, and retrains after `RETRAIN_EVERY_N_LABELED` new labeled records once `MIN_TRAINING_ROWS` are available. Evaluation metrics and the active model version are available from `GET /model/status`; predictions use `POST /predict` with a `features` object.

Run `python train.py` to force a training/evaluation cycle, or `python -m unittest discover -s tests` to run tests. Keep this starter at one application instance: SQLite and the local model artifact are not a distributed queue or shared model registry. For production, connect ingestion to a durable broker and shared storage, and add authentication, monitoring, and model approval before deployment.