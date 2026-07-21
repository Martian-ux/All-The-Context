"""Composition root for authoritative Core application services."""

from __future__ import annotations

from pathlib import Path

from ..config import CoreConfig
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
        self.ingestion = IngestionService(self.store)
        self.retrieval = RetrievalEngine(self.store)
        self.imports = ArchiveImportService(self.store, max_bytes=config.max_import_bytes)

    @classmethod
    def in_directory(cls, data_dir: Path, *, require_auth: bool = False) -> CoreService:
        return cls(CoreConfig.in_directory(data_dir, require_auth=require_auth))
