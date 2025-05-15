#! /bin/bash

gcloud run deploy upload-image-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function upload_image \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated

# Default bucket trigger event is google.cloud.storage.object.v1.finalized
# Can be changed with --trigger-event
gcloud functions deploy process-image-for-labels \
  --runtime python313 \
  --region asia-southeast1 \
  --entry-point process_image_for_labels \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --trigger-bucket photo-cloud-storage-bucket-1

gcloud run deploy get-images-metadata-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function get_images_metadata \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated
