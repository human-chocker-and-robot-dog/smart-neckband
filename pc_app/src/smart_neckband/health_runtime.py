from __future__ import annotations

from dataclasses import replace
import logging
import os
from pathlib import Path
from threading import Event, Thread
import time
from typing import Callable

from .analysis import EcgAnalysisResult
from .health_contract import validate_wearer_id
from .health_state import HealthStateBuilder
from .health_store import HealthStore
from .health_rules import load_health_rules
from .health_webhook import (
    HealthWebhookClient,
    HealthWebhookDispatcher,
    parse_secret_hex,
)
from .protocol import ParserStats
from .serial_io import PcDataStores, SerialRuntimeStatus


LOGGER = logging.getLogger(__name__)


class HealthRuntimeWorker:
    def __init__(
        self,
        *,
        wearer_id: str,
        stores: PcDataStores,
        reader_provider: Callable[[], object | None],
        analysis_provider: Callable[[], EcgAnalysisResult | None],
        store: HealthStore,
        data_source: str = "live",
        test_mode: bool = False,
        interval_s: float = 0.5,
        dispatcher: HealthWebhookDispatcher | None = None,
    ) -> None:
        self.stores = stores
        self.reader_provider = reader_provider
        self.analysis_provider = analysis_provider
        self.store = store
        self.builder = HealthStateBuilder(
            wearer_id=wearer_id,
            data_source=data_source,
            test_mode=test_mode,
        )
        self.interval_s = interval_s
        self.dispatcher = dispatcher
        self._stop = Event()
        self._thread: Thread | None = None
        self._last_runtime: SerialRuntimeStatus | None = None
        self._last_stats: ParserStats | None = None
        self._last_transport = "unknown"

    @classmethod
    def from_settings(
        cls,
        *,
        wearer_id: str,
        db_path: Path,
        rules_path: Path | None,
        stores: PcDataStores,
        reader_provider: Callable[[], object | None],
        analysis_provider: Callable[[], EcgAnalysisResult | None],
        webhook_url: str | None = None,
        webhook_key_id: str | None = None,
        webhook_secret_hex: str | None = None,
    ) -> HealthRuntimeWorker:
        validate_wearer_id(wearer_id)
        alert_rules = load_health_rules(rules_path) if rules_path is not None else ()
        store = HealthStore(db_path, alert_rules=alert_rules)
        configured = (webhook_url, webhook_key_id, webhook_secret_hex)
        if any(configured) and not all(configured):
            raise ValueError(
                "health webhook URL, key ID, and secret must be configured together"
            )
        dispatcher = None
        if all(configured):
            client = HealthWebhookClient(
                url=str(webhook_url),
                key_id=str(webhook_key_id),
                secret=parse_secret_hex(webhook_secret_hex),
            )
            dispatcher = HealthWebhookDispatcher(store=store, client=client)
        return cls(
            wearer_id=wearer_id,
            stores=stores,
            reader_provider=reader_provider,
            analysis_provider=analysis_provider,
            store=store,
            dispatcher=dispatcher,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        stores: PcDataStores,
        reader_provider: Callable[[], object | None],
        analysis_provider: Callable[[], EcgAnalysisResult | None],
    ) -> HealthRuntimeWorker | None:
        wearer_id = os.environ.get("SMART_COLLAR_WEARER_ID")
        if not wearer_id:
            return None
        db_value = os.environ.get("SMART_COLLAR_HEALTH_DB_PATH")
        db_path = (
            Path(db_value)
            if db_value
            else Path(__file__).resolve().parents[3]
            / "data"
            / "health"
            / "health_state.db"
        )
        rules_value = os.environ.get("SMART_COLLAR_HEALTH_RULES_PATH")
        return cls.from_settings(
            wearer_id=wearer_id,
            db_path=db_path,
            rules_path=Path(rules_value) if rules_value else None,
            stores=stores,
            reader_provider=reader_provider,
            analysis_provider=analysis_provider,
            webhook_url=os.environ.get("SMART_COLLAR_HEALTH_WEBHOOK_URL"),
            webhook_key_id=os.environ.get("SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID"),
            webhook_secret_hex=os.environ.get(
                "SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX"
            ),
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.store.cleanup_retention()
        if self.dispatcher is not None:
            self.dispatcher.start()
        self._thread = Thread(
            target=self._run,
            name="HealthRuntimeWorker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self.dispatcher is not None:
            self.dispatcher.stop(timeout)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def update_once(self, *, now_monotonic_ns: int | None = None) -> bool:
        now_ns = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        reader = self.reader_provider()
        if reader is not None:
            runtime = reader.runtime_status
            stats = reader.stats
            transport = getattr(reader, "health_transport", "unknown")
            self._last_runtime = runtime
            self._last_stats = stats
            self._last_transport = transport
        elif self._last_runtime is not None and self._last_stats is not None:
            runtime = replace(self._last_runtime, serial_open=False)
            stats = self._last_stats
            transport = self._last_transport
        else:
            return False

        device = self.builder.build_device_snapshot(
            stores=self.stores,
            runtime=runtime,
            parser_stats=stats,
            transport=transport,
            now_monotonic_ns=now_ns,
        )
        self.store.save_device_snapshot(
            wearer_id=self.builder.wearer_id,
            source_instance_id=runtime.source_instance_id,
            data_source=self.builder.data_source,
            device=device,
            committed_monotonic_ns=now_ns,
            transport_received_monotonic_ns=runtime.last_transport_packet_monotonic_ns,
        )
        analysis = self.analysis_provider()
        self.store.save_runtime_observability(
            wearer_id=self.builder.wearer_id,
            parser_stats=stats,
            analysis=analysis,
        )
        built = self.builder.build(
            stores=self.stores,
            runtime=runtime,
            parser_stats=stats,
            analysis=analysis,
            transport=transport,
            now_monotonic_ns=now_ns,
        )
        if built is None:
            return False
        self.store.commit_state(built)
        if self.dispatcher is not None:
            self.dispatcher.wake()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.update_once()
            except Exception:
                LOGGER.exception("Health runtime update failed")
            self._stop.wait(self.interval_s)
