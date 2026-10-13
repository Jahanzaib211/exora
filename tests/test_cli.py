"""First-run CLI: `init` writes .env and the first admin, `import` loads the example accounts synchronously."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from prospector.cli import app

REPO = Path(__file__).resolve().parents[1]


def test_init_and_import_on_a_fresh_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text((REPO / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    # The CLI exports .env into os.environ without overriding what is set; pinning every uncommented key
    # here keeps this test from leaking settings into the rest of the suite.
    monkeypatch.setenv("PROSPECTOR_SECRET_KEY", "")
    monkeypatch.setenv("PROSPECTOR_ORG_NAME", "")
    monkeypatch.setenv("PROSPECTOR_OPERATOR_TIMEZONE", "UTC")
    monkeypatch.setenv("PROSPECTOR_DATABASE_URL", f"sqlite:///{(tmp_path / 'data' / 'p.db').as_posix()}")
    monkeypatch.setenv("PROSPECTOR_DOCS_ROOT", str(REPO))
    monkeypatch.setenv("PROSPECTOR_LOG_DIR", str(tmp_path / "logs"))
    runner = CliRunner()

    first = runner.invoke(
        app, ["init", "--admin-email", "Ada@Example.com", "--admin-name", "Ada", "--admin-password", "Correct-Horse-9"]
    )
    assert first.exit_code == 0, first.output
    assert re.search(r"^PROSPECTOR_SECRET_KEY=[0-9a-f]{64}$", (tmp_path / ".env").read_text(), re.MULTILINE)
    assert "Created admin ada@example.com" in first.output

    again = runner.invoke(app, ["init"])
    assert again.exit_code == 0, again.output
    assert ".env already exists" in again.output and "Database ready (1 user)" in again.output

    imported = runner.invoke(app, ["import", "--no-enrich"])
    assert imported.exit_code == 0, imported.output
    assert "Import done: 5 rows: 5 new, 0 updated, 0 failed" in imported.output

    missing = runner.invoke(app, ["import", "no/such/file.csv", "--no-enrich"])
    assert missing.exit_code == 1 and "Import failed" in missing.output


def test_version_flag():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0 and result.output.startswith("Exora ")
