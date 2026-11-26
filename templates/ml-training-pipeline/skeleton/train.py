import json

from dotenv import load_dotenv

from pipeline import TrainingService


def main() -> None:
    load_dotenv()
    service = TrainingService.from_env()
    result = service.retrain_if_due(force=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()