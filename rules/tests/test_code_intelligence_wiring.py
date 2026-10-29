"""Source contracts for the three code-intelligence tools.

Serena, zvec-grep and codebase-memory-mcp have been set up and lost twice
already. Both losses were silent because the wiring lived somewhere a cleanup
could remove without anything going red: commit dffaa9fd6 moved Serena's
.mcp.json entry to a path outside the repo that later stopped existing, and
commit 9151676a7 then deleted the rule file holding the restore instructions as
part of a token-efficiency pass. Nothing failed, so nobody noticed until an
agent quietly fell back to grep.

These assertions exist so the next such cleanup goes red instead. Each one
pins a specific thing whose loss reproduces one of those two failures.
"""

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_CONFIG = REPO_ROOT / ".mcp.json"
CONDUCTOR_SETTINGS = REPO_ROOT / ".conductor" / "settings.toml"
MAKEFILE = REPO_ROOT / "Makefile"
GITIGNORE = REPO_ROOT / ".gitignore"
SETUP_DOC = REPO_ROOT / "docs" / "setup" / "code-intelligence.md"
GUARD_SCRIPT = REPO_ROOT / "scripts" / "code-daemon-guard.sh"
SMOKE_SCRIPT = REPO_ROOT / ".claude" / "scripts" / "mcp-smoke.mjs"


def _mcp_servers() -> dict[str, dict]:
    return json.loads(MCP_CONFIG.read_text())["mcpServers"]


def test_serena_is_declared_in_the_repo_mcp_config() -> None:
    # The 2026-03 regression was Serena's entry living in a parent-directory
    # .mcp.json instead of this file. Claude Code resolves project scope from
    # the project root, and a Conductor worktree has no stable parent, so the
    # entry has to be here.
    assert "serena" in _mcp_servers()


def test_serena_binds_to_the_worktree_it_is_started_in() -> None:
    args = _mcp_servers()["serena"]["args"]
    # --project . resolves against the directory Claude Code spawns the server
    # in. --project-from-cwd guesses instead and binds to the wrong root when
    # Agent Teams spawn inside a worktree (oraios/serena#1496), which looks
    # exactly like a working Serena while answering about another checkout.
    assert args[args.index("--project") + 1] == "."
    assert "--project-from-cwd" not in args
    # Without the claude-code context Serena also exposes its own read, search
    # and shell tools, which duplicate Claude Code's native ones.
    assert args[args.index("--context") + 1] == "claude-code"


def test_every_mcp_server_has_a_comment_explaining_itself() -> None:
    # $comment is where the reasoning for a launcher or a flag survives; a
    # server added without one is the start of the next undocumented setup.
    for name, server in _mcp_servers().items():
        assert server.get("$comment"), f"{name} has no $comment in .mcp.json"


def test_new_worktrees_build_their_own_indexes() -> None:
    # All three tools key their state off the working directory and none of it
    # is committed, so a fresh Conductor worktree starts empty. The index step
    # is the machine-wide ~/bin/dev-code-tools (dotfiles repo ai-dev-workflow),
    # and it has to be the FIRST thing in setup: Conductor no longer waits for
    # setup before the first chat, and the dotfiles installer prepends the same
    # command to any setup that does not already start with it, which is how
    # this repo indexed every new workspace twice until 2026-10-29.
    settings = tomllib.loads(CONDUCTOR_SETTINGS.read_text())
    assert settings["scripts"]["setup"].startswith('~/bin/dev-code-tools setup "$PWD"')
    makefile = MAKEFILE.read_text()
    assert "code-tools:" in makefile
    assert 'dev-code-tools setup "$$PWD"' in makefile


def test_only_the_guard_starts_the_shared_daemons() -> None:
    # zvec-grep and codebase-memory-mcp run one daemon per MACHINE and each
    # inherits the working directory of whoever starts it; a daemon rooted in
    # a workspace that is later archived still answers "ready" while every
    # index write on the machine fails with uv_cwd ENOENT. dev-code-tools
    # starts a missing daemon from $HOME before it indexes and refuses one
    # with another cwd (pinned by its own tests); the repair, restarting a
    # wrongly rooted daemon, is this script, and nothing in this repo may
    # start a daemon from a worktree (that rooted one in a disposable agent
    # worktree on 2026-09-15).
    assert GUARD_SCRIPT.exists()
    assert "daemon start" not in MAKEFILE.read_text()


def test_archiving_a_workspace_rehomes_the_shared_daemons() -> None:
    # Archive is the exact moment a daemon can be orphaned, and it is the last
    # moment this directory still exists. Dropping this hook reintroduces the
    # failure for every repo on the machine, not just this one.
    settings = tomllib.loads(CONDUCTOR_SETTINGS.read_text())
    assert "code-daemon-guard.sh" in settings["scripts"]["archive"]


def test_conductor_json_has_not_come_back() -> None:
    # Conductor ignores a repo-level conductor.json once .conductor/settings.toml
    # exists, so a reintroduced one is config that silently does nothing.
    assert not (REPO_ROOT / "conductor.json").exists()


def test_the_zvec_grep_index_stays_out_of_git() -> None:
    assert ".zvec-grep/" in GITIGNORE.read_text()


def test_the_setup_documentation_still_exists() -> None:
    # This is the assertion aimed squarely at the 9151676a7 failure: the
    # restore instructions were deleted for token efficiency and nothing
    # noticed. Deleting this doc now breaks CI.
    assert SETUP_DOC.exists()
    text = SETUP_DOC.read_text()
    for command in ("make code-tools", "make mcp-smoke"):
        assert command in text, f"{SETUP_DOC.name} no longer documents {command}"


def test_the_smoke_check_reads_the_server_list_from_mcp_json() -> None:
    # Hard-coding the server names would let a newly added server escape the
    # only check that proves a server actually starts.
    assert "readFileSync" in SMOKE_SCRIPT.read_text()
    assert "'../../.mcp.json'" in SMOKE_SCRIPT.read_text()
