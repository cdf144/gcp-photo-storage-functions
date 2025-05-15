import datetime

import google.auth.credentials
import google.auth.impersonated_credentials
import google.auth.transport.requests


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
