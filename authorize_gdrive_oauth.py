"""One-time Google Drive OAuth authorization helper.

This script opens a local browser sign-in flow and prints the environment
variables needed by Zeabur. Keep the generated token private.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_CLIENT_FILE = "gdrive_oauth_client.json"
DEFAULT_TOKEN_FILE = "gdrive_oauth_token.json"


def load_client_config(path: Path | None, prefer_env: bool = False) -> dict:
    env_json = os.getenv("GDRIVE_OAUTH_CLIENT_JSON")
    if prefer_env and env_json:
        return json.loads(env_json)
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if env_json:
        return json.loads(env_json)
    raise SystemExit(
        "找不到 OAuth client 設定。\n"
        f"請把 Google OAuth desktop client JSON 存成 {DEFAULT_CLIENT_FILE}，"
        "或設定 GDRIVE_OAUTH_CLIENT_JSON。"
    )


def compact_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Authorize Google Drive OAuth for Line Inspiration Helper.")
    parser.add_argument("--client-file", default=DEFAULT_CLIENT_FILE, help="OAuth desktop client JSON path.")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="Where to save the generated token JSON.")
    parser.add_argument("--port", type=int, default=0, help="Local callback port. 0 lets the OS choose.")
    parser.add_argument("--no-browser", action="store_true", help="Print the auth URL instead of opening a browser.")
    parser.add_argument("--prefer-env-client", action="store_true", help="Use GDRIVE_OAUTH_CLIENT_JSON before --client-file.")
    args = parser.parse_args()

    load_dotenv()
    client_path = Path(args.client_file)
    token_path = Path(args.token_file)
    client_config = load_client_config(client_path, prefer_env=args.prefer_env_client)

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    credentials = flow.run_local_server(port=args.port, prompt="consent", open_browser=not args.no_browser)
    token_info = json.loads(credentials.to_json())
    token_path.write_text(json.dumps(token_info, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n授權完成。請把以下環境變數設定到 Zeabur：\n")
    print("GDRIVE_AUTH_MODE=oauth")
    print(f"GDRIVE_OAUTH_CLIENT_JSON={compact_json(client_config)}")
    print(f"GDRIVE_OAUTH_TOKEN_JSON={compact_json(token_info)}")
    print("\n本機也已保存 token：")
    print(str(token_path.resolve()))
    print("\n注意：這些值等同 Drive 寫入權限，不要 commit、截圖或貼到公開地方。")


if __name__ == "__main__":
    main()
