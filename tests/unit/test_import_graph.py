from pathlib import Path

from architecture_checks.import_graph import FirstPartyImportGraph, ImportCycle


def test_import_graph_detects_relative_cycle(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "first.py").write_text("from . import second\n", encoding="utf-8")
    (package / "second.py").write_text("from .first import value\n", encoding="utf-8")

    graph = FirstPartyImportGraph.from_project(
        tmp_path,
        source_packages=("sample",),
        source_modules=(),
    )

    assert graph.cycles() == (ImportCycle(("sample.first", "sample.second")),)


def test_backend_has_no_first_party_import_cycles() -> None:
    project_root = Path(__file__).resolve().parents[2]

    graph = FirstPartyImportGraph.from_project(project_root)

    assert graph.cycles() == ()
