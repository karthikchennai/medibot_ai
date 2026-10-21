"""Compare raw and reranked top-three passages from an existing measured run.

No queries are rerun and no annotations are changed. All four baselines are
reported, including the dense+reranking control, even when methods tie.
"""
import json
import statistics
from pathlib import Path


def main():
    source = Path('../evidence/retrieval.json')
    report = json.loads(source.read_text(encoding='utf-8'))
    methods = {}
    for mode in ['dense', 'hybrid']:
        for ranking, label in [('candidates', 'only'), ('reranked', 'with_reranking')]:
            reciprocal_ranks = []
            details = []
            for question in report['queries']:
                passages = question['modes'][mode][ranking][:3]
                rr = next((1 / (i + 1) for i, passage in enumerate(passages)
                           if passage['matched_target_indices']), 0)
                reciprocal_ranks.append(rr)
                details.append({'id': question['id'], 'reciprocal_rank_at_3': rr})
            methods[f'{mode}_{label}'] = {'mrr_at_3': statistics.mean(reciprocal_ranks),
                                         'hit_at_3': statistics.mean(int(x > 0) for x in reciprocal_ranks),
                                         'per_question': details}
    output = {'source_report': source.name, 'questions_sha256': report['questions_sha256'],
              'question_count': len(report['queries']), 'methods': methods,
              'method': 'Same 25 questions, source annotations, role filters, index and top-three output budget. Only the ranking stage changes. Calculated from the saved unmodified retrieval run.',
              'limits': 'Small development dataset; results do not establish general superiority or clinical accuracy. Dense+reranking and hybrid+reranking controls are both reported.'}
    path = Path('../evidence/baselines.json')
    path.write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps({key: {'MRR@3': value['mrr_at_3'], 'Hit@3': value['hit_at_3']} for key, value in methods.items()}, indent=2))


if __name__ == '__main__':
    main()
