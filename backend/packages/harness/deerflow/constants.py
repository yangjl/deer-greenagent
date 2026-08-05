"""Shared runtime protocol constants."""

DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"

# Hidden subdirectory (under a thread's outputs dir) that holds the browser
# tools' per-step screenshots. These are transient live-progress frames, not
# deliverables, so the workspace-changes scanner excludes this directory. Both
# the browser tools (which write here) and the scanner (which ignores it) import
# this single source of truth so the name cannot drift between them.
BROWSER_FRAMES_DIRNAME = ".browser-frames"

# Default subdirectory (under a thread's outputs dir) where the tool-output
# budget middleware persists oversized tool outputs. These are process
# feedback the model reads back via ``read_file`` (the budget preview carries
# the reference), not deliverables, so the workspace-changes scanner excludes
# this directory and run delivery verification never counts it as a produced
# artifact. Both the budget middleware's default ``storage_subdir`` and the
# scanner import this single source of truth so the name cannot drift between
# them; a custom configured ``storage_subdir`` is threaded through the
# snapshot capture as an extra excluded dir name.
TOOL_RESULTS_DIRNAME = ".tool-results"

# Default timeout (seconds) for MCP server bring-up: tool discovery (subprocess
# spawn + initialize + tools/list) and persistent-session initialization. A hung
# stdio server (e.g. npx blocked on a package download or a server that never
# answers initialize) would otherwise block agent construction forever — and on
# the Gateway event loop, the whole process. Per-server override is
# ``mcpServers.<name>.session_init_timeout``; ``None`` disables the timeout.
DEFAULT_MCP_SESSION_INIT_TIMEOUT = 60.0

# Persisted run-event envelope limits. Runtime definitions and the ORM both
# import these from this dependency-free module so lower layers never need to
# initialize deerflow.runtime just to validate storage constraints.
RUN_EVENT_TYPE_MAX_LENGTH = 32
RUN_EVENT_CATEGORY_MAX_LENGTH = 16

# Workspace changes are produced below the runtime layer, so their persisted
# event identity also lives here rather than in the runtime event catalog.
WORKSPACE_CHANGES_EVENT_TYPE = "workspace_changes"
WORKSPACE_CHANGES_EVENT_CATEGORY = "workspace"

# Agent activity has one concept and two names, matching the existing
# task_started / subagent.start precedent: the stream name is what
# emit_custom_event dispatches on and what astream_events consumers match, while
# the event type is the persisted catalog name. Both live here so the envelope
# builder can validate storage limits without importing deerflow.runtime.
#
# The category is dedicated on purpose. list_messages — the thread feed — filters
# by category, so activity sharing "message" or "trace" would surface every
# routing transition inside a conversation.
AGENT_ACTIVITY_STREAM_NAME = "agent_activity"
AGENT_ACTIVITY_EVENT_TYPE = "runtime.agent.activity"
AGENT_ACTIVITY_EVENT_CATEGORY = "activity"
