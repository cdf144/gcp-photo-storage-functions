import datetime
import os
from http import HTTPStatus
from typing import Any, Tuple

import functions_framework
from firebase_admin import auth
from flask import Request, Response, jsonify, make_response
from flask_cors import CORS
from google.cloud import firestore, vision
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import utils
from config import (
    ALLOWED_IMAGE_TYPES,
    BUCKET_SIGNED_URL_EXPIRATION,
    FIRESTORE_COLLECTION,
    firestore_client,
    storage_client,
    vision_client,
)

cors = CORS(resources={r"/.*": {"origins": "*"}})


def cors_middleware(func):
    def wrapper(request: Request) -> Response | Tuple[str, int]:
        if request.method == "OPTIONS":
            response = make_response(
                jsonify({"status": "success", "message": "Preflight request handled"}),
                200,
            )
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, DELETE, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type"
            )
            response.headers["Access-Control-Max-Age"] = "86400"
            return response

        result = func(request)

        # Ensure CORS headers are set for all responses
        if isinstance(result, tuple) and len(result) == 2:
            response, status = result
            if isinstance(response, str):
                response = jsonify({"message": response})
            elif not isinstance(response, Response):
                response = make_response(jsonify(response))
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response, status
        elif isinstance(result, (str, dict, list)):
            response = make_response(jsonify(result))
            response.headers["Access-Control-Allow-Origin"] = "*"
            return response, 200
        elif isinstance(result, Response):
            result.headers["Access-Control-Allow-Origin"] = "*"
            return result

        return result

    return wrapper


@cors_middleware
@functions_framework.http
def upload_image(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to receive an image via POST request
    (multipart/form-data) and upload it to Cloud Storage.

    Expects a file field named 'image' in the request.
    The target bucket name is specified in the BUCKET_NAME environment variable.
    """
    if request.method != "POST":
        return (
            "This endpoint only accepts POST requests.",
            HTTPStatus.METHOD_NOT_ALLOWED,
        )

    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Error: BUCKET_NAME environment variable is not set.")
        return ("Server configuration error.", HTTPStatus.INTERNAL_SERVER_ERROR)

    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return "Missing or invalid Authorization header", HTTPStatus.UNAUTHORIZED

    id_token = auth_header.split("Bearer ")[1]

    try:
        decoded_token: dict[str, Any] = auth.verify_id_token(id_token)
        user_id: str = decoded_token["uid"]
    except Exception as e:
        print(f"Token verification failed: {e}")
        return "Unauthorized", HTTPStatus.UNAUTHORIZED

    image_file: FileStorage | None = request.files.get("image")

    if not image_file:
        return (
            "Bad Request: Missing 'image' field in the request.",
            HTTPStatus.BAD_REQUEST,
        )

    if image_file.mimetype not in ALLOWED_IMAGE_TYPES:
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
        blob.metadata = {"userId": user_id}
        blob.upload_from_file(image_file.stream, content_type=image_file.mimetype)
        blob.patch()

        doc_id = destination_blob_name.replace("/", "_")
        metadata = {
            "bucket": bucket_name,
            "fileName": destination_blob_name,
            "imageUri": f"gs://{bucket_name}/{destination_blob_name}",
            "userId": user_id,
            "uploadedTimestamp": firestore.SERVER_TIMESTAMP,
        }

        firestore_client.collection(FIRESTORE_COLLECTION).document(doc_id).set(metadata)

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


@cors_middleware
@functions_framework.http
def delete_image(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to delete an image from Cloud Storage.
    Requires Firebase Authentication and expects a 'fileName' query parameter
    (full path of the object in the bucket, e.g., 'uploads/your_image.jpg').

    The image metadata in Firestore will be deleted by a separate Cloud Event function
    triggered by the GCS object deletion.
    """
    if request.method != "DELETE":
        return (
            "This endpoint only accepts DELETE requests.",
            HTTPStatus.METHOD_NOT_ALLOWED,
        )

    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Error: BUCKET_NAME environment variable is not set.")
        return ("Server configuration error.", HTTPStatus.INTERNAL_SERVER_ERROR)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return "Missing or invalid Authorization header", HTTPStatus.UNAUTHORIZED
    id_token = auth_header.split("Bearer ")[1]

    try:
        decoded_token: dict[str, Any] = auth.verify_id_token(id_token)
        user_id: str = decoded_token["uid"]
    except Exception as e:
        print(f"Token verification failed: {e}")
        return "Unauthorized", HTTPStatus.UNAUTHORIZED

    file_name: str | None = request.args.get("fileName")
    if not file_name:
        return (
            "Bad Request: Missing 'fileName' query parameter.",
            HTTPStatus.BAD_REQUEST,
        )

    try:
        doc_id = file_name.replace("/", "_")
        doc_ref: firestore.DocumentReference = firestore_client.collection(
            FIRESTORE_COLLECTION
        ).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            return (
                f"File metadata for '{file_name}' not found.",
                HTTPStatus.NOT_FOUND,
            )

        metadata = doc.to_dict()
        if metadata.get("userId") != user_id:
            return "Permission denied to delete this file.", HTTPStatus.FORBIDDEN

        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(file_name)
        if not blob.exists():
            return (
                f"Image '{file_name}' not found in storage or already deleted.",
                HTTPStatus.OK,
            )

        blob.delete()
        return (
            f"Image '{file_name}' deleted successfully.",
            HTTPStatus.OK,
        )

    except Exception as e:
        print(f"An error occurred during image deletion for {file_name}: {e}")
        return (
            "An error occurred during image deletion.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


@cors_middleware
@functions_framework.http
def get_images_metadata(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to retrieve image metadata and labels from Firestore
    and generate signed URLs for corresponding images in Cloud Storage.

    Returns a JSON response containing a list of image metadata objects.
    """
    id_token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not id_token:
        return "Missing Authorization header", HTTPStatus.UNAUTHORIZED

    try:
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token["uid"]
    except Exception as e:
        print(f"Token verification failed: {e}")
        return "Unauthorized", HTTPStatus.UNAUTHORIZED

    images_data: list[dict[str, Any]] = []

    try:
        docs = (
            firestore_client.collection(FIRESTORE_COLLECTION)
            .where("userId", "==", user_id)
            .stream()
        )

        for doc in docs:
            metadata = doc.to_dict()
            doc_id = doc.id

            file_name: str | None = metadata.get("fileName")
            bucket_name: str | None = metadata.get("bucket")
            labels: list[dict[str, str | float]] = metadata.get("labels", [])

            if not file_name or not bucket_name:
                print(
                    f"Warning: Skipping document '{doc_id}' due to missing fileName or bucket."
                )
                continue

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

        return jsonify(images_data)

    except Exception as e:
        print(f"An error occurred while retrieving metadata: {e}")
        return (
            "An internal error occurred while fetching image data.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


@cors_middleware
@functions_framework.http
def get_image_metadata(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to retrieve metadata for a specific image
    from Firestore based on the provided 'doc_id' query parameter.

    Expects a query parameter 'docId' in the request URL.

    Returns a JSON response containing the image metadata.
    """
    doc_id: str | None = request.args.get("docId")

    if not doc_id:
        return (
            "Bad Request: Missing 'docId' query parameter.",
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

        return jsonify(metadata)

    except Exception as e:
        print(f"An error occurred while retrieving metadata for {doc_id}: {e}")
        return (
            "An internal error occurred while fetching image data.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


@cors_middleware
@functions_framework.http
def ocr_image(request: Request) -> Response | Tuple[str, int]:
    """
    HTTP Cloud Run function to perform OCR on an image file
    and return the extracted text.

    Expects a file field named 'image' in the request.
    """
    if request.method != "POST":
        return (
            "This endpoint only accepts POST requests.",
            HTTPStatus.METHOD_NOT_ALLOWED,
        )

    image_file: FileStorage | None = request.files.get("image")
    if not image_file:
        return (
            "Bad Request: Missing 'image' field in the request.",
            HTTPStatus.BAD_REQUEST,
        )

    if image_file.mimetype not in ALLOWED_IMAGE_TYPES:
        return (
            f"Unsupported file type {image_file.mimetype}",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    try:
        image = vision.Image(content=image_file.read())
        result = vision_client.text_detection(image=image)
        annotations = result.text_annotations[1:]
        boxes: list[dict] = []
        for annotation in annotations:
            vertices = annotation.bounding_poly.vertices
            x = vertices[0].x or 0
            y = vertices[0].y or 0
            width = (vertices[1].x or 0) - x
            height = (vertices[2].y or 0) - y

            boxes.append(
                {
                    "text": annotation.description,
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
            )

        return jsonify({"boxes": boxes})

    except Exception as e:
        print(f"An error occurred during ocr image: {e}")
        return (
            "An error occurred during file upload.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
