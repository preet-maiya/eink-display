import json
import logging
import os
from datetime import datetime, timezone

from azure.data.tables import TableServiceClient, UpdateMode

_svc = None


def _service() -> TableServiceClient:
    """Return a cached TableServiceClient.

    Prefer the AzureWebJobsStorage connection string (always present, uses account
    key) over Managed Identity — avoids RBAC propagation lag on fresh deploys.
    """
    global _svc
    if _svc is None:
        conn_str = os.environ.get("AzureWebJobsStorage", "")
        if conn_str and not conn_str.startswith("UseDevelopment"):
            _svc = TableServiceClient.from_connection_string(conn_str)
            logging.info("table_ops: using connection string auth")
        else:
            # Local dev (Azurite) or explicit Managed Identity path
            from azure.identity import DefaultAzureCredential
            account = os.environ["STORAGE_ACCOUNT_NAME"]
            _svc = TableServiceClient(
                endpoint=f"https://{account}.table.core.windows.net",
                credential=DefaultAzureCredential(),
            )
            logging.info("table_ops: using DefaultAzureCredential")
    return _svc


def get_settings(key: str) -> dict:
    try:
        client = _service().get_table_client("settings")
        entity = client.get_entity(partition_key="config", row_key=key)
        return json.loads(entity.get("data", "{}"))
    except Exception:
        return {}


def set_settings(key: str, value) -> None:
    client = _service().get_table_client("settings")
    client.upsert_entity(
        {
            "PartitionKey": "config",
            "RowKey": key,
            "data": json.dumps(value),
        },
        mode=UpdateMode.REPLACE,
    )


def get_all_settings() -> dict:
    result = {}
    for key in ("widgets", "addresses", "meal_plan", "menu_items", "portfolio", "integrations", "schedule", "display"):
        result[key] = get_settings(key)
    return result


def get_cache(widget: str) -> dict:
    try:
        client = _service().get_table_client("widgetcache")
        entity = client.get_entity(partition_key="cache", row_key=widget)
        return {
            "data": json.loads(entity.get("data", "null")),
            "updated_at": entity.get("updated_at", ""),
        }
    except Exception:
        return {"data": None, "updated_at": ""}


def set_cache(widget: str, data) -> None:
    client = _service().get_table_client("widgetcache")
    client.upsert_entity(
        {
            "PartitionKey": "cache",
            "RowKey": widget,
            "data": json.dumps(data),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        mode=UpdateMode.REPLACE,
    )


def get_rotation_state(device_id: str) -> int:
    try:
        client = _service().get_table_client("settings")
        entity = client.get_entity(partition_key="state", row_key=device_id)
        return int(entity.get("widget_index", 0))
    except Exception:
        return 0


def set_rotation_state(device_id: str, index: int) -> None:
    client = _service().get_table_client("settings")
    client.upsert_entity(
        {
            "PartitionKey": "state",
            "RowKey": device_id,
            "widget_index": index,
        },
        mode=UpdateMode.REPLACE,
    )
