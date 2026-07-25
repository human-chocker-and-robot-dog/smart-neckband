from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, TextIO

from .analysis import EcgAnalysisResult
from .health_contract import validate_wearer_id
from .health_mcp import _validate_http_path
from .health_runtime import HealthRuntimeWorker
from .serial_io import PcDataStores


MCP_TOOL_NAMES = (
    "health.get_heart_rate",
    "health.get_hrv",
    "health.get_imu_state",
)


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "health" / "health_state.db"


def _environment_port(value: str | None) -> int:
    try:
        return int(value) if value else 8765
    except ValueError:
        return 8765


@dataclass(frozen=True, slots=True)
class HealthIntegrationSettings:
    wearer_id: str
    db_path: Path
    rules_path: Path | None
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    mcp_path: str = "/mcp"

    @classmethod
    def from_environment(cls) -> HealthIntegrationSettings:
        db_value = os.environ.get("SMART_COLLAR_HEALTH_DB_PATH")
        rules_value = os.environ.get("SMART_COLLAR_HEALTH_RULES_PATH")
        host = os.environ.get("SMART_COLLAR_HEALTH_MCP_HOST", "0.0.0.0").strip()
        port = _environment_port(os.environ.get("SMART_COLLAR_HEALTH_MCP_PORT"))
        return cls(
            wearer_id=os.environ.get("SMART_COLLAR_WEARER_ID", "").strip(),
            db_path=Path(db_value) if db_value else _default_db_path(),
            rules_path=Path(rules_value) if rules_value else None,
            mcp_host=host or "0.0.0.0",
            mcp_port=port,
            mcp_path=os.environ.get("SMART_COLLAR_HEALTH_MCP_PATH", "/mcp").strip()
            or "/mcp",
        )

    @property
    def health_configured(self) -> bool:
        return bool(self.wearer_id)

    @property
    def mcp_configured(self) -> bool:
        return self.health_configured

    @property
    def endpoint(self) -> str:
        return f"http://{self.mcp_host}:{self.mcp_port}{self.mcp_path}"

    @property
    def identity(self) -> tuple[str, Path, Path | None]:
        return (self.wearer_id, self.db_path, self.rules_path)

    @property
    def mcp_identity(self) -> tuple[object, ...]:
        return (
            *self.identity,
            self.mcp_host,
            self.mcp_port,
            self.mcp_path,
        )

    def validate_health(self) -> None:
        validate_wearer_id(self.wearer_id)
        if not str(self.db_path).strip():
            raise ValueError("Health database path is required")
        if self.rules_path is not None and not self.rules_path.is_file():
            raise ValueError(f"Health rules file does not exist: {self.rules_path}")

    def validate_mcp(self) -> None:
        self.validate_health()
        _validate_http_path(self.mcp_path)
        if not 1 <= self.mcp_port <= 65_535:
            raise ValueError("Health MCP port must be in 1..65535")
        if not self.mcp_host:
            raise ValueError("Health MCP host is required")


@dataclass(frozen=True, slots=True)
class HealthIntegrationStatus:
    health_running: bool
    mcp_running: bool
    mcp_pid: int | None
    mcp_exit_code: int | None
    mcp_log_path: Path | None
    last_error: str | None


class HealthMcpProcessController:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        python_executable: str | None = None,
        popen_factory: Callable[..., object] | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self.python_executable = python_executable or sys.executable
        self.popen_factory = popen_factory or subprocess.Popen
        self._process: object | None = None
        self._active_identity: tuple[object, ...] | None = None
        self._log_handle: TextIO | None = None
        self.log_path: Path | None = None
        self.last_error: str | None = None
        self.last_exit_code: int | None = None
        self.last_pid: int | None = None

    def command(self, settings: HealthIntegrationSettings) -> list[str]:
        arguments = [
            self.python_executable,
            "-m",
            "smart_neckband.health_mcp",
            "--transport",
            "streamable-http",
            "--db",
            str(settings.db_path),
            "--wearer-id",
            settings.wearer_id,
            "--host",
            settings.mcp_host,
            "--port",
            str(settings.mcp_port),
            "--path",
            settings.mcp_path,
        ]
        return arguments

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        src_path = str(self.project_root / "pc_app" / "src")
        old_python_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            src_path if not old_python_path else f"{src_path};{old_python_path}"
        )
        return environment

    @property
    def running(self) -> bool:
        if self._process is None:
            return False
        exit_code = self._process.poll()  # type: ignore[attr-defined]
        if exit_code is None:
            return True
        self.last_exit_code = int(exit_code)
        if self.last_exit_code != 0 and self.last_error is None:
            self.last_error = f"Health MCP exited with code {self.last_exit_code}"
        self._process = None
        self._active_identity = None
        self._close_log()
        return False

    @property
    def pid(self) -> int | None:
        if self.running and self._process is not None:
            return int(getattr(self._process, "pid", 0)) or None
        return self.last_pid

    def start(self, settings: HealthIntegrationSettings) -> None:
        settings.validate_mcp()
        identity = settings.mcp_identity
        if self.running and self._active_identity == identity:
            return
        self.stop()
        log_dir = self.project_root / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / (
            "health_mcp_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
        )
        self._log_handle = self.log_path.open("a", encoding="utf-8")
        self.last_error = None
        self.last_exit_code = None
        try:
            self._process = self.popen_factory(
                self.command(settings),
                cwd=str(self.project_root / "pc_app"),
                env=self.environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._log_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.last_pid = int(getattr(self._process, "pid", 0)) or None
            self._active_identity = identity
        except BaseException as exc:
            self.last_error = str(exc)
            self._process = None
            self._active_identity = None
            self._close_log()
            raise

    def stop(self, timeout: float = 5.0) -> None:
        process = self._process
        if process is None:
            self._active_identity = None
            self._close_log()
            return
        if process.poll() is None:  # type: ignore[attr-defined]
            process.terminate()  # type: ignore[attr-defined]
            try:
                process.wait(timeout=timeout)  # type: ignore[attr-defined]
            except subprocess.TimeoutExpired:
                process.kill()  # type: ignore[attr-defined]
                process.wait(timeout=timeout)  # type: ignore[attr-defined]
        exit_code = process.poll()  # type: ignore[attr-defined]
        self.last_exit_code = int(exit_code) if exit_code is not None else None
        self._process = None
        self._active_identity = None
        self._close_log()

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class HealthIntegrationController:
    def __init__(
        self,
        *,
        stores: PcDataStores,
        reader_provider: Callable[[], object | None],
        analysis_provider: Callable[[], EcgAnalysisResult | None],
        mcp_process: HealthMcpProcessController | None = None,
    ) -> None:
        self.stores = stores
        self.reader_provider = reader_provider
        self.analysis_provider = analysis_provider
        self.mcp_process = mcp_process or HealthMcpProcessController()
        self.runtime: HealthRuntimeWorker | None = None
        self._runtime_identity: tuple[str, Path, Path | None] | None = None
        self.last_error: str | None = None

    def start_runtime(self, settings: HealthIntegrationSettings) -> None:
        settings.validate_health()
        if (
            self.runtime is not None
            and self.runtime.is_running
            and self._runtime_identity == settings.identity
        ):
            return
        if self.mcp_process.running:
            self.mcp_process.stop()
        self.stop_runtime()
        worker = HealthRuntimeWorker.from_settings(
            wearer_id=settings.wearer_id,
            db_path=settings.db_path,
            rules_path=settings.rules_path,
            stores=self.stores,
            reader_provider=self.reader_provider,
            analysis_provider=self.analysis_provider,
            webhook_url=os.environ.get("SMART_COLLAR_HEALTH_WEBHOOK_URL"),
            webhook_key_id=os.environ.get("SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID"),
            webhook_secret_hex=os.environ.get(
                "SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX"
            ),
        )
        worker.start()
        self.runtime = worker
        self._runtime_identity = settings.identity
        self.last_error = None

    def start_mcp(self, settings: HealthIntegrationSettings) -> None:
        settings.validate_mcp()
        self.start_runtime(settings)
        self.mcp_process.start(settings)
        self.last_error = None

    def auto_start(self, settings: HealthIntegrationSettings) -> None:
        if settings.health_configured:
            self.start_runtime(settings)
        if settings.mcp_configured:
            self.start_mcp(settings)

    def stop_runtime(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
        self.runtime = None
        self._runtime_identity = None

    def stop_mcp(self) -> None:
        self.mcp_process.stop()

    def close(self) -> None:
        self.stop_mcp()
        self.stop_runtime()

    def status(self) -> HealthIntegrationStatus:
        mcp_running = self.mcp_process.running
        return HealthIntegrationStatus(
            health_running=self.runtime is not None and self.runtime.is_running,
            mcp_running=mcp_running,
            mcp_pid=self.mcp_process.pid,
            mcp_exit_code=self.mcp_process.last_exit_code,
            mcp_log_path=self.mcp_process.log_path,
            last_error=self.last_error or self.mcp_process.last_error,
        )
