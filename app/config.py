"""Settings loaded from the environment (.env locally, Railway variables in prod).

Every name here must match .env.example. Secrets never get logged or printed.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic. Model IDs are fixed stack choices; see docs/DECISIONS.md.
    anthropic_api_key: str = ""
    orchestrator_model: str = "claude-sonnet-5-5"
    voice_model: str = "claude-haiku-4-5"  # selected inside ElevenLabs, not called by us

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

    # Security
    events_shared_secret: str = ""
    tools_shared_secret: str = ""
    allowed_caller_number: str = ""

    # Where callbacks ring
    my_phone_number: str = ""

    # Deploy
    public_base_url: str = ""


settings = Settings()
