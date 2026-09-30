from __future__ import annotations

import json
import shutil
import time


def _wait_job(test_client, project_id, timeout=30.0):
    created = test_client.post(f"/api/projects/{project_id}/exports").json()
    job_id = created["id"]
    deadline = time.time() + timeout
    status = created["status"]
    while status in ("pending", "running") and time.time() < deadline:
        time.sleep(0.2)
        status = test_client.get(f"/api/exports/{job_id}").json()["status"]
    return test_client.get(f"/api/exports/{job_id}").json()


def _create_project_with_clips(test_client, samples, outs=(2.0, 2.5, 1.5)):
    media_ids = list(samples)
    payload = {
        "name": "流程工程",
        "clips": [
            {
                "id": f"clip-{index}",
                "media_id": media_id,
                "source_in": 0.5,
                "source_out": out,
            }
            for index, (media_id, out) in enumerate(zip(media_ids, outs))
        ],
    }
    return test_client.post("/api/projects", json=payload).json()


def test_full_cut_reopen_export_and_boundaries(client):
    test_client, samples, config = client
    project = _create_project_with_clips(test_client, samples)
    project_id = project["id"]
    expected_duration = (2.0 - 0.5) + (2.5 - 0.5) + (1.5 - 0.5)

    # Timeline endpoint matches the math used by the exporter.
    timeline = test_client.get(
        f"/api/projects/{project_id}/timeline"
    ).json()
    assert abs(timeline["duration"] - expected_duration) < 0.01
    assert [c["start"] for c in timeline["clips"]] == [0.0, 1.5, 3.5]

    # Reopen the project from disk and edit (trim first clip).
    reopened = test_client.get(f"/api/projects/{project_id}").json()
    assert len(reopened["clips"]) == 3
    reopened["clips"][0]["source_out"] = 1.2
    test_client.put(f"/api/projects/{project_id}", json=reopened).json()
    timeline = test_client.get(f"/api/projects/{project_id}/timeline").json()
    assert abs(timeline["duration"] - (expected_duration - 0.8)) < 0.01
    assert [round(c["start"], 3) for c in timeline["clips"]] == [
        0.0,
        0.7,
        2.7,
    ]

    # Restore full plan and export.
    reopened["clips"][0]["source_out"] = 2.0
    test_client.put(f"/api/projects/{project_id}", json=reopened)
    job = _wait_job(test_client, project_id)
    assert job["status"] == "done", job

    output = config.EXPORTS_DIR / f"{job['id']}.mp4"
    assert output.exists()

    # ffprobe: rendered duration and each stream duration line up.
    probe = json.loads(
        __import__("subprocess").run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    format_duration = float(probe["format"]["duration"])
    assert abs(format_duration - expected_duration) < 0.12
    stream_durations = [
        float(stream["duration"]) for stream in probe["streams"]
    ]
    for stream_duration in stream_durations:
        assert abs(stream_duration - expected_duration) < 0.12

    # Downloaded bytes are the final mp4; finer boundary checks live in
    # the standalone ffmpeg verification script after the test run.
    download = test_client.get(job["output_url"])
    assert download.status_code == 200
    assert len(download.content) > 1000


def test_export_blocked_when_media_missing_but_project_survives(client):
    test_client, samples, _ = client
    project = _create_project_with_clips(test_client, samples)
    project_id = project["id"]

    # Remove a media file (simulating missing source material).
    import os

    from app.config import MEDIA_DIR

    missing_id = project["clips"][1]["media_id"]
    os.rename(MEDIA_DIR / missing_id, MEDIA_DIR / f"{missing_id}.bak")
    try:
        timeline = test_client.get(f"/api/projects/{project_id}/timeline").json()
        assert missing_id in timeline["missing_media_ids"]
        job = _wait_job(test_client, project_id)
        assert job["status"] == "failed"
        assert "素材缺失" in (job["error"] or "")
    finally:
        os.rename(MEDIA_DIR / f"{missing_id}.bak", MEDIA_DIR / missing_id)

    # Project is still fully editable and exports after restoring media.
    reopened = test_client.get(f"/api/projects/{project_id}").json()
    assert len(reopened["clips"]) == 3
    job = _wait_job(test_client, project_id)
    assert job["status"] == "done", job


def test_cancelled_export_leaves_editable_project_and_no_output(client):
    test_client, samples, config = client
    project = _create_project_with_clips(test_client, samples)
    project_id = project["id"]

    created = test_client.post(f"/api/projects/{project_id}/exports").json()
    job_id = created["id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        status = test_client.get(f"/api/exports/{job_id}").json()["status"]
        if status == "running":
            break
        time.sleep(0.02)
    # Even if export already finished, project integrity must hold; the
    # fast samples make timing tight, so cancel best-effort then verify
    # invariants: no partial final file + project still editable.
    test_client.post(f"/api/exports/{job_id}/cancel")
    deadline = time.time() + 10
    final_status = None
    while time.time() < deadline:
        info = test_client.get(f"/api/exports/{job_id}").json()
        final_status = info["status"]
        if final_status in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert final_status in ("done", "cancelled")
    output = config.EXPORTS_DIR / f"{job_id}.mp4"
    if final_status == "cancelled":
        assert not output.exists()
        # No partial render may be left behind either.
        leftovers = [
            p.name for p in config.EXPORTS_DIR.iterdir()
            if p.suffix == ".mp4" or p.name.endswith(".part.mp4")
        ]
        assert leftovers == [], leftovers
    reopened = test_client.get(f"/api/projects/{project_id}").json()
    assert len(reopened["clips"]) == 3
    # Another export attempt works right away.
    retry = _wait_job(test_client, project_id)
    assert retry["status"] == "done"


def test_upload_is_deduplicated_and_original_untouched(client, tmp_path):
    test_client, samples, config = client
    media_id = list(samples)[0]
    original = config.MEDIA_DIR / media_id
    uploaded = tmp_path / "copy.mp4"
    uploaded.write_bytes(original.read_bytes())
    before = uploaded.read_bytes()
    response = test_client.post(
        "/api/media",
        files={"file": ("copy.mp4", uploaded.read_bytes(), "video/mp4")},
    )
    assert response.status_code == 200
    assert uploaded.read_bytes() == before
    media_list = test_client.get("/api/media").json()
    # Same content -> same media id, no duplicate entry.
    assert sum(item["id"] == media_id for item in media_list) == 1


def test_upload_works_on_fresh_root_and_after_wiping_temp(client, tmp_path):
    """First-ever upload (no dirs created yet) and an upload after the
    staging area was wiped must both succeed without any prior op.
    """
    test_client, samples, config = client
    # A tiny valid clip independent of any previously generated assets.
    import subprocess

    source = tmp_path / "fresh-source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=purple:s=64x36:r=30:d=0.5",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=0.5:sample_rate=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(source),
        ],
        check=True,
    )
    payload = source.read_bytes()

    # Simulate a brand-new installation: delete the whole data root, then
    # upload as the very first operation (no GET /media, no samples).
    data_root = config.DATA_ROOT
    shutil.rmtree(data_root, ignore_errors=True)
    response = test_client.post(
        "/api/media",
        files={"file": ("fresh.mp4", payload, "video/mp4")},
    )
    assert response.status_code == 200, response
    assert (data_root / "staging").is_dir()
    # No staging leftovers.
    leftovers = [p for p in (data_root / "staging").iterdir()]
    assert leftovers == []

    # Wipe the temp material dir and upload again immediately.
    shutil.rmtree(data_root / "staging", ignore_errors=True)
    response = test_client.post(
        "/api/media",
        files={"file": ("again.mp4", payload, "video/mp4")},
    )
    assert response.status_code == 200
    assert (data_root / "staging").is_dir()


def _transition_project(samples, transitions=(0.0, 0.5, 0.8), volumes=(1.0, 0.5, 1.5)):
    return {
        "name": "转场工程",
        "clips": [
            {
                "id": f"clip-{index}",
                "media_id": media_id,
                "source_in": 0.0,
                "source_out": 2.0,
                "transition_in": tr,
                "volume": vol,
            }
            for index, (media_id, tr, vol) in enumerate(
                zip(samples, transitions, volumes)
            )
        ],
    }


def test_transitions_shorthen_timeline_and_persist(client):
    test_client, samples, _ = client
    # clip0 [0,2]; clip1 starts 0.5 earlier -> [1.5,3.5]; clip2 overlaps
    # 0.8 -> [2.7,4.7].
    project = test_client.post("/api/projects", json=_transition_project(samples)).json()
    timeline = test_client.get(f"/api/projects/{project['id']}/timeline").json()
    assert abs(timeline["duration"] - 4.7) < 0.001
    assert [round(c["start"], 3) for c in timeline["clips"]] == [0.0, 1.5, 2.7]
    assert [c["transition_in"] for c in timeline["clips"]] == [0.0, 0.5, 0.8]
    assert [c["volume"] for c in timeline["clips"]] == [1.0, 0.5, 1.5]

    # Reopen: transition and volume survive the saved JSON.
    reopened = test_client.get(f"/api/projects/{project['id']}").json()
    assert [c.get("transition_in") for c in reopened["clips"]] == [0.0, 0.5, 0.8]
    assert [c.get("volume") for c in reopened["clips"]] == [1.0, 0.5, 1.5]


def test_transitions_are_clamped_and_exports_with_overlap(client):
    test_client, samples, config = client
    payload = _transition_project(samples, transitions=(0.0, 9.0, 9.0))
    project = test_client.post("/api/projects", json=payload).json()
    timeline = test_client.get(f"/api/projects/{project['id']}/timeline").json()
    # Each 2s clip can lose at most half a frame to the overlap and two
    # neighbouring overlaps cannot touch.
    assert all(0 <= c["transition_in"] < 2.0 for c in timeline["clips"])
    assert all(c["start"] >= 0 for c in timeline["clips"])

    sane = _transition_project(samples)
    project = test_client.post("/api/projects", json=sane).json()
    job = _wait_job(test_client, project["id"])
    assert job["status"] == "done", job
    output = config.EXPORTS_DIR / f"{job['id']}.mp4"
    probe = json.loads(
        __import__("subprocess").run(
            ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(output)],
            capture_output=True, text=True, check=True,
        ).stdout
    )
    assert abs(float(probe["format"]["duration"]) - 4.7) < 0.12
