"""Preflight and isolation boundaries for the disposable classification runner."""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
from pathlib import Path
import sys

import pytest
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/importer/src"))

from v3_spansh.pipeline import ArtifactRegistration, ImportConfig  # noqa: E402
from v3_spansh_classified.pipeline import (  # noqa: E402
    ClassifiedImportConfig, ClassifiedSpanshPipeline, dry_run,
)


def artifact(tmp_path, text):
    data = gzip.compress(text.encode(), mtime=0)
    path = tmp_path / "slice.json.gz"
    path.write_bytes(data)
    return ArtifactRegistration(path, hashlib.sha256(data).hexdigest(), len(data),
                                datetime(2026, 9, 19, tzinfo=timezone.utc), None)


def config():
    return ClassifiedImportConfig(generation_key="test_classified", target_systems=3,
                                  max_systems=3, chunk_systems=1)


LOCAL_DSN = "host=127.0.0.1 port=55439 dbname=spansh_classified_test"
SYSTEM = '{"id64":1,"name":"Test","coords":{"x":0,"y":0,"z":0},"bodies":[]}'


@pytest.mark.parametrize("text,match", [
    ('{}', 'JSON array'), ('[]', 'empty'), ('[7]', 'invalid Spansh'),
    ('[{"bodies":{}}]', 'invalid Spansh'),
    ('[' + SYSTEM + ',' + SYSTEM + ']', 'duplicate Spansh system'),
    ('[' + SYSTEM + ',' + SYSTEM.replace('"id64":1', '"id64":2') + ']', 'max-systems'),
])
def test_invalid_slices_fail_without_database(tmp_path, monkeypatch, text, match):
    def no_connect(*args, **kwargs):
        pytest.fail("dry-run must never connect")
    monkeypatch.setattr("psycopg.connect", no_connect)
    with pytest.raises(ValueError, match=match):
        dry_run(artifact(tmp_path, text), max_systems=1 if match == 'max-systems' else 3)


def test_bad_hash_fails_before_any_registration(tmp_path, monkeypatch):
    sample = artifact(tmp_path, '[' + SYSTEM + ']')
    sample.path.write_bytes(bytes(sample.size_bytes))
    monkeypatch.setattr("psycopg.connect", lambda *a, **kw: pytest.fail("preflight must precede DB access"))
    pipeline = ClassifiedSpanshPipeline(LOCAL_DSN, sample, config())
    with pytest.raises(ValueError, match="SHA256"):
        pipeline.run()


@pytest.mark.parametrize("dsn", [
    "host=db.example port=55439 dbname=spansh_classified_test",
    "host=127.0.0.1 port=5432 dbname=spansh_classified_test",
    "host=127.0.0.1 dbname=spansh_classified_test",
    "host=127.0.0.1 port=55439 dbname=edfinder",
    LOCAL_DSN + " hostaddr=192.0.2.1", LOCAL_DSN + " service=remote",
])
def test_rejects_non_disposable_targets(tmp_path, dsn):
    with pytest.raises(ValueError, match="rehearsal needs"):
        ClassifiedSpanshPipeline(dsn, artifact(tmp_path, '[' + SYSTEM + ']'), config())


def test_ambient_hostaddr_cannot_reroute_local_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGSERVICE", "remote")
    pipeline = ClassifiedSpanshPipeline(LOCAL_DSN, artifact(tmp_path, '[' + SYSTEM + ']'), config())
    assert conninfo_to_dict(pipeline.dsn)["hostaddr"] == "127.0.0.1"
    with pytest.raises(ValueError, match="cannot publish"):
        pipeline.publish("no publication")


def test_rejects_old_normalizer_config_and_large_slice(tmp_path):
    with pytest.raises(ValueError, match="versioned"):
        ClassifiedSpanshPipeline(LOCAL_DSN, artifact(tmp_path, '[' + SYSTEM + ']'),
                                ImportConfig(generation_key="old"))
    with pytest.raises(ValueError, match="bounded slice"):
        ClassifiedImportConfig(generation_key="big", target_systems=10001, max_systems=10001)


def test_classified_identity_differs_from_retained_identity():
    assert config().payload()["classification_version"] == "v3-spansh-classified-1"
    assert len(config().payload()["classification_code_sha256"]) == 64
    assert config().payload()["selector_version"] == "complete-local-slice-1"


def test_dry_run_reports_unknown_class_and_counts(tmp_path):
    text = SYSTEM.replace('"bodies":[]', '"bodies":[{"bodyId":0,"name":"Test A",'
                          '"type":"Star","subType":"Future spectral type"}]')
    report = dry_run(artifact(tmp_path, '[' + text + ']'))
    assert report["source"] == "Spansh"
    assert report["body_type_counts"]["star"] == 1
    assert report["main_stars"] == 1
    assert report["spectral_classes"] == {}
    assert report["unmapped"] == [{"domain": "body_type", "token": "Future spectral type", "systems": 1}]
