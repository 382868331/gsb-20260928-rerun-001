from __future__ import annotations

import subprocess

import pytest

from .conftest import HTTPError


def _make_video(path, seconds, color="0x2080D0", freq=550, fps=24, audio=True):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r={fps}:d={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=48000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def _upload(test_client, path, name=None):
    data = path.read_bytes()
    return test_client.post(
        "/api/media", files={"file": (name or path.name, data)}
    ).json()


def _shared_speed_project(test_client, samples):
    media_ids = list(samples)
    payload = {
        "name": "替换工程",
        "clips": [
            {
                "id": "clip-a",
                "media_id": media_ids[0],
                "source_in": 0.0,
                "source_out": 3.0,
                "speed_segments": [
                    {"source_start": 1.0, "source_end": 2.0, "rate": 2.0}
                ],
            },
            # Same media, different trim and a slow-motion segment.
            {
                "id": "clip-b",
                "media_id": media_ids[0],
                "source_in": 1.0,
                "source_out": 4.0,
                "speed_segments": [
                    {"source_start": 2.0, "source_end": 3.0, "rate": 0.5}
                ],
            },
            # Independent clip on another media.
            {
                "id": "clip-c",
                "media_id": media_ids[1],
                "source_in": 0.0,
                "source_out": 1.5,
            },
        ],
    }
    return test_client.post("/api/projects", json=payload).json()


def _delete_media(config, media_id, forget_index=False):
    (config.MEDIA_DIR / media_id).unlink(missing_ok=True)
    if forget_index:
        import json as _json

        index_path = config.MEDIA_DIR / "index.json"
        index = _json.loads(index_path.read_text(encoding="utf-8"))
        index.pop(media_id, None)
        index_path.write_text(_json.dumps(index), encoding="utf-8")


def test_preview_keep_source_blocks_short_replacement(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    missing_id = list(samples)[0]
    _delete_media(config, missing_id)

    short = tmp_path / "short.mp4"
    _make_video(short, 2)
    replacement = _upload(test_client, short)

    plan = test_client.post(
        f"/api/projects/{project['id']}/replacements/preview",
        json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_source"},
    ).json()
    assert plan["replacement"]["duration"] == pytest.approx(2.0, abs=0.1)
    assert plan["replacement"]["fps"] == pytest.approx(24.0, abs=0.5)
    assert plan["original"]["duration"] == pytest.approx(4.0, abs=0.1)
    assert plan["original"]["fps"] == pytest.approx(30.0, abs=0.5)
    assert plan["identical_to_original"] is False
    by_id = {c["clip_id"]: c for c in plan["clips"]}
    assert set(by_id) == {"clip-a", "clip-b"}
    assert by_id["clip-a"]["blocked"] and "时长不足" in by_id["clip-a"]["reasons"][0]
    assert by_id["clip-b"]["blocked"]
    assert by_id["clip-a"]["rendered_before"] == pytest.approx(2.5)  # 1 + 1/2 + 1
    assert by_id["clip-b"]["rendered_before"] == pytest.approx(4.0)  # 1 + 1/0.5 + 1

    # Independent clips are unaffected: the project still saves and the
    # timeline computes for the non-blocked media.
    timeline = test_client.get(f"/api/projects/{project['id']}/timeline").json()
    assert timeline["missing_media_ids"] == [missing_id]
    assert timeline["clips"][2]["media_missing"] is False

    # Blocked clips cannot be confirmed through the apply route.
    with pytest.raises(HTTPError) as excinfo:
        test_client.post(
            f"/api/projects/{project['id']}/replacements/apply",
            json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_source"},
        )
    assert excinfo.value.status_code == 400
    assert "时长不足" in excinfo.value.detail


def test_keep_source_apply_preserves_positions_and_timeline(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    missing_id = list(samples)[0]
    before = test_client.get(f"/api/projects/{project['id']}/timeline").json()
    _delete_media(config, missing_id)

    replacement_file = tmp_path / "long.mp4"
    _make_video(replacement_file, 4, fps=24)
    replacement = _upload(test_client, replacement_file)

    result = test_client.post(
        f"/api/projects/{project['id']}/replacements/apply",
        json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_source"},
    ).json()
    updated = result["project"]
    assert updated["version"] == project["version"] + 1
    by_id = {c["id"]: c for c in updated["clips"]}
    # Source time positions are kept exactly; only the media reference moves.
    assert by_id["clip-a"]["media_id"] == replacement["id"]
    assert by_id["clip-a"]["source_in"] == 0.0 and by_id["clip-a"]["source_out"] == 3.0
    assert by_id["clip-a"]["speed_segments"] == [
        {"source_start": 1.0, "source_end": 2.0, "rate": 2.0}
    ]
    assert by_id["clip-b"]["media_id"] == replacement["id"]
    assert by_id["clip-b"]["source_in"] == 1.0 and by_id["clip-b"]["source_out"] == 4.0
    assert by_id["clip-c"]["media_id"] == list(samples)[1]

    after = result["timeline"]
    assert after["missing_media_ids"] == []
    assert after["duration"] == pytest.approx(before["duration"])
    assert [c["start"] for c in after["clips"]] == [
        pytest.approx(c["start"]) for c in before["clips"]
    ]


def test_keep_duration_scales_shorter_replacement(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    missing_id = list(samples)[0]
    before = test_client.get(f"/api/projects/{project['id']}/timeline").json()
    _delete_media(config, missing_id)

    replacement_file = tmp_path / "half.mp4"
    _make_video(replacement_file, 2)
    replacement = _upload(test_client, replacement_file)

    plan = test_client.post(
        f"/api/projects/{project['id']}/replacements/preview",
        json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_duration"},
    ).json()
    by_id = {c["clip_id"]: c for c in plan["clips"]}
    assert not by_id["clip-a"]["blocked"]
    assert not by_id["clip-b"]["blocked"]
    # factor = 2/4 = 0.5: source times halve, rates halve, rendered
    # duration of every clip is preserved.
    assert by_id["clip-a"]["source_out"] == pytest.approx(1.5)
    rates_a = [seg["rate"] for seg in by_id["clip-a"]["speed_segments"]]
    assert rates_a == [pytest.approx(0.5), pytest.approx(1.0), pytest.approx(0.5)]
    assert by_id["clip-a"]["rendered_after"] == pytest.approx(
        by_id["clip-a"]["rendered_before"]
    )
    assert by_id["clip-b"]["source_in"] == pytest.approx(0.5)
    assert by_id["clip-b"]["source_out"] == pytest.approx(2.0)
    rates_b = [seg["rate"] for seg in by_id["clip-b"]["speed_segments"]]
    assert rates_b == [pytest.approx(0.5), pytest.approx(0.25), pytest.approx(0.5)]
    assert by_id["clip-b"]["rendered_after"] == pytest.approx(
        by_id["clip-b"]["rendered_before"]
    )

    result = test_client.post(
        f"/api/projects/{project['id']}/replacements/apply",
        json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_duration"},
    ).json()
    assert result["timeline"]["duration"] == pytest.approx(before["duration"])
    assert result["timeline"]["missing_media_ids"] == []


def test_replacement_without_audio_blocks_only_clips_that_need_it(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    missing_id = list(samples)[0]
    # clip-b is muted, so it does not need the replacement to have audio.
    clips = project["clips"]
    clips[1]["volume"] = 0.0
    test_client.put(f"/api/projects/{project['id']}", json={"name": project["name"], "clips": clips})
    _delete_media(config, missing_id)

    silent = tmp_path / "silent.mp4"
    _make_video(silent, 4, audio=False)
    replacement = _upload(test_client, silent)
    assert replacement["has_audio"] is False

    plan = test_client.post(
        f"/api/projects/{project['id']}/replacements/preview",
        json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_source"},
    ).json()
    by_id = {c["clip_id"]: c for c in plan["clips"]}
    assert by_id["clip-a"]["blocked"]
    assert "音轨" in by_id["clip-a"]["reasons"][0]
    assert not by_id["clip-b"]["blocked"]

    # The blocked clip stays; the independent unblocked clip confirms.
    result = test_client.post(
        f"/api/projects/{project['id']}/replacements/apply",
        json={
            "media_id": missing_id,
            "replacement_media_id": replacement["id"],
            "mode": "keep_source",
            "clip_ids": ["clip-b"],
        },
    ).json()
    by_id = {c["id"]: c for c in result["project"]["clips"]}
    assert by_id["clip-a"]["media_id"] == missing_id
    assert by_id["clip-b"]["media_id"] == replacement["id"]
    assert result["timeline"]["missing_media_ids"] == [missing_id]


def test_partial_replacement_leaves_unselected_clips_unchanged(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    missing_id = list(samples)[0]
    _delete_media(config, missing_id)

    replacement_file = tmp_path / "long.mp4"
    _make_video(replacement_file, 4)
    replacement = _upload(test_client, replacement_file)

    result = test_client.post(
        f"/api/projects/{project['id']}/replacements/apply",
        json={
            "media_id": missing_id,
            "replacement_media_id": replacement["id"],
            "mode": "keep_source",
            "clip_ids": ["clip-a"],
        },
    ).json()
    by_id = {c["id"]: c for c in result["project"]["clips"]}
    assert by_id["clip-a"]["media_id"] == replacement["id"]
    # Unselected clip keeps the old (still missing) reference untouched.
    assert by_id["clip-b"]["media_id"] == missing_id
    assert by_id["clip-b"]["source_in"] == 1.0 and by_id["clip-b"]["source_out"] == 4.0
    assert result["timeline"]["missing_media_ids"] == [missing_id]
    timeline_clip_b = result["timeline"]["clips"][1]
    assert timeline_clip_b["media_missing"] is True


def test_same_name_different_content_is_not_original(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    original_id = list(samples)[0]
    original_info = test_client.get("/api/media").json()
    original_name = next(m["filename"] for m in original_info if m["id"] == original_id)

    # Same filename, different content: imported as a new media entry
    # and never treated as the original.
    fake = tmp_path / "fake.mp4"
    _make_video(fake, 4, color="0x802020", freq=300)
    replacement = _upload(test_client, fake, name=original_name)
    assert replacement["id"] != original_id

    plan = test_client.post(
        f"/api/projects/{project['id']}/replacements/preview",
        json={"media_id": original_id, "replacement_media_id": replacement["id"], "mode": "keep_source"},
    ).json()
    assert plan["identical_to_original"] is False

    # Identical content (hash match) is recognised as the original.
    original_bytes = (config.MEDIA_DIR / original_id).read_bytes()
    clone = tmp_path / "clone.mp4"
    clone.write_bytes(original_bytes)
    same = _upload(test_client, clone, name="different-name.mp4")
    assert same["id"] == original_id  # dedup by content hash
    plan = test_client.post(
        f"/api/projects/{project['id']}/replacements/preview",
        json={"media_id": original_id, "replacement_media_id": same["id"], "mode": "keep_source"},
    ).json()
    assert plan["identical_to_original"] is True


def test_preview_without_library_record_uses_clip_ranges(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    missing_id = list(samples)[0]
    _delete_media(config, missing_id, forget_index=True)

    replacement_file = tmp_path / "rep.mp4"
    _make_video(replacement_file, 2)
    replacement = _upload(test_client, replacement_file)

    plan = test_client.post(
        f"/api/projects/{project['id']}/replacements/preview",
        json={"media_id": missing_id, "replacement_media_id": replacement["id"], "mode": "keep_duration"},
    ).json()
    assert plan["original"] is None
    assert plan["identical_to_original"] is False
    # Fallback original duration = max source_out (4.0) -> factor 0.5.
    by_id = {c["clip_id"]: c for c in plan["clips"]}
    assert by_id["clip-b"]["source_out"] == pytest.approx(2.0)
    assert by_id["clip-b"]["rendered_after"] == pytest.approx(
        by_id["clip-b"]["rendered_before"]
    )


def test_apply_rejects_unknown_replacement_and_unreferenced_media(client, tmp_path):
    test_client, samples, config = client
    project = _shared_speed_project(test_client, samples)
    media_ids = list(samples)
    with pytest.raises(HTTPError) as excinfo:
        test_client.post(
            f"/api/projects/{project['id']}/replacements/preview",
            json={"media_id": media_ids[0], "replacement_media_id": "nope.mp4", "mode": "keep_source"},
        )
    assert excinfo.value.status_code == 400
    with pytest.raises(HTTPError) as excinfo:
        test_client.post(
            f"/api/projects/{project['id']}/replacements/preview",
            json={"media_id": "missing.mp4", "replacement_media_id": media_ids[1], "mode": "keep_source"},
        )
    assert excinfo.value.status_code == 400
