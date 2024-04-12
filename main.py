import os
from google.cloud import storage
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def init_storage_client():
    """
    Initializes and returns a Google Cloud Storage client using the credentials
    and project ID specified via environment variables.
    """
    service_account_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    project_id = os.getenv('PROJECT_ID')
    return storage.Client.from_service_account_json(service_account_path, project=project_id)

def transfer_and_delete_data(storage_client, bucket_name, source_folder, destination_path):
    """
    Downloads the specified source folder (prefix) and all its contents from the
    bucket to the local destination, maintaining the original directory structure.
    It supports nested directories and replicates the full hierarchy locally.

    Args:
    - storage_client: The initialized Google Cloud Storage client.
    - bucket_name: The name of the source GCS bucket.
    - source_folder: The source folder (prefix) in the bucket to download.
                     Make sure it ends with '/' to include the folder itself.
    - destination_path: The local destination path where files and directories will be created.
    """
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=source_folder)

    for blob in blobs:

        # Skip any directories (any path that ends with '/')
        if blob.name.endswith('/'):
            continue

        # Construct the full local path for the blob's file including directories
        local_file_path = os.sep.join([destination_path, blob.name[len(source_folder):]])

        # Create local directories as needed
        os.makedirs(os.path.dirname(local_file_path), exist_ok=True)

        # Download the blob to the local file
        blob.download_to_filename(local_file_path)
        print(f"Downloaded {blob.name} to {local_file_path}")

        # Optionally, delete the blob after a successful download
        # blob.delete()
        # print(f"Deleted {blob.name} from GCS.")

    print("Data transfer completed.")


if __name__ == "__main__":
    # Initialize the Google Cloud Storage client
    gcs_client = init_storage_client()

    # Environment variables for configuration
    bucket_name = os.getenv('SOURCE_BUCKET_NAME')
    source_folder = os.getenv('SOURCE_FOLDER_PREFIX')
    destination_path = os.getenv('DESTINATION_PATH')

    # Perform the file transfer and optional deletion
    transfer_and_delete_data(gcs_client, bucket_name, source_folder, destination_path)
