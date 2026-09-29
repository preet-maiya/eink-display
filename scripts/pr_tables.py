#!/usr/bin/env python3
"""Per-PR snapshot tables for staging Function Apps.

  pr_tables.py create <pr>   create pr<N>settings / pr<N>widgetcache, copying prod
                             data into them only when they don't exist yet
  pr_tables.py delete <pr>   drop them

Auth: Azure CLI login (needs Storage Table Data Contributor on the account).
Env: STORAGE_ACCOUNT_NAME
"""
import os
import sys

from azure.core.exceptions import ResourceExistsError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import AzureCliCredential

TABLES = ("settings", "widgetcache")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("create", "delete") or not sys.argv[2].isdigit():
        sys.exit(__doc__)
    action, pr = sys.argv[1], sys.argv[2]
    account = os.environ["STORAGE_ACCOUNT_NAME"]
    svc = TableServiceClient(
        endpoint=f"https://{account}.table.core.windows.net",
        credential=AzureCliCredential(),
    )

    for name in TABLES:
        target = f"pr{pr}{name}"
        if action == "delete":
            svc.delete_table(target)  # no-op if missing
            print(f"deleted {target}")
            continue
        try:
            svc.create_table(target)
        except ResourceExistsError:
            print(f"{target} exists, keeping PR data")
            continue
        src, dst = svc.get_table_client(name), svc.get_table_client(target)
        n = 0
        for entity in src.list_entities():
            dst.upsert_entity(entity, mode=UpdateMode.REPLACE)
            n += 1
        print(f"created {target}, copied {n} rows from {name}")


if __name__ == "__main__":
    main()
