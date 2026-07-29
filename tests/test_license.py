from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class LicenseTests(unittest.TestCase):
    def test_repository_declares_mit_license(self):
        license_text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 Vincent Chou", license_text)
        self.assertIn(
            "Permission is hereby granted, free of charge, to any person "
            "obtaining a copy",
            license_text,
        )
        self.assertIn('THE SOFTWARE IS PROVIDED "AS IS"', license_text)

    def test_readme_links_mit_license(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## 授權", readme)
        self.assertIn("[MIT License](LICENSE)", readme)


if __name__ == "__main__":
    unittest.main()
