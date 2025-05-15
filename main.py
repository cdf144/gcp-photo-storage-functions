import datetime
import os
from http import HTTPStatus
from typing import Any, Tuple, Union

import functions_framework
from flask import Request, Response, jsonify
from google.cloud import firestore, storage, vision
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import utils

FIRESTORE_COLLECTION = "image_metadata"
BUCKET_SIGNED_URL_EXPIRATION = datetime.timedelta(minutes=15)

storage_client = storage.Client()
vision_client = vision.ImageAnnotatorClient()
firestore_client = firestore.Client(
    database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
)


@functions_framework.http
def upload_image(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to receive an image via POST request
    (multipart/form-data) and upload it to Cloud Storage.

    Expects a file field named 'image' in the request.
    The target bucket name is specified in the BUCKET_NAME environment variable.
    """

    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Error: BUCKET_NAME environment variable is not set.")
        return ("Server configuration error.", HTTPStatus.INTERNAL_SERVER_ERROR)

    image_file: Union[FileStorage, None] = request.files.get("image")

    if not image_file:
        return (
            "Bad Request: Missing 'image' field in the request.",
            HTTPStatus.BAD_REQUEST,
        )

    allowed_types = ["image/jpeg", "image/png", "image/gif"]
    if image_file.mimetype not in allowed_types:
        return (
            f"Unsupported file type {image_file.mimetype}",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    filename = image_file.filename or "uploaded_file"
    safe_filename = secure_filename(filename)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    destination_blob_name = f"uploads/{timestamp}_{safe_filename}"

    try:
        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)

        blob.upload_from_file(image_file.stream, content_type=image_file.mimetype)

        return (
            f"File '{filename}' uploaded successfully as '{destination_blob_name}'.",
            HTTPStatus.CREATED,
        )

    except Exception as e:
        print(f"An error occurred during upload: {e}")
        return (
            "An error occurred during file upload.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


@functions_framework.http
def get_images_metadata(_: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to retrieve image metadata and labels from Firestore
    and generate signed URLs for corresponding images in Cloud Storage.

    Returns a JSON response containing a list of image metadata objects.
    """
    images_data: list[dict[str, Any]] = []

    try:
        docs = firestore_client.collection(FIRESTORE_COLLECTION).stream()

        for doc in docs:
            metadata = doc.to_dict()
            doc_id = doc.id

            file_name: str | None = metadata.get("fileName")
            bucket_name: str | None = metadata.get("bucket")
            labels: list[dict[str, Union[str, float]]] = metadata.get("labels", [])

            if not file_name or not bucket_name:
                print(
                    f"Warning: Skipping document '{doc_id}' due to missing fileName or bucket."
                )
                continue

            # Signed URLs give time-limited access to files in Cloud Storage without making them public.
            # Info: https://cloud.google.com/storage/docs/access-control/signed-urls
            signed_url: str | None = None
            try:
                bucket = storage_client.get_bucket(bucket_name)
                blob = bucket.blob(file_name)

                credentials = utils.get_impersonated_credentials()
                signed_url = blob.generate_signed_url(
                    expiration=BUCKET_SIGNED_URL_EXPIRATION,
                    credentials=credentials,
                    version="v4",
                )

            except Exception as e:
                print(f"Error generating signed URL for {file_name}: {e}")

            images_data.append(
                {
                    "id": doc_id,
                    "fileName": file_name,
                    "labels": labels,
                    "signedUrl": signed_url,
                    "processedTimestamp": metadata.get("processedTimestamp"),
                    "timeCreated": metadata.get("timeCreated"),
                    "size": metadata.get("size"),
                }
            )

        return jsonify(images_data), HTTPStatus.OK

    except Exception as e:
        print(f"An error occurred while retrieving metadata: {e}")
        return jsonify(
            {"error": "An internal error occurred while fetching image data."}
        ), HTTPStatus.INTERNAL_SERVER_ERROR


@functions_framework.http
def get_image_metadata(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to retrieve metadata for a specific image
    from Firestore based on the provided 'doc_id' query parameter.

    Expects a query parameter 'doc_id' in the request URL.

    Returns a JSON response containing the image metadata.
    """
    doc_id: str | None = request.args.get("doc_id")

    if not doc_id:
        return (
            "Bad Request: Missing 'doc_id' query parameter.",
            HTTPStatus.BAD_REQUEST,
        )

    try:
        doc_ref: firestore.DocumentReference = firestore_client.collection(
            FIRESTORE_COLLECTION
        ).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            return ("Document does not exist.", HTTPStatus.NOT_FOUND)

        metadata = doc.to_dict()

        # Generate signed URL for the image
        file_name: str | None = metadata.get("fileName")
        bucket_name: str | None = metadata.get("bucket")
        if not file_name or not bucket_name:
            print(f"Error: Document '{doc_id}' is missing fileName or bucket.")
            return (
                "The document stored on the server has incomplete metadata.",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        signed_url: str | None = None
        try:
            bucket = storage_client.get_bucket(bucket_name)
            blob = bucket.blob(file_name)

            credentials = utils.get_impersonated_credentials()
            signed_url = blob.generate_signed_url(
                expiration=BUCKET_SIGNED_URL_EXPIRATION,
                credentials=credentials,
                version="v4",
            )

        except Exception as e:
            print(f"Error generating signed URL for {file_name}: {e}")

        finally:
            metadata["signedUrl"] = signed_url

        metadata["id"] = doc_id

        return jsonify(metadata), HTTPStatus.OK

    except Exception as e:
        print(f"An error occurred while retrieving metadata for {doc_id}: {e}")
        return (
            "An internal error occurred while fetching image data.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


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


@functions_framework.cloud_event
def delete_image_metadata(cloud_event: functions_framework.CloudEvent) -> None:
    """
    Cloud Storage event-triggered Cloud Function that deletes the corresponding
    Firestore document when an image is deleted from the bucket.

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

    print(
        f"Event ID: {event_id}, Event type: {event_type}, Bucket: {bucket_name}, File: {file_name}, Metageneration: {metageneration}"
    )

    if not bucket_name or not file_name:
        print("Error: Missing bucket name or file name in event data for deletion.")
        return

    if file_name.endswith("/"):
        print(f"Skipping directory deletion event for '{file_name}'")
        return

    try:
        doc_id = file_name.replace("/", "_")
        doc_ref: firestore.DocumentReference = firestore_client.collection(
            FIRESTORE_COLLECTION
        ).document(doc_id)
        doc_ref.delete()

    except firestore.exceptions.NotFound:
        print(
            f"Warning: Firestore document '{doc_id}' not found for deleted file '{file_name}'. It may have already been deleted or never existed."
        )
    except Exception as e:
        print(
            f"An error occurred while deleting Firestore document for {file_name}: {e}"
        )
