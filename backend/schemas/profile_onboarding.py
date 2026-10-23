from pydantic import BaseModel, Field


class ProfileOnboardingMessageRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1200)
    source_type: str = Field(default="user_text", description="user_text / voice_transcript / uploaded_material / image_ocr")
    source_metadata: dict | None = Field(default=None, description="附件来源元信息（文件名、OCR引擎等）")


class ProfileOnboardingTaskRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)


class ProfileOnboardingConfirmRequest(ProfileOnboardingTaskRequest):
    edits: dict[str, str] = Field(default_factory=dict)
