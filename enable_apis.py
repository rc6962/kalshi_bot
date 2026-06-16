import os
import sys
import json

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

def check_and_enable_apis():
    manual_load_dotenv()
    creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "google_credentials.json")
    
    if not os.path.exists(creds_path):
        print(f"Error: Credentials file not found at '{creds_path}'")
        return
        
    try:
        with open(creds_path, "r") as f:
            creds_data = json.load(f)
            project_id = creds_data.get("project_id")
    except Exception as e:
        print(f"Error reading credentials file: {e}")
        return
        
    if not project_id:
        print("Error: Could not find 'project_id' in your credentials file.")
        return
        
    print(f"Target Google Cloud Project: {project_id}\n")
    
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests
        import requests
    except ImportError:
        print("Required libraries missing. Installing packages...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "google-auth"])
        from google.oauth2 import service_account
        import google.auth.transport.requests
        import requests

    try:
        # Load credentials with the broad cloud-platform scope
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)
        
        # Refresh the credentials to get an access token
        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
        access_token = creds.token
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        apis_to_check = ["sheets.googleapis.com", "drive.googleapis.com"]
        
        for api in apis_to_check:
            print(f"Checking status of {api}...")
            url = f"https://serviceusage.googleapis.com/v1/projects/{project_id}/services/{api}"
            
            resp = requests.get(url, headers=headers)
            if resp.status_code == 403:
                print(f"Permission Denied (403) checking {api}.")
                print("Your service account does not have permission to check/enable APIs via the Service Usage API.")
                print("Please enable the API manually in the Google Cloud Console:")
                print(f"  https://console.cloud.google.com/apis/library/{api}?project={project_id}")
                print()
                continue
            elif resp.status_code != 200:
                print(f"Failed to check status (HTTP {resp.status_code}): {resp.text}\n")
                continue
                
            data = resp.json()
            state = data.get("state")
            print(f"Current state: {state}")
            
            if state == "ENABLED":
                print(f"✅ {api} is already enabled!")
            else:
                print(f"🔄 {api} is NOT enabled. Attempting to enable it...")
                enable_url = f"https://serviceusage.googleapis.com/v1/projects/{project_id}/services/{api}:enable"
                enable_resp = requests.post(enable_url, headers=headers)
                
                if enable_resp.status_code == 200:
                    print(f"✅ Successfully enabled {api}!")
                else:
                    print(f"❌ Failed to enable {api} (HTTP {enable_resp.status_code}): {enable_resp.text}")
                    print("Please enable it manually in Google Cloud Console:")
                    print(f"  https://console.cloud.google.com/apis/library/{api}?project={project_id}")
            print()
            
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_and_enable_apis()
