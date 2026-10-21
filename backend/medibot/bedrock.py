"""Project Bedrock gateway using Azure AD OAuth2 client credentials."""
import json
import re
import threading
import time

import httpx

from .config import get_settings
from .llm import LLMError, ProviderRefusal, tls_context


def bedrock_configured(settings) -> bool:
    return all(getattr(settings, name, '') for name in
               ('tenant_id', 'ai_client_id', 'ai_client_secret', 'model_serving_scope', 'bedrock_api_url'))


class BedrockLLM:
    def __init__(self):
        self._token = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _get_token(self, force=False):
        settings = get_settings()
        with self._lock:
            if not force and self._token and time.monotonic() < self._expires_at:
                return self._token
            if not re.fullmatch(r'[A-Za-z0-9.-]+', settings.tenant_id):
                raise LLMError('Check TENANT_ID in the local Bedrock configuration.')
            response = httpx.post(
                f'https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token',
                data={'client_id': settings.ai_client_id, 'client_secret': settings.ai_client_secret,
                      'scope': settings.model_serving_scope, 'grant_type': 'client_credentials'},
                verify=tls_context(), timeout=settings.bedrock_auth_timeout_seconds,
            )
            if not response.is_success:
                raise LLMError(f'Bedrock authentication failed (HTTP {response.status_code}). Check the Azure AD configuration.')
            data = response.json()
            token = data['access_token']
            if not isinstance(token, str) or not token:
                raise LLMError('Bedrock authentication returned no access token.')
            lifetime = float(data.get('expires_in', 3600))
            self._token = token
            self._expires_at = time.monotonic() + max(0, min(lifetime - 60, 3300))
            return token

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        settings = get_settings()
        if not bedrock_configured(settings):
            raise LLMError('Configure TENANT_ID, AI_CLIENT_ID, AI_CLIENT_SECRET, MODEL_SERVING_SCOPE and BEDROCK_API_URL in .env.')
        if not settings.bedrock_api_url.startswith('https://'):
            raise LLMError('BEDROCK_API_URL must use HTTPS.')
        if json_mode:
            system += '\nReturn only a valid JSON object. Do not wrap it in Markdown or add commentary.'
        payload = {'anthropic_version': 'bedrock-2023-05-31', 'max_tokens': 4096,
                   'temperature': 0, 'system': system,
                   'messages': [{'role': 'user', 'content': user}]}
        try:
            token = self._get_token()
            response = httpx.post(settings.bedrock_api_url, json=payload,
                                  headers={'Authorization': f'Bearer {token}'},
                                  verify=tls_context(), timeout=settings.bedrock_timeout_seconds)
            # Refresh an expired/revoked cached token once, then let fallback handle failure.
            if response.status_code == 401:
                token = self._get_token(force=True)
                response = httpx.post(settings.bedrock_api_url, json=payload,
                                      headers={'Authorization': f'Bearer {token}'},
                                      verify=tls_context(), timeout=settings.bedrock_timeout_seconds)
            if not response.is_success:
                raise LLMError(f'Bedrock request failed (HTTP {response.status_code}). Check the gateway configuration and access.')
            data = response.json()
            if data.get('stop_reason') == 'refusal' or data.get('amazon-bedrock-guardrailAction') == 'INTERVENED':
                raise ProviderRefusal('Bedrock declined this request. Please rephrase it.')
            if data.get('stop_reason') in ('max_tokens', 'model_context_window_exceeded'):
                raise LLMError('Bedrock reached the response limit. Please ask a narrower question.')
            content = data.get('content', [])
            if not isinstance(content, list):
                raise LLMError('Bedrock returned an invalid response.')
            result = '\n'.join(block['text'] for block in content
                               if isinstance(block, dict) and block.get('type', 'text') == 'text'
                               and isinstance(block.get('text'), str)).strip()
            if not result:
                raise LLMError('Bedrock returned an empty response.')
            if json_mode:
                if result.startswith('```') and result.endswith('```'):
                    result = re.sub(r'^```(?:json)?\s*', '', result, flags=re.I)[:-3].strip()
                if not isinstance(json.loads(result), dict):
                    raise LLMError('Bedrock returned an invalid JSON object.')
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise LLMError('Bedrock could not be reached or returned an invalid response.') from error
