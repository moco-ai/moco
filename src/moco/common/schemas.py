from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    profile: str = "development"
    provider: str = "gemini"
    model: Optional[str] = None
    verbose: bool = False
    working_directory: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None

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
