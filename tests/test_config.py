"""Çalışma dizini: alt klasörden çalıştırılsa da ajan bütün projeyi görür, fazlasını görmez."""

from pathlib import Path

from harness.core.config import find_project_root


def test_finds_git_root_from_a_subfolder(tmp_path: Path) -> None:
    (tmp_path / "proje" / ".git").mkdir(parents=True)
    subfolder = tmp_path / "proje" / "src" / "harness"
    subfolder.mkdir(parents=True)

    assert find_project_root(subfolder, home=tmp_path) == (tmp_path / "proje").resolve()


def test_without_git_uses_the_starting_folder(tmp_path: Path) -> None:
    folder = tmp_path / "klasor"
    folder.mkdir()

    assert find_project_root(folder, home=tmp_path) == folder.resolve()


def test_home_folder_is_never_the_project_even_if_it_is_a_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()  # ev klasörü depo
    folder = tmp_path / "Desktop" / "notlar"
    folder.mkdir(parents=True)

    assert find_project_root(folder, home=tmp_path) == folder.resolve()
