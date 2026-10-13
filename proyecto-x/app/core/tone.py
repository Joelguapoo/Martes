from dataclasses import dataclass


@dataclass(frozen=True)
class ToneSignal:
    label: str
    confidence: float
    guidance: str


def analyze_tone(text: str) -> ToneSignal:
    lowered = text.lower()
    frustration = ["no funciona", "harto", "frustrado", "molesto", "error otra vez"]
    enthusiasm = ["excelente", "genial", "vamos", "me encanta", "perfecto"]
    tired = ["cansado", "agotado", "sin energia"]

    if any(word in lowered for word in frustration):
        return ToneSignal("frustracion_posible", 0.62, "Se breve, calmado y paso a paso.")
    if any(word in lowered for word in enthusiasm):
        return ToneSignal("entusiasmo_posible", 0.58, "Puedes proponer un plan mas ambicioso.")
    if any(word in lowered for word in tired):
        return ToneSignal(
            "cansancio_posible",
            0.55,
            "Reduce carga cognitiva y prioriza lo esencial.",
        )
    return ToneSignal("neutral_o_indeterminado", 0.35, "Mantener tono profesional y flexible.")
