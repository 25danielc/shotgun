"""Settings loaded from the environment (.env locally, Railway variables in prod).

Every name here must match .env.example. Secrets never get logged or printed.
"""

from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic. Model IDs are fixed stack choices; see docs/DECISIONS.md.
    anthropic_api_key: str = ""
    orchestrator_model: str = "claude-sonnet-5-5"
    voice_model: str = "claude-haiku-4-5"  # selected inside ElevenLabs, not called by us
    inline_model: str = "claude-haiku-4-5"  # inline tools search_web / draft_message (D17)

    # ElevenLabs Agents + Twilio
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_phone_number_id: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Neon Postgres
    database_url: str = ""

    # GitHub coder worker
    github_token: str = ""
    github_demo_repo: str = ""
    github_webhook_secret: str = ""

    # Composio Gmail worker
    composio_api_key: str = ""
    composio_user_id: str = ""

    # Google Maps Platform: Routes API only (ETA, step 5.1)
    google_maps_api_key: str = ""
    home_address: str = ""  # ETA destination (D16)
    # Local time for spoken times and deadlines (the Railway server clock is UTC)
    timezone: str = "America/Detroit"

    # Security
    events_shared_secret: str = ""
    tools_shared_secret: str = ""
    allowed_caller_number: str = ""

    # Where calls ring
    my_phone_number: str = ""

    # Departure call policy (step 4.3, D17). CALL_POLICY=always rings on every plug-in (rehearsals).
    call_policy: str = "auto"  # auto | always
    departure_min_drive_minutes: int = 10
    departure_quiet_minutes: int = 30

    # Unplug recap push (step 4.4). The topic is a secret: anyone who knows it can read it.
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    # Deploy
    public_base_url: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
