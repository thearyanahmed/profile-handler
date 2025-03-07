import gspread
import requests
import json
import sys
import os
from requests.auth import HTTPBasicAuth
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PAPERTRAIL_ENDPOINT = os.getenv("PAPERTRAIL_ENDPOINT")
PAPERTRAIL_TOKEN = os.getenv("PAPERTRAIL_TOKEN")
STRAPI_PROFILE_ENDPOINT = os.getenv("STRAPI_PROFILE_ENDPOINT")
API_TOKEN = os.getenv("API_TOKEN")
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}"
}

def log_to_papertrail(message):
    """Send logs to Papertrail in the correct format."""
    try:
        headers = {
            "Content-Type": "application/json",
        }
        data = {
            "message": message
        }
        response = requests.post(
            PAPERTRAIL_ENDPOINT,
            headers=headers,
            auth=HTTPBasicAuth("", PAPERTRAIL_TOKEN),
            data=json.dumps(data)
        )
        # Check if the request was successful
        if response.status_code == 200:
            print("Log sent successfully!")
        else:
            print(f"Failed to send log: {response.status_code} - {response.text}")
    except Exception as e:
        sys.stderr.write(f"Error sending log to Papertrail: {e}\n")

def authenticate_drive_api():
    """Authenticate and return the Drive API client"""
    credentials_dict = {
        "type": "service_account",
        "project_id": os.getenv("PROJECT_ID"),
        "private_key_id": os.getenv("PRIVATE_KEY_ID"),
        "private_key": os.getenv("PRIVATE_KEY").replace('\\n', '\n'),
        "client_email": os.getenv("CLIENT_EMAIL"),
        "client_id": os.getenv("CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{os.getenv('GOOGLE_CLIENT_EMAIL')}"
    }

    creds = Credentials.from_service_account_info(credentials_dict, scopes=["https://www.googleapis.com/auth/drive"])
    return build('drive', 'v3', credentials=creds)

def directory_check(drive_service, folder_id, incoming_data):
    """Check for an existing directory"""
    query = f"'{folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get('files', [])
    
    customer_name = str(incoming_data["entry"]["customer"]["customer_name"]).lower()
    for folder in folders:
        if 'MANUAL PROCES' in folder['name'].upper() and customer_name in folder['name'].lower():
            return folder['id']

    return False

def retrieve_sheet(drive_service, file_id):
    return drive_service.files().copy(fileId=file_id).execute()

def create_sheets(drive_service, folder_id, incoming_data, directory_id):
    """Create Google Sheets from templates"""
    templates = {
        "datastudio": ("1wRRr5N86nNoMypxqLOJVtm2DVsQ4EgmN8E5gxErA4CA", "{customer} - {profile} - DATASTUDIO"),
        "follow-up": ("15bRMSzfdkIAJjR2MmQhyZBTNg0SuTXb_xg3HItEaM-8", "Follow-up LeadBlocks X {customer}"),
        "ghost": ("1IyrC2DskQvzQZF62-C0r-hevX6pbWcsKmOq1HhRNcrk", "Ghost sheet - {profile}"),
        "log": ("1oofomhKNQLjFpeg2u35AR-pdqgMRGT2KFszGb0XPgWg", "{customer} log - {profile}")
    }

    query = f"'{directory_id}' in parents"
    files = drive_service.files().list(q=query, fields="files(id, name)").execute().get('files', [])

    customer = incoming_data["entry"]["customer"]["customer_name"]
    profile = incoming_data["entry"]["profile_name"]

    for key, (file_id, name_template) in templates.items():
        new_name = name_template.format(customer=customer, profile=profile).lower()
        exists = any(new_name in file['name'].lower() for file in files)

        if not exists:
            new_file = retrieve_sheet(drive_service, file_id)
            new_file_id = new_file['id']
            drive_service.files().update(fileId=new_file_id, body={'name': name_template.format(customer=customer, profile=profile)}).execute()
            drive_service.files().update(fileId=new_file_id, addParents=directory_id, removeParents=folder_id).execute()
            print(f"{key.capitalize()} sheet created: {name_template.format(customer=customer, profile=profile)}")

def create_directory_and_sheets(drive_service, folder_id, incoming_data):
    """Create a new directory and sheets"""
    customer_name = incoming_data['entry']['customer']['customer_name']
    directory_name = f"MANUAL PROCES - {customer_name}".upper()
    folder_metadata = {
        'name': directory_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [folder_id]
    }
    folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
    new_folder_id = folder['id']
    create_sheets(drive_service, folder_id, incoming_data, new_folder_id)

def create_content(incoming_data):
    """Main function to create Google Drive content"""
    drive_folder_id = '1rieJHS6wgfgdPKLglgNkwEs7pRvX3lKm'
    drive_service = authenticate_drive_api()

    directory_exists = directory_check(drive_service, drive_folder_id, incoming_data)
    
    if directory_exists:
        create_sheets(drive_service, drive_folder_id, incoming_data, directory_exists)
    else:
        create_directory_and_sheets(drive_service, drive_folder_id, incoming_data)



def main(incoming_data):
    print('test')
    """Handle webhook requests from Strapi"""
    try:
        print(f"Received webhook data: {incoming_data}")

        if not incoming_data:
            return json.dumps({"error": "No data received"}), 400

        log_to_papertrail(f"Webhook received for handler.py: {incoming_data}")

        if incoming_data.get("model") == "profile":
            create_content(incoming_data)
            return json.dumps({"message": "Sheets created successfully"}), 200

        return json.dumps({"message": "Unhandled event type"}), 200

    except Exception as e:
        error_message = f"Error processing webhook: {str(e)}"
        print(error_message)
        log_to_papertrail(f"Error message for handler.py: {error_message}")  # Log the error to Papertrail or other logging services
        return json.dumps({"error": "Internal server error", "details": str(e)}), 500


# if __name__ == "__main__":
#     test_data = {
#         "model": "profile",
#         "entry": {
#             "customer": {"customer_name": "TestCustomer"},
#             "profile_name": "TestProfile"
#         }
#     }
#     result, status_code = main(test_data)
#     print(f"Result: {result}, Status: {status_code}")