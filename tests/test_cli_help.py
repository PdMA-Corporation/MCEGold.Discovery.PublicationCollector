import pytest

from mcegold_discovery_publication_collector.__main__ import main


def test_help_displays_usage_and_exits_without_starting_collector(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage: mcegold-publication-collector" in output
    assert "config_path" in output
