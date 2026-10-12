# ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3 | (c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0
"""
GitHubTool — Full GitHub integration using PyGithub.

Capabilities:
- Repos: create, delete, fork, list, search, get info
- Issues: create, list, close, reopen, comment, assign, label
- PRs: create, list, merge, review, comment, files changed
- Branches: list, create, delete, switch
- Commits: list, get info, compare
- User: profile info, repositories
- Actions: list workflows, trigger, get runs
- Search: code, repos, issues, users
"""

import os
import subprocess
from typing import Optional

from ..core.policy_engine import check_tool

try:
    from github import Github, GithubException  # noqa: F401

    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False


def _check():
    if not GITHUB_AVAILABLE:
        raise RuntimeError("PyGithub not installed. pip install PyGithub")


class GitHubTool:
    def __init__(self, token: Optional[str] = None):
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        self._client = None
        self._user = None

    def _gate(self, action: str) -> Optional[dict]:
        decision = check_tool("github", action)
        if not decision.allowed:
            return {
                "success": False,
                "error": f"Access denied ({decision.reason}): github.{action}",
                "policy": decision.to_dict(),
            }
        return None

    def _get_client(self) -> "Github":
        if not self._client:
            _check()
            if self._token:
                self._client = Github(self._token)
            else:
                self._client = Github()
            try:
                self._user = self._client.get_user()
            except Exception:
                self._user = None
        return self._client

    def _user_obj(self):
        client = self._get_client()
        return self._user or client.get_user()

    def is_configured(self) -> bool:
        return GITHUB_AVAILABLE and bool(self._token)

    def get_status(self) -> dict:
        if not GITHUB_AVAILABLE:
            return {"available": False, "error": "PyGithub not installed"}
        if not self._token:
            return {"available": True, "authenticated": False, "error": "GITHUB_TOKEN not set"}
        try:
            user = self._user_obj()
            return {
                "available": True,
                "authenticated": True,
                "user": user.login,
                "name": user.name or user.login,
                "public_repos": user.public_repos,
                "followers": user.followers,
                "following": user.following,
            }
        except Exception as e:
            return {"available": True, "authenticated": False, "error": str(e)}

    # ── Repos ──────────────────────────────────────────────────────────────

    def list_repos(self, sort: str = "updated", limit: int = 20) -> dict:
        try:
            user = self._user_obj()
            repos = user.get_repos(sort=sort)
            result = []
            for i, repo in enumerate(repos):
                if i >= limit:
                    break
                result.append(
                    {
                        "name": repo.name,
                        "full_name": repo.full_name,
                        "description": repo.description or "",
                        "language": repo.language or "unknown",
                        "stars": repo.stargazers_count,
                        "forks": repo.forks_count,
                        "open_issues": repo.open_issues_count,
                        "private": repo.private,
                        "updated": str(repo.updated_at),
                        "url": repo.html_url,
                    }
                )
            return {"success": True, "repos": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_repo(self, full_name: str) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            return {
                "success": True,
                "repo": {
                    "name": repo.name,
                    "full_name": repo.full_name,
                    "description": repo.description or "",
                    "language": repo.language or "unknown",
                    "stars": repo.stargazers_count,
                    "forks": repo.forks_count,
                    "open_issues": repo.open_issues_count,
                    "private": repo.private,
                    "default_branch": repo.default_branch,
                    "created": str(repo.created_at),
                    "updated": str(repo.updated_at),
                    "url": repo.html_url,
                    "topics": repo.get_topics(),
                    "license": str(repo.license) if repo.license else None,
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_repo(self, name: str, description: str = "", private: bool = False) -> dict:
        g = self._gate("create_repo")
        if g:
            return g
        try:
            user = self._user_obj()
            repo = user.create_repo(name, description=description, private=private)
            return {
                "success": True,
                "repo": {
                    "name": repo.name,
                    "full_name": repo.full_name,
                    "url": repo.html_url,
                    "private": repo.private,
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_repo(self, full_name: str) -> dict:
        g = self._gate("delete_repo")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            repo.delete()
            return {"success": True, "deleted": full_name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def fork_repo(self, full_name: str) -> dict:
        g = self._gate("fork_repo")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            fork = repo.create_fork()
            return {
                "success": True,
                "fork": {
                    "name": fork.name,
                    "full_name": fork.full_name,
                    "url": fork.html_url,
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_repos(self, query: str, limit: int = 10) -> dict:
        try:
            client = self._get_client()
            repos = client.search_repositories(query=query)
            result = []
            for i, repo in enumerate(repos):
                if i >= limit:
                    break
                result.append(
                    {
                        "name": repo.name,
                        "full_name": repo.full_name,
                        "description": repo.description or "",
                        "language": repo.language or "unknown",
                        "stars": repo.stargazers_count,
                        "url": repo.html_url,
                    }
                )
            return {"success": True, "repos": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Issues ─────────────────────────────────────────────────────────────

    def list_issues(self, full_name: str, state: str = "open", limit: int = 20) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            issues = repo.get_issues(state=state, sort="updated")
            result = []
            for i, issue in enumerate(issues):
                if i >= limit:
                    break
                if issue.pull_request:
                    continue
                result.append(
                    {
                        "number": issue.number,
                        "title": issue.title,
                        "state": issue.state,
                        "body": (issue.body or "")[:200],
                        "user": issue.user.login if issue.user else "unknown",
                        "labels": [l.name for l in issue.labels],
                        "assignees": [a.login for a in issue.assignees],
                        "comments": issue.comments,
                        "created": str(issue.created_at),
                        "updated": str(issue.updated_at),
                        "url": issue.html_url,
                    }
                )
            return {"success": True, "issues": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_issue(
        self, full_name: str, title: str, body: str = "", labels: list = None, assignees: list = None
    ) -> dict:
        g = self._gate("create_issue")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            issue = repo.create_issue(
                title=title,
                body=body,
                labels=labels or [],
                assignees=assignees or [],
            )
            return {
                "success": True,
                "issue": {
                    "number": issue.number,
                    "title": issue.title,
                    "url": issue.html_url,
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def close_issue(self, full_name: str, number: int) -> dict:
        g = self._gate("close_issue")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            issue = repo.get_issue(number)
            issue.edit(state="closed")
            return {"success": True, "number": number, "state": "closed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def reopen_issue(self, full_name: str, number: int) -> dict:
        g = self._gate("reopen_issue")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            issue = repo.get_issue(number)
            issue.edit(state="open")
            return {"success": True, "number": number, "state": "open"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def comment_issue(self, full_name: str, number: int, body: str) -> dict:
        g = self._gate("comment")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            issue = repo.get_issue(number)
            comment = issue.create_comment(body)
            return {"success": True, "comment_id": comment.id, "url": comment.html_url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_label(self, full_name: str, number: int, labels: list) -> dict:
        g = self._gate("add_label")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            issue = repo.get_issue(number)
            issue.add_to_labels(*labels)
            return {"success": True, "number": number, "labels": labels}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Pull Requests ──────────────────────────────────────────────────────

    def list_prs(self, full_name: str, state: str = "open", limit: int = 20) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            prs = repo.get_pulls(state=state, sort="updated")
            result = []
            for i, pr in enumerate(prs):
                if i >= limit:
                    break
                result.append(
                    {
                        "number": pr.number,
                        "title": pr.title,
                        "state": pr.state,
                        "body": (pr.body or "")[:200],
                        "user": pr.user.login if pr.user else "unknown",
                        "head": pr.head.ref,
                        "base": pr.base.ref,
                        "mergeable": pr.mergeable,
                        "merged": pr.merged,
                        "comments": pr.comments,
                        "review_comments": pr.review_comments,
                        "changed_files": pr.changed_files,
                        "additions": pr.additions,
                        "deletions": pr.deletions,
                        "created": str(pr.created_at),
                        "updated": str(pr.updated_at),
                        "url": pr.html_url,
                    }
                )
            return {"success": True, "prs": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_pr(self, full_name: str, title: str, body: str = "", head: str = "", base: str = "main") -> dict:
        g = self._gate("create_pr")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            pr = repo.create_pull(title=title, body=body, head=head, base=base)
            return {
                "success": True,
                "pr": {
                    "number": pr.number,
                    "title": pr.title,
                    "url": pr.html_url,
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def merge_pr(self, full_name: str, number: int, commit_message: str = "") -> dict:
        g = self._gate("merge_pr")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            pr = repo.get_pull(number)
            if pr.mergeable:
                pr.merge(commit_message=commit_message or f"Merge PR #{number}")
                return {"success": True, "number": number, "merged": True}
            return {"success": False, "error": "PR not mergeable"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def review_pr(self, full_name: str, number: int, event: str = "COMMENT", body: str = "") -> dict:
        g = self._gate("review_pr")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            pr = repo.get_pull(number)
            if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
                event = "COMMENT"
            review = pr.create_review(body=body, event=event)
            return {"success": True, "review_id": review.id, "state": review.state}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_pr_files(self, full_name: str, number: int) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            pr = repo.get_pull(number)
            files = pr.get_files()
            result = []
            for f in files:
                result.append(
                    {
                        "filename": f.filename,
                        "status": f.status,
                        "additions": f.additions,
                        "deletions": f.deletions,
                        "changes": f.changes,
                        "patch": (f.patch or "")[:500],
                    }
                )
            return {"success": True, "files": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Branches ───────────────────────────────────────────────────────────

    def list_branches(self, full_name: str) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            branches = repo.get_branches()
            result = [{"name": b.name, "protected": b.protected} for b in branches]
            return {"success": True, "branches": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_branch(self, full_name: str, name: str, from_branch: str = "main") -> dict:
        g = self._gate("create_branch")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            source = repo.get_branch(from_branch)
            repo.create_git_ref(ref=f"refs/heads/{name}", sha=source.commit.sha)
            return {"success": True, "branch": name, "from": from_branch}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_branch(self, full_name: str, name: str) -> dict:
        g = self._gate("delete_branch")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            ref = repo.get_git_ref(f"heads/{name}")
            ref.delete()
            return {"success": True, "deleted": name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Commits ────────────────────────────────────────────────────────────

    def list_commits(self, full_name: str, sha: str = "main", limit: int = 20) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            commits = repo.get_commits(sha=sha)
            result = []
            for i, c in enumerate(commits):
                if i >= limit:
                    break
                result.append(
                    {
                        "sha": c.sha[:10],
                        "message": c.commit.message[:200],
                        "author": c.commit.author.name if c.commit.author else "unknown",
                        "date": str(c.commit.author.date) if c.commit.author else "",
                        "url": c.html_url,
                    }
                )
            return {"success": True, "commits": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── User ───────────────────────────────────────────────────────────────

    def get_user_info(self, username: Optional[str] = None) -> dict:
        try:
            client = self._get_client()
            user = client.get_user(username) if username else self._user_obj()
            return {
                "success": True,
                "user": {
                    "login": user.login,
                    "name": user.name or "",
                    "bio": user.bio or "",
                    "public_repos": user.public_repos,
                    "followers": user.followers,
                    "following": user.following,
                    "avatar": user.avatar_url,
                    "url": user.html_url,
                    "company": user.company or "",
                    "location": user.location or "",
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_starred(self, limit: int = 20) -> dict:
        try:
            user = self._user_obj()
            starred = user.get_starred()
            result = []
            for i, repo in enumerate(starred):
                if i >= limit:
                    break
                result.append(
                    {
                        "name": repo.name,
                        "full_name": repo.full_name,
                        "description": repo.description or "",
                        "stars": repo.stargazers_count,
                        "url": repo.html_url,
                    }
                )
            return {"success": True, "starred": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Search ─────────────────────────────────────────────────────────────

    def search_code(self, query: str, limit: int = 10) -> dict:
        try:
            client = self._get_client()
            results = client.search_code(query=query)
            items = []
            for i, item in enumerate(results):
                if i >= limit:
                    break
                items.append(
                    {
                        "name": item.name,
                        "path": item.path,
                        "repository": item.repository.full_name,
                        "url": item.html_url,
                    }
                )
            return {"success": True, "results": items, "count": len(items)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_issues(self, query: str, limit: int = 10) -> dict:
        try:
            client = self._get_client()
            results = client.search_issues(query=query)
            items = []
            for i, issue in enumerate(results):
                if i >= limit:
                    break
                items.append(
                    {
                        "number": issue.number,
                        "title": issue.title,
                        "state": issue.state,
                        "repository": issue.repository.full_name if issue.repository else "",
                        "url": issue.html_url,
                    }
                )
            return {"success": True, "results": items, "count": len(items)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Actions ────────────────────────────────────────────────────────────

    def list_workflows(self, full_name: str) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            workflows = repo.get_workflows()
            result = []
            for w in workflows:
                result.append(
                    {
                        "id": w.id,
                        "name": w.name,
                        "state": w.state,
                        "path": w.path,
                        "url": w.html_url,
                    }
                )
            return {"success": True, "workflows": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def trigger_workflow(self, full_name: str, workflow_id: int, ref: str = "main") -> dict:
        g = self._gate("trigger_workflow")
        if g:
            return g
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            workflow = repo.get_workflow(workflow_id)
            workflow.create_dispatch(ref)
            return {"success": True, "workflow": workflow.name, "ref": ref}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_workflow_runs(self, full_name: str, workflow_id: Optional[int] = None, limit: int = 10) -> dict:
        try:
            client = self._get_client()
            repo = client.get_repo(full_name)
            if workflow_id:
                workflow = repo.get_workflow(workflow_id)
                runs = workflow.get_runs()
            else:
                runs = repo.get_workflow_runs()
            result = []
            for i, run in enumerate(runs):
                if i >= limit:
                    break
                result.append(
                    {
                        "id": run.id,
                        "name": run.name,
                        "status": run.status,
                        "conclusion": run.conclusion or "",
                        "branch": run.head_branch,
                        "event": run.event,
                        "created": str(run.created_at),
                        "updated": str(run.updated_at),
                        "url": run.html_url,
                    }
                )
            return {"success": True, "runs": result, "count": len(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Git Operations (local) ─────────────────────────────────────────────

    def git_status(self) -> dict:
        try:
            r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10)
            return {"success": True, "status": r.stdout.strip()[:2000]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_log(self, limit: int = 10) -> dict:
        try:
            r = subprocess.run(["git", "log", "--oneline", f"-{limit}"], capture_output=True, text=True, timeout=10)
            lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
            return {"success": True, "commits": lines, "count": len(lines)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_add(self, files: list = None) -> dict:
        g = self._gate("git_add")
        if g:
            return g
        try:
            args = ["git", "add"]
            if files:
                args.extend(files)
            else:
                args.append(".")
            r = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return {"success": r.returncode == 0, "output": r.stdout.strip()[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_commit(self, message: str) -> dict:
        g = self._gate("git_commit")
        if g:
            return g
        try:
            r = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True, timeout=10)
            return {"success": r.returncode == 0, "output": r.stdout.strip()[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_push(self, branch: str = "main") -> dict:
        g = self._gate("git_push")
        if g:
            return g
        try:
            r = subprocess.run(["git", "push", "origin", branch], capture_output=True, text=True, timeout=30)
            return {"success": r.returncode == 0, "output": r.stdout.strip()[:500], "error": r.stderr.strip()[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_pull(self) -> dict:
        try:
            r = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=30)
            return {"success": r.returncode == 0, "output": r.stdout.strip()[:500]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_branches(self) -> dict:
        try:
            r = subprocess.run(["git", "branch", "-a"], capture_output=True, text=True, timeout=10)
            branches = [b.strip().replace("* ", "") for b in r.stdout.strip().split("\n") if b.strip()]
            return {"success": True, "branches": branches, "count": len(branches)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_diff(self) -> dict:
        try:
            r = subprocess.run(["git", "diff"], capture_output=True, text=True, timeout=10)
            return {"success": True, "diff": r.stdout.strip()[:3000]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Natural Language Handler ───────────────────────────────────────────

    def handle_natural(self, text: str) -> dict:
        import re

        t = text.lower().strip()

        if "status" in t and "github" in t:
            return self.get_status()

        if "list repos" in t or "my repos" in t or "show repos" in t:
            return self.list_repos()

        if "list my issues" in t or "my issues" in t:
            repos = self.list_repos(limit=1)
            if repos.get("success") and repos.get("repos"):
                repo_name = repos["repos"][0].get("full_name", "priyanshu0602/ORBIT")
            else:
                repo_name = "priyanshu0602/ORBIT"
            return self.list_issues(repo_name)

        m = re.match(r"(?:create|banao?|banaye?)\s+(?:issue|bug|feature)\s+(?:for|in|on)\s+(.+?)(?:\s*:\s*(.+))?$", t)
        if m:
            repo = m.group(1).strip()
            title = m.group(2) or "New issue"
            return self.create_issue(repo, title)

        m = re.match(r"(?:git status|repo status)", t)
        if m:
            return self.git_status()

        m = re.match(r"(?:git log|commit history|show commits)", t)
        if m:
            return self.git_log()

        if "git pull" in t or "pull latest" in t:
            return self.git_pull()

        m = re.match(r"(?:git push|push changes)", t)
        if m:
            return self.git_push()

        m = re.match(r"(?:search repos?|find repos?)\s+(.+)", t)
        if m:
            return self.search_repos(m.group(1))

        return {
            "success": False,
            "error": "GitHub command samajh nahi aaya. Try: list repos, git status, search repos AI, create issue for repo: title",
        }


def get_github_tool(token: Optional[str] = None) -> GitHubTool:
    return GitHubTool(token=token)
