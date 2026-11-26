# ${{ values.name }}

${{ values.description }}

This service accepts continuously pushed, labeled text records, stores them in SQLite, periodically trains a user-selected text classifier, evaluates it on a stable holdout partition, and saves each active model artifact.

## Configure and run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --host 0.0.0.0 --port 8000
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1` and copy the configuration with `Copy-Item .env.example .env`.

Set `TEXT_COLUMN` and `LABEL_COLUMN` to the corresponding fields in each record. Choose a starter `MODEL_KIND`, or provide a trusted Python `MODEL_FACTORY=module:function` that returns an estimator with the scikit-learn `fit` and `predict` interface. The default models use TF-IDF features with logistic regression or multinomial Naive Bayes; replace the factory for domain-specific tokenization, embeddings, transformers, or other NLP tasks.

Push each event to `POST /records`:

```json
{
  "record_id": "source-event-123",
  "record": { "text": "Example incoming text", "label": "support" }
}
```

Only labeled records contribute to supervised training. The service retrains on the most recent `TRAINING_WINDOW_RECORDS` after `RETRAIN_EVERY_N_LABELED` additional labeled events once `MIN_TRAINING_ROWS` is met. `GET /model/status` returns record counts, model version, and holdout accuracy/macro-F1; `POST /predict` accepts a text value. Run `python -m unittest discover -s tests` for the included checks.

This generated starter runs as one application instance using local SQLite and filesystem model artifacts. For production streaming, place a durable broker/source adapter in front of the ingestion endpoint and use shared durable storage, authentication, monitoring, and model approval controls.