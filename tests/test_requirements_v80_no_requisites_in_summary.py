# -*- coding: utf-8 -*-
"""V80: requisites reach the passenger only via the driver button."""
import ast
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUISITES = "\u0420\u0435\u043a\u0432\u0438\u0437\u0438\u0442\u044b \u0434\u043b\u044f \u043e\u043f\u043b\u0430\u0442\u044b"
SUMMARY = "\u0418\u0442\u043e\u0433 \u043f\u043e \u043f\u043e\u0435\u0437\u0434\u043a\u0435"
ALREADY_PAID = (
    "\u0415\u0441\u043b\u0438 \u0432\u044b \u0443\u0436\u0435 \u043e\u043f\u043b\u0430\u0442\u0438\u043b\u0438 "
    "\u043f\u043e\u0435\u0437\u0434\u043a\u0443"
)


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return handle.read()


def function_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("function %s not found" % name)


class FinalSummaryHasNoRequisites(unittest.TestCase):
    def setUp(self):
        self.handlers = read("bot/handlers.py")
        self.complete = function_source(self.handlers, "driver_complete_ride")

    def test_summary_never_renders_requisites(self):
        self.assertIn(SUMMARY, self.complete)
        self.assertNotIn(REQUISITES, self.complete)

    def test_requisites_text_is_not_built_on_completion(self):
        self.assertNotIn("_payment_details_text(user)", self.complete)
        self.assertNotIn("_payment_details_ready(user)", self.complete)
        self.assertNotIn("payment_details_enabled", self.complete)
        self.assertNotIn("details_included", self.complete)

    def test_completion_never_marks_details_as_sent(self):
        self.assertNotIn("order.payment_details_sent = True", self.complete)

    def test_already_paid_note_only_after_the_button_was_used(self):
        self.assertIn("if order.payment_details_sent:", self.complete)
        head, _, tail = self.complete.partition("if order.payment_details_sent:")
        branch = tail.split("else:", 1)[0]
        self.assertIn(ALREADY_PAID, branch)
        self.assertNotIn(ALREADY_PAID, head)

    def test_button_flow_still_sends_the_requisites(self):
        button = function_source(self.handlers, "driver_send_payment_details")
        self.assertIn(REQUISITES, button)
        self.assertIn("_payment_details_text(user)", button)
        self.assertIn("order.payment_details_sent", button)

    def test_button_is_the_only_sender_of_requisites(self):
        senders = [
            node.name
            for node in ast.walk(ast.parse(self.handlers))
            if isinstance(node, ast.FunctionDef)
            and REQUISITES in (ast.get_source_segment(self.handlers, node) or "")
        ]
        self.assertEqual(senders, ["driver_send_payment_details"])


if __name__ == "__main__":
    unittest.main()
