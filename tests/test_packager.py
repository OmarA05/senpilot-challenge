import zipfile

from uarb_agent.packager import make_zip


def _files(tmp_path, sizes):
    paths = []
    for i, size in enumerate(sizes):
        p = tmp_path / f"{i}.pdf"
        p.write_bytes(b"x" * size)
        paths.append(p)
    return paths


def test_zips_all_files(tmp_path):
    files = _files(tmp_path, [100, 200, 300])
    result = make_zip(files, tmp_path / "out.zip", max_bytes=10_000)
    assert result.skipped == []
    with zipfile.ZipFile(result.path) as zf:
        assert sorted(zf.namelist()) == ["0.pdf", "1.pdf", "2.pdf"]
        assert zf.read("1.pdf") == b"x" * 200


def test_skips_files_over_limit(tmp_path):
    files = _files(tmp_path, [400, 700, 500])
    result = make_zip(files, tmp_path / "out.zip", max_bytes=1000)
    assert [f.name for f in result.included] == ["0.pdf", "2.pdf"]
    assert [f.name for f in result.skipped] == ["1.pdf"]


def test_no_files_means_no_zip(tmp_path):
    result = make_zip([], tmp_path / "out.zip")
    assert result.path is None
    assert not (tmp_path / "out.zip").exists()
