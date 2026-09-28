"""HTTP API built on the Python standard library."""

from __future__ import annotations

import json
import mimetypes
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from media import (EXPORTS_DIR, MEDIA_DIR, describe, ensure_samples,
                   import_file, load_library)
from multipart import parse_multipart
from projects import list_projects, load_project, save_project
from render import MANAGER, RenderError, validate
from timeline import Timeline

HOST, PORT = "127.0.0.1", 8000


class Handler(BaseHTTPRequestHandler):
    server_version = "ClipFlow/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[api] " + fmt % args + "\n")

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _file(self, path, download_name=None):
        if not path.is_file():
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        if download_name:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/health":
            return self._json({"ok": True})
        if p == "/api/media":
            return self._json({"media": [describe(i) for i in load_library()]})
        if p == "/api/projects":
            return self._json({"projects": list_projects()})
        if p.startswith("/api/projects/"):
            name = p.rsplit("/", 1)[-1]
            try:
                return self._json(load_project(name))
            except FileNotFoundError:
                return self._json({"error": "project not found"}, 404)
            except json.JSONDecodeError:
                return self._json({"error": "project file corrupted"}, 500)
        if p.startswith("/api/render/"):
            job = MANAGER.jobs.get(p.rsplit("/", 1)[-1])
            return self._json(job.to_dict() if job else {"error": "no job"},
                              200 if job else 404)
        if p.startswith("/media/"):
            return self._file((MEDIA_DIR / Path(p).name).resolve())
        if p.startswith("/exports/"):
            return self._file(EXPORTS_DIR / Path(p).name)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/media/samples":
                items = ensure_samples()
                return self._json({"media": [describe(i) for i in items]})
            if p == "/api/media/import":
                return self._handle_import()
            if p == "/api/render":
                return self._handle_render()
            if p.startswith("/api/render/") and p.endswith("/cancel"):
                job_id = p.split("/")[-2]
                return self._json({"cancelled": MANAGER.cancel(job_id)})
            return self._json({"error": "not found"}, 404)
        except RenderError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_PUT(self):
        p = urlparse(self.path).path
        if not p.startswith("/api/projects/"):
            return self._json({"error": "not found"}, 404)
        name = p.rsplit("/", 1)[-1]
        try:
            data = self._read_json()
            timeline = Timeline.from_dict(data.get("timeline", {}))
            validate(timeline)
        except RenderError as exc:
            return self._json({"error": str(exc)}, 400)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            return self._json({"error": f"bad project json: {exc}"}, 400)
        saved = save_project(name, timeline,
                             {k: v for k, v in data.items() if k != "timeline"})
        return self._json(saved)

    def _handle_import(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._json({"error": "expected multipart upload"}, 400)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        fields = parse_multipart(body, ctype)
        item = fields.get("file")
        if not item or not item.get("filename"):
            return self._json({"error": "no file"}, 400)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(item["data"])
            tmp_name = tmp.name
        try:
            return self._json(import_file(tmp_name, item["filename"]))
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def _handle_render(self):
        data = self._read_json()
        kind = data.get("kind", "export")
        if kind not in ("preview", "export"):
            return self._json({"error": "kind must be preview|export"}, 400)
        try:
            timeline = Timeline.from_dict(data.get("timeline", {}))
        except (KeyError, TypeError, ValueError) as exc:
            return self._json({"error": f"bad timeline: {exc}"}, 400)
        project_name = data.get("project_name") or "untitled"
        save_project(project_name, timeline)
        job = MANAGER.start(timeline, kind, project_name)
        return self._json(job.to_dict(), 202)


def main():
    ensure_samples()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ClipFlow API on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
