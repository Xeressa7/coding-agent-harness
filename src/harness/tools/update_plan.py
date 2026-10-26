"""update_plan: modelin iş planını yazdığı araç (Codex'teki update_plan, Claude Code'daki todo).

Plan dışarıda hiçbir şeyi değiştirmez; modelin "ne yapacağım, neredeyim" diye
düşündüğünü görünür kılar. Her çağrı planın tamamını yeniden yazar; araç son
hâlini tutar ve ölçüm olarak kayda geçirir. Zaman çizelgesi planın adım adım
nasıl ilerlediğini buradan çizer.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from harness.tools.base import Tool, ToolOutput

StepStatus = Literal["pending", "in_progress", "completed"]
_MARK = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}


class PlanStep(BaseModel):
    step: str = Field(min_length=1, description="Adımın kısa anlatımı")
    status: StepStatus = Field(description="pending, in_progress ya da completed")


class UpdatePlanParams(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, description="Planın tamamı, sırasıyla")
    explanation: str | None = Field(default=None, description="Neden değişti (isteğe bağlı)")

    @model_validator(mode="after")
    def one_step_at_a_time(self) -> "UpdatePlanParams":
        if sum(s.status == "in_progress" for s in self.steps) > 1:
            raise ValueError("Aynı anda en fazla bir adım in_progress olabilir.")
        return self


class UpdatePlan(Tool[UpdatePlanParams]):
    name = "update_plan"
    description = (
        "Çok adımlı işlerde planını yazar ya da günceller: her adım pending, in_progress "
        "ya da completed. Her seferinde planın tamamını gönder; aynı anda tek adım "
        "in_progress olsun. Basit tek adımlı işlerde kullanma."
    )
    params_model = UpdatePlanParams
    category = "plan"

    def __init__(self) -> None:
        self.steps: list[PlanStep] = []

    def describe(self, params: UpdatePlanParams) -> str:
        done = sum(s.status == "completed" for s in params.steps)
        return f"{done}/{len(params.steps)} adım tamam"

    async def run(self, params: UpdatePlanParams) -> ToolOutput:
        self.steps = list(params.steps)
        done = sum(s.status == "completed" for s in self.steps)
        current = next((s.step for s in self.steps if s.status == "in_progress"), None)
        lines = [f"{_MARK[s.status]} {s.step}" for s in self.steps]
        if params.explanation:
            lines.insert(0, f"Not: {params.explanation}")
        summary = f"{done}/{len(self.steps)} tamam" + (f" · şimdi: {current}" if current else "")
        return ToolOutput(
            "Plan güncellendi.\n" + "\n".join(lines),
            summary=summary,
            details={
                "steps": [s.model_dump() for s in self.steps],
                "completed": done,
                "total": len(self.steps),
                "current": current,
            },
        )
