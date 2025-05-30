import datetime
from typing import Any, Union

import functions_framework
from google.cloud import firestore, vision
from google.cloud.vision import AnnotateImageResponse

from config import FIRESTORE_COLLECTION, firestore_client, vision_client


@functions_framework.cloud_event
def process_image_upload_labels(cloud_event: functions_framework.CloudEvent) -> None:
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
    response = None

    try:
        response: AnnotateImageResponse = vision_client.label_detection(image=image)
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

        if response and response.error and response.error.message:
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
        if response and response.error and response.error.message
        else None,
    }

    try:
        if time_created:
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
        doc_ref.set(metadata, merge=True)

    except Exception as e:
        print(f"An error occurred while saving to Firestore: {e}")


@functions_framework.cloud_event
def process_image_deletion(cloud_event: functions_framework.CloudEvent) -> None:
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
