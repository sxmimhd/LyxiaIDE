"""The list of tools the AI can call."""
from app.tools import code, files, git, shell
from app.tools.base import Level, Tool

_PATH = {"type": "string", "default": ".", "maxLength": 500}


def _tools() -> list[Tool]:
    return [
        Tool("list_directory", "List the files and folders in a project folder.", Level.READ,
             {"properties": {"path": _PATH}}, files.list_directory, example={"path": "."}),
        Tool("read_file", "Read a file with line numbers. Long files are returned in pieces of 400 lines.", Level.READ,
             {"properties": {"path": {"type": "string", "maxLength": 500},
                             "start_line": {"type": "integer", "default": 1, "minimum": 1},
                             "end_line": {"type": "integer", "default": 0, "minimum": 0}}, "required": ["path"]},
             files.read_file, example={"path": "README.md", "start_line": 1}),
        Tool("search_files", "Find files by name, with * wildcards (for example *.py or src/*.ts).", Level.READ,
             {"properties": {"pattern": {"type": "string", "maxLength": 200}}, "required": ["pattern"]},
             files.search_files, example={"pattern": "*.py"}),
        Tool("grep", "Search the project's text files with a regular expression.", Level.READ,
             {"properties": {"pattern": {"type": "string", "maxLength": 200}, "path": _PATH,
                             "glob": {"type": "string", "default": "", "maxLength": 100},
                             "case_sensitive": {"type": "boolean", "default": False},
                             "max_results": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200}},
              "required": ["pattern"]},
             files.grep, example={"pattern": "def \\w+_stream", "glob": "*.py"}),
        Tool("semantic_search", "Find code by meaning, using the project's search index.", Level.READ,
             {"properties": {"query": {"type": "string", "maxLength": 500},
                             "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 15}}, "required": ["query"]},
             code.semantic_search, example={"query": "where is the chat reply streamed"}),
        Tool("find_symbol", "Find where a function, class or method is defined.", Level.READ,
             {"properties": {"name": {"type": "string", "maxLength": 200}}, "required": ["name"]},
             code.find_symbol, example={"name": "build_graph"}),
        Tool("git_status", "Show changed files and the current branch.", Level.READ, {"properties": {}}, git.git_status),
        Tool("git_diff", "Show uncommitted changes (optionally for one file, or only staged ones).", Level.READ,
             {"properties": {"path": {"type": "string", "default": "", "maxLength": 500},
                             "staged": {"type": "boolean", "default": False}}}, git.git_diff, example={"staged": False}),
        Tool("git_log", "Show recent commits.", Level.READ,
             {"properties": {"count": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50}}}, git.git_log),
        Tool("git_show", "Show one commit with its changes.", Level.READ,
             {"properties": {"revision": {"type": "string", "default": "HEAD", "maxLength": 100}}}, git.git_show),
        Tool("git_branch", "List branches.", Level.READ, {"properties": {}}, git.git_branch),
        Tool("run_command", "Run a command (tests, build, linters...) in the project folder. Always needs approval.",
             Level.EXECUTE,
             {"properties": {"command": {"type": "string", "maxLength": 1000},
                             "timeout_seconds": {"type": "integer", "default": 60, "minimum": 1, "maximum": 300}},
              "required": ["command"]},
             shell.run_command, classify=shell.classify_command, example={"command": "git --version"}),
    ]


REGISTRY: dict[str, Tool] = {t.name: t for t in _tools()}