"""Démarre le banc d'essai et ouvre le navigateur."""

import os
import webbrowser

from gateway.server import serve

PORT = int(os.environ.get("MESH_PORT", "8091"))
OPEN_BROWSER = os.environ.get("MESH_OPEN_BROWSER", "1") not in ("0", "false", "False")
URL = f"http://127.0.0.1:{PORT}"

if __name__ == "__main__":
    try:
        serve(port=PORT, open_browser=OPEN_BROWSER)
    except OSError:
        print(f"Le banc d'essai tourne déjà : {URL}", flush=True)
        if OPEN_BROWSER:
            webbrowser.open(URL)
