"""
Memory subsystem package for moco.

Provides persistent memory and learning capabilities:
- recall: Retrieve relevant memories for context
- learn: Store new knowledge with deduplication
- record_task_run_event: Log tool executions for learning
"""

from .service import MemoryService
from .embeddings import GENAI_AVAILABLE

__all__ = ["MemoryService", "GENAI_AVAILABLE"]












