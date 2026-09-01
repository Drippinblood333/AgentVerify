"""Tiny standard-library web application maintained for the M6 verification demo."""

from __future__ import annotations

import argparse
import os
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = b"""<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Greeting</title></head>
  <body>
    <label>Name <input id="name"></label>
    <button id="greet">Greet</button>
    <p id="message" hidden></p>
    <p id="never-visible" hidden>This remains hidden.</p>
    <script>
      document.querySelector("#greet").addEventListener("click", () => {
        const name = document.querySelector("#name").value;
        const message = document.querySelector("#message");
        message.textContent = `Hello, ${name}!`;
        message.hidden = name !== "Ada";
      });
    </script>
  </body>
</html>
"""


class GreetingHandler(BaseHTTPRequestHandler):
    """Serve the single maintained greeting page."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the DoneWitness greeting demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--startup-delay-ms", type=int, default=0)
    parser.add_argument("--ignore-terminate", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.pid_file is not None:
        args.pid_file.write_text(str(os.getpid()), encoding="ascii")
    print("Greeting app process started", flush=True)
    if args.ignore_terminate and os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if args.startup_delay_ms > 0:
        time.sleep(args.startup_delay_ms / 1000)

    server = ThreadingHTTPServer((args.host, args.port), GreetingHandler)
    print(f"Greeting app listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
