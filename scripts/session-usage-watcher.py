#!/usr/bin/env python3
"""
Poll claude.ai/settings/usage via CDP and maintain /tmp/orch-session-usage.json.

Depends on a Chrome with remote-debugging-port=9222 running and logged
into claude.ai. scripts/launch-chrome-debug.sh starts such a Chrome.

Contract for the output file (consumed by scripts/session-usage-check.sh):
  {
    "usage_percent": <int 0-100>,
    "reset_epoch":   <unix ts when the 5h window resets>,
    "updated_epoch": <unix ts of this poll>,
    "source":        "browser",
    "reset_str":     <raw "1 hr 31 min" string, for debugging>
  }

Run in tmux or systemd. Logs to stderr.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

CDP_HTTP = "http://localhost:{port}"
USAGE_URL = "https://claude.ai/settings/usage"
OUT_PATH = Path("/tmp/orch-session-usage.json")
POLL_SECONDS = 120
RELOAD_EVERY_N_POLLS = 3  # force a page reload every ~6 min to keep numbers fresh

EXTRACT_JS = r"""
(() => {
  const text = document.body && document.body.innerText;
  if (!text) return { error: "no body text" };
  const re = /Current session\s*\n+\s*Resets in ([^\n]+)\s*\n+\s*(\d+)%\s*used/i;
  const m = text.match(re);
  if (!m) return { error: "no Current session block" };
  const reset_str = m[1].trim();
  const percent = parseInt(m[2], 10);
  let secs = 0;
  const hr_m = reset_str.match(/(\d+)\s*hr/i);
  const min_m = reset_str.match(/(\d+)\s*min/i);
  if (hr_m) secs += parseInt(hr_m[1], 10) * 3600;
  if (min_m) secs += parseInt(min_m[1], 10) * 60;
  const reset_epoch = Math.floor(Date.now() / 1000) + secs;
  return { usage_percent: percent, reset_epoch, reset_str };
})()
"""


async def cdp_send(ws, method, params=None, req_id=1):
    await ws.send(json.dumps({"id": req_id, "method": method, "params": params or {}}))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == req_id:
            return resp


def list_pages(port):
    with urllib.request.urlopen(f"{CDP_HTTP.format(port=port)}/json", timeout=5) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def find_or_open_usage_tab(port):
    pages = list_pages(port)
    for p in pages:
        if "claude.ai/settings/usage" in p.get("url", ""):
            return p
    # Open a new tab on the usage page
    url = f"{CDP_HTTP.format(port=port)}/json/new?" + USAGE_URL
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


async def poll_once(port, reload):
    page = find_or_open_usage_tab(port)
    ws_url = page["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=4 * 1024 * 1024) as ws:
        if reload:
            await cdp_send(ws, "Page.enable", req_id=1)
            await cdp_send(ws, "Page.reload", {"ignoreCache": True}, req_id=2)
            # give it time to render
            await asyncio.sleep(4)
        resp = await cdp_send(
            ws,
            "Runtime.evaluate",
            {"expression": EXTRACT_JS, "returnByValue": True, "awaitPromise": True},
            req_id=3,
        )
        result = resp.get("result", {}).get("result", {})
        return result.get("value", {"error": "no value", "raw": resp})


def write_out(payload):
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(OUT_PATH)


async def main_loop(port, once):
    log = logging.getLogger("usage-watcher")
    poll_count = 0
    while True:
        poll_count += 1
        reload = (poll_count % RELOAD_EVERY_N_POLLS == 1)  # always reload on first poll
        try:
            data = await poll_once(port, reload)
            if "error" in data:
                log.warning("parse error: %s", data.get("error"))
                # still write an aged-out marker so the gate fails open
            else:
                payload = {
                    "usage_percent": data["usage_percent"],
                    "reset_epoch": data["reset_epoch"],
                    "updated_epoch": int(time.time()),
                    "source": "browser",
                    "reset_str": data.get("reset_str", ""),
                }
                write_out(payload)
                log.info(
                    "usage=%d%% reset_in=%s reset_epoch=%d",
                    payload["usage_percent"],
                    payload["reset_str"],
                    payload["reset_epoch"],
                )
        except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
            log.error("Chrome unreachable on port %d: %s", port, e)
        except websockets.exceptions.WebSocketException as e:
            log.error("CDP websocket error: %s", e)
        except Exception as e:
            log.exception("unexpected: %s", e)

        if once:
            return
        await asyncio.sleep(POLL_SECONDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--once", action="store_true", help="poll once and exit (for testing)")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_loop(args.port, args.once))


if __name__ == "__main__":
    main()
