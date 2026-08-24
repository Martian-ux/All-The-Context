"""Composition root for authoritative Core application services."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Literal

from ..capture_runtime import compose_capture_coordinator
from ..config import CoreConfig
from ..import_operations import ImportOperationService
from ..importers import ArchiveImportService
from ..ingestion import IngestionService
from ..retrieval import RetrievalEngine
from ..storage import CoreStore


class CoreService:
    def __init__(self, config: CoreConfig) -> None:
        self.config = config
        self.config.prepare()
        self.store = CoreStore(config.database_path)
        self.store.initialize_vault()
        self.capture = compose_capture_coordinator(self.store, self.config)
        self.store.repair_preledger_secrets()
        while self.store.evaluate_staged_observations():
            pass
        self.store.rebuild_integrity_groups()
        self.ingestion = IngestionService(self.store)
        self.retrieval = RetrievalEngine(self.store)
        self.imports = ArchiveImportService(self.store, max_bytes=config.max_import_bytes)
        self.import_operations = ImportOperationService(
            self.store,
            self.imports,
            data_dir=config.data_dir,
            max_bytes=config.max_import_bytes,
        )
        # Deterministic recovery for operations interrupted by process death.
        self.import_operations.recover_interrupted_operations()

    @classmethod
    def in_directory(cls, data_dir: Path, *, require_auth: bool = False) -> CoreService:
        return cls(CoreConfig.in_directory(data_dir, require_auth=require_auth))

    def close(self) -> None:
        """Release store resources owned by this Core instance."""
        self.store.close()

    def __enter__(self) -> CoreService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        self.close()
        return False
