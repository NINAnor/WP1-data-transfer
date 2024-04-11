import subprocess
import os
import json
from google.cloud import storage
from google.oauth2 import service_account
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Function to fetch service account credentials from a GCS bucket
def fetch_service_account_key(bucket_name, blob_name):
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    key_file_content = blob.download_as_text()
    key_file_dict = json.loads(key_file_content)
    return key_file_dict

# Function to initialize GCS client with service account credentials
def init_storage_client(service_account_info):
    credentials = service_account.Credentials.from_service_account_info(service_account_info)
    storage_client = storage.Client(credentials=credentials)
    return storage_client

# Function to list and transfer files, then optionally delete them
def transfer_and_delete_data(bucket_name, source_folder, destination_server_path, service_account_info):
    # Initialize the GCS client with credentials
    storage_client = init_storage_client(service_account_info)
    bucket = storage_client.bucket(bucket_name)
    
    blobs = bucket.list_blobs(prefix=source_folder)
    for blob in blobs:
        # Skip newly uploaded files
        #if current_time - blob.time_created < processing_delay:
        #    continue

        # Create local directory structure mirroring that of the GCS bucket
        local_dir = os.path.join(destination_server_path, os.path.dirname(blob.name))
        if not os.path.exists(local_dir):
            os.makedirs(local_dir)

        # Modify the gsutil command to copy the entire directory structure
        gsutil_cp_command = f'gsutil -m cp -r gs://{bucket_name}/{blob.name} {local_dir}/'


        # Execute the command
        subprocess.run(gsutil_cp_command, shell=True, check=True)

        # After a successful transfer, delete the blob
        # Comment out the following line if you want to manually verify before deletion
        #blob.delete()

    print("Data transfer and optional deletion completed.")

# Main execution starts here
if __name__ == "__main__":
    # Fetch service account key file from environment variables
    service_account_info = fetch_service_account_key(
        os.getenv('SERVICE_ACCOUNT_KEY_BUCKET'), 
        os.getenv('SERVICE_ACCOUNT_KEY_BLOB_NAME')
    )

    # Perform the file transfer and deletion
    transfer_and_delete_data(
        os.getenv('SOURCE_BUCKET_NAME'), 
        os.getenv('SOURCE_FOLDER_PREFIX'), 
        os.getenv('DESTINATION_PATH'), 
        service_account_info
    )
