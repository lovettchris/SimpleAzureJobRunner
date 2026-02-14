import argparse
import json
from typing import Any, Dict, List, Set

from az_cmd import run_az_cmd


def list_role_assignments(prompt: str, scope: str):
    if scope:
        cmd = f'role assignment list --scope "{scope}"'
        data = run_az_cmd(cmd, prompt)
    else:
        cmd = "role assignment list --all"
        data = run_az_cmd(cmd, prompt)
    return data


def get_unique_users(data: Dict[str, Any]) -> Set[str]:
    unique_users = set()
    for identity in data["Stale identities"]:
        principal_id = identity["UserName"]
        unique_users.add(principal_id)
    return unique_users


def check_orphans(data: Dict[str, Any]) -> bool:
    unique_users = get_unique_users(data)
    to_remove = set()
    count = len(unique_users)
    for pos, principal_id in enumerate(unique_users):
        cmd = f"ad user show --id {principal_id}"
        try:
            result = run_az_cmd(cmd, f"check user {pos} of {count}: {principal_id} exists")
            givenName = result.get("givenName", "")
            surname = result.get("surname", "")
            mail = result.get("userPrincipalName")
            print(f"  User exists as: {givenName} {surname} <{mail}> therefore is not an orphan!")
            to_remove.add(principal_id)
        except Exception as e:
            msg = str(e)
            if "does not exist" in msg:
                print("  ok, we've verified this user really does not exist.")
                pass
            else:
                print(f"  Unexpected error, please try {principal_id} again later.")
                to_remove.add(principal_id)

    before = data["Stale identities"]
    for principal_id in to_remove:
        data["Stale identities"] = [
            identity for identity in data["Stale identities"] if identity["UserName"] != principal_id
        ]

    updated_unique_users = get_unique_users(data)
    new_len = len(updated_unique_users)
    if len(to_remove):
        print(f"Removed {len(to_remove)} non-orphaned identities from the list, leaving {new_len}.")
    after = data["Stale identities"]
    removed = len(before) - len(after)
    if removed > 0:
        print(f"Removed {removed} non-orphaned roles from the list, we now have {len(after)}.")
    return removed > 0


def find_orphaned_roles() -> Dict[str, Set]:
    # find any roles in the subscription that seem to be associated with users that no longer exist in Entra.
    result = list_role_assignments("list all role assignments to find orphaned accounts", "")
    orphaned_roles_by_scope: Dict[str, Set] = {}
    known_good_users = set()
    for item in result:
        principal_type = item.get("principalType", "")
        if principal_type != "User":
            continue
        principal_id = item["principalId"]
        principal_name = item.get("principalName", "")
        if principal_id in known_good_users:
            continue
        known_good_users.add(principal_id)
        try:
            data = run_az_cmd(
                f"ad user show --id {principal_id} --output json",
                f"check if principal {principal_id} {principal_name} is an orphan",
            )
            givenName = data.get("givenName", "")
            surname = data.get("surname", "")
            print(f"  Principal exists as user {givenName} {surname} therefore is not an orphan!")
        except Exception as e:
            msg = str(e)
            if "does not exist" in msg:
                scope = item["scope"]
                role_id = item["id"]
                if scope not in orphaned_roles_by_scope:
                    orphaned_roles_by_scope[scope] = set()
                orphaned_roles_by_scope[scope].add(role_id)
            else:
                print(f"Unexpected error, please check principal {principal_id} again later.")
    return orphaned_roles_by_scope


def remove_orphaned_account(json_file: str, dry_run: bool = False):

    roles_to_remove_by_scope: Dict[str, Set] = {}

    if json_file:
        with open(json_file, "r") as f:
            data = json.load(f)

        if "Stale identities" not in data:
            print("No 'Stale identities' found")
            return

        check_orphans(data)

        if not data["Stale identities"]:
            print("No stale identities to process")
            return

        role_assignments_by_scope: Dict[str, Any] = {}

        # Note the reason this is more complex than it should be is because with orphaned accounts
        # you can't just run "az role assignment delete --principal <principal_id>" because the id
        # is no longer defined and azure complains.  So instead we have to list the existing role
        # assignments to get the "role_id" that matches the orphaned principal id, and then delete
        # the role assignment by role_id.
        print("Looking up role assignment info for orphaned accounts...")
        count = len(data["Stale identities"])
        for pos, identity in enumerate(data["Stale identities"]):
            principal_id = identity["UserName"]
            scope = identity["Scope"]
            if scope not in role_assignments_by_scope:
                prompt = f"{pos} of {count}: list role assignments for scope {scope}"
                role_assignments_by_scope[scope] = list_role_assignments(prompt, scope)

            all_roles = role_assignments_by_scope[scope]
            for item in all_roles:
                role_id = item["id"]
                if item["principalId"] == principal_id:
                    if scope not in roles_to_remove_by_scope:
                        roles_to_remove_by_scope[scope] = set()
                    roles_to_remove_by_scope[scope].add(role_id)
    else:
        roles_to_remove_by_scope = find_orphaned_roles()

    if len(roles_to_remove_by_scope) == 0:
        print("No stale role assignments found for orphaned accounts, nothing to remove!")
        return

    # note also you might think that "--ids" could take a whole list of ids, but that fails
    # most of the time, so it is just easier to delete them one by one and that way if one
    # particular delete fails it won't stop the script from deleting all the others.
    errors: List[str] = []
    for scope, roles in roles_to_remove_by_scope.items():
        if dry_run:
            print(f"DRY_RUN: would remove {len(roles)} role assignments from scope {scope}")
        else:
            print(f"Removing {len(roles)} role assignments from scope {scope} ...")

        for role_id in roles:
            if dry_run:
                print(f"DRY RUN: would remove role {role_id}")
            else:
                prompt = f"Removing stale role {role_id} ..."
                cmd = f'role assignment delete --ids "{role_id}"'
                try:
                    result = run_az_cmd(cmd, prompt, no_data_ok=True)
                    if "provisioningState" in result:
                        print(result["provisioningState"])
                except Exception as e:
                    msg = str(e)
                    print(f"  Failed to remove role {role_id}: {msg}")
                    errors.append(f"Failed to remove role {role_id}: {msg}")

    if errors:
        print("\n".join(errors))


class SmartFormatter(argparse.HelpFormatter):
    def _split_lines(self, text, width):
        if text.startswith("R|"):
            return text[2:].splitlines()
        # this is the RawTextHelpFormatter._split_lines
        return argparse.HelpFormatter._split_lines(self, text, width)


example_data = {
    "Stale identities": [
        {
            "RoleName": "Unknown",
            "PrincipalName": "identity not found or stale account",
            "Scope": "/subscriptions/.../resourceGroups/.../providers/Microsoft.Storage/storageAccounts/...",
            "UserName": "810b0fcc-607a-413b-af86-14100c709f44",
            "IdentityType": "User",
            "AssignmentType": "Permanent",
        }
    ]
}


def parse_args():
    """Parse the json file name argument"""
    parser = argparse.ArgumentParser(description="Remove orphaned accounts", formatter_class=SmartFormatter)
    parser.add_argument(
        "--json_file",
        "-j",
        type=str,
        help="Optional json file containing the orphaned accounts that you get from S360 alert.\n"
        + "The json file should have the following format:\n"
        + json.dumps(example_data, indent=2),
    )
    parser.add_argument("--subscription", "-s", type=str, help="Optional subscription to switch your az account to.")
    parser.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        help="If specified, the script will verify the orphaned accounts. "
        + "And list the role assignments that would be removed, but it won't actually remove any role assignments.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.subscription:
        print(f"Switching to subscription {args.subscription} ...")
        run_az_cmd(f"account set --subscription {args.subscription}", "switch subscription", no_data_ok=True)
    remove_orphaned_account(args.json_file, args.dry_run)


if __name__ == "__main__":
    main()
