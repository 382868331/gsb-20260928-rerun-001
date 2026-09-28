from __future__ import annotations


def test_trimming_shifts_later_clips(client):
    _, samples, _ = client
    media_ids = list(samples)

    from app.models import Clip, Project, ProjectPayload
    from app.store import save_project
    from app.timeline import compute_timeline
    from app.media import load_index
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    project = Project(
        id=uuid.uuid4().hex,
        name="t",
        clips=[
            Clip(id="a", media_id=media_ids[0], source_in=0.0, source_out=2.0),
            Clip(id="b", media_id=media_ids[1], source_in=1.0, source_out=3.5),
            Clip(id="c", media_id=media_ids[2], source_in=0.0, source_out=4.0),
        ],
        version=1,
        created_at=now,
        updated_at=now,
    )
    save_project(project)
    timeline = compute_timeline(project, load_index())
    assert timeline.duration == 8.5
    assert [(c.start, c.end, c.duration) for c in timeline.clips] == [
        (0.0, 2.0, 2.0),
        (2.0, 4.5, 2.5),
        (4.5, 8.5, 4.0),
    ]

    # Trim the first clip by 1.2s: every later start must move left by
    # exactly the same amount.
    project.clips[0].source_out = 0.8
    save_project(project)
    timeline = compute_timeline(project, load_index())
    assert timeline.duration == 7.3
    assert [round(c.start, 6) for c in timeline.clips] == [0.0, 0.8, 3.3]
    assert timeline.missing_media_ids == []


def test_missing_media_is_reported_not_crashing(client):
    _, _, _ = client
    from app.models import Clip, Project
    from app.timeline import compute_timeline
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    project = Project(
        id=uuid.uuid4().hex,
        name="t",
        clips=[Clip(id="x", media_id="gone.mp4", source_in=0, source_out=1)],
        version=1,
        created_at=now,
        updated_at=now,
    )
    timeline = compute_timeline(project, {})
    assert timeline.clips[0].media_missing is True
    assert timeline.missing_media_ids == ["gone.mp4"]
