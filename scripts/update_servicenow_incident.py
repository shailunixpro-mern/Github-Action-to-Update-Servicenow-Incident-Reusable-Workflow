#!/usr/bin/env python3

import json
import os
import sys
from typing import Any, Dict, Optional

import requests


MAX_WORK_NOTES_CHARS = 15000
TERMINAL_SUCCESS_VALUES = {"success", "successful"}


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is null/empty")
    return value


def normalize_instance(instance: str) -> str:
    clean_instance = instance.replace("https://", "").replace("http://", "").rstrip("/")
    return f"https://{clean_instance}"


def build_session(user: str, password: str) -> requests.Session:
    session = requests.Session()
    session.auth = (user, password)
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    return session


def find_incident_by_number(session: requests.Session, instance_url: str, incident_number: str) -> Dict[str, Any]:
    url = f"{instance_url}/api/now/table/incident"
    response = session.get(
        url,
        params={
            "sysparm_query": f"number={incident_number}",
            "sysparm_limit": "1",
            "sysparm_fields": "sys_id,number,state,incident_state",
        },
        timeout=60,
    )
    response.raise_for_status()

    results = response.json().get("result", [])
    if not results:
        raise RuntimeError(f"Incident '{incident_number}' not found in ServiceNow")

    return results[0]


def update_incident(
    session: requests.Session,
    instance_url: str,
    incident_sys_id: str,
    short_description: str,
    work_notes: str,
) -> Dict[str, Any]:
    url = f"{instance_url}/api/now/table/incident/{incident_sys_id}"
    payload = {
        "short_description": short_description,
        "work_notes": work_notes[:MAX_WORK_NOTES_CHARS],
    }

    response = session.patch(url, json=payload, timeout=60)
    response.raise_for_status()
    return response.json().get("result", {})


def close_incident(session: requests.Session, instance_url: str, incident_sys_id: str) -> Dict[str, Any]:
    url = f"{instance_url}/api/now/table/incident/{incident_sys_id}"
    payload = {
        "incident_state": "7",
        "state": "6",
        "close_code": "Solved (Permanently)",
        "close_notes": "Closed automatically because AWX job completed successfully.",
    }

    response = session.patch(url, json=payload, timeout=60)
    response.raise_for_status()
    return response.json().get("result", {})


def write_result(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2)


def main() -> int:
    output_json = os.getenv("OUTPUT_JSON", "servicenow_update_result.json")

    try:
        runner_name = get_required_env("RUNNER_NAME")
        incident_number = get_required_env("INCIDENT_CURRENT_WORKFLOW_EXECUTION")
        awx_job_id = get_required_env("AWX_JOB_ID")
        awx_job_status = get_required_env("AWX_JOB_STATUS")
        awx_job_stdout_excerpt = get_required_env("AWX_JOB_STDOUT_EXCERPT")
        job_template_name = get_required_env("JOB_TEMPLATE_NAME")
        branch_name = get_required_env("BRANCH_NAME")
        servicenow_instance = normalize_instance(get_required_env("SERVICENOW_INSTANCE"))
        servicenow_user = get_required_env("SERVICENOW_USER")
        servicenow_password = get_required_env("SERVICENOW_PASSWORD")

        short_description = f"<{awx_job_id}>< {job_template_name} > <{branch_name}> <{awx_job_status}>"
        session = build_session(servicenow_user, servicenow_password)

        incident = find_incident_by_number(session, servicenow_instance, incident_number)
        incident_sys_id = str(incident.get("sys_id", "")).strip()
        if not incident_sys_id:
            raise RuntimeError(f"Incident '{incident_number}' returned without sys_id")

        update_incident(
            session,
            servicenow_instance,
            incident_sys_id,
            short_description,
            awx_job_stdout_excerpt,
        )

        incident_closed = False
        if awx_job_status.strip().lower() in TERMINAL_SUCCESS_VALUES:
            close_incident(session, servicenow_instance, incident_sys_id)
            incident_closed = True

        result_payload = {
            "runner_name": runner_name,
            "incident_number": incident_number,
            "incident_sys_id": incident_sys_id,
            "awx_job_id": awx_job_id,
            "awx_job_status": awx_job_status,
            "job_template_name": job_template_name,
            "branch_name": branch_name,
            "short_description": short_description,
            "incident_closed": incident_closed,
            "return_value": "Success",
        }
        write_result(output_json, result_payload)
        print(json.dumps(result_payload, indent=2))
        return 0

    except Exception as exc:
        result_payload = {
            "runner_name": os.getenv("RUNNER_NAME", ""),
            "incident_number": os.getenv("INCIDENT_CURRENT_WORKFLOW_EXECUTION", ""),
            "awx_job_id": os.getenv("AWX_JOB_ID", ""),
            "awx_job_status": os.getenv("AWX_JOB_STATUS", ""),
            "job_template_name": os.getenv("JOB_TEMPLATE_NAME", ""),
            "branch_name": os.getenv("BRANCH_NAME", ""),
            "incident_closed": False,
            "return_value": "Failure",
            "error": str(exc),
        }
        write_result(output_json, result_payload)
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
