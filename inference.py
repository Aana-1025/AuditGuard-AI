from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL")
MODEL_NAME = os.getenv("MODEL_NAME")
HF_TOKEN = os.getenv("HF_TOKEN")

FLAG_MERCHANTS = {"GIFT HUB", "CASH DEPOT"}


def _require_env(name: str, value: str | None) -> str:
    if value and value.strip():
        return value.strip().rstrip("/")
    raise RuntimeError(f"Missing required environment variable: {name}")


def _resolve_observation(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("observation", payload)


def _merchant_name(item: dict[str, Any]) -> str:
    return str(item.get("merchant_descriptor") or item.get("merchant") or "").strip().upper()


def _item_id(item: dict[str, Any], index: int) -> str:
    value = item.get("item_id") or item.get("id") or item.get("expense_id")
    if value:
        return str(value).strip().upper()
    return f"EXP-{index + 1:03d}"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    if MODEL_NAME:
        session.headers.update({"X-Model-Name": MODEL_NAME})
    if HF_TOKEN:
        session.headers.update({"Authorization": f"Bearer {HF_TOKEN}"})
    return session


def main() -> None:
    base_url = _require_env("API_BASE_URL", API_BASE_URL)
    session = _session()

    print("START")
    print("STEP: resetting environment")
    reset_response = session.post(
        f"{base_url}/reset",
        json={"scenario": "easy", "seed": 0},
        timeout=30,
    )
    reset_response.raise_for_status()
    observation = _resolve_observation(reset_response.json())
    items = observation.get("items", [])

    for index, item in enumerate(items):
        item_id = _item_id(item, index)
        action_type = "flag" if _merchant_name(item) in FLAG_MERCHANTS else "approve"
        print(f"STEP: processing item {item_id} \u2192 {action_type.upper()}")
        step_response = session.post(
            f"{base_url}/step",
            json={
                "action_type": action_type,
                "item_id": item_id,
            },
            timeout=30,
        )
        step_response.raise_for_status()
        observation = _resolve_observation(step_response.json())

    print("STEP: finalising audit")
    finalise_response = session.post(
        f"{base_url}/step",
        json={"action_type": "finalise"},
        timeout=30,
    )
    finalise_response.raise_for_status()
    _resolve_observation(finalise_response.json())
    print("END")


if __name__ == "__main__":
    main()
