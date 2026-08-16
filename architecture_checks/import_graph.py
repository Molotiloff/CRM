from __future__ import annotations

import argparse
import ast
import importlib.util
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SOURCE_PACKAGES = (
    "api",
    "app",
    "db_asyncpg",
    "domain",
    "gutils",
    "handlers",
    "keyboards",
    "middlewares",
    "models",
    "observability",
    "services",
    "telegram_adapters",
)
DEFAULT_SOURCE_MODULES = ("bot_app.py", "config.py", "main.py")


@dataclass(frozen=True, slots=True)
class ImportCycle:
    modules: tuple[str, ...]

    def __str__(self) -> str:
        return " -> ".join((*self.modules, self.modules[0]))


class FirstPartyImportGraph:
    def __init__(self, dependencies: dict[str, frozenset[str]]) -> None:
        self._dependencies = dependencies

    @property
    def module_count(self) -> int:
        return len(self._dependencies)

    @classmethod
    def from_project(
        cls,
        project_root: Path,
        *,
        source_packages: tuple[str, ...] = DEFAULT_SOURCE_PACKAGES,
        source_modules: tuple[str, ...] = DEFAULT_SOURCE_MODULES,
    ) -> FirstPartyImportGraph:
        module_paths = _discover_modules(project_root, source_packages, source_modules)
        known_modules = frozenset(module_paths)
        dependencies = {
            module: frozenset(_module_dependencies(module, path, known_modules) - {module})
            for module, path in module_paths.items()
        }
        return cls(dependencies)

    def cycles(self) -> tuple[ImportCycle, ...]:
        components = _strongly_connected_components(self._dependencies)
        return tuple(
            ImportCycle(tuple(sorted(component))) for component in components if len(component) > 1
        )


def _discover_modules(
    project_root: Path,
    source_packages: tuple[str, ...],
    source_modules: tuple[str, ...],
) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for package_name in source_packages:
        package_root = project_root / package_name
        if not package_root.is_dir():
            continue
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            modules[_module_name(project_root, path)] = path
    for filename in source_modules:
        path = project_root / filename
        if path.is_file():
            modules[path.stem] = path
    return modules


def _module_name(project_root: Path, path: Path) -> str:
    relative = path.relative_to(project_root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_dependencies(
    module: str,
    path: Path,
    known_modules: frozenset[str],
) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    dependencies: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dependencies.update(_known_import_target(alias.name, known_modules))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_module = _resolve_from_import(node, package)
        if not imported_module:
            continue
        dependencies.update(_known_import_target(imported_module, known_modules))
        for alias in node.names:
            candidate = f"{imported_module}.{alias.name}"
            dependencies.update(_known_import_target(candidate, known_modules))
    return dependencies


def _resolve_from_import(node: ast.ImportFrom, package: str) -> str:
    if node.level == 0:
        return node.module or ""
    if not package:
        return ""
    relative_name = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(relative_name, package)
    except (ImportError, ValueError):
        return ""


def _known_import_target(
    imported_name: str,
    known_modules: frozenset[str],
) -> set[str]:
    parts = imported_name.split(".")
    for index in range(len(parts), 0, -1):
        candidate = ".".join(parts[:index])
        if candidate in known_modules:
            return {candidate}
    return set()


def _strongly_connected_components(
    graph: dict[str, frozenset[str]],
) -> tuple[frozenset[str], ...]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[frozenset[str]] = []

    def visit(module: str) -> None:
        nonlocal index
        indices[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)

        for dependency in graph[module]:
            if dependency not in indices:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[dependency])

        if lowlinks[module] != indices[module]:
            return
        component: set[str] = set()
        while stack:
            dependency = stack.pop()
            on_stack.remove(dependency)
            component.add(dependency)
            if dependency == module:
                break
        components.append(frozenset(component))

    for module in graph:
        if module not in indices:
            visit(module)
    return tuple(components)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check first-party Python import cycles")
    parser.add_argument("project_root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args()
    graph = FirstPartyImportGraph.from_project(args.project_root.resolve())
    cycles = graph.cycles()
    if not cycles:
        print(f"No import cycles across {graph.module_count} first-party modules.")
        return 0
    for cycle in cycles:
        print(f"Import cycle: {cycle}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
