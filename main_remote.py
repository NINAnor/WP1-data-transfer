import os
from google.cloud import storage
from dotenv import load_dotenv
import paramiko
from scp import SCPClient
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

logging.basicConfig(
    filename="data_transfer.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

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

def create_remote_directory(ssh_client, remote_path):
    """
    Creates a remote directory if it does not exist.

    Args:
    - ssh_client: An active paramiko SSH client.
    - remote_path: The remote directory to create.
    """
    stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {remote_path}")
    stdout.channel.recv_exit_status()  # Wait for the command to complete
    logging.info(f"Ensured remote directory exists: {remote_path}")

@retry(
    stop=stop_after_attempt(3),  # Retry up to 3 times
    wait=wait_exponential(multiplier=2, min=1, max=10),  # Exponential backoff
    retry=retry_if_exception_type(Exception),  # Retry on any exception
)
def transfer_file_with_retry(ssh_client, local_file_path, remote_file_path):
    """
    Transfers a file to a remote server with retry logic.
    """
    with SCPClient(ssh_client.get_transport()) as scp:
        scp.put(local_file_path, remote_file_path)
        logging.info(f"Transferred {local_file_path} to {remote_file_path}")

@retry(
    stop=stop_after_attempt(3),  # Retry up to 3 times
    wait=wait_exponential(multiplier=2, min=1, max=10),  # Exponential backoff
    retry=retry_if_exception_type(Exception),  # Retry on any exception
)
def download_blob_with_retry(blob, local_file_path):
    """
    Downloads a blob from GCS to a local file with retry logic.
    """
    blob.download_to_filename(local_file_path)
    logging.info(f"Downloaded {blob.name} to {local_file_path}")

def transfer_single_blob(blob, remote_host, remote_user, remote_path, password):
    """
    Handles the downloading and transferring of a single blob with an independent SSH connection.
    Deletes temporary files after successful transfer to prevent /tmp from filling up.
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    local_file_path = None  # Initialize to ensure cleanup even in case of exceptions

    try:
        # Establish the SSH connection
        ssh.connect(
            hostname=remote_host,
            username=remote_user,
            password=password
        )

        # Set keep-alive to send packets every 30 seconds
        ssh.get_transport().set_keepalive(30)

        # Construct paths
        relative_path = blob.name
        local_file_path = os.path.join("/tmp", relative_path.lstrip('/'))
        remote_file_path = os.path.join(remote_path.rstrip('/'), relative_path.lstrip('/'))
        remote_directory = os.path.dirname(remote_file_path)

        # Ensure local directory exists
        os.makedirs(os.path.dirname(local_file_path), exist_ok=True)

        # Download the blob with retry
        download_blob_with_retry(blob, local_file_path)

        # Ensure the remote directory exists
        create_remote_directory(ssh, remote_directory)

        # Transfer the file with retry
        transfer_file_with_retry(ssh, local_file_path, remote_file_path)

        # Optionally delete the blob from GCS
        blob.delete()
        logging.info(f"Deleted {blob.name} from GCS.")

        # Delete the local temporary file
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
            logging.info(f"Deleted temporary file: {local_file_path}")

    except Exception as e:
        logging.error(f"Failed to process {blob.name}: {e}")
    finally:
        # Cleanup in case of failure
        if local_file_path and os.path.exists(local_file_path):
            os.remove(local_file_path)
            logging.info(f"Cleaned up temporary file after failure: {local_file_path}")
        ssh.close()


def transfer_and_delete_data_parallel(storage_client, bucket_name, source_folder, remote_host, remote_user, remote_path, password, max_workers=4):
    """
    Uses parallel processing to transfer files to a remote server.
    """
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=source_folder)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(transfer_single_blob, blob, remote_host, remote_user, remote_path, password)
            for blob in blobs if not blob.name.endswith('/')  # Skip directories
        ]

        for future in as_completed(futures):
            try:
                future.result()  # Raise exceptions if any occurred
            except Exception as e:
                logging.error(f"Error in parallel task: {e}")

    logging.info("Data transfer to remote server completed in parallel.")


if __name__ == "__main__":
    # Initialize the Google Cloud Storage client
    gcs_client = init_storage_client()

    # Environment variables for configuration
    bucket_name = os.getenv('SOURCE_BUCKET_NAME')
    source_folder = os.getenv('SOURCE_FOLDER_PREFIX')
    remote_host = os.getenv('REMOTE_HOST')
    remote_user = os.getenv('REMOTE_USER')
    remote_path = os.getenv('REMOTE_PATH')
    password = os.getenv('REMOTE_PASSWORD')

    # Perform parallel file transfer
    logging.info("Starting parallel data transfer...")
    transfer_and_delete_data_parallel(
        gcs_client, bucket_name, source_folder,
        remote_host, remote_user, remote_path, password,
        max_workers=18
    )