from __future__ import annotations

"""Her side of changing her own source.

Three pieces, in the order she meets them:

- selfwork.tier decides whether a change may go in without a human looking.
  It only ever errs towards review.
- selfwork.mirror is the git repository she writes: a copy of the live tree
  built from an allow list, plus the guard that keeps every path inside it.
- selfwork.tools are the six source_* tools the model calls. They are wired
  into the toolbox as an extra, next to the tools that read her memory.

Nothing in this package touches the live tree, a git remote, or the network.
Proposals are written for the applier to pick up; merging is not her job.
"""

from .mirror import DEFAULT_INCLUDES, MIRROR_NOT_READY, Mirror, MirrorError, SelfWorkPaths
from .tier import AUTO_PREFIXES, classify
from .tools import NAMES, SOURCE_TOOL_SCHEMAS, SelfToolbox

__all__ = [
    "AUTO_PREFIXES",
    "DEFAULT_INCLUDES",
    "MIRROR_NOT_READY",
    "Mirror",
    "MirrorError",
    "NAMES",
    "SOURCE_TOOL_SCHEMAS",
    "SelfToolbox",
    "SelfWorkPaths",
    "classify",
]
