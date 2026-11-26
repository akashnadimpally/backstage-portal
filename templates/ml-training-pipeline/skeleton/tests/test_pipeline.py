import unittest

from pipeline import classification_metrics, retraining_due, split_records, train_and_evaluate


class ConstantClassifier:
    def fit(self, features, labels):
        self.label = labels[0]

    def predict(self, features):
        return [self.label] * len(features)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"record_id": f"record-{index}", "record": {"x": index, "label": index % 2}}
            for index in range(200)
        ]

    def test_split_is_stable_and_disjoint(self):
        training, testing = split_records(self.records, 0.2)
        training_again, testing_again = split_records(self.records, 0.2)
        self.assertEqual([row["record_id"] for row in training], [row["record_id"] for row in training_again])
        self.assertEqual([row["record_id"] for row in testing], [row["record_id"] for row in testing_again])
        self.assertFalse({row["record_id"] for row in training} & {row["record_id"] for row in testing})
        self.assertGreater(len(testing), 0)

    def test_classification_metrics(self):
        metrics = classification_metrics(["a", "b", "a"], ["a", "a", "a"])
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertIn("macro_f1", metrics)

    def test_retraining_waits_for_minimum_then_new_record_threshold(self):
        self.assertFalse(retraining_due(79, 0, 80, 25))
        self.assertTrue(retraining_due(80, 0, 80, 25))
        self.assertFalse(retraining_due(104, 80, 80, 25))
        self.assertTrue(retraining_due(105, 80, 80, 25))

    def test_training_fits_and_evaluates_separate_partitions(self):
        model, metrics, training_count, evaluation_count = train_and_evaluate(
            self.records, ["x"], "label", "classification", ConstantClassifier, 0.2
        )
        self.assertIsInstance(model, ConstantClassifier)
        self.assertGreater(training_count, 0)
        self.assertGreater(evaluation_count, 0)
        self.assertIn("accuracy", metrics)


if __name__ == "__main__":
    unittest.main()