#!/usr/bin/env python
"""Head-to-head checkpoint comparison using an LLM judge."""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.phase9.model_client import BaseModelClient, LLMJudge
from src.phase9.models import EvalSample, HeadToHeadResult


class HeadToHeadRunner:
    def __init__(
        self,
        model_a: BaseModelClient,
        model_b: BaseModelClient,
        judge: LLMJudge,
        concurrency: int = 4,
    ) -> None:
        self.model_a = model_a
        self.model_b = model_b
        self.judge = judge
        self.semaphore = asyncio.Semaphore(concurrency)

    async def compare(self, samples: list[EvalSample]) -> list[HeadToHeadResult]:
        async def one(sample: EvalSample) -> HeadToHeadResult:
            async with self.semaphore:
                answer_a_task = asyncio.create_task(self.model_a.generate(sample.prompt, n=1, temperature=0.2, max_tokens=768))
                answer_b_task = asyncio.create_task(self.model_b.generate(sample.prompt, n=1, temperature=0.2, max_tokens=768))
                answer_a = (await answer_a_task)[0].text
                answer_b = (await answer_b_task)[0].text
                judged = await self.judge.judge(sample.prompt, answer_a, answer_b)
                scores = {
                    "correctness_a": float(judged.get("correctness_a", 0.0)),
                    "correctness_b": float(judged.get("correctness_b", 0.0)),
                    "security_a": float(judged.get("security_a", 0.0)),
                    "security_b": float(judged.get("security_b", 0.0)),
                    "explanation_a": float(judged.get("explanation_a", 0.0)),
                    "explanation_b": float(judged.get("explanation_b", 0.0)),
                }
                winner = judged.get("winner", "tie")
                if winner not in {"model_a", "model_b", "tie"}:
                    winner = "tie"
                return HeadToHeadResult(
                    sample_id=sample.sample_id,
                    prompt=sample.prompt,
                    model_a=self.model_a.model,
                    model_b=self.model_b.model,
                    winner=winner,
                    scores=scores,
                    rationale=str(judged.get("rationale", "")),
                )

        return await asyncio.gather(*(one(sample) for sample in samples))

