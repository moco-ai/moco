from dataclasses import dataclass
from PIL import Image
import io
import tempfile
import os
import uuid

@dataclass
class ProcessedMedia:
    """処理済みメディアデータ"""
    data: bytes
    mime_type: str
    metadata: dict

class MediaProcessor:
    """モバイルからのメディアファイル前処理"""
    
    MAX_IMAGE_DIMENSION = 2048
    JPEG_QUALITY = 85
    STORAGE_PATH = os.path.expanduser("~/.moco/media")
    
    def __init__(self):
        os.makedirs(self.STORAGE_PATH, exist_ok=True)
    
    async def process_image(self, data: bytes) -> ProcessedMedia:
        """画像をリサイズしてJPEG変換"""
        img = Image.open(io.BytesIO(data))
        
        # リサイズ
        if max(img.size) > self.MAX_IMAGE_DIMENSION:
            ratio = self.MAX_IMAGE_DIMENSION / max(img.size)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # RGBA/P → RGB変換
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=self.JPEG_QUALITY)
        
        return ProcessedMedia(
            data=output.getvalue(),
            mime_type="image/jpeg",
            metadata={
                "original_size": len(data),
                "processed_size": output.tell(),
                "dimensions": img.size
            }
        )
    
    async def process_audio(self, data: bytes, mime_type: str) -> ProcessedMedia:
        """音声をテキストに変換 (Whisper API等)"""
        transcript = await self._transcribe(data, mime_type)
        
        return ProcessedMedia(
            data=data,
            mime_type=mime_type,
            metadata={"transcript": transcript}
        )
    
    async def _transcribe(self, data: bytes, mime_type: str) -> str:
        """Whisper APIで音声認識"""
        try:
            from openai import OpenAI
        except ImportError:
            return "[Error: openai library not installed]"
        
        client = OpenAI()
        
        # 一時ファイルに保存
        suffix = ".m4a" if "m4a" in mime_type else ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            temp_path = f.name
        
        try:
            with open(temp_path, "rb") as audio_file:
                response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ja"
                )
            return response.text
        except Exception as e:
            return f"[Transcription failed: {str(e)}]"
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    
    def save_temp(self, data: bytes, filename: str) -> str:
        """一時保存してパスを返す"""
        safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
        path = os.path.join(self.STORAGE_PATH, safe_name)
        with open(path, "wb") as f:
            f.write(data)
        return path
