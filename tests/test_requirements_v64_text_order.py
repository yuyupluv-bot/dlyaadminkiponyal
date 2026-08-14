# -*- coding: utf-8 -*-
"""V64: a plain passenger text message must create an order."""
import ast
import io
import os
import unittest

ROOTS = ["/data/bot", "/data/admin"]


def _source(root):
    path = os.path.join(root, "bot/handlers.py")
    return io.open(path, encoding="utf-8").read()


class TextOrderTests(unittest.TestCase):
    def test_handler_falls_back_to_text_order(self):
        for root in ROOTS:
            src = _source(root)
            self.assertIn("def passenger_text_order(", src, root)
            self.assertIn(
                'if not cmd and (text or "").strip():\n        return passenger_text_order(session, user, text)',
                src,
                root,
            )
            idx_call = src.index("return passenger_text_order(session, user, text)")
            idx_menu = src.index("return show_main_menu(session, user)\n\n\nTEXT_ORDER_MIN_LENGTH") \
                if "TEXT_ORDER_MIN_LENGTH" in src else -1
            self.assertGreater(idx_call, 0, root)

    def test_text_order_creates_via_order_set_addresses(self):
        for root in ROOTS:
            tree = ast.parse(_source(root))
            func = next(
                (n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "passenger_text_order"),
                None,
            )
            self.assertIsNotNone(func, root)
            body = ast.dump(func)
            self.assertIn("order_set_addresses", body, root)
            self.assertIn("P_ADDR", body, root)
            self.assertIn("_passenger_order_limit_reached", body, root)
            self.assertIn("_order_ban_message", body, root)
            self.assertIn("active_order_for", body, root)
            # V72: the menu-command check moved into _is_not_order_text(),
            # which still consults _menu_text_commands().
            self.assertIn("_is_not_order_text", body, root)

    def test_menu_texts_still_open_menu(self):
        for root in ROOTS:
            tree = ast.parse(_source(root))
            func = next(
                (n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "_menu_text_commands"),
                None,
            )
            self.assertIsNotNone(func, root)
            dumped = ast.dump(func)
            self.assertIn("_passenger_labels", dumped, root)
            self.assertIn("casefold", dumped, root)


if __name__ == "__main__":
    unittest.main()
