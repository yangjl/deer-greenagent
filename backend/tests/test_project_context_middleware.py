"""The agent must know its workspace IS the human's project folder.

Without this, the model assumes it "can't see your local machine's
filesystem" and offers copy-paste terminal commands instead of just writing
files (observed live in the G2F project). The middleware injects a per-request
SystemMessage — request-only, never checkpointed — mapping the sandbox path to
the human path.
"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from deerflow.agents.middlewares.project_context_middleware import (
    ProjectContextMiddleware,
    build_mounts_reminder,
    build_project_reminder,
)


def _mount(host_path: str, container_path: str, read_only: bool = False):
    return SimpleNamespace(host_path=host_path, container_path=container_path, read_only=read_only)


class TestBuildProjectReminder:
    def test_maps_the_sandbox_path_to_the_human_folder(self):
        reminder = build_project_reminder("/Users/jyang21/Documents/projects/G2F")
        assert reminder is not None
        assert "/mnt/user-data/workspace" in reminder
        assert "/Users/jyang21/Documents/projects/G2F" in reminder
        assert "G2F" in reminder

    def test_tells_the_agent_not_to_hand_back_terminal_commands(self):
        reminder = build_project_reminder("/Users/jyang21/Documents/projects/G2F")
        assert reminder is not None
        assert "do NOT" in reminder or "directly" in reminder
        assert "README.md" in reminder
        assert "/mnt/projects" in reminder
        assert "unless the human explicitly" in reminder
        assert "Ignore any earlier memory or assistant response" in reminder

    def test_no_project_scope_means_no_reminder(self):
        assert build_project_reminder(None) is None
        assert build_project_reminder("") is None


class TestBuildMountsReminder:
    def test_maps_host_folders_to_container_paths(self):
        reminder = build_mounts_reminder([_mount("/Users/jyang21/Documents/projects", "/mnt/projects")])
        assert reminder is not None
        assert "/Users/jyang21/Documents/projects" in reminder
        assert "/mnt/projects" in reminder
        assert "read-write" in reminder

    def test_marks_read_only_mounts(self):
        reminder = build_mounts_reminder([_mount("/data", "/mnt/data", read_only=True)])
        assert reminder is not None
        assert "read-only" in reminder

    def test_no_mounts_means_no_reminder(self):
        assert build_mounts_reminder([]) is None
        assert build_mounts_reminder(None) is None


class TestMiddleware:
    def test_appends_one_system_message_when_scoped(self):
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [])
        request = SimpleNamespace(
            messages=[],
            runtime=SimpleNamespace(context={"project_root": "/tmp/G2F", "project_id": "p1"}),
        )
        seen = {}

        def handler(req):
            seen["messages"] = list(req.messages)
            return "ok"

        assert middleware.wrap_model_call(request, handler) == "ok"
        systems = [m for m in seen["messages"] if isinstance(m, SystemMessage)]
        assert len(systems) == 1
        assert "/tmp/G2F" in systems[0].content

    def test_unscoped_requests_without_mounts_pass_through_untouched(self):
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [])
        request = SimpleNamespace(messages=[], runtime=SimpleNamespace(context={}))

        def handler(req):
            assert req is request
            assert req.messages == []
            return "ok"

        assert middleware.wrap_model_call(request, handler) == "ok"

    def test_mounts_block_is_injected_even_without_a_project(self):
        """An unfiled chat must still know about shared local folders."""
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [_mount("/Users/x/projects", "/mnt/projects")])
        request = SimpleNamespace(messages=[], runtime=SimpleNamespace(context={}))
        seen = {}

        def handler(req):
            seen["messages"] = list(req.messages)
            return "ok"

        assert middleware.wrap_model_call(request, handler) == "ok"
        systems = [m for m in seen["messages"] if isinstance(m, SystemMessage)]
        assert len(systems) == 1
        assert "/mnt/projects" in systems[0].content

    def test_project_and_non_overlapping_mounts_blocks_combine_into_one_message(self):
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [_mount("/Users/x/shared", "/mnt/shared")])
        request = SimpleNamespace(
            messages=[],
            runtime=SimpleNamespace(context={"project_root": "/Users/x/projects/G2F"}),
        )
        seen = {}

        def handler(req):
            seen["messages"] = list(req.messages)
            return "ok"

        middleware.wrap_model_call(request, handler)
        systems = [m for m in seen["messages"] if isinstance(m, SystemMessage)]
        assert len(systems) == 1
        assert "<project_context>" in systems[0].content
        assert "<local_folders>" in systems[0].content

    def test_project_scope_hides_an_overlapping_parent_mount(self):
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [_mount("/Users/x/projects", "/mnt/projects")])
        request = SimpleNamespace(
            messages=[],
            runtime=SimpleNamespace(context={"project_root": "/Users/x/projects/G2F"}),
        )
        seen = {}

        def handler(req):
            seen["messages"] = list(req.messages)
            return "ok"

        middleware.wrap_model_call(request, handler)
        systems = [m for m in seen["messages"] if isinstance(m, SystemMessage)]
        assert len(systems) == 1
        assert "<project_context>" in systems[0].content
        assert "<local_folders>" not in systems[0].content
        assert "/mnt/projects" in systems[0].content

    def test_project_scope_strips_stale_global_memory_from_existing_thread(self):
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [])
        stale_memory = HumanMessage(
            content="<memory>Active project: greenagent-test</memory>",
            id="first-message__memory",
            additional_kwargs={
                "hide_from_ui": True,
                "dynamic_context_reminder": True,
            },
        )
        current_question = HumanMessage(
            content="Which project are you working on?",
            id="current-message",
        )
        request = SimpleNamespace(
            messages=[stale_memory, current_question],
            runtime=SimpleNamespace(
                context={
                    "project_id": "project-test2",
                    "project_root": "/Users/jyang21/Documents/projects/test2",
                }
            ),
        )
        seen = {}

        def handler(req):
            seen["messages"] = list(req.messages)
            return "ok"

        middleware.wrap_model_call(request, handler)

        assert stale_memory not in seen["messages"]
        assert current_question in seen["messages"]
        assert any(isinstance(message, SystemMessage) and "'test2'" in message.content for message in seen["messages"])

    def test_project_scope_keeps_date_dynamic_context_reminder(self):
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [])
        date_reminder = SystemMessage(
            content="<system-reminder><current_date>2026-07-25</current_date></system-reminder>",
            id="first-message",
            additional_kwargs={
                "hide_from_ui": True,
                "dynamic_context_reminder": True,
                "reminder_date": "2026-07-25, Saturday",
            },
        )
        request = SimpleNamespace(
            messages=[date_reminder],
            runtime=SimpleNamespace(
                context={
                    "project_id": "project-test2",
                    "project_root": "/Users/jyang21/Documents/projects/test2",
                }
            ),
        )
        seen = {}

        middleware.wrap_model_call(
            request,
            lambda req: seen.setdefault("messages", list(req.messages)),
        )

        assert date_reminder in seen["messages"]

    def test_current_project_data_is_immediately_before_latest_user_in_old_thread(self):
        """Recent stale assistant text must not outrank durable project scope."""
        middleware = ProjectContextMiddleware(mounts_provider=lambda: [])
        stale_answer = AIMessage(
            content=("We are working in greenagent-test at /Users/jyang21/Documents/projects/greenagent-test"),
            id="old-answer",
        )
        current_question = HumanMessage(
            content="Which project are you working on right now?",
            id="current-question",
        )
        request = SimpleNamespace(
            messages=[
                HumanMessage(content="Old question", id="old-question"),
                stale_answer,
                current_question,
            ],
            runtime=SimpleNamespace(
                context={
                    "project_id": "project-drought",
                    "project_root": "/Users/jyang21/Documents/projects/Drought resistent",
                }
            ),
        )
        seen = {}

        middleware.wrap_model_call(
            request,
            lambda req: seen.setdefault("messages", list(req.messages)),
        )

        messages = seen["messages"]
        current_index = messages.index(current_question)
        authoritative = messages[current_index - 1]
        assert isinstance(authoritative, HumanMessage)
        assert authoritative.additional_kwargs.get("current_project_data") is True
        assert "Drought resistent" in authoritative.content
        assert "/Users/jyang21/Documents/projects/Drought resistent" in authoritative.content
        assert messages.index(stale_answer) < messages.index(authoritative)
