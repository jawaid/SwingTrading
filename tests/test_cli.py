import yaml

from swingtrading import cli
from swingtrading.config import DEFAULT_SETTINGS_PATH


def _settings(tmp_path):
    data = yaml.safe_load(DEFAULT_SETTINGS_PATH.read_text())
    data["paths"]["db_path"] = str(tmp_path / "swing.db")
    data["paths"]["log_dir"] = str(tmp_path / "logs")
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_app_creates_db_and_launches_streamlit(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("subprocess.call", lambda cmd, **kw: calls.append((cmd, kw)) or 0)
    settings = _settings(tmp_path)
    assert cli.main(["--settings", str(settings), "app", "--port", "8600"]) == 0
    assert (tmp_path / "swing.db").exists()
    cmd, kw = calls[0]
    assert cmd[1:4] == ["-m", "streamlit", "run"] and cmd[4].endswith("app/Home.py")
    assert "8600" in cmd and kw["env"]["SWING_SETTINGS"] == str(settings)
    assert "http://localhost:8600" in capsys.readouterr().out


def test_run_refused_while_another_run_holds_the_lock(tmp_path, capsys):
    from swingtrading import db, pipeline

    settings = _settings(tmp_path)
    conn = db.connect(tmp_path / "swing.db")
    with pipeline.run_lock(conn):
        assert cli.main(["--settings", str(settings), "run", "--date", "2026-10-30"]) == 1
    conn.close()
    assert "Not started: another run is already in progress" in capsys.readouterr().out
