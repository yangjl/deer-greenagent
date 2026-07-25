"""Human-visible project storage configuration.

A project's workspace is a real folder the human owns (e.g.
``~/Documents/projects/G2F``) — browsable in Finder/Explorer, editable outside
DeerFlow — not an internal application directory. ``projects.root`` names the
parent folder new projects are created under (and existing same-named folders
are adopted from).
"""

from pathlib import Path

from pydantic import BaseModel, Field


class ProjectsConfig(BaseModel):
    root: str = Field(
        default="~/DeerFlowProjects",
        description="Parent folder for project workspaces; each project lives in its own human-visible subfolder.",
    )

    def resolved_root(self) -> Path:
        return Path(self.root).expanduser()
