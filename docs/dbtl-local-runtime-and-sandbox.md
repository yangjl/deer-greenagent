# DBTL Local Runtime and Sandbox

## Summary

Local DBTL workers run as constrained processes on the Gateway host; they are
not separate containers. They use virtual paths rooted at `/mnt/user-data`,
receive exact input and output paths through server-issued environment
variables, and resolve `python` from the Gateway virtual environment. On macOS,
DBTL shell commands are additionally wrapped with `sandbox-exec` so a worker can
write only inside its assigned stage directory. Start DeerFlow through the
standard launcher so the Gateway environment includes the `dbtl-build`
scientific packages.

## Scope

This document describes the local `LocalSandboxProvider` path used by DBTL
Build and Test workers. Remote sandbox providers have their own filesystem and
package images; server verification additionally requires `bwrap` there.

The local sandbox has two layers:

1. The ordinary DeerFlow sandbox maps agent-visible virtual paths to host paths,
   validates tool paths, and sanitizes subprocess environments.
2. DBTL adds a narrower per-stage write grant and, during server verification,
   an exact input-read grant.

## Agent-visible filesystem

For a project-scoped conversation, the worker uses these paths:

| Agent path | Meaning | DBTL authority |
| --- | --- | --- |
| `/mnt/user-data` | Human-visible project folder | Project root; not a general DBTL write grant |
| `/mnt/user-data/workspace` | Alias for the project workspace | Same project data view |
| `/mnt/user-data/uploads` | Project uploads | Read only when used as an issued input |
| `/mnt/user-data/outputs` | Project outputs | Parent of governed and scratch outputs |
| `/mnt/user-data/outputs/dbtl` | Published DBTL evidence | Read-only to model-facing file tools |
| `/mnt/acp-workspace` | Per-thread ACP workspace | Ordinary sandbox mapping, separate from DBTL stage output |
| configured skills path | Enabled custom and legacy skills | Read-only projection |

`LocalSandbox` translates these virtual paths to the corresponding host paths
immediately before starting a subprocess. Environment values that exactly name
a mounted virtual path are translated the same way. Worker prompts and result
contracts must continue to use virtual paths; host paths are implementation
details and must not be embedded in generated code.

Relevant implementation:

- `backend/packages/harness/deerflow/sandbox/local/local_sandbox_provider.py`
- `backend/packages/harness/deerflow/sandbox/local/local_sandbox.py`
- `backend/packages/harness/deerflow/sandbox/tools.py`

## Per-stage workspace and input grant

Every non-Design stage receives a scratch root:

```text
/mnt/user-data/outputs/.dbtl-stage-work/<attempt-id>/<stage>
```

Concurrent workers receive separate hashed child directories:

```text
/mnt/user-data/outputs/.dbtl-stage-work/<attempt-id>/<stage>/<unit-hash>
```

The server provides the path contract through environment variables:

| Variable | Value |
| --- | --- |
| `DBTL_WORKSPACE` | The current worker's writable unit directory |
| `DBTL_PROJECT_ROOT` | `/mnt/user-data` |
| `DBTL_INPUT_COUNT` | Number of issued inputs |
| `DBTL_INPUT_1` ... `DBTL_INPUT_N` | Exact input paths, in declared order |

Generated code should read inputs from `DBTL_INPUT_N` and place every derived
file below `DBTL_WORKSPACE`. It must not guess a host path, hardcode another
project path, or use `/tmp` for governed output. The server scans generated
source for foreign absolute paths and then executes the declared entry point
under the filesystem boundary; the execution is the authoritative check.

Relevant implementation:

- `backend/packages/harness/deerflow/dbtl/build_grant.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/workspace.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/build_phase_verification.py`

## Worked example: one Build stage, multiple internal steps

Build is one DBTL stage, but a phased Build runs several server-governed steps
inside it. The following example comes from the manual-profile cycle
"Deterministic recovery of y = 2x + 1". Its plan separated implementation from
independent validation:

1. **Load the approved Design.** The server resolves the Design evidence and
   issued project inputs. No specialist runs yet.
2. **Plan Build.** A planner proposed two ordered phases: implement the exact
   fixture, then independently rerun and audit it. The plan was shown in chat
   and required human confirmation before execution.
3. **Run the implementation specialist.** The first specialist used its own
   unit workspace to create the exact-rational fit, predictions, metrics,
   lineage, manifest, notebook, figure, and validator. The server then executed
   the declared entry point again under the stricter input boundary.
4. **Run the independent audit specialist.** A second specialist received a
   different unit workspace plus the first phase's published outputs as issued
   inputs. It reran the persisted Build twice in clean directories and checked
   exact coefficients, zero holdout error, deterministic bytes, lineage,
   train-only fitting, and deliverable accounting without trusting the first
   specialist's validator.
5. **Synthesize and render review evidence.** After both phases passed, a
   synthesis worker produced ordinary conversational prose. The server wrote a
   Markdown review package and an HTML feedback deck, and native chat displayed
   each specialist's progress report. Build finished with evidence awaiting the
   human gate; it did not approve itself.

The same run also demonstrates recovery. Its first implementation attempt
returned a structured failure after a required `phase_done_condition` failed.
That result was diagnostic input, not Build evidence. Build v12 may give one
such precise, uncapped failure to a fresh correction worker; only the fresh
worker's independently valid result may satisfy the phase. If the run stops,
**Retry Run the build** reuses the accepted plan and completed phases instead of
starting over. In this example the retry completed both phases and produced the
normal review package.

`degraded_evidence_continuation=true` was enabled during this replay, but it did
not waive a phase, manufacture evidence, or approve the gate. The successful
path still required specialist output, server execution, the independent
audit, synthesis, and human review.

This is the ownership boundary to preserve:

| Build step | Primary actor | What can advance it |
| --- | --- | --- |
| Load Design | Server | Approved, project-owned Design evidence |
| Plan | Planner plus human | Valid typed plan and explicit confirmation |
| Implement | Specialist plus server verifier | Structured result, published outputs, and successful server execution |
| Audit | Independent specialist | Separate rerun and typed audit checks |
| Synthesize | Synthesis worker plus server renderer | Accepted phase results |
| Review | Human | Submit and an explicit approve/revise/reject decision in the registered deck |

## `PATH`, Python, and available packages

For a local DBTL worker, the Gateway derives the runtime from `sys.executable`:

1. Prepend that executable's `bin` directory to `PATH`.
2. Preserve the remaining inherited `PATH` entries without duplicating the
   Gateway bin directory.
3. Set `VIRTUAL_ENV` to the Gateway environment root.

With the standard local launcher this normally makes `python` resolve to:

```text
<repository>/backend/.venv/bin/python
```

The path is deliberately derived rather than hardcoded. This keeps the
interpreter and its `site-packages` together even when the venv's `python` is a
symlink to a system interpreter.

The `dbtl-build` optional extra supplies the supported scientific stack:

- NumPy
- SciPy
- pandas
- Matplotlib
- statsmodels
- scikit-learn
- seaborn
- Jupyter
- nbconvert
- ipykernel

A worker may import other packages already installed in the same Gateway venv,
but they are not part of the DBTL runtime contract. Node, R, Julia, Ruby, Perl,
and other commands are available only when their executables are already on
the inherited `PATH`; DBTL does not provision those runtimes automatically.
Build code should not repair the environment with runtime package installation.

The normal launchers run `scripts/detect_uv_extras.py`. When `dbtl.mode` is
`manual` or `graph_enabled`, it selects `dbtl-build` unless the operator has
explicitly replaced automatic extra selection with `UV_EXTRAS`.

For a direct Gateway launch:

```bash
cd backend
uv sync --extra dbtl-build
uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001
```

Do not start the Gateway with an unrelated system `python3`. Having `python3`
on the host does not mean that interpreter has NumPy or the rest of the DBTL
stack.

## Subprocess environment

Sandbox subprocesses inherit the Gateway environment after credential-shaped
variables are removed. Names containing `KEY`, `SECRET`, `TOKEN`, `PASS`,
`CREDENTIAL`, or `DSN` are scrubbed, as are known credential-bearing variables
such as `DATABASE_URL`, `REDIS_URL`, and `GITHUB_PAT`. Explicit request-scoped
secrets may be injected only through the authorized secret path.

DBTL then overlays its workspace/input grant and, locally, the Gateway `PATH`
and `VIRTUAL_ENV`. Server verification also redirects Jupyter and IPython state
into the current writable workspace:

- `JUPYTER_CONFIG_DIR`
- `JUPYTER_DATA_DIR`
- `JUPYTER_RUNTIME_DIR`
- `IPYTHONDIR`

Relevant implementation:

- `backend/packages/harness/deerflow/sandbox/env_policy.py`
- `backend/packages/harness/deerflow/runtime/secret_context.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`

## Local enforcement model

### Ordinary local sandbox

The ordinary local sandbox is path translation plus tool-level validation. It
runs commands using a host shell and a sanitized host environment. It is not a
VM, container, resource quota, or general network-isolation boundary.

### DBTL model-facing shell

On macOS with `LocalSandboxProvider`, DBTL Bash calls are wrapped in
`sandbox-exec`. The profile allows normal execution and reads but denies all
filesystem writes except:

- the worker's assigned unit workspace;
- `/private/tmp`, `/tmp`, and the platform temporary directory;
- `/dev/null`.

The sandbox applies to the complete subprocess tree, so a child Python process
cannot write around the Bash tool's grant. Model-facing file tools separately
enforce the same DBTL-owned path rules.

If a DBTL stage has a write grant but the provider cannot enforce a process-tree
boundary, generic Bash is denied and the narrower file tools remain available.

### Server Build verification

The server executes the worker's declared entry point again. On local macOS,
the verification profile adds a read boundary: all project reads are denied,
then only the unit workspace and exact `DBTL_INPUT_N` files are reopened.
Unissued project files therefore cannot silently influence the verified result.

Local Build verification fails closed on non-macOS platforms because DeerFlow
does not currently have an equivalent local process-tree write sandbox there.
Remote providers must supply `bwrap`; verification replaces the project mount
with an empty namespace and mounts back only the workspace and issued inputs.

The macOS profile is focused on filesystem authority. It does not itself block
network access; network/tool availability is governed separately by the worker
tool registry and deployment environment.

Relevant implementation:

- `backend/packages/harness/deerflow/agents/middlewares/dbtl_output_policy_middleware.py`
- `backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py`
- `backend/packages/harness/deerflow/agents/dbtl/live_stage/adapter.py`

## Quick diagnostics

Verify that the Gateway interpreter and scientific stack agree:

```bash
cd backend
uv run python -c 'import sys, numpy, scipy, pandas, sklearn; print(sys.executable); print(numpy.__version__)'
```

Verify the executable selected inside the environment:

```bash
cd backend
uv run python -c 'import shutil; print(shutil.which("python"))'
```

Common failures:

| Symptom | Meaning | Fix |
| --- | --- | --- |
| Exit `127` or `python: command not found` | Gateway venv was not placed on the worker `PATH` | Start through the standard launcher; verify the active code includes `local_dbtl_runtime_env()` |
| `ModuleNotFoundError: numpy` | Gateway started from an environment without `dbtl-build` | Run `uv sync --extra dbtl-build`, then launch with `uv run` |
| Write permission error | Code wrote outside `DBTL_WORKSPACE` | Build all derived paths from `DBTL_WORKSPACE` |
| Input permission error during verification | Code read a path not issued as `DBTL_INPUT_N` | Declare the input and read its issued variable |
| Build refuses before a worker starts | Scientific preflight or shell boundary is incomplete | Read the preflight message; repair the launcher or use a supported sandbox provider |
| Local verification refused on Linux | No supported local process-tree sandbox | Use a remote/container provider with `bwrap` |

## Contract checklist for changes

When changing DBTL execution, keep these invariants together:

- `python` and its packages come from one venv selected through `PATH`.
- Portable receipts record `python`, not a host-specific interpreter path.
- Workers receive virtual paths only; local host paths never enter prompts or
  generated source.
- Inputs are server-issued and retain their original `DBTL_INPUT_N` numbering.
- Workers write only under their unit `DBTL_WORKSPACE`.
- Server verification re-executes the entry point with the same environment and
  a stricter read boundary.
- Missing packages or isolation fail before planner/worker spend whenever
  possible.
