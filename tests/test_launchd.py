from __future__ import annotations

import plistlib

from memo import launchd


import pytest


@pytest.fixture
def tools(tmp_path):
    """Stand-in tp7/ffmpeg binaries in a directory that is not on PATH."""
    directory = tmp_path / "tools"
    directory.mkdir()
    for name in ("tp7", "ffmpeg"):
        executable = directory / name
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
    return directory


def build(tmp_path, tools=None):
    tools = tools or tmp_path / "tools"
    return launchd.build_plist(
        memo_exe="/opt/tools/bin/memo",
        memo_dir=tmp_path / "Memos",
        tp7=str(tools / "tp7"),
        ffmpeg=str(tools / "ffmpeg"),
        home="/Users/tester",
    )


def test_plist_shape(tmp_path):
    plist = build(tmp_path)
    assert plist["Label"] == "local.memo.sync"
    assert plist["ProgramArguments"] == ["/opt/tools/bin/memo", "sync", "--auto"]
    assert plist["ThrottleInterval"] == 10
    assert plist["EnvironmentVariables"]["HOME"] == "/Users/tester"
    log = str(tmp_path / "Memos" / ".memo" / "launchd.log")
    assert plist["StandardOutPath"] == log
    assert plist["StandardErrorPath"] == log


def test_usb_match_covers_both_personalities(tmp_path):
    matching = build(tmp_path)["LaunchEvents"]["com.apple.iokit.matching"]
    assert len(matching) == 2
    for entry in matching.values():
        assert entry["IOProviderClass"] == "IOUSBDevice"
        assert entry["idVendor"] == 9063
        assert "IOMatchLaunchStream" not in entry
    assert {entry["idProduct"] for entry in matching.values()} == {32793, 25}


def test_path_has_tool_dirs_then_system(tmp_path, tools):
    path = build(tmp_path, tools)["EnvironmentVariables"]["PATH"].split(":")
    assert path[-2:] == ["/usr/bin", "/bin"]
    assert str(tools) in path
    assert len(path) == len(set(path))


def test_path_skips_tools_that_are_not_installed(tmp_path):
    path = build(tmp_path)["EnvironmentVariables"]["PATH"].split(":")
    assert path == ["/usr/bin", "/bin"]


def test_plist_round_trips(tmp_path):
    plist = build(tmp_path)
    written = launchd.write_plist(plist, tmp_path / "local.memo.sync.plist")
    assert plistlib.loads(written.read_bytes()) == plist
