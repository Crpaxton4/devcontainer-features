"""``python -m webui``: serve on 0.0.0.0:8765 (the compose service publishes it on 127.0.0.1 only)."""

from webui import create_app

if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=8765, threaded=True)
