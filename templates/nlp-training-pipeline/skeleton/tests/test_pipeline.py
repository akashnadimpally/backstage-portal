import unittest

from pipeline import classification_metrics, retraining_due, split_records, train_and_evaluate


class FirstLabelClassifier:
    def fit(self, texts, labels):
        self.label = labels[0]

    def predict(self, texts):
        return [self.label] * len(texts)


class NlpPipelineTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"record_id": f"text-{index}", "record": {"text": f"message {index}", "label": "positive" if index % 2 else "negative"}}
            for index in range(200)
        ]

    def test_split_is_stable_and_disjoint(self):
        training, testing = split_records(self.records, 0.2)
        again_training, again_testing = split_records(self.records, 0.2)
        self.assertEqual([row["record_id"] for row in training], [row["record_id"] for row in again_training])
        self.assertEqual([row["record_id"] for row in testing], [row["record_id"] for row in again_testing])
        self.assertFalse({row["record_id"] for row in training} & {row["record_id"] for row in testing})
        self.assertGreater(len(testing), 0)

    def test_classification_metrics(self):
        metrics = classification_metrics(["positive", "negative"], ["positive", "positive"])
        self.assertAlmostEqual(metrics["accuracy"], 0.5)
        self.assertIn("macro_f1", metrics)

    def test_retraining_waits_for_minimum_then_new_record_threshold(self):
        self.assertFalse(retraining_due(99, 0, 100, 25))
        self.assertTrue(retraining_due(100, 0, 100, 25))
        self.assertFalse(retraining_due(124, 100, 100, 25))
        self.assertTrue(retraining_due(125, 100, 100, 25))

    def test_training_fits_and_evaluates_separate_partitions(self):
        model, metrics, training_count, evaluation_count = train_and_evaluate(
            self.records, "text", "label", FirstLabelClassifier, 0.2
        )
        self.assertIsInstance(model, FirstLabelClassifier)
        self.assertGreater(training_count, 0)
        self.assertGreater(evaluation_count, 0)
        self.assertIn("macro_f1", metrics)


if __name__ == "__main__":
    unittest.main()