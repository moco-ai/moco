"""
CheckpointStore: Manages session snapshots (checkpoints).
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

class CheckpointStore:
    """Manages session checkpoints stored as JSON files."""

    def __init__(self, checkpoints_dir: Optional[Path] = None):
        if checkpoints_dir:
            self.checkpoints_dir = checkpoints_dir
        else:
            self.checkpoints_dir = Path.home() / ".moco" / "checkpoints"
        
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, name: str) -> Path:
        """Get safe path for checkpoint file, preventing traversal."""
        # Remove directory separators and dots
        safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_", " ")).strip()
        if not safe_name:
            raise ValueError(f"Invalid checkpoint name: {name}")
        return self.checkpoints_dir / f"{safe_name}.json"

    def save_checkpoint(self, name: str, session_id: str, profile: str, working_dir: Optional[str] = None) -> Path:
        """Save a session checkpoint."""
        checkpoint_data = {
            "name": name,
            "session_id": session_id,
            "profile": profile,
            "working_dir": working_dir,
            "timestamp": datetime.now().isoformat()
        }
        
        path = self._safe_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
            
        return path

    def get_checkpoint(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific checkpoint by name."""
        try:
            path = self._safe_path(name)
        except ValueError:
            return None
            
        if not path.exists():
            return None
            
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        """List all saved checkpoints."""
        checkpoints = []
        for path in self.checkpoints_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    checkpoints.append(json.load(f))
            except Exception:
                continue
                
        # Sort by timestamp descending
        return sorted(checkpoints, key=lambda x: x.get("timestamp", ""), reverse=True)

    def delete_checkpoint(self, name: str) -> bool:
        """Delete a checkpoint."""
        try:
            path = self._safe_path(name)
        except ValueError:
            return False

        if path.exists():
            path.unlink()
            return True
        return False
