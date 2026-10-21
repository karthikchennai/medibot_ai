import secrets
import ssl
from types import SimpleNamespace

import httpx
import pytest

from medibot import llm


@pytest.fixture
def settings(monkeypatch):
    value = SimpleNamespace(groq_api_key=secrets.token_urlsafe(24), groq_model='openai/gpt-oss-20b',
                            groq_timeout_seconds=45)
    monkeypatch.setattr(llm, 'get_settings', lambda: value)
    return value


def test_gpt_oss_json_uses_verified_tls_and_separate_reasoning(settings, monkeypatch):
    def post(url, **kwargs):
        context = kwargs['verify']
        assert isinstance(context, ssl.SSLContext)
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        body = kwargs['json']
        assert body['model'] == 'openai/gpt-oss-20b'
        assert body['response_format'] == {'type': 'json_object'}
        assert body['reasoning_effort'] == 'low'
        assert body['include_reasoning'] is False
        assert body['max_completion_tokens'] == 4096
        assert 'reasoning_format' not in body
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"answer":"ok"}', 'reasoning': 'must not be returned'}}]})
    monkeypatch.setattr(llm.httpx, 'post', post)
    assert llm.GroqLLM().complete('Return JSON.', 'Question', json_mode=True) == '{"answer":"ok"}'


def test_incomplete_generation_is_rejected(settings, monkeypatch):
    monkeypatch.setattr(llm.httpx, 'post', lambda *a, **kw: httpx.Response(200, json={
        'choices': [{'finish_reason': 'length', 'message': {'content': 'SELECT partial'}}]}))
    with pytest.raises(llm.LLMError, match='response limit'):
        llm.GroqLLM().complete('Generate SQL.', 'Question')


def test_other_models_do_not_receive_gpt_oss_parameters(settings, monkeypatch):
    settings.groq_model = 'llama-3.3-70b-versatile'
    def post(url, **kwargs):
        assert 'reasoning_effort' not in kwargs['json']
        assert 'include_reasoning' not in kwargs['json']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                                                    'message': {'content': 'ok'}}]})
    monkeypatch.setattr(llm.httpx, 'post', post)
    assert llm.GroqLLM().complete('Answer.', 'Question') == 'ok'
