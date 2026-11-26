"""Döngü yöneticisi: Agent sınıfı (rapor Bölüm 7 ve 11).

Görevi alır ve modeli çağırır. Yanıtta araç çağrısı varsa çağrıyı izin
katmanından geçirip çalıştırır, sonucu mesaj geçmişine ekler ve modeli yeniden
çağırır. Model araç çağırmayı bıraktığında ya da adım/token limiti dolduğunda
durur.

Ekrana yazmaz: her adımda events.py'deki bir olayı üretir, onu çizmek ön
yüzün (cli/) işidir. İzin katmanı "sor" dediğinde PermissionRequested üretir
ve cevabı kendisi sormadan, `asend` ile geri gelecek şekilde bekler; cevapsız
(örn. sade `async for` ile) geçilirse güvenli taraf seçilir: araç reddedilir.
"""

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from harness.core.anatomy import Anatomy
from harness.core.checkpoints import Checkpoints, CheckpointUnavailable
from harness.core.code_blocks import extract_code_blocks, save_code_blocks
from harness.core.context import grouped, limit_output
from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
    Event,
    HarnessIntervened,
    ModelReasoningDelta,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    QuestionAsked,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.finish_check import FinishCheck
from harness.core.finish_check import note as finish_note
from harness.core.guards import RepeatGuard, TestGuard
from harness.core.permissions import Decision, PermissionManager
from harness.core.project_context import CtxMemory, MemoryUnavailable, load_instructions
from harness.core.pruning import ContextPruner
from harness.core.tool_call_fallback import recover_tool_calls
from harness.llm.base import LLMClient, StreamingLLMClient
from harness.messages import (
    AssistantMessage,
    ImageAttachment,
    Message,
    ReasoningDelta,
    SystemMessage,
    ToolResult,
    UserMessage,
)
from harness.tools.ask_user import answered, question_of
from harness.tools.base import ToolOutcome, ToolRegistry

# Hafızanın "dosyaya dokununca hatırlat" kancası hangi araçlarda çalışır (ctx'in araç adlarıyla).
_RECALL_TOOLS = {"read_file": "Read", "edit_file": "Edit", "write_file": "Write"}


class Agent:
    """Modele sorar, istediği araçları çalıştırır, model "bitti" diyene kadar tekrarlar.

    Mesaj geçmişi `history`'de tutulur ve yalnız sona eklenerek büyür.
    `permissions` da açıktır: ön yüz, "bu oturum için her zaman izin ver"
    seçildiğinde doğrudan `agent.permissions.grant(ad)` çağırır.
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        system_prompt: str,
        *,
        permissions: PermissionManager,
        max_steps: int = 20,
        model_label: str | None = None,
        provider: str | None = None,
        effort: str | None = None,
        anatomy: Anatomy | None = None,
        root: Path | None = None,
        history: Sequence[Message] = (),
        memory_session_id: str | None = None,
        checkpoints: Checkpoints | None = None,
    ) -> None:
        """root: harness'ın kendisinin dosya yazdığı çalışma dizini (seviye 0 kod blokları).

        history: talimattan sonra gelen önceki konuşma; devam edilen oturum ya da
        sohbet ortasında model değişimi için.

        memory_session_id: hafıza katmanının ctx'e verdiği oturum kimliği. ctx aynı notu
        aynı kimlikte bir kez verir; sohbet ajanı yeniden kurarken (model/katman değişimi)
        aynı kimliği geçirir ki geçmişte zaten olan notlar tekrar eklenmesin.

        checkpoints: her görevden önce çalışma dizininin görüntüsünü alır (sohbetin `/undo`'su).
        Model bunu görmez; katman değil, kullanıcının güvenlik ağıdır.
        """
        if anatomy is not None and anatomy.save_code_blocks and root is None:
            raise ValueError("Kod bloklarını kaydeden anatomi bir çalışma dizini (root) ister.")
        self._model_label = model_label
        self._provider = provider
        self._anatomy = anatomy
        self._root = root
        self._effort = effort
        self._llm = llm
        self._tools = tools
        self.permissions = permissions
        self._max_steps = max_steps
        # Anatomisiz kurulan ajan (testler, eski çağıranlar) katmanlarda varsayılanları kullanır.
        self._layers = anatomy if anatomy is not None else Anatomy(name="varsayılan")
        self._prepared = False
        self._memory = (
            CtxMemory(root, session_id=memory_session_id or uuid4().hex)
            if self._layers.memory and root is not None
            else None
        )
        self.history: list[Message] = [SystemMessage(text=system_prompt), *history]
        self.fingerprint = harness_fingerprint(system_prompt, tools)
        self._checkpoints = checkpoints
        # Bir sonraki görevin başına eklenecek harness notları (ör. "kullanıcı geri aldı").
        # Görev metnine girer: kayıtta görünür, oturuma devam edilince de geçmişte kalır.
        self.notes: list[str] = []

    async def _add_project_context(self) -> AsyncGenerator[HarnessIntervened]:
        """AGENTS.md ve hafıza haritasını sistem talimatının sonuna ekler (adım 0).

        Eklenen metnin kendisi ilk `ContextAssembled`'da görünür; burada her ekleme
        (ya da çalışmayan katman) ayrı bir müdahale olarak kaydedilir.
        """
        blocks: list[str] = []
        now = datetime.now
        if self._layers.agents_md and self._root is not None:
            found = load_instructions(self._root)
            # ctx'in hafıza bölümü memory katmanına ait; bu katman onu hiç taşımaz.
            split = f"hafıza bölümü ({grouped(found.memory_chars)}) ayrıldı" if found else ""
            if found is not None and found.text:
                blocks.append(f"# Proje talimatı ({found.source})\n\n{found.text}")
                summary = f"{found.source} · {grouped(len(found.text))} karakter"
                yield HarnessIntervened(
                    step=0,
                    layer="agents_md",
                    action="inject",
                    summary=f"{summary} · {split}" if found.memory_chars else summary,
                    timestamp=now(UTC),
                )
            elif found is not None:
                yield HarnessIntervened(
                    step=0,
                    layer="agents_md",
                    action="skip",
                    summary=f"{found.source} yalnız ctx hafıza bölümünden oluşuyor · {split}",
                    timestamp=now(UTC),
                )
        if self._memory is not None:
            try:
                index = await self._memory.index()
            except MemoryUnavailable as exc:
                self._memory = None  # dosya başına tekrar denenip yavaşlatmasın
                yield HarnessIntervened(
                    step=0, layer="memory", action="skip", summary=str(exc), timestamp=now(UTC)
                )
            else:
                if index:
                    blocks.append(index)
                    yield HarnessIntervened(
                        step=0,
                        layer="memory",
                        action="inject",
                        summary=f"not haritası · {grouped(len(index))} karakter",
                        timestamp=now(UTC),
                    )
        if blocks and isinstance(self.history[0], SystemMessage):
            text = "\n\n".join([self.history[0].text, *blocks])
            self.history[0] = SystemMessage(text=text)

    async def _recall(
        self, tool: str, details: dict[str, Any], is_error: bool
    ) -> tuple[str, str] | None:
        """Hafıza katmanı: dokunulan dosyayı anan notlar → (modele not, kayıt özeti).

        ctx çalışmazsa `MemoryUnavailable` çağırana gider (katmanı kapatıp kaydeder)."""
        kind = _RECALL_TOOLS.get(tool)
        path = details.get("path")
        if self._memory is None or kind is None or is_error or not isinstance(path, str):
            return None
        notes = await self._memory.recall(path, kind)
        if not notes:
            return None
        count = sum(line.startswith("- ") for line in notes.splitlines())
        return f"[harness · hafıza]\n{notes}", f"{path} → {count} not"

    async def run(
        self, task: str, images: Sequence[ImageAttachment] = ()
    ) -> AsyncGenerator[Event, bool | str | None]:
        """Bir görevi çalıştırır; görseller (varsa) kullanıcı mesajına eklenir."""
        request = task
        if self.notes:
            task = "\n\n".join([*self.notes, task])
            self.notes.clear()
        self.history.append(UserMessage(text=task, images=tuple(images)))
        guard = RepeatGuard() if self._anatomy and self._anatomy.loop_guard else None
        tests = TestGuard(self._root) if self._layers.test_guard and self._root else None
        pruner = ContextPruner() if self._layers.context_prune else None
        finish = (
            FinishCheck(can_run_commands=any(s.name == "bash" for s in self._tools.specs()))
            if self._layers.finish_check
            else None
        )
        started = perf_counter()
        step = 0
        yield TaskStarted(
            task=task,
            images=tuple(images),
            provider=self._provider,
            model=self._model_label,
            effort=self._effort,
            anatomy=self._anatomy,
            root=str(self._root) if self._root else None,
            fingerprint=self.fingerprint,
            timestamp=datetime.now(UTC),
        )
        if self._checkpoints is not None and self._checkpoints.broken is None:
            try:
                tree = await asyncio.to_thread(self._checkpoints.save, request)
            except CheckpointUnavailable as exc:
                action, summary = "skip", f"geri alma bu oturumda kapalı: {exc}"
            else:
                action, summary = "save", f"tur öncesi görüntü {tree[:8]}"
            yield HarnessIntervened(
                step=0,
                layer="checkpoint",
                action=action,
                summary=summary,
                timestamp=datetime.now(UTC),
            )
        try:
            if not self._prepared:  # yalnız ilk görevde: talimat bir kez kurulur
                self._prepared = True
                async for note in self._add_project_context():
                    yield note
            for step in range(1, self._max_steps + 1):
                if pruner is not None:
                    last = next(
                        (m for m in reversed(self.history) if isinstance(m, AssistantMessage)),
                        None,
                    )
                    used = last.usage.input_tokens if last and last.usage else None
                    checked = pruner.check(self.history, used)
                    if checked is not None:  # geçmiş bir kez kısalır; önbellek o noktadan kırılır
                        self.history[:], report = checked
                        yield HarnessIntervened(
                            step=step,
                            layer="context_prune",
                            action="prune",
                            summary=report.summary(),
                            timestamp=datetime.now(UTC),
                        )
                messages = tuple(self.history)
                specs = tuple(self._tools.specs())
                yield ContextAssembled(
                    step=step, messages=messages, tools=specs, timestamp=datetime.now(UTC)
                )
                yield ModelStarted(step=step, timestamp=datetime.now(UTC))
                model_started = perf_counter()
                consumer_seconds = 0.0
                if isinstance(self._llm, StreamingLLMClient):
                    reply: AssistantMessage | None = None
                    async with aclosing(self._llm.stream(messages, specs)) as parts:
                        async for part in parts:
                            if isinstance(part, AssistantMessage):
                                reply = part
                                break
                            if part.text:
                                paused = perf_counter()
                                now = datetime.now(UTC)
                                if isinstance(part, ReasoningDelta):
                                    yield ModelReasoningDelta(
                                        step=step, text=part.text, timestamp=now
                                    )
                                else:
                                    yield ModelTextDelta(step=step, text=part.text, timestamp=now)
                                consumer_seconds += perf_counter() - paused
                    if reply is None:
                        raise RuntimeError("Model akışı tamamlanmış yanıt vermeden bitti.")
                else:
                    reply = await self._llm.complete(messages, specs)
                model_duration = perf_counter() - model_started - consumer_seconds
                self.history.append(reply)
                yield ModelReplied(
                    step=step,
                    text=reply.text,
                    tool_calls=reply.tool_calls,
                    usage=reply.usage,
                    first_text_seconds=reply.first_text_seconds,
                    duration_seconds=model_duration,
                    reasoning=reply.reasoning,
                    timestamp=datetime.now(UTC),
                )

                calls = reply.tool_calls
                if not calls and self._layers.tool_call_fallback:
                    recovered = recover_tool_calls(reply.text, self._tools)
                    if recovered:
                        calls = recovered
                        yield HarnessIntervened(
                            step=step,
                            layer="tool_call_fallback",
                            action="recover",
                            summary="metne yazılmış araç çağrısı yakalandı: "
                            + ", ".join(c.name for c in recovered),
                            timestamp=datetime.now(UTC),
                        )

                if not calls:
                    missing = finish.check(reply.text) if finish is not None else None
                    if missing is not None:  # bir kez: eksik söylenir, döngü bir adım sürer
                        self.history.append(UserMessage(text=finish_note(missing)))
                        yield HarnessIntervened(
                            step=step,
                            layer="finish_check",
                            action="warn",
                            summary=missing,
                            timestamp=datetime.now(UTC),
                        )
                        continue
                    if self._anatomy and self._anatomy.save_code_blocks and self._root:
                        blocks, unnamed = extract_code_blocks(reply.text)
                        saved, rejected = save_code_blocks(blocks, self._root)
                        yield CodeBlocksSaved(
                            step=step,
                            saved=tuple(saved),
                            rejected=tuple(rejected),
                            unnamed=unnamed,
                            timestamp=datetime.now(UTC),
                        )
                    yield TaskFinished(
                        reason="done",
                        steps=step,
                        duration_seconds=perf_counter() - started,
                        timestamp=datetime.now(UTC),
                    )
                    return

                for call in calls:
                    category, summary = self._tools.describe(call)
                    verdict = guard.check(call) if guard else None
                    if verdict is not None:
                        yield HarnessIntervened(
                            step=step,
                            layer="loop_guard",
                            action=verdict.action,
                            summary=f"{call.name} art arda {verdict.repeats}. kez",
                            call_id=call.id,
                            timestamp=datetime.now(UTC),
                        )
                    if verdict is not None and verdict.action == "block":
                        result = ToolResult(
                            tool_call_id=call.id, content=verdict.message(call.name), is_error=True
                        )
                        self.history.append(result)
                        yield ToolFinished(
                            call=call,
                            result=result,
                            duration_seconds=0.0,
                            category=category,
                            summary=summary,
                            outcome="takılma: çalıştırılmadı",
                            timestamp=datetime.now(UTC),
                        )
                        continue
                    decision = self.permissions.decide(call)
                    if decision is Decision.ASK:
                        answer = yield PermissionRequested(
                            call=call, summary=summary, timestamp=datetime.now(UTC)
                        )
                        decision = Decision.ALLOW if answer else Decision.DENY
                    if decision is Decision.DENY:
                        result = ToolResult(
                            tool_call_id=call.id,
                            content="İzin verilmedi, araç çalıştırılmadı.",
                            is_error=True,
                        )
                        self.history.append(result)
                        yield ToolFinished(
                            call=call,
                            result=result,
                            duration_seconds=0.0,
                            category=category,
                            summary=summary,
                            outcome="izin verilmedi",
                            timestamp=datetime.now(UTC),
                        )
                        continue
                    yield ToolStarted(
                        call=call, category=category, summary=summary, timestamp=datetime.now(UTC)
                    )
                    if tests is not None:
                        tests.before(call)
                    tool_started = perf_counter()
                    asked = question_of(call) if self._tools.get(call.name) else None
                    if asked is not None:  # soruyu ön yüz sorar, cevabı asend ile verir
                        reply_text = yield QuestionAsked(
                            step=step,
                            call_id=call.id,
                            question=asked.question,
                            options=tuple(asked.options),
                            timestamp=datetime.now(UTC),
                        )
                        given = answered(asked, reply_text if isinstance(reply_text, str) else None)
                        outcome = ToolOutcome(
                            result=ToolResult(tool_call_id=call.id, content=given.content),
                            summary=given.summary,
                            details=given.details,
                        )
                    else:
                        outcome = await self._tools.execute(call)
                    tool_duration = perf_counter() - tool_started
                    result = outcome.result
                    # Önce aracın kendi çıktısı kesilir; harness notları sonra eklenir. Böylece
                    # `full_content` yalnız aracın tam metnidir ve notlar kesilip kaybolmaz.
                    full_content: str | None = None
                    limited = limit_output(result.content) if self._layers.output_limit else None
                    if limited is not None:  # modele kesilmiş hâl gider, tam hâl kayıtta kalır
                        full_content = result.content
                        result = result.model_copy(update={"content": limited})
                        yield HarnessIntervened(
                            step=step,
                            layer="output_limit",
                            action="truncate",
                            summary=f"{call.name} sonucu {grouped(len(full_content))} → "
                            f"{grouped(len(limited))} karakter",
                            call_id=call.id,
                            timestamp=datetime.now(UTC),
                        )
                    if verdict is not None:  # uyarı: çağrı çalıştı, modele not düşülür
                        note = verdict.message(call.name)
                        content = f"{result.content}\n\n{note}"
                        result = result.model_copy(update={"content": content})
                    removed = tests.after(call) if tests is not None else None
                    if removed is not None:  # eski testten doğrulama eksildi: modele sorulur
                        if finish is not None:
                            finish.guard_warned()
                        content = f"{result.content}\n\n{removed.message()}"
                        result = result.model_copy(update={"content": content})
                        yield HarnessIntervened(
                            step=step,
                            layer="test_guard",
                            action="warn",
                            summary=f"{removed.path}: {len(removed.removed)} eski doğrulama",
                            call_id=call.id,
                            timestamp=datetime.now(UTC),
                        )
                    try:
                        recalled = await self._recall(call.name, outcome.details, result.is_error)
                    except MemoryUnavailable as exc:
                        # Takılan/bozuk ctx her dosyada yeniden beklenmesin: katman kapanır.
                        self._memory = None
                        recalled = None
                        yield HarnessIntervened(
                            step=step,
                            layer="memory",
                            action="skip",
                            summary=f"{exc} · hafıza bu oturumda kapatıldı",
                            call_id=call.id,
                            timestamp=datetime.now(UTC),
                        )
                    if recalled is not None:
                        notes, recall_summary = recalled
                        content = f"{result.content}\n\n{notes}"
                        result = result.model_copy(update={"content": content})
                        yield HarnessIntervened(
                            step=step,
                            layer="memory",
                            action="recall",
                            summary=recall_summary,
                            call_id=call.id,
                            timestamp=datetime.now(UTC),
                        )
                    self.history.append(result)
                    if finish is not None:
                        finish.saw(call, result, outcome.details)
                    yield ToolFinished(
                        call=call,
                        result=result,
                        duration_seconds=tool_duration,
                        category=category,
                        summary=summary,
                        outcome=outcome.summary,
                        details=outcome.details,
                        full_content=full_content,
                        timestamp=datetime.now(UTC),
                    )
        except asyncio.CancelledError:
            yield TaskFinished(
                reason="cancelled",
                steps=step,
                duration_seconds=perf_counter() - started,
                timestamp=datetime.now(UTC),
            )
            raise
        except Exception as exc:
            yield TaskFinished(
                reason="error",
                steps=step,
                error=str(exc),
                duration_seconds=perf_counter() - started,
                timestamp=datetime.now(UTC),
            )
            raise

        yield TaskFinished(
            reason="step_limit",
            steps=step,
            duration_seconds=perf_counter() - started,
            timestamp=datetime.now(UTC),
        )


def harness_fingerprint(system_prompt: str, tools: ToolRegistry) -> str:
    """Talimat + araç şemalarının özeti (sha256, ilk 16 hane). Proje bağlamı (AGENTS.md, hafıza)
    sonradan eklendiği için girmez: parmak izi depodan bağımsız, yalnız harness ayarını söyler."""
    blob = json.dumps(
        {"system": system_prompt, "tools": [spec.model_dump() for spec in tools.specs()]},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
