from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceStatus:
    stt_available: bool
    tts_available: bool
    message: str


class VoiceService:
    def status(self) -> VoiceStatus:
        try:
            import faster_whisper  # noqa: F401

            stt = True
        except Exception:
            stt = False
        return VoiceStatus(
            stt_available=stt,
            tts_available=False,
            message=(
                "STT local disponible via faster-whisper." if stt else
                "Voz opcional no instalada. Instala el extra [voice] y configura Piper para TTS."
            ),
        )
