#!/bin/bash

# Log start time
echo "$(date): Starting preprocessing cron job..." >> /var/log/move.log

echo "$(date): Running move from Google Cloud to NIRD S3 storage..." >> /var/log/move.log
/app/move.sh >> /var/log/move.log 2>&1

echo "$(date): Running indexing of the data..." >> /var/log/indexing.log
/app/make_parquet.sh >> /var/log/indexing.log 2>&1


