from functools import lru_cache
import ssl
import logging

import httpx

from .config import get_settings


class LLMError(RuntimeError):
    pass


class ProviderRefusal(LLMError):
    pass


@lru_cache
def tls_context() -> ssl.SSLContext:
    # Include trusted Windows certificates while retaining hostname and chain checks.
    bundle = getattr(get_settings(), 'ssl_ca_bundle', '')
    return ssl.create_default_context(cafile=bundle or None)


class GroqLLM:
    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        settings = get_settings()
        if not settings.groq_api_key:
            raise LLMError('Add GROQ_API_KEY to the project .env file, then restart the backend.')
        body = {'model': settings.groq_model, 'temperature': 0,
                'max_completion_tokens': 4096, 'messages': [{'role': 'system', 'content': system},
                                               {'role': 'user', 'content': user}]}
        if settings.groq_model in ('openai/gpt-oss-20b', 'openai/gpt-oss-120b'):
            body.update(reasoning_effort='low', include_reasoning=False)
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        try:
            response = httpx.post('https://api.groq.com/openai/v1/chat/completions', json=body,
                                 headers={'Authorization': f'Bearer {settings.groq_api_key}'},
                                 verify=tls_context(),
                                 timeout=settings.groq_timeout_seconds)
            if response.status_code == 429:
                raise LLMError('Groq rate limit reached. Please wait a moment and try again.')
            if response.status_code in (401, 403):
                raise LLMError('Groq rejected the API credentials or model access. Check the local configuration.')
            if response.status_code >= 400:
                raise LLMError(f'Groq could not complete this request (HTTP {response.status_code}). Check GROQ_MODEL and retry.')
            choice = response.json()['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise LLMError('Groq reached the response limit. Please retry with a narrower question.')
            result = choice['message']['content']
            if not isinstance(result, str) or not result.strip():
                raise LLMError('Groq returned an empty response. Please retry.')
            return result
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
            raise LLMError('The Groq service could not be reached or returned an invalid response. Please retry.') from error


def provider_status(settings=None) -> dict:
    from .bedrock import bedrock_configured
    settings = settings or get_settings()
    primary = bedrock_configured(settings)
    fallback = bool(settings.groq_api_key) and getattr(settings, 'groq_fallback_enabled', True)
    return {'bedrock_configured': primary, 'groq_configured': bool(settings.groq_api_key),
            'groq_fallback_enabled': getattr(settings, 'groq_fallback_enabled', True),
            'llm_configured': primary or fallback,
            'provider': 'Bedrock' if primary else ('Groq' if fallback else 'Unconfigured'),
            'fallback_provider': 'Groq' if fallback else None}


class FallbackLLM:
    def __init__(self):
        from .bedrock import BedrockLLM
        self.primary = BedrockLLM()
        self.fallback = GroqLLM()

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        settings = get_settings()
        status = provider_status(settings)
        primary_error = None
        if status['bedrock_configured']:
            try:
                return self.primary.complete(system, user, json_mode=json_mode)
            except ProviderRefusal:
                raise
            except LLMError as error:
                primary_error = error
                logging.getLogger('medibot').warning('Bedrock unavailable; Groq fallback enabled: %s',
                                                    status['groq_fallback_enabled'])
        if status['groq_configured'] and status['groq_fallback_enabled']:
            try:
                return self.fallback.complete(system, user, json_mode=json_mode)
            except LLMError as error:
                if primary_error:
                    raise LLMError('Bedrock and Groq fallback are unavailable. Check provider credentials and connectivity.') from None
                raise error
        if primary_error:
            raise primary_error
        raise LLMError('Configure Bedrock credentials or enable Groq fallback with GROQ_API_KEY in .env.')


@lru_cache
def get_llm() -> FallbackLLM:
    return FallbackLLM()
