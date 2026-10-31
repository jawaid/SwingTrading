import subprocess

from swingtrading import notify


def test_send_builds_an_osascript_call(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(notify.subprocess, "run", lambda args, **kw: calls.append(args))
    assert notify.send('2 new picks, "AAPL"') is True
    assert calls == [
        ["osascript", "-e",
         'display notification "2 new picks, \\"AAPL\\"" with title "SwingTrading"']
    ]  # fmt: skip


def test_send_never_raises(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("osascript", 10)

    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/usr/bin/osascript")
    monkeypatch.setattr(notify.subprocess, "run", fail)
    assert notify.send("hi") is False
    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert notify.send("hi") is False
