install:
	uv sync

dev:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload --reload-exclude='tests/**'

gateway:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

test:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ -v

# Local-iteration subset: skips live/real-LLM tests (they hit real endpoints when
# config.yaml has API keys) plus subsystems unused in a local single-user
# deployment: IM channels, GitHub app integration, TUI, remote sandboxes and
# provisioner, non-OpenRouter model providers, vendor search/crawl tools,
# deploy/ops scripts, and enterprise auth. CI still runs the full `make test`.
TEST_CORE_IGNORES = \
	--ignore-glob='*_live.py' \
	--ignore=tests/test_deferred_tool_promotion_real_llm.py \
	--ignore=tests/test_setup_agent_http_e2e_real_server.py \
	--ignore-glob='*/test_tui_*' \
	--ignore-glob='*/test_github_*' \
	--ignore-glob='*/test_channel*' \
	--ignore-glob='*/test_claude_provider_*' \
	--ignore-glob='*/test_provisioner_*' \
	--ignore=tests/test_feishu_parser.py \
	--ignore=tests/test_dingtalk_channel.py \
	--ignore=tests/test_discord_channel.py \
	--ignore=tests/test_discord_channel_connections.py \
	--ignore=tests/test_slack_channel_connections.py \
	--ignore=tests/test_telegram_channel_connections.py \
	--ignore=tests/test_wechat_channel.py \
	--ignore=tests/test_wecom_ws_text.py \
	--ignore=tests/test_additional_channel_connections.py \
	--ignore=tests/test_runtime_channel_config_merge.py \
	--ignore=tests/test_e2b_sandbox_provider.py \
	--ignore=tests/test_boxlite_provider.py \
	--ignore=tests/test_remote_sandbox_backend.py \
	--ignore=tests/test_bench_sandbox_provider.py \
	--ignore=tests/test_bench_checkpoint_channels.py \
	--ignore=tests/test_docker_sandbox_mode_detection.py \
	--ignore=tests/test_warm_pool_lifecycle.py \
	--ignore=tests/test_codex_provider.py \
	--ignore=tests/test_mindie_provider.py \
	--ignore=tests/test_vllm_provider.py \
	--ignore=tests/test_patched_deepseek.py \
	--ignore=tests/test_patched_minimax.py \
	--ignore=tests/test_patched_stepfun.py \
	--ignore=tests/test_acp_config.py \
	--ignore=tests/test_invoke_acp_agent_tool.py \
	--ignore=tests/test_cli_auth_providers.py \
	--ignore=tests/test_brave_tools.py \
	--ignore=tests/test_exa_tools.py \
	--ignore=tests/test_serper_tools.py \
	--ignore=tests/test_searxng_client.py \
	--ignore=tests/test_firecrawl_tools.py \
	--ignore=tests/test_crawl4ai_tools.py \
	--ignore=tests/test_ddg_search_tools.py \
	--ignore=tests/test_jina_client.py \
	--ignore=tests/test_infoquest_client.py \
	--ignore=tests/test_groundroute_tools.py \
	--ignore=tests/test_fastcrw_tools.py \
	--ignore=tests/test_image_search.py \
	--ignore=tests/test_browserless_client.py \
	--ignore=tests/test_browser_automation.py \
	--ignore=tests/test_browser_router.py \
	--ignore=tests/test_nginx_langgraph_body_size.py \
	--ignore=tests/test_serve_nginx_stop.py \
	--ignore=tests/test_deploy_uv_extras.py \
	--ignore=tests/test_setup_wizard.py \
	--ignore=tests/test_doctor.py \
	--ignore=tests/test_support_bundle.py \
	--ignore=tests/test_check_script.py \
	--ignore=tests/test_compose_default_workers.py \
	--ignore=tests/test_dev_entrypoint.py \
	--ignore=tests/test_uvicorn_reload_exclude.py \
	--ignore=tests/test_oidc_auth.py \
	--ignore=tests/test_rbac_authorization_provider.py

test-core:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ -q $(TEST_CORE_IGNORES)

test-blocking-io:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/blocking_io -q --tb=short

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check . --fix && uv run ruff format .

detect-blocking-io:
	@PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scripts/detect_blocking_io_static.py --output ../.deer-flow/blocking-io-findings.json

# Generate a new alembic revision by autogenerating the diff against the live
# ORM models. Usage: make migrate-rev MSG="add foo column to runs"
# The Gateway runs `alembic upgrade head` automatically at startup via
# `bootstrap_schema`, so there is intentionally no `migrate` / `migrate-stamp`
# target -- the single execution path keeps ops mistakes off the table.
# The script builds a fresh temp SQLite at head, then diffs the live models
# against it, so a clean checkout does NOT need a pre-existing ./data/deerflow.db.
migrate-rev:
	@if [ -z "$(MSG)" ]; then echo 'usage: make migrate-rev MSG="describe the change"'; exit 1; fi
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python scripts/_autogen_revision.py "$(MSG)"
