"""Where a committed step keeps its own output, so a replay can read it back.

The step table records that a step succeeded and what its output *digest* was.
That is enough to decide a later step is still valid and nowhere near enough to
skip re-running it: a digest is not a plan, and it is not a worker's structured
result. So the recorder found a committed success, reported `replayed=True`,
and every caller dispatched anyway — which is the one thing the whole digest
chain exists to prevent. An hour of sandbox work was pinned in the record and
re-run in reality.

Three rules make this safe to read back.

**A payload is only ever accepted against the digest it was recorded under.**
The caller rebuilds the typed object and recomputes its digest; a mismatch is a
miss. So a truncated write, a hand-edited file, or a payload left behind by a
different plan cannot become this attempt's result — the worst it can do is cost
one re-run.

**Both directions fail soft.** A store that cannot be written leaves a step that
replays by re-running: wasteful, never wrong. A store that cannot be read does
the same. Neither may raise, because this is an accelerator bolted to a
governance record and an accelerator must not be able to fail a Build.

**It is scratch, not evidence.** Payloads live under the stage work root, which
is already excluded from the project manifest, from Build's input snapshot, and
from everything a worker is shown. Nothing here is ever served to a reviewer;
the governed output tree remains the only place evidence lives.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from deerflow.agents.dbtl.live_stage.workspace import STAGE_WORK_ROOT, safe_token
from deerflow.dbtl.build_workflow import BuildStepKey
from deerflow.projects.storage import project_outputs_dir

logger = logging.getLogger(__name__)

#: A step payload holds a plan or one worker's structured result, both already
#: bounded by their own parsers. The cap is a backstop against a corrupt file,
#: not a contract.
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024


class StepOutputStore:
    """The committed outputs of one stage attempt's Build steps."""

    def __init__(self, *, project_root: str, stage_attempt_id: str) -> None:
        self._root: Path | None = None
        try:
            outputs = project_outputs_dir(Path(project_root).expanduser().resolve())
        except (OSError, ValueError):  # pragma: no cover - an unresolvable root disables the store
            logger.warning("Could not resolve the Build step store root; steps will replay by re-running.", exc_info=True)
            return
        # Keyed by the durable stage attempt, which is what the step rows key
        # on. Deriving it from anything a single run chooses would put the
        # payloads somewhere the *next* run does not look, which is a store that
        # silently never hits.
        self._root = outputs / STAGE_WORK_ROOT / "steps" / safe_token(stage_attempt_id)

    @property
    def available(self) -> bool:
        return self._root is not None

    def _path(self, step: BuildStepKey, digest: str) -> Path | None:
        token = (digest or "").strip()
        if self._root is None or not token.isalnum():
            return None
        return self._root / f"{step.value}-{token[:40]}.json"

    def save(self, step: BuildStepKey, digest: str, payload: Any) -> None:
        """Record one step's output beside its digest. Never raises."""
        path = self._path(step, digest)
        if path is None:
            return
        try:
            encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            if len(encoded) > MAX_PAYLOAD_BYTES:
                logger.info("A Build step payload for %s exceeded the store cap and was not kept.", step.value)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_bytes(encoded)
            temporary.replace(path)
        except (OSError, TypeError, ValueError):
            logger.warning("Could not keep the Build step payload for %s; it will replay by re-running.", step.value, exc_info=True)

    def load(self, step: BuildStepKey, digest: str) -> Any | None:
        """Read back one step's output, or `None`. Never raises."""
        path = self._path(step, digest)
        if path is None or not path.is_file():
            return None
        try:
            if path.stat().st_size > MAX_PAYLOAD_BYTES:
                return None
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            logger.warning("Could not read the kept Build step payload for %s; it will replay by re-running.", step.value, exc_info=True)
            return None
