"""Media replacement for projects referencing missing sources.

Covers: per-clip impact preview (duration / fps / audio differences),
keep-source vs keep-duration modes, partial replacement of clips that
share one media, blocking of under-length or audio-less replacements,
same-name protection, and undo restoring picture order + A/V sync.
"""

from __future__ import annotations

import json
import subprocess

from app import media as media_module


def _make_media(path, seconds, color="0x808080", freq=330, fps=30, audio=True):
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r={fps}:d={seconds}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return path


def _upload(test_client, path, name=None):
    data = path.read_bytes() if hasattr(path, "read_bytes") else path
    return test_client.post(
        "/api/media", files={"file": (name or "replacement.mp4", data)}
    ).json()


def _shared_media_project(test_client, media_id, other_media_id):
    """Two clips with different speeds sharing one media, plus an
    independent third clip on another media."""
    payload = {
        "name": "替换工程",
        "clips": [
            {
                "id": "clip-a1",
                "media_id": media_id,
                "source_in": 0.5,
                "source_out": 3.0,
                "speed_segments": [
                    {"source_start": 1.0, "source_end": 2.0, "rate": 2.0}
                ],
            },
            {
                "id": "clip-a2",
                "media_id": media_id,
                "source_in": 0.0,
                "source_out": 2.0,
                "speed_segments": [
                    {"source_start": 0.5, "source_end": 1.5, "rate": 0.5}
                ],
            },
            {
                "id": "clip-b",
                "media_id": other_media_id,
                "source_in": 0.5,
                "source_out": 1.5,
            },
        ],
    }
    return test_client.post("/api/projects", json=payload).json()


def _remove_media_file(config, media_id):
    (config.MEDIA_DIR / media_id).unlink()


def _remove_index_entry(media_id):
    index = media_module.load_index()
    index.pop(media_id, None)
    media_module.save_index(index)


def _preview(test_client, project_id, body):
    return test_client.post(
        f"/api/projects/{project_id}/replacement/preview", json=body
    ).json()


def _confirm(test_client, project_id, body):
    return test_client.post(
        f"/api/projects/{project_id}/replacement/confirm", json=body
    ).json()


def test_partial_replace_shared_media_and_undo_restores_sync(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    target, other = media_ids[0], media_ids[1]
    project = _shared_media_project(test_client, target, other)
    project_id = project["id"]
    clips_before = test_client.get(f"/api/projects/{project_id}").json()["clips"]

    _remove_media_file(config, target)
    timeline_missing = test_client.get(f"/api/projects/{project_id}/timeline").json()
    assert timeline_missing["missing_media_ids"] == [target]

    replacement = _upload(
        test_client, _make_media(tmp_path / "rep.mp4", 6, fps=24, freq=500)
    )
    assert replacement["fps"] == 24.0

    body = {
        "media_id": target,
        "replacement_media_id": replacement["id"],
        "mode": "keep_source",
        "clip_ids": ["clip-a1"],
    }
    preview = _preview(test_client, project_id, body)
    plans = {c["clip_id"]: c for c in preview["clips"]}
    assert plans["clip-a1"]["status"] == "ok"
    assert plans["clip-a1"]["selected"] is True
    assert any("帧率" in note for note in plans["clip-a1"]["notes"])
    # clip-a2 shares the media but was not selected: stays untouched.
    assert plans["clip-a2"]["selected"] is False
    assert plans["clip-a2"]["status"] == "ok"
    # Preview must not modify the project.
    assert test_client.get(f"/api/projects/{project_id}").json()["clips"] == clips_before

    result = _confirm(test_client, project_id, body)
    assert result["applied"] == ["clip-a1"]
    assert result["blocked"] == []
    clips = {c["id"]: c for c in result["project"]["clips"]}
    assert clips["clip-a1"]["media_id"] == replacement["id"]
    assert clips["clip-a2"]["media_id"] == target
    assert clips["clip-b"]["media_id"] == other

    timeline = test_client.get(f"/api/projects/{project_id}/timeline").json()
    by_id = {c["clip_id"]: c for c in timeline["clips"]}
    # Picture order preserved; only the replaced clip is no longer missing.
    assert [c["clip_id"] for c in timeline["clips"]] == ["clip-a1", "clip-a2", "clip-b"]
    assert by_id["clip-a1"]["media_missing"] is False
    assert by_id["clip-a2"]["media_missing"] is True
    # A/V sync: speed segments (shared video+audio mapping) unchanged.
    assert [s["rate"] for s in by_id["clip-a1"]["speed_segments"]] == [1.0, 2.0, 1.0]
    assert by_id["clip-a1"]["start"] == 0.0
    assert by_id["clip-a2"]["start"] == timeline_missing["clips"][1]["start"]

    # Undo: restore the pre-replacement clips; timeline returns exactly
    # to the missing-media state (order + per-clip source mapping).
    test_client.put(
        f"/api/projects/{project_id}",
        json={"name": "替换工程", "clips": clips_before},
    )
    restored = test_client.get(f"/api/projects/{project_id}/timeline").json()
    assert restored == timeline_missing


def test_short_replacement_blocks_only_affected_clip(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    target, other = media_ids[0], media_ids[1]
    project = _shared_media_project(test_client, target, other)
    project_id = project["id"]
    _remove_media_file(config, target)

    short = _upload(test_client, _make_media(tmp_path / "short.mp4", 2, freq=510))
    body = {
        "media_id": target,
        "replacement_media_id": short["id"],
        "mode": "keep_source",
    }
    preview = _preview(test_client, project_id, body)
    plans = {c["clip_id"]: c for c in preview["clips"]}
    # clip-a1 needs source up to 3.0s; the 2s replacement cannot cover it.
    assert plans["clip-a1"]["status"] == "blocked"
    assert any("时长不足" in reason for reason in plans["clip-a1"]["reasons"])
    # clip-a2 only needs 2.0s and fits.
    assert plans["clip-a2"]["status"] == "ok"

    result = _confirm(test_client, project_id, body)
    assert result["applied"] == ["clip-a2"]
    assert [b["clip_id"] for b in result["blocked"]] == ["clip-a1"]
    timeline = test_client.get(f"/api/projects/{project_id}/timeline").json()
    by_id = {c["clip_id"]: c for c in timeline["clips"]}
    assert by_id["clip-a1"]["media_missing"] is True
    assert by_id["clip-a2"]["media_missing"] is False
    # The independent clip on another media was never involved.
    assert by_id["clip-b"]["media_id"] == other


def test_replacement_without_audio_blocks_clips_that_need_it(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    target = media_ids[0]
    payload = {
        "name": "音轨工程",
        "clips": [
            {"id": "clip-loud", "media_id": target, "source_in": 0.0, "source_out": 2.0},
            {
                "id": "clip-muted",
                "media_id": target,
                "source_in": 0.0,
                "source_out": 2.0,
                "volume": 0.0,
            },
        ],
    }
    project_id = test_client.post("/api/projects", json=payload).json()["id"]
    _remove_media_file(config, target)

    silent = _upload(
        test_client, _make_media(tmp_path / "silent.mp4", 4, audio=False)
    )
    assert silent["has_audio"] is False
    body = {
        "media_id": target,
        "replacement_media_id": silent["id"],
        "mode": "keep_source",
    }
    preview = _preview(test_client, project_id, body)
    plans = {c["clip_id"]: c for c in preview["clips"]}
    assert plans["clip-loud"]["status"] == "blocked"
    assert any("音轨" in reason for reason in plans["clip-loud"]["reasons"])
    # A muted clip does not need the audio track and may proceed.
    assert plans["clip-muted"]["status"] == "ok"

    result = _confirm(test_client, project_id, body)
    assert result["applied"] == ["clip-muted"]
    assert [b["clip_id"] for b in result["blocked"]] == ["clip-loud"]


def test_same_name_different_content_is_never_matched(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    target = media_ids[0]
    original_name = samples[target].filename
    project = _shared_media_project(test_client, target, media_ids[1])
    project_id = project["id"]
    _remove_media_file(config, target)

    # A different file that merely reuses the original filename.
    impostor = _upload(
        test_client,
        _make_media(tmp_path / "impostor.mp4", 4, color="0x111111", freq=555),
        name=original_name,
    )
    assert impostor["id"] != target
    timeline = test_client.get(f"/api/projects/{project_id}/timeline").json()
    assert timeline["missing_media_ids"] == [target]
    assert all(not c["media_id"] == impostor["id"] for c in
               test_client.get(f"/api/projects/{project_id}").json()["clips"])


def test_keep_duration_mode_preserves_timeline_and_sync(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    target, other = media_ids[0], media_ids[1]
    project = _shared_media_project(test_client, target, other)
    project_id = project["id"]
    _remove_media_file(config, target)
    before = test_client.get(f"/api/projects/{project_id}/timeline").json()

    # Half the source duration (4s -> 2s): positions and rates scale by 0.5.
    half = _upload(test_client, _make_media(tmp_path / "half.mp4", 2, freq=520))
    body = {
        "media_id": target,
        "replacement_media_id": half["id"],
        "mode": "keep_duration",
    }
    preview = _preview(test_client, project_id, body)
    assert preview["timeline_duration_after"] == preview["timeline_duration_before"]
    assert preview["timeline_duration_before"] == before["duration"]
    plans = {c["clip_id"]: c for c in preview["clips"]}
    assert plans["clip-a1"]["status"] == "ok"
    assert abs(plans["clip-a1"]["source_in_after"] - 0.25) < 1e-6
    assert abs(plans["clip-a1"]["source_out_after"] - 1.5) < 1e-6
    assert plans["clip-a1"]["rendered_after"] == plans["clip-a1"]["rendered_before"]

    result = _confirm(test_client, project_id, body)
    assert sorted(result["applied"]) == ["clip-a1", "clip-a2"]
    after = test_client.get(f"/api/projects/{project_id}/timeline").json()
    assert after["duration"] == before["duration"]
    assert after["missing_media_ids"] == []
    by_id = {c["clip_id"]: c for c in after["clips"]}
    # Rates scaled by the same factor keep every segment's rendered
    # length (and therefore A/V sync and clip order) identical.
    assert [s["rate"] for s in by_id["clip-a1"]["speed_segments"]] == [0.5, 1.0, 0.5]
    a1_segments = by_id["clip-a1"]["speed_segments"]
    before_a1 = before["clips"][0]["speed_segments"]
    for old, new in zip(before_a1, a1_segments):
        assert abs((new["clip_end"] - new["clip_start"])
                   - (old["clip_end"] - old["clip_start"])) < 1e-6
    assert [c["start"] for c in after["clips"]] == [c["start"] for c in before["clips"]]


def test_keep_duration_blocked_when_original_unknown(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    target = media_ids[0]
    project = _shared_media_project(test_client, target, media_ids[1])
    project_id = project["id"]
    _remove_media_file(config, target)
    _remove_index_entry(target)

    replacement = _upload(test_client, _make_media(tmp_path / "rep2.mp4", 4))
    body = {
        "media_id": target,
        "replacement_media_id": replacement["id"],
        "mode": "keep_duration",
    }
    preview = _preview(test_client, project_id, body)
    assert preview["original"] is None
    assert all(c["status"] == "blocked" for c in preview["clips"])
    # keep_source still works without original metadata.
    body["mode"] = "keep_source"
    preview = _preview(test_client, project_id, body)
    assert all(c["status"] == "ok" for c in preview["clips"])
    result = _confirm(test_client, project_id, body)
    assert sorted(result["applied"]) == ["clip-a1", "clip-a2"]


def test_replacement_of_unknown_media_id_rejected(client, tmp_path):
    test_client, samples, config = client
    media_ids = list(samples)
    project = _shared_media_project(test_client, media_ids[0], media_ids[1])
    project_id = project["id"]
    body = {
        "media_id": media_ids[0],
        "replacement_media_id": "not-in-library.mp4",
        "mode": "keep_source",
    }
    try:
        _preview(test_client, project_id, body)
    except Exception as exc:  # HTTPError from the direct client
        assert getattr(exc, "status_code", None) == 400
    else:  # pragma: no cover - defensive
        raise AssertionError("expected 400 for unknown replacement media")
