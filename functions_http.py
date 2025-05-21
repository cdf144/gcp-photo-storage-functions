import datetime
import os
from http import HTTPStatus
from typing import Any, Tuple, Union

import firebase_admin
from firebase_admin import auth, credentials
from google.cloud import firestore, vision
from google.cloud.vision_v1 import types

import functions_framework
from flask import Request, Response, jsonify
from google.cloud import firestore  # For type hinting and .exists attribute
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import utils
from config import (
    BUCKET_SIGNED_URL_EXPIRATION,
    FIRESTORE_COLLECTION,
    firestore_client,
    storage_client,
)

# Initialize Firebase Admin SDK if not already initialized
if not firebase_admin._apps:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)

def perform_ocr(bucket_name: str, file_name: str) -> str:
    try:
        client = vision.ImageAnnotatorClient()
        image = types.Image()
        image.source.image_uri = f"gs://{bucket_name}/{file_name}"
        
        response = client.text_detection(image=image)
        texts = response.text_annotations
        
        if texts:
            return texts[0].description  # Lấy văn bản đầy đủ từ kết quả OCR
        return ""
    except Exception as e:
        print(f"Lỗi khi thực hiện OCR trên {file_name}: {e}")
        return ""
    
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

    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return "Missing or invalid Authorization header", HTTPStatus.UNAUTHORIZED

    id_token = auth_header.split("Bearer ")[1]

    try:
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token["uid"]
    except Exception as e:
        print(f"Token verification failed: {e}")
        return "Unauthorized", HTTPStatus.UNAUTHORIZED
    
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
        # Lưu Firestore metadata có userId
        ocr_text = perform_ocr(bucket_name, destination_blob_name)
        doc_id = destination_blob_name.replace("/", "_")
        metadata = {
            "bucket": bucket_name,
            "fileName": destination_blob_name,
            "imageUri": f"gs://{bucket_name}/{destination_blob_name}",
            "userId": user_id,
            "uploadedTimestamp": firestore.SERVER_TIMESTAMP,
            "ocrText": ocr_text,
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
        
@functions_framework.http
def update_image(request: Request) -> Response | Tuple[str, int]:
    """
    API để cập nhật ảnh hiện có trong Cloud Storage và Firestore.
    Yêu cầu doc_id và tệp image mới.
    """
    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Lỗi: Biến môi trường BUCKET_NAME chưa được thiết lập.")
        return ("Lỗi cấu hình server.", HTTPStatus.INTERNAL_SERVER_ERROR)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return "Thiếu hoặc sai tiêu đề Authorization", HTTPStatus.UNAUTHORIZED

    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token["uid"]
    except Exception as e:
        print(f"Xác minh token thất bại: {e}")
        return "Không được ủy quyền", HTTPStatus.UNAUTHORIZED

    doc_id: str | None = request.form.get("doc_id")
    image_file: Union[FileStorage, None] = request.files.get("image")

    if not doc_id or not image_file:
        return (
            "Yêu cầu không hợp lệ: Thiếu 'doc_id' hoặc 'image'.",
            HTTPStatus.BAD_REQUEST,
        )

    allowed_types = ["image/jpeg", "image/png", "image/gif"]
    if image_file.mimetype not in allowed_types:
        return (
            f"Loại tệp không được hỗ trợ {image_file.mimetype}",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
        )

    try:
        doc_ref = firestore_client.collection(FIRESTORE_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            return ("Tài liệu không tồn tại.", HTTPStatus.NOT_FOUND)

        metadata = doc.to_dict()
        if metadata.get("userId") != user_id:
            return ("Bạn không có quyền cập nhật ảnh này.", HTTPStatus.FORBIDDEN)

        # Xóa tệp cũ trong Cloud Storage
        old_file_name = metadata.get("fileName")
        if old_file_name:
            bucket = storage_client.get_bucket(bucket_name)
            old_blob = bucket.blob(old_file_name)
            if old_blob.exists():
                old_blob.delete()

        # Tải lên tệp mới
        filename = image_file.filename or "updated_file"
        safe_filename = secure_filename(filename)
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        destination_blob_name = f"uploads/{timestamp}_{safe_filename}"

        bucket = storage_client.get_bucket(bucket_name)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_file(image_file.stream, content_type=image_file.mimetype)

        # Thực hiện OCR
        ocr_text = perform_ocr(bucket_name, destination_blob_name)

        # Cập nhật metadata
        updated_metadata = {
            "bucket": bucket_name,
            "fileName": destination_blob_name,
            "imageUri": f"gs://{bucket_name}/{destination_blob_name}",
            "userId": user_id,
            "uploadedTimestamp": firestore.SERVER_TIMESTAMP,
            "ocrText": ocr_text,  # Thêm văn bản OCR
        }

        doc_ref.set(updated_metadata)

        return (
            f"Tệp '{filename}' được cập nhật thành công dưới tên '{destination_blob_name}'.",
            HTTPStatus.OK,
        )

    except Exception as e:
        print(f"Lỗi khi cập nhật ảnh: {e}")
        return (
            "Lỗi xảy ra khi cập nhật tệp.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

@functions_framework.http
def delete_image(request: Request) -> Response | Tuple[str, int]:
    """
    API để xóa ảnh từ Cloud Storage và Firestore.
    Yêu cầu doc_id.
    """
    bucket_name: str = os.environ.get("BUCKET_NAME", "")
    if not bucket_name:
        print("Lỗi: Biến môi trường BUCKET_NAME chưa được thiết lập.")
        return ("Lỗi cấu hình server.", HTTPStatus.INTERNAL_SERVER_ERROR)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return "Thiếu hoặc sai tiêu đề Authorization", HTTPStatus.UNAUTHORIZED

    id_token = auth_header.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token["uid"]
    except Exception as e:
        print(f"Xác minh token thất bại: {e}")
        return "Không được ủy quyền", HTTPStatus.UNAUTHORIZED

    doc_id: str | None = request.args.get("doc_id")
    if not doc_id:
        return (
            "Yêu cầu không hợp lệ: Thiếu 'doc_id'.",
            HTTPStatus.BAD_REQUEST,
        )

    try:
        doc_ref = firestore_client.collection(FIRESTORE_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            return ("Tài liệu không tồn tại.", HTTPStatus.NOT_FOUND)

        metadata = doc.to_dict()
        if metadata.get("userId") != user_id:
            return ("Bạn không có quyền xóa ảnh này.", HTTPStatus.FORBIDDEN)

        # Xóa tệp từ Cloud Storage
        file_name = metadata.get("fileName")
        if file_name:
            bucket = storage_client.get_bucket(bucket_name)
            blob = bucket.blob(file_name)
            if blob.exists():
                blob.delete()

        # Xóa tài liệu từ Firestore
        doc_ref.delete()

        return (
            f"Ảnh '{file_name}' đã được xóa thành công.",
            HTTPStatus.OK,
        )

    except Exception as e:
        print(f"Lỗi khi xóa ảnh: {e}")
        return (
            "Lỗi xảy ra khi xóa tệp.",
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )


@functions_framework.http
def get_images_metadata(_: Request) -> Response | Tuple[str, int]:
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
        docs = firestore_client.collection(FIRESTORE_COLLECTION).where("userId", "==", user_id).stream()

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
