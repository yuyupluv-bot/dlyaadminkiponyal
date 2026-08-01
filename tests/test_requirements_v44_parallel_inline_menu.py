import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ParallelInlineMenuV44Tests(unittest.TestCase):
    def source(self):
        return (ROOT / "bot/keyboards.py").read_text("utf-8")

    def test_keyboard_module_parses(self):
        ast.parse(self.source(), filename="bot/keyboards.py")

    def test_all_transient_parallel_controls_are_inline(self):
        source = self.source()
        names = (
            "route_parallel_offer_keyboard",
            "parallel_eta_keyboard",
            "parallel_reserved_keyboard",
        )
        boundaries = (
            "parallel_eta_keyboard",
            "parallel_reserved_keyboard",
            "fake_calls_keyboard",
        )
        for name, boundary in zip(names, boundaries):
            fn = source.split(f"def {name}", 1)[1].split(f"def {boundary}", 1)[0]
            self.assertIn("inline=True", fn, name)

    def test_active_ride_menu_remains_regular(self):
        source = self.source()
        fn = source.split("def driver_ride_keyboard", 1)[1].split("def parallel_orders_keyboard", 1)[0]
        self.assertIn("return keyboard(rows)", fn)
        self.assertNotIn("inline=True", fn)

if __name__ == "__main__":
    unittest.main()
