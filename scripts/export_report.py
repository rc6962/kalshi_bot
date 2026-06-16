import os
import sys
from datetime import datetime
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def export_google_sheet_pdf(sheet_id: str, creds_path: str) -> bool:
    try:
        from google.oauth2 import service_account
        import gspread
    except ImportError:
        print("gspread not installed. Install with: pip install gspread google-auth")
        return False

    if not os.path.exists(creds_path):
        print(f"Credentials not found: {creds_path}")
        return False

    if not sheet_id or sheet_id == "your_google_sheet_id_here":
        print("Google Sheet ID not configured in .env")
        return False

    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        import requests
        token = creds.refresh(creds._token_request)
        access_token = creds.token

        export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
        params = {
            "format": "pdf",
            "portrait": "true",
            "fitw": "true",
            "gridlines": "false",
            "printtitle": "false",
            "sheetnames": "true",
            "pagenum": "UNDEFINED",
            "size": "letter",
            "top_margin": "0.5",
            "bottom_margin": "0.5",
            "left_margin": "0.5",
            "right_margin": "0.5",
        }
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = requests.get(export_url, params=params, headers=headers)

        if resp.status_code != 200:
            print(f"Export failed: {resp.status_code} {resp.reason}")
            return False

        reports_dir = Path(REPORTS_DIR)
        reports_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now()
        filename = f"Daily Report {today.strftime('%m-%d-%Y')}.pdf"
        filepath = reports_dir / filename

        with open(filepath, "wb") as f:
            f.write(resp.content)

        print(f"Report saved: {filepath}")
        return True

    except Exception as e:
        print(f"Export error: {e}")
        return False


def main():
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
    creds_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), creds_path)

    print(f"Exporting daily report...")
    print(f"Sheet ID: {sheet_id}")
    print(f"Credentials: {creds_path}")

    success = export_google_sheet_pdf(sheet_id, creds_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
