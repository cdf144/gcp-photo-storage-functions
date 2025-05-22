#!/bin/bash

gcloud run deploy upload-image-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function upload_image \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated \
  --timeout 180s

gcloud run deploy delete-image-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function delete_image \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated \
  --timeout 180s

gcloud run deploy get-images-metadata-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function get_images_metadata \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated \
  --timeout 180s

gcloud run deploy get-image-metadata-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function get_image_metadata \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated \
  --timeout 180s

gcloud run deploy ocr-image-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function ocr_image \
  --allow-unauthenticated \
  --timeout 180s

# --trigger-bucket event is google.cloud.storage.object.v1.finalized
gcloud functions deploy process-image-upload-labels \
  --runtime python313 \
  --region asia-southeast1 \
  --entry-point process_image_upload_labels \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --trigger-bucket photo-cloud-storage-bucket-1 \
  --timeout 180s

gcloud functions deploy process-image-deletion \
  --runtime python313 \
  --region asia-southeast1 \
  --entry-point process_image_deletion \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --trigger-event google.cloud.storage.object.v1.deleted \
  --trigger-resource photo-cloud-storage-bucket-1 \
  --timeout 180s
