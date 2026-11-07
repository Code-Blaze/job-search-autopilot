#!/usr/bin/env python3
"""Send a file or a message to your Telegram chat (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).

  notify_telegram.py <file> [caption]     send a document
  notify_telegram.py --text "<message>"   send a text message
Exit: 0 sent, 1 not configured, 2 send failed. The bot token never appears in output.
"""
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


def redact(text):
    return re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot<REDACTED>", str(text))


def configured():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not tok or not chat or "REPLACE" in tok + chat:
        return None
    return tok, chat


def _post(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
    except OSError as e:
        return False, str(e)
    try:
        return bool(json.loads(body).get("ok")), body
    except ValueError:
        return False, body


def send_text(msg):
    cfg = configured()
    if not cfg:
        return 1, "not configured"
    tok, chat = cfg
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg[:4096]}).encode()
    ok, body = _post("https://api.telegram.org/bot%s/sendMessage" % tok, data,
                     {"Content-Type": "application/x-www-form-urlencoded"}, 60)
    return (0, "sent") if ok else (2, redact(body)[:400])


def send_file(path, caption=""):
    cfg = configured()
    if not cfg:
        return 1, "not configured"
    if not os.path.isfile(path):
        return 2, "file not found: %s" % path
    tok, chat = cfg
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("chat_id", chat), ("caption", caption[:1024])):
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, name, value)).encode("utf-8"))
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"document\"; filename=\"%s\"\r\n"
                  "Content-Type: %s\r\n\r\n" % (boundary, os.path.basename(path), ctype)).encode("utf-8")
                 + content + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    ok, body = _post("https://api.telegram.org/bot%s/sendDocument" % tok, b"".join(parts),
                     {"Content-Type": "multipart/form-data; boundary=%s" % boundary}, 120)
    return (0, "sent") if ok else (2, redact(body)[:400])


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--text":
        rc, msg = send_text(sys.argv[2])
    elif len(sys.argv) >= 2 and sys.argv[1] != "--text":
        rc, msg = send_file(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        print(__doc__)
        return 2
    print("[telegram] %s" % ("not configured - skipping" if rc == 1 else msg if rc == 0 else "send FAILED: " + msg))
    return rc


if __name__ == "__main__":
    sys.exit(main())
