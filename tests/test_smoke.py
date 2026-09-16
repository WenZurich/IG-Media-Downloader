import tempfile
import unittest
from pathlib import Path

import app


class CoreSmokeTests(unittest.TestCase):
    def test_normalize_instagram_url(self):
        self.assertEqual(
            app.normalize_url("instagram.com/example/"),
            "https://instagram.com/example/",
        )
        with self.assertRaises(ValueError):
            app.normalize_url("https://example.com/not-instagram")

    def test_target_detection(self):
        self.assertEqual(
            app.target_info("https://www.instagram.com/example/"),
            ("profile", "example"),
        )
        self.assertEqual(
            app.target_info("https://www.instagram.com/p/ABC123/"),
            ("post", "ABC123"),
        )
        self.assertEqual(
            app.target_info("https://www.instagram.com/reel/XYZ987/"),
            ("post", "XYZ987"),
        )

    def test_media_detection(self):
        self.assertTrue(
            app.is_media_url("https://scontent-test.cdninstagram.com/file.jpg")
        )
        self.assertTrue(
            app.is_media_url("https://s11.imginn.com/path/file.jpg?x=1")
        )
        self.assertEqual(app.ext_for("image/jpeg", "https://x.invalid/a"), ".jpg")
        self.assertEqual(app.ext_for("video/mp4", "https://x.invalid/a"), ".mp4")
        self.assertEqual(app.kind_for("image/jpeg", "https://x.invalid/a"), "image")
        self.assertEqual(app.kind_for("video/mp4", "https://x.invalid/a"), "video")

    def test_target_closed_detection(self):
        self.assertTrue(
            app.is_target_closed_error(
                RuntimeError("Target page, context or browser has been closed")
            )
        )
        self.assertFalse(
            app.is_target_closed_error(RuntimeError("HTTP 403 Forbidden"))
        )

    def test_cache_writer(self):
        with tempfile.TemporaryDirectory() as td:
            path, kind = app.save_response_bytes(
                b"x" * 128,
                "image/jpeg",
                "https://scontent-test.cdninstagram.com/a.jpg",
                Path(td),
                1,
            )
            self.assertEqual(kind, "image")
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b"x" * 128)


if __name__ == "__main__":
    unittest.main()
