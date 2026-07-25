from deerflow.agents.memory.scope import (
    memory_scope_label,
    scoped_memory_user_id,
)


def test_unfiled_memory_scope_keeps_user_id_unchanged():
    assert scoped_memory_user_id("user-1", {}) == "user-1"
    assert memory_scope_label({}) == "user"


def test_project_memory_scope_is_stable_and_distinct_per_project():
    project_a = {"project_id": "project-a", "project_root": "/tmp/a"}
    project_b = {"project_id": "project-b", "project_root": "/tmp/b"}

    scoped_a = scoped_memory_user_id("user-1", project_a)
    scoped_b = scoped_memory_user_id("user-1", project_b)

    assert scoped_a == scoped_memory_user_id("user-1", project_a)
    assert scoped_a != scoped_b
    assert scoped_a != "user-1"
    assert memory_scope_label(project_a) == "project:project-a"
