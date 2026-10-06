"""Run pinned OneDayAgent scoring in an isolated, credential-free container.

The upstream loader, artifact parsers, prompt and criterion parser are unchanged.
Only the LLM transport is adapted to the registered SWE2 backend via host spool.
This is an adapted judge, not a reproduction of Gemini leaderboard scores.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path


async def main():
    from onedayagent.judge.analyze_agent_scores import calculate_question_score
    from onedayagent.judge.llm_score.config import get_settings
    from onedayagent.judge.llm_score.data_loader import Answer, DataLoader
    from onedayagent.judge.llm_score.llm.client.factory import LLMClientFactory
    from onedayagent.judge.llm_score.llm.schemas.types import LLMResponse
    from onedayagent.judge.llm_score.scorer import AsyncScorer, ScoringTask

    control = Path('/judge-control')
    config = json.loads((control / 'config.json').read_text())
    settings = get_settings()
    settings.web_search_fallback_enabled = False
    settings.enable_google_search_grounding = False
    settings.llm_max_tokens = 12000
    questions = DataLoader().load_questions('/bench/datasets/questions_all.jsonl')
    question = next(q for q in questions if q.question_id == config['question_id'])
    answer = Answer(question_id=question.question_id, agent_name='anonymous',
                    content={'text': config['answer']}, attachment_filenames=config['files'], task_dir_name='submission')
    count = 0

    async def transport(request):
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError('One registered final judge call; no automatic retries')
        content = [{'type': 'input_text', 'text': '\n'.join(m.content for m in request.messages)}]
        for image in request.image_attachments or []:
            content.append({'type': 'input_image', 'image_url': f'data:{image.mime_type};base64,{image.base64_data}'})
        tmp = control / 'request.tmp'
        tmp.write_text(json.dumps({'content': content}))
        tmp.replace(control / 'request.json')
        for _ in range(360):
            response = control / 'response.json'
            if response.exists():
                result = json.loads(response.read_text())
                if result.get('error'):
                    raise RuntimeError(result['error'])
                return LLMResponse(content=result['text'], model=result['model'], usage=result.get('usage'))
            await asyncio.sleep(1)
        raise TimeoutError('Host judge transport timeout')

    LLMClientFactory.call_with_attachments = staticmethod(transport)
    scorer = AsyncScorer(model_name='devin/swe-2', max_concurrent=1, max_retries=1,
                         attachment_base_path='/bench/Attachments', answer_attachment_base_path='/answers',
                         reference_answer_base_path='/bench/Attachments/Reference_answer')
    results = await scorer._do_score(ScoringTask(question=question, answer=answer, model_name='devin/swe-2'))
    # Upstream missing-criterion defaults must not disguise an incomplete judge.
    if len(results) != len(question.score_criteria) or any(not r.reasoning.strip() for r in results):
        raise RuntimeError('Judge omitted one or more criterion decisions')
    raw = json.loads((control / 'response.json').read_text())['text']
    parsed = scorer._parse_llm_response(raw).get('criteria_results', [])
    expected = {c.criterion_id or str(i) for i, c in enumerate(question.score_criteria, 1)}
    if (len(parsed) != len(expected) or {r.get('criterion_id') for r in parsed} != expected
            or any(type(r.get('satisfied')) is not bool for r in parsed)):
        raise RuntimeError('Judge criterion IDs or boolean decisions are invalid')
    rows = [asdict(r) for r in results]
    (control / 'scores.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    (control / 'aggregate.json').write_text(json.dumps(calculate_question_score(rows), indent=2))


if __name__ == '__main__':
    asyncio.run(main())
