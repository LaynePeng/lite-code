"""会话持久化（对应课程第10课（插件架构） SessionStore，增强：列表/删除/备份安全写盘）。"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from .types import Message

logger = logging.getLogger("litecode.session")


class SessionSnapshot:
    def __init__(
        self,
        session_id: str,
        messages: List[Message],
        metadata: Dict[str, Any],
        created_at: int,
        updated_at: int,
    ) -> None:
        self.session_id = session_id
        self.messages = messages
        self.metadata = metadata
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionSnapshot":
        return cls(
            session_id=data.get("session_id", ""),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
        )


class SessionStore:
    def __init__(self, storage_dir: str = "./.lite-code/sessions") -> None:
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)

    def _file_path(self, session_id: str) -> str:
        safe_id = session_id.replace("/", "_").replace("\\", "_")
        return os.path.join(self.storage_dir, f"{safe_id}.json")

    def save(self, session_id: str, messages: List[Message], metadata: Dict[str, Any] = None) -> None:
        existing = self.load(session_id)
        snapshot = SessionSnapshot(
            session_id=session_id,
            messages=messages,
            metadata=metadata or {},
            created_at=existing.created_at if existing else int(__import__("time").time() * 1000),
            updated_at=int(__import__("time").time() * 1000),
        )
        # 原子写盘：先写临时文件再 rename，防止中途崩溃产生半截 JSON
        path = self._file_path(session_id)
        fd, tmp_path = tempfile.mkstemp(dir=self.storage_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load(self, session_id: str) -> Optional[SessionSnapshot]:
        path = self._file_path(session_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return SessionSnapshot.from_dict(json.load(f))
        except Exception:
            logger.exception("[SessionStore] Error reading session file %s", session_id)
            return None

    def list(self) -> List[Dict[str, Any]]:
        snapshots: List[Dict[str, Any]] = []
        for fname in os.listdir(self.storage_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.storage_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    snapshots.append(json.load(f))
            except Exception:
                logger.exception("[SessionStore] Error reading session file %s", fname)
        snapshots.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return snapshots

    def delete(self, session_id: str) -> bool:
        path = self._file_path(session_id)
        if os.path.exists(path):
            os.unlink(path)
            return True
        return False

    def update_metadata(self, session_id: str, updates: Dict[str, Any]) -> Optional[SessionSnapshot]:
        """原子更新会话元数据，保留消息与创建时间。"""
        snapshot = self.load(session_id)
        if snapshot is None:
            return None
        metadata = {**snapshot.metadata, **updates}
        self.save(session_id, snapshot.messages, metadata)
        return self.load(session_id)
