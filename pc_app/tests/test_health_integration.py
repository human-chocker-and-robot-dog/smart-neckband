from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from smart_neckband.health_integration import (
    MCP_TOOL_NAMES,
    HealthIntegrationSettings,
    HealthMcpProcessController,
)


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.exit_code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def wait(self, timeout: float) -> int:
        del timeout
        return 0 if self.exit_code is None else self.exit_code

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9


def settings(tmp_path: Path) -> HealthIntegrationSettings:
    return HealthIntegrationSettings(
        wearer_id="xwen",
        db_path=tmp_path / "health.sqlite3",
        rules_path=None,
        mcp_host="127.0.0.1",
        mcp_port=8765,
        mcp_path="/mcp",
    )


def test_settings_expose_exact_four_tools_for_trusted_lan(tmp_path) -> None:
    assert MCP_TOOL_NAMES == (
        "health.get_heart_rate",
        "health.get_hrv",
        "health.get_imu_state",
        "health.get_sleep_report",
    )
    configured = settings(tmp_path)
    configured.validate_mcp()
    assert configured.endpoint == "http://127.0.0.1:8765/mcp"

def test_mcp_process_uses_no_auth_configuration_and_stops_cleanly(tmp_path) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    process = FakeProcess()

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    controller = HealthMcpProcessController(
        project_root=tmp_path,
        python_executable="python-test",
        popen_factory=fake_popen,
    )
    configured = settings(tmp_path)
    controller.start(configured)

    command = calls[0][0][0]
    assert command[0:4] == [
        "python-test",
        "-m",
        "smart_neckband.health_mcp",
        "--transport",
    ]
    assert "--bearer-token" not in command
    assert "--allowed-host" not in command
    assert controller.running
    assert controller.pid == 4321

    controller.stop()
    assert process.terminated
    assert not process.killed
    assert not controller.running


def test_mcp_process_restarts_when_configuration_changes(tmp_path) -> None:
    processes: list[FakeProcess] = []

    def fake_popen(*args, **kwargs):
        del args, kwargs
        process = FakeProcess()
        process.pid += len(processes)
        processes.append(process)
        return process

    controller = HealthMcpProcessController(
        project_root=tmp_path,
        python_executable="python-test",
        popen_factory=fake_popen,
    )
    configured = settings(tmp_path)
    controller.start(configured)
    controller.start(configured)
    assert len(processes) == 1

    controller.start(replace(configured, mcp_port=9876))

    assert len(processes) == 2
    assert processes[0].terminated
    assert controller.pid == 4322
    controller.stop()


def test_environment_defaults_to_cpe_lan_listener(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SMART_COLLAR_WEARER_ID", "xwen")
    monkeypatch.setenv("SMART_COLLAR_HEALTH_DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.delenv("SMART_COLLAR_HEALTH_MCP_HOST", raising=False)

    configured = HealthIntegrationSettings.from_environment()

    assert configured.mcp_host == "0.0.0.0"
    assert configured.mcp_configured
    configured.validate_mcp()
