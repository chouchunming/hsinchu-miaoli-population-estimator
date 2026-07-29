from pathlib import Path
import tempfile
import unittest

from scripts.validate_exam_population_artifacts import validate
from tests.test_service import fake_sources
from exam_population.service import update


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        sources, _http = fake_sources()
        update(
            self.root,
            sources=sources,
            backfill_from=(115, 6),
            range_end=(115, 6),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_archive_database_and_exports_pass(self):
        self.assertEqual(validate(self.root), [])

    def test_corrupted_archive_hash_is_reported(self):
        artifact = next((self.root / "raw").rglob("*.csv"))
        artifact.write_bytes(b"corrupted")
        errors = validate(self.root)
        self.assertTrue(any("SHA-256" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
