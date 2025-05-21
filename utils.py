import datetime

import google.auth.credentials
import google.auth.impersonated_credentials
import google.auth.transport.requests
from google.cloud import vision

from config import vision_client


def get_impersonated_credentials() -> google.auth.credentials.Credentials:
    """
    Get impersonated credentials from the default credentials for the Cloud Run
    environment (which should be where this function is running).

    The scopes are set to allow access to all Google Cloud services.

    Info: https://cloud.google.com/iam/docs/service-account-impersonation

    Returns:
        google.auth.credentials.Credentials: Impersonated credentials.
    """
    # Info: https://developers.google.com/identity/protocols/oauth2/scopes
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    credentials, _ = google.auth.default(scopes=scopes)
    if credentials.token is None:
        credentials.refresh(google.auth.transport.requests.Request())

    return google.auth.impersonated_credentials.Credentials(
        source_credentials=credentials,
        target_principal=credentials.service_account_email,
        target_scopes=scopes,
        lifetime=datetime.timedelta(seconds=3600),
        delegates=[credentials.service_account_email],
    )


def perform_ocr(bucket_name: str, file_name: str) -> str:
    image_uri = f"gs://{bucket_name}/{file_name}"

    image = vision.Image(source=vision.ImageSource(image_uri=image_uri))
    text = ""

    try:
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations

        if texts:
            text = texts[0].description
        else:
            print("No text found in the image.")
    except Exception as e:
        print(f"An error occurred during OCR processing: {e}")

    return text
