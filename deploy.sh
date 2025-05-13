#! /bin/bash

gcloud run deploy upload-image-function \
  --source . \
  --region asia-southeast1 \
  --function upload_image \
  --base-image python313 \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated

gcloud functions deploy process-image-for-labels \
  --runtime python313 \
  --trigger-bucket photo-cloud-storage-bucket-1 \
  --entry-point process_image_for_labels \
  --region asia-southeast1