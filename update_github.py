#!/usr/bin/env python3
r"""Commit the complete local working tree and push its current branch.

Examples (Windows PowerShell):
  py .\update_github.py --repo "D:\your-project"
  py .\update_github.py --repo "D:\your-project" --message "Update application"
  py .\update_github.py --repo "D:\your-project" --dry-run

First upload to an EXISTING, EMPTY GitHub repository:
  py .\update_github.py --repo "D:\your-project" --remote-url https://github.com/YOUR_USERNAME/YOUR_REPO.git --branch main

Requires Python 3.9+ and Git. Uses existing Git authentication. Respects
.gitignore, includes deletions, and never force-pushes or merges automatically.
"""
import argparse
from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


class UpdateError(Exception):
    pass


def git(repo, *args, allowed=(0,), display=False, quiet=False):
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        stdout=None if display else subprocess.PIPE,
        stderr=subprocess.DEVNULL if quiet else None,
        text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode not in allowed:
        raise UpdateError(f"git {args[0]} failed (exit {result.returncode}). See Git's message above.")
    return result


def credential_free_url(url):
    if not url or url.startswith("-") or any(ch in url for ch in "\r\n"):
        raise UpdateError("Invalid remote URL.")
    if url.lower().startswith(("http://", "https://")):
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise UpdateError("Use a remote URL without a username/token in it; use Git Credential Manager or SSH.")
    return url


def protected_path(name):
    """Filename check only; this is not a general secret-content scanner."""
    base = PurePosixPath(name).name.lower()
    return (base == ".env" or
            (base.startswith(".env.") and base not in {".env.example", ".env.sample", ".env.template"}) or
            base in {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"})


def check_private_files(repo, has_head):
    candidates = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.split("\0")
    blocked = {name for name in candidates if name and protected_path(name) and
               ((repo / name).exists() or (repo / name).is_symlink())}
    if has_head:
        # Include files added and later deleted in commits not yet on origin.
        # -m also covers changes introduced by merge commits.
        pending = git(repo, "log", "-m", "--format=", "--name-only", "-z",
                      "--diff-filter=ACMR", "HEAD", "--not", "--remotes=origin").stdout
        blocked.update(name for item in pending.split("\0")
                       if (name := item.lstrip("\n")) and protected_path(name))
    if blocked:
        paths = "\n".join("  " + repr(name) for name in sorted(blocked))
        raise UpdateError(
            "Private configuration/key filenames would be published:\n" + paths +
            "\nIgnore untracked private files, or remove them from the index/outgoing commits before retrying. "
            "Adding .gitignore does not remove previously committed files."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Exact repository root; defaults to current directory")
    parser.add_argument("--message", default="Update project " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    parser.add_argument("--remote-url", help="Set origin only if absent; can initialize a first upload to an empty remote")
    parser.add_argument("--branch", help="Require this current branch; defaults to current branch, or main for first upload")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/check and show candidate changes; do not stage, commit or push")
    args = parser.parse_args()
    if not shutil.which("git"):
        raise UpdateError("Git was not found. Install Git for Windows and reopen PowerShell.")
    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        raise UpdateError(f"Project directory does not exist: {repo}")
    if not args.message.strip():
        raise UpdateError("The commit message cannot be empty.")
    if args.remote_url:
        credential_free_url(args.remote_url)

    root = git(repo, "rev-parse", "--show-toplevel", allowed=(0, 128), quiet=True)
    if root.returncode:
        if (repo / ".git").exists():
            raise UpdateError("This folder contains Git metadata but is not a usable working tree. Fix it first.")
        if not args.remote_url:
            raise UpdateError("This is not a Git repository. Supply --remote-url for a first upload to an empty GitHub repository.")
        if args.dry_run:
            raise UpdateError("Dry-run requires an existing local repository; no repository was initialized.")
        heads = git(repo, "ls-remote", "--heads", args.remote_url).stdout.strip()
        if heads:
            raise UpdateError(
                "The remote already has history. Clone that repository, copy your updated project files into the clone, "
                "then run this script there. No remote files were overwritten."
            )
        git(repo, "check-ref-format", "--branch", args.branch or "main")
        git(repo, "init", "--initial-branch", args.branch or "main", display=True)
        root = git(repo, "rev-parse", "--show-toplevel")
    if os.path.normcase(str(Path(root.stdout.strip()).resolve())) != os.path.normcase(str(repo)):
        raise UpdateError("--repo must point to the repository root, not a subfolder of another repository.")

    branch_result = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
    if branch_result.returncode:
        raise UpdateError("Detached HEAD: switch to the branch you want to update first.")
    branch = branch_result.stdout.strip()
    if args.branch and args.branch != branch:
        raise UpdateError(f"Current branch is {branch!r}, not {args.branch!r}. Switch branches before running this script.")
    git(repo, "check-ref-format", "--branch", branch)
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        location = Path(git(repo, "rev-parse", "--git-path", state).stdout.strip())
        if not location.is_absolute():
            location = repo / location
        if location.exists():
            raise UpdateError("Finish or abort the existing merge/rebase/cherry-pick/revert first.")

    remotes = git(repo, "remote").stdout.splitlines()
    if "origin" not in remotes:
        if not args.remote_url:
            raise UpdateError("origin is missing. Run again with --remote-url https://github.com/OWNER/REPO.git")
        if args.dry_run:
            raise UpdateError("Dry-run will not configure origin. Configure the remote first.")
        git(repo, "remote", "add", "origin", args.remote_url)
    fetch_urls = git(repo, "remote", "get-url", "--all", "origin").stdout.splitlines()
    push_urls = git(repo, "remote", "get-url", "--push", "--all", "origin").stdout.splitlines()
    if len(fetch_urls) != 1 or push_urls != fetch_urls:
        raise UpdateError("origin must have one identical fetch/push URL. Review your remote configuration first.")
    remote_url = credential_free_url(fetch_urls[0])
    if args.remote_url and args.remote_url != remote_url:
        raise UpdateError("origin already points elsewhere. The script will not change an existing destination.")
    print(f"Repository: {repo}\nBranch: {branch}\nRemote: {remote_url}", flush=True)
    git(repo, "fetch", "--prune", "--no-tags", "origin", display=True)
    has_head = git(repo, "rev-parse", "--verify", "--quiet", "HEAD", allowed=(0, 1)).returncode == 0
    remote_ref = "refs/remotes/origin/" + branch
    has_remote = git(repo, "show-ref", "--verify", "--quiet", remote_ref, allowed=(0, 1)).returncode == 0
    if has_remote:
        if not has_head or git(repo, "merge-base", "--is-ancestor", remote_ref, "HEAD", allowed=(0, 1)).returncode:
            raise UpdateError(
                f"origin/{branch} contains commits missing locally. Integrate the remote changes first, then rerun. "
                "The script has not staged or committed your working files."
            )
    check_private_files(repo, has_head)
    if args.dry_run:
        git(repo, "status", "--short", display=True)
        git(repo, "add", "--dry-run", "--all", display=True)
        print("Dry-run complete. No files staged, no commit created, no push sent.")
        return

    git(repo, "add", "--all", display=True)
    changed = git(repo, "diff", "--cached", "--quiet", allowed=(0, 1)).returncode == 1
    if changed:
        for setting in ("user.name", "user.email"):
            configured = git(repo, "config", "--get", setting, allowed=(0, 1))
            if not configured.stdout.strip():
                raise UpdateError(f"Changes remain staged locally. Set identity, then rerun: git config --global {setting} \"YOUR_VALUE\"")
        git(repo, "diff", "--cached", "--stat", display=True)
        git(repo, "commit", "-m", args.message, display=True)
    elif not has_head:
        raise UpdateError("There are no included files to commit. Check .gitignore and the project path.")
    else:
        print("No new file changes; checking/pushing existing local commits.", flush=True)
    # Repeat after commit in case a hook or another process changed the tree.
    check_private_files(repo, True)
    git(repo, "-c", "remote.origin.mirror=false", "-c", "push.followTags=false",
        "push", "--no-follow-tags", "--set-upstream", "origin", "HEAD:refs/heads/" + branch, display=True)
    print(f"Updated origin/{branch} successfully.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (UpdateError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Cancelled. Check git status before retrying.", file=sys.stderr)
        sys.exit(130)
