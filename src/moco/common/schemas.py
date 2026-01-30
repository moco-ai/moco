from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime

class Attachment(BaseModel):
    type: str  # "image" or "file"
    name: str
    path: str
    mime_type: Optional[str] = None
    data: Optional[str] = None  # Base64 encoded data (optional, for web UI)

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    profile: str = "development"
    provider: str = "openrouter"
    model: Optional[str] = None
    verbose: bool = False
    working_directory: Optional[str] = None
    attachments: Optional[list[Attachment]] = None

class SessionCreate(BaseModel):
    title: str = "New Chat"
    profile: str = "development"
    working_directory: Optional[str] = None

class FileResponse(BaseModel):
    content: str
    line_count: int
    size: int
    path: str

class SessionInfo(BaseModel):
    session_id: str
    title: Optional[str]
    status: str
    last_updated: Optional[str]
    profile: Optional[str]

class CostSummary(BaseModel):
    total_cost: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    record_count: int
    budget_limit: Optional[float] = None
    budget_status: Optional[str] = None

class LogEntry(BaseModel):
    timestamp: datetime = datetime.now()
    level: str = "info"
    message: str
    agent: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
