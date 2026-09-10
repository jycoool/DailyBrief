#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
drive_backup.py — 把每日初判（或其他檔案）備份到 Google Drive。

「草稿＋精修」引擎的雲端備份端。目標：每晚產出的初判自動上傳到你指定的
Drive 資料夾（例如 `每日初判/`），不用手動拖曳。

設計原則（呼應 knowledge_project.md 的踩坑紀錄）：
  1. Drive 連接器「只能新建、不能覆蓋」→ 我們每天新建一個帶日期的檔名，
     天然符合限制，不需要任何覆蓋動作。
  2. 上傳走 multipart（MediaIoBaseUpload），不做 base64 塞 metadata
     （base64Content 會損壞大檔/中文）。
  3. 認證用 OAuth refresh token（Google Cloud Console 建 Desktop 用戶端，
     跑授權腳本拿一次、永久有效），放環境變數，不寫死在程式。

---- 使用前：一次性設定（只需做一次）----
  0) pip install google-api-python-client google-auth google-auth-oauthlib
  1) Google Cloud Console：建專案 → 啟用「Google Drive API」
  2) 「API 與服務 → 憑證」→ 建立 OAuth 用戶端 ID（應用程式類型：桌面應用程式）
  3) 下載 JSON 存成  client_secret.json（放 Dashboard 資料夾）
  4) 跑一次授權腳本，拿 refresh token：
         python drive_backup.py --authorize
     會印出 refresh token，把整串貼進環境變數（見下）。

  環境變數（進 daily_brief 或 .env）：
     GOOGLE_DRIVE_REFRESH_TOKEN = <跑 --authorize 印出的 token>
     GOOGLE_DRIVE_FOLDER_ID     = <目標資料夾 ID，可省略，省略用 root>
     GOOGLE_DRIVE_CLIENT_ID     = <client_secret.json 內的 client_id>
     GOOGLE_DRIVE_CLIENT_SECRET = <client_secret.json 內的 client_secret>

---- 用法：----
  # 獨立備份一個檔案
  python drive_backup.py --file briefs/每日初判_盤後_2026-09-07_1853.md

  # 備份並指定父資料夾（用資料夾名稱，找不到自動建立）
  python drive_backup.py --file 某檔.md --folder 每日初判
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path=None):
    """載入 .env 檔到 os.environ（若存在）。不覆蓋已存在的環境變數。

    .env 格式：每行 KEY=VALUE，支援 `#` 註解與空行。不依賴 python-dotenv。
    """
    if path is None:
        path = os.path.join(_HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去掉包圍的引號（單或雙）
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            # 已有環境變數時不覆蓋
            os.environ.setdefault(key, value)


_load_dotenv()

# 目標資料夾：名稱優先（自動查找/建立），其次才看 GOOGLE_DRIVE_FOLDER_ID
DEFAULT_DRIVE_FOLDER_NAME = os.environ.get("GOOGLE_DRIVE_FOLDER_NAME", "每日初判")

# API scope：只要求檔案讀寫（不提完整 drive 全權），最小權限
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _load_creds():
    """用 refresh token 換 access token，回傳 google-auth 的 Credentials。"""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise SystemExit(
            "缺少 google-auth。請先：pip install google-api-python-client "
            "google-auth google-auth-oauthlib"
        )

    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN")
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET")

    if not refresh_token:
        # 試著從 client_secret.json 挖 client_id/secret（使用者可能只設了 refresh）
        sid, ssecret = _read_client_secret()
        client_id = client_id or sid
        client_secret = client_secret or ssecret

    if not refresh_token or not client_id or not client_secret:
        raise SystemExit(
            "缺少 Google Drive 認證。請先跑  python drive_backup.py --authorize，\n"
            "並把印出的 refresh token / client_id / client_secret 設成環境變數：\n"
            "  GOOGLE_DRIVE_REFRESH_TOKEN, GOOGLE_DRIVE_CLIENT_ID, "
            "GOOGLE_DRIVE_CLIENT_SECRET"
        )

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _read_client_secret():
    """從 client_secret.json 讀 client_id / client_secret（若存在）。"""
    import json
    p = os.path.join(_HERE, "client_secret.json")
    if not os.path.exists(p):
        return None, None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        inst = (data.get("installed") or data.get("web") or {})
        return inst.get("client_id"), inst.get("client_secret")
    except Exception:
        return None, None


def _build_service():
    """建立並回傳 googleapiclient 的 drive service。"""
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise SystemExit(
            "缺少 google-api-python-client。請先："
            "pip install google-api-python-client"
        )
    creds = _load_creds()
    return build("drive", "v3", credentials=creds)


def _find_or_create_folder(service, name, parent_id=None):
    """依名稱找資料夾；找不到就新建。回傳資料夾 ID。"""
    if not name:
        return parent_id  # 沒指定名稱 → 用父資料夾（或 root）

    q = ("mimeType='application/vnd.google-apps.folder' "
         f"and name='{name}' and trashed=false")
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(
        q=q, spaces="drive",
        fields="files(id, name)", pageSize=10).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]
    created = service.files().create(
        body=meta, fields="id").execute()
    return created.get("id")


def upload_file(local_path, folder_name=DEFAULT_DRIVE_FOLDER_NAME,
                folder_id=None, mime_type=None):
    """把 local_path 上傳到 Drive。回傳 file_id。失敗丟例外。

    - folder_name：目標資料夾名稱（找不到會自動建立）。None 則直接用 folder_id / root。
    - folder_id：也可直接指定父資料夾 ID（環境變數 GOOGLE_DRIVE_FOLDER_ID）。
    - 上傳用 multipart（MediaIoBaseUpload），中文檔名安全、不損壞。
    """
    from googleapiclient.http import MediaIoBaseUpload

    if not os.path.exists(local_path):
        raise FileNotFoundError(local_path)

    service = _build_service()

    # 決定父資料夾
    parent = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if folder_name:
        parent = _find_or_create_folder(service, folder_name, parent)

    fname = os.path.basename(local_path)
    if mime_type is None:
        mime_type = "text/markdown" if fname.endswith(".md") else None

    media = MediaIoBaseUpload(
        open(local_path, "rb"),
        mimetype=mime_type or "application/octet-stream",
        resumable=True,
    )
    meta = {"name": fname}
    if parent:
        meta["parents"] = [parent]

    created = service.files().create(
        body=meta, media_body=media, fields="id, name").execute()
    return created.get("id")


def authorize():
    """互動式一次性授權：拿 refresh token。使用者需本機跑一次。"""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit(
            "缺少 google-auth-oauthlib。請先：pip install google-auth-oauthlib"
        )

    p = os.path.join(_HERE, "client_secret.json")
    if not os.path.exists(p):
        raise SystemExit(
            f"找不到 {p}\n"
            "請先到 Google Cloud Console 建立 OAuth 桌面用戶端，"
            "下載 JSON 存成 client_secret.json。"
        )

    flow = InstalledAppFlow.from_client_secrets_file(p, SCOPES)
    creds = flow.run_local_server(port=0)  # 開瀏覽器授權

    print("\n授權完成！請把下面三行設成環境變數：\n")
    print(f"GOOGLE_DRIVE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"GOOGLE_DRIVE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_DRIVE_CLIENT_SECRET={creds.client_secret}")
    print("\n提示：refresh token 是長期有效的機密，別 commit 進版本控制。")


def main():
    p = argparse.ArgumentParser(description="Google Drive 備份工具")
    p.add_argument("--authorize", action="store_true",
                   help="一次性 OAuth 授權，拿 refresh token")
    p.add_argument("--file", help="要備份的檔案路徑")
    p.add_argument("--folder", default=DEFAULT_DRIVE_FOLDER_NAME,
                   help=f"目標資料夾名稱（預設 {DEFAULT_DRIVE_FOLDER_NAME}）")
    args = p.parse_args()

    if args.authorize:
        authorize()
        return

    if not args.file:
        p.error("請用 --file 指定要備份的檔案（或 --authorize 授權）")

    try:
        fid = upload_file(args.file, folder_name=args.folder)
        print(f"  [Drive] 已上傳 {os.path.basename(args.file)} → file_id={fid}")
    except Exception as e:
        print(f"  [Drive] 上傳失敗：{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()