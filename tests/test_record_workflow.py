import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from record_workflow import record


class WorkflowArchiveTests(unittest.TestCase):
    def test_skipped_and_failed_steps_are_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            environment = {"GITHUB_RUN_ID": "456", "GITHUB_RUN_ATTEMPT": "2",
                           "GITHUB_EVENT_NAME": "schedule", "EVENT_SCHEDULE": "0 8 * * *",
                           "STEP_TESTS": "success", "STEP_REPLAY": "success",
                           "STEP_GENERATE": "failure", "STEP_DAILY": "skipped"}
            path = record(temp, datetime(2026, 11, 24, 13, tzinfo=timezone.utc), environment)
            payload = json.loads(path.read_text())
            self.assertEqual(payload["steps"]["generate"], "failure")
            self.assertEqual(payload["steps"]["daily"], "skipped")
            self.assertEqual(len(json.loads((Path(temp) / "runs/workflow-manifest.json").read_text())), 1)
            with self.assertRaises(FileExistsError):
                record(temp, datetime(2026, 11, 24, 13, tzinfo=timezone.utc), environment)


if __name__ == "__main__":
    unittest.main()
