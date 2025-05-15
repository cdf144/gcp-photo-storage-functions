import datetime
import os

from google.cloud import firestore, storage, vision

FIRESTORE_COLLECTION = "image_metadata"
BUCKET_SIGNED_URL_EXPIRATION = datetime.timedelta(minutes=15)

storage_client = storage.Client()
vision_client = vision.ImageAnnotatorClient()
firestore_client = firestore.Client(
    database=os.environ.get("FIRESTORE_DATABASE", "(default)"),
)
