"""Import resolution: regular packages, namespace packages (no __init__.py) and TypeScript/JavaScript."""
from app.graph.resolver import PythonResolver, js_kind, js_package, resolve_js

REGULAR = {
    "backend/app/__init__.py", "backend/app/main.py", "backend/app/config.py",
    "backend/app/api/__init__.py", "backend/app/api/health.py", "backend/app/api/llm.py",
    "backend/tests/test_main.py",
}
NAMESPACE = {f for f in REGULAR if not f.endswith("__init__.py")}  # the same code, as Python allows, without __init__.py

CASES = [  # (importer, source, names)
    ("backend/app/main.py", "app.config", ["get_settings"]),
    ("backend/app/main.py", "app.api", ["health", "llm"]),
    ("backend/app/api/llm.py", ".", ["health"]),
    ("backend/app/api/llm.py", "..config", ["get_settings"]),
    ("backend/tests/test_main.py", "app.main", ["app"]),  # a test folder importing code that lives elsewhere
]


def targets(layout: set[str], importer: str, source: str, names: list[str]) -> set[str]:
    return {t for t in PythonResolver(layout).resolve(importer, source, names) if not t.endswith("__init__.py")}


def test_regular_layout_resolves_every_case():
    for importer, source, names in CASES:
        assert targets(REGULAR, importer, source, names), (importer, source)


def test_layout_without_init_files_resolves_identically():
    for importer, source, names in CASES:
        assert targets(NAMESPACE, importer, source, names) == targets(REGULAR, importer, source, names), (importer, source)


def test_submodules_imported_by_name_are_found():
    assert targets(NAMESPACE, "backend/app/main.py", "app.api", ["health", "llm"]) == {"backend/app/api/health.py", "backend/app/api/llm.py"}


def test_standard_library_is_never_bound_to_a_local_file():
    files = NAMESPACE | {"backend/app/logging.py"}
    assert PythonResolver(files).resolve("backend/app/main.py", "logging", []) == []
    assert PythonResolver(files).resolve("backend/app/main.py", "app", ["logging"]) == ["backend/app/logging.py"]


def test_a_third_party_package_is_not_bound_to_a_same_named_module_file():
    files = {"starlette/middleware/opentelemetry.py", "fastapi/telemetry/_api.py"}
    assert PythonResolver(files).resolve("fastapi/telemetry/_api.py", "opentelemetry", ["context"]) == []


def test_script_outside_any_package_can_import_its_sibling():
    files = {"scripts/run.py", "scripts/helper.py"}
    assert PythonResolver(files).resolve("scripts/run.py", "helper", []) == ["scripts/helper.py"]


def test_unknown_imports_resolve_to_nothing():
    assert PythonResolver(REGULAR).resolve("backend/app/main.py", "fastapi", ["FastAPI"]) == []


TS = {"src/App.tsx", "src/api.ts", "src/auth/AuthService.ts", "src/utils/index.ts", "src/utils/log.ts"}


def test_js_relative_file_and_folder_index():
    assert resolve_js("src/App.tsx", "./api", TS) == "src/api.ts"
    assert resolve_js("src/auth/AuthService.ts", "../utils", TS) == "src/utils/index.ts"
    assert resolve_js("src/auth/AuthService.ts", "../api", TS) == "src/api.ts"


def test_typescript_esm_js_suffix_maps_to_the_ts_file():
    assert resolve_js("src/utils/index.ts", "./log.js", TS) == "src/utils/log.ts"


def test_js_missing_and_package_imports():
    assert resolve_js("src/App.tsx", "./nope", TS) is None
    assert resolve_js("src/App.tsx", "react", TS) is None


def test_js_import_classification():
    assert js_kind("./Login.css") == "asset"
    assert js_kind("./nope") == "relative"
    assert js_kind("node:fs") == "builtin"
    assert js_kind("@/components/x") == "alias"
    assert js_kind("react") == "package"
    assert js_package("@scope/pkg/sub/deep") == "@scope/pkg" and js_package("lodash/fp") == "lodash"