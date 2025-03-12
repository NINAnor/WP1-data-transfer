# TABMON data transfer

[TABMON](https://www.nina.no/english/TABMON) uses [BUGGs acoustic devices](https://www.bugg.xyz/) which by default send the data over to Google Cloud.

This repository documents our procedure to copy the data over Google Cloud to our NIRD server.

## Google Cloud setup

### Create service account with access to the Google Cloud bucket

First create a new service account:

```
IAM and Admin -> Service Account -> Create Service Account -> tabmon-data-upload
```

Then you need to change the permission for **Storage Object User** so that the service account can access the cloud bucket.

Then create a **Key** that will act as the `GOOGLE_APPLICATION_CREDENTIALS`, an environment variable that authentificate a user for accessing the Google Cloud Bucket (see `main.py/fetch_audio_data`).

Copy/Paste the `.json` file created from the key and copy it in a file called `key-file.json` that is to be stored both on your `local directory`.


## Setup rclone

### Setup the remote connections

Run

```
rclone config
```

and follow the prompts to create two remotes:

- Google Cloud Storage Remote
Create a new remote (e.g., name it gcs) and select Google Cloud Storage as the type. You will be asked for your project number and the path to your service account JSON file (the `key-file.json` created earlier).

- Remote Server via SFTP
Create another remote (e.g., name it remote) and select SFTP as the type. Provide your remote host, username, password (or key), and port.


### Start the transfer

Run

```
./move.sh
```

## Acknowledgments

This program has been conceived by [Benjamin Cretois](https://bencretois.github.io/) as part of the [TABMON](https://www.nina.no/english/TABMON) project.
