"""Run four live-provider SQL questions after the user configures their API key.

Start the backend, then run: python -m evaluation.live_smoke
Reports actual API responses and compares generated-query rows to reference data.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from medibot.config import get_settings
from medibot.sql_rag import execute_read_only


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://localhost:8000')
    parser.add_argument('--output', type=Path, default=Path('../evidence/live-groq.json'))
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name('sql_cases.json').read_text(encoding='utf-8'))['cases']
    settings = get_settings()
    credentials = {role: (getattr(settings, role + '_username'), getattr(settings, role + '_password'))
                   for role in ('billing_executive', 'admin')}
    if any(not username or not password for username, password in credentials.values()):
        raise SystemExit('Configure Billing Executive and Admin credentials in .env before running this script.')
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'provider': 'Configured provider', 'cases': []}
    with httpx.Client(base_url=args.api, timeout=120) as client:
        health = client.get('/health')
        health.raise_for_status()
        report['model'] = health.json().get('model')
        report['provider'] = health.json().get('provider')
        report['fallback_provider'] = health.json().get('fallback_provider')
        if not health.json()['checks']['llm_configured']:
            raise SystemExit('No generation provider is configured. Add Bedrock settings or Groq fallback in .env and restart the backend first.')
        headers = {}
        for role, (username, password) in credentials.items():
            response = client.post('/login', json={'username': username, 'password': password})
            response.raise_for_status()
            headers[role] = {'Authorization': 'Bearer ' + response.json()['access_token']}
        for case in cases:
            response = client.post('/chat', headers=headers[case['role']], json={'question': case['question']})
            record = {'id': case['id'], 'question': case['question'], 'status_code': response.status_code,
                      'response': response.json(), 'passed': False}
            if response.is_success and response.json().get('sql'):
                actual = execute_read_only(response.json()['sql'], get_settings().database_path)
                normalize = lambda rows: sorted(json.dumps(row, sort_keys=True) for row in rows)
                record['passed'] = normalize(actual['rows']) == normalize(case['expected_rows'])
                record['actual_rows'] = actual['rows']
                record['expected_rows'] = case['expected_rows']
            report['cases'].append(record)
            print(f"{case['id']}: {'PASS' if record['passed'] else 'FAIL'}")
    report['passed'] = all(case['passed'] for case in report['cases'])
    report['note'] = 'Row equality validates generated SQL; review the saved natural-language answers and date interpretations too.'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Report: {args.output.resolve()}')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
