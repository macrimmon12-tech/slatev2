"""CLI entry point for the standalone content editor (``editor/editor.py``).

No component doc actually wires one up — ``13-editor-core-authoring.md``
specifies the ``Editor`` app shell and ``Project.new``/``Project.open``,
but nothing ever calls them from a script a person can run. Usage::

    python run_editor.py [path/to/project]

With no argument, opens (or creates, if it doesn't exist yet) a project
folder named ``my_project`` next to this file — matching the doc's "New
Project -> designer is in the Map Editor within seconds, blank map already
loaded" intent without making the user pick a path on the very first run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from editor.editor import Editor
from editor.project import Project

DEFAULT_PROJECT_DIR = Path(__file__).resolve().parent / "my_project"


def main() -> None:
    project_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROJECT_DIR
    if (project_dir / "project.json").exists():
        project = Project.open(project_dir)
    else:
        project = Project.new(project_dir)
    Editor(project=project).run()


if __name__ == "__main__":
    main()
