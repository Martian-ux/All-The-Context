"""Opt-in pytest/xdist hooks for CI collection and terminal execution receipts."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from scripts.check_test_collection import (
    assign_test_cases,
    collection_digest,
    normalize_nodeid,
    projected_ids,
    test_case_groups,
    xdist_collection,
)


def pytest_addoption(parser):
    group = parser.getgroup("ATC CI receipts")
    group.addoption("--atc-ci-metadata")
    group.addoption("--atc-ci-receipt")


def pytest_configure(config):
    metadata = config.getoption("--atc-ci-metadata")
    if metadata:
        config.pluginmanager.register(Receipt(config, json.loads(metadata)), "atc-ci-receipt")


class Receipt:
    def __init__(self, config, metadata):
        self.config = config
        self.metadata = metadata
        self.is_worker = hasattr(config, "workerinput")
        self.full = []
        self.collections = {}
        self.worker_full = {}
        self.worker_schedule = {}
        self.worker_errors = {}
        self.started = Counter()
        self.finished = Counter()
        self.reports = defaultdict(list)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        # xdist resets worker dist to "no". Its derived loadgroup flag is the
        # real contract. Inject marks before yielding to its own suffix shim.
        self.full = [normalize_nodeid(item.nodeid) for item in items]
        if not self.full or len(set(self.full)) != len(self.full):
            raise pytest.UsageError("CI requires a non-empty unique collection")
        if self.metadata["mode"] == "shard":
            loadgroup = self.config.getoption("loadgroup", False)
            if not self.is_worker or loadgroup is not True:
                raise pytest.UsageError("CI shards require real loadgroup workers")
            platform = self.metadata.get("platform")
            groups = test_case_groups(self.full, platform=platform)
            inverse = projected_ids(groups)
            shards = assign_test_cases(
                xdist_collection(Counter(self.full)),
                shard_count=self.metadata["shard_count"],
                platform=platform,
            )
            targets = shards[self.metadata["shard"]]
            for item, nodeid in zip(items, self.full, strict=True):
                if list(item.iter_markers("xdist_group")):
                    raise pytest.UsageError("CI scheduling group conflicts with existing mark")
                item.add_marker(pytest.mark.xdist_group(groups[nodeid]))
        result = yield
        if self.metadata["mode"] == "shard":
            expected = [f"{node}@{groups[node]}" for node in self.full]
            if [item.nodeid for item in items] != expected:
                raise pytest.UsageError("xdist scheduling projection differs from approved groups")
            selected, rejected = [], []
            for item in items:
                destination = selected if inverse[item.nodeid] in targets else rejected
                destination.append(item)
            self.config.hook.pytest_deselected(items=rejected)
            items[:] = selected
            self.config.workeroutput["atc_ci_full"] = self.full
            self.config.workeroutput["atc_ci_schedule"] = {
                "loadgroup": loadgroup,
                "dist": self.config.getoption("dist"),
            }
        else:
            self.full = [normalize_nodeid(item.nodeid) for item in items]
            if self.metadata["mode"] == "retrieval" and (
                self.is_worker or self.config.getoption("numprocesses", 0) not in (0, None)
            ):
                raise pytest.UsageError("retrieval invocation must run without xdist")
        return result

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        worker = node.gateway.id
        if worker in self.collections:
            raise RuntimeError(f"worker collected twice: {worker}")
        self.collections[worker] = list(ids)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        worker = node.gateway.id
        if worker in self.worker_full or worker in self.worker_errors:
            raise RuntimeError(f"worker finished twice: {worker}")
        if error is not None:
            self.worker_errors[worker] = True
        else:
            self.worker_full[worker] = node.workeroutput.get("atc_ci_full")
            self.worker_schedule[worker] = node.workeroutput.get("atc_ci_schedule")

    def pytest_runtest_logstart(self, nodeid):
        if not self.is_worker:
            self.started[nodeid] += 1

    def pytest_runtest_logfinish(self, nodeid):
        if not self.is_worker:
            self.finished[nodeid] += 1

    def pytest_runtest_logreport(self, report):
        if not self.is_worker:
            self.reports[report.nodeid].append(
                {
                    "phase": report.when,
                    "duration_seconds": report.duration,
                    "outcome": report.outcome,
                    "xfail": hasattr(report, "wasxfail"),
                    "worker": getattr(report, "worker_id", None),
                }
            )

    def pytest_sessionfinish(self, exitstatus):
        if self.is_worker:
            return
        payload = dict(self.metadata, exit=int(exitstatus))
        if self.metadata["mode"] == "canonical":
            payload["collection"] = self.full
            payload["collection_digest"] = collection_digest(Counter(self.full))
        else:
            payload.update(
                collections=self.collections,
                worker_full=self.worker_full,
                worker_schedule=self.worker_schedule,
                worker_errors=self.worker_errors,
                started=dict(self.started),
                finished=dict(self.finished),
                reports=dict(self.reports),
            )
            if self.metadata["mode"] == "retrieval":
                payload["collection"] = self.full
        path = Path(self.config.getoption("--atc-ci-receipt"))
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(path)
