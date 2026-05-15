"""
One-time OAuth2 setup for Google Drive uploads.
Run this once to authorize the clinic bot to upload files to your Google Drive.

Usage: python setup_drive_auth.py
"""
import os
import json
from pathlib import Path

CREDENTIALS_PATH = Path(__file__).parent / "googlekey" / "oauth_credentials.json"
TOKEN_PATH = Path(__file__).parent / "googlekey" / "oauth_token.json"

SCOPES = ['https://www.googleapis.com/auth/drive']


def main():
    if not CREDENTIALS_PATH.exists():
        print(f"ERROR: {CREDENTIALS_PATH} not found.")
        print("Download OAuth2 credentials from Google Cloud Console and save it there.")
        return

    with open(CREDENTIALS_PATH) as f:
        creds_data = json.load(f)

    # Check if client_secret is set
    secret = creds_data.get("installed", {}).get("client_secret", "")
    if "PASTE_YOUR_CLIENT_SECRET_HERE" in secret:
        print("ERROR: You need to paste your client_secret into googlekey/oauth_credentials.json")
        print("Find it at: https://console.cloud.google.com/apis/credentials")
        return

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Installing google-auth-oauthlib...")
        os.system("pip install google-auth-oauthlib")
        from google_auth_oauthlib.flow import InstalledAppFlow

    print("=" * 60)
    print("Google Drive OAuth2 Authorization")
    print("=" * 60)
    print()
    print("A browser window will open. Sign in with your Google account")
    print("and grant permission to upload files to your Drive.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    # Save the token
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }

    with open(TOKEN_PATH, "w") as f:
        json.dump(token_data, f, indent=2)

    print()
    print(f"Token saved to: {TOKEN_PATH}")
    print("You can now close this window. Drive uploads will work automatically.")
    print()

    # Quick test
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    test_creds = Credentials(
        token=creds.token,
        refresh_token=creds.refresh_token,
        token_uri=creds.token_uri,
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        scopes=creds.scopes,
    )
    service = build('drive', 'v3', credentials=test_creds)
    about = service.about().get(fields="user(emailAddress, displayName)").execute()
    user = about.get("user", {})
    print(f"Authenticated as: {user.get('displayName', 'Unknown')} ({user.get('emailAddress', 'Unknown')})")
    print("Setup complete!")


if __name__ == "__main__":
    main()
