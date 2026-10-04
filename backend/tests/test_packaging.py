from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

from app import api as api_module
from app import packager as packager_module

from .conftest import HTTPError


def _wait_package(test_client, project_id, timeout=30.0):
    created = test_client.post(f"/api/projects/{project_id}/packages").json()
    job_id = created["id"]
    deadline = time.time() + timeout
    status = created["status"]
    while status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.05)
        status = test_client.get(f"/api/packages/{job_id}").json()["status"]
    return test_client.get(f"/api/packages/{job_id}").json()


def _package_bytes(test_client, project_id):
    job = _wait_package(test_client, project_id)
    assert job["status"] == "done", job
    return test_client.get(job["output_url"]).content


def _shared_media_project(test_client, samples):
    media_ids = list(samples)
    payload = {
        "name": "打包工程",
        "clips": [
            {
                "id": "clip-a",
                "media_id": media_ids[0],
                "source_in": 0.5,
                "source_out": 2.0,
                "speed_segments": [
                    {"source_start": 1.0, "source_end": 1.5, "rate": 2.0}
                ],
            },
            # Second clip reuses the same media with a different trim.
            {
                "id": "clip-b",
                "media_id": media_ids[0],
                "source_in": 2.5,
                "source_out": 3.5,
                "volume": 0.5,
            },
            {
                "id": "clip-c",
                "media_id": media_ids[1],
                "source_in": 0.0,
                "source_out": 1.5,
                "speed_segments": [
                    {"source_start": 0.5, "source_end": 1.5, "rate": 0.5}
                ],
            },
        ],
    }
    return test_client.post("/api/projects", json=payload).json()


def _wipe_library(config):
    for path in config.MEDIA_DIR.glob("*"):
        path.unlink()
    (config.MEDIA_DIR / "index.json").unlink(missing_ok=True)


def test_package_contains_only_referenced_media_deduped(client):
    test_client, samples, config = client
    project = _shared_media_project(test_client, samples)
    data = _package_bytes(test_client, project["id"])

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        packed_project = json.loads(archive.read("project.json"))

    media_members = [name for name in names if name.startswith("media/")]
    # Two clips share sample1 and sample3 is never referenced: exactly
    # two media members, identical content stored once.
    assert len(media_members) == 2
    assert len(manifest["media"]) == 2
    assert packed_project["clips"] == project["clips"]


def test_import_restores_project_into_empty_library_and_exports_same(
    client, monkeypatch
):
    # Simulate moving to a machine with an empty library: samples must
    # not be regenerated during this test.
    monkeypatch.setattr(api_module, "ensure_samples", lambda: None)
    test_client, samples, config = client
    project = _shared_media_project(test_client, samples)
    original_timeline = test_client.get(
        f"/api/projects/{project['id']}/timeline"
    ).json()
    data = _package_bytes(test_client, project["id"])

    # Move to an empty media library, then restore from the package.
    _wipe_library(config)
    assert test_client.get("/api/media").json() == []

    result = test_client.post(
        "/api/projects/import",
        files={"file": ("pack.gsbpack", data, "application/zip")},
    ).json()
    restored = result["project"]
    assert result["report"]["missing"] == []
    assert result["report"]["corrupt"] == []
    assert len(result["report"]["imported"]) == 2

    # Clip order, trims, volumes and speed plans survive the round trip.
    assert [c["id"] for c in restored["clips"]] == [c["id"] for c in project["clips"]]
    for original, restored_clip in zip(project["clips"], restored["clips"]):
        for key in ("source_in", "source_out", "volume", "speed_segments"):
            assert restored_clip[key] == original[key]

    # Timeline math is identical to before packing.
    restored_timeline = test_client.get(
        f"/api/projects/{restored['id']}/timeline"
    ).json()
    assert restored_timeline["missing_media_ids"] == []
    assert restored_timeline["duration"] == original_timeline["duration"]
    for before, after in zip(original_timeline["clips"], restored_timeline["clips"]):
        assert before["start"] == after["start"]
        assert before["end"] == after["end"]
        assert before["speed_segments"] == after["speed_segments"]

    # Restored project exports the same programme as before packing.
    created = test_client.post(f"/api/projects/{restored['id']}/exports").json()
    job_id = created["id"]
    deadline = time.time() + 30
    status = created["status"]
    while status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.2)
        status = test_client.get(f"/api/exports/{job_id}").json()["status"]
    job = test_client.get(f"/api/exports/{job_id}").json()
    assert job["status"] == "done", job
    import subprocess

    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json",
             str(config.EXPORTS_DIR / f"{job_id}.mp4")],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    assert abs(
        float(probe["format"]["duration"]) - original_timeline["duration"]
    ) < 0.12


def test_import_reuses_identical_content_but_never_same_name_different_content(
    client, monkeypatch
):
    monkeypatch.setattr(api_module, "ensure_samples", lambda: None)
    test_client, samples, config = client
    project = _shared_media_project(test_client, samples)
    data = _package_bytes(test_client, project["id"])

    # First import into the current library: everything already present
    # by content, so nothing is duplicated.
    before = test_client.get("/api/media").json()
    result = test_client.post(
        "/api/projects/import", files={"file": ("a.gsbpack", data, "application/zip")}
    ).json()
    after = test_client.get("/api/media").json()
    assert len(after) == len(before)
    assert sorted(result["report"]["reused"]) == sorted(
        entry["filename"] for entry in json.loads(
            zipfile.ZipFile(io.BytesIO(data)).read("manifest.json")
        )["media"]
    )

    # Now plant a same-name file with different content in the library.
    _wipe_library(config)
    media_ids = list(samples)
    conflicting = config.MEDIA_DIR / "conflict.mp4"
    conflicting.write_bytes((config.SAMPLES_DIR / "sample3.mp4").read_bytes())
    index = {
        "conflict.mp4": {
            "id": "conflict.mp4",
            "filename": "样例1-红 440Hz",  # same display name as packaged media
            "duration": 4.0,
            "width": 640,
            "height": 360,
            "has_audio": True,
            "is_sample": False,
            "sha256": "0" * 64,  # different content hash
        }
    }
    (config.MEDIA_DIR / "index.json").write_text(json.dumps(index), encoding="utf-8")

    result = test_client.post(
        "/api/projects/import", files={"file": ("b.gsbpack", data, "application/zip")}
    ).json()
    restored = result["project"]
    library = test_client.get("/api/media").json()
    # The conflicting same-name entry is untouched and not reused; the
    # packaged content was imported as new entries.
    conflict_entry = next(item for item in library if item["id"] == "conflict.mp4")
    assert conflict_entry["sha256"] == "0" * 64
    used_ids = {clip["media_id"] for clip in restored["clips"]}
    assert "conflict.mp4" not in used_ids
    assert len(library) == 3  # conflict + two packaged media
    timeline = test_client.get(f"/api/projects/{restored['id']}/timeline").json()
    assert timeline["missing_media_ids"] == []


def test_cancel_during_packaging_leaves_no_archive(client, monkeypatch):
    test_client, samples, config = client
    project = _shared_media_project(test_client, samples)

    manager = packager_module.manager
    monkeypatch.setattr(manager, "chunk_size", 256)
    state = {"calls": 0}

    def cancel_midway():
        state["calls"] += 1
        if state["calls"] == 5:
            manager.cancel(job_id)

    monkeypatch.setattr(manager, "chunk_hook", cancel_midway)

    created = test_client.post(f"/api/projects/{project['id']}/packages").json()
    job_id = created["id"]
    deadline = time.time() + 10
    status = created["status"]
    while status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.05)
        status = test_client.get(f"/api/packages/{job_id}").json()["status"]
    info = test_client.get(f"/api/packages/{job_id}").json()
    assert info["status"] == "cancelled", info
    assert not (config.PACKAGES_DIR / f"{job_id}.gsbpack").exists()
    assert not (config.PACKAGES_DIR / f"{job_id}.tmp").exists()
    with pytest.raises(HTTPError) as excinfo:
        test_client.get(f"/api/packages/{job_id}/download")
    assert excinfo.value.status_code == 404

    # Project untouched; a fresh packaging run succeeds right away.
    assert len(test_client.get(f"/api/projects/{project['id']}").json()["clips"]) == 3
    retry = _wait_package(test_client, project["id"])
    assert retry["status"] == "done", retry


def _rebuild_zip(data, transform):
    source = zipfile.ZipFile(io.BytesIO(data))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name in source.namelist():
            payload = source.read(name)
            result = transform(name, payload)
            if result is None:
                continue
            out.writestr(name, result)
    return buffer.getvalue()


def test_corrupt_or_incomplete_package_is_rejected_with_details(client):
    test_client, samples, config = client
    project = _shared_media_project(test_client, samples)
    data = _package_bytes(test_client, project["id"])
    media_before = test_client.get("/api/media").json()
    projects_before = test_client.get("/api/projects").json()

    # 1) Not a zip at all.
    with pytest.raises(HTTPError) as excinfo:
        test_client.post(
            "/api/projects/import",
            files={"file": ("junk.gsbpack", b"not a zip", "application/zip")},
        )
    assert excinfo.value.status_code == 400

    # 2) A media member with flipped bytes -> listed as corrupt by name.
    def corrupt(name, payload):
        if name.startswith("media/"):
            return payload[:-1] + bytes([payload[-1] ^ 0xFF])
        return payload

    corrupted = _rebuild_zip(data, corrupt)
    with pytest.raises(HTTPError) as excinfo:
        test_client.post(
            "/api/projects/import",
            files={"file": ("bad.gsbpack", corrupted, "application/zip")},
        )
    assert excinfo.value.status_code == 400
    assert "损坏素材" in excinfo.value.detail
    assert "样例" in excinfo.value.detail  # offending file named explicitly

    # 3) A media member removed -> listed as missing by name.
    def drop_first_media(name, payload):
        if name.startswith("media/"):
            return None
        return payload

    incomplete = _rebuild_zip(data, drop_first_media)
    with pytest.raises(HTTPError) as excinfo:
        test_client.post(
            "/api/projects/import",
            files={"file": ("bad2.gsbpack", incomplete, "application/zip")},
        )
    assert excinfo.value.status_code == 400
    assert "缺失素材" in excinfo.value.detail

    # Failed imports never touch the library or the project list.
    assert test_client.get("/api/media").json() == media_before
    assert test_client.get("/api/projects").json() == projects_before
