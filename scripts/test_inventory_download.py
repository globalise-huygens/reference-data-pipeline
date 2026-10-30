import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class InventoryDownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "bin").mkdir()
        shutil.copy(ROOT / "Makefile", self.root)
        shutil.copy(ROOT / "scripts/regenerate_inventory_data.sh", self.root / "scripts")
        duckdb = self.root / "bin/duckdb"
        duckdb.write_text(
            '#!/bin/bash\ncat > "$TEST_SQL"\nexit "${TEST_DUCKDB_EXIT:-0}"\n'
        )
        duckdb.chmod(0o755)
        uv = self.root / "bin/uv"
        uv.write_text("#!/bin/bash\nexit 0\n")
        uv.chmod(0o755)
        self.env = os.environ.copy()
        for key in (
            "SURF_GLOB_KEY_ID", "SURF_GLOB_SECRET",
            "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
            "MAKEFLAGS", "MFLAGS", "MAKEFILES",
        ):
            self.env.pop(key, None)
        self.env["PATH"] = f"{self.root / 'bin'}:{os.defpath}"
        self.env["TEST_SQL"] = str(self.root / "sql.txt")

    def run_script(self):
        return subprocess.run(
            ["bash", str(self.root / "scripts/regenerate_inventory_data.sh")],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )

    def run_make(self, target):
        return subprocess.run(
            ["make", "-j6", target], cwd=self.root, env=self.env,
            text=True, capture_output=True,
        )

    def test_missing_credentials_skips_without_connecting(self):
        for credentials in ({}, {"SURF_GLOB_KEY_ID": "key"}):
            with self.subTest(credentials=credentials):
                self.env.update(credentials)
                result = self.run_script()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Skipping", result.stderr)
                self.assertFalse((self.root / "sql.txt").exists())

    def test_configured_credentials_download_and_failures_propagate(self):
        self.env.update(SURF_GLOB_KEY_ID="key", SURF_GLOB_SECRET="secret")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        sql = (self.root / "sql.txt").read_text()
        self.assertIn("s3://globalise-data/objects/inventory/*.csv", sql)
        self.assertIn("s3://globalise-data/objects/inventory/links/*.csv", sql)
        self.env["TEST_DUCKDB_EXIT"] = "7"
        self.assertEqual(self.run_script().returncode, 7)

    def test_aws_fallback_is_preserved(self):
        self.env.update(AWS_ACCESS_KEY_ID="key", AWS_SECRET_ACCESS_KEY="secret")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "sql.txt").exists())

    def test_make_continues_without_credentials_but_checks_required_links(self):
        result = self.run_make("document")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Skipping", result.stderr)

        (self.root / "data/input/concept").mkdir(parents=True)
        (self.root / "data/input/concept/thesaurus.trig").touch()
        result = self.run_make("thesaurus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing downloaded file: data/input/links_data.parquet", result.stderr)


if __name__ == "__main__":
    unittest.main()
