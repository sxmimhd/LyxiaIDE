"""Static knowledge used by the scanner: languages, file kinds, noise to skip."""
import re

EXTENSIONS: dict[str, str] = {
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".py": "Python", ".pyi": "Python",
    ".cs": "C#", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".php": "PHP", ".go": "Go", ".rs": "Rust", ".sql": "SQL",
    ".sh": "Shell", ".ps1": "PowerShell",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte",
    ".md": "Markdown", ".mdx": "Markdown", ".rst": "Text", ".txt": "Text",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".xml": "XML", ".ini": "INI",
}

DOC_LANGUAGES = {"Markdown", "Text"}
CONFIG_LANGUAGES = {"JSON", "YAML", "TOML", "XML", "INI"}

# Directories that are generated, vendored or tool state: never worth indexing.
DEFAULT_IGNORED_DIRS = {
    "node_modules", ".git", "dist", "build", "out", "bin", "obj", "target",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".nuxt", ".turbo", ".cache", "coverage", ".idea", ".vscode", ".vs",
}

# Generated lock files: huge and meaningless for code understanding.
IGNORED_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock",
    "Cargo.lock", "composer.lock", "Pipfile.lock", ".DS_Store", "Thumbs.db",
        ".next", ".nuxt", ".turbo", ".cache", "coverage", ".idea", ".vscode", ".vs",
    ".lyxia",  # LyxiaIDE's own state (indexes, evaluation sets): never part of the code being searched

}

CONFIG_NAMES = {
    "Dockerfile", "Makefile", ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
    "docker-compose.yml", "docker-compose.yaml", "package.json", "tsconfig.json", "pyproject.toml",
    "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
}

ENTRY_NAMES = {
    "main.py", "app.py", "manage.py", "__main__.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "index.tsx", "main.js", "main.ts", "main.tsx", "server.js", "server.ts",
    "Program.cs", "main.go", "main.rs", "Main.java", "Application.java",
}

MANIFEST_NAMES = {"package.json", "requirements.txt", "pyproject.toml"}

TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|cs)$)")
TEST_DIRS = {"tests", "test", "__tests__", "spec"}

# Secrets: never listed, never read, only counted.
SENSITIVE = re.compile(
    r"(^\.env(\..+)?$|\.(pem|key|p12|pfx|keystore|jks)$|^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|^secrets?\.(json|ya?ml|toml)$)",
    re.IGNORECASE,
)
SENSITIVE_ALLOWED = {".env.example", ".env.sample", ".env.template"}

FRAMEWORKS: dict[str, str] = {
    # JavaScript / TypeScript
    "react": "React", "next": "Next.js", "vue": "Vue", "@angular/core": "Angular", "svelte": "Svelte",
    "express": "Express", "@nestjs/core": "NestJS", "electron": "Electron", "vite": "Vite",
    "tailwindcss": "Tailwind CSS", "monaco-editor": "Monaco Editor", "vitest": "Vitest", "jest": "Jest",
    # Python
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "pytest": "pytest", "torch": "PyTorch",
    "langchain": "LangChain", "qdrant-client": "Qdrant", "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic",
}


def is_sensitive(name: str) -> bool:
    return name.lower() not in SENSITIVE_ALLOWED and SENSITIVE.search(name) is not None