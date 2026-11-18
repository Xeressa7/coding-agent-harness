"""ask_user: istek gerçekten belirsizse kullanıcıya kısa bir soru sorar (`ask_user` katmanı).

Araç soruyu kendisi sormaz: ajan çağrıyı görünce `QuestionAsked` olayı üretir, ön yüz
cevabı (izin sorusu gibi `asend` ile) geri verir (core/agent.py). Cevap gelmezse (tek
görevlik `-p` koşusu, deney) model makul varsayımla devam etmeye ve varsayımını özetine
yazmaya yönlendirilir; böylece soru sorulamayan yerde de araç zarar vermez.
"""

from collections.abc import Sequence

from pydantic import BaseModel, Field, ValidationError

from harness.messages import ToolCall
from harness.tools.base import Tool, ToolOutput, short

NAME = "ask_user"
ASSUMPTION_LINE = "Varsayım"
NO_ANSWER = (
    "Kullanıcıya şu an soru sorulamıyor (cevap verecek kimse yok). En makul varsayımı seç, "
    f'devam et ve bitirirken özetine "{ASSUMPTION_LINE}: …" satırı yaz.'
)


class AskUserParams(BaseModel):
    question: str = Field(min_length=1, description="Tek, kısa ve cevaplanabilir soru")
    options: list[str] = Field(
        default_factory=lambda: [],
        max_length=5,
        description="Önerilen cevaplar (en fazla 5); kullanıcı başka bir şey de yazabilir",
    )


class AskUser(Tool[AskUserParams]):
    name = NAME
    description = (
        "İstek gerçekten belirsizse ve yanlış tahmin işi bozacaksa kullanıcıya kısa bir soru "
        "sorar, cevabı döner. Koddan, testlerden ya da istekten çıkarabileceğin şeyi sorma."
    )
    params_model = AskUserParams
    category = "plan"  # izin sormaz: kullanıcıya soru zaten kullanıcının önüne gelir

    def describe(self, params: AskUserParams) -> str:
        return short(params.question, 70)

    async def run(self, params: AskUserParams) -> ToolOutput:
        return answered(params, None)  # ajan araya girmediyse soracak ön yüz yok


def question_of(call: ToolCall) -> AskUserParams | None:
    """Çağrı geçerli bir soruysa parametreleri; değilse None (araç kendi hatasını döner)."""
    if call.name != NAME:
        return None
    try:
        return AskUserParams.model_validate_json(call.arguments)
    except ValidationError:
        return None


def pick_answer(typed: str, options: Sequence[str]) -> str | None:
    """Ön yüzün aldığı metin: seçenek numarası seçeneğe çevrilir; boş metin cevap yok."""
    text = typed.strip()
    if not text:
        return None
    if text.isdigit() and 1 <= int(text) <= len(options):
        return options[int(text) - 1]
    return text


def answered(params: AskUserParams, answer: str | None) -> ToolOutput:
    if answer is None or not answer.strip():
        return ToolOutput(NO_ANSWER, summary="cevap yok", details={"answered": False})
    return ToolOutput(
        f"Kullanıcının cevabı: {answer.strip()}",
        summary=short(answer, 60),
        details={"answered": True, "question": params.question},
    )
