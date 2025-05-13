import os
from typing import Tuple, Union

import functions_framework
from flask import Request, Response
from google.cloud import storage, vision
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

storage_client = storage.Client()
vision_client = vision.ImageAnnotatorClient()


@functions_framework.http
def upload_image(request: Request) -> Union[Response, Tuple[str, int]]:
    """
    HTTP Cloud Run function to receive an image via POST request
    (multipart/form-data) and upload it to Google Cloud Storage.

    Expects a file field named 'image' in the request.
    The target bucket name is specified in the BUCKET_NAME environment variable.
    """

    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Error: BUCKET_NAME environment variable is not set.")
        return ("Configuration error: Bucket name not specified.", 500)

    image_file: Union[FileStorage, None] = request.files.get("image")

    if not image_file:
        print("Error: No 'image' file part in the request.")
        return ("Bad Request: Missing 'image' file part.", 400)

    allowed_types = ["image/jpeg", "image/png", "image/gif"]
    if image_file.mimetype not in allowed_types:
        return (f"Unsupported file type {image_file.mimetype}", 415)

    original_filename: str = image_file.filename or "uploaded_file"
    safe_filename: str = secure_filename(original_filename)

    destination_blob_name: str = f"uploads/{safe_filename}"

    print(
        f"Attempting to upload file '{original_filename}' to '{bucket_name}/{destination_blob_name}'"
    )

    try:
        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        blob.upload_from_file(image_file.stream, content_type=image_file.mimetype)

        print(f"File {destination_blob_name} uploaded successfully.")
        return (
            f"File '{original_filename}' uploaded successfully as '{destination_blob_name}'.",
            201,
        )

    except Exception as e:
        print(f"An error occurred during upload: {e}")
        return (f"An error occurred during file upload: {e}", 500)


@functions_framework.cloud_event
def process_image_for_labels(cloud_event: functions_framework.CloudEvent) -> None:
    """
    Cloud Storage event-triggered Cloud Function that processes a new image
    uploaded to a bucket using the Vision API for label detection.

    Args:
        cloud_event: The Cloud Event object containing event data.
    """
    data = cloud_event.data
    attributes = cloud_event.get_attributes()

    event_id = attributes.get("id")
    event_type = attributes.get("type")

    bucket_name = data["bucket"]
    file_name = data["name"]
    metageneration = data["metageneration"]
    time_created = data["timeCreated"]
    updated = data["updated"]

    print(f"Event ID: {event_id}, Event type: {event_type}")
    print(f"Bucket: {bucket_name}, File: {file_name}, Metageneration: {metageneration}")
    print(f"Created: {time_created}, Updated: {updated}")

    if file_name.endswith("/"):
        print(f"Skipping directory creation event for '{file_name}'")
        return

    print(f"Processing image: gs://{bucket_name}/{file_name}")

    image = vision.Image(
        source=vision.ImageSource(image_uri=f"gs://{bucket_name}/{file_name}")
    )

    try:
        print("Calling Vision API for label detection...")
        response = vision_client.label_detection(image=image)
        labels = response.label_annotations

        print("Labels:")
        if labels:
            for label in labels:
                print(
                    f"  {label.description} (score: {label.score:.2f}, topicality: {label.topicality:.2f})"
                )
        else:
            print("  No labels detected.")

        if response.error.message:
            print(f"Vision API error: {response.error.message}")

    except Exception as e:
        print(f"An error occurred during Vision API processing: {e}")

    print(f"Finished processing image: {file_name}")
