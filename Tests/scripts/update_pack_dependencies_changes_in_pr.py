import json
import logging as logger
from argparse import ArgumentParser, Namespace
from pathlib import Path
from string import Template

from demisto_sdk.commands.common.constants import MarketplaceVersions
from demisto_sdk.commands.common.logger import logging_setup
from demisto_sdk.commands.common.tools import get_marketplace_to_core_packs

from Tests.scripts.find_pack_dependencies_changes import DEPENDENCIES_FIELDS, DIFF_FILENAME
from Tests.scripts.github_client import GithubPullRequest
from Tests.scripts.utils.log_util import install_logging

BOOL_TO_M_LEVEL: dict = {
    True: "mandatory",
    False: "optional",
}
CHANGE_TYPE_TO_TEMPLATE: dict[str, Template] = {
    "added": Template("   - A new *$m_level* dependency **$dep_id** was added.\n"),
    "removed": Template("   - Pack **$dep_id** is no longer a dependency.\n"),
    "modified": Template("   - The dependency **$dep_id** was changed to *$m_level*.\n"),
}
MP_VERSION_TO_DISPLAY: dict = {
    MarketplaceVersions.XSOAR: "XSOAR",
    MarketplaceVersions.MarketplaceV2: "XSIAM",
    MarketplaceVersions.XPANSE: "XPANSE",
}
NO_CHANGES_MSG = "**No changes in packs dependencies were made on this pull request.**"
CHANGES_MSG_TITLE = "## This pull request introduces changes in packs dependencies\n"


logging_setup(logger.DEBUG)
install_logging("update_pack_dependencies_changes_in_pr.log", logger=logger)


def parse_args() -> Namespace:
    options = ArgumentParser()
    options.add_argument('--artifacts-folder', required=True, help='The artifacts folder')
    options.add_argument('--github-token', required=True, help='A GitHub API token')
    options.add_argument('--current-sha', required=True, help='Current branch commit SHA')
    options.add_argument('--current-branch', required=True, help='Current branch name')
    return options.parse_args()


def get_summary(diff: dict, core_packs: set) -> str:
    """Logs and returns a string reperesentation of the pack dependencies changes.

    `diff` is expected to contain key-value pairs of pack IDs and their changes.
    The data is expected to be in the following structure:
    {
        "pack_id": {
            "added": {
                "dependencies": {  // first-level dependencies
                    "dep_id": {
                        "display_name": str,
                        "mandatory": bool,
                        ...
                    }
                },
                "allLevelDependencies": {
                    "dep_id": {
                        "display_name": str,
                        "mandatory": bool,
                        ...
                    }
                }
            },
            "removed": {...},
            "modified": {...}
        },
        ...
    }
    """
    s = ""

    pack_data: dict[str, dict[str, dict]]
    for pack_id, pack_data in diff.items():
        for change_type, change_data in pack_data.items():
            for dep_field in DEPENDENCIES_FIELDS:
                if dependencies_data := change_data.get(dep_field):
                    core_pack = " (core pack)" if pack_id in core_packs else ""
                    s += (
                        f"- In the {'all' if dep_field.startswith('all') else 'first'}-"
                        f"level dependencies of pack **{pack_id}{core_pack}**:\n"
                    )
                    for dep_id, dep_data in dependencies_data.items():
                        s += CHANGE_TYPE_TO_TEMPLATE[change_type].safe_substitute(
                            dep_id=dep_id,
                            m_level=BOOL_TO_M_LEVEL[dep_data["mandatory"]],
                        )
    if s:
        logger.info(s)
    return s


def aggregate_summaries(artifacts_folder: str) -> dict:
    """Aggregates summaries of pack dependencies changes in all marketplaces.

    Args:
        artifacts_folder (str): The artifacts folder.

    Returns:
        dict: a key-value pairs of marketplaces and their pack dependencies changes' summary.
    """
    summaries: dict = {}
    core_packs = get_marketplace_to_core_packs()
    for marketplace in list(MarketplaceVersions):
        diff_path = Path(artifacts_folder) / marketplace.value / DIFF_FILENAME
        if diff_path.is_file():
            diff = json.loads(diff_path.read_text())
            if summary := get_summary(diff, core_packs[marketplace]):
                summaries[marketplace.value] = summary
    return summaries


def format_summaries_to_single_comment(summaries: dict) -> str:
    if not any(bool(s) for s in summaries.values()):
        return NO_CHANGES_MSG
    s = CHANGES_MSG_TITLE
    for marketplace, summary in summaries.items():
        if summary:
            s += f"### {MP_VERSION_TO_DISPLAY[marketplace]}\n{summary}\n"
    return s


def main():  # pragma: no cover
    args = parse_args()
    summaries = aggregate_summaries(args.artifacts_folder)
    pull_request = GithubPullRequest(
        args.github_token,
        sha1=args.current_sha,
        branch=args.current_branch,
        fail_on_error=True,
    )

    pull_request.edit_comment(
        format_summaries_to_single_comment(summaries),
        section_name="Packs dependencies diff",
    )


if __name__ == '__main__':
    main()
