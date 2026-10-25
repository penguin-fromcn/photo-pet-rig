"""photo-pet-rig: full-body photo -> pivot-rigged 2D desktop pet assets."""

from .builder import BuildError, BuildResult, build
from .constants import VERSION
from .project import Project, ProjectError, load_project

__all__ = [
    "VERSION",
    "BuildError",
    "BuildResult",
    "build",
    "Project",
    "ProjectError",
    "load_project",
]
__version__ = VERSION
