"""The heavy-compile bundles: their size and bytes, and the size whose build exhausts the hostcall
fuel of componentize-py."""

import tarfile

from wdf_compile import bundle, heavy
from wdf_compile.__main__ import main as compile_main
from wdf_compile.heavy import main


def test_heavy_bundle_size_and_bytes(tmp_path):
    size_mib = 0.25
    paths = []
    for n in (1, 2):
        assert main(["--size-mib", str(size_mib), "--out", str(tmp_path / f"b{n}")]) == 0
        paths.append(tmp_path / f"b{n}" / bundle.NAME)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    info = bundle.info(paths[0])
    assert info.modules == {heavy.ENTRY, "generated"}
    assert info.lockfile_sha256 is not None
    with tarfile.open(paths[0]) as tar:
        source = tar.getmember("generated.py").size
    # The source size of the stand-in kernel module that the measured sizes were taken with.
    assert source == 262_346
    print(f"{size_mib} MiB asked, {source} bytes of source, bundle {paths[0].stat().st_size} bytes")


def test_heavy_over_fuel_budget_is_compile_failed(tmp_path, capsys):
    path = heavy.write(heavy.FUEL_EXHAUSTED_MIB, tmp_path / "b")
    argv = ["build", "--bundle", str(path), "--entry", heavy.ENTRY, "--out", str(tmp_path / "out")]
    code = compile_main(argv)
    reason = capsys.readouterr().err.strip().splitlines()[-1]
    with capsys.disabled():
        print(f"{heavy.FUEL_EXHAUSTED_MIB} MiB: exit {code}, {reason}")
    assert code == 3
    assert reason.startswith("reason: compile_failed: componentize-py: ")
    assert "fuel allocated for hostcalls has been exhausted" in reason
