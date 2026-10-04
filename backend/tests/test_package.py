from __future__ import annotations

import io
import json
import time
import zipfile

from app import config as config_module
from app import exporter as exporter_module
from app import media as media_module
from app import package as package_module
from app import store as store_module

from tests.conftest import HTTPError


def _wait_package(test_client, job_id, timeout=20.0):
    deadline = time.time() + timeout
    info = test_client.get(f"/api/packages/{job_id}").json()
    while info["status"] in ("pending", "running") and time.time() < deadline:
        time.sleep(0.05)
        info = test_client.get(f"/api/packages/{job_id}").json()
    return info


def _create_project(test_client, samples, clips_extra=None):
    media_ids = list(samples)
    clips = clips_extra or [
        {"id": f"clip-{i}", "media_id": media_ids[i % len(media_ids)],
         "source_in": 0.2, "source_out": 1.5 + 0.3 * i,
         "volume": 0.8,
         "speed_segments": (
             [{"source_start": 0.5, "source_end": 1.0, "rate": 0.5}]
             if i == 0 else []
         )}
        for i in range(4)
    ]
    payload = {"name": "打包工程", "clips": clips}
    return test_client.post("/api/projects", json=payload).json()


def _import_bytes(test_client, data, name="project.zip"):
    try:
        return test_client.post(
            "/api/packages/import",
            files={"file": (name, data, "application/zip")},
        )
    except HTTPError as exc:
        return exc


def test_packages_only_referenced_content_once(client):
    test_client, samples, config = client
    project = _create_project(test_client, samples)

    created = test_client.post(
        f"/api/projects/{project['id']}/packages"
    ).json()
    info = _wait_package(test_client, created["id"])
    assert info["status"] == "done", info
    package_path = config.PACKAGES_DIR / f"{info['id']}.zip"

    with zipfile.ZipFile(package_path) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))

    # 4 clips over 3 samples, every clip referenced -> exactly 3
    # distinct blobs (same content stored once) even though one media is
    # referenced by two clips.
    blob_names = [n for n in names if n.startswith("blobs/")]
    referenced = {clip["media_id"] for clip in project["clips"]}
    assert len(blob_names) == len(referenced) == 3
    assert len({entry["sha256"] for entry in manifest["media"]}) == 3
    # Unreferenced library media would be excluded as well; empty clip
    # lists are covered separately.


def test_roundtrip_into_empty_library_keips_edit_and_export(client, tmp_path, monkeypatch):
    test_client, samples, config = client
    project = _create_project(test_client, samples)
    timeline_before = test_client.get(
        f"/api/projects/{project['id']}/timeline"
    ).json()

    created = test_client.post(
        f"/api/projects/{project['id']}/packages"
    ).json()
    info = _wait_package(test_client, created["id"])
    data = (config.PACKAGES_DIR / f"{info['id']}.zip").read_bytes()

    # Simulate a fresh machine: an empty media library and no projects.
    new_root = tmp_path / "fresh-data"
    dirs = {
        "MEDIA_DIR": new_root / "media",
        "PROJECTS_DIR": new_root / "projects",
        "EXPORTS_DIR": new_root / "exports",
        "SAMPLES_DIR": new_root / "samples",
        "PACKAGES_DIR": new_root / "packages",
    }
    for target in dirs.values():
        target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_module, "DATA_ROOT", new_root)
    monkeypatch.setattr(config_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(config_module, "PROJECTS_DIR", dirs["PROJECTS_DIR"])
    monkeypatch.setattr(config_module, "EXPORTS_DIR", dirs["EXPORTS_DIR"])
    monkeypatch.setattr(config_module, "SAMPLES_DIR", dirs["SAMPLES_DIR"])
    monkeypatch.setattr(config_module, "PACKAGES_DIR", dirs["PACKAGES_DIR"])
    monkeypatch.setattr(media_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(media_module, "SAMPLES_DIR", dirs["SAMPLES_DIR"])
    monkeypatch.setattr(media_module, "MEDIA_INDEX_FILE", dirs["MEDIA_DIR"] / "index.json")
    monkeypatch.setattr(package_module, "PACKAGES_DIR", dirs["PACKAGES_DIR"])
    monkeypatch.setattr(package_module, "STAGING_DIR", new_root / "staging")
    monkeypatch.setattr(config_module, "STAGING_DIR", new_root / "staging")
    monkeypatch.setattr(store_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(store_module, "PROJECTS_DIR", dirs["PROJECTS_DIR"])
    monkeypatch.setattr(exporter_module, "EXPORTS_DIR", dirs["EXPORTS_DIR"])

    # Import into the genuinely empty library.
    result = _import_bytes(test_client, data).json()
    assert result["ok"] is True
    assert len(result["imported_media"]) == 3
    assert result["reused_media"] == []

    restored = test_client.get(
        f"/api/projects/{result['project_id']}"
    ).json()
    assert [clip["id"] for clip in restored["clips"]] == [
        clip["id"] for clip in project["clips"]
    ]
    # Trim points, order and speed plan are byte-identical.
    for before, after in zip(project["clips"], restored["clips"]):
        assert after["source_in"] == before["source_in"]
        assert after["source_out"] == before["source_out"]
        assert after["volume"] == before["volume"]
        assert after["speed_segments"] == before["speed_segments"]

    timeline_after = test_client.get(
        f"/api/projects/{result['project_id']}/timeline"
    ).json()
    assert timeline_after["missing_media_ids"] == []
    assert timeline_after["duration"] == timeline_before["duration"]
    for before, after in zip(timeline_before["clips"], timeline_after["clips"]):
        assert after["start"] == before["start"]
        assert after["duration"] == before["duration"]
        assert [
            (p["source_start"], p["source_end"], p["rate"])
            for p in after["speed_segments"]
        ] == [
            (p["source_start"], p["source_end"], p["rate"])
            for p in before["speed_segments"]
        ]

    # Export works and the render duration matches the original plan.
    created_export = test_client.post(
        f"/api/projects/{result['project_id']}/exports"
    ).json()
    job = _wait_payload(test_client, result["project_id"], created_export["id"])
    assert job["status"] == "done", job
    output = dirs["EXPORTS_DIR"] / f"{job['id']}.mp4"
    probe = json.loads(
        __import__("subprocess").run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(output)],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    assert abs(float(probe["format"]["duration"]) - timeline_before["duration"]) < 0.12


def _wait_payload(test_client, project_id, job_id, timeout=30.0):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        info = test_client.get(f"/api/exports/{job_id}").json()
        status = info["status"]
        if status not in ("pending", "running"):
            return info
        time.sleep(0.1)
    return test_client.get(f"/api/exports/{job_id}").json()


def test_same_name_different_content_not_reused(client, tmp_path, monkeypatch):
    test_client, samples, config = client
    project = _create_project(test_client, samples)
    created = test_client.post(
        f"/api/projects/{project['id']}/packages"
    ).json()
    info = _wait_package(test_client, created["id"])
    data = (config.PACKAGES_DIR / f"{info['id']}.zip").read_bytes()
    media_index = media_module.load_index()
    media_ids = list(samples)
    target_info = media_index[media_ids[0]]
    source_path = config.MEDIA_DIR / media_ids[0]

    # Import the package into a genuinely fresh library that already
    # holds a same-named but different-content decoy: matching is
    # sha256-only, so the decoy must never be reused for the project.
    fresh_root = tmp_path / "fresh"
    dirs = {
        "MEDIA_DIR": fresh_root / "media",
        "PROJECTS_DIR": fresh_root / "projects",
        "EXPORTS_DIR": fresh_root / "exports",
        "PACKAGES_DIR": fresh_root / "packages",
        "SAMPLES_DIR": fresh_root / "samples",
    }
    for target in dirs.values():
        target.mkdir(parents=True, exist_ok=True)
    decoy_src = tmp_path / target_info.filename
    raw = bytearray(source_path.read_bytes())
    raw[200] ^= 0xFF
    decoy_src.write_bytes(bytes(raw))

    monkeypatch.setattr(config_module, "DATA_ROOT", fresh_root)
    monkeypatch.setattr(config_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(config_module, "PROJECTS_DIR", dirs["PROJECTS_DIR"])
    monkeypatch.setattr(config_module, "EXPORTS_DIR", dirs["EXPORTS_DIR"])
    monkeypatch.setattr(config_module, "PACKAGES_DIR", dirs["PACKAGES_DIR"])
    monkeypatch.setattr(config_module, "SAMPLES_DIR", dirs["SAMPLES_DIR"])
    monkeypatch.setattr(config_module, "STAGING_DIR", fresh_root / "staging")
    monkeypatch.setattr(media_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(media_module, "SAMPLES_DIR", dirs["SAMPLES_DIR"])
    monkeypatch.setattr(media_module, "MEDIA_INDEX_FILE", dirs["MEDIA_DIR"] / "index.json")
    monkeypatch.setattr(store_module, "MEDIA_DIR", dirs["MEDIA_DIR"])
    monkeypatch.setattr(store_module, "PROJECTS_DIR", dirs["PROJECTS_DIR"])
    monkeypatch.setattr(exporter_module, "EXPORTS_DIR", dirs["EXPORTS_DIR"])
    monkeypatch.setattr(package_module, "PACKAGES_DIR", dirs["PACKAGES_DIR"])
    monkeypatch.setattr(package_module, "STAGING_DIR", fresh_root / "staging")

    decoy_info = media_module.import_file(decoy_src, target_info.filename, is_sample=False)
    assert decoy_info.sha256 != target_info.sha256

    result = _import_bytes(test_client, data).json()
    assert result["ok"] is True
    # The decoy shares the filename but has different content, so none
    # of the package media may reuse it: all three import anew.
    assert len(result["imported_media"]) == 3
    assert result["reused_media"] == []

    restored = test_client.get(
        f"/api/projects/{result['project_id']}"
    ).json()
    timeline = test_client.get(
        f"/api/projects/{result['project_id']}/timeline"
    ).json()
    assert timeline["missing_media_ids"] == []
    restored_ids = {clip["media_id"] for clip in restored["clips"]}
    assert decoy_info.id not in restored_ids

    media_list = test_client.get("/api/media").json()
    same_name = [item for item in media_list if item["filename"] == target_info.filename]
    # Two distinct contents share one filename: decoy + restored media.
    assert len(same_name) == 2
    assert {item["sha256"] for item in same_name} == {
        decoy_info.sha256,
        target_info.sha256,
    }


def test_cancelled_package_leaves_no_archive(client):
    test_client, samples, config = client
    project = _create_project(test_client, samples)

    original = package_module.shutil.copyfileobj

    class Cancelled(Exception):
        pass

    def cancel_during_copy(*args, **kwargs):
        # Cancel the job while the first blob is being streamed.
        jobs = [
            job for job in package_module.manager._jobs.values()
            if job.project_id == project["id"]
        ]
        for job in jobs:
            job.cancel_event.set()
        raise Cancelled()

    package_module.shutil.copyfileobj = cancel_during_copy
    try:
        created = test_client.post(
            f"/api/projects/{project['id']}/packages"
        ).json()
        info = _wait_package(test_client, created["id"])
    finally:
        package_module.shutil.copyfileobj = original
    assert info["status"] == "cancelled", info
    assert not (config.PACKAGES_DIR / f"{info['id']}.zip").exists()
    # No temp archive left behind.
    assert list(config.PACKAGES_DIR.glob("*.tmp")) == []
    # The project is untouched and can be packaged again successfully.
    retry = test_client.post(
        f"/api/projects/{project['id']}/packages"
    ).json()
    retry_info = _wait_package(test_client, retry["id"])
    assert retry_info["status"] == "done"


def test_corrupt_and_missing_package_reported_and_changes_nothing(client):
    test_client, samples, config = client
    project = _create_project(test_client, samples)
    created = test_client.post(
        f"/api/projects/{project['id']}/packages"
    ).json()
    info = _wait_package(test_client, created["id"])
    good = (config.PACKAGES_DIR / f"{info['id']}.zip").read_bytes()

    projects_before = test_client.get("/api/projects").json()
    media_before = test_client.get("/api/media").json()

    # 1) Not a zip at all.
    response = _import_bytes(test_client, b"definitely not a zip")
    assert response.status_code == 422
    detail = response.json() if hasattr(response, "json") else None

    # 2) Missing blob entry: rewrite the archive without one referenced
    # blob.
    src = config.PACKAGES_DIR / f"{info['id']}.zip"
    with zipfile.ZipFile(src) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entries = {n: archive.read(n) for n in archive.namelist()}
    blob_entries = [n for n in entries if n.startswith("blobs/")]
    missing_name = blob_entries[0]
    missing_hash = missing_name.split("/", 1)[1]
    removed_media = next(
        m for m in manifest["media"] if m["sha256"] == missing_hash
    )
    rebuilt = config.STAGING_DIR if hasattr(config, "STAGING_DIR") else None
    out_path = config.PACKAGES_DIR / "missing.zip"
    with zipfile.ZipFile(out_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, payload in entries.items():
            if name in ("manifest.json", missing_name):
                continue
            archive.writestr(name, payload)
    response = _import_bytes(test_client, out_path.read_bytes())
    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert len(payload["issues"]) == 1
    issue = payload["issues"][0]
    assert issue["kind"] == "missing"
    assert issue["media_id"] == removed_media["media_id"]

    # 3) Corrupt blob: flip a byte inside a stored blob.
    out_path = config.PACKAGES_DIR / "corrupt.zip"
    with zipfile.ZipFile(src) as archive:
        payloads = {n: archive.read(n) for n in archive.namelist()}
    target = [n for n in payloads if n.startswith("blobs/")][0]
    broken = bytearray(payloads[target])
    broken[10] ^= 0xFF
    payloads[target] = bytes(broken)
    with zipfile.ZipFile(out_path, "w") as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    response = _import_bytes(test_client, out_path.read_bytes())
    assert response.status_code == 422
    issue = response.json()["issues"][0]
    assert issue["kind"] == "corrupt"
    assert "校验不匹配" in issue["detail"]

    # Nothing was imported or saved.
    projects_after = test_client.get("/api/projects").json()
    media_after = test_client.get("/api/media").json()
    assert [p["id"] for p in projects_after] == [p["id"] for p in projects_before]
    assert [m["id"] for m in media_after] == [m["id"] for m in media_before]

    # The good package still imports cleanly.
    ok = _import_bytes(test_client, good).json()
    assert ok["ok"] is True


def test_empty_project_package_roundtrips(client):
    test_client, samples, config = client
    empty = test_client.post(
        "/api/projects", json={"name": "空工程", "clips": []}
    ).json()
    created = test_client.post(
        f"/api/projects/{empty['id']}/packages"
    ).json()
    info = _wait_package(test_client, created["id"])
    assert info["status"] == "done", info
    data = (config.PACKAGES_DIR / f"{info['id']}.zip").read_bytes()
    result = _import_bytes(test_client, data).json()
    assert result["ok"] is True
    restored = test_client.get(
        f"/api/projects/{result['project_id']}"
    ).json()
    assert restored["clips"] == []
