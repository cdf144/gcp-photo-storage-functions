import datetime
import os
from typing import Any, Tuple, Union

import functions_framework
from flask import Request, Response
from google.cloud import firestore, storage, vision
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

FIRESTORE_COLLECTION = "image_metadata"

storage_client = storage.Client()
vision_client = vision.ImageAnnotatorClient()
firestore_client = firestore.Client(
    database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
)


@functions_framework.http
def upload_image(request: Request) -> Union[Response, Tuple[str, int]]:
    """
    HTTP Cloud Run function to receive an image via POST request
    (multipart/form-data) and upload it to Cloud Storage.

    Expects a file field named 'image' in the request.
    The target bucket name is specified in the BUCKET_NAME environment variable.
    """

    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Error: BUCKET_NAME environment variable is not set.")
        return ("Server configuration error.", 500)

    image_file: Union[FileStorage, None] = request.files.get("image")

    if not image_file:
        return ("Bad Request: Missing 'image' field in the request.", 400)

    allowed_types = ["image/jpeg", "image/png", "image/gif"]
    if image_file.mimetype not in allowed_types:
        return (f"Unsupported file type {image_file.mimetype}", 415)

    filename: str = image_file.filename or "uploaded_file"
    safe_filename: str = secure_filename(filename)

    destination_blob_name: str = f"uploads/{safe_filename}"

    try:
        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        blob.upload_from_file(image_file.stream, content_type=image_file.mimetype)

        return (
            f"File '{filename}' uploaded successfully as '{destination_blob_name}'.",
            201,
        )

    except Exception as e:
        print(f"An error occurred during upload: {e}")
        return ("An error occurred during file upload.", 500)


@functions_framework.cloud_event
def process_image_for_labels(cloud_event: functions_framework.CloudEvent) -> None:
    """
    Cloud Storage event-triggered Cloud Function that processes a new image
    uploaded to a bucket using the Vision API for label detection and saves
    metadata and labels to Firestore.

    Args:
        cloud_event: The Cloud Event object containing event data.
    """
    data: dict[str, Any] = cloud_event.data
    attributes = cloud_event.get_attributes()

    event_id = attributes.get("id")
    event_type = attributes.get("type")

    bucket_name: str | None = data.get("bucket")
    file_name: str | None = data.get("name")
    metageneration: str | None = data.get("metageneration")
    time_created: str | None = data.get("timeCreated")
    updated: str | None = data.get("updated")
    size: str | None = data.get("size")

    print(
        f"Event ID: {event_id}, Event type: {event_type}, Bucket: {bucket_name}, File: {file_name}, Metageneration: {metageneration}"
    )

    if file_name.endswith("/"):
        print(f"Skipping directory creation event for '{file_name}'")
        return

    image_uri: str = f"gs://{bucket_name}/{file_name}"

    image = vision.Image(source=vision.ImageSource(image_uri=image_uri))
    labels_data: list[dict[str, Union[str, float]]] = []

    try:
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations

        if labels:
            sorted_labels = sorted(
                labels, key=lambda label: (-label.topicality, -label.score)
            )

            top_labels = sorted_labels[:4]

            for label in top_labels:
                labels_data.append(
                    {
                        "description": label.description,
                        "score": label.score,
                        "topicality": label.topicality,
                    }
                )

        if response.error.message:
            print(f"Vision API error: {response.error.message}")

    except Exception as e:
        print(f"An error occurred during Vision API processing: {e}")

    metadata: dict[str, Any] = {
        "bucket": bucket_name,
        "fileName": file_name,
        "imageUri": image_uri,
        "size": int(size) if size else 0,
        "labels": labels_data,
        "processedTimestamp": firestore.SERVER_TIMESTAMP,
        "visionApiError": response.error.message
        if response and response.error.message
        else None,
    }

    try:
        if time_created:
            # Cloud Storage timestamps are in RFC3339 format ('2025-05-13T10:30:00.123Z')
            metadata["timeCreated"] = datetime.datetime.fromisoformat(
                time_created.replace("Z", "+00:00")
            )
        if updated:
            metadata["updated"] = datetime.datetime.fromisoformat(
                updated.replace("Z", "+00:00")
            )

    except ValueError as e:
        print(f"Warning: Could not parse Cloud Storage timestamp string: {e}")

    try:
        doc_id = file_name.replace("/", "_")
        doc_ref: firestore.DocumentReference = firestore_client.collection(
            FIRESTORE_COLLECTION
        ).document(doc_id)
        doc_ref.set(metadata)

    except Exception as e:
        print(f"An error occurred while saving to Firestore: {e}")
