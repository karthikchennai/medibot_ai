import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
from qdrant_client import QdrantClient, models

from medibot import auth, ingestion
from medibot.policy import COLLECTION_ROLES, infer_intent
from medibot.retrieval import HybridRetriever, read_manifest


def test_simultaneous_first_logins_never_observe_or_cache_an_incomplete_key(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, 'ROOT', tmp_path)
    monkeypatch.setattr(auth, 'get_settings', lambda: SimpleNamespace(jwt_secret=''))
    generating, release = Event(), Event()
    generated_key = secrets.token_urlsafe(48)

    def generate(length):
        generating.set()
        assert release.wait(10)
        return generated_key

    monkeypatch.setattr(auth.secrets, 'token_urlsafe', generate)
    auth.signing_key.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(auth.signing_key) for _ in range(8)]
            try:
                assert generating.wait(10)
                # The final path is not visible before the key is complete,
                # including to another backend process.
                prematurely_published = (tmp_path / '.state' / 'session.key').exists()
            finally:
                release.set()
            keys = [future.result(timeout=10) for future in futures]
        assert not prematurely_published
        assert keys == [generated_key] * 8
        assert auth.signing_key() == generated_key
        assert (tmp_path / '.state' / 'session.key').read_text(encoding='utf-8') == generated_key
    finally:
        auth.signing_key.cache_clear()


@pytest.mark.parametrize('stored_key', ['', 'short', ' ' * 40])
def test_invalid_persisted_key_fails_closed_without_replacing_it(tmp_path, monkeypatch, stored_key):
    path = tmp_path / '.state' / 'session.key'
    path.parent.mkdir()
    path.write_text(stored_key, encoding='utf-8')
    monkeypatch.setattr(auth, 'ROOT', tmp_path)
    monkeypatch.setattr(auth, 'get_settings', lambda: SimpleNamespace(jwt_secret=''))
    auth.signing_key.cache_clear()
    try:
        with pytest.raises(RuntimeError, match='Persisted session key'):
            auth.signing_key()
        assert path.read_text(encoding='utf-8') == stored_key
        assert auth.signing_key.cache_info().currsize == 0
    finally:
        auth.signing_key.cache_clear()


@pytest.mark.parametrize('question,expected', [
    ('How many days do I have to submit an insurance claim?', 'hybrid_rag'),
    ('What is the claim submission deadline?', 'hybrid_rag'),
    ('Show the claim submission procedures.', 'hybrid_rag'),
    ('List the documents required for a claim.', 'hybrid_rag'),
    ('How long can I wait to file a claim?', 'hybrid_rag'),
    ('List pending claims from April 2025', 'sql_rag'),
    ('Show open maintenance tickets', 'sql_rag'),
    ('How many claims were escalated last month?', 'sql_rag'),
    ('Which equipment category has the most open maintenance tickets?', 'sql_rag'),
    ('What are the 5 moments of hand hygiene?', 'hybrid_rag'),
])
def test_routing_separates_rules_from_operational_records(question, expected):
    assert infer_intent(question)[0] == expected


@pytest.mark.parametrize('fail_second_document', [True, False])
def test_ingestion_marks_complete_only_after_every_document_succeeds(tmp_path, monkeypatch, fail_second_document):
    settings = SimpleNamespace(
        data_dir=tmp_path / 'data', qdrant_path=tmp_path / 'qdrant', qdrant_url=None,
        qdrant_api_key=None, qdrant_collection='regression', embedding_model='test-model',
        reranker_model='test-reranker', model_cache_dir=tmp_path / 'models',
        retrieval_candidates=10, rerank_top_k=3,
    )
    folder = settings.data_dir / 'general'
    folder.mkdir(parents=True)
    for name in ('first.md', 'second.md'):
        (folder / name).write_text('Source document', encoding='utf-8')
    client = QdrantClient(path=str(settings.qdrant_path))

    def get_client():
        return client

    get_client.cache_clear = lambda: None

    class Embeddings:
        def __init__(self, settings):
            pass

        def dense_documents(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def sparse_documents(self, texts):
            return [models.SparseVector(indices=[1], values=[1.0]) for _ in texts]

    def parse(path, data_dir, converter, chunker):
        if path.name == 'second.md' and fail_second_document:
            raise RuntimeError('Conversion of the second document failed')
        source = path.relative_to(data_dir).as_posix()
        return [{'chunk_id': str(uuid5(NAMESPACE_URL, source)), 'text': 'Extracted document text',
                 'source_document': source, 'collection': 'general', 'chunk_type': 'text',
                 'access_roles': list(COLLECTION_ROLES['general'])}]

    monkeypatch.setattr(ingestion, 'get_qdrant_client', get_client)
    monkeypatch.setattr(ingestion, '_docling_pipeline', lambda settings: (None, None))
    monkeypatch.setattr(ingestion, 'EmbeddingModels', Embeddings)
    monkeypatch.setattr(ingestion, 'parse_document', parse)
    if fail_second_document:
        with pytest.raises(RuntimeError, match='second document'):
            ingestion.ingest(settings)
    else:
        assert ingestion.ingest(settings)['chunks'] == 2
    # ingest closes its embedded client, as a separate CLI invocation would.
    reopened = QdrantClient(path=str(settings.qdrant_path))
    try:
        manifest = read_manifest(reopened, settings)
        state = HybridRetriever(settings, reopened).status()
        assert manifest['ingestion_state'] == ('failed' if fail_second_document else 'complete')
        assert state['ready'] is not fail_second_document
        assert state['count'] == (1 if fail_second_document else 2)
    finally:
        reopened.close()
