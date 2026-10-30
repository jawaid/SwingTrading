import yaml

from swingtrading import cli
from swingtrading.config import DEFAULT_SETTINGS_PATH


def test_pending_command_creates_db_and_reports_phase(tmp_path, capsys):
    data = yaml.safe_load(DEFAULT_SETTINGS_PATH.read_text())
    data["paths"]["db_path"] = str(tmp_path / "swing.db")
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump(data))

    assert cli.main(["--settings", str(settings), "run", "--dry-run"]) == 1
    assert "Phase 7" in capsys.readouterr().out
    assert (tmp_path / "swing.db").exists()
