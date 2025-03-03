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


## Run the program locally

There is no need to use any login (`gcloud auth login`, `gcloud config set project`) as everything is handled by the script.

### Installing the dependancies

```
python -m venv .venv
pip install -r requirements.txt
.venv/bin/activate
```

### Create a `.env` file

Document all the paths in an .env file.

Use the [template](https://github.com/NINAnor/WP1-data-transfer/blob/main/.env)

### Download the data

```
python main_remote.py
```

## Acknowledgement

This program has been conceived by [Benjamin Cretois](https://bencretois.github.io/) as part of the [TABMON](https://www.nina.no/english/TABMON) project.

