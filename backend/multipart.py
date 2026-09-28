"""Tiny multipart/form-data parser (cgi was removed in Python 3.13)."""

from __future__ import annotations


def parse_multipart(body: bytes, content_type: str):
    """Return {field_name: {"filename": str|None, "data": bytes}}."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
    if not boundary:
        raise ValueError("missing multipart boundary")
    delim = b"--" + boundary.encode()
    fields = {}
    segments = body.split(delim)
    for seg in segments:
        if not seg or seg in (b"--", b"--\r\n", b"\r\n"):
            continue
        seg = seg.lstrip(b"\r\n")
        if seg.startswith(b"--"):
            break
        if b"\r\n\r\n" not in seg:
            continue
        header_blob, payload = seg.split(b"\r\n\r\n", 1)
        payload = payload
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        headers = {}
        for line in header_blob.decode("utf-8", "replace").split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        disp = headers.get("content-disposition", "")
        name = None
        filename = None
        for token in disp.split(";"):
            token = token.strip()
            if token.startswith("name="):
                name = token[5:].strip('"')
            elif token.startswith("filename="):
                filename = token[9:].strip('"')
        if name:
            fields[name] = {"filename": filename, "data": payload}
    return fields
