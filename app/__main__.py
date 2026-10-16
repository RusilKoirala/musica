"""`python -m app` - start the server and open the page, like a desktop app would."""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    parser = argparse.ArgumentParser(prog="musica", description="Local music downloader for a pendrive.")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    arguments = parser.parse_args()

    url = f"http://{arguments.host}:{arguments.port}"

    if not arguments.no_browser:
        def open_late() -> None:
            # Wait for the port to answer, so the first tab is never a connection error.
            import urllib.request

            for _ in range(60):
                try:
                    urllib.request.urlopen(f"{url}/api/health", timeout=0.4)
                    break
                except Exception:
                    time.sleep(0.25)
            webbrowser.open(url)

        threading.Thread(target=open_late, daemon=True).start()

    print(f"Musica on {url}  (Ctrl+C to stop)")
    uvicorn.run("app.main:app", host=arguments.host, port=arguments.port, log_level="info")


if __name__ == "__main__":
    main()
