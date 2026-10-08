"""Regression: harbor `batch` must exit non-zero when 0 accounts were created
(was always 0 -> kancahub pipeline reported a false success and injected a stale key)."""
from pathlib import Path
import re
import unittest

CLI = Path(__file__).resolve().parent.parent / "harbor" / "tools" / "tokenharbor" / "cli.py"


class TestBatchExit(unittest.TestCase):
    def test_batch_returns_nonzero_when_nothing_created(self):
        src = CLI.read_text()
        # the exit rule now lives in _print_batch_summary (called by both the
        # serial and the concurrent path of _run_batch)
        body = src[src.index("def _run_batch"):]
        self.assertTrue(re.search(r"return 0 if accounts else 1", src)
                        and "_print_batch_summary" in body,
                        "_run_batch must not return 0 unconditionally")


if __name__ == "__main__":
    unittest.main()
