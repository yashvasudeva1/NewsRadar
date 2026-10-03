import base64
import json
import secrets
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from ..config import settings


class GmailService:
    """Google Gmail OAuth + sending service.

    Local development may use credentials.json. Vercel should use the
    GOOGLE_CLIENT_SECRET_JSON environment variable so no secret file is
    committed to the repository.
    """

    @staticmethod
    def _client_config() -> dict:
        if settings.google_client_secret_json.strip():
            return json.loads(settings.google_client_secret_json)
        with open(settings.google_client_secrets_file, "r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _credentials_json(token_json: str) -> Credentials:
        info = json.loads(token_json)
        return Credentials.from_authorized_user_info(info, [settings.gmail_scope])

    @staticmethod
    def get_authorization_url(state: str) -> tuple[str, str]:
        code_verifier = secrets.token_urlsafe(64)
        flow = Flow.from_client_config(
            GmailService._client_config(),
            scopes=[settings.gmail_scope],
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = settings.google_redirect_uri

        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return authorization_url, code_verifier

    @staticmethod
    def exchange_code(code: str, state: str, code_verifier: str) -> str:
        flow = Flow.from_client_config(
            GmailService._client_config(),
            scopes=[settings.gmail_scope],
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = settings.google_redirect_uri
        flow.fetch_token(code=code, code_verifier=code_verifier)
        return flow.credentials.to_json()

    @staticmethod
    def build_service(token_json: str):
        credentials = GmailService._credentials_json(token_json)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    @staticmethod
    def get_profile_email(token_json: str) -> str:
        service = GmailService.build_service(token_json)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")

    @staticmethod
    def send_email(token_json: str, recipient: str, subject: str, html: str, text: str) -> str:
        service = GmailService.build_service(token_json)
        message = MIMEMultipart("alternative")
        message["To"] = recipient
        message["Subject"] = subject
        message.attach(MIMEText(text, "plain", "utf-8"))
        message.attach(MIMEText(html, "html", "utf-8"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return result["id"]
