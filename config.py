import datetime
import os

import firebase_admin
from google.cloud import firestore, storage, vision

FIRESTORE_COLLECTION = "image_metadata"
BUCKET_SIGNED_URL_EXPIRATION = datetime.timedelta(minutes=15)
# Info: https://cloud.google.com/vision/docs/supported-files
ALLOWED_IMAGE_TYPES = [
    "image/jpeg",
    "image/png",
    "image/bmp",
    "image/webp",
    "image/x-icon",  # .ico
    "image/tiff",
]

# Firebase Admin SDK initialization, done automatically when this `config.py` module is imported
firebase_admin_app = firebase_admin.initialize_app()

storage_client = storage.Client()
vision_client = vision.ImageAnnotatorClient()
firestore_client = firestore.Client(
    database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
)
