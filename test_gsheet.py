import os
import traceback
import sys

def manual_load_dotenv(filepath=".env"):
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

def test_sheet():
    manual_load_dotenv()
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    
    print("Diagnostics:")
    print(f"GOOGLE_CREDENTIALS_PATH: {creds_path}")
    print(f"GOOGLE_SHEET_ID: {sheet_id}")
    print(f"Credentials file exists: {os.path.exists(creds_path)}")
    
    try:
        import gspread
        from google.oauth2 import service_account
        print("Successfully imported gspread and google.oauth2")
    except ImportError as e:
        print(f"Import Error: {e}")
        print("Please install gspread and google-auth. Run: pip install gspread google-auth")
        return
        
    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        print("Service account credentials loaded successfully.")
        
        client = gspread.authorize(creds)
        print("gspread authorized.")
        
        print(f"Attempting to open sheet by key: {sheet_id}...")
        sheet = client.open_by_key(sheet_id)
        print("Successfully opened the spreadsheet!")
        print(f"Title: {sheet.title}")
        
        # Check if worksheets exist
        worksheets = [ws.title for ws in sheet.worksheets()]
        print(f"Worksheets: {worksheets}")
        
    except Exception as e:
        print("\n--- ERROR ENCOUNTERED ---")
        traceback.print_exc()
        print("-------------------------\n")
        
        # Provide specific helpful hints based on the error
        err_msg = str(e)
        if "SpreadsheetNotFound" in err_msg or "APIError" in err_msg:
            print("Hint: This usually means either:")
            print("1. The Google Sheet ID is incorrect.")
            print("2. The Google Sheet has not been shared with the service account email.")
            print("   Please share your Google Sheet with: kalshi-bot@kalshi-trading-499010.iam.gserviceaccount.com")
            print("   and give it 'Editor' access.")

if __name__ == "__main__":
    test_sheet()
