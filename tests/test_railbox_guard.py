import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import railbox


def bat(root, status, cap):
    b = root / "BAT0"; b.mkdir()
    (b / "status").write_text(status + "\n"); (b / "capacity").write_text(f"{cap}\n")


class BatteryLow(unittest.TestCase):
    def check(self, status, cap):
        with tempfile.TemporaryDirectory() as d:
            if status: bat(Path(d), status, cap)
            return railbox.battery_low(15, Path(d))

    def test_cases(self):
        self.assertTrue(self.check("Discharging", 10))
        self.assertFalse(self.check("Discharging", 80))
        self.assertFalse(self.check("Charging", 5))
        self.assertFalse(self.check(None, 0))  # desktop, no battery


if __name__ == "__main__":
    unittest.main()
