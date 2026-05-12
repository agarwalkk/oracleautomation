"""Agent main loop: poll → execute → report → repeat.

The loop runs forever until interrupted (Ctrl-C or SIGTERM).
A background heartbeat task fires every ``QCS_HEARTBEAT_S`` seconds so the
center can detect stale/dead agents.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import httpx

import config
from qcs_agent.executor import execute_job

log = logging.getLogger("qcs.agent.loop")


async def run_agent_loop(
    center_url: str,
    token: str,
    agent_name: str,
    tags: str = "",
) -> None:
    """Run forever: long-poll the center, execute jobs, post results."""
    hostname = socket.gethostname()
    headers = {"Authorization": f"Bearer {token}"}
    poll_body = {"agent_name": agent_name, "hostname": hostname, "tags": tags}
    hb_body   = {"agent_name": agent_name, "hostname": hostname, "tags": tags}

    # httpx timeout: poll endpoint holds connection open for ~29 s, so use 40 s
    async with httpx.AsyncClient(
        base_url=center_url,
        headers=headers,
        timeout=httpx.Timeout(connect=10.0, read=40.0, write=10.0, pool=10.0),
    ) as client:
        log.info("Agent '%s' (%s) started — center: %s", agent_name, hostname, center_url)

        hb_task = asyncio.create_task(
            _heartbeat_loop(client, hb_body), name="heartbeat"
        )
        try:
            while True:
                try:
                    resp = await client.post("/api/v1/agents/poll", json=poll_body)
                    resp.raise_for_status()
                    job: dict | None = resp.json().get("job")
                    if job is None:
                        # No work — poll immediately again
                        continue
                    log.info(
                        "Received job %s  run_id=%s", job["id"], job["run_id"]
                    )
                    await _handle_job(client, agent_name, job)

                except httpx.HTTPStatusError as exc:
                    log.error("HTTP error during poll: %s", exc)
                    await asyncio.sleep(5.0)
                except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                    log.warning("Connection error: %s — retrying in 10 s", exc)
                    await asyncio.sleep(10.0)
        except asyncio.CancelledError:
            log.info("Agent loop cancelled — shutting down.")
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass


async def _handle_job(
    client: httpx.AsyncClient, agent_name: str, job: dict
) -> None:
    """Execute a job and post the result back to the center."""
    job_id = job["id"]
    try:
        result = await execute_job(job)
    except Exception as exc:
        log.exception("Unhandled error executing job %s", job_id)
        result = {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Agent executor error: {exc}",
            "recording_jsonl": "",
        }

    payload = {
        "agent_name": agent_name,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "recording_jsonl": result["recording_jsonl"],
    }
    try:
        resp = await client.post(f"/api/v1/agents/result/{job_id}", json=payload)
        resp.raise_for_status()
        log.info(
            "Job %s posted — status: %s", job_id, resp.json().get("status")
        )
    except Exception as exc:
        log.error("Failed to post result for job %s: %s", job_id, exc)


async def _heartbeat_loop(client: httpx.AsyncClient, body: dict) -> None:
    interval = config.QCS_HEARTBEAT_S
    while True:
        await asyncio.sleep(interval)
        try:
            await client.post("/api/v1/agents/heartbeat", json=body)
        except Exception as exc:
            log.debug("Heartbeat failed (will retry): %s", exc)
