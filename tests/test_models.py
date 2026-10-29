import hashlib
import http.server
import io
import tarfile
import threading
import zipfile
from functools import partial

import pytest

from kong import models


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files with Range support; can cut a response short once."""

    cut_once: set = set()

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.translate_path(self.path)
        try:
            data = open(path, "rb").read()
        except OSError:
            self.send_error(404)
            return
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.path in self.cut_once:
            self.cut_once.discard(self.path)
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(RangeHandler, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


def _tar(root, name, files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for n, data in files.items():
            info = tarfile.TarInfo(n)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    (root / name).write_bytes(buf.getvalue())
    return buf.getvalue()


def test_tar_install_with_checksum_and_resume(server, monkeypatch):
    root, base = server
    payload = _tar(root, "m.tar.bz2", {"top/model.onnx": b"x" * 200_000, "top/tokens.txt": b"a\nb\n", "top/junk": b"z"})
    RangeHandler.cut_once = {"/m.tar.bz2"}
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    spec = models.ModelSpec(
        id="t1", label="t", kind="tar",
        sources=(models.Source(base + "m.tar.bz2", "m.tar.bz2", len(payload), hashlib.sha256(payload).hexdigest()),),
        keep=("top/model.onnx", "top/tokens.txt"),
    )
    seen = []
    models.install(spec, lambda d, t: seen.append(d))
    assert spec.installed()
    assert (spec.dir / "tokens.txt").read_bytes() == b"a\nb\n"
    assert not (spec.dir / "junk").exists()
    assert seen[-1] == len(payload)


def test_checksum_mismatch_is_rejected(server, monkeypatch):
    root, base = server
    (root / "f.bin").write_bytes(b"hello")
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    spec = models.ModelSpec(id="t2", label="t", kind="files",
                            sources=(models.Source(base + "f.bin", "f.bin", 5, "0" * 64),))
    with pytest.raises(models.DownloadError):
        models.install(spec)
    assert not spec.installed()
    assert not (spec.dir / "f.bin").exists()


def test_optional_files_and_wheel(server):
    root, base = server
    (root / "a.json").write_bytes(b"{}")
    (root / "vocabulary.txt").write_bytes(b"v")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("nvidia/cublas/bin/cublas64_12.dll", b"dll")
    (root / "w.whl").write_bytes(buf.getvalue())

    files = models.ModelSpec(id="t3", label="t", kind="files", sources=(
        models.Source(base + "a.json", "a.json"),
        models.Source(base + "vocabulary.json", "vocabulary.json", optional=True),
        models.Source(base + "vocabulary.txt", "vocabulary.txt", optional=True),
    ))
    models.install(files)
    assert files.installed()

    wheel = models.ModelSpec(id="t4", label="t", kind="wheel",
                             sources=(models.Source(base + "w.whl", "w.whl"),),
                             keep=("nvidia/cublas/bin/cublas64_12.dll",))
    models.install(wheel)
    assert (wheel.dir / "cublas64_12.dll").read_bytes() == b"dll"


def test_pinned_specs_are_consistent():
    for spec in (models.SENSEVOICE, models.CUBLAS):
        assert all(len(s.sha256) == 64 and s.size > 0 for s in spec.sources)
    assert models.WHISPER_TURBO.sources[-1].name == "model.bin"
