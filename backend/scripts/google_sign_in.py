"""
One-time sign-in that lets the backend act as your Google account.

The audit spreadsheets live in your own Drive, and a service account can't
create files there, so the backend uses a sign-in to your account instead.

    pip install -r backend/anpr-server/requirements-dev.txt
    python backend/scripts/google_sign_in.py --client-secret ~/Downloads/client_secret_XXX.json

A browser opens; sign in and allow access. Google warns that the app isn't
verified: that's expected for your own OAuth client (Advanced -> Go to ...).
The sign-in is written to backend/google-token.json (git-ignored, and outside
the folder that's uploaded to Cloud Run). Then deploy with
OAUTH_TOKEN_FILE=backend/google-token.json, or for local runs set
GOOGLE_APPLICATION_CREDENTIALS to that file.

Set the OAuth consent screen to "In production": while it's "Testing",
Google expires the sign-in after 7 days.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "anpr-server"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-secret", type=Path, required=True,
                    help="the OAuth client JSON (Desktop app) downloaded from Google Cloud")
    ap.add_argument("--out", type=Path, default=ROOT / "google-token.json")
    args = ap.parse_args()

    from google_auth_oauthlib.flow import InstalledAppFlow
    from sheets import SCOPES  # the same access the server asks for

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), SCOPES)
    # offline + consent: always return a refresh token, so the sign-in lasts
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        raise SystemExit("Google returned no refresh token; run again.")

    args.out.write_text(creds.to_json())
    os.chmod(args.out, 0o600)
    print(f"Saved the sign-in to {args.out}. Keep it private: it can edit your Drive.")


if __name__ == "__main__":
    main()
