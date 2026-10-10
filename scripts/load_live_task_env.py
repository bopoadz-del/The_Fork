#!/usr/bin/env python3
"""Load the live web task's env + secrets into this process.

Values are never printed. Each value is registered with GitHub's mask
directive when GITHUB_ENV is set. Reuses the same cluster/service the
deploy-aws workflow rolls.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List

_PREFIX = "SCRUB_AT_SOURCE"
_SKIP = {
    "PORT", "WORKER_COMMAND", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
    "P1B_KEEP_ALIVE",
}


def _emit(**fields: object) -> None:
    parts = [f"{key}={fields[key]}" for key in fields]
    print(f"{_PREFIX} {' '.join(parts)}", flush=True)


def mask_value(value: str) -> None:
    """Register a secret with the Actions mask. Never echo it otherwise."""
    if not value:
        return
    # Mask the whole value and each non-empty line (rules are newline-separated).
    chunks = [value] + [line for line in value.splitlines() if line.strip()]
    seen = set()
    for chunk in chunks:
        if chunk in seen or len(chunk) < 2:
            continue
        seen.add(chunk)
        sys.stdout.write(f"::add-mask::{chunk}\n")
    sys.stdout.flush()


def export_env(mapping: Dict[str, str], github_env: str | None) -> int:
    """Write mapping into os.environ and optionally GITHUB_ENV. Masks values."""
    n = 0
    handle = None
    if github_env:
        handle = open(github_env, "a", encoding="utf-8")
    try:
        for key, value in mapping.items():
            if not key or key in _SKIP or value is None:
                continue
            os.environ[key] = value
            mask_value(value)
            if handle is not None:
                handle.write(f"{key}<<__SCRUB_EOF__\n{value}\n__SCRUB_EOF__\n")
            n += 1
    finally:
        if handle is not None:
            handle.close()
    return n


def _aws_json(args: List[str]) -> object:
    proc = subprocess.run(
        args, check=False, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("aws_failed")
    return json.loads(proc.stdout or "null")


def _secret_string(value_from: str) -> str:
    """Resolve an ECS valueFrom ARN. stdout of aws is not forwarded."""
    if ":ssm:" in value_from and "parameter" in value_from:
        name = value_from.split("parameter", 1)[-1]
        if name.startswith("/"):
            name = name  # full path
        proc = subprocess.run(
            ["aws", "ssm", "get-parameter", "--name", name,
             "--with-decryption", "--query", "Parameter.Value", "--output", "text"],
            check=False, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError("ssm_failed")
        return (proc.stdout or "").rstrip("\n")
    # Secrets Manager. valueFrom may be arn:...:secret:name or arn:...:secret:name:key::
    key = ""
    arn = value_from
    if arn.count(":") >= 7 and arn.rstrip(":").split(":")[-1] and not arn.endswith("::"):
        # leave as-is
        pass
    parts = value_from.split(":")
    if len(parts) >= 8 and parts[6] == "secret":
        # arn:aws:secretsmanager:region:acct:secret:name[:jsonkey[:stage]]
        rest = parts[7:]
        if len(rest) >= 2 and rest[1]:
            key = rest[1]
            arn = ":".join(parts[:8])
    proc = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value",
         "--secret-id", arn, "--query", "SecretString", "--output", "text"],
        check=False, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("secretsmanager_failed")
    raw = proc.stdout or ""
    if raw.endswith("\n"):
        raw = raw[:-1]
    if key:
        try:
            blob = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("secret_json") from exc
        return str(blob.get(key, ""))
    return raw


def load_from_task(
    *, cluster: str, service: str, github_env: str | None,
) -> int:
    svc = _aws_json([
        "aws", "ecs", "describe-services",
        "--cluster", cluster, "--services", service,
    ])
    services = (svc or {}).get("services") or []
    if not services or not services[0].get("taskDefinition"):
        _emit(error="task_definition_missing", service=service)
        return 2
    td_arn = services[0]["taskDefinition"]
    td = _aws_json([
        "aws", "ecs", "describe-task-definition",
        "--task-definition", td_arn,
    ])
    defs = ((td or {}).get("taskDefinition") or {}).get("containerDefinitions") or []
    if not defs:
        _emit(error="container_missing")
        return 2
    container = defs[0]
    mapping: Dict[str, str] = {}
    for item in container.get("environment") or []:
        name = item.get("name")
        if name:
            mapping[name] = item.get("value") or ""
    for item in container.get("secrets") or []:
        name = item.get("name")
        src = item.get("valueFrom") or ""
        if not name or not src:
            continue
        mapping[name] = _secret_string(src)
    n = export_env(mapping, github_env)
    _emit(loaded_keys=n)
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load live task env; never print values.")
    parser.add_argument("--cluster", default=os.getenv("ECS_CLUSTER", "cerebrum"))
    parser.add_argument("--service", default=os.getenv("ECS_SERVICE", "the-fork"))
    parser.add_argument(
        "--github-env",
        default=os.getenv("GITHUB_ENV") or "",
        help="Path to GITHUB_ENV (empty: process env only)",
    )
    args = parser.parse_args(argv)
    try:
        return load_from_task(
            cluster=args.cluster,
            service=args.service,
            github_env=args.github_env or None,
        )
    except RuntimeError as exc:
        _emit(error=str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
