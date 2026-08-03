#!/bin/bash

rclone copy ./all_device_stats.csv nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/
rclone copy ./dataset_stats.csv nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/
rclone copy ./image_mapping.csv nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/
rclone copy ./device_status.csv nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/
rclone copy ./recording_matrix.csv nirds3:bencretois-ns8129k-proj-tabmon/data/preprocessed/