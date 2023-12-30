# Copyright (C) 2008 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
import functools
import optparse
import re
import sys
from typing import List

from command import DEFAULT_LOCAL_JOBS
from command import InteractiveCommand
from editor import Editor
from error import GitError
from error import SilentRepoExitError
from error import PushError
from git_command import GitCommand
from git_refs import R_HEADS
from hooks import RepoHook
from project import ReviewableBranch
from repo_logging import RepoLogger
from subcmds.sync import LocalSyncState


_DEFAULT_UNUSUAL_COMMIT_THRESHOLD = 5
logger = RepoLogger(__file__)


class PushExitError(SilentRepoExitError):
    """Indicates that there is an push command error requiring a sys exit."""


def _VerifyPendingCommits(branches: List[ReviewableBranch]) -> bool:
    """Perform basic safety checks on the given set of branches.

    Ensures that each branch does not have a "large" number of commits
    and, if so, prompts the user to confirm they want to proceed with
    the push.

    Returns true if all branches pass the safety check or the user
    confirmed. Returns false if the push should be aborted.
    """

    # Determine if any branch has a suspicious number of commits.
    many_commits = False
    for branch in branches:
        # Get the user's unusual threshold for the branch.
        #
        # Each branch may be configured to have a different threshold.
        remote = branch.project.GetBranch(branch.name).remote
        key = f"review.{remote.review}.pushwarningthreshold"
        threshold = branch.project.config.GetInt(key)
        if threshold is None:
            threshold = _DEFAULT_UNUSUAL_COMMIT_THRESHOLD

        # If the branch has more commits than the threshold, show a warning.
        if len(branch.commits) > threshold:
            many_commits = True
            break

    # If any branch has many commits, prompt the user.
    if many_commits:
        if len(branches) > 1:
            logger.warning(
                "ATTENTION: One or more branches has an unusually high number "
                "of commits."
            )
        else:
            logger.warning(
                "ATTENTION: You are pushing an unusually high number of "
                "commits."
            )
        logger.warning(
            "YOU PROBABLY DO NOT MEAN TO DO THIS. (Did you rebase across "
            "branches?)"
        )
        answer = input(
            "If you are sure you intend to do this, type 'yes': "
        ).strip()
        return answer == "yes"

    return True


def _die(fmt, *args):
    msg = fmt % args
    logger.error("error: %s", msg)
    raise PushExitError(msg)


def _SplitEmails(values):
    result = []
    for value in values:
        result.extend([s.strip() for s in value.split(",")])
    return result


class Push(InteractiveCommand):
    COMMON = True
    helpSummary = "Push changes to upstream"
    helpUsage = """
%prog [--re --cc] [<project>]...
"""
    helpDescription = """
The '%prog' command is used to push changes to the upstream server
system.  It searches for topic branches in local projects that have 
not yet been published.  If multiple topic branches are found, 
'%prog' opens an editor to allow the user to select which branches 
to push.

'%prog' searches for pushable changes in all projects listed at
the command line.  Projects can be specified either by name, or by
a relative or absolute path to the project's local directory. If no
projects are specified, '%prog' will search for pushable changes
in all projects listed in the manifest.

# Configuration
"""
    PARALLEL_JOBS = DEFAULT_LOCAL_JOBS

    def _Options(self, p):
        p.add_option(
            "--br",
            "--branch",
            type="string",
            action="store",
            dest="branch",
            help="(local) branch to push",
        )
        p.add_option(
            "-c",
            "--current-branch",
            dest="current_branch",
            action="store_true",
            help="push current git branch",
        )
        p.add_option(
            "--no-current-branch",
            dest="current_branch",
            action="store_false",
            help="push all git branches",
        )
        # Turn this into a warning & remove this someday.
        p.add_option(
            "--cbr",
            dest="current_branch",
            action="store_true",
            help=optparse.SUPPRESS_HELP,
        )
        p.add_option(
            "-D",
            "--destination",
            "--dest",
            type="string",
            action="store",
            dest="dest_branch",
            metavar="BRANCH",
            help="push this target branch",
        )
        p.add_option(
            "-n",
            "--dry-run",
            dest="dryrun",
            default=False,
            action="store_true",
            help="do everything except actually push the CL",
        )
        p.add_option(
            "-y",
            "--yes",
            default=False,
            action="store_true",
            help="answer yes to all safe prompts",
        )
        p.add_option(
            "--ignore-untracked-files",
            action="store_true",
            default=False,
            help="ignore untracked files in the working copy",
        )
        p.add_option(
            "--no-ignore-untracked-files",
            dest="ignore_untracked_files",
            action="store_false",
            help="always ask about untracked files in the working copy",
        )
        p.add_option(
            "--no-cert-checks",
            dest="validate_certs",
            action="store_false",
            default=True,
            help="disable verifying ssl certs (unsafe)",
        )
        RepoHook.AddOptionGroup(p, "pre-push")

    def _SingleBranch(self, opt, branch):
        project = branch.project
        name = branch.name
        remote = project.GetBranch(name).remote

        key = "review.%s.autopush" % remote.review
        answer = project.config.GetBoolean(key)

        if answer is False:
            _die("push blocked by %s = false" % key)

        if answer is None:
            date = branch.date
            commit_list = branch.commits

            destination = (
                opt.dest_branch or project.dest_branch or project.revisionExpr
            )
            '''
            print(
                "Push project %s/ to remote branch %s%s:"
                % (
                    project.RelPath(local=opt.this_manifest_only),
                    destination,
                    " (private)" if opt.private else "",
                )
            )
            '''
            print(
                "  branch %s (%2d commit%s, %s):"
                % (
                    name,
                    len(commit_list),
                    len(commit_list) != 1 and "s" or "",
                    date,
                )
            )
            for commit in commit_list:
                print("         %s" % commit)

            print("to %s (y/N)? " % remote.review, end="", flush=True)
            if opt.yes:
                print("<--yes>")
                answer = True
            else:
                answer = sys.stdin.readline().strip().lower()
                answer = answer in ("y", "yes", "1", "true", "t")
            if not answer:
                _die("push aborted by user")

        # Perform some basic safety checks prior to pushing.
        if not opt.yes and not _VerifyPendingCommits([branch]):
            _die("push aborted by user")

        self._PushAndReport(opt, [branch])

    def _MultipleBranches(self, opt, pending):
        projects = {}
        branches = {}

        script = []
        script.append("# Uncomment the branches to push:")
        for project, avail in pending:
            project_path = project.RelPath(local=opt.this_manifest_only)
            script.append("#")
            script.append(f"# project {project_path}/:")

            b = {}
            for branch in avail:
                if branch is None:
                    continue
                name = branch.name
                date = branch.date
                commit_list = branch.commits

                if b:
                    script.append("#")
                destination = (
                    opt.dest_branch
                    or project.dest_branch
                    or project.revisionExpr
                )
                script.append(
                    "#  branch %s (%2d commit%s, %s) to remote branch %s:"
                    % (
                        name,
                        len(commit_list),
                        len(commit_list) != 1 and "s" or "",
                        date,
                        destination,
                    )
                )
                for commit in commit_list:
                    script.append("#         %s" % commit)
                b[name] = branch

            projects[project_path] = project
            branches[project_path] = b
        script.append("")

        script = Editor.EditString("\n".join(script)).split("\n")

        project_re = re.compile(r"^#?\s*project\s*([^\s]+)/:$")
        branch_re = re.compile(r"^\s*branch\s*([^\s(]+)\s*\(.*")

        project = None
        todo = []

        for line in script:
            m = project_re.match(line)
            if m:
                name = m.group(1)
                project = projects.get(name)
                if not project:
                    _die("project %s not available for push", name)
                continue

            m = branch_re.match(line)
            if m:
                name = m.group(1)
                if not project:
                    _die("project for branch %s not in script", name)
                project_path = project.RelPath(local=opt.this_manifest_only)
                branch = branches[project_path].get(name)
                if not branch:
                    _die("branch %s not in %s", name, project_path)
                todo.append(branch)
        if not todo:
            _die("nothing uncommented for push")

        # Perform some basic safety checks prior to pushing.
        if not opt.yes and not _VerifyPendingCommits(todo):
            _die("push aborted by user")

        self._PushAndReport(opt, todo, people)


    def _FindGerritChange(self, branch):
        last_pub = branch.project.WasPublished(branch.name)
        if last_pub is None:
            return ""

        refs = branch.GetPublishedRefs()
        try:
            # refs/changes/XYZ/N --> XYZ
            return refs.get(last_pub).split("/")[-2]
        except (AttributeError, IndexError):
            return ""

    def _PushBranch(self, opt, branch):
        """Push Branch."""

        # Check if there are local changes that may have been forgotten.
        changes = branch.project.UncommitedFiles()
        if opt.ignore_untracked_files:
            untracked = set(branch.project.UntrackedFiles())
            changes = [x for x in changes if x not in untracked]

        if changes:
            key = "review.%s.autopush" % branch.project.remote.review
            answer = branch.project.config.GetBoolean(key)

            # If they want to auto push, let's not ask because it
            # could be automated.
            if answer is None:
                print()
                print(
                    "Uncommitted changes in %s (did you forget to "
                    "amend?):" % branch.project.name
                )
                print("\n".join(changes))
                print("Continue pushing? (y/N) ", end="", flush=True)
                if opt.yes:
                    print("<--yes>")
                    a = "yes"
                else:
                    a = sys.stdin.readline().strip().lower()
                if a not in ("y", "yes", "t", "true", "on"):
                    print("skipping push", file=sys.stderr)
                    branch.pushed = False
                    branch.error = "User aborted"
                    return

        # Check if topic branches should be sent to the server during
        # push.
        '''
        if opt.auto_topic is not True:
            key = "review.%s.pushtopic" % branch.project.remote.review
            opt.auto_topic = branch.project.config.GetBoolean(key)
        '''

        def _ExpandCommaList(value):
            """Split |value| up into comma delimited entries."""
            if not value:
                return
            for ret in value.split(","):
                ret = ret.strip()
                if ret:
                    yield ret

        '''
        # Check if hashtags should be included.
        key = "review.%s.pushhashtags" % branch.project.remote.review
        hashtags = set(_ExpandCommaList(branch.project.config.GetString(key)))
        for tag in opt.hashtags:
            hashtags.update(_ExpandCommaList(tag))
        if opt.hashtag_branch:
            hashtags.add(branch.name)

        # Check if labels should be included.
        key = "review.%s.pushlabels" % branch.project.remote.review
        labels = set(_ExpandCommaList(branch.project.config.GetString(key)))
        for label in opt.labels:
            labels.update(_ExpandCommaList(label))

        # Handle e-mail notifications.
        if opt.notify is False:
            notify = "NONE"
        else:
            key = "review.%s.pushnotify" % branch.project.remote.review
            notify = branch.project.config.GetString(key)
        '''

        destination = opt.dest_branch or branch.project.dest_branch

        if branch.project.dest_branch and not opt.dest_branch:
            merge_branch = self._GetMergeBranch(
                branch.project, local_branch=branch.name
            )

            full_dest = destination
            if not full_dest.startswith(R_HEADS):
                full_dest = R_HEADS + full_dest

            # If the merge branch of the local branch is different from
            # the project's revision AND destination, this might not be
            # intentional.
            if (
                merge_branch
                and merge_branch != branch.project.revisionExpr
                and merge_branch != full_dest
            ):
                print(
                    f"For local branch {branch.name}: merge branch "
                    f"{merge_branch} does not match destination branch "
                    f"{destination}"
                )
                print("skipping push.")
                print(
                    f"Please use `--destination {destination}` if this "
                    "is intentional"
                )
                branch.pushed = False
                return

        branch.PushDirect(
            dryrun=opt.dryrun,
            auto_topic=False,
            hashtags=(),
            labels=(),
            private=False,
            notify=None,
            wip=False,
            ready=False,
            dest_branch=destination,
            validate_certs=opt.validate_certs,
            push_options=None,
        )

        branch.pushed = True

    def _PushAndReport(self, opt, todo):
        have_errors = False
        aggregate_errors = []
        for branch in todo:
            try:
                self._PushBranch(opt, branch)
            except (PushError, GitError) as e:
                self.git_event_log.ErrorEvent(f"push error: {e}")
                branch.error = e
                aggregate_errors.append(e)
                branch.pushed = False
                have_errors = True

        print(file=sys.stderr)
        print("-" * 70, file=sys.stderr)

        if have_errors:
            for branch in todo:
                if not branch.pushed:
                    if len(str(branch.error)) <= 30:
                        fmt = " (%s)"
                    else:
                        fmt = "\n       (%s)"
                    print(
                        ("[FAILED] %-15s %-15s" + fmt)
                        % (
                            branch.project.RelPath(local=opt.this_manifest_only)
                            + "/",
                            branch.name,
                            str(branch.error),
                        ),
                        file=sys.stderr,
                    )
            print()

        for branch in todo:
            if branch.pushed:
                print(
                    "[OK    ] %-15s %s"
                    % (
                        branch.project.RelPath(local=opt.this_manifest_only)
                        + "/",
                        branch.name,
                    ),
                    file=sys.stderr,
                )

        if have_errors:
            raise PushExitError(aggregate_errors=aggregate_errors)

    def _GetMergeBranch(self, project, local_branch=None):
        if local_branch is None:
            p = GitCommand(
                project,
                ["rev-parse", "--abbrev-ref", "HEAD"],
                capture_stdout=True,
                capture_stderr=True,
            )
            p.Wait()
            local_branch = p.stdout.strip()
        p = GitCommand(
            project,
            ["config", "--get", "branch.%s.merge" % local_branch],
            capture_stdout=True,
            capture_stderr=True,
        )
        p.Wait()
        merge_branch = p.stdout.strip()
        return merge_branch

    @staticmethod
    def _GatherOne(opt, project):
        """Figure out the push status for |project|."""
        if opt.current_branch:
            cbr = project.CurrentBranch
            up_branch = project.GetPushableBranch(cbr)
            avail = [up_branch] if up_branch else None
        else:
            avail = project.GetPushableBranches(opt.branch)
        return (project, avail)

    def Execute(self, opt, args):
        projects = self.GetProjects(
            args, all_manifests=not opt.this_manifest_only
        )

        def _ProcessResults(_pool, _out, results):
            pending = []
            for result in results:
                project, avail = result
                if avail is None:
                    logger.error(
                        'repo: error: %s: Unable to push branch "%s". '
                        "You might be able to fix the branch by running:\n"
                        "  git branch --set-upstream-to m/%s",
                        project.RelPath(local=opt.this_manifest_only),
                        project.CurrentBranch,
                        project.manifest.branch,
                    )
                elif avail:
                    pending.append(result)
            return pending

        pending = self.ExecuteInParallel(
            opt.jobs,
            functools.partial(self._GatherOne, opt),
            projects,
            callback=_ProcessResults,
        )

        if not pending:
            if opt.branch is None:
                logger.error("repo: error: no branches ready for push")
            else:
                logger.error(
                    'repo: error: no branches named "%s" ready for push',
                    opt.branch,
                )
            return 1

        manifests = {
            project.manifest.topdir: project.manifest
            for (project, available) in pending
        }
        ret = 0
        for manifest in manifests.values():
            pending_proj_names = [
                project.name
                for (project, available) in pending
                if project.manifest.topdir == manifest.topdir
            ]
            pending_worktrees = [
                project.worktree
                for (project, available) in pending
                if project.manifest.topdir == manifest.topdir
            ]
            hook = RepoHook.FromSubcmd(
                hook_type="pre-push",
                manifest=manifest,
                opt=opt,
                abort_if_user_denies=True,
            )
            if not hook.Run(
                project_list=pending_proj_names, worktree_list=pending_worktrees
            ):
                if LocalSyncState(manifest).IsPartiallySynced():
                    logger.error(
                        "Partially synced tree detected. Syncing all projects "
                        "may resolve issues you're seeing."
                    )
                ret = 1
        if ret:
            return ret

        if len(pending) == 1 and len(pending[0][1]) == 1:
            self._SingleBranch(opt, pending[0][1][0])
        else:
            self._MultipleBranches(opt, pending)
