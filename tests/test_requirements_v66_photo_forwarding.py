# -*- coding: utf-8 -*-
"""V66: a photo sent without any text must still reach the other participant."""
import ast
import io
import os
import unittest

ROOTS = ["/data/bot", "/data/admin"]


def _read(root, rel):
    with io.open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _func(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


class PhotoForwardingTests(unittest.TestCase):
    def test_photo_source_url_handles_missing_sizes(self):
        for root in ROOTS:
            src = _read(root, "bot/vk_client.py")
            node = _func(src, "photo_source_url")
            self.assertIsNotNone(node, root)
            dumped = ast.dump(node)
            for key in ("sizes", "orig_photo", "photo_1280", "photo_604"):
                self.assertIn(key, dumped, f"{root}:{key}")

    def test_photo_forward_falls_back_to_reference(self):
        for root in ROOTS:
            src = _read(root, "bot/vk_client.py")
            node = _func(src, "_forward_photo")
            self.assertIsNotNone(node, root)
            dumped = ast.dump(node)
            self.assertIn("_reupload_photo", dumped, root)
            self.assertIn("photo_reference", dumped, root)
            self.assertIn('Try', dumped, root)
            self.assertIn("_forward_photo", src, root)

    def test_reupload_photo_validates_download_and_saved_photo(self):
        for root in ROOTS:
            src = _read(root, "bot/vk_client.py")
            node = _func(src, "_reupload_photo")
            self.assertIsNotNone(node, root)
            dumped = ast.dump(node)
            self.assertIn("photo_source_url", dumped, root)
            self.assertIn("photo download failed", dumped, root)
            self.assertIn("VK did not return the saved photo", dumped, root)
            self.assertNotIn("access_key']}\"", dumped, root)

    def test_relay_sends_photo_links_when_upload_impossible(self):
        for root in ROOTS:
            src = _read(root, "bot/messaging.py")
            node = _func(src, "relay")
            self.assertIsNotNone(node, root)
            dumped = ast.dump(node)
            self.assertIn("media_note", dumped, root)
            self.assertIn("photo_source_url", dumped, root)

    def test_photo_without_text_is_routed_to_chat(self):
        for root in ROOTS:
            src = _read(root, "bot/handlers.py")
            self.assertIn(
                "if active and active.driver_id and (text or attachments):", src, root
            )
            self.assertIn("if active and (text or attachments):", src, root)


if __name__ == "__main__":
    unittest.main()
